"""Substance-material fix (2026-08-21): a kept prop whose own material is a styled
visual stand-in for the competitor's product substance (a cream swirl, a gel smear
used as flat-lay set-dressing) must be reconciled against products.substance_colour
rather than cloned verbatim. Live case: a competitor flat-lay showed the product
resting on a white cream swirl; the Besque draft reproduced that cream swirl verbatim
under a golden-amber oil bottle.

Data-driven via a new optional blueprint field, objects[].represents_product_substance
(schema/blueprint.schema.json), set by deconstruct.py's own judgement at deconstruct
time - never a keyword match on "cream"/"oil"/"Besque" anywhere in the generation-side
code. generate_image_prompt._objects_clause's existing KEEP branch is extended, not
replaced or duplicated by a second mechanism."""
import json

from src import generate_image_prompt as gip
from tests.blueprint_fixtures import load_blueprint_fixture

OSEA_BLUEPRINT = load_blueprint_fixture("osea_two_products_both_substitute")

PRODUCT = {
    "name": "Magic Body Oil",
    "description": "A luxury fragrant blend of 7 cold-pressed oils.",
    "substance_colour": "bright golden-amber oil",
}


def _substance_prop(represents=True, object_id="obj_swirl"):
    return {
        "object_id": object_id, "kind": "prop", "role": "supporting_prop",
        "description": "swirl of white cream on a marble surface",
        "disposition": "keep", "represents_product_substance": represents,
    }


# ---- _objects_clause: the KEEP branch reconciles a flagged prop against substance_colour ----

def test_objects_clause_recolours_a_flagged_substance_prop():
    objects = [_substance_prop()]
    context = {"substance_colour": "bright golden-amber oil"}
    clause = gip._objects_clause(objects, context, ad_id="TEST")
    assert "KEEP, RECOLOURED" in clause
    assert "bright golden-amber oil" in clause
    assert "visual stand-in for the competitor's product substance" in clause
    # must not ALSO carry the plain "reproduce exactly as shown, unchanged" KEEP wording
    assert "reproduce exactly as shown, unchanged" not in clause


def test_objects_clause_recolour_falls_back_when_substance_colour_unset():
    objects = [_substance_prop()]
    clause = gip._objects_clause(objects, {}, ad_id="TEST")
    assert "KEEP, RECOLOURED" in clause
    assert "our product's actual colour and texture" in clause


def test_objects_clause_ordinary_kept_prop_unaffected():
    """A ordinary kept prop (no represents_product_substance) must be byte-for-byte
    unaffected - the plain KEEP line, never the recolour treatment."""
    objects = [{"object_id": "obj_towel", "kind": "prop", "role": "supporting_prop",
                "description": "folded white towel", "disposition": "keep"}]
    clause = gip._objects_clause(objects, {"substance_colour": "bright golden-amber oil"}, ad_id="TEST")
    assert "KEEP: folded white towel" in clause
    assert "reproduce exactly as shown, unchanged" in clause
    assert "RECOLOURED" not in clause


def test_objects_clause_represents_product_substance_false_is_ordinary_keep():
    objects = [_substance_prop(represents=False)]
    clause = gip._objects_clause(objects, {"substance_colour": "bright golden-amber oil"}, ad_id="TEST")
    assert "RECOLOURED" not in clause
    assert "reproduce exactly as shown, unchanged" in clause


def test_objects_clause_generic_not_hardcoded_to_cream_or_oil():
    """A completely different product substance value produces the identical
    mechanism with a different word - proves the fix is data-driven, not a
    cream/oil-specific special case."""
    objects = [_substance_prop()]
    clause = gip._objects_clause(objects, {"substance_colour": "deep emerald green gel"}, ad_id="TEST")
    assert "deep emerald green gel" in clause
    assert "KEEP, RECOLOURED" in clause


# ---- End-to-end: reaches the assembled prompt ----

def test_build_image_prompt_recolours_substance_prop_end_to_end():
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    blueprint["objects"].append(_substance_prop(object_id="obj_new_swirl"))
    prompt = gip.build_image_prompt(
        blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    assert "KEEP, RECOLOURED" in prompt
    assert "bright golden-amber oil" in prompt


def test_build_image_prompt_ordinary_props_unaffected_end_to_end():
    """No object flags represents_product_substance anywhere in this real fixture -
    the prompt must contain no RECOLOURED lines at all."""
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    prompt = gip.build_image_prompt(
        blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    assert "RECOLOURED" not in prompt
