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

def test_critic_system_cites_every_individually_numbered_rule():
    """Item 4 (2026-08-12): derived from generate_image_prompt.py's own _RULE_<N>_...
    constants (critic._numbered_rule_ids()), not a hand-maintained tuple - this IS the
    fix for the failure this test guards against. The old version iterated
    CITED_RULE_IDS, a manually maintained allowlist: rule 11 (SKIN TEXTURE REALISM) was
    added as a new module constant in generate_image_prompt.py with no checklist entry,
    and this test kept passing because CITED_RULE_IDS was never told rule 11 existed. A
    new rule N is now automatically required to be cited the moment its _RULE_N_...
    constant is added - nothing else to remember to update."""
    ids = critic._numbered_rule_ids()
    assert ids, "discovered zero numbered rules - the introspection itself is broken"
    for rule_id in ids:
        assert f"rule {rule_id}" in critic.CRITIC_SYSTEM.lower(), (
            f"rule {rule_id} (generate_image_prompt._RULE_{rule_id}_...) has no citation "
            f"in CRITIC_SYSTEM"
        )


def test_numbered_rule_ids_excludes_bundled_and_functional_rules():
    """Rules 1-5 (bundled in one _RULES_1_TO_5 string) and 6-7 (built by functions, not
    constants) must NOT appear - they were never individually-numbered module constants
    to derive an id from, and their own citations are pinned by dedicated tests below,
    not by this derived mechanism."""
    ids = critic._numbered_rule_ids()
    for excluded in (1, 2, 3, 4, 5, 6, 7):
        assert excluded not in ids


def test_numbered_rule_ids_includes_rule_8_and_11():
    """The two rules that were actually missing a checklist entry when this mechanism
    was introduced - rule 8 (LAYOUT DESCRIPTORS) had never been cited at all, and rule
    11 (SKIN TEXTURE REALISM) is the rule that motivated this whole inversion."""
    ids = critic._numbered_rule_ids()
    assert 8 in ids
    assert 11 in ids


def test_critic_system_cites_rule_9_next_to_competitor_marks():
    assert "(rule 9)" in critic.CRITIC_SYSTEM


# ---- 2026-08-12 15:13 sweep: rule 9's checklist entry passed both a competitor
# "by X" tagline surviving beneath the BESQUE logo and a competitor's product jar
# surviving beside the substituted Besque bottle - strengthened to name both
# explicitly and confirmed HIGH-confidence-by-default, since the category had shipped
# in real drafts but was not in HIGH_CONFIDENCE_BY_DEFAULT at all. ----

def test_critic_system_rule_9_names_wordmark_endorsement_and_competitor_product():
    assert "(rule 9)" in critic.CRITIC_SYSTEM
    assert "wordmark" in critic.CRITIC_SYSTEM
    assert "by X" in critic.CRITIC_SYSTEM
    assert "competitor's own product or packaging" in critic.CRITIC_SYSTEM
    assert "SECOND competitor product" in critic.CRITIC_SYSTEM


def test_critic_system_competitor_brand_mark_or_product_default_high_confidence():
    assert "competitor brand mark or product" in critic.HIGH_CONFIDENCE_BY_DEFAULT
    assert "competitor brand mark or product" in critic.CRITIC_SYSTEM.lower().split("treat a hit")[1]


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


# ---- Item 3 (2026-08-12): SUBJECT AGE is its OWN dedicated checklist category, not
# something to notice only incidentally - live evidence a ~30-year-old subject shipped
# completely unflagged on one ad while a separate ad's violation was correctly caught ----

def test_critic_system_cites_rule_10_next_to_subject_age():
    assert "(rule 10)" in critic.CRITIC_SYSTEM
    assert "SUBJECT AGE VIOLATION" in critic.CRITIC_SYSTEM


def test_critic_system_subject_age_default_high_confidence():
    assert "subject age violation" in critic.HIGH_CONFIDENCE_BY_DEFAULT
    assert "subject age" in critic.CRITIC_SYSTEM.lower().split("treat a hit")[1]


def test_critic_system_subject_age_instructs_checking_regardless_of_reference():
    """Must not be excusable by 'the reference itself showed a young model' - rule 10 is
    unconditional, and the checklist must say so too, not just cite the rule number."""
    assert "regardless of what age the reference ad's own model was" in critic.CRITIC_SYSTEM


# ---- Item 2 (2026-08-12): SUBJECT IDENTITY - the critic must actually be shown the
# reference image to compare against, or this category has nothing to judge ----

def test_critic_system_has_subject_identity_category():
    assert "SUBJECT IDENTITY" in critic.CRITIC_SYSTEM
    assert "subject identity" in critic.HIGH_CONFIDENCE_BY_DEFAULT
    assert "subject identity" in critic.CRITIC_SYSTEM.lower().split("treat a hit")[1]


def test_build_user_prompt_no_reference_image_keeps_single_image_wording():
    prompt = critic._build_user_prompt("rules")
    assert "Review the attached image" in prompt
    assert "TWO images are attached" not in prompt


def test_build_user_prompt_with_reference_image_states_which_image_is_which():
    prompt = critic._build_user_prompt("rules", has_reference_image=True)
    assert "TWO images are attached" in prompt
    assert "FIRST is the competitor's ORIGINAL reference ad" in prompt
    assert "SECOND is the GENERATED Besque draft" in prompt
    assert "SUBJECT IDENTITY" in prompt


