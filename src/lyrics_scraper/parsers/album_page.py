"""Parser for album listing pages.

URL shape: ``/album/{slug}/?page={n}``

Verified markup (Astro-generated, static HTML):

    <header class="mb-8">
      <p class="...uppercase...">Album</p>
      <h1>Jebathotta Jeyageethangal Songs Lyrics</h1>
      <p>Browse all 364 Tamil Christian song lyrics from the album X.</p>
    </header>

    <div class="grid gap-4 sm:grid-cols-2">
      <article class="card group">
        <a href="/lyrics/aabathu-naalil-karthar/" class="block">
          <h2>Aabathu Naalil Karthar</h2>       <!-- roman / Tanglish title -->
          <p class="text-sm ...">Tamil title</p>
        </a>
      </article>
      ...
    </div>

    <nav aria-label="Pagination">
      <a href="/album/x/?page=2">2</a> ... <a href="/album/x/?page=8">8</a>
    </nav>

PITFALL: out-of-range pages (``?page=9``, ``?page=99``) return **HTTP 200 with
page-1 content** instead of 404. The page count must therefore be read from the
pagination nav; probing until an empty/failed page loops forever.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin

from ..models import AlbumInfo, SongRef
from . import make_soup

logger = logging.getLogger(__name__)

_DECLARED_TOTAL_RE = re.compile(r"Browse all\s+(\d+)", re.IGNORECASE)
_ALBUM_NAME_RE = re.compile(r"from the album\s+(.+?)\s*\.\s*$", re.IGNORECASE)
_PAGE_PARAM_RE = re.compile(r"[?&]page=(\d+)")
_LYRICS_SLUG_RE = re.compile(r"^/lyrics/([^/]+)/?$")


@dataclass
class AlbumPageResult:
    album: AlbumInfo
    songs: List[SongRef] = field(default_factory=list)


def _slug_from_href(href: str) -> Optional[str]:
    match = _LYRICS_SLUG_RE.match((href or "").strip())
    return match.group(1) if match else None


def _detect_total_pages(soup, album_url: str) -> int:
    """Read the highest ``?page=N`` advertised by the pagination nav."""
    nav = soup.select_one('nav[aria-label="Pagination"]')
    if nav is None:
        return 1
    highest = 1
    for anchor in nav.select("a[href]"):
        href = anchor.get("href") or ""
        absolute = urljoin(album_url, href)
        match = _PAGE_PARAM_RE.search(absolute)
        if match:
            highest = max(highest, int(match.group(1)))
    return max(1, highest)


def _detect_album_name(soup, display_title: str, description: str) -> str:
    crumb = soup.select_one('ol li span[aria-current="page"]')
    if crumb:
        text = crumb.get_text(" ", strip=True)
        if text:
            return text
    if description:
        match = _ALBUM_NAME_RE.search(description)
        if match:
            return match.group(1).strip()
    # "<Album> Songs Lyrics" -> "<Album>"
    cleaned = re.sub(r"\s*Songs\s+Lyrics\s*$", "", display_title, flags=re.IGNORECASE)
    return cleaned.strip() or display_title.strip()


def parse_album_page(
    html: str,
    *,
    base_url: str,
    slug: str,
    page: int,
    album_url: str,
    parser: str = "auto",
) -> AlbumPageResult:
    soup = make_soup(html, parser)

    scope = soup.select_one("main") or soup

    header = scope.select_one("header")
    display_title = ""
    description = ""
    if header is not None:
        h1 = header.select_one("h1")
        display_title = h1.get_text(" ", strip=True) if h1 else ""
        paragraphs = header.select("p")
        for paragraph in paragraphs:
            text = paragraph.get_text(" ", strip=True)
            if "Browse all" in text or "song lyrics" in text.lower():
                description = text
                break
        if not description and len(paragraphs) > 1:
            description = paragraphs[-1].get_text(" ", strip=True)

    declared_total: Optional[int] = None
    match = _DECLARED_TOTAL_RE.search(description or "")
    if match:
        declared_total = int(match.group(1))

    album_name = _detect_album_name(soup, display_title, description)
    total_pages = _detect_total_pages(soup, album_url)

    # ---- song cards -----------------------------------------------------
    # Scope to the grid, then require an <h2> inside the anchor: that pairing is
    # what makes a song card distinct from sidebar/related links.
    grid = scope.select_one("div.grid") or scope
    songs: List[SongRef] = []
    seen = set()
    for anchor in grid.select('article.card a[href]'):
        href = (anchor.get("href") or "").strip()
        song_slug = _slug_from_href(href)
        if not song_slug or song_slug in seen:
            continue
        heading = anchor.select_one("h2")
        if heading is None:
            continue
        seen.add(song_slug)

        subtitle = anchor.select_one("p")
        songs.append(
            SongRef(
                slug=song_slug,
                url=urljoin(base_url.rstrip("/") + "/", href),
                title_roman=heading.get_text(" ", strip=True),
                title_tamil=subtitle.get_text(" ", strip=True) if subtitle else "",
                page=page,
                index_in_page=len(songs) + 1,
            )
        )

    album = AlbumInfo(
        slug=slug,
        name=album_name,
        display_title=display_title,
        url=album_url,
        declared_total=declared_total,
        total_pages=total_pages,
        songs_per_page=len(songs),
    )
    return AlbumPageResult(album=album, songs=songs)


def estimate_pages(declared_total: Optional[int], songs_per_page: int) -> Optional[int]:
    """Cross-check the nav-derived page count against the declared song total."""
    if not declared_total or songs_per_page <= 0:
        return None
    return max(1, math.ceil(declared_total / songs_per_page))
