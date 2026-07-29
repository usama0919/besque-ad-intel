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


def test_build_copy_prompt_includes_compliance_rules():
    prompt = generate_copy.build_copy_prompt({"angle": "x"})
    assert "C2. NO FABRICATED TESTIMONIALS" in prompt
    assert "C5. NO IMPLIED MEDICAL/PHARMACEUTICAL CLAIMS" in prompt


def test_build_copy_prompt_default_approved_testimonials_bans_invention():
    prompt = generate_copy.build_copy_prompt({"angle": "x"})
    assert "Do not invent, quote, or imply any customer testimonial" in prompt


def test_build_copy_prompt_includes_supplied_approved_testimonials():
    prompt = generate_copy.build_copy_prompt({"angle": "x"}, approved_testimonials="Jane says it changed her routine")
    assert "Jane says it changed her routine" in prompt


def test_build_copy_prompt_includes_compliance_feedback_on_retry():
    prompt = generate_copy.build_copy_prompt({"angle": "x"}, compliance_feedback=["fabricated testimonial detected"])
    assert "REVISION REQUIRED" in prompt
    assert "fabricated testimonial detected" in prompt


def test_build_copy_prompt_omits_revision_section_when_no_feedback():
    prompt = generate_copy.build_copy_prompt({"angle": "x"})
    assert "REVISION REQUIRED" not in prompt
