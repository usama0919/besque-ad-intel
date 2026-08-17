"""Three quality (non-compliance) capabilities restored 2026-08-17, deleted by
6b82f60/a9b1e9f with no replacement, confirmed by the deletion audit. Per the
standing rule this same day, all three are purely additive on the objects model -
new optional fields on objects[], no top-level array revived, nothing removed.

Each test names the specific pre-refactor test whose asserted behaviour it restores,
per this task's explicit requirement. The old functions operated on a separate
top-level array matched by a free-text position/zone string
(typography_zones/testimonial_zones/text_purpose); the restored versions operate on
per-object fields (typography/styling/text_purpose, all on blueprint.objects[]), so
assertions are adapted to the new API and data shape, not byte-identical calls - but
the underlying claim being proven is the same one the old test proved.
"""
from src import deconstruct, generate_copy as gc, generate_image_prompt as gip
from src import validator


def _text_obj(object_id, text_purpose, **overrides):
    base = {
        "object_id": object_id, "kind": "text", "description": f"{object_id} text",
        "bbox": [0.1, 0.1, 0.3, 0.1], "colours": [], "ownership": "generic",
        "role": "secondary", "carries_brand_mark": False,
        "persuasive_function": "unspecified", "disposition": "substitute",
        "text_purpose": text_purpose,
    }
    base.update(overrides)
    return base


TYPOGRAPHY_A = {
    "typeface_class": "serif", "weight": "bold", "case": "title",
    "letter_spacing": "normal", "colour": "white", "size_relative": "large",
    "decorative_elements": [], "line_count": 2,
}
TYPOGRAPHY_B = {
    "typeface_class": "sans", "weight": "light", "case": "sentence",
    "letter_spacing": "wide", "colour": "gold", "size_relative": "small",
    "decorative_elements": ["pipe divider between clauses"], "line_count": 3,
}


# ---- Item 1: per-zone typography, restored onto objects[].typography ----
# Restores test_typography_zones_clause_empty_for_blank_input,
# _states_every_field_per_zone, _never_raises_on_missing_fields, and
# test_build_image_prompt_edit_mode_reads_typography_zones_from_blueprint /
# _generate_mode_unaffected_by_typography_zones (tests/test_edit_mode.py, 6b82f60~1).

def test_object_typography_clause_empty_for_blank_input():
    """Restores test_typography_zones_clause_empty_for_blank_input."""
    for blank in (None, []):
        assert gip._object_typography_clause(blank, {}) == ""


def test_object_typography_clause_empty_when_no_object_has_typography():
    """New-model equivalent of the blank-input case: text objects exist but none
    carries a `typography` field (a pre-existing blueprint predating this field)."""
    objects = [_text_obj("obj_01", "headline")]
    assert gip._object_typography_clause(objects, {}) == ""


def test_object_typography_clause_states_every_field_per_object():
    """Restores test_typography_zones_clause_states_every_field_per_zone - the old
    test labelled each level by a `zone` position string; the new one labels each by
    the object's own `description`."""
    objects = [
        _text_obj("obj_01", "headline", description="headline upper-right", typography=TYPOGRAPHY_A),
        _text_obj("obj_02", "subtext", description="ingredient sub-copy mid-right", typography=TYPOGRAPHY_B),
    ]
    clause = gip._object_typography_clause(objects, {})
    assert "TYPOGRAPHIC LEVELS" in clause
    assert "2 distinct typographic level(s)" in clause
    assert "never collapsing two into one" in clause
    assert "headline upper-right" in clause
    assert "serif typeface" in clause
    assert "bold weight" in clause
    assert "title case" in clause
    assert "normal letter-spacing" in clause
    assert "colour white" in clause
    assert "large relative to the frame" in clause
    assert "2 line(s)" in clause
    assert "ingredient sub-copy mid-right" in clause
    assert "sans typeface" in clause
    assert "wide letter-spacing" in clause
    assert "colour gold" in clause
    assert "pipe divider between clauses" in clause
    assert "3 line(s)" in clause


def test_object_typography_clause_never_raises_on_missing_fields():
    """Restores test_typography_zones_clause_never_raises_on_missing_fields - a
    partially-filled typography dict must degrade to "?" placeholders, never crash."""
    objects = [_text_obj("obj_01", "headline", description="headline", typography={})]
    clause = gip._object_typography_clause(objects, {})
    assert "headline" in clause
    assert "? typeface" in clause
    assert "? line(s)" in clause


