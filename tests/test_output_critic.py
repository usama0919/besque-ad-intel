"""Tests for the output critic (Prompt 4, Item 1) - a SAFETY control, not a quality
feature. Every guardrail on the image path up to this point is prompt-only; this module
is the only thing that inspects what Gemini actually produced. check_draft must never
raise (a failure must never lose a draft or fail a run), and must drop low-confidence
findings (a critic that flags everything becomes noise)."""
import json
from src import output_critic as critic


def test_build_user_prompt_includes_brand_rules_text():
    prompt = critic._build_user_prompt("STRICT RULES - NEVER VIOLATE: some rule text")
    assert "STRICT RULES - NEVER VIOLATE: some rule text" in prompt


def test_build_user_prompt_states_authorised_headline():
    prompt = critic._build_user_prompt("rules", headline="Firmer Skin By Friday", subtext="7 oils")
    assert "Firmer Skin By Friday" in prompt
    assert "7 oils" in prompt


def test_build_user_prompt_states_no_text_authorised_when_headline_absent():
    prompt = critic._build_user_prompt("rules", headline=None)
    assert "NONE - no text was authorised for this image" in prompt


def test_build_user_prompt_states_authorised_offer():
    prompt = critic._build_user_prompt("rules", offer_text="20% off launch week")
    assert "20% off launch week" in prompt


def test_build_user_prompt_states_no_offer_authorised_when_absent():
    prompt = critic._build_user_prompt("rules", offer_text=None)
    assert "NONE - no offer was authorised for this image" in prompt


def test_build_user_prompt_states_product_presence():
    prompt = critic._build_user_prompt("rules", include_product=True)
    assert "exactly one Besque product" in prompt
    prompt2 = critic._build_user_prompt("rules", include_product=False)
    assert "NONE - this was a deliberately productless image" in prompt2


def test_build_user_prompt_states_documented_visual_description_for_label_judgment():
    # PART 1G (2026-08-06): the critic must judge label text against the product's OWN
    # documented design, not rule 1's bare wording alone - the exact gap that produced a
    # false positive on the L'Occitane run's real, correct label ("LUXURY BODY OIL",
    # "NOURISH, HYDRATE & SMOOTH SKIN", ...), which the critic had never been told about.
    prompt = critic._build_user_prompt(
        "rules", visual_description="Clear bottle, 'LUXURY BODY OIL' beneath the name.",
        ingredients="Almond, Rosehip, Vitamin E",
    )
    assert "Clear bottle, 'LUXURY BODY OIL' beneath the name." in prompt
    assert "Almond, Rosehip, Vitamin E" in prompt
    assert "judge label text" in prompt.lower() or "judge the" in prompt.lower()

    # Omitting both (every pre-existing caller) must not add empty sections.
    prompt_without = critic._build_user_prompt("rules")
    assert "documented label" not in prompt_without.lower()
    assert "actual ingredients" not in prompt_without.lower()


# ---- The prompt-fixed instruction: proven-shipped categories default to high confidence ----

def test_critic_system_states_high_confidence_defaults_for_shipped_categories():
    for cat in ("unauthorised offer", "scarcity claim", "promo code", "efficacy claim",
                "testimonial", "product category mismatch"):
        assert cat in critic.CRITIC_SYSTEM


def test_critic_system_lists_every_required_check():
    checks = ["competitor logo", "competitor brand or product name", "unauthorised offer",
              "quantified efficacy claim", "testimonial", "more than one product",
              "product-derived substance", "empty graphic container", "garbled or illegible",
              "text rendered when none was authorised"]
    for c in checks:
        assert c in critic.CRITIC_SYSTEM


def test_critic_system_checks_product_category_regardless_of_reference():
    # 2026-08-05: a real draft rendered a hair & body mist (the competitor's own category)
    # instead of the authorised body oil - the reference supplies composition/styling only,
    # never product identity, and a mismatched reference category is never a reason to
    # relax this or read the drift as intentional.
    assert "OTHER than a body oil" in critic.CRITIC_SYSTEM
    assert "regardless of what category the competitor's OWN reference ad sells" in critic.CRITIC_SYSTEM


