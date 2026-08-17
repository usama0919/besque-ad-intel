"""Three safeguards restored 2026-08-19, confirmed by the src/tests deletion audit as
GONE with no replacement (6b82f60/a9b1e9f) - one confirmed live in production. Per the
standing rule set this same session, nothing was deleted to make room for these; each
is purely additive (a new optional schema field, new functions, new wiring in
_objects_clause) on top of the existing objects model.

Each test below names the specific pre-6b82f60 test (tests/test_edit_mode.py, as it
existed at 6b82f60~1) whose asserted behaviour it restores, per this task's explicit
requirement - the old function operated on a bare `detail` string / a structural_zones
list; the restored version operates on the current objects-model shape
(deconstruct.resolve_disposition / deconstruct.resolve_testimonial_dispositions /
deconstruct._is_stat_shaped_text), so assertions are adapted to the new API, not
byte-identical calls - but the underlying claim being proven is the same one the old
test proved.
"""
from src import deconstruct, generate_image_prompt as gip
from src import validator


def _testimonial_obj(object_id, **overrides):
    base = {
        "object_id": object_id, "kind": "text", "description": "a customer quote",
        "bbox": [0.1, 0.1, 0.3, 0.2], "colours": [], "ownership": "generic",
        "role": "secondary", "carries_brand_mark": False,
        "persuasive_function": "social proof", "disposition": "substitute",
        "text_purpose": "testimonial",
    }
    base.update(overrides)
    return base


def _text_obj(object_id, text_purpose, **overrides):
    base = {
        "object_id": object_id, "kind": "text", "description": "a text block",
        "bbox": [0.1, 0.1, 0.3, 0.2], "colours": [], "ownership": "generic",
        "role": "secondary", "carries_brand_mark": False,
        "persuasive_function": "callout", "disposition": "substitute",
        "text_purpose": text_purpose,
    }
    base.update(overrides)
    return base


REAL_TESTIMONIAL = {"quote": "This oil changed my skin.", "attribution": "sally p."}


# ---- Item 1: duplicate-testimonial guard ----
# Restores test_structural_zones_clause_testimonial_renders_exactly_once_across_two_zones
# and test_structural_zones_clause_handles_several_of_the_same_type
# (tests/test_edit_mode.py at 6b82f60~1). CONFIRMED LIVE: a draft rendered the identical
# review ("Nice and smooth... - Margaret P.") in two separate boxes.

def test_duplicate_testimonial_guard_only_first_of_two_substitutes():
    """Restores test_structural_zones_clause_testimonial_renders_exactly_once_across_
    two_zones - the old assertion was `clause.count("This oil changed my skin.") == 1`;
    the equivalent claim here is that only ONE of the two objects resolves to
    "substitute", the other to "drop"."""
    objects = [_testimonial_obj("obj_09"), _testimonial_obj("obj_10")]
    result = deconstruct.resolve_testimonial_dispositions(objects, {"testimonial": REAL_TESTIMONIAL})
    assert result == {"obj_09": "substitute", "obj_10": "drop"}


def test_duplicate_testimonial_guard_handles_several_of_the_same_type():
    """Restores test_structural_zones_clause_handles_several_of_the_same_type,
    extended from 2 to 3 objects - the old test only proved the clause didn't crash
    or misbehave with several zones of one type; this proves the SAME single-winner
    rule holds regardless of how many duplicates exist, not just exactly two."""
    objects = [_testimonial_obj("obj_09"), _testimonial_obj("obj_10"), _testimonial_obj("obj_11")]
    result = deconstruct.resolve_testimonial_dispositions(objects, {"testimonial": REAL_TESTIMONIAL})
    substituted = [oid for oid, d in result.items() if d == "substitute"]
    assert substituted == ["obj_09"]
    assert result["obj_10"] == "drop"
    assert result["obj_11"] == "drop"


def test_duplicate_testimonial_guard_end_to_end_prompt_renders_quote_exactly_once():
    """End-to-end version of the same restored test, built through the real prompt
    assembly path (_objects_clause) rather than calling the resolver directly - proves
    the fix reaches the actual assembled SCENE OBJECTS text, matching the old test's
    own end-to-end shape (it called _structural_zones_clause, not a lower-level
    resolver, and asserted on the returned clause string)."""
    objects = [
        _testimonial_obj("obj_09", description="first review card quote"),
        _testimonial_obj("obj_10", description="second review card quote"),
    ]
    context = {"testimonial": REAL_TESTIMONIAL}
    clause = gip._objects_clause(objects, context, ad_id="FIXTURE_dup_testimonial")
    assert clause.count("This oil changed my skin.") == 1
    assert clause.count("sally p.") == 1
    assert "SUBSTITUTE" in clause
    assert "ABSENT: the second review card quote" in clause


def test_duplicate_testimonial_guard_no_context_both_drop():
    """No real testimonial supplied this run - both objects drop, same as the
    pre-existing single-object behaviour (resolve_disposition's context-gated branch
    already dropped a lone testimonial object with no context; this proves that
    still holds for two)."""
    objects = [_testimonial_obj("obj_09"), _testimonial_obj("obj_10")]
    result = deconstruct.resolve_testimonial_dispositions(objects, None)
    assert result == {"obj_09": "drop", "obj_10": "drop"}


def test_duplicate_testimonial_guard_missing_attribution_falls_back():
    """Restores test_structural_zones_clause_social_proof_missing_attribution_falls_
    back - the winning object still gets the "a verified customer" fallback wording
    when the real testimonial has no attribution, same as the single-object case."""
    objects = [_testimonial_obj("obj_09")]
    context = {"testimonial": {"quote": "Great oil.", "attribution": ""}}
    clause = gip._objects_clause(objects, context, ad_id="FIXTURE_no_attribution")
    assert "a verified customer" in clause


# ---- Item 3: aggregate review bar vs single quote ----
# Restores test_structural_zones_clause_social_proof_aggregate_bar_always_removed,
# test_structural_zones_clause_social_proof_single_quote_substituted_with_real_review,
# and test_structural_zones_clause_social_proof_unrecognised_kind_removed.

def test_aggregate_social_proof_never_substitutes_even_alone_with_real_testimonial():
    """Restores test_structural_zones_clause_social_proof_aggregate_bar_always_removed
    - Besque has no approved aggregate figure (CLAUDE.md: "A published review-count/
    average is HELD pending Harry"), so an aggregate-shaped object must drop even when
    it is the ONLY testimonial-purposed object and a real quote WAS supplied this run
    - there is no fallback that lets an aggregate bar borrow a single customer's words."""
    objects = [_testimonial_obj("obj_09", social_proof_kind="aggregate")]
    result = deconstruct.resolve_testimonial_dispositions(objects, {"testimonial": REAL_TESTIMONIAL})
    assert result == {"obj_09": "drop"}


def test_single_quote_wins_over_aggregate_when_both_present():
    """Restores test_structural_zones_clause_social_proof_single_quote_substituted_
    with_real_review, extended to prove the aggregate-vs-quote DISTINCTION specifically
    (the old test only had one zone kind at a time) - an aggregate-shaped object never
    wins the one substitute slot even when a genuinely eligible single_quote object
    exists alongside it, regardless of list order."""
    objects = [
        _testimonial_obj("obj_09", social_proof_kind="aggregate"),
        _testimonial_obj("obj_10", social_proof_kind="single_quote"),
    ]
    result = deconstruct.resolve_testimonial_dispositions(objects, {"testimonial": REAL_TESTIMONIAL})
    assert result == {"obj_09": "drop", "obj_10": "substitute"}


