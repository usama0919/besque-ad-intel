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


# ---- Em dash/en dash/double-hyphen ban (2026-08-12) - mechanical strip, not a prompt
# request. Real incident: "The arms I'd been hiding — finally uncovered." ----

def test_copy_from_response_strips_em_dash():
    raw = json.dumps({
        "headline": "The arms I’d been hiding — finally uncovered.",
        "primary_text": "x", "cta": "y",
    })
    copy = generate_copy.copy_from_response(raw)
    assert "—" not in copy["headline"]
    assert "hiding, finally uncovered" in copy["headline"]


def test_copy_from_response_strips_en_dash():
    raw = json.dumps({"headline": "Firmer skin – naturally.", "primary_text": "x", "cta": "y"})
    copy = generate_copy.copy_from_response(raw)
    assert "–" not in copy["headline"]
    assert "Firmer skin, naturally." == copy["headline"]


def test_copy_from_response_strips_double_hyphen():
    raw = json.dumps({"headline": "Firmer skin -- naturally.", "primary_text": "x", "cta": "y"})
    copy = generate_copy.copy_from_response(raw)
    assert "--" not in copy["headline"]
    assert "Firmer skin, naturally." == copy["headline"]


def test_strip_banned_dashes_covers_every_string_field():
    copy = {"headline": "a — b", "primary_text": "c – d", "cta": "e -- f", "image_subtext": "g"}
    stripped = generate_copy.strip_banned_dashes(copy)
    assert stripped == {"headline": "a, b", "primary_text": "c, d", "cta": "e, f", "image_subtext": "g"}


def test_strip_banned_dashes_covers_panel_copy_text():
    copy = {"headline": "x", "panel_copy": [{"position": "top", "text": "before — after"}]}
    stripped = generate_copy.strip_banned_dashes(copy)
    assert stripped["panel_copy"][0]["text"] == "before, after"
    assert stripped["panel_copy"][0]["position"] == "top"


# ---- PRODUCT_FACT_KEYS is name-only (2026-08-11): description and ingredients both
# removed, not just reworded - either one let a copywriter paraphrase a marketing
# sentence into a headline regardless of PRODUCT's own "constraint, not a copy source"
# framing. hero_claim removed too even though it's currently blanked in the DB, so it
# can't silently start reaching this prompt again once that field is repopulated. ----

def test_product_fact_keys_is_name_only():
    assert generate_copy.PRODUCT_FACT_KEYS == ("name",)


def test_product_facts_omits_description_ingredients_and_hero_claim():
    product = {
        "name": "Besque Magic Body Oil",
        "description": "A luxury fragrant blend of 7 cold-pressed oils.",
        "ingredients": "Almond (hydrates the skin); Primrose (increases elasticity, bounce-back)",
        "hero_claim": "Visibly firms and tightens the skin with consistent use",
    }
    facts = generate_copy._product_facts(product)
    assert "Besque Magic Body Oil" in facts
    assert "cold-pressed oils" not in facts
    assert "Almond" not in facts
    assert "Primrose" not in facts
    assert "firms and tightens" not in facts


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


# ---- _has_text_purpose_object (2026-08-17): the objects-array replacement for the
# deleted text_zone_targets/_cta_zone - text_purpose is now a per-object field
# (schema/blueprint.schema.json), not a separate structural_zones/text_purpose array,
# so there is one exact-match helper instead of two structural-zone detectors. The
# per-zone-type COPY generation these used to drive (a DIFFERENT panel_copy line per
# sub_line/body_copy/product_callout zone, a dedicated CTA ZONE clause quoting the
# reference's own detail) has NO restored equivalent - see generate_copy.py's own
# handover comment for why this was scoped down rather than rebuilt. What survives is
# the narrower, load-bearing half: knowing whether a real subtext/cta-shaped text block
# exists at all, so validate_copy's mechanical backstop and image_subtext's own field
# permission still track reality. ----

def _text_obj(purpose, **overrides):
    base = {"kind": "text", "text_purpose": purpose, "object_id": "obj_x",
            "description": "reference text", "disposition": "substitute"}
    base.update(overrides)
    return base


def test_has_text_purpose_object_false_for_ordinary_blueprint():
    assert generate_copy._has_text_purpose_object({"angle": "x"}, "subtext") is False
    assert generate_copy._has_text_purpose_object({"objects": []}, "cta") is False


def test_has_text_purpose_object_true_when_present():
    bp = {"objects": [_text_obj("subtext")]}
    assert generate_copy._has_text_purpose_object(bp, "subtext") is True
    assert generate_copy._has_text_purpose_object(bp, "cta") is False


