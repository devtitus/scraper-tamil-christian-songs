"""Logging setup.

The Windows console defaults to a legacy code page, so Tamil text written to
stdout raises UnicodeEncodeError (or prints as mojibake) unless the streams are
explicitly reconfigured to UTF-8.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
_DATEFMT = "%H:%M:%S"


def _force_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # pragma: no cover - non-standard streams
            pass


def setup_logging(level: str = "INFO", log_file: Optional[Path] = None) -> None:
    """Configure root logging to stdout and, optionally, a UTF-8 file."""
    _force_utf8_streams()

    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    root.handlers.clear()

    formatter = logging.Formatter(_FORMAT, _DATEFMT)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Keep third-party chatter out of the log unless we are debugging.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
