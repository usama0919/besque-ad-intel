"""Application-area fix (2026-08-21): products.application_area is a new column
recording what body area(s)/use context a product is actually formulated for (e.g.
"bums, tums, thighs & underarms") - previously nothing recorded this at all, despite
the fact already existing as printed label copy inside visual_description. Live case:
a body oil for bums/tums/thighs/underarms was placed in a face-skincare context by a
draft that followed a reference showing that use.

Data-driven: the constraint reads whatever THIS product's own application_area row
says, never a hardcoded body area - a different value produces a different stated
constraint automatically."""
import uuid

from src import dedupe, generate_image_prompt as gip
from tests.blueprint_fixtures import load_blueprint_fixture

OSEA_BLUEPRINT = load_blueprint_fixture("osea_two_products_both_substitute")


def _make_product(**kw):
    dedupe.init_products()
    name = f"__test_{uuid.uuid4().hex[:8]}__"
    return dedupe.add_product(name, **kw)


# ---- dedupe.py: the column round-trips through add_product/get_product ----

def test_application_area_round_trips_through_add_and_get_product():
    pid = _make_product(application_area="bums, tums, thighs & underarms")
    try:
        assert dedupe.get_product(pid)["application_area"] == "bums, tums, thighs & underarms"
    finally:
        dedupe.delete_product(pid)


def test_application_area_defaults_to_empty_string():
    pid = _make_product()
    try:
        assert dedupe.get_product(pid)["application_area"] == ""
    finally:
        dedupe.delete_product(pid)


def test_update_product_sets_application_area():
    pid = _make_product()
    try:
        dedupe.update_product(pid, "renamed", "d", "i", "h", application_area="face and neck")
        assert dedupe.get_product(pid)["application_area"] == "face and neck"
    finally:
        dedupe.delete_product(pid)


# ---- generate_image_prompt.py: surfaced as a constraint in the built prompt ----

def test_build_image_prompt_states_application_area_constraint():
    product = {"name": "Magic Body Oil", "description": "A luxury oil blend.",
               "application_area": "bums, tums, thighs & underarms"}
    prompt = gip.build_image_prompt(
        OSEA_BLUEPRINT, product=product, include_product=True, edit_mode=True, realism=None,
    )
    assert "formulated for use on: bums, tums, thighs & underarms" in prompt
    assert "never depict or imply application to a body area or use context that contradicts it" in prompt


def test_build_image_prompt_omits_constraint_when_application_area_unset():
    """Every pre-existing product row (application_area="" or absent) must be
    byte-for-byte unaffected - no empty/guessed constraint sentence."""
    product = {"name": "Magic Body Oil", "description": "A luxury oil blend."}
    prompt = gip.build_image_prompt(
        OSEA_BLUEPRINT, product=product, include_product=True, edit_mode=True, realism=None,
    )
    assert "formulated for use on:" not in prompt


def test_build_image_prompt_application_area_constraint_is_data_driven():
    """A completely different application area produces the identical mechanism with
    different words - proves this isn't hardcoded to "bums, tums, thighs & underarms"
    specifically."""
    product = {"name": "Face Serum", "description": "A serum.", "application_area": "face and neck only"}
    prompt = gip.build_image_prompt(
        OSEA_BLUEPRINT, product=product, include_product=True, edit_mode=True, realism=None,
    )
    assert "formulated for use on: face and neck only" in prompt