def test_has_text_purpose_object_ignores_non_text_kind():
    # A prop/logo/graphic object could in principle carry a stray text_purpose key by
    # caller error - only kind=="text" counts, matching deconstruct.py's own schema
    # (text_purpose is only ever populated for kind=="text").
    bp = {"objects": [{"kind": "prop", "text_purpose": "cta"}]}
    assert generate_copy._has_text_purpose_object(bp, "cta") is False


def test_has_text_purpose_object_exact_match_only():
    # "other" must never satisfy a "headline"/"subtext"/"cta" check - the exact bug
    # _has_headline_shaped_text_purpose (edit_capability.py) used to have with the old
    # top-level text_purpose array's looser purpose-set membership check.
    bp = {"objects": [_text_obj("other")]}
    assert generate_copy._has_text_purpose_object(bp, "headline") is False


# ---- image_subtext's field description is conditional, never contradicted later
# (2026-08-11) - the empty-string permission is present ONLY when there's no
# subtext-purposed text object, in the field description itself, not revoked by a later
# clause (that would be the exact "prompt demands and forbids the same thing" shape
# behind artifact 1136 - see CLAUDE.md). ----

def test_build_copy_prompt_image_subtext_keeps_empty_permission_with_no_subtext_object():
    prompt = generate_copy.build_copy_prompt({"angle": "x"})
    assert 'Empty string "" if no short line is appropriate' in prompt
    assert "Must NOT be empty string this time" not in prompt


def test_build_copy_prompt_image_subtext_drops_empty_permission_with_subtext_object_present():
    bp = {"objects": [_text_obj("subtext")]}
    prompt = generate_copy.build_copy_prompt(bp)
    assert 'Empty string "" if no short line is appropriate' not in prompt
    assert "Must NOT be empty string this time" in prompt
    assert "REMOVED from the generated image" in prompt


def test_build_copy_prompt_unaffected_by_non_subtext_objects():
    """Byte-for-byte the same prompt as before this feature existed, for every ordinary
    blueprint with no subtext-purposed text object at all - the overwhelming majority."""
    with_unrelated_object = generate_copy.build_copy_prompt(
        {"angle": "x", "objects": [_text_obj("headline")]}
    )
    without_objects = generate_copy.build_copy_prompt({"angle": "x"})
    assert with_unrelated_object.count(generate_copy.IMAGE_SUBTEXT_FIELD_DEFAULT) == 1
    assert without_objects.count(generate_copy.IMAGE_SUBTEXT_FIELD_DEFAULT) == 1


# ---- Item 2 (2026-08-13, sharpened): personal-name-shaped attribution ("Sean R.", an
# "attributed to X" construction, or an em-dash signature) must never reach a copy
# prompt from ANY reference-derived field. ----

def test_redact_personal_attribution_strips_initial_surname():
    assert "Sean R." not in generate_copy._redact_personal_attribution(
        "This is my go-to for vacation. Sean R."
    )


def test_redact_personal_attribution_strips_attributed_to_construction():
    redacted = generate_copy._redact_personal_attribution("attributed to Teresa C.")
    assert "Teresa" not in redacted


def test_redact_personal_attribution_strips_em_dash_signature():
    redacted = generate_copy._redact_personal_attribution('"My new staple." — Sandy O.')
    assert "Sandy O" not in redacted


def test_redact_personal_attribution_strips_json_attribution_key():
    redacted = generate_copy._redact_personal_attribution('{"attribution": "Sean R."}')
    assert "Sean R" not in redacted
    assert '"attribution": ""' in redacted


def test_redact_personal_attribution_leaves_ordinary_text_untouched():
    text = "Tired of crepey skin? Try Besque today."
    assert generate_copy._redact_personal_attribution(text) == text


# ---- C9 extended (2026-08-13 evening): a social media @handle is the same
# unconsented-endorsement exposure as a personal name - redaction must strip it from
# reference-derived text before it ever reaches a copy prompt, same as a name. ----

def test_redact_personal_attribution_strips_at_handle():
    redacted = generate_copy._redact_personal_attribution("as seen on @fitness_ty's page")
    assert "@fitness_ty" not in redacted


def test_build_copy_prompt_redacts_a_reference_objects_description_from_raw_blueprint():
    # A different name from the "Sean R." example already baked into the prohibition
    # wording itself (see PERSONAL_NAME_ATTRIBUTION_PATTERN's own docstring) - using the
    # same name here would trivially match the instructional prose, not prove redaction.
    # The raw blueprint dump (build_copy_prompt's own blueprint=... kwarg) is where an
    # object's own `description` (a competitor's testimonial text, verbatim) would
    # otherwise reach Claude unredacted.
    bp = {"angle": "x", "objects": [
        _text_obj("testimonial", description="This is my go-to for vacation. Wendy P."),
    ]}
    prompt = generate_copy.build_copy_prompt(bp)
    assert "Wendy P" not in prompt