# ---- Rule-ID coverage (drift guard): the checklist is hand-written, not generated from
# brand_rules()/compliance_rules.py's actual text - these citations are the only thing
# tying it back to the real rule numbering, so they must survive future edits ----

def test_critic_system_cites_every_rule_id_it_claims_to_cover():
    for rule_id in critic.CITED_RULE_IDS:
        assert rule_id in critic.CRITIC_SYSTEM


def test_critic_system_cites_rule_9_next_to_competitor_marks():
    assert "(rule 9)" in critic.CRITIC_SYSTEM


def test_critic_system_cites_rule_7_next_to_product_count():
    assert "(rule 7)" in critic.CRITIC_SYSTEM


def test_critic_system_cites_rule_5_next_to_product_category():
    assert "(rule 5)" in critic.CRITIC_SYSTEM


def test_critic_system_cites_rule_6_next_to_text_state():
    assert "(rule 6)" in critic.CRITIC_SYSTEM


def test_critic_system_cites_c2_next_to_testimonial():
    assert "(C2)" in critic.CRITIC_SYSTEM


def test_critic_system_cites_c3_next_to_efficacy_claim():
    assert "C3" in critic.CRITIC_SYSTEM


# ---- check_draft: never raises, filters confidence, parses JSON ----

class _FakeMessage:
    def __init__(self, text):
        self.content = [type("obj", (), {"text": text})()]


class _FakeMessages:
    def __init__(self, response_text):
        self._text = response_text

    def create(self, **kwargs):
        return _FakeMessage(self._text)


class _FakeClient:
    def __init__(self, response_text):
        self.messages = _FakeMessages(response_text)


def test_check_draft_returns_high_and_medium_only(monkeypatch):
    response = json.dumps({"violations": [
        {"category": "testimonial", "description": "fabricated quote", "confidence": "high"},
        {"category": "logo", "description": "faint mark, might be shadow", "confidence": "low"},
        {"category": "offer", "description": "possible price text", "confidence": "medium"},
    ]})
    monkeypatch.setattr(critic.anthropic, "Anthropic", lambda *a, **k: _FakeClient(response))

    findings = critic.check_draft(b"\x89PNG\r\n\x1a\nfake", "rules")
    assert len(findings) == 2
    assert {f["confidence"] for f in findings} == {"high", "medium"}
    assert not any(f["confidence"] == "low" for f in findings)


def test_check_draft_returns_empty_list_when_no_violations(monkeypatch):
    monkeypatch.setattr(critic.anthropic, "Anthropic",
                        lambda *a, **k: _FakeClient(json.dumps({"violations": []})))
    assert critic.check_draft(b"fake-bytes", "rules") == []


def test_check_draft_returns_none_on_api_error(monkeypatch):
    class _BoomClient:
        def __init__(self, *a, **k):
            raise RuntimeError("API unavailable")

    monkeypatch.setattr(critic.anthropic, "Anthropic", _BoomClient)
    assert critic.check_draft(b"fake-bytes", "rules") is None


def test_check_draft_returns_none_on_unparseable_response(monkeypatch):
    monkeypatch.setattr(critic.anthropic, "Anthropic",
                        lambda *a, **k: _FakeClient("not valid json at all"))
    assert critic.check_draft(b"fake-bytes", "rules") is None


def test_check_draft_never_raises_even_on_malformed_violation_entries(monkeypatch):
    """A violation entry missing "confidence" must not crash the filter - it's simply
    dropped (not high/medium), never a hard failure."""
    response = json.dumps({"violations": [{"category": "x", "description": "y"}]})
    monkeypatch.setattr(critic.anthropic, "Anthropic", lambda *a, **k: _FakeClient(response))
    findings = critic.check_draft(b"fake-bytes", "rules")
    assert findings == []


