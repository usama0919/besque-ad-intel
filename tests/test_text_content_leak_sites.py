"""Fix for text_content leak sites surfaced by _assert_no_text_content_leak
(2026-08-20). Three ads raised TextContentLeakError in the 13:46 batch:

- 1357229623024367, obj_17_txt_02, "The Treatment Scalp Serum" (a product label)
- 1767532861100741, obj_02_txt_01, "90-Day Guarantee" (a badge)
- 965378629787425, obj_06_txt_06, "Volumizing Conditioner" (a product label)

Root cause, confirmed by tracing every quoting site: `description` (and, for
products, `appearance`) are model-authored free text that commonly restates an
object's own baked-in visible text verbatim - exactly what text_content now
separately, correctly, detects. Every site that quotes description/appearance/a
typography label into a built prompt is a latent leak, whether or not it happens
to fire on any given blueprint. Fixed at the SOURCE (scrub known text_content
strings out of these fields before they are ever quoted), not by weakening
_assert_no_text_content_leak - it is working correctly, per the task's own
instruction.

Confirmed sites, all fixed:
1. generate_image_prompt._objects_clause's `description` (feeds KEEP/OBSERVED/
   ABSENT lines directly, and _substitute_object_line's every branch via the
   `description` parameter it passes down).
2. generate_image_prompt._substitute_object_line's `appearance_text` (product
   branch) - a SEPARATE read of obj["appearance"], not covered by (1).
3. generate_image_prompt._object_typography_clause's `label` - an independent
   computation, not fed from (1).
4. generate_copy._object_copy_clause's `description`/`persuasive_function`.
5. generate_copy._blueprint_without_bbox - the raw blueprint dump fed to the COPY
   prompt left text_content fully intact; Claude's generated copy could echo a
   detected string back into `object_copy`, which then reaches the IMAGE prompt.
6. generate_image_prompt.build_targeted_edit_instruction's `label`/`current_value`
   (Dynamic Edit System, dashboard.py's targeted-edit path).
7. generate_image_prompt.build_object_removal_instruction's `description` (same
   subsystem, object-removal path) - previously took no blueprint at all.
8. generate_image_prompt.build_drift_retry_instruction's `label` (same subsystem,
   the one-shot drift retry note).

Sites 6-8 are notable: build_image_prompt's own _assert_no_text_content_leak does
NOT cover them even indirectly, because dashboard.py's object-removal flow
concatenates build_object_removal_instruction's output onto an ALREADY-CHECKED
build_image_prompt result (_regenerate_image_bytes) - the checked string and the
leak-vulnerable string are never the same string the assertion ran against."""
import json

import pytest

from src import deconstruct, generate_copy, generate_image_prompt as gip
from tests.blueprint_fixtures import load_blueprint_fixture

OSEA_BLUEPRINT = load_blueprint_fixture("osea_two_products_both_substitute")

PRODUCT = {
    "name": "Magic Body Oil",
    "visual_description": "a tall cylindrical bottle with a gold collar and black pump",
    "substance_colour": "golden-amber oil",
    "certifications": ["Vegan", "Cruelty Free", "100% Natural"],
}


def _sub(object_id, content, **overrides):
    base = {
        "object_id": object_id, "content": content, "bbox": [0.1, 0.1, 0.2, 0.05],
        "text_purpose": "other", "ownership": "competitor_branded",
        "carries_brand_mark": True, "disposition": "keep",
    }
    base.update(overrides)
    return base


# ---- Helpers ----

def test_known_text_content_strings_collects_across_all_objects():
    objects = [
        {"object_id": "obj_01", "text_content": [_sub("obj_01_txt_01", "Volumizing Conditioner")]},
        {"object_id": "obj_02", "text_content": [_sub("obj_02_txt_01", "90-Day Guarantee")]},
    ]
    assert gip._known_text_content_strings(objects) == {"Volumizing Conditioner", "90-Day Guarantee"}


def test_scrub_known_text_content_removes_and_cleans_whitespace():
    text = "This position held a competitor product Volumizing Conditioner bottle"
    scrubbed = gip._scrub_known_text_content(text, {"Volumizing Conditioner"})
    assert "Volumizing Conditioner" not in scrubbed
    assert "  " not in scrubbed


def test_scrub_known_text_content_longest_first_no_mangled_fragment():
    text = "The Treatment Scalp Serum bottle"
    scrubbed = gip._scrub_known_text_content(text, {"Treatment Scalp", "The Treatment Scalp Serum"})
    assert "Treatment" not in scrubbed
    assert "Serum" not in scrubbed


def test_scrub_known_text_content_noop_when_absent():
    text = "a plain gradient panel"
    assert gip._scrub_known_text_content(text, {"Norse Organics"}) == text


# ---- Site 1: _objects_clause's description ----

def test_objects_clause_scrubs_description_in_keep_line():
    objects = [
        {"object_id": "obj_02", "kind": "graphic", "role": "environment",
         "description": "badge reading 90-Day Guarantee", "ownership": "generic",
         "carries_brand_mark": False, "disposition": "keep",
         "text_content": [_sub("obj_02_txt_01", "90-Day Guarantee")]},
    ]
    clause = gip._objects_clause(objects, {}, ad_id="FIXTURE_badge")
    assert "90-Day Guarantee" not in clause


def test_objects_clause_scrubs_description_in_absent_line():
    objects = [
        {"object_id": "obj_02", "kind": "graphic", "role": "environment",
         "description": "badge reading 90-Day Guarantee", "ownership": "competitor_branded",
         "carries_brand_mark": True, "disposition": "drop",
         "text_content": [_sub("obj_02_txt_01", "90-Day Guarantee", disposition="drop")]},
    ]
    clause = gip._objects_clause(objects, {}, ad_id="FIXTURE_badge_drop")
    assert "90-Day Guarantee" not in clause


def test_objects_clause_description_fallback_never_empty_after_scrub():
    """A description that IS entirely the detected string must not collapse to an
    empty, name-less line - falls back to object_id."""
    objects = [
        {"object_id": "obj_06", "kind": "graphic", "role": "environment",
         "description": "Volumizing Conditioner", "ownership": "generic",
         "carries_brand_mark": False, "disposition": "keep",
         "text_content": [_sub("obj_06_txt_06", "Volumizing Conditioner")]},
    ]
    clause = gip._objects_clause(objects, {}, ad_id="FIXTURE_empty_after_scrub")
    assert "Volumizing Conditioner" not in clause
    assert "obj_06" in clause


# ---- Site 2: _substitute_object_line's appearance (product branch) ----

def test_substitute_object_line_scrubs_appearance_for_product():
    obj = {"object_id": "obj_17", "appearance": "The Treatment Scalp Serum bottle, matte label",
           "bbox": [0.1, 0.1, 0.3, 0.6]}
    known = {"The Treatment Scalp Serum"}
    line = gip._substitute_object_line(
        obj, "product", None, "a product bottle", {}, known_text_content=known,
    )
    assert "The Treatment Scalp Serum" not in line


def test_substitute_object_line_appearance_scrub_falls_back_to_description():
    """If scrubbing empties `appearance` entirely, falls back to (already-scrubbed
    by the caller) `description`, never an empty quoted string."""
    obj = {"object_id": "obj_17", "appearance": "The Treatment Scalp Serum",
           "bbox": [0.1, 0.1, 0.3, 0.6]}
    known = {"The Treatment Scalp Serum"}
    line = gip._substitute_object_line(
        obj, "product", None, "a competitor body serum", {}, known_text_content=known,
    )
    assert "The Treatment Scalp Serum" not in line
    assert "a competitor body serum" in line


def test_substitute_object_line_none_known_text_content_unchanged_behaviour():
    """A caller that doesn't pass known_text_content (e.g. an existing direct test)
    gets byte-identical prior behaviour - no crash, no unexpected scrubbing."""
    obj = {"object_id": "obj_17", "appearance": "a tall glass bottle", "bbox": [0.1, 0.1, 0.3, 0.6]}
    line = gip._substitute_object_line(obj, "product", None, "a bottle", {})
    assert "a tall glass bottle" in line


# ---- Site 3: _object_typography_clause's label ----

