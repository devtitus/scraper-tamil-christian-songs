"""JSON writers and the crash-safe JSONL checkpoint.

All JSON is written atomically (temp file + ``os.replace``) so an interrupted
run can never leave a truncated file behind, and always with
``encoding="utf-8"`` so Tamil text round-trips correctly.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import AppConfig

logger = logging.getLogger(__name__)


def album_output_dir(cfg: AppConfig, album_slug: str) -> Path:
    """``<output.root>/<album-slug>/`` - the per-album output folder."""
    return Path(cfg.output.root) / album_slug


def atomic_write_json(
    path: Path, payload: Any, *, ensure_ascii: bool = False, indent: int = 2
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=ensure_ascii, indent=indent)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def write_json_documents(
    documents: Iterable[Dict[str, Any]],
    path: Path,
    *,
    ensure_ascii: bool = False,
    indent: int = 2,
) -> Path:
    """Write a list of song documents to ``path``."""
    return atomic_write_json(
        path, list(documents), ensure_ascii=ensure_ascii, indent=indent
    )


class Checkpoint:
    """Append-only JSONL progress log used to resume an interrupted crawl.

    One line per completed song. Because ``hymn_number`` is derived from the
    song's position in the album listing, resumed runs renumber identically.
    """

    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = bool(enabled)
        self._handle = None
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- reading ----------------------------------------------------------
    def load(self) -> Dict[str, Dict[str, Any]]:
        records: Dict[str, Dict[str, Any]] = {}
        if not self.enabled or not self.path.is_file():
            return records
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "skipping corrupt checkpoint line %d in %s", line_number, self.path
                    )
                    continue
                slug = record.get("slug")
                if slug:
                    records[str(slug)] = record
        return records

    # -- writing ----------------------------------------------------------
    def append(self, record: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8", newline="\n")
        self._handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None

    def clear(self) -> None:
        self.close()
        if self.path.is_file():
            try:
                self.path.unlink()
            except OSError:  # pragma: no cover
                pass

    def __enter__(self) -> "Checkpoint":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def checkpoint_path(cfg: AppConfig, album_slug: str) -> Path:
    return Path(cfg.resume.state_dir) / f"{album_slug}.checkpoint.jsonl"


def summarize_records(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(records, key=lambda record: record.get("hymn_number", 0))
