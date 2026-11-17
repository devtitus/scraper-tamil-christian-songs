"""Per-album orchestration: discover songs, fetch lyrics, transform, write JSON.

Stage separation matters here. The crawl writes *raw* parsed stanzas to a JSONL
checkpoint; the schema transform runs at finalize time. That means changing
anything under ``[schema]`` in settings.toml can be re-applied with the
``transform`` command in seconds, without re-crawling ~372 pages.
"""

from __future__ import annotations

import dataclasses
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tqdm import tqdm

from .cache import HtmlCache
from .config import AlbumSpec, AppConfig
from .http import HttpClient, HttpError
from .models import AlbumInfo, SongRef
from .output import (
    Checkpoint,
    album_output_dir,
    atomic_write_json,
    checkpoint_path,
    write_json_documents,
)
from .parsers.album_page import estimate_pages, parse_album_page
from .parsers.lyrics_page import parse_lyrics_page
from .transform import build_document

logger = logging.getLogger(__name__)


@dataclass
class Failure:
    slug: str
    url: str
    page: Optional[int]
    error: str

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class AlbumResult:
    album: Optional[AlbumInfo] = None
    songs_found: int = 0
    songs_written: int = 0
    songs_missing: List[str] = field(default_factory=list)
    failures: List[Failure] = field(default_factory=list)
    alignment_warnings: List[str] = field(default_factory=list)
    output_files: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    http_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fetch_html(
    client: Optional[HttpClient],
    cache: HtmlCache,
    url: str,
    *,
    refresh: bool = False,
    offline: bool = False,
) -> str:
    """Cache-first fetch. ``offline`` forbids any network access."""
    if not refresh:
        cached = cache.get(url)
        if cached is not None:
            return cached
    if offline or client is None:
        raise HttpError(f"no cached HTML for {url} (offline mode)")
    html = client.get(url)
    cache.put(url, html)
    return html


def discover_songs(
    cfg: AppConfig,
    album_spec: AlbumSpec,
    client: Optional[HttpClient],
    cache: HtmlCache,
    *,
    max_pages: Optional[int] = None,
    refresh: bool = False,
    offline: bool = False,
    show_progress: bool = True,
) -> Tuple[AlbumInfo, List[SongRef]]:
    """Walk the album's paginated listing and return every song reference.

    The page count comes from the pagination nav, never from probing: the site
    answers out-of-range ``?page=N`` requests with HTTP 200 and page-1 content,
    so a "fetch until empty" loop would never terminate.
    """
    first_url = cfg.site.album_url(album_spec.slug)
    html = _fetch_html(client, cache, first_url, refresh=refresh, offline=offline)
    first = parse_album_page(
        html,
        base_url=cfg.site.base_url,
        slug=album_spec.slug,
        page=1,
        album_url=first_url,
        parser=cfg.parser,
    )
    album = first.album
    songs: List[SongRef] = list(first.songs)
    seen = {song.slug for song in songs}

    # Cross-check the nav-derived page count against the declared song total.
    estimated = estimate_pages(album.declared_total, album.songs_per_page)
    if estimated and estimated != album.total_pages:
        logger.warning(
            "album %s: pagination nav reports %d pages but %s songs at %d/page implies %d",
            album_spec.slug,
            album.total_pages,
            album.declared_total,
            album.songs_per_page,
            estimated,
        )

    effective_pages = max(1, album.total_pages)
    for cap in (album_spec.max_pages, max_pages):
        if cap and cap > 0:
            effective_pages = min(effective_pages, cap)

    if effective_pages > 1:
        iterator = range(2, effective_pages + 1)
        if show_progress:
            iterator = tqdm(
                iterator, desc=f"[{album_spec.slug}] listing", unit="page", leave=False
            )
        for page_number in iterator:
            page_url = cfg.site.album_url(album_spec.slug, page_number)
            try:
                page_html = _fetch_html(
                    client, cache, page_url, refresh=refresh, offline=offline
                )
            except HttpError as exc:
                logger.error("failed to fetch album page %d: %s", page_number, exc)
                continue
            parsed = parse_album_page(
                page_html,
                base_url=cfg.site.base_url,
                slug=album_spec.slug,
                page=page_number,
                album_url=page_url,
                parser=cfg.parser,
            )
            new_count = 0
            for song in parsed.songs:
                if song.slug in seen:
                    # Out-of-range pages wrap to page 1; dedupe protects us.
                    continue
                seen.add(song.slug)
                songs.append(song)
                new_count += 1
            if new_count == 0:
                logger.warning(
                    "album %s page %d produced no new songs; stopping pagination",
                    album_spec.slug,
                    page_number,
                )
                break

    if album.declared_total and len(songs) != album.declared_total:
        logger.warning(
            "album %s: found %d songs but the page declares %d",
            album_spec.slug,
            len(songs),
            album.declared_total,
        )
    else:
        logger.info(
            "album %s: found %d songs across %d page(s)",
            album_spec.slug,
            len(songs),
            effective_pages,
        )

    album = dataclasses.replace(album, total_pages=effective_pages)
    return album, songs