def test_unset_social_proof_kind_defaults_to_single_quote_eligible():
    """Restores test_structural_zones_clause_social_proof_unrecognised_kind_removed
    in spirit but with the opposite polarity, deliberately: the OLD schema's default
    for an unrecognised/missing social_proof_kind was conservative REMOVAL (per that
    test's own docstring: "must be conservative... never fall through to no-
    instruction-at-all"). The NEW field is additive and optional (schema/blueprint.
    schema.json's own back-compat contract for every field added this session) - a
    blueprint predating this field entirely must not have its testimonial newly start
    dropping. Absent/None social_proof_kind is therefore treated as "single_quote",
    the same behaviour every testimonial-purposed object already had before this
    field existed - a deliberate, documented deviation from the old default, not an
    oversight."""
    objects = [_testimonial_obj("obj_09", social_proof_kind=None)]
    result = deconstruct.resolve_testimonial_dispositions(objects, {"testimonial": REAL_TESTIMONIAL})
    assert result == {"obj_09": "substitute"}


def test_aggregate_social_proof_object_validates_against_schema():
    bp = {
        "ad_id": "FIXTURE_aggregate_bar", "source_page": "Example", "captured_at": "2026-01-01",
        "format": "testimonial_review", "hook": {"type": "social_proof", "headline_structure": "x"},
        "awareness_stage": "solution", "claims": ["social_proof"],
        "visual": {"layout": "x", "subject": "x", "palette_mood": "x", "text_placement": "x"},
        "background": {"surface": "x", "colour": "x", "light": "x"},
        "objects": [_testimonial_obj("obj_09", social_proof_kind="aggregate",
                                      description="Rated 4.8 by 12,000 customers")],
        "cta": "Shop", "layout_detail": {}, "body_area_shown": "none",
        "face_present": {"has_face": False, "prominence": "none", "location": ""},
        "semantic_split": {"is_split": False, "split_axis": None, "left_or_before": "", "right_or_after": ""},
        "production_style": {"style": "high_spec", "confidence": "high", "signals": []},
    }
    assert validator.is_valid(bp), validator.validation_error(bp)


# ---- Item 2: stat-claim badge removal ----
# Restores test_is_stat_shaped_zone_true_for_percentage, _true_for_ratio_claim,
# _true_for_timescale_claim, _false_for_non_stat_control, and
# test_structural_zones_clause_product_callout_removes_when_stat_shaped_even_with_
# callout_copy - all from tests/test_edit_mode.py at 6b82f60~1. Recovered patterns
# (git show 6b82f60~1:src/generate_image_prompt.py) reused verbatim via
# deconstruct.STAT_CLAIM_PATTERNS = (compliance.NUMERIC_CLAIM_PATTERN,
# compliance.RATIO_CLAIM_PATTERN, compliance.TIMESCALE_CLAIM_PATTERN).

def test_is_stat_shaped_text_true_for_percentage_claim():
    """Restores test_is_stat_shaped_zone_true_for_percentage ("94% saw visible
    results") - old signature took a bare string, new one takes the object dict."""
    obj = _text_obj("obj_05", "product_callout", description="94% saw visible results")
    assert deconstruct._is_stat_shaped_text(obj) is True


def test_is_stat_shaped_text_true_for_ratio_claim():
    """Restores test_is_stat_shaped_zone_true_for_ratio_claim ("9 out of 10 customers
    agree", "3x faster absorption")."""
    obj_a = _text_obj("obj_05", "product_callout", description="9 out of 10 customers agree")
    obj_b = _text_obj("obj_06", "product_callout", description="3x faster absorption")
    assert deconstruct._is_stat_shaped_text(obj_a) is True
    assert deconstruct._is_stat_shaped_text(obj_b) is True


def test_is_stat_shaped_text_true_for_timescale_claim():
    """Restores test_is_stat_shaped_zone_true_for_timescale_claim ("results in just
    7 days")."""
    obj = _text_obj("obj_05", "product_callout", description="results in just 7 days")
    assert deconstruct._is_stat_shaped_text(obj) is True


