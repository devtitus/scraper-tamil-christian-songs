"""HTML parsers for album listing pages and song pages."""

from __future__ import annotations

import logging
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_RESOLVED_PARSER: Optional[str] = None


def resolve_parser(preferred: str = "auto") -> str:
    """Pick a BeautifulSoup parser.

    ``auto`` prefers lxml (much faster) and transparently falls back to the
    stdlib ``html.parser`` when lxml is unavailable - useful because lxml is a
    C extension and may lag brand-new Python releases.
    """
    global _RESOLVED_PARSER
    if preferred and preferred != "auto":
        return preferred
    if _RESOLVED_PARSER:
        return _RESOLVED_PARSER
    try:
        import lxml  # noqa: F401

        _RESOLVED_PARSER = "lxml"
    except Exception:  # pragma: no cover - depends on environment
        logger.warning("lxml unavailable; falling back to stdlib html.parser")
        _RESOLVED_PARSER = "html.parser"
    return _RESOLVED_PARSER


def make_soup(html: str, parser: str = "auto") -> BeautifulSoup:
    return BeautifulSoup(html, resolve_parser(parser))


__all__ = ["make_soup", "resolve_parser"]
