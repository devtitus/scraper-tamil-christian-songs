"""Transform segmented stanzas into the requested JSON document schema.

Target shape (identical for every song; absent values are ``null``, never
omitted)::

    {
      "parts":    [ {"title": "Part I", "stanzas": [{"lines": [...], "number": 1}], "part_number": 1} ],
      "bridge":   null,
      "chorus":   null,
      "metadata": {"has_bridge": false, "has_chorus": false, "part_count": 1,
                   "hymn_number": 1, "verse_count": 6, "stanza_format": "plain"},
      "full_text": "Hymn No. 1 1. <lines> 2. <lines> ..."
    }

Key order is preserved exactly as in the reference sample.

This module is deliberately independent of the network layer: it operates on
plain ``list[list[str]]`` stanza data, so schema rules can be changed and
re-applied to cached HTML via ``python -m lyrics_scraper transform`` without
re-crawling the site.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from .config import SchemaConfig
from .models import LyricsDocument, Part, Stanza

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")
_VERSE_NUMBER_RE = re.compile(r"^(\d{1,3})\s*[.)]\s*(.*)$")
_PART_MARKER_RE = re.compile(r"^part\s+([0-9]+|[ivx]+)\s*[:.\-]?\s*(.*)$", re.IGNORECASE)
_MARKER_SEPARATORS = ":：-–—"

_ROMAN_VALUES = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)


def to_roman(number: int) -> str:
    if number <= 0:
        return str(number)
    out: List[str] = []
    for value, symbol in _ROMAN_VALUES:
        while number >= value:
            out.append(symbol)
            number -= value
    return "".join(out)


def normalize_line(line: str) -> str:
    return _WS_RE.sub(" ", str(line or "")).strip()


def normalize_stanzas(raw: Sequence[Sequence[str]]) -> List[List[str]]:
    """Strip lines, collapse whitespace, drop empty lines and empty stanzas."""
    stanzas: List[List[str]] = []
    for raw_stanza in raw or []:
        lines = [normalize_line(line) for line in raw_stanza]
        lines = [line for line in lines if line]
        if lines:
            stanzas.append(lines)
    return stanzas


def _marker_map(cfg: SchemaConfig) -> List[Tuple[str, str]]:
    """Build a longest-first list of (lowercased marker, section kind)."""
    entries: List[Tuple[str, str]] = []
    for marker in cfg.chorus_explicit_markers:
        text = normalize_line(marker).lower()
        if text:
            entries.append((text, "chorus"))
    for marker in cfg.bridge_markers:
        text = normalize_line(marker).lower()
        if text:
            entries.append((text, "bridge"))
    entries.sort(key=lambda item: len(item[0]), reverse=True)
    return entries


def _match_marker(line: str, markers: Sequence[Tuple[str, str]]) -> Tuple[Optional[str], str]:
    """Return ``(kind, remainder)`` when ``line`` opens a labelled section.

    A marker must be followed by end-of-line or a separator (``:``, ``-`` ...)
    so that ordinary words beginning with a marker ("chorused") never match.
    """
    text = normalize_line(line)
    lowered = text.lower()
    for marker, kind in markers:
        if not lowered.startswith(marker):
            continue
        rest = text[len(marker):].strip()
        if rest and rest[0] not in _MARKER_SEPARATORS:
            continue
        return kind, rest.lstrip(_MARKER_SEPARATORS).strip()
    return None, ""


def _extract_sections(
    stanzas: Sequence[Sequence[str]], markers: Sequence[Tuple[str, str]]
) -> Tuple[List[List[str]], Optional[List[str]], Optional[List[str]]]:
    """Pull labelled chorus/bridge blocks out of the stanza stream.

    Handles both ``Chorus : <text>`` on one line and a bare ``Chorus :`` line
    whose content lives in the following stanza.
    """
    verses: List[List[str]] = []
    chorus: Optional[List[str]] = None
    bridge: Optional[List[str]] = None
    pending_kind: Optional[str] = None

    def assign(kind: str, lines: Sequence[str]) -> None:
        nonlocal chorus, bridge
        cleaned = [normalize_line(x) for x in lines]
        cleaned = [x for x in cleaned if x]
        if not cleaned:
            return
        if kind == "chorus" and chorus is None:
            chorus = cleaned
        elif kind == "bridge" and bridge is None:
            bridge = cleaned

    for stanza in stanzas:
        lines = list(stanza)
        if pending_kind is not None:
            kind, pending_kind = pending_kind, None
            assign(kind, lines)
            continue

        verse_lines: List[str] = []
        index = 0
        while index < len(lines):
            kind, remainder = _match_marker(lines[index], markers)
            if kind is None:
                verse_lines.append(lines[index])
                index += 1
                continue
            index += 1
            section_lines = [remainder] if remainder else []
            section_lines.extend(lines[index:])
            index = len(lines)
            if section_lines:
                assign(kind, section_lines)
            else:
                pending_kind = kind  # content expected in the next stanza
        if verse_lines:
            verses.append(verse_lines)

    return verses, chorus, bridge


_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def _stanza_key(stanza: Sequence[str]) -> str:
    joined = " ".join(normalize_line(x) for x in stanza).lower()
    return _WS_RE.sub(" ", _PUNCT_RE.sub("", joined)).strip()


def _find_repeated_index(stanzas: Sequence[Sequence[str]]) -> Optional[int]:
    keys = [_stanza_key(s) for s in stanzas]
    counts = Counter(keys)
    for index, key in enumerate(keys):
        if key and counts[key] >= 2:
            return index
    return None


def _strip_verse_numbers(
    stanzas: Sequence[Sequence[str]],
) -> Tuple[List[List[str]], bool]:
    """Remove leading ``1.`` / ``1)`` verse numbers; report whether they existed."""
    stripped: List[List[str]] = []
    numbered = 0
    for stanza in stanzas:
        lines = list(stanza)
        if not lines:
            continue
        match = _VERSE_NUMBER_RE.match(lines[0])
        if match:
            numbered += 1
            remainder = match.group(2).strip()
            rest = ([remainder] if remainder else []) + [normalize_line(x) for x in lines[1:]]
            rest = [x for x in rest if x]
            if rest:
                stripped.append(rest)
        else:
            stripped.append([normalize_line(x) for x in lines])
    is_numbered = numbered > 0 and numbered * 2 >= len(stanzas)
    return stripped, is_numbered


def _split_parts(stanzas: Sequence[Sequence[str]]) -> List[Tuple[str, List[List[str]]]]:
    """Split on explicit ``Part I`` / ``Part 2`` markers; default to one part."""
    groups: List[Tuple[Optional[str], List[List[str]]]] = []
    current_title: Optional[str] = None
    current: List[List[str]] = []

    for stanza in stanzas:
        lines = list(stanza)
        match = _PART_MARKER_RE.match(lines[0]) if lines else None
        if match:
            if current:
                groups.append((current_title, current))
                current = []
            token = match.group(1)
            is_roman = set(token.lower()) <= set("ivx")
            current_title = f"Part {token.upper() if is_roman else token}"
            remainder = match.group(2).strip()
            rest = ([remainder] if remainder else []) + lines[1:]
            rest = [x for x in rest if x]
            if rest:
                current.append(rest)
        else:
            current.append(lines)
    if current:
        groups.append((current_title, current))

    resolved: List[Tuple[str, List[List[str]]]] = []
    for index, (title, group) in enumerate(groups, start=1):
        resolved.append((title or f"Part {to_roman(index)}", group))
    return resolved


def _build_full_text(
    stanzas: Sequence[Stanza],
    chorus: Optional[Sequence[str]],
    cfg: SchemaConfig,
    hymn_number: int,
) -> Optional[str]:
    """Rebuild the flat ``full_text`` string exactly as in the reference sample.

    ``"<prefix> 1. <lines> Chorus : <lines> 2. <lines> ..."``, single-space joined.
    """
    segments: List[str] = []

    prefix = (cfg.full_text_prefix or "").strip()
    if prefix:
        try:
            segments.append(prefix.format(n=hymn_number))
        except (KeyError, IndexError, ValueError):
            segments.append(prefix)

    for index, stanza in enumerate(stanzas, start=1):
        segments.append(f"{index}.")
        segments.extend(stanza.lines)
        if index == 1 and chorus:
            label = (cfg.full_text_chorus_label or "").strip()
            if label:
                segments.append(label)
            segments.extend(chorus)

    if not stanzas and chorus:
        label = (cfg.full_text_chorus_label or "").strip()
        if label:
            segments.append(label)
        segments.extend(chorus)

    text = " ".join(segment for segment in segments if segment).strip()
    return text or None


def build_document(
    raw_stanzas: Sequence[Sequence[str]],
    *,
    hymn_number: int,
    cfg: SchemaConfig,
    identity: Optional[Dict[str, str]] = None,
) -> Dict:
    """Build one output document from raw stanza data.

    ``identity`` is only merged into ``metadata`` when
    ``schema.include_identity`` is enabled; the default keeps the strict schema.
    """
    stanzas = normalize_stanzas(raw_stanzas)

    if not stanzas:
        metadata: Dict[str, object] = {
            "has_bridge": False,
            "has_chorus": False,
            "part_count": 0,
            "hymn_number": hymn_number,
            "verse_count": 0,
            "stanza_format": None,
        }
        if cfg.include_identity and identity:
            metadata.update(identity)
        return LyricsDocument(
            parts=None, bridge=None, chorus=None, metadata=metadata, full_text=None
        ).to_dict()

    markers = _marker_map(cfg)
    verses, chorus, bridge = _extract_sections(stanzas, markers)

    if chorus is None and cfg.chorus_repeat_heuristic:
        repeated = _find_repeated_index(verses)
        if repeated is not None:
            chorus = list(verses[repeated])
            key = _stanza_key(chorus)
            verses = [stanza for stanza in verses if _stanza_key(stanza) != key]

    verses, is_numbered = _strip_verse_numbers(verses)

    parts: Optional[List[Part]] = None
    groups = _split_parts(verses)
    if groups:
        parts = []
        counter = 0
        for part_number, (title, group) in enumerate(groups, start=1):
            stanza_objects: List[Stanza] = []
            for lines in group:
                counter += 1
                stanza_objects.append(Stanza(lines=list(lines), number=counter))
            if stanza_objects:
                parts.append(
                    Part(title=title, stanzas=stanza_objects, part_number=part_number)
                )
        if not parts:
            parts = None

    flat = [stanza for part in (parts or []) for stanza in part.stanzas]

    metadata = {
        "has_bridge": bridge is not None,
        "has_chorus": chorus is not None,
        "part_count": len(parts) if parts else 0,
        "hymn_number": hymn_number,
        "verse_count": len(flat),
        "stanza_format": cfg.stanza_format_numbered if is_numbered else cfg.stanza_format_plain,
    }
    if cfg.include_identity and identity:
        metadata.update(identity)

    return LyricsDocument(
        parts=parts,
        bridge=bridge,
        chorus=chorus,
        metadata=metadata,
        full_text=_build_full_text(flat, chorus, cfg, hymn_number),
    ).to_dict()
