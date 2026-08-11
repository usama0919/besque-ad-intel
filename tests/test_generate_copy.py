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


# ---- TIER 3 (result_phrases/main_benefit) removed entirely 2026-08-11: "forbidden to
# emit" was still a text instruction sitting next to the actual phrases, and the model
# lifted bare words (e.g. "firmness") out of them into real headlines despite the ban.
# TIER 2 (core_angle/main_pain_point) is untouched - different risk category, no
# efficacy/outcome claims to leak. ----

def _angle_language_with_tier3():
    return {
        "core_angle": "Loose skin is the concern that shows up every morning.",
        "main_pain_point": "She stopped wearing sleeveless tops.",
        "common_phrases": ["used to be firm", "skin that hangs"],
        "result_phrases": ["firmer to the touch", "neck firmed up"],
        "main_benefit": "Skin that visibly firms and tightens within weeks.",
    }


def test_build_copy_prompt_omits_tier_3_result_phrases_and_main_benefit():
    prompt = generate_copy.build_copy_prompt({"angle": "x"}, angle_language=_angle_language_with_tier3())
    assert "TIER 3" not in prompt
    assert "firmer to the touch" not in prompt
    assert "neck firmed up" not in prompt
    assert "Skin that visibly firms and tightens within weeks" not in prompt
    assert "REFERENCE ONLY" not in prompt
    assert "may inform which problem phrase" not in prompt


def test_build_copy_prompt_keeps_tier_1_and_tier_2():
    prompt = generate_copy.build_copy_prompt({"angle": "x"}, angle_language=_angle_language_with_tier3())
    assert "TIER 1" in prompt
    assert "used to be firm" in prompt  # common_phrases, still written from directly
    assert "TIER 2" in prompt
    assert "Loose skin is the concern that shows up every morning." in prompt
    assert "She stopped wearing sleeveless tops." in prompt


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


# ---- used_headlines (2026-08-11, same-run copy convergence fix): additive clause, same
# pattern as panel_copy/compliance_feedback - None/[] must leave the prompt byte-identical
# to before this existed. ----

def test_used_copy_clause_empty_for_none_or_empty_list():
    assert generate_copy._used_copy_clause(None) == ""
    assert generate_copy._used_copy_clause([]) == ""


def test_build_copy_prompt_used_headlines_none_and_omitted_are_byte_identical():
    omitted = generate_copy.build_copy_prompt({"angle": "x"})
    explicit_none = generate_copy.build_copy_prompt({"angle": "x"}, used_headlines=None)
    explicit_empty = generate_copy.build_copy_prompt({"angle": "x"}, used_headlines=[])
    assert omitted == explicit_none == explicit_empty
    assert "ALREADY USED" not in omitted


def test_build_copy_prompt_includes_used_headlines_clause():
    prompt = generate_copy.build_copy_prompt(
        {"angle": "x"},
        used_headlines=[{"headline": "Go Jumbo & Save", "image_subtext": "7 oils. One blend."}],
    )
    assert "ALREADY USED EARLIER IN THIS RUN" in prompt
    assert "Go Jumbo & Save" in prompt
    assert "7 oils. One blend." in prompt


def test_build_copy_prompt_used_headlines_lists_every_entry():
    prompt = generate_copy.build_copy_prompt(
        {"angle": "x"},
        used_headlines=[
            {"headline": "H1", "image_subtext": "S1"},
            {"headline": "H2", "image_subtext": "S2"},
        ],
    )
    assert "H1" in prompt and "S1" in prompt
    assert "H2" in prompt and "S2" in prompt


def test_generate_copy_live_forwards_used_headlines_to_prompt(monkeypatch):
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
    generate_copy.generate_copy_live(
        {"angle": "x"}, used_headlines=[{"headline": "Prior Headline", "image_subtext": "Prior sub"}],
    )
    assert "Prior Headline" in captured["prompt"]
    assert "Prior sub" in captured["prompt"]


# ---- comparison_panels / panel_copy (2026-08-06, Grüns GLP-1 leak: a two-panel before/
# after joke rendered the SAME headline text in both panels) ----

def _comparison_blueprint():
    return {
        "angle": "loose skin",
        "structural_zones": [
            {"zone_type": "sub_line", "position": "upper-left-mid", "container": "none",
             "detail": "negative outcome - hair loss"},
            {"zone_type": "sub_line", "position": "upper-right-mid", "container": "none",
             "detail": "positive outcome - keeping hair"},
        ],
    }


def test_comparison_panels_absent_for_ordinary_blueprint():
    assert generate_copy.comparison_panels({"angle": "x"}) == []


def test_comparison_panels_absent_for_single_sub_line():
    """One sub_line is an ordinary accent line, not a comparison - the whole point is
    TWO OR MORE distinct panels."""
    bp = {"structural_zones": [
        {"zone_type": "sub_line", "position": "top-center", "detail": "a tagline"},
    ]}
    assert generate_copy.comparison_panels(bp) == []


def test_comparison_panels_detected_for_two_sub_lines():
    panels = generate_copy.comparison_panels(_comparison_blueprint())
    assert len(panels) == 2
    assert {p["position"] for p in panels} == {"upper-left-mid", "upper-right-mid"}


def test_comparison_panels_counts_body_copy_too():
    bp = {"structural_zones": [
        {"zone_type": "sub_line", "position": "left", "detail": "before"},
        {"zone_type": "body_copy", "position": "right", "detail": "after"},
    ]}
    assert len(generate_copy.comparison_panels(bp)) == 2


def test_comparison_panels_ignores_cta_and_other_zone_types():
    """A comparison ad has one CTA, not one per panel - cta must never count toward the
    panel total."""
    bp = {"structural_zones": [
        {"zone_type": "sub_line", "position": "left", "detail": "before"},
        {"zone_type": "cta", "position": "bottom", "detail": "shop now"},
    ]}
    assert generate_copy.comparison_panels(bp) == []


def test_build_copy_prompt_unaffected_when_no_comparison_panels():
    """Byte-for-byte the same prompt as before this feature existed, for every ordinary
    single-panel blueprint - the overwhelming majority."""
    with_zones_but_no_comparison = generate_copy.build_copy_prompt(
        {"angle": "x", "structural_zones": [{"zone_type": "badge", "position": "top"}]}
    )
    without_zones = generate_copy.build_copy_prompt({"angle": "x"})
    assert "MULTI-PANEL COMPARISON" not in with_zones_but_no_comparison
    assert "MULTI-PANEL COMPARISON" not in without_zones


def test_build_copy_prompt_states_multi_panel_instruction_when_detected():
    prompt = generate_copy.build_copy_prompt(_comparison_blueprint())
    assert "MULTI-PANEL COMPARISON" in prompt
    assert "panel_copy" in prompt
    assert "upper-left-mid" in prompt
    assert "upper-right-mid" in prompt
    assert "negative outcome - hair loss" in prompt
    assert "positive outcome - keeping hair" in prompt


def test_build_copy_prompt_panel_instruction_states_exact_count():
    prompt = generate_copy.build_copy_prompt(_comparison_blueprint())
    assert "2 distinct text panels" in prompt
    assert "EXACTLY 2 objects" in prompt