def test_object_typography_clause_excludes_dropped_objects():
    """New behaviour, not present in the old code (which never filtered by
    disposition despite its own closing sentence claiming "for whichever zones
    survive") - an object that resolves to "drop" contributes no typographic
    instruction, since dressing something being removed is meaningless."""
    objects = [
        _text_obj("obj_01", "headline", description="surviving headline", typography=TYPOGRAPHY_A),
        _text_obj("obj_02", "award", description="removed award badge", typography=TYPOGRAPHY_B),
    ]
    clause = gip._object_typography_clause(objects, {})
    assert "surviving headline" in clause
    assert "removed award badge" not in clause
    assert "1 distinct typographic level(s)" in clause


def test_build_image_prompt_edit_mode_reads_typography_from_objects():
    """Restores test_build_image_prompt_edit_mode_reads_typography_zones_from_
    blueprint."""
    bp = {
        "ad_id": "FIXTURE_typography", "source_page": "x", "captured_at": "x",
        "format": "product_hero", "hook": {"type": "bold_claim", "headline_structure": "x"},
        "awareness_stage": "problem", "claims": [],
        "visual": {"layout": "x", "subject": "x", "palette_mood": "x", "text_placement": "x"},
        "background": {"surface": "x", "colour": "x", "light": "soft light"},
        "objects": [_text_obj("obj_01", "offer", description="offer banner bottom-right",
                               typography=TYPOGRAPHY_A)],
        "cta": "Shop", "layout_detail": {}, "body_area_shown": "none",
        "face_present": {"has_face": False, "prominence": "none", "location": ""},
        "semantic_split": {"is_split": False, "split_axis": None, "left_or_before": "", "right_or_after": ""},
        "production_style": {"style": "high_spec", "confidence": "high", "signals": []},
    }
    prompt = gip.build_image_prompt(bp, product=None, include_product=False, edit_mode=True,
                                     offer_text="20% off", realism=None)
    assert "TYPOGRAPHIC LEVELS" in prompt
    assert "offer banner bottom-right" in prompt


def test_build_image_prompt_generate_mode_unaffected_by_typography():
    """Restores test_build_image_prompt_generate_mode_unaffected_by_typography_zones -
    typography is edit-mode only (matching the deleted clause's own original scope);
    the flat template path never reads it."""
    bp = {
        "ad_id": "FIXTURE_typography2", "source_page": "x", "captured_at": "x",
        "format": "product_hero", "hook": {"type": "bold_claim", "headline_structure": "x"},
        "awareness_stage": "problem", "claims": [],
        "visual": {"layout": "x", "subject": "x", "palette_mood": "x", "text_placement": "x"},
        "background": {"surface": "x", "colour": "x", "light": "soft light"},
        "objects": [_text_obj("obj_01", "headline", description="headline", typography=TYPOGRAPHY_A)],
        "cta": "Shop", "layout_detail": {}, "body_area_shown": "none",
        "face_present": {"has_face": False, "prominence": "none", "location": ""},
        "semantic_split": {"is_split": False, "split_axis": None, "left_or_before": "", "right_or_after": ""},
        "production_style": {"style": "high_spec", "confidence": "high", "signals": []},
    }
    prompt = gip.build_image_prompt(bp, product=None, include_product=False, edit_mode=False, realism=None)
    assert "TYPOGRAPHIC LEVELS" not in prompt


def test_typography_field_validates_against_schema_and_is_optional():
    bp_with = {
        "ad_id": "FIXTURE_typo_schema", "source_page": "x", "captured_at": "x",
        "format": "product_hero", "hook": {"type": "bold_claim", "headline_structure": "x"},
        "awareness_stage": "problem", "claims": [],
        "visual": {"layout": "x", "subject": "x", "palette_mood": "x", "text_placement": "x"},
        "background": {"surface": "x", "colour": "x", "light": "x"},
        "objects": [_text_obj("obj_01", "headline", typography=TYPOGRAPHY_A)],
        "cta": "Shop", "layout_detail": {}, "body_area_shown": "none",
        "face_present": {"has_face": False, "prominence": "none", "location": ""},
        "semantic_split": {"is_split": False, "split_axis": None, "left_or_before": "", "right_or_after": ""},
        "production_style": {"style": "high_spec", "confidence": "high", "signals": []},
    }
    assert validator.is_valid(bp_with), validator.validation_error(bp_with)
    bp_without = {**bp_with, "objects": [_text_obj("obj_01", "headline")]}
    assert validator.is_valid(bp_without), validator.validation_error(bp_without)


# ---- Item 2: testimonial styling, restored onto objects[].styling ----
# Restores test_structural_zones_clause_testimonial_zones_adds_styling_detail and
# _testimonial_zones_absent_unaffected (tests/test_edit_mode.py, 6b82f60~1).