def test_object_typography_clause_scrubs_label():
    objects = [
        {"object_id": "obj_17", "kind": "text", "text_purpose": "other",
         "disposition": "keep", "description": "label reading The Treatment Scalp Serum",
         "typography": {"typeface_class": "sans", "weight": "bold", "case": "upper",
                         "letter_spacing": "normal", "colour": "white",
                         "size_relative": "medium", "decorative_elements": [], "line_count": 1},
         "text_content": [_sub("obj_17_txt_02", "The Treatment Scalp Serum")]},
    ]
    clause = gip._object_typography_clause(objects, {})
    assert "The Treatment Scalp Serum" not in clause


# ---- Site 4: generate_copy._object_copy_clause ----

def test_object_copy_clause_scrubs_description_and_persuasive_function():
    objects = [
        {"object_id": "obj_02", "kind": "text", "text_purpose": "other",
         "disposition": "substitute", "role": "secondary", "bbox": [0.1, 0.1, 0.2, 0.1],
         "description": "badge reading 90-Day Guarantee",
         "persuasive_function": "reassures with a 90-Day Guarantee"},
    ]
    all_objects = objects + [
        {"object_id": "obj_99", "text_content": [_sub("obj_99_txt_01", "90-Day Guarantee")]},
    ]
    clause = generate_copy._object_copy_clause(objects, all_objects=all_objects)
    assert "90-Day Guarantee" not in clause


def test_object_copy_clause_no_all_objects_unchanged_behaviour():
    objects = [
        {"object_id": "obj_02", "kind": "text", "text_purpose": "other",
         "disposition": "substitute", "role": "secondary", "bbox": [0.1, 0.1, 0.2, 0.1],
         "description": "a plain badge", "persuasive_function": "adds trust"},
    ]
    clause = generate_copy._object_copy_clause(objects)
    assert "a plain badge" in clause


# ---- Site 5: generate_copy._blueprint_without_bbox also strips text_content ----

def test_blueprint_without_bbox_also_strips_text_content():
    bp = {"objects": [
        {"object_id": "obj_06", "kind": "product", "bbox": [0.1, 0.1, 0.3, 0.6],
         "description": "a competitor product",
         "text_content": [_sub("obj_06_txt_06", "Volumizing Conditioner")]},
    ]}
    stripped = generate_copy._blueprint_without_bbox(bp)
    assert "text_content" not in stripped["objects"][0]
    assert "bbox" not in stripped["objects"][0]
    assert stripped["objects"][0]["description"] == "a competitor product"
    # never mutates the caller's own blueprint
    assert "text_content" in bp["objects"][0]


def test_build_copy_prompt_never_dumps_text_content_json():
    bp = json.loads(json.dumps(OSEA_BLUEPRINT))
    bp["objects"][2]["text_content"] = [_sub("obj_03_txt_01", "Volumizing Conditioner")]
    prompt = generate_copy.build_copy_prompt(bp, product=PRODUCT)
    assert "Volumizing Conditioner" not in prompt
    assert "text_content" not in prompt


# ---- Sites 6-8: Dynamic Edit System instruction builders ----

def _blueprint_with_object(object_id, description, content):
    return {
        "objects": [
            {"object_id": object_id, "kind": "product", "description": description,
             "bbox": [0.1, 0.1, 0.3, 0.6], "ownership": "competitor_branded",
             "carries_brand_mark": True, "disposition": "substitute",
             "text_content": [_sub(f"{object_id}_txt_01", content)]},
        ]
    }


def test_build_object_removal_instruction_scrubs_when_blueprint_given():
    blueprint = _blueprint_with_object("obj_17", "The Treatment Scalp Serum bottle",
                                        "The Treatment Scalp Serum")
    instruction = gip.build_object_removal_instruction(
        "The Treatment Scalp Serum bottle", blueprint=blueprint,
    )
    assert "The Treatment Scalp Serum" not in instruction


def test_build_object_removal_instruction_no_blueprint_unchanged_behaviour():
    instruction = gip.build_object_removal_instruction("a plain bottle")
    assert "a plain bottle" in instruction


def test_build_targeted_edit_instruction_scrubs_object_label_when_blueprint_given():
    blueprint = _blueprint_with_object("obj_06", "Volumizing Conditioner bottle",
                                        "Volumizing Conditioner")
    descriptor = {"target": "object", "attribute": "obj_06",
                  "label": "Volumizing Conditioner bottle",
                  "current_value": "Volumizing Conditioner bottle"}
    instruction = gip.build_targeted_edit_instruction(descriptor, "remove", None, blueprint=blueprint)
    assert "Volumizing Conditioner" not in instruction