def test_is_stat_shaped_text_false_for_non_stat_control():
    """Restores test_is_stat_shaped_zone_false_for_non_stat_control - a bottle size is
    a number too, but it's not a stat/efficacy claim shape; proves this isn't just
    "does the string contain a digit"."""
    obj_a = _text_obj("obj_05", "product_callout", description="reads 8 fl oz / 240ml")
    obj_b = _text_obj("obj_06", "product_callout", description="New Scent card - Coconut Vanilla")
    obj_c = _text_obj("obj_07", "product_callout", description="")
    assert deconstruct._is_stat_shaped_text(obj_a) is False
    assert deconstruct._is_stat_shaped_text(obj_b) is False
    assert deconstruct._is_stat_shaped_text(obj_c) is False


def test_stat_shaped_product_callout_always_drops_even_though_callout_purpose_normally_substitutes():
    """Restores test_structural_zones_clause_product_callout_removes_when_stat_shaped_
    even_with_callout_copy - product_callout is unconditionally in _TEXT_PURPOSE_ALWAYS_
    SUBSTITUTE, so this proves the stat-shape check actually intercepts it BEFORE that
    unconditional rule, not after (a real ordering bug this restoration could have
    reintroduced if checked in the wrong place)."""
    obj = _text_obj("obj_05", "product_callout",
                     description="91% saw visibly firmer skin in 4 weeks", disposition="substitute")
    assert deconstruct.resolve_disposition(obj) == "drop"


def test_stat_shaped_other_purpose_always_drops():
    """The live risk this restoration exists for: a stat-shaped badge classified
    text_purpose="other" (the current model's catch-all bucket) would otherwise reach
    disposition="substitute" and get FRESH Besque wording written into it by
    _object_copy_clause - an unsubstantiated efficacy claim, the exact violation class
    from 31 Jul. Must drop instead, container included."""
    obj = _text_obj("obj_05", "other",
                     description="roundel badge reading +61% more supple skin", disposition="substitute")
    assert deconstruct.resolve_disposition(obj) == "drop"


def test_stat_shaped_check_scoped_to_product_callout_and_other_only():
    """Deliberately NOT headline/subtext/cta/offer/certification/testimonial/
    price_anchor/award/disclaimer - matching the ORIGINAL pre-6b82f60 scope exactly
    (that code only ever ran _is_stat_shaped_zone against product_callout zones). A
    stat-shaped headline's wording is governed entirely by rule 6/TIER 1 angle
    language elsewhere and never copies the reference's claim verbatim - forcing its
    SLOT to drop here would delete a headline position that should still exist for
    Besque's own non-stat wording to occupy, a regression this test locks against."""
    headline = _text_obj("obj_01", "headline", description="headline reading 94% of users agree",
                          role="hero")
    assert deconstruct.resolve_disposition(headline) == "substitute"


def test_stat_shaped_check_reads_persuasive_function_too():
    """The claim may land in persuasive_function rather than description depending on
    how the vision model phrases it - both fields are checked, not description alone."""
    obj = _text_obj("obj_05", "other", description="a small roundel badge",
                     persuasive_function="claims 94% saw visible results", disposition="substitute")
    assert deconstruct.resolve_disposition(obj) == "drop"


def test_stat_shaped_product_callout_end_to_end_prompt_shows_absent_not_substitute():
    """End-to-end: the assembled SCENE OBJECTS clause shows ABSENT for the stat-shaped
    callout, matching the old test's own end-to-end assertion shape (`"STRUCTURAL
    ZONES - SUBSTITUTE" not in clause`, `"STRUCTURAL ZONES - REMOVE" in clause`)."""
    objects = [_text_obj("obj_05", "product_callout",
                          description="91% saw visibly firmer skin in 4 weeks")]
    clause = gip._objects_clause(objects, {}, ad_id="FIXTURE_stat_badge")
    assert "SUBSTITUTE" not in clause
    assert "ABSENT: the 91% saw visibly firmer skin in 4 weeks" in clause
