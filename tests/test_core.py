"""Tests for the dedupe store and blueprint validator."""
import uuid
from src import dedupe, validator


def _valid_blueprint():
    return {
        "ad_id": "A1",
        "source_page": "TestPage",
        "captured_at": "2026-01-01T00:00:00Z",
        "format": "product_hero",
        "hook": {"type": "question", "headline_structure": "Q + benefit"},
        "awareness_stage": "problem",
        "claims": ["efficacy", "sensory"],
        "visual": {"layout": "centered", "subject": "bottle", "palette_mood": "warm", "text_placement": "top"},
        "cta": "Shop Now",
        "destination_url": "https://example.com",
    }


def test_dedupe_new_then_seen():
    dedupe.init_db()
    ad_id = f"TEST_{uuid.uuid4().hex[:8]}"
    assert dedupe.is_new(ad_id) is True
    dedupe.mark_seen(ad_id, "TestPage")
    assert dedupe.is_new(ad_id) is False


def test_dedupe_double_mark_is_safe():
    dedupe.init_db()
    ad_id = f"TEST_{uuid.uuid4().hex[:8]}"
    dedupe.mark_seen(ad_id, "TestPage")
    dedupe.mark_seen(ad_id, "TestPage")  # must not raise
    assert dedupe.is_new(ad_id) is False


def test_valid_blueprint_passes():
    assert validator.is_valid(_valid_blueprint()) is True


def test_missing_required_field_fails():
    bp = _valid_blueprint()
    del bp["cta"]
    assert validator.is_valid(bp) is False


def test_bad_enum_value_fails():
    bp = _valid_blueprint()
    bp["awareness_stage"] = "not_a_real_stage"
    assert validator.is_valid(bp) is False