"""Typed configuration loaded from TOML (stdlib ``tomllib``).

PyYAML is deliberately not used: it ships a C extension with no wheel for
Python 3.15, whereas ``tomllib`` has been in the stdlib since 3.11.

Every key has a default, so a partial or hand-trimmed config file still loads.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.toml"
DEFAULT_ALBUMS_PATH = PROJECT_ROOT / "config" / "albums.toml"

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _section(data: Mapping[str, Any], name: str) -> Dict[str, Any]:
    value = data.get(name)
    return dict(value) if isinstance(value, Mapping) else {}


def _get(section: Mapping[str, Any], key: str, default: Any) -> Any:
    value = section.get(key, default)
    return default if value is None else value


def _as_path(value: Any, base: Path) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else (base / path)


@dataclass(frozen=True)
class SiteConfig:
    base_url: str = "https://tamilchristiansongs.in"
    album_path: str = "/album/{slug}/"
    lyrics_path: str = "/lyrics/{slug}/"

    @property
    def root(self) -> str:
        return self.base_url.rstrip("/")

    def album_url(self, slug: str, page: Optional[int] = None) -> str:
        """Build an album URL.

        Page 1 is requested without a ``?page=`` query so it matches the
        canonical URL the site itself emits.
        """
        url = f"{self.root}{self.album_path.format(slug=slug)}"
        if page and page > 1:
            url = f"{url}?page={page}"
        return url

    def lyrics_url(self, slug: str) -> str:
        return f"{self.root}{self.lyrics_path.format(slug=slug)}"


@dataclass(frozen=True)
class ScrapingConfig:
    delay_seconds: float = 10.0
    delay_jitter_seconds: float = 1.5
    timeout_seconds: float = 30.0
    max_retries: int = 4
    backoff_factor: float = 2.0
    respect_robots_crawl_delay: bool = True
    user_agent: str = _DEFAULT_USER_AGENT


@dataclass(frozen=True)
class SchemaConfig:
    """Rules that shape the emitted JSON document."""

    chorus_explicit_markers: tuple = ("chorus", "refrain")
    bridge_markers: tuple = ("bridge",)
    chorus_repeat_heuristic: bool = False
    stanza_format_numbered: str = "numbered"
    stanza_format_plain: str = "plain"
    full_text_prefix: str = "Hymn No. {n}"
    full_text_chorus_label: str = "Chorus :"
    include_identity: bool = False


@dataclass(frozen=True)
class OutputConfig:
    root: Path = PROJECT_ROOT / "data" / "output"
    tamil_file: str = "tamil.json"
    tanglish_file: str = "tanglish.json"
    report_file: str = "_scrape_report.json"
    ensure_ascii: bool = False
    indent: int = 2


@dataclass(frozen=True)
class CacheConfig:
    enabled: bool = True
    root: Path = PROJECT_ROOT / "data" / "cache"


@dataclass(frozen=True)
class ResumeConfig:
    enabled: bool = True
    state_dir: Path = PROJECT_ROOT / "data" / "state"


@dataclass(frozen=True)
class AlbumSpec:
    """One configurable album entry from ``config/albums.toml``."""

    slug: str
    enabled: bool = True
    max_pages: int = 0  # 0 (or omitted) -> auto-detect from the pagination nav
    name: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AlbumSpec":
        slug = str(_get(raw, "slug", "") or "").strip().strip("/")
        if not slug:
            raise ValueError("album entry is missing a 'slug'")
        return cls(
            slug=slug,
            enabled=bool(_get(raw, "enabled", True)),
            max_pages=int(_get(raw, "max_pages", 0) or 0),
            name=(str(raw["name"]) if raw.get("name") else None),
        )


@dataclass(frozen=True)
class AppConfig:
    site: SiteConfig = field(default_factory=SiteConfig)
    scraping: ScrapingConfig = field(default_factory=ScrapingConfig)
    parser: str = "auto"
    schema: SchemaConfig = field(default_factory=SchemaConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    resume: ResumeConfig = field(default_factory=ResumeConfig)
    log_level: str = "INFO"
    log_file: Optional[Path] = None
    albums: tuple = ()

    def enabled_albums(self) -> List[AlbumSpec]:
        return [a for a in self.albums if a.enabled]

    def find_album(self, slug: str) -> Optional[AlbumSpec]:
        needle = str(slug).strip().strip("/")
        for album in self.albums:
            if album.slug == needle:
                return album
        return None


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_toml(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _tuple_of_strings(value: Any, default: Sequence[str]) -> tuple:
    if value is None:
        return tuple(default)
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return tuple(default)


def load_settings(path: Optional[Path] = None, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    settings_path = Path(path) if path else DEFAULT_SETTINGS_PATH
    base = Path(base_dir) if base_dir else PROJECT_ROOT
    raw = _load_toml(settings_path)

    site_raw = _section(raw, "site")
    scrape_raw = _section(raw, "scraping")
    schema_raw = _section(raw, "schema")
    output_raw = _section(raw, "output")
    cache_raw = _section(raw, "cache")
    resume_raw = _section(raw, "resume")
    logging_raw = _section(raw, "logging")
    parsing_raw = _section(raw, "parsing")

    log_file = _get(logging_raw, "file", None)

    return {
        "site": SiteConfig(
            base_url=str(_get(site_raw, "base_url", "https://tamilchristiansongs.in")),
            album_path=str(_get(site_raw, "album_path", "/album/{slug}/")),
            lyrics_path=str(_get(site_raw, "lyrics_path", "/lyrics/{slug}/")),
        ),
        "scraping": ScrapingConfig(
            delay_seconds=float(_get(scrape_raw, "delay_seconds", 10.0)),
            delay_jitter_seconds=float(_get(scrape_raw, "delay_jitter_seconds", 1.5)),
            timeout_seconds=float(_get(scrape_raw, "timeout_seconds", 30.0)),
            max_retries=int(_get(scrape_raw, "max_retries", 4)),
            backoff_factor=float(_get(scrape_raw, "backoff_factor", 2.0)),
            respect_robots_crawl_delay=bool(
                _get(scrape_raw, "respect_robots_crawl_delay", True)
            ),
            user_agent=str(_get(scrape_raw, "user_agent", _DEFAULT_USER_AGENT)),
        ),
        "parser": str(_get(parsing_raw, "parser", "auto")),
        "schema": SchemaConfig(
            chorus_explicit_markers=_tuple_of_strings(
                schema_raw.get("chorus_explicit_markers"), ("chorus", "refrain")
            ),
            bridge_markers=_tuple_of_strings(schema_raw.get("bridge_markers"), ("bridge",)),
            chorus_repeat_heuristic=bool(_get(schema_raw, "chorus_repeat_heuristic", False)),
            stanza_format_numbered=str(_get(schema_raw, "stanza_format_numbered", "numbered")),
            stanza_format_plain=str(_get(schema_raw, "stanza_format_plain", "plain")),
            full_text_prefix=str(_get(schema_raw, "full_text_prefix", "Hymn No. {n}")),
            full_text_chorus_label=str(_get(schema_raw, "full_text_chorus_label", "Chorus :")),
            include_identity=bool(_get(schema_raw, "include_identity", False)),
        ),
        "output": OutputConfig(
            root=_as_path(_get(output_raw, "root", "data/output"), base),
            tamil_file=str(_get(output_raw, "tamil_file", "tamil.json")),
            tanglish_file=str(_get(output_raw, "tanglish_file", "tanglish.json")),
            report_file=str(_get(output_raw, "report_file", "_scrape_report.json")),
            ensure_ascii=bool(_get(output_raw, "ensure_ascii", False)),
            indent=int(_get(output_raw, "indent", 2)),
        ),
        "cache": CacheConfig(
            enabled=bool(_get(cache_raw, "enabled", True)),
            root=_as_path(_get(cache_raw, "root", "data/cache"), base),
        ),
        "resume": ResumeConfig(
            enabled=bool(_get(resume_raw, "enabled", True)),
            state_dir=_as_path(_get(resume_raw, "state_dir", "data/state"), base),
        ),
        "log_level": str(_get(logging_raw, "level", "INFO")),
        "log_file": _as_path(log_file, base) if log_file else None,
    }


def load_albums(path: Optional[Path] = None) -> List[AlbumSpec]:
    raw = _load_toml(Path(path) if path else DEFAULT_ALBUMS_PATH)
    entries = raw.get("albums") or []
    if isinstance(entries, Mapping):  # tolerate a single [albums] table
        entries = [entries]
    albums: List[AlbumSpec] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            albums.append(AlbumSpec.from_dict(entry))
    return albums


def load_config(
    settings_path: Optional[Path] = None,
    albums_path: Optional[Path] = None,
    base_dir: Optional[Path] = None,
) -> AppConfig:
    """Load settings + albums into one immutable :class:`AppConfig`."""
    parts = load_settings(settings_path, base_dir=base_dir)
    parts["albums"] = tuple(load_albums(albums_path))
    return AppConfig(**parts)