def _testimonial_obj(object_id, **overrides):
    base = {
        "object_id": object_id, "kind": "text", "description": "a customer quote",
        "bbox": [0.1, 0.5, 0.3, 0.2], "colours": [], "ownership": "generic",
        "role": "secondary", "carries_brand_mark": False,
        "persuasive_function": "social proof", "disposition": "substitute",
        "text_purpose": "testimonial",
    }
    base.update(overrides)
    return base


REAL_TESTIMONIAL_CTX = {"testimonial": {"quote": "This oil changed my skin.", "attribution": "Jane D."}}


def test_testimonial_styling_instruction_adds_styling_detail():
    """Restores test_structural_zones_clause_testimonial_zones_adds_styling_detail -
    content still comes ONLY from the real testimonial in context, styling comes
    ONLY from the object's own `styling` field, never mixed up."""
    obj = _testimonial_obj("obj_09", styling="Avatar thumbnail top-left, reaction bar below quote")
    line = gip._substitute_object_line(obj, "text", "testimonial", obj["description"], REAL_TESTIMONIAL_CTX)
    assert "Avatar thumbnail top-left, reaction bar below quote" in line
    assert '"This oil changed my skin."' in line
    assert "Jane D." in line


def test_testimonial_styling_instruction_absent_unaffected():
    """Restores test_structural_zones_clause_testimonial_zones_absent_unaffected -
    no `styling` on the object (every pre-existing testimonial-purposed object) -
    byte-for-byte the same substitution as before this field existed, just without
    the extra styling detail."""
    obj = _testimonial_obj("obj_09")
    line = gip._substitute_object_line(obj, "text", "testimonial", obj["description"], REAL_TESTIMONIAL_CTX)
    assert '"This oil changed my skin."' in line
    assert "Jane D." in line
    assert "Match this reference's own styling" not in line


def test_testimonial_styling_instruction_carries_account_chrome_carve_out():
    """The account-chrome carve-out (rule C9) is not optional to drop when restoring
    styling - "match this reference's own styling for the card" previously read as
    license to reproduce the reference's own avatar/handle verbatim, a real live leak
    this exact wording closed. Must survive the restoration unchanged."""
    obj = _testimonial_obj("obj_09", styling="Avatar circle top-left, verified checkmark")
    line = gip._substitute_object_line(obj, "text", "testimonial", obj["description"], REAL_TESTIMONIAL_CTX)
    assert "NEVER license to reproduce WHOSE account this is" in line
    assert "compliance rule C9" in line
    assert "rule 10" in line


def test_testimonial_styling_instruction_empty_when_no_styling():
    obj = _testimonial_obj("obj_09")
    assert gip._testimonial_styling_instruction(obj) == ""


def test_testimonial_styling_end_to_end_through_objects_clause():
    objects = [_testimonial_obj("obj_09", styling="quote marks, no card")]
    clause = gip._objects_clause(objects, REAL_TESTIMONIAL_CTX, ad_id="FIXTURE_styling")
    assert "quote marks, no card" in clause
    assert '"This oil changed my skin."' in clause


def test_styling_field_validates_against_schema_and_is_optional():
    obj_with = _testimonial_obj("obj_09", social_proof_kind="single_quote", styling="quote marks, no card")
    obj_without = _testimonial_obj("obj_10", social_proof_kind="single_quote")
    bp = {
        "ad_id": "FIXTURE_styling_schema", "source_page": "x", "captured_at": "x",
        "format": "testimonial_review", "hook": {"type": "social_proof", "headline_structure": "x"},
        "awareness_stage": "solution", "claims": ["social_proof"],
        "visual": {"layout": "x", "subject": "x", "palette_mood": "x", "text_placement": "x"},
        "background": {"surface": "x", "colour": "x", "light": "x"},
        "objects": [obj_with, obj_without],
        "cta": "Shop", "layout_detail": {}, "body_area_shown": "none",
        "face_present": {"has_face": False, "prominence": "none", "location": ""},
        "semantic_split": {"is_split": False, "split_axis": None, "left_or_before": "", "right_or_after": ""},
        "production_style": {"style": "high_spec", "confidence": "high", "signals": []},
    }
    assert validator.is_valid(bp), validator.validation_error(bp)


# ---- Item 3: copy purpose-steering, restored onto per-object text_purpose ----
# Restores test_text_purpose_clause_prohibits_reusing_reference_wording,
# _prohibits_personal_name_in_output, _redacts_handle_from_text_verbatim, and
# _redacts_personal_name_from_text_verbatim (tests/test_generate_copy.py, a9b1e9f~1).

