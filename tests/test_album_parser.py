"""Tests for the album listing page parser."""

from __future__ import annotations

import pytest

from lyrics_scraper.parsers.album_page import estimate_pages, parse_album_page

BASE = "https://tamilchristiansongs.in"
SLUG = "test-album"
ALBUM_URL = f"{BASE}/album/{SLUG}/"


def _parse(html: str, page: int = 1, url: str = ALBUM_URL):
    return parse_album_page(
        html, base_url=BASE, slug=SLUG, page=page, album_url=url
    )


def test_album_metadata(fixture_html):
    result = _parse(fixture_html("album_page_1.html"))
    assert result.album.name == "Test Album"
    assert result.album.display_title == "Test Album Songs Lyrics"
    assert result.album.declared_total == 101
    assert result.album.songs_per_page == 3


def test_total_pages_comes_from_pagination_nav(fixture_html):
    """The nav is authoritative: probing is unsafe because out-of-range
    ``?page=N`` returns HTTP 200 with page-1 content."""
    result = _parse(fixture_html("album_page_1.html"))
    assert result.album.total_pages == 3


def test_songs_in_order_with_absolute_urls(fixture_html):
    result = _parse(fixture_html("album_page_1.html"))
    assert [song.slug for song in result.songs] == ["song-one", "song-two", "song-three"]
    assert [song.index_in_page for song in result.songs] == [1, 2, 3]

    first = result.songs[0]
    assert first.title_roman == "Song One"
    assert first.title_tamil == "பாடல் ஒன்று"
    assert first.url == f"{BASE}/lyrics/song-one/"
    assert first.page == 1


def test_sidebar_and_decoy_links_are_ignored(fixture_html):
    """The aside contains /lyrics/, /artist/ and a card without an <h2>."""
    result = _parse(fixture_html("album_page_1.html"))
    slugs = {song.slug for song in result.songs}
    assert slugs == {"song-one", "song-two", "song-three"}


def test_tamil_titles_survive_utf8_decoding(fixture_html):
    for song in _parse(fixture_html("album_page_1.html")).songs:
        assert "\ufffd" not in song.title_tamil
        assert song.title_tamil


def test_partial_last_page(fixture_html):
    html = fixture_html("album_page_3.html")
    result = _parse(html, page=3, url=ALBUM_URL + "?page=3")
    assert [song.slug for song in result.songs] == ["song-last"]
    assert result.songs[0].title_tamil == "கடைசி பாடல்"
    assert result.album.total_pages == 3


@pytest.mark.parametrize(
    "total,per_page,expected",
    [(364, 50, 8), (100, 50, 2), (50, 50, 1), (1, 50, 1), (None, 50, None), (364, 0, None)],
)
def test_estimate_pages(total, per_page, expected):
    assert estimate_pages(total, per_page) == expected