def test_check_draft_without_reference_attaches_only_the_draft(monkeypatch):
    calls = []

    class _CapturingMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _FakeMessage(json.dumps({"violations": []}))

    class _CapturingClient:
        def __init__(self, *a, **k):
            self.messages = _CapturingMessages()

    monkeypatch.setattr(critic.anthropic, "Anthropic", _CapturingClient)
    critic.check_draft(b"\x89PNG\r\n\x1a\ndraft-bytes", "rules")
    content = calls[0]["messages"][0]["content"]
    images = [c for c in content if c["type"] == "image"]
    assert len(images) == 1


def test_check_draft_with_reference_attaches_reference_before_draft(monkeypatch):
    calls = []

    class _CapturingMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _FakeMessage(json.dumps({"violations": []}))

    class _CapturingClient:
        def __init__(self, *a, **k):
            self.messages = _CapturingMessages()

    monkeypatch.setattr(critic.anthropic, "Anthropic", _CapturingClient)
    critic.check_draft(
        b"\x89PNG\r\n\x1a\ndraft-bytes", "rules",
        reference_image_bytes=b"\x89PNG\r\n\x1a\nreference-bytes",
    )
    content = calls[0]["messages"][0]["content"]
    images = [c for c in content if c["type"] == "image"]
    assert len(images) == 2
    import base64
    assert base64.standard_b64decode(images[0]["source"]["data"]) == b"\x89PNG\r\n\x1a\nreference-bytes"
    assert base64.standard_b64decode(images[1]["source"]["data"]) == b"\x89PNG\r\n\x1a\ndraft-bytes"
    text_content = next(c for c in content if c["type"] == "text")
    assert "TWO images are attached" in text_content["text"]


def test_check_draft_reference_bytes_default_none_unaffected(monkeypatch):
    """Every pre-existing caller (no reference_image_bytes kwarg at all) must be
    completely unaffected - single image, old closing wording."""
    monkeypatch.setattr(critic.anthropic, "Anthropic",
                        lambda *a, **k: _FakeClient(json.dumps({"violations": []})))
    result = critic.check_draft(b"fake-bytes", "rules")
    assert result == []


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


def test_check_draft_returns_marked_finding_on_persistent_api_error(monkeypatch):
    """2026-08-12 (item 5): check_draft no longer returns None on any failure - a
    persistent failure through the retry now returns CRITIC_CHECK_FAILED_FINDING, a
    synthetic HIGH-confidence finding, so the draft gets marked rather than silently
    passed. Client construction itself failing (e.g. missing API key) must be caught by
    the SAME retry/mark path, never propagate uncaught - a real regression this test
    caught during development (client construction had briefly moved outside the loop's
    try/except)."""
    class _BoomClient:
        def __init__(self, *a, **k):
            raise RuntimeError("API unavailable")

    monkeypatch.setattr(critic.anthropic, "Anthropic", _BoomClient)
    result = critic.check_draft(b"fake-bytes", "rules")
    assert result == critic.CRITIC_CHECK_FAILED_FINDING
    assert critic.has_high_confidence(result) is True


def test_check_draft_returns_marked_finding_on_persistent_unparseable_response(monkeypatch):
    """2026-08-12 (item 5): a real incident - "output critic failed (JSONDecodeError:
    Invalid \\escape), draft left unflagged" - meant this exact scenario silently passed
    a draft that was never actually reviewed. Now retries once, and if the retry ALSO
    fails to parse, returns CRITIC_CHECK_FAILED_FINDING instead of None."""
    monkeypatch.setattr(critic.anthropic, "Anthropic",
                        lambda *a, **k: _FakeClient("not valid json at all"))
    result = critic.check_draft(b"fake-bytes", "rules")
    assert result == critic.CRITIC_CHECK_FAILED_FINDING
    assert critic.has_high_confidence(result) is True


def test_check_draft_retries_once_with_json_escape_nudge_then_succeeds(monkeypatch):
    """The retry must actually be usable, not just present - a client that fails to parse
    on attempt 1 but returns valid JSON on attempt 2 (the JSON_ESCAPE_SYSTEM nudge having
    worked) must return the real findings, not the failure marker."""
    calls = {"n": 0}
    good_response = json.dumps({"violations": [
        {"category": "testimonial", "description": "fabricated quote", "confidence": "high"},
    ]})

    class _FlakyClient:
        def __init__(self, *a, **k):
            self.messages = self

        def create(self, **kwargs):
            calls["n"] += 1
            text = "not valid json at all" if calls["n"] == 1 else good_response
            return type("obj", (), {"content": [type("obj", (), {"text": text})()]})()

    monkeypatch.setattr(critic.anthropic, "Anthropic", _FlakyClient)
    result = critic.check_draft(b"fake-bytes", "rules")
    assert calls["n"] == 2
    assert result == [{"category": "testimonial", "description": "fabricated quote", "confidence": "high"}]


def test_check_draft_never_calls_api_a_third_time(monkeypatch):
    """One retry, never a loop - a client that always fails must be called exactly twice."""
    calls = {"n": 0}

    class _AlwaysBoomClient:
        def __init__(self, *a, **k):
            calls["n"] += 1
            raise RuntimeError("still broken")

    monkeypatch.setattr(critic.anthropic, "Anthropic", _AlwaysBoomClient)
    critic.check_draft(b"fake-bytes", "rules")
    assert calls["n"] == 2


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
