"""Structural validation of the emitted JSON files.

Guards the two invariants the user asked for:
1. Every document has an *identical* key set (absent values are ``null``,
   never omitted).
2. ``tamil.json`` and ``tanglish.json`` stay index-aligned and share
   ``metadata.hymn_number``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List

from .config import AlbumSpec, AppConfig
from .output import album_output_dir

logger = logging.getLogger(__name__)

TOP_LEVEL_KEYS = ["parts", "bridge", "chorus", "metadata", "full_text"]
METADATA_KEYS = [
    "has_bridge",
    "has_chorus",
    "part_count",
    "hymn_number",
    "verse_count",
    "stanza_format",
]
PART_KEYS = ["title", "stanzas", "part_number"]
STANZA_KEYS = ["lines", "number"]

_TAMIL_RANGE = re.compile(r"[\u0B80-\u0BFF]")


@dataclass
class FileReport:
    path: str
    documents: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class AlbumValidation:
    album_slug: str
    files: List[FileReport] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and all(report.ok for report in self.files)


def _validate_document(doc: Any, index: int, report: FileReport, *, expect_tamil: bool) -> None:
    where = f"[{index}]"
    if not isinstance(doc, dict):
        report.errors.append(f"{where} is not an object")
        return

    keys = list(doc.keys())
    if keys != TOP_LEVEL_KEYS:
        report.errors.append(f"{where} keys {keys} != {TOP_LEVEL_KEYS}")

    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        report.errors.append(f"{where} metadata is missing or not an object")
        return

    meta_keys = list(metadata.keys())
    if meta_keys[: len(METADATA_KEYS)] != METADATA_KEYS:
        report.errors.append(
            f"{where} metadata keys {meta_keys} do not start with {METADATA_KEYS}"
        )

    parts = doc.get("parts")
    chorus = doc.get("chorus")
    bridge = doc.get("bridge")

    if bool(metadata.get("has_chorus")) != (chorus is not None):
        report.errors.append(f"{where} has_chorus disagrees with chorus value")
    if bool(metadata.get("has_bridge")) != (bridge is not None):
        report.errors.append(f"{where} has_bridge disagrees with bridge value")

    stanza_total = 0
    if parts is None:
        if metadata.get("part_count") not in (0, None):
            report.errors.append(f"{where} parts is null but part_count is not 0")
    elif isinstance(parts, list):
        if metadata.get("part_count") != len(parts):
            report.errors.append(
                f"{where} part_count {metadata.get('part_count')} != len(parts) {len(parts)}"
            )
        for part_index, part in enumerate(parts):
            if not isinstance(part, dict) or list(part.keys()) != PART_KEYS:
                report.errors.append(f"{where} parts[{part_index}] keys != {PART_KEYS}")
                continue
            for stanza_index, stanza in enumerate(part.get("stanzas") or []):
                if not isinstance(stanza, dict) or list(stanza.keys()) != STANZA_KEYS:
                    report.errors.append(
                        f"{where} parts[{part_index}].stanzas[{stanza_index}] keys != {STANZA_KEYS}"
                    )
                    continue
                stanza_total += 1
                lines = stanza.get("lines")
                if not isinstance(lines, list) or not all(
                    isinstance(line, str) for line in lines
                ):
                    report.errors.append(
                        f"{where} parts[{part_index}].stanzas[{stanza_index}].lines must be list[str]"
                    )
    else:
        report.errors.append(f"{where} parts must be an array or null")

    if metadata.get("verse_count") != stanza_total:
        report.errors.append(
            f"{where} verse_count {metadata.get('verse_count')} != counted stanzas {stanza_total}"
        )
    if not isinstance(metadata.get("hymn_number"), int):
        report.errors.append(f"{where} hymn_number must be an int")
    elif metadata["hymn_number"] != index + 1:
        report.errors.append(
            f"{where} hymn_number {metadata['hymn_number']} != expected {index + 1}"
        )

    full_text = doc.get("full_text")
    if parts and not isinstance(full_text, str):
        report.errors.append(f"{where} full_text must be a string when parts is present")
    if not parts and full_text is not None:
        report.errors.append(f"{where} full_text must be null when parts is null")

    if expect_tamil and parts:
        joined = json.dumps(doc, ensure_ascii=False)
        if not _TAMIL_RANGE.search(joined):
            report.warnings.append(f"{where} expected Tamil script but found none")


def validate_file(path: Path, *, expect_tamil: bool = False) -> FileReport:
    report = FileReport(path=str(path))
    if not path.is_file():
        report.errors.append(f"{path} does not exist")
        return report
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.errors.append(f"cannot read {path}: {exc}")
        return report

    if not isinstance(payload, list):
        report.errors.append(f"{path} must contain a JSON array")
        return report

    report.documents = len(payload)
    for index, doc in enumerate(payload):
        _validate_document(doc, index, report, expect_tamil=expect_tamil)
    return report


def validate_album(cfg: AppConfig, album_spec: AlbumSpec) -> AlbumValidation:
    out_dir = album_output_dir(cfg, album_spec.slug)
    result = AlbumValidation(album_slug=album_spec.slug)

    tamil_path = out_dir / cfg.output.tamil_file
    tanglish_path = out_dir / cfg.output.tanglish_file

    tamil_report = validate_file(tamil_path, expect_tamil=True)
    tanglish_report = validate_file(tanglish_path, expect_tamil=False)
    result.files.extend([tamil_report, tanglish_report])

    if tamil_report.ok and tanglish_report.ok:
        tamil_docs = json.loads(tamil_path.read_text(encoding="utf-8"))
        tanglish_docs = json.loads(tanglish_path.read_text(encoding="utf-8"))
        if len(tamil_docs) != len(tanglish_docs):
            result.errors.append(
                f"file lengths differ: tamil={len(tamil_docs)} tanglish={len(tanglish_docs)}"
            )
        else:
            for index, (left, right) in enumerate(zip(tamil_docs, tanglish_docs)):
                left_number = (left.get("metadata") or {}).get("hymn_number")
                right_number = (right.get("metadata") or {}).get("hymn_number")
                if left_number != right_number:
                    result.errors.append(
                        f"[{index}] hymn_number mismatch "
                        f"tamil={left_number} tanglish={right_number}"
                    )
                    break
    return result
