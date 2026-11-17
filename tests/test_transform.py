"""Tests for the schema transform.

These lock the exact document shape the user specified, including key ORDER and
the rule that absent values are emitted as ``null`` rather than omitted.
"""

from __future__ import annotations

import json

import pytest

from lyrics_scraper.config import SchemaConfig
from lyrics_scraper.transform import build_document, to_roman

TOP_KEYS = ["parts", "bridge", "chorus", "metadata", "full_text"]
META_KEYS = [
    "has_bridge",
    "has_chorus",
    "part_count",
    "hymn_number",
    "verse_count",
    "stanza_format",
]

REFERENCE_STANZAS = [
    ["1. Bakthare varum", "Asai avalodum"],
    ["Chorus :"],
    ["Sashtangam seiya varum", "Sashtangam seiya varum yesuvai"],
    ["2. Thevathi theva", "Jothiyil jyothi"],
]

def test_top_level_key_order_is_exact():
    doc = build_document(REFERENCE_STANZAS, hymn_number=7, cfg=SchemaConfig())
    assert list(doc.keys()) == TOP_KEYS


def test_metadata_key_order_is_exact():
    doc = build_document(REFERENCE_STANZAS, hymn_number=7, cfg=SchemaConfig())
    assert list(doc["metadata"].keys()) == META_KEYS


def test_reference_sample_reproduced_exactly():
    doc = build_document(REFERENCE_STANZAS, hymn_number=7, cfg=SchemaConfig())

    assert doc["bridge"] is None
    assert doc["chorus"] == ["Sashtangam seiya varum", "Sashtangam seiya varum yesuvai"]
    assert doc["metadata"] == {
        "has_bridge": False,
        "has_chorus": True,
        "part_count": 1,
        "hymn_number": 7,
        "verse_count": 2,
        "stanza_format": "numbered",
    }

    assert len(doc["parts"]) == 1
    part = doc["parts"][0]
    assert list(part.keys()) == ["title", "stanzas", "part_number"]
    assert part["title"] == "Part I"
    assert part["part_number"] == 1
    assert part["stanzas"] == [
        {"lines": ["Bakthare varum", "Asai avalodum"], "number": 1},
        {"lines": ["Thevathi theva", "Jothiyil jyothi"], "number": 2},
    ]

    assert doc["full_text"] == (
        "Hymn No. 7 1. Bakthare varum Asai avalodum "
        "Chorus : Sashtangam seiya varum Sashtangam seiya varum yesuvai "
        "2. Thevathi theva Jothiyil jyothi"
    )


def test_plain_song_without_markers():
    doc = build_document([["a", "b"], ["c", "d"]], hymn_number=1, cfg=SchemaConfig())
    assert doc["chorus"] is None
    assert doc["bridge"] is None
    assert doc["metadata"]["stanza_format"] == "plain"
    assert doc["metadata"]["verse_count"] == 2
    assert doc["metadata"]["has_chorus"] is False
    assert doc["full_text"] == "Hymn No. 1 1. a b 2. c d"


def test_empty_input_still_emits_every_key():
    """The user's rule: keep the structure identical, absent values as null."""
    doc = build_document([], hymn_number=3, cfg=SchemaConfig())
    assert list(doc.keys()) == TOP_KEYS
    assert doc["parts"] is None
    assert doc["bridge"] is None
    assert doc["chorus"] is None
    assert doc["full_text"] is None
    assert list(doc["metadata"].keys()) == META_KEYS
    assert doc["metadata"] == {
        "has_bridge": False,
        "has_chorus": False,
        "part_count": 0,
        "hymn_number": 3,
        "verse_count": 0,
        "stanza_format": None,
    }


def test_whitespace_only_input_is_treated_as_empty():
    doc = build_document([["", "  "], ["\n"]], hymn_number=4, cfg=SchemaConfig())
    assert doc["parts"] is None
    assert doc["full_text"] is None
    assert doc["metadata"]["verse_count"] == 0


