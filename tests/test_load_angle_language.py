"""Tests for scripts/load_angle_language.py's best_verbatims parsing (2026-08-19,
fabricated-named-testimonials fix - see CLAUDE.md). Pure parsing tests, no DB
connection - _parse_best_verbatims/_parse_attribution operate on plain text."""
from scripts import load_angle_language as lal


def test_parse_attribution_plain_name_no_age():
    assert lal._parse_attribution("Tiffany W.") == ("Tiffany W.", None)


def test_parse_attribution_name_with_real_age():
    assert lal._parse_attribution("Mona S., 68") == ("Mona S.", "68")


def test_parse_attribution_name_with_decade_age():
    assert lal._parse_attribution("Cherilyn C., 70s") == ("Cherilyn C.", "70s")


def test_parse_attribution_name_with_non_age_descriptor_drops_the_descriptor():
    """'3 months of use' and 'Florida golfer' share the comma position with a real
    age but are not ages - must not be misparsed as one, and the descriptor itself
    is not captured anywhere (only quote/customer_name/age are asked for)."""
    assert lal._parse_attribution("Sharon H., 3 months of use") == ("Sharon H.", None)
    assert lal._parse_attribution("Sandra K., Florida golfer") == ("Sandra K.", None)


def test_parse_attribution_verified_customer_kept_as_generic():
    assert lal._parse_attribution("verified customer") == ("a verified customer", None)
    assert lal._parse_attribution("customer") == ("a verified customer", None)


def test_parse_attribution_sentiment_label_excluded():
    """A composite/aggregate sentiment summary across many reviews is not one real
    customer's own words - excluded entirely (None), never loaded with a made-up or
    generic name."""
    assert lal._parse_attribution("weight loss customer sentiment") is None
    assert lal._parse_attribution("customer sentiment across multiple reviews") is None


def test_parse_best_verbatims_extracts_quote_and_attribution_pairs():
    lines = [
        '"First quote here." — Jane D.',
        '"Second quote here." — Mona S., 68',
    ]
    entries = lal._parse_best_verbatims(lines)
    assert entries == [
        {"quote": "First quote here.", "customer_name": "Jane D.", "age": None},
        {"quote": "Second quote here.", "customer_name": "Mona S.", "age": "68"},
    ]


def test_parse_best_verbatims_handles_em_dash_inside_the_quote():
    """A real quote can itself contain an em-dash (e.g. 'they feel so much firmer.')
    - the quote/attribution boundary must be the closing literal '"', never the
    first em-dash encountered."""
    lines = ['"My arms feel like I have been working out — they feel so much '
             'firmer." — Tara C.']
    entries = lal._parse_best_verbatims(lines)
    assert entries == [{
        "quote": "My arms feel like I have been working out — they feel so much firmer.",
        "customer_name": "Tara C.", "age": None,
    }]


def test_parse_best_verbatims_handles_wrapped_markdown_lines():
    """The doc word-wraps a single quote/attribution across multiple markdown lines -
    must flatten to one entry, not split into two or corrupt the boundary."""
    lines = [
        '"I thought I would need a leg lift in 5 years. That\'s not even a thought',
        'anymore." — Sharon H., 3',
        'months of use',
    ]
    entries = lal._parse_best_verbatims(lines)
    assert entries == [{
        "quote": "I thought I would need a leg lift in 5 years. That's not even a thought anymore.",
        "customer_name": "Sharon H.", "age": None,
    }]


def test_parse_best_verbatims_excludes_sentiment_entries_from_the_list():
    lines = [
        '"Skin that doesn\'t fit the body I worked so hard for." — weight loss customer sentiment',
        '"A real quote." — Jane D.',
    ]
    entries = lal._parse_best_verbatims(lines)
    assert entries == [{"quote": "A real quote.", "customer_name": "Jane D.", "age": None}]


def test_parse_doc_loads_best_verbatims_for_every_angle():
    """End-to-end against the real doc: every angle gets a non-empty, parsed
    best_verbatims list - the exact gap this fix closes (previously always [])."""
    from pathlib import Path
    text = lal.DOC_PATH.read_text(encoding="utf-8")
    rows, _ = lal.parse_doc(text)
    for slug in lal.ANGLE_SLUGS.values():
        bv = rows[slug]["best_verbatims"]
        assert bv, f"{slug} has no best_verbatims parsed"
        for entry in bv:
            assert entry["quote"], f"{slug} has an entry with an empty quote"
            assert entry["customer_name"], f"{slug} has an entry with no customer_name"
            assert "sentiment" not in entry["customer_name"].lower()


def test_parse_doc_menopause_excludes_the_two_sentiment_entries():
    """Regression lock for the specific real doc content: menopause's raw section has
    12 attributions, 1 of which ('customer sentiment across multiple reviews') must
    be excluded, leaving exactly 11."""
    text = lal.DOC_PATH.read_text(encoding="utf-8")
    rows, _ = lal.parse_doc(text)
    bv = rows["menopause"]["best_verbatims"]
    assert len(bv) == 11
    assert sum(1 for v in bv if v["customer_name"] == "a verified customer") == 4
