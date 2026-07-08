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