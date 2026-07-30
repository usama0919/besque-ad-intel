"""Tests for the deconstruction step — mocks Claude's response, no API call."""
import json
import pytest
from src import deconstruct


def _fake_claude_json():
    return json.dumps({
        "ad_id": "AD123",
        "source_page": "CompetitorPage",
        "captured_at": "2026-01-01T00:00:00Z",
        "format": "testimonial_card",
        "hook": {"type": "social_proof", "headline_structure": "quote + result"},
        "angle": "confidence at any age",
        "awareness_stage": "solution",
        "claims": ["efficacy", "social_proof"],
        "visual": {"layout": "portrait", "subject": "woman", "palette_mood": "warm", "text_placement": "lower third"},
        "cta": "Shop Now",
        "destination_url": "https://example.com",
    })


def test_parse_plain_json():
    raw = _fake_claude_json()
    bp = deconstruct.parse_blueprint(raw)
    assert bp["ad_id"] == "AD123"


def test_parse_strips_markdown_fences():
    raw = "```json\n" + _fake_claude_json() + "\n```"
    bp = deconstruct.parse_blueprint(raw)
    assert bp["format"] == "testimonial_card"


def test_deconstruct_valid_response_passes_schema():
    raw = _fake_claude_json()
    bp = deconstruct.deconstruct_from_response(raw)
    assert bp["awareness_stage"] == "solution"


def test_deconstruct_invalid_response_raises():
    bad = json.dumps({"ad_id": "X"})  # missing required fields
    with pytest.raises(ValueError):
        deconstruct.deconstruct_from_response(bad)


def test_build_prompt_inserts_values():
    prompt = deconstruct.build_prompt("AD1", "PageX", "2026-01-01", "https://x.com")
    assert "AD1" in prompt and "PageX" in prompt


def test_build_prompt_production_style_options_match_validator():
    """production_style_options is built from validator.production_styles(), not a
    repeated literal - so it can't drift from what validation_error() actually accepts."""
    from src import validator
    prompt = deconstruct.build_prompt("AD1", "PageX", "2026-01-01")
    for style in validator.production_styles():
        assert style in prompt
    assert "illustrated" in prompt


def test_build_prompt_includes_new_creative_fields_without_format_error():
    """Part B: creative_objective/target_audience/typography/expanded layout_detail were
    added to BLUEPRINT_PROMPT, which is itself run through .format() in build_prompt() -
    an unescaped literal brace in any of the new text would raise KeyError here."""
    prompt = deconstruct.build_prompt("AD1", "PageX", "2026-01-01")
    assert "creative_objective" in prompt
    assert "target_audience" in prompt
    assert "typography" in prompt
    assert "headline_face" in prompt
    assert "hierarchy_levels" in prompt
    assert "zone_positions" in prompt
    assert "has_bottom_banner" in prompt
    assert "frame_division" in prompt


def test_deconstruct_image_scraped_ad_copy_with_braces_does_not_raise(monkeypatch):
    """ad_text/cta are passed to Claude as a SEPARATE content block, never through
    .format() - confirmed end to end: literal { and } in scraped ad copy must not raise
    KeyError, the exact risk .format()-interpolating new fields would have introduced."""
    class FakeMessage:
        content = [type("obj", (), {"text": _fake_claude_json()})()]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeMessage()

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(deconstruct.anthropic, "Anthropic", FakeClient)

    bp = deconstruct.deconstruct_image(
        image_bytes=b"\x89PNG\r\n\x1a\nfakepngbytes",
        ad_id="AD1", source_page="PageX", captured_at="2026-01-01",
        ad_text="Save 20% {today only} - don't miss it! {limited stock}",
        cta="Shop {Now}",
    )
    assert bp["ad_id"] == "AD123"