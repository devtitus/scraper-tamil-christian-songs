"""Raw-HTML cache.

Caching serves two purposes:

1. Resume - a full album crawl is ~372 requests at the site's mandated 10s
   crawl-delay (~62 minutes). An interrupted run should not start over.
2. Re-transformation - the schema rules in transform.py can be re-applied to
   cached HTML with no network access at all, so schema tweaks cost seconds.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class HtmlCache:
    """Content-addressed HTML store keyed by absolute URL."""

    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = bool(enabled)
        self.hits = 0
        self.misses = 0
        if self.enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        # Keep a readable prefix so the cache directory can be eyeballed.
        tail = url.split("://", 1)[-1]
        label = _UNSAFE.sub("_", tail).strip("_")[:80] or "page"
        return self.root / digest[:2] / f"{label}__{digest[:12]}.html"

    def get(self, url: str) -> Optional[str]:
        if not self.enabled:
            return None
        path = self.path_for(url)
        if path.is_file():
            self.hits += 1
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                self.misses += 1
                return None
        self.misses += 1
        return None

    def put(self, url: str, html: str) -> None:
        if not self.enabled:
            return
        path = self.path_for(url)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
        except OSError:  # pragma: no cover - cache is best-effort
            pass

    def stats(self) -> dict:
        return {"enabled": self.enabled, "hits": self.hits, "misses": self.misses}