# ---- has_high_confidence: the single gate condition the retry loop and the
# "failed review" card state both key off (2026-08-05) ----

def test_has_high_confidence_true_when_any_finding_is_high():
    findings = [{"category": "x", "description": "y", "confidence": "medium"},
                {"category": "a", "description": "b", "confidence": "high"}]
    assert critic.has_high_confidence(findings) is True


def test_has_high_confidence_false_when_only_medium_or_low():
    findings = [{"category": "x", "description": "y", "confidence": "medium"}]
    assert critic.has_high_confidence(findings) is False


def test_has_high_confidence_false_for_empty_or_none():
    assert critic.has_high_confidence([]) is False
    assert critic.has_high_confidence(None) is False


def test_has_high_confidence_case_insensitive_and_never_raises_on_malformed_entries():
    assert critic.has_high_confidence([{"confidence": "HIGH"}]) is True
    assert critic.has_high_confidence([{"category": "x"}]) is False


# ---- drop_findings_contradicted_by_authorised: TESTIMONIAL ONLY, fail-open (2026-08-10) ----

def test_drop_contradicted_keeps_findings_that_merely_quote_the_authorised_headline():
    # Live failure: three genuine, distinct violations on one ad, all wrongly dropped by
    # the prior version because each description quotes the authorised headline
    # ("years of sun damage — and the skin to prove it.") while reporting something else
    # entirely - an unauthorised leaked label, a missing headline, a missing subtext.
    # None of these is a testimonial re-flag; all three must survive.
    headline = "years of sun damage — and the skin to prove it."
    findings = [
        {"category": "unauthorised text", "confidence": "high", "description": (
            "The labels 'Without' and 'With Besque' appear as in-scene typography. "
            "These words are not part of the authorised text budget - the authorised "
            f"headline was {headline!r}, and no other text is permitted."
        )},
        {"category": "missing authorised text", "confidence": "high", "description": (
            f"The authorised headline {headline!r} is not rendered anywhere in the image."
        )},
        {"category": "missing authorised text", "confidence": "high", "description": (
            "The authorised supporting text is not rendered anywhere in the image."
        )},
    ]
    kept = critic.drop_findings_contradicted_by_authorised(
        findings, testimonial={"quote": "Works like magic!", "attribution": "SANDY O."},
    )
    assert kept == findings


def test_drop_contradicted_drops_testimonial_reflag_when_both_conditions_hold():
    # The one CONFIRMED motivating case (ad 1653458269057951): a real, authorised
    # testimonial re-flagged as fabricated under a testimonial-shaped category.
    findings = [
        {"category": "testimonial", "confidence": "high",
         "description": "A customer quote \"Works like magic!\" - SANDY O. appears as a fabricated review."},
    ]
    kept = critic.drop_findings_contradicted_by_authorised(
        findings, testimonial={"quote": "Works like magic!", "attribution": "SANDY O."},
    )
    assert kept == []


def test_drop_contradicted_keeps_finding_when_quote_matches_but_category_does_not():
    # FAIL OPEN: quote text present is not enough on its own - an unrelated category
    # (e.g. a leaked competitor prop) that happens to quote the testimonial text for
    # context is not a testimonial re-flag and must be kept.
    findings = [
        {"category": "carried-over prop", "confidence": "medium",
         "description": "A citrus prop from the reference sits beside text reading 'Works like magic!'."},
    ]
    kept = critic.drop_findings_contradicted_by_authorised(
        findings, testimonial={"quote": "Works like magic!", "attribution": "SANDY O."},
    )
    assert kept == findings


def test_drop_contradicted_keeps_everything_when_no_testimonial_authorised():
    findings = [{"category": "testimonial", "confidence": "high", "description": "some quote appears"}]
    assert critic.drop_findings_contradicted_by_authorised(findings, testimonial=None) == findings
