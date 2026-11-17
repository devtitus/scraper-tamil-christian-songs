"""Shared pytest fixtures. All tests are offline."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from lyrics_scraper.config import load_config

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_html():
    """Return a callable that loads a fixture file as UTF-8 text."""

    def _load(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return _load


@pytest.fixture
def tmp_cfg(tmp_path):
    """AppConfig redirected into a temporary directory (no writes to the repo)."""
    cfg = load_config()
    return dataclasses.replace(
        cfg,
        output=dataclasses.replace(cfg.output, root=tmp_path / "out"),
        cache=dataclasses.replace(cfg.cache, root=tmp_path / "cache", enabled=True),
        resume=dataclasses.replace(cfg.resume, state_dir=tmp_path / "state"),
        log_file=None,
    )
