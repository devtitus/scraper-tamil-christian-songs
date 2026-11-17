"""Command-line interface.

Commands
--------
list-albums   show the albums declared in config/albums.toml
scrape        crawl album listing pages + song pages, write tamil/tanglish JSON
transform     re-apply the schema rules to cached data (no song re-crawling)
validate      check the emitted JSON against the required structure
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path
from typing import List

from .config import (
    DEFAULT_ALBUMS_PATH,
    DEFAULT_SETTINGS_PATH,
    AlbumSpec,
    AppConfig,
    load_config,
)
from .http import HttpError
from .logging_setup import setup_logging
from .pipeline import AlbumResult, rebuild_from_cache, scrape_album
from .validate import validate_album

logger = logging.getLogger("lyrics_scraper")


def _add_album_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--album",
        nargs="+",
        default=None,
        metavar="SLUG",
        help="album slug(s) or full album URL(s); ad-hoc slugs need not be in the config",
    )
    parser.add_argument(
        "--all", action="store_true", help="run for every enabled album in the config"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lyrics-scraper",
        description="Scrape tamilchristiansongs.in album lyrics into structured JSON.",
    )
    parser.add_argument(
        "--config", type=Path, default=None, help=f"settings TOML (default: {DEFAULT_SETTINGS_PATH})"
    )
    parser.add_argument(
        "--albums-config", type=Path, default=None, help=f"albums TOML (default: {DEFAULT_ALBUMS_PATH})"
    )
    parser.add_argument("--output-dir", type=Path, default=None, help="override output.root")
    parser.add_argument("--cache-dir", type=Path, default=None, help="override cache.root")
    parser.add_argument("--no-cache", action="store_true", help="disable the HTML cache")
    parser.add_argument(
        "--delay", type=float, default=None, help="override the per-request delay in seconds"
    )
    parser.add_argument(
        "--parser", choices=["auto", "lxml", "html.parser"], default=None, help="HTML parser"
    )
    parser.add_argument("--log-level", default=None, help="DEBUG | INFO | WARNING | ERROR")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="warnings only, no progress bars"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-albums", help="list configured albums")
    list_parser.add_argument(
        "--all", action="store_true", help="include albums marked enabled = false"
    )
    list_parser.set_defaults(func=cmd_list_albums)

    scrape_parser = subparsers.add_parser("scrape", help="scrape one or more albums")
    _add_album_args(scrape_parser)
    scrape_parser.add_argument("--pages", type=int, default=None, help="cap pages per album")
    scrape_parser.add_argument("--limit", type=int, default=None, help="cap songs per album")
    scrape_parser.add_argument(
        "--refresh", action="store_true", help="ignore cache + checkpoint and re-crawl"
    )
    scrape_parser.add_argument(
        "--no-resume", action="store_true", help="discard the checkpoint before starting"
    )
    scrape_parser.add_argument(
        "--offline", action="store_true", help="use cached HTML only; never hit the network"
    )
    scrape_parser.set_defaults(func=cmd_scrape)

    transform_parser = subparsers.add_parser(
        "transform", help="rebuild JSON from cached data (no song re-crawling)"
    )
    _add_album_args(transform_parser)
    transform_parser.add_argument("--limit", type=int, default=None, help="cap songs per album")
    transform_parser.add_argument(
        "--allow-network",
        action="store_true",
        help="permit fetching the album listing if it is not cached",
    )
    transform_parser.set_defaults(func=cmd_transform)

    validate_parser = subparsers.add_parser("validate", help="validate emitted JSON")
    _add_album_args(validate_parser)
    validate_parser.set_defaults(func=cmd_validate)

    return parser


def _apply_overrides(cfg: AppConfig, args: argparse.Namespace) -> AppConfig:
    if getattr(args, "output_dir", None):
        cfg = dataclasses.replace(
            cfg, output=dataclasses.replace(cfg.output, root=Path(args.output_dir))
        )
    cache = cfg.cache
    if getattr(args, "cache_dir", None):
        cache = dataclasses.replace(cache, root=Path(args.cache_dir))
    if getattr(args, "no_cache", False):
        cache = dataclasses.replace(cache, enabled=False)
    if cache is not cfg.cache:
        cfg = dataclasses.replace(cfg, cache=cache)
    if getattr(args, "delay", None) is not None:
        cfg = dataclasses.replace(
            cfg, scraping=dataclasses.replace(cfg.scraping, delay_seconds=float(args.delay))
        )
    if getattr(args, "parser", None):
        cfg = dataclasses.replace(cfg, parser=args.parser)
    return cfg


def _resolve_albums(cfg: AppConfig, args: argparse.Namespace) -> List[AlbumSpec]:
    slugs = list(getattr(args, "album", None) or [])
    if slugs:
        resolved: List[AlbumSpec] = []
        for raw in slugs:
            slug = str(raw).strip().strip("/")
            if slug.startswith("http://") or slug.startswith("https://"):
                slug = slug.rstrip("/").rsplit("/", 1)[-1]
            spec = cfg.find_album(slug)
            if spec is None:
                logger.info("album '%s' is not in the config; scraping it ad hoc", slug)
                spec = AlbumSpec(slug=slug, enabled=True)
            resolved.append(spec)
        return resolved

    if getattr(args, "all", False):
        albums = cfg.enabled_albums()
        if not albums:
            raise SystemExit("no albums to run: nothing enabled in the albums config")
        return albums

    raise SystemExit("specify --album <slug> [...] or --all")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list_albums(cfg: AppConfig, args: argparse.Namespace) -> int:
    albums = list(cfg.albums) if getattr(args, "all", False) else cfg.enabled_albums()
    if not albums:
        print("No albums configured. Edit config/albums.toml or pass --album <slug>.")
        return 0
    print(f"{'SLUG':<40} {'ENABLED':<9} MAX_PAGES")
    print("-" * 62)
    for album in albums:
        cap = album.max_pages if album.max_pages else "auto"
        print(f"{album.slug:<40} {str(album.enabled):<9} {cap}")
    return 0


def _print_summary(result: AlbumResult) -> None:
    album_name = result.album.name if result.album else "?"
    print()
    print("=" * 68)
    print(f"  Album           : {album_name}")
    if result.album:
        print(f"  Slug            : {result.album.slug}")
        print(f"  Pages           : {result.album.total_pages}")
        if result.album.declared_total:
            print(f"  Declared total  : {result.album.declared_total}")
    print(f"  Songs found     : {result.songs_found}")
    print(f"  Songs written   : {result.songs_written}")
    if result.songs_missing:
        print(f"  Missing         : {len(result.songs_missing)}")
    if result.failures:
        print(f"  Failed          : {len(result.failures)}")
    if result.alignment_warnings:
        print(f"  Alignment warns : {len(result.alignment_warnings)}")
    print(f"  HTTP requests   : {result.http_requests}")
    print(f"  Cache hits/miss : {result.cache_hits}/{result.cache_misses}")
    print(f"  Elapsed         : {result.duration_seconds:.1f}s")
    for path in result.output_files:
        print(f"  -> {path}")
    print("=" * 68)


def cmd_scrape(cfg: AppConfig, args: argparse.Namespace) -> int:
    albums = _resolve_albums(cfg, args)
    exit_code = 0
    for spec in albums:
        logger.info("=== scraping album: %s ===", spec.slug)
        try:
            result = scrape_album(
                cfg,
                spec,
                max_pages=args.pages,
                limit=args.limit,
                refresh=args.refresh,
                resume=not args.no_resume,
                offline=args.offline,
                show_progress=not args.quiet,
            )
        except KeyboardInterrupt:
            logger.warning("interrupted - progress is checkpointed, re-run to resume")
            return 130
        except HttpError as exc:
            logger.error("album '%s' failed: %s", spec.slug, exc)
            exit_code = 1
            continue
        _print_summary(result)
        if result.failures or result.songs_missing:
            logger.warning(
                "%s: %d failure(s), %d missing - see the scrape report",
                spec.slug,
                len(result.failures),
                len(result.songs_missing),
            )
            exit_code = 1
    return exit_code


def cmd_transform(cfg: AppConfig, args: argparse.Namespace) -> int:
    albums = _resolve_albums(cfg, args)
    exit_code = 0
    for spec in albums:
        logger.info("=== rebuilding JSON for album: %s ===", spec.slug)
        try:
            result = rebuild_from_cache(
                cfg, spec, limit=args.limit, offline=not args.allow_network
            )
        except HttpError as exc:
            logger.error("album '%s' failed: %s", spec.slug, exc)
            exit_code = 1
            continue
        _print_summary(result)
    return exit_code


def cmd_validate(cfg: AppConfig, args: argparse.Namespace) -> int:
    albums = _resolve_albums(cfg, args)
    exit_code = 0
    for spec in albums:
        result = validate_album(cfg, spec)
        print(f"[{'OK' if result.ok else 'FAILED'}] {spec.slug}")
        for report in result.files:
            print(f"    {Path(report.path).name}: {report.documents} document(s)")
            for error in report.errors[:20]:
                print(f"      ERROR   {error}")
            if len(report.errors) > 20:
                print(f"      ... and {len(report.errors) - 20} more error(s)")
            for warning in report.warnings[:10]:
                print(f"      WARN    {warning}")
            if len(report.warnings) > 10:
                print(f"      ... and {len(report.warnings) - 10} more warning(s)")
        for error in result.errors[:20]:
            print(f"      ERROR   {error}")
        if not result.ok:
            exit_code = 1
    return exit_code


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg = load_config(settings_path=args.config, albums_path=args.albums_config)
    cfg = _apply_overrides(cfg, args)

    if args.verbose:
        level = "DEBUG"
    elif args.quiet:
        level = "WARNING"
    else:
        level = args.log_level or cfg.log_level
    setup_logging(level=level, log_file=cfg.log_file)

    logger.debug("working dir : %s", Path.cwd())
    logger.debug("output root : %s", cfg.output.root)

    try:
        return int(args.func(cfg, args) or 0)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        logger.warning("interrupted")
        return 130
    except HttpError as exc:
        logger.error("%s", exc)
        return 1