def _record_from_parsed(parsed) -> Dict[str, Any]:
    """Serialize a parsed song into the JSONL checkpoint (raw stanzas, no schema)."""
    return {
        "slug": parsed.ref.slug,
        "url": parsed.ref.url,
        "page": parsed.ref.page,
        "index_in_page": parsed.ref.index_in_page,
        "title_roman": parsed.ref.title_roman,
        "title_tamil": parsed.ref.title_tamil,
        "title_combined": parsed.title_combined,
        "tamil_stanzas": parsed.tamil_stanzas,
        "tanglish_stanzas": parsed.tanglish_stanzas,
        "english_meaning": parsed.english_meaning,
        "ppt_url": parsed.ppt_url,
        "hashtags": parsed.hashtags,
        "alignment_ok": parsed.alignment_ok,
        "scraped_at": _now_iso(),
    }


def fetch_song_records(
    cfg: AppConfig,
    album_spec: AlbumSpec,
    client: Optional[HttpClient],
    cache: HtmlCache,
    songs: Sequence[SongRef],
    *,
    limit: Optional[int] = None,
    refresh: bool = False,
    resume: bool = True,
    offline: bool = False,
    show_progress: bool = True,
) -> Tuple[Dict[str, Dict[str, Any]], List[Failure], List[SongRef]]:
    """Fetch each song page once, checkpointing raw stanzas as we go."""
    checkpoint = Checkpoint(checkpoint_path(cfg, album_spec.slug), enabled=True)
    if refresh or not resume:
        checkpoint.clear()
    records = checkpoint.load()
    if records:
        logger.info("resuming %s: %d song(s) already checkpointed", album_spec.slug, len(records))

    targets = list(songs[:limit]) if limit and limit > 0 else list(songs)
    todo = [ref for ref in targets if ref.slug not in records]
    failures: List[Failure] = []

    iterator = tqdm(
        todo,
        desc=f"[{album_spec.slug}] songs",
        unit="song",
        disable=not show_progress or not todo,
        file=sys.stdout,
    )
    for ref in iterator:
        url = ref.url or cfg.site.lyrics_url(ref.slug)
        try:
            html = _fetch_html(client, cache, url, refresh=refresh, offline=offline)
            parsed = parse_lyrics_page(
                html, ref=ref, base_url=cfg.site.base_url, parser=cfg.parser
            )
        except Exception as exc:  # keep a 1-hour crawl alive across single-song faults
            logger.error("%s: %s: %s", ref.slug, type(exc).__name__, exc)
            failures.append(
                Failure(
                    slug=ref.slug,
                    url=url,
                    page=ref.page,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        record = _record_from_parsed(parsed)
        checkpoint.append(record)
        records[ref.slug] = record

    checkpoint.close()
    return records, failures, targets


def rebuild_outputs(
    cfg: AppConfig,
    album_spec: AlbumSpec,
    album: Optional[AlbumInfo],
    songs: Sequence[SongRef],
    records: Dict[str, Dict[str, Any]],
    *,
    failures: Optional[Sequence[Failure]] = None,
    duration_seconds: float = 0.0,
    http_requests: int = 0,
    cache_stats: Optional[Dict[str, Any]] = None,
) -> AlbumResult:
    """Apply the schema transform and write tamil.json + tanglish.json.

    Both files are index-aligned: entry ``i`` in each corresponds to the same
    song and carries ``metadata.hymn_number == i + 1``. Songs with no data still
    get a structurally identical, all-null entry so that alignment holds.
    """
    failures = list(failures or [])
    out_dir = album_output_dir(cfg, album_spec.slug)
    out_dir.mkdir(parents=True, exist_ok=True)

    tamil_docs: List[Dict[str, Any]] = []
    tanglish_docs: List[Dict[str, Any]] = []
    missing: List[str] = []
    alignment_warnings: List[str] = []

    for number, ref in enumerate(songs, start=1):
        record = records.get(ref.slug)
        identity = {
            "title": (record or {}).get("title_roman") or ref.title_roman,
            "title_tamil": (record or {}).get("title_tamil") or ref.title_tamil,
            "slug": ref.slug,
            "url": ref.url or cfg.site.lyrics_url(ref.slug),
        }
        if record is None:
            missing.append(ref.slug)
            tamil_stanzas: List[Any] = []
            tanglish_stanzas: List[Any] = []
        else:
            if record.get("alignment_ok") is False:
                alignment_warnings.append(ref.slug)
            tamil_stanzas = record.get("tamil_stanzas") or []
            tanglish_stanzas = record.get("tanglish_stanzas") or []

        tamil_docs.append(
            build_document(
                tamil_stanzas, hymn_number=number, cfg=cfg.schema, identity=identity
            )
        )
        tanglish_docs.append(
            build_document(
                tanglish_stanzas, hymn_number=number, cfg=cfg.schema, identity=identity
            )
        )

    tamil_path = write_json_documents(
        tamil_docs,
        out_dir / cfg.output.tamil_file,
        ensure_ascii=cfg.output.ensure_ascii,
        indent=cfg.output.indent,
    )
    tanglish_path = write_json_documents(
        tanglish_docs,
        out_dir / cfg.output.tanglish_file,
        ensure_ascii=cfg.output.ensure_ascii,
        indent=cfg.output.indent,
    )

    written = len(songs) - len(missing)
    report: Dict[str, Any] = {
        "album": dataclasses.asdict(album) if album else {"slug": album_spec.slug},
        "generated_at": _now_iso(),
        "counts": {
            "songs_found": len(songs),
            "songs_written": written,
            "songs_missing": len(missing),
            "songs_failed": len(failures),
            "alignment_warnings": len(alignment_warnings),
            "http_requests": http_requests,
            "duration_seconds": round(duration_seconds, 2),
        },
        "cache": cache_stats or {},
        "output_files": {
            "tamil": str(tamil_path),
            "tanglish": str(tanglish_path),
        },
        "schema_options": {
            "include_identity": cfg.schema.include_identity,
            "chorus_repeat_heuristic": cfg.schema.chorus_repeat_heuristic,
            "chorus_explicit_markers": list(cfg.schema.chorus_explicit_markers),
            "bridge_markers": list(cfg.schema.bridge_markers),
            "full_text_prefix": cfg.schema.full_text_prefix,
        },
        "missing_slugs": missing,
        "alignment_warning_slugs": alignment_warnings,
        "failures": [failure.to_dict() for failure in failures],
    }
    if album_spec.name:
        report["album"]["name"] = album_spec.name

    report_path = atomic_write_json(
        out_dir / cfg.output.report_file,
        report,
        ensure_ascii=cfg.output.ensure_ascii,
        indent=cfg.output.indent,
    )

    logger.info(
        "%s: wrote %d song(s) -> %s , %s",
        album_spec.slug,
        written,
        tamil_path,
        tanglish_path,
    )
    if missing:
        logger.warning("%s: %d song(s) missing data (see %s)", album_spec.slug, len(missing), report_path)

    return AlbumResult(
        album=album,
        songs_found=len(songs),
        songs_written=written,
        songs_missing=missing,
        failures=failures,
        alignment_warnings=alignment_warnings,
        output_files=[str(tamil_path), str(tanglish_path), str(report_path)],
        duration_seconds=duration_seconds,
        http_requests=http_requests,
        cache_hits=int((cache_stats or {}).get("hits", 0)),
        cache_misses=int((cache_stats or {}).get("misses", 0)),
    )


def scrape_album(
    cfg: AppConfig,
    album_spec: AlbumSpec,
    *,
    client: Optional[HttpClient] = None,
    cache: Optional[HtmlCache] = None,
    max_pages: Optional[int] = None,
    limit: Optional[int] = None,
    refresh: bool = False,
    resume: bool = True,
    offline: bool = False,
    show_progress: bool = True,
) -> AlbumResult:
    """Full pipeline for one album: discover -> fetch -> checkpoint -> write JSON."""
    started = time.monotonic()

    owns_client = client is None and not offline
    if owns_client:
        client = HttpClient(cfg)
        client.apply_robots_crawl_delay()
    if cache is None:
        cache = HtmlCache(cfg.cache.root, cfg.cache.enabled)

    try:
        album, songs = discover_songs(
            cfg,
            album_spec,
            client,
            cache,
            max_pages=max_pages,
            refresh=refresh,
            offline=offline,
            show_progress=show_progress,
        )
        records, failures, targets = fetch_song_records(
            cfg,
            album_spec,
            client,
            cache,
            songs,
            limit=limit,
            refresh=refresh,
            resume=resume,
            offline=offline,
            show_progress=show_progress,
        )
        return rebuild_outputs(
            cfg,
            album_spec,
            album,
            targets,
            records,
            failures=failures,
            duration_seconds=time.monotonic() - started,
            http_requests=(client.request_count if client else 0),
            cache_stats=cache.stats(),
        )
    finally:
        if owns_client and client is not None:
            client.close()


def rebuild_from_cache(
    cfg: AppConfig,
    album_spec: AlbumSpec,
    *,
    max_pages: Optional[int] = None,
    limit: Optional[int] = None,
    offline: bool = True,
) -> AlbumResult:
    """Re-apply the schema transform to already-checkpointed songs.

    Used by the ``transform`` command so that ``[schema]`` changes never require
    re-crawling. Reads the album listing from the HTML cache and the raw stanzas
    from the JSONL checkpoint; touches the network only if ``offline=False``.
    """
    started = time.monotonic()
    cache = HtmlCache(cfg.cache.root, cfg.cache.enabled)

    client: Optional[HttpClient] = None
    if not offline:
        client = HttpClient(cfg)
        client.apply_robots_crawl_delay()
    try:
        album, songs = discover_songs(
            cfg,
            album_spec,
            client,
            cache,
            max_pages=max_pages,
            offline=offline,
            show_progress=False,
        )
        checkpoint = Checkpoint(checkpoint_path(cfg, album_spec.slug), enabled=True)
        records = checkpoint.load()
        if not records:
            raise HttpError(
                f"no checkpointed songs for '{album_spec.slug}'. "
                f"Run 'scrape --album {album_spec.slug}' first."
            )
        targets = list(songs[:limit]) if limit and limit > 0 else list(songs)
        return rebuild_outputs(
            cfg,
            album_spec,
            album,
            targets,
            records,
            duration_seconds=time.monotonic() - started,
            http_requests=(client.request_count if client else 0),
            cache_stats=cache.stats(),
        )
    finally:
        if client is not None:
            client.close()
