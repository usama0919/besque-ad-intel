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


# ---- image_subtext: a short on-image line, distinct from the long-form primary_text ----

def test_build_copy_prompt_requires_short_image_subtext_field():
    """Regression guard: primary_text is long-form Facebook post body copy (~80 words) -
    pipeline.py must have a SEPARATE short field to hand to the image side, or it ends up
    passing the whole paragraph as in-scene subtext (the actual 2026-07-31 incident)."""
    prompt = generate_copy.build_copy_prompt({"angle": "x"})
    assert "image_subtext" in prompt
    assert "under about 12 words" in prompt
    assert "NOT the full primary_text" in prompt


# ---- OFFER: offer_text governs discount/price/urgency language, never blueprint.offer ----

def test_build_copy_prompt_offer_text_given_states_exact_wording_only():
    prompt = generate_copy.build_copy_prompt({"angle": "x"}, offer_text="20% off this week only")
    assert "An offer has been supplied for this run: 20% off this week only." in prompt
    assert "No offer has been supplied" not in prompt


def test_build_copy_prompt_offer_text_absent_forbids_discount_price_and_urgency():
    """Regression guard for the 2026-07-31 incident: with offer_text empty, a draft read
    "50% off - ONLY while stock lasts", lifted from the competitor's own clearance sale via
    blueprint.offer. The ban must be explicit and present even though blueprint.offer isn't
    read directly here - CREATIVE BLUEPRINT (which DOES include it) is rendered verbatim
    later in the same prompt."""
    prompt = generate_copy.build_copy_prompt({"angle": "x", "offer": {"type": "clearance", "value": "50% off"}})
    assert "No offer has been supplied for this run" in prompt
    assert "discount, percentage, price, sale, or urgency/scarcity mechanic" in prompt
    assert "while stock lasts" in prompt


def test_generate_copy_live_forwards_offer_text_to_prompt(monkeypatch):
    """offer_text must actually reach the prompt sent to Claude, not just exist as an
    unused generate_copy_live parameter."""
    captured = {}

    class FakeMessage:
        content = [type("obj", (), {"text": _fake_copy_json()})()]

    class FakeMessages:
        def create(self, **kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return FakeMessage()

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(generate_copy.anthropic, "Anthropic", FakeClient)
    generate_copy.generate_copy_live({"angle": "x"}, offer_text="free shipping this week")
    assert "free shipping this week" in captured["prompt"]
