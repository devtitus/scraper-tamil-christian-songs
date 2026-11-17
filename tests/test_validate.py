"""Tests for the JSON structure validator."""

from __future__ import annotations

from dataclasses import replace as dataclass_replace

from lyrics_scraper.config import load_config
from lyrics_scraper.output import atomic_write_json
from lyrics_scraper.validate import validate_album
from lyrics_scraper.config import AlbumSpec


def _write_dummy_album(tmp_path, slug, docs):
    out_dir = tmp_path / "out" / slug
    out_dir.mkdir(parents=True)
    atomic_write_json(out_dir / "tamil.json", docs, ensure_ascii=False, indent=2)
    atomic_write_json(out_dir / "tanglish.json", docs, ensure_ascii=False, indent=2)


def _valid_doc(hymn_number: int) -> dict:
    return {
        "parts": [
            {
                "title": "Part I",
                "stanzas": [{"lines": ["a", "b"], "number": 1}],
                "part_number": 1,
            }
        ],
        "bridge": None,
        "chorus": None,
        "metadata": {
            "has_bridge": False,
            "has_chorus": False,
            "part_count": 1,
            "hymn_number": hymn_number,
            "verse_count": 1,
            "stanza_format": "plain",
        },
        "full_text": f"Hymn No. {hymn_number} 1. a b",
    }


def test_valid_album_passes(tmp_path):
    slug = "test-album"
    cfg = load_config()
    cfg = dataclass_replace(cfg, output=dataclass_replace(cfg.output, root=tmp_path / "out"))
    _write_dummy_album(tmp_path, slug, [_valid_doc(1), _valid_doc(2)])

    spec = AlbumSpec(slug=slug, enabled=True)
    result = validate_album(cfg, spec)
    assert result.ok is True
    assert result.files[0].documents == 2
    assert result.files[1].documents == 2


def test_null_fields_are_allowed(tmp_path):
    """All-null entry must validate cleanly."""
    doc = {
        "parts": None,
        "bridge": None,
        "chorus": None,
        "metadata": {
            "has_bridge": False,
            "has_chorus": False,
            "part_count": 0,
            "hymn_number": 1,
            "verse_count": 0,
            "stanza_format": None,
        },
        "full_text": None,
    }
    slug = "test-album"
    cfg = load_config()
    cfg = dataclass_replace(cfg, output=dataclass_replace(cfg.output, root=tmp_path / "out"))
    _write_dummy_album(tmp_path, slug, [doc])

    spec = AlbumSpec(slug=slug, enabled=True)
    result = validate_album(cfg, spec)
    assert result.ok is True


def test_missing_metadata_key_fails(tmp_path):
    doc = {
        "parts": None,
        "bridge": None,
        "chorus": None,
        "metadata": {
            "has_bridge": False,
            "has_chorus": False,
            "part_count": 0,
            "hymn_number": 5,
            "stanza_format": None,
        },
        "full_text": None,
    }
    slug = "test-album"
    cfg = load_config()
    cfg = dataclass_replace(cfg, output=dataclass_replace(cfg.output, root=tmp_path / "out"))
    _write_dummy_album(tmp_path, slug, [doc])

    spec = AlbumSpec(slug=slug, enabled=True)
    result = validate_album(cfg, spec)
    assert result.ok is False
