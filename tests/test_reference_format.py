"""Tests for the reference-format FLAG (Prompt 4, Item 4). Detected purely from data
already in the blueprint - never a filter, never gates generation, only surfaced on the
card for a human to weigh."""
from src import reference_format as rf


def test_multi_product_count_flagged():
    bp = {"layout_detail": {"product_count": 6}}
    reason = rf.format_flag_reason(bp)
    assert reason is not None
    assert "6 products" in reason


def test_single_product_count_not_flagged():
    bp = {"layout_detail": {"product_count": 1}}
    assert rf.format_flag_reason(bp) is None


def test_offer_led_format_flagged():
    bp = {"creative_format": "offer_led"}
    reason = rf.format_flag_reason(bp)
    assert reason is not None
    assert "offer_led" in reason


def test_comparison_format_flagged():
    bp = {"creative_format": "comparison"}
    assert rf.format_flag_reason(bp) is not None


def test_other_creative_formats_not_flagged_on_their_own():
    for fmt in ("testimonial_review", "product_hero", "lifestyle_scene"):
        bp = {"creative_format": fmt}
        assert rf.format_flag_reason(bp) is None


# ---- Bundle mechanic detection ----

def test_bundle_keyword_in_offer_mechanic_flagged():
    bp = {"offer": {"type": "bundle", "value": "", "mechanic": "buy the full ritual bundle"}}
    reason = rf.format_flag_reason(bp)
    assert reason is not None
    assert "bundle" in reason.lower()


def test_bundle_quantity_pattern_flagged():
    bp = {"offer": {"type": "", "value": "", "mechanic": "5 for $109"}}
    assert rf.format_flag_reason(bp) is not None


def test_value_pack_keyword_flagged():
    bp = {"offer": {"mechanic": "limited-edition value pack"}}
    assert rf.format_flag_reason(bp) is not None


def test_ordinary_single_item_offer_not_flagged():
    bp = {"offer": {"type": "discount", "value": "20%", "mechanic": "20% off first order"}}
    assert rf.format_flag_reason(bp) is None


def test_no_offer_present_not_flagged():
    bp = {"offer": None}
    assert rf.format_flag_reason(bp) is None


# ---- Combination phrasing matches the spec's own example exactly ----

def test_product_count_and_bundle_together_matches_spec_example():
    bp = {"layout_detail": {"product_count": 6}, "offer": {"mechanic": "5 for $109 bundle"}}
    reason = rf.format_flag_reason(bp)
    assert reason == "reference was a 6-product bundle offer"


# ---- Robustness ----

def test_empty_blueprint_not_flagged():
    assert rf.format_flag_reason({}) is None
    assert rf.format_flag_reason(None) is None


def test_missing_layout_detail_and_offer_not_flagged():
    bp = {"format": "hero", "angle": "confidence"}
    assert rf.format_flag_reason(bp) is None


def test_non_numeric_product_count_does_not_crash():
    bp = {"layout_detail": {"product_count": "unknown"}}
    assert rf.format_flag_reason(bp) is None