def test_communicative_purpose_clause_prohibits_reusing_reference_wording():
    """Restores test_text_purpose_clause_prohibits_reusing_reference_wording - old
    test used purpose="problem_hook" (an enum value that no longer exists); the new
    text_purpose enum has no equivalent persuasive-mode category, so this uses
    "headline" (a real current value) with a problem-hook-shaped description in its
    place - the RULE being proven (never reuse the reference's own sentence
    structure) is purpose-independent."""
    bp = {"objects": [_text_obj("obj_01", "headline", description="Tired of crepey skin?")]}
    prompt = gc._communicative_purpose_clause(bp)
    assert "entirely new sentences" in prompt.lower()
    assert "never reuse the reference's own sentence structure" in prompt


def test_communicative_purpose_clause_prohibits_personal_name_in_output():
    """Restores test_text_purpose_clause_prohibits_personal_name_in_output."""
    bp = {"objects": [_text_obj("obj_01", "testimonial", description="Loving this. Sean R.")]}
    prompt = gc._communicative_purpose_clause(bp)
    assert "must never appear in your output" in prompt
    assert "APPROVED TESTIMONIALS" in prompt


def test_communicative_purpose_clause_redacts_handle_from_description():
    """Restores test_text_purpose_clause_redacts_handle_from_text_verbatim - old test
    read from text_verbatim, new one reads from description (objects have no literal
    transcription field for arbitrary text blocks)."""
    bp = {"objects": [_text_obj("obj_01", "testimonial",
                                 description="@fitness_ty: this changed everything")]}
    prompt = gc._communicative_purpose_clause(bp)
    assert "@fitness_ty" not in prompt


def test_communicative_purpose_clause_redacts_personal_name_from_description():
    """Restores test_text_purpose_clause_redacts_personal_name_from_text_verbatim."""
    bp = {"objects": [_text_obj("obj_01", "testimonial",
                                 description="So glad I tried this. Wendy P.")]}
    prompt = gc._communicative_purpose_clause(bp)
    assert "Wendy P" not in prompt


def test_communicative_purpose_clause_empty_for_no_text_objects():
    """New-model equivalent of the deleted clause's own "" default for a blueprint
    with no text_purpose data - a blueprint with no kind=="text" objects at all
    (or none) produces byte-identical prompt output."""
    assert gc._communicative_purpose_clause({"objects": []}) == ""
    assert gc._communicative_purpose_clause({}) == ""
    assert gc._communicative_purpose_clause({"objects": [
        {"object_id": "obj_01", "kind": "product", "description": "a bottle"}
    ]}) == ""


def test_communicative_purpose_clause_lists_every_text_object_by_its_job():
    """The broader restoration this task actually requires, beyond the redaction
    tests above: EVERY text object's job is listed (not just the "other" bucket
    _object_copy_clause already covers), so an offer-led reference steers toward
    offer-led Besque copy and a problem-hook reference toward a problem-hook."""
    bp = {"objects": [
        _text_obj("obj_01", "offer", description="20% off today only",
                   persuasive_function="creates urgency around a discount"),
        _text_obj("obj_02", "headline", description="Tired of crepey skin?",
                   persuasive_function="agitates the core pain point"),
    ]}
    prompt = gc._communicative_purpose_clause(bp)
    assert "20% off today only" in prompt
    assert "offer" in prompt
    assert "creates urgency around a discount" in prompt
    assert "Tired of crepey skin?" in prompt
    assert "headline" in prompt
    assert "agitates the core pain point" in prompt


def test_communicative_purpose_clause_wired_into_build_copy_prompt():
    bp = {"objects": [_text_obj("obj_01", "offer", description="20% off today only")]}
    prompt = gc.build_copy_prompt(bp)
    assert "COMMUNICATIVE PURPOSE" in prompt
    assert "20% off today only" in prompt


def test_communicative_purpose_clause_never_double_commissions_with_object_copy_clause():
    """The "other"-bucket object gets a SPECIFIC content instruction from
    _object_copy_clause AND a general register-steering mention from this clause -
    the two operate at different levels (specific content vs. overall tone) and are
    never in conflict, unlike the deleted _dedupe_text_purpose_against_zones' problem
    (two mechanisms independently commissioning the SAME content decision)."""
    bp = {"objects": [_text_obj("obj_01", None, description="a DM bubble reply")]}
    prompt = gc.build_copy_prompt(bp)
    assert "OBJECT COPY" in prompt
    assert "COMMUNICATIVE PURPOSE" in prompt
