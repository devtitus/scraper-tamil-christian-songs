"""Dataclasses shared across the parsers, transform and output stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Site-scrape results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SongRef:
    """A song as discovered on an album listing page."""

    slug: str
    url: str
    title_roman: str
    title_tamil: str
    page: int
    index_in_page: int


@dataclass(frozen=True)
class AlbumInfo:
    slug: str
    name: str
    display_title: str
    url: str
    declared_total: Optional[int]
    total_pages: int
    songs_per_page: int


@dataclass
class ParsedLyrics:
    """Raw, segmented lyrics pulled off a single song page."""

    ref: SongRef
    title_combined: str = ""
    tamil_stanzas: List[List[str]] = field(default_factory=list)
    tanglish_stanzas: List[List[str]] = field(default_factory=list)
    english_meaning: Optional[str] = None
    ppt_url: Optional[str] = None
    hashtags: List[str] = field(default_factory=list)
    alignment_ok: Optional[bool] = None


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Stanza:
    lines: List[str]
    number: int


@dataclass(frozen=True)
class Part:
    title: str
    stanzas: List[Stanza]
    part_number: int


@dataclass(frozen=True)
class LyricsDocument:
    """The exact document shape requested for tamil.json / tanglish.json.

    Key order is significant and mirrors the reference sample:
    ``parts``, ``bridge``, ``chorus``, ``metadata``, ``full_text``.
    Fields with no value are emitted as ``null`` rather than omitted, so every
    song object in the file has an identical structure.
    """

    parts: Optional[List[Part]]
    bridge: Optional[List[str]]
    chorus: Optional[List[str]]
    metadata: Dict[str, Any]
    full_text: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        parts: Optional[List[Dict[str, Any]]] = None
        if self.parts is not None:
            parts = [
                {
                    "title": part.title,
                    "stanzas": [
                        {"lines": list(stanza.lines), "number": stanza.number}
                        for stanza in part.stanzas
                    ],
                    "part_number": part.part_number,
                }
                for part in self.parts
            ]
        return {
            "parts": parts,
            "bridge": list(self.bridge) if self.bridge is not None else None,
            "chorus": list(self.chorus) if self.chorus is not None else None,
            "metadata": dict(self.metadata),
            "full_text": self.full_text,
        }
