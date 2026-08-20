"""Text sub-objects (2026-08-20, DETECTION ONLY). Root cause: competitor copy baked
into a non-text object's own pixels never became a text object, so resolve_
disposition was never consulted on it at all - confirmed live twice, "Norse
Organics" rendered twice on a gradient-panel graphic and "KilgourMD" rendered on
both bottle labels of a substituted product.

Fixed: any object may carry a `text_content` array (schema/blueprint.schema.json) -
legible text baked into that object's own pixels, regardless of its `kind`. Each
sub-object goes through the EXACT SAME resolve_disposition machinery as a top-level
kind=='text' object, at both resolution points (deconstruct time and generation
time), so a branded sub-object can never survive as "keep". `content` is
analysis-only - generate_image_prompt.build_image_prompt asserts at the end of every
build that no sub-object's content string appears verbatim in the assembled prompt.

Also covers the separate attribution-leak fix: _substitute_object_line's testimonial
branch no longer constructs the literal phrase "attributed to X" - that exact shape
is what a live draft rendered as pixel text, because it is also the shape
PERSONAL_NAME_ATTRIBUTION_PATTERN (src/compliance.py) exists to strip from output-
side text. Audited generate_copy._redact_personal_attribution's own call sites
separately - none of them feed the image path at all; see the report for this task."""
import json

import pytest

from src import deconstruct, generate_image_prompt as gip
from tests.blueprint_fixtures import load_blueprint_fixture

OSEA_BLUEPRINT = load_blueprint_fixture("osea_two_products_both_substitute")

PRODUCT = {
    "name": "Magic Body Oil",
    "visual_description": "a tall cylindrical bottle with a gold collar and black pump",
    "substance_colour": "golden-amber oil",
    "certifications": ["Vegan", "Cruelty Free", "100% Natural"],
}


def _sub(object_id, content="Norse Organics", **overrides):
    base = {
        "object_id": object_id, "content": content, "bbox": [0.1, 0.1, 0.2, 0.05],
        "text_purpose": "other", "ownership": "competitor_branded",
        "carries_brand_mark": True, "disposition": "keep",
    }
    base.update(overrides)
    return base


# ---- Schema ----

def test_schema_accepts_text_content_on_a_non_text_object():
    from src import validator
    bp = json.loads(json.dumps(OSEA_BLUEPRINT))
    bp["objects"][2]["text_content"] = [_sub("obj_03_txt_01")]
    assert validator.validation_error(bp) is None


def test_schema_rejects_text_content_entry_missing_required_field():
    from src import validator
    bp = json.loads(json.dumps(OSEA_BLUEPRINT))
    bad_sub = _sub("obj_03_txt_01")
    del bad_sub["disposition"]
    bp["objects"][2]["text_content"] = [bad_sub]
    assert validator.validation_error(bp) is not None


# ---- Disposition: resolve_disposition runs on every text sub-object, both resolution points ----

def test_resolve_disposition_branded_sub_object_never_resolves_to_keep():
    sub = _sub("obj_03_txt_01", disposition="keep")  # model's own (wrong) guess
    assert deconstruct.resolve_disposition(sub) == "drop"


def test_resolve_disposition_branded_via_ownership_alone_never_keep():
    sub = _sub("obj_03_txt_01", carries_brand_mark=False,
                ownership="competitor_branded", disposition="keep")
    assert deconstruct.resolve_disposition(sub) == "drop"


def test_resolve_disposition_generic_sub_object_passes_through_stored_value():
    sub = _sub("obj_03_txt_01", ownership="generic", carries_brand_mark=False,
                disposition="keep")
    assert deconstruct.resolve_disposition(sub) == "keep"


def test_resolve_disposition_context_gated_purpose_on_sub_object_is_actually_gated():
    """2026-08-20 bug found while building the empty-container fix: a sub-object
    has no `kind` field (schema/blueprint.schema.json - it doesn't need one, its
    own text_purpose already says what it is), but resolve_disposition's dispatch
    checked kind=="text" only - every sub-object silently fell through to the
    generic (non-text) branches, so a context-gated purpose (offer/certification/
    testimonial) was NEVER actually gated on a sub-object. Branded sub-objects
    resolved correctly by accident (both paths agree "drop" for anything branded),
    which is why this went unnoticed until the empty-container fix's own dual-
    resolution claim (a sub-object re-resolving "substitute" once real context
    exists) needed it to actually work."""
    sub = _sub("obj_02_txt_01", text_purpose="offer", ownership="generic",
               carries_brand_mark=False, disposition="drop")
    assert deconstruct.resolve_disposition(sub, {}) == "drop"
    assert deconstruct.resolve_disposition(sub, {"offer_text": "20% OFF"}) == "substitute"


def test_resolve_text_content_dispositions_returns_new_list_never_mutates():
    subs = [_sub("obj_03_txt_01", disposition="keep")]
    resolved = deconstruct._resolve_text_content_dispositions(subs)
    assert resolved[0]["disposition"] == "drop"
    assert subs[0]["disposition"] == "keep"  # original untouched


def test_deconstruct_time_resolution_resolves_sub_objects_on_every_object():
    """First resolution point: deconstruct._resolve_object_dispositions."""
    blueprint = {
        "objects": [
            {"object_id": "obj_01", "kind": "graphic", "ownership": "generic",
             "carries_brand_mark": False, "disposition": "keep",
             "text_content": [_sub("obj_01_txt_01", disposition="keep")]},
        ]
    }
    resolved = deconstruct._resolve_object_dispositions(blueprint)
    sub = resolved["objects"][0]["text_content"][0]
    assert sub["disposition"] == "drop"


def test_objects_clause_resolution_re_resolves_sub_objects_with_real_context():
    """Second resolution point: generate_image_prompt._objects_clause, via
    _text_content_removal_note - context-gated purposes could in principle differ
    from the deconstruct-time (no-context) resolution, same dual-resolution design
    as every other context-gated field in this function."""
    obj = {"object_id": "obj_01", "kind": "graphic", "description": "gradient panel",
           "role": "environment", "ownership": "generic", "carries_brand_mark": False,
           "disposition": "keep",
           "text_content": [_sub("obj_01_txt_01", disposition="keep")]}
    note = gip._text_content_removal_note(obj, {})
    assert "baked-in text/brand mark" in note
    assert "Norse Organics" not in note


def test_assert_no_competitor_branded_object_kept_extended_to_sub_objects():
    blueprint = {
        "objects": [
            {"object_id": "obj_01", "kind": "graphic", "ownership": "generic",
             "carries_brand_mark": False, "disposition": "keep",
             "text_content": [_sub("obj_01_txt_01", disposition="keep")]},
        ]
    }
    with pytest.raises(deconstruct.BlueprintValidationError):
        deconstruct._assert_no_competitor_branded_object_kept(blueprint)


def test_assert_no_competitor_branded_object_kept_passes_when_sub_object_dropped():
    blueprint = {
        "objects": [
            {"object_id": "obj_01", "kind": "graphic", "ownership": "generic",
             "carries_brand_mark": False, "disposition": "keep",
             "text_content": [_sub("obj_01_txt_01", disposition="drop")]},
        ]
    }
    deconstruct._assert_no_competitor_branded_object_kept(blueprint)  # must not raise


# ---- Content is analysis-only: never emitted into a built prompt ----

def test_objects_clause_never_quotes_sub_object_content():
    objects = [
        {"object_id": "obj_01", "kind": "graphic", "description": "gradient panel",
         "role": "environment", "ownership": "generic", "carries_brand_mark": False,
         "disposition": "keep",
         "text_content": [_sub("obj_01_txt_01", content="Norse Organics")]},
    ]
    clause = gip._objects_clause(objects, {}, ad_id="FIXTURE_subtext")
    assert "Norse Organics" not in clause
    assert "baked-in text/brand mark" in clause


def test_build_image_prompt_end_to_end_kept_object_with_branded_sub_text():
    """Reproduces the live 'Norse Organics on a gradient panel' shape: a KEPT,
    non-branded graphic object that nonetheless carries a competitor-branded
    sub-text baked into its own pixels."""
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    blueprint["objects"].append({
        "object_id": "obj_99", "kind": "graphic", "description": "gradient panel",
        "role": "environment", "colours": ["gold"], "ownership": "generic",
        "carries_brand_mark": False, "persuasive_function": "background texture",
        "disposition": "keep",
        "text_content": [_sub("obj_99_txt_01", content="Norse Organics")],
    })
    prompt = gip.build_image_prompt(
        blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    assert "Norse Organics" not in prompt
    assert "baked-in text/brand mark" in prompt


def test_build_image_prompt_end_to_end_substituted_product_with_branded_sub_text():
    """Reproduces the live 'KilgourMD on both bottle labels' shape: a SUBSTITUTED
    product object that also carries a competitor-branded sub-text (a printed
    wordmark on its own label)."""
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    for obj in blueprint["objects"]:
        if obj["object_id"] == "obj_03":
            obj["text_content"] = [_sub("obj_03_txt_01", content="KilgourMD")]
    prompt = gip.build_image_prompt(
        blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    assert "KilgourMD" not in prompt
    assert "baked-in text/brand mark" in prompt


# ---- _assert_no_text_content_leak in isolation ----

def test_assert_no_text_content_leak_passes_when_absent():
    objects = [{"object_id": "obj_01", "text_content": [_sub("obj_01_txt_01", content="Norse Organics")]}]
    gip._assert_no_text_content_leak("a clean prompt with nothing leaked", objects)  # no raise


def test_assert_no_text_content_leak_raises_when_content_appears_verbatim():
    objects = [{"object_id": "obj_01", "text_content": [_sub("obj_01_txt_01", content="Norse Organics")]}]
    with pytest.raises(gip.TextContentLeakError):
        gip._assert_no_text_content_leak(
            "some prompt text mentioning Norse Organics by name", objects, ad_id="AD1",
        )


def test_assert_no_text_content_leak_skips_very_short_content():
    objects = [{"object_id": "obj_01", "text_content": [_sub("obj_01_txt_01", content="OK")]}]
    gip._assert_no_text_content_leak("the word OK appears here too", objects)  # no raise


def test_build_image_prompt_raises_when_a_future_site_leaks_sub_object_content(monkeypatch):
    """Integration proof the assertion is actually wired into build_image_prompt -
    force a leak by monkeypatching the removal-note helper to (wrongly) quote the
    content, confirming it's caught rather than silently shipped."""
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    blueprint["objects"].append({
        "object_id": "obj_99", "kind": "graphic", "description": "gradient panel",
        "role": "environment", "colours": ["gold"], "ownership": "generic",
        "carries_brand_mark": False, "persuasive_function": "background texture",
        "disposition": "keep",
        "text_content": [_sub("obj_99_txt_01", content="Norse Organics")],
    })

    def _leaky_note(obj, context):
        return " LEAKED: Norse Organics"

    monkeypatch.setattr(gip, "_text_content_removal_note", _leaky_note)
    with pytest.raises(gip.TextContentLeakError):
        gip.build_image_prompt(
            blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
        )


# ---- Attribution leak fix ----

def test_substitute_object_line_testimonial_never_says_attributed_to():
    obj = {"object_id": "obj_09", "description": "a customer quote"}
    context = {"testimonial": {"quote": "This oil changed my skin.", "attribution": "Leah E."}}
    line = gip._substitute_object_line(obj, "text", "testimonial", obj["description"], context)
    assert "attributed to" not in line.lower()
    assert "Leah E." in line
    assert "This oil changed my skin." in line


def test_substitute_object_line_testimonial_no_attribution_never_invents_one():
    """2026-08-20 (Part D, verbatim binding): superseded assertion - this test used
    to require the generic "a verified customer" fallback when the source row
    carries no attribution. That fallback was itself the violation Part D closes
    ("attribution only as it exists in the source row, or none at all") - a source
    row with attribution="" (a real, common outcome - see pipeline.
    select_testimonial_review's own return statements) must now render with NO
    name/initial attached, never a generic invented placeholder."""
    obj = {"object_id": "obj_09", "description": "a customer quote"}
    context = {"testimonial": {"quote": "Great oil.", "attribution": ""}}
    line = gip._substitute_object_line(obj, "text", "testimonial", obj["description"], context)
    assert "a verified customer" not in line
    assert "attributed to" not in line.lower()
    assert "no name" in line.lower() or "no attribution" in line.lower()