# ---- validate_copy mechanical backstop (2026-08-11) - a prompt instruction alone is the
# pattern that has repeatedly failed on this codebase; an empty cta/image_subtext against
# a real zone must fail validation, not pass silently. ----

def test_validate_copy_allows_empty_cta_when_no_cta_zone():
    generate_copy.validate_copy({"headline": "H", "primary_text": "P", "cta": ""}, require_cta=False)


def test_validate_copy_rejects_empty_cta_when_cta_zone_present():
    with pytest.raises(ValueError):
        generate_copy.validate_copy({"headline": "H", "primary_text": "P", "cta": ""}, require_cta=True)


def test_validate_copy_rejects_whitespace_only_cta_when_required():
    with pytest.raises(ValueError):
        generate_copy.validate_copy({"headline": "H", "primary_text": "P", "cta": "   "}, require_cta=True)


def test_validate_copy_allows_empty_image_subtext_when_no_text_zone():
    generate_copy.validate_copy(
        {"headline": "H", "primary_text": "P", "cta": "C", "image_subtext": ""},
        require_image_subtext=False,
    )


def test_validate_copy_rejects_empty_image_subtext_when_text_zone_present():
    with pytest.raises(ValueError):
        generate_copy.validate_copy(
            {"headline": "H", "primary_text": "P", "cta": "C", "image_subtext": ""},
            require_image_subtext=True,
        )


def test_copy_from_response_rejects_empty_cta_when_required():
    raw = json.dumps({"headline": "H", "primary_text": "P", "cta": ""})
    with pytest.raises(ValueError):
        generate_copy.copy_from_response(raw, require_cta=True)


def test_copy_from_response_accepts_empty_cta_when_not_required():
    raw = json.dumps({"headline": "H", "primary_text": "P", "cta": ""})
    copy = generate_copy.copy_from_response(raw, require_cta=False)
    assert copy["cta"] == ""


# ---- SEASON CONTRADICTION mechanical check (2026-08-12 15:13 sweep) - a real draft
# rendered "Show it off this spring" as the headline with "Give your skin some love
# this winter" as body copy beneath it, both inherited verbatim from a reference ad
# whose OWN copy mixed seasons. Mechanical, not a prompt request - see SEASON_PATTERNS'
# own comment. ----

def test_validate_copy_rejects_two_seasons_across_headline_and_primary_text():
    with pytest.raises(ValueError, match="season"):
        generate_copy.validate_copy({
            "headline": "Show it off this spring", "primary_text": "Give your skin some love this winter.",
            "cta": "Shop Now",
        })


def test_validate_copy_rejects_two_seasons_in_image_subtext_too():
    with pytest.raises(ValueError, match="season"):
        generate_copy.validate_copy({
            "headline": "Summer glow starts here", "primary_text": "P", "cta": "C",
            "image_subtext": "Cozy up this winter",
        })


def test_validate_copy_allows_a_single_season():
    generate_copy.validate_copy({
        "headline": "Show it off this spring", "primary_text": "Fresh, lightweight, radiant.",
        "cta": "Shop Now",
    })


def test_validate_copy_allows_no_season_mentioned():
    generate_copy.validate_copy({"headline": "Show it off", "primary_text": "P", "cta": "C"})


def test_validate_copy_fall_matches_autumn_not_a_second_distinct_season():
    """"fall" and "autumn" are the SAME season - naming both must not itself trigger the
    contradiction check (only a genuinely different season name should)."""
    generate_copy.validate_copy({
        "headline": "Fall in love with your skin this autumn", "primary_text": "P", "cta": "C",
    })


def test_seasons_mentioned_is_case_insensitive():
    assert generate_copy._seasons_mentioned("SPRING into your best skin yet") == {"spring"}


def test_copy_from_response_rejects_season_contradiction():
    raw = json.dumps({
        "headline": "Show it off this spring", "primary_text": "Give your skin some love this winter.",
        "cta": "Shop Now",
    })
    with pytest.raises(ValueError, match="season"):
        generate_copy.copy_from_response(raw)


def test_generate_copy_live_requires_cta_and_image_subtext_when_zones_present(monkeypatch):
    """End-to-end: a blueprint with a cta-purposed object AND a subtext-purposed object
    must reject an empty cta/image_subtext on every attempt and raise, rather than
    silently accepting it."""
    bp = {"objects": [
        _text_obj("cta", description="DISCOVER NOW"),
        _text_obj("subtext", description="SWEET ALMOND | WARM VANILLA"),
    ]}
    empty_copy_json = json.dumps({"headline": "H", "primary_text": "P", "cta": "", "image_subtext": ""})

    class FakeMessage:
        content = [type("obj", (), {"text": empty_copy_json})()]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeMessage()

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(generate_copy.anthropic, "Anthropic", FakeClient)
    with pytest.raises(ValueError):
        generate_copy.generate_copy_live(bp)
