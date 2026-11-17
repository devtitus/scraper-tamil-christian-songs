"""Tests for the song page parser."""

from __future__ import annotations

from lyrics_scraper.models import SongRef
from lyrics_scraper.parsers.lyrics_page import parse_lyrics_page

BASE = "https://tamilchristiansongs.in"


def _ref(slug: str) -> SongRef:
    return SongRef(
        slug=slug,
        url=f"{BASE}/lyrics/{slug}/",
        title_roman="",
        title_tamil="",
        page=1,
        index_in_page=1,
    )


def test_plain_song_titles(fixture_html):
    parsed = parse_lyrics_page(
        fixture_html("lyrics_plain.html"), ref=_ref("song-one"), base_url=BASE
    )
    assert parsed.title_combined == "Song One - பாடல் ஒன்று"
    assert parsed.ref.title_roman == "Song One"
    assert parsed.ref.title_tamil == "பாடல் ஒன்று"


def test_plain_song_tamil_stanzas(fixture_html):
    """<p> per stanza, <br/> per line, plus the literal newline that follows
    every <br/> in the real markup."""
    parsed = parse_lyrics_page(
        fixture_html("lyrics_plain.html"), ref=_ref("song-one"), base_url=BASE
    )
    assert parsed.tamil_stanzas == [
        [
            "ஆபத்து நாளில் கர்த்தர்",
            "என் ஜெபம் கேட்கின்றீர்",
            "யாக்கோபின் தேவனின்",
            "நாமம் பாதுகாக்கின்றது",
        ],
        ["என் துணையாளர் நீர்தானே", "சகாயர் நீர்தானே", "நீர்தானே என் துணையாளர்"],
    ]


def test_plain_song_tanglish_stanzas(fixture_html):
    parsed = parse_lyrics_page(
        fixture_html("lyrics_plain.html"), ref=_ref("song-one"), base_url=BASE
    )
    assert parsed.tanglish_stanzas == [
        [
            "aapaththu Naalil Karththar",
            "en Jepam Kaetkinteer",
            "Yaakkopin Thaevanin",
            "Naamam Paathukaakkintathu",
        ],
        [
            "en Thunnaiyaalar Neerthaanae",
            "Sakaayar Neerthaanae",
            "Neerthaanae En Thunnaiyaalar",
        ],
    ]


def test_tamil_and_tanglish_are_aligned(fixture_html):
    parsed = parse_lyrics_page(
        fixture_html("lyrics_plain.html"), ref=_ref("song-one"), base_url=BASE
    )
    assert parsed.alignment_ok is True


def test_extras(fixture_html):
    parsed = parse_lyrics_page(
        fixture_html("lyrics_plain.html"), ref=_ref("song-one"), base_url=BASE
    )
    assert parsed.ppt_url == f"{BASE}/lyrics/song-one/ppt/"
    assert parsed.hashtags == ["நீர்தானே", "துணையாளர்"]
    assert parsed.english_meaning == "Lord in the day of danger\nYou hear my prayer"


def test_chorus_markers_stay_in_raw_stanzas(fixture_html):
    """The parser must not interpret sections - transform.py owns that."""
    parsed = parse_lyrics_page(
        fixture_html("lyrics_chorus.html"), ref=_ref("bakthare-varum"), base_url=BASE
    )
    assert len(parsed.tamil_stanzas) == 6
    # Tanglish block uses blank-line separation -> 4 stanzas (not 6).
    assert len(parsed.tanglish_stanzas) == 4
    # Cross-check correctly flags the mismatch in this constructed fixture.
    assert parsed.alignment_ok is False
    assert parsed.tanglish_stanzas[1] == ["Chorus :", "Sashtangam seiya varum", "Sashtangam seiya varum yesuvai"]
    assert parsed.tamil_stanzas[1] == ["தொடர்பல்லவி :"]
    assert parsed.tanglish_stanzas[0] == ["1. Bakthare varum", "Asai avalodum"]


def test_missing_translated_content_is_nullable(fixture_html):
    parsed = parse_lyrics_page(
        fixture_html("lyrics_chorus.html"), ref=_ref("bakthare-varum"), base_url=BASE
    )
    assert parsed.english_meaning is None
    assert parsed.ppt_url is None
    assert parsed.hashtags == []