def test_bridge_marker_detected():
    doc = build_document(
        [["verse one"], ["Bridge :"], ["bridge line"]], hymn_number=1, cfg=SchemaConfig()
    )
    assert doc["bridge"] == ["bridge line"]
    assert doc["metadata"]["has_bridge"] is True
    assert doc["metadata"]["verse_count"] == 1
    assert "bridge line" not in doc["full_text"]

def test_inline_marker_on_the_same_line():
    doc = build_document(
        [["1. verse one", "Chorus : la la la"]], hymn_number=1, cfg=SchemaConfig()
    )
    assert doc["chorus"] == ["la la la"]
    assert doc["metadata"]["verse_count"] == 1


def test_word_starting_with_a_marker_is_not_a_marker():
    doc = build_document(
        [["Chorusline is an ordinary word"], ["verse"]], hymn_number=1, cfg=SchemaConfig()
    )
    assert doc["chorus"] is None
    assert doc["metadata"]["verse_count"] == 2


def test_tamil_markers():
    cfg = SchemaConfig(
        chorus_explicit_markers=("chorus", "தொடர்பல்லவி"),
        bridge_markers=("bridge", "பாலம்"),
    )
    doc = build_document(
        [
            ["1. பக்தரே வரும்"],
            ["தொடர்பல்லவி :"],
            ["சாஷ்டாங்கம் செய்ய வரும்"],
            ["2. தேவதி தேவா"],
            ["பாலம் :"],
            ["பாலம் வரி ஒன்று"],
        ],
        hymn_number=1,
        cfg=cfg,
    )
    assert doc["chorus"] == ["சாஷ்டாங்கம் செய்ய வரும்"]
    assert doc["bridge"] == ["பாலம் வரி ஒன்று"]
    assert doc["metadata"]["verse_count"] == 2
    assert doc["metadata"]["stanza_format"] == "numbered"


def test_repeat_heuristic_is_off_by_default():
    stanzas = [["refrain line"], ["verse a"], ["refrain line"]]
    doc = build_document(stanzas, hymn_number=1, cfg=SchemaConfig())
    assert doc["chorus"] is None
    assert doc["metadata"]["verse_count"] == 3


def test_repeat_heuristic_when_enabled():
    stanzas = [["refrain line"], ["verse a"], ["refrain line"]]
    cfg = SchemaConfig(chorus_repeat_heuristic=True)
    doc = build_document(stanzas, hymn_number=1, cfg=cfg)
    assert doc["chorus"] == ["refrain line"]
    assert doc["metadata"]["has_chorus"] is True
    assert doc["metadata"]["verse_count"] == 1


def test_include_identity_only_extends_metadata():
    cfg = SchemaConfig(include_identity=True)
    doc = build_document(
        [["a"]],
        hymn_number=1,
        cfg=cfg,
        identity={"title": "T", "title_tamil": "ட", "slug": "s", "url": "u"},
    )
    assert list(doc.keys()) == TOP_KEYS
    keys = list(doc["metadata"].keys())
    assert keys[:6] == META_KEYS
    assert keys[6:] == ["title", "title_tamil", "slug", "url"]


def test_identity_ignored_when_disabled():
    doc = build_document(
        [["a"]], hymn_number=1, cfg=SchemaConfig(), identity={"slug": "s"}
    )
    assert list(doc["metadata"].keys()) == META_KEYS


def test_document_is_json_serialisable_with_tamil_intact():
    doc = build_document(
        [["ஆபத்து நாளில் கர்த்தர்"]], hymn_number=1, cfg=SchemaConfig()
    )
    text = json.dumps(doc, ensure_ascii=False)
    assert "ஆபத்து நாளில் கர்த்தர்" in text


@pytest.mark.parametrize("number,expected", [(1, "I"), (2, "II"), (4, "IV"), (9, "IX"), (12, "XII")])
def test_to_roman(number, expected):
    assert to_roman(number) == expected


