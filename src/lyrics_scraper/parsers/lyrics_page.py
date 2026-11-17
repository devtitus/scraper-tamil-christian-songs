"""Parser for individual song pages.

URL shape: ``/lyrics/{slug}/``

Verified markup:

    <article>
      <header class="mb-6">
        <h1>Aabathu Naalil Karthar - ஆபத்து நாளில் கர்த்தர்</h1>
        <p>ஆபத்து நாளில் கர்த்தர்</p>
      </header>

      <div class="lyrics-content">
        <div id="tamiltext"><div class="wp-content">
          <p>line1<br/>line2<br/>line3</p>      <!-- one <p> per stanza -->
        </div></div>

        <section id="tamilroman">                <!-- Tanglish / transliteration -->
          <div class="whitespace-pre-line">line1\nline2\n\nline3...</div>
        </section>

        <section id="info"> ... <a href="/lyrics/x/ppt/">...</a> </section>

        <div id="merge_lyrics">                  <!-- Tamil/Tanglish interleaved -->
          <div class="whitespace-pre-line">tamil1\ntanglish1\ntamil2\ntanglish2</div>
        </div>

        <section id="translated-content">        <!-- OPTIONAL machine translation -->
          <div class="wp-content">...</div>
        </section>
      </div>
    </article>

Notes:
* ``#translated-content`` is absent on some songs, so it is nullable.
* The ``/artist/...`` links on a song page belong to the sidebar "Top Artists"
  widget, NOT to the song. This site publishes no per-song artist metadata.
* ``<br/>`` is followed by a literal newline in the source, so text extraction
  must strip and drop empty lines.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional
from urllib.parse import urljoin

from ..models import ParsedLyrics, SongRef
from . import make_soup

logger = logging.getLogger(__name__)

_BLANK_LINE_RE = re.compile(r"\n\s*\n")


def _clean_lines(text: str) -> List[str]:
    """Split raw text into stripped, non-empty lines."""
    if not text:
        return []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in normalized.split("\n") if line.strip()]


def _split_stanzas(text: str) -> List[List[str]]:
    """Split ``whitespace-pre-line`` text on blank lines into stanzas."""
    if not text:
        return []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    stanzas: List[List[str]] = []
    for block in _BLANK_LINE_RE.split(normalized):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if lines:
            stanzas.append(lines)
    return stanzas


def _stanzas_from_wp_content(container) -> List[List[str]]:
    """Extract stanzas from a ``.wp-content`` block.

    Prefers ``<p>``-per-stanza; falls back to blank-line splitting when the
    markup uses plain text or ``<br>`` only.
    """
    if container is None:
        return []
    content = container.select_one(".wp-content") or container
    paragraphs = content.find_all("p", recursive=True)
    if paragraphs:
        stanzas: List[List[str]] = []
        for paragraph in paragraphs:
            lines = _clean_lines(paragraph.get_text("\n"))
            if lines:
                stanzas.append(lines)
        if stanzas:
            return stanzas
    return _split_stanzas(content.get_text("\n"))


def _pre_line_stanzas(section, selector: str) -> List[List[str]]:
    if section is None:
        return []
    block = section.select_one(selector) or section
    return _split_stanzas(block.get_text())


def _extract_titles(scope, ref: SongRef):
    """Return (combined_title, SongRef with titles filled in)."""
    title_combined = ""
    title_tamil = ref.title_tamil
    title_roman = ref.title_roman

    header = None
    article = scope.select_one("article")
    if article is not None:
        header = article.select_one("header")
    if header is None:
        header = scope.select_one("header")

    if header is not None:
        h1 = header.select_one("h1")
        if h1:
            title_combined = h1.get_text(" ", strip=True)
        paragraphs = header.select("p")
        if paragraphs:
            subtitle = paragraphs[0].get_text(" ", strip=True)
            if subtitle:
                title_tamil = subtitle

    if title_combined and " - " in title_combined:
        left, right = title_combined.split(" - ", 1)
        if not title_roman:
            title_roman = left.strip()
        if not title_tamil:
            title_tamil = right.strip()

    return title_combined, SongRef(
        slug=ref.slug,
        url=ref.url,
        title_roman=title_roman or ref.title_roman,
        title_tamil=title_tamil or ref.title_tamil,
        page=ref.page,
        index_in_page=ref.index_in_page,
    )


def _extract_extras(soup, root: str):
    """Return (ppt_url, hashtags)."""
    ppt_url: Optional[str] = None
    ppt_anchor = soup.select_one('a[href$="/ppt/"]')
    if ppt_anchor is not None and ppt_anchor.get("href"):
        ppt_url = urljoin(root, ppt_anchor["href"])

    hashtags: List[str] = []
    seen = set()
    for anchor in soup.select('a[href^="/hashtag/"]'):
        label = anchor.get_text(" ", strip=True)
        if label and label not in seen:
            seen.add(label)
            hashtags.append(label)
    return ppt_url, hashtags


def parse_lyrics_page(
    html: str,
    *,
    ref: SongRef,
    base_url: str,
    parser: str = "auto",
) -> ParsedLyrics:
    """Parse one song page into segmented Tamil + Tanglish stanzas."""
    soup = make_soup(html, parser)
    scope = soup.select_one("main") or soup
    root = base_url.rstrip("/") + "/"

    title_combined, ref = _extract_titles(scope, ref)

    tamil_stanzas = _stanzas_from_wp_content(soup.select_one("#tamiltext"))
    tanglish_stanzas = _pre_line_stanzas(
        soup.select_one("#tamilroman"), "div.whitespace-pre-line"
    )

    # Optional machine translation - absent on some songs.
    english_meaning: Optional[str] = None
    translated = soup.select_one("#translated-content")
    if translated is not None:
        meaning_stanzas = _stanzas_from_wp_content(translated)
        flattened = [line for stanza in meaning_stanzas for line in stanza]
        if flattened:
            english_meaning = "\n".join(flattened)

    ppt_url, hashtags = _extract_extras(soup, root)

    # Alignment cross-check: #merge_lyrics interleaves Tamil/Tanglish
    # line-by-line, so the two independent blocks must agree.
    alignment_ok: Optional[bool] = None
    if tamil_stanzas and tanglish_stanzas:
        tamil_lines = sum(len(s) for s in tamil_stanzas)
        tanglish_lines = sum(len(s) for s in tanglish_stanzas)
        alignment_ok = (
            len(tamil_stanzas) == len(tanglish_stanzas)
            and tamil_lines == tanglish_lines
        )
        if not alignment_ok:
            logger.warning(
                "%s: stanza/line mismatch (tamil %d stanzas/%d lines vs tanglish %d/%d)",
                ref.slug,
                len(tamil_stanzas),
                tamil_lines,
                len(tanglish_stanzas),
                tanglish_lines,
            )

    if not tamil_stanzas:
        logger.warning("%s: no Tamil lyrics block found (#tamiltext)", ref.slug)
    if not tanglish_stanzas:
        logger.warning("%s: no Tanglish lyrics block found (#tamilroman)", ref.slug)

    return ParsedLyrics(
        ref=ref,
        title_combined=title_combined,
        tamil_stanzas=tamil_stanzas,
        tanglish_stanzas=tanglish_stanzas,
        english_meaning=english_meaning,
        ppt_url=ppt_url,
        hashtags=hashtags,
        alignment_ok=alignment_ok,
    )
