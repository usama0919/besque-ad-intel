import json
import pytest
from src import generate_copy


def _fake_copy_json():
    return json.dumps({
        "headline": "Rediscover Firmer, Radiant Skin",
        "primary_text": "Cold-pressed botanical oils that nourish and firm.",
        "cta": "Shop the Ritual",
    })


def test_parse_plain_copy():
    copy = generate_copy.parse_copy(_fake_copy_json())
    assert copy["headline"].startswith("Rediscover")


def test_parse_strips_fences():
    raw = "```json\n" + _fake_copy_json() + "\n```"
    copy = generate_copy.parse_copy(raw)
    assert copy["cta"] == "Shop the Ritual"


def test_copy_from_response_valid():
    copy = generate_copy.copy_from_response(_fake_copy_json())
    assert set(copy.keys()) >= {"headline", "primary_text", "cta"}


def test_copy_missing_field_raises():
    bad = json.dumps({"headline": "Only a headline"})
    with pytest.raises(ValueError):
        generate_copy.copy_from_response(bad)


def test_build_copy_prompt_includes_blueprint():
    prompt = generate_copy.build_copy_prompt({"angle": "firmer skin at any age"})
    assert "firmer skin at any age" in prompt