def test_build_targeted_edit_instruction_no_blueprint_unchanged_behaviour():
    descriptor = {"target": "headline", "attribute": "headline", "label": "headline",
                  "current_value": "You'll Wish You Went Jumbo"}
    instruction = gip.build_targeted_edit_instruction(descriptor, "change", "New headline")
    assert "headline" in instruction.lower()


def test_build_drift_retry_instruction_scrubs_label_when_blueprint_given():
    blueprint = _blueprint_with_object("obj_02", "90-Day Guarantee badge", "90-Day Guarantee")
    descriptor = {"target": "object", "attribute": "obj_02", "label": "90-Day Guarantee badge"}
    retry = gip.build_drift_retry_instruction("base instruction", descriptor, blueprint=blueprint)
    assert "90-Day Guarantee" not in retry


def test_build_drift_retry_instruction_no_blueprint_unchanged_behaviour():
    descriptor = {"target": "headline", "attribute": "headline", "label": "headline"}
    retry = gip.build_drift_retry_instruction("base instruction", descriptor)
    assert "headline" in retry.lower()


# ---- End-to-end: the exact three reported leak shapes ----

def test_end_to_end_product_label_leak_fixed():
    """1357229623024367 / obj_17_txt_02, 'The Treatment Scalp Serum' - a product
    label leaking via the product branch's appearance/description fallback."""
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    for obj in blueprint["objects"]:
        if obj["object_id"] == "obj_03":
            obj["description"] = "The Treatment Scalp Serum bottle, standard size"
            obj.pop("appearance", None)
            obj["text_content"] = [_sub("obj_03_txt_01", "The Treatment Scalp Serum")]
    prompt = gip.build_image_prompt(
        blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    assert "The Treatment Scalp Serum" not in prompt


def test_end_to_end_badge_leak_fixed():
    """1767532861100741 / obj_02_txt_01, '90-Day Guarantee' - a badge leaking via a
    text_purpose branch's description quoting."""
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    blueprint["objects"].append({
        "object_id": "obj_98", "kind": "text", "text_purpose": "certification",
        "role": "secondary", "colours": ["gold"], "ownership": "competitor_branded",
        "carries_brand_mark": False, "persuasive_function": "reassures the buyer",
        "disposition": "substitute", "bbox": [0.05, 0.9, 0.3, 0.08],
        "description": "badge reading 90-Day Guarantee",
        "text_content": [_sub("obj_98_txt_01", "90-Day Guarantee")],
    })
    prompt = gip.build_image_prompt(
        blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    assert "90-Day Guarantee" not in prompt


def test_end_to_end_second_product_label_leak_fixed():
    """965378629787425 / obj_06_txt_06, 'Volumizing Conditioner' - a second product
    label leaking the same way as the first."""
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    for obj in blueprint["objects"]:
        if obj["object_id"] == "obj_04":
            obj["description"] = "Volumizing Conditioner bottle, jumbo size"
            obj.pop("appearance", None)
            obj["text_content"] = [_sub("obj_04_txt_01", "Volumizing Conditioner")]
    prompt = gip.build_image_prompt(
        blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    assert "Volumizing Conditioner" not in prompt


# ---- The assertion itself is untouched and still fires on a genuine leak ----

def test_assertion_still_fires_when_a_new_unscrubbed_site_is_introduced(monkeypatch):
    """Regression lock proving the assertion was NOT weakened: a hypothetical future
    site that forgets to scrub still gets caught."""
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    blueprint["objects"].append({
        "object_id": "obj_97", "kind": "graphic", "description": "gradient panel",
        "role": "environment", "colours": ["gold"], "ownership": "generic",
        "carries_brand_mark": False, "persuasive_function": "background texture",
        "disposition": "keep",
        "text_content": [_sub("obj_97_txt_01", "Norse Organics")],
    })

    def _leaky_note(obj, context):
        return " LEAKED: Norse Organics"

    monkeypatch.setattr(gip, "_text_content_removal_note", _leaky_note)
    with pytest.raises(gip.TextContentLeakError):
        gip.build_image_prompt(
            blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
        )
