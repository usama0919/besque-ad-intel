"""Phase 2 of the three-voices product-count fix (2026-08-18).

Diagnosis (prior session): rule 7 (_rule7_product_policy) said "exactly one bottle...
NEVER add a second"; SCENE OBJECTS emitted two byte-identical SUBSTITUTE bullets for
two competitor product objects; _edit_mode_instruction said "substitute a Besque item
[for each]". Three voices, two answers, on the real OSEA "You'll Wish You Went Jumbo"
reference (tests/fixtures/blueprints/osea_two_products_both_substitute.json) - the
critic reported neither bottle was ever replaced.

The fix: deconstruct.resolve_product_group_dispositions computes, from the objects
inventory alone (never prose), whether multiple product objects are the SAME product
differing only in size/format (same_product_as - all substitute, matching the
reference's own count) or genuinely DIFFERENT products (exactly one substitutes, the
rest drop). generate_image_prompt.resolve_authorised_product_count derives a single
count from that. Every clause that used to make its own independent claim about
product count - rule 7, _edit_mode_instruction, product_clause's >1 branch,
_substitute_object_line's per-object text - now reads this SAME value.

This file tests: the resolver itself (both product-count scenarios, plus the manual-
drop-respecting case object removal needs); rule 7's count-aware rewrite; per-object
substitute-line differentiation; a generic contradiction guard scanning a BUILT prompt
for internally-consistent bottle-count statements (the regression lock this task
explicitly asked for - it must fail if any future edit reintroduces an unconditional
count assertion); and cross-path invariants for the four ways a prompt gets built
(fresh generate, regenerate, object removal, targeted edit)."""
import json
import re

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


def _product_obj(object_id, **overrides):
    base = {
        "object_id": object_id, "kind": "product", "description": f"{object_id} bottle",
        "bbox": [0.1, 0.1, 0.3, 0.3], "colours": ["amber"], "ownership": "competitor_branded",
        "role": "secondary", "carries_brand_mark": True, "persuasive_function": "sold item",
        "disposition": "substitute",
    }
    base.update(overrides)
    return base


# ---- deconstruct.resolve_product_group_dispositions ----

def test_single_competitor_product_substitutes():
    objects = [_product_obj("obj_01")]
    assert deconstruct.resolve_product_group_dispositions(objects) == {"obj_01": "substitute"}


def test_same_product_two_instances_via_same_product_as_both_substitute():
    objects = [
        _product_obj("obj_01"),
        _product_obj("obj_02", same_product_as="obj_01"),
    ]
    result = deconstruct.resolve_product_group_dispositions(objects)
    assert result == {"obj_01": "substitute", "obj_02": "substitute"}


def test_same_product_three_instances_all_substitute():
    objects = [
        _product_obj("obj_01"),
        _product_obj("obj_02", same_product_as="obj_01"),
        _product_obj("obj_03", same_product_as="obj_01"),
    ]
    result = deconstruct.resolve_product_group_dispositions(objects)
    assert result == {"obj_01": "substitute", "obj_02": "substitute", "obj_03": "substitute"}


def test_different_products_hero_wins_other_drops():
    objects = [
        _product_obj("obj_01", role="secondary"),
        _product_obj("obj_02", role="hero"),
    ]
    result = deconstruct.resolve_product_group_dispositions(objects)
    assert result == {"obj_01": "drop", "obj_02": "substitute"}


def test_different_products_no_hero_first_listed_wins():
    objects = [
        _product_obj("obj_01", role="secondary"),
        _product_obj("obj_02", role="secondary"),
    ]
    result = deconstruct.resolve_product_group_dispositions(objects)
    assert result == {"obj_01": "substitute", "obj_02": "drop"}


def test_manually_forced_drop_excluded_from_grouping_and_count():
    # Same shape as generate_image_prompt.blueprint_with_object_dropped's output - an
    # operator's object-removal edit forces one product's disposition to "drop" ahead
    # of this function ever running. That object must not be re-admitted into a group
    # or counted, and must not affect the survivor's own resolution.
    objects = [
        _product_obj("obj_01"),
        _product_obj("obj_02", same_product_as="obj_01", disposition="drop"),
    ]
    result = deconstruct.resolve_product_group_dispositions(objects)
    assert result == {"obj_01": "substitute"}


def test_non_branded_product_excluded_generic_besque_ownership():
    objects = [_product_obj("obj_01", ownership="generic", carries_brand_mark=False)]
    assert deconstruct.resolve_product_group_dispositions(objects) == {}


def test_empty_when_no_objects():
    assert deconstruct.resolve_product_group_dispositions([]) == {}
    assert deconstruct.resolve_product_group_dispositions(None) == {}


def test_real_osea_fixture_both_instances_substitute():
    result = deconstruct.resolve_product_group_dispositions(OSEA_BLUEPRINT["objects"])
    assert result == {"obj_03": "substitute", "obj_04": "substitute"}


# ---- generate_image_prompt.resolve_authorised_product_count ----

def test_authorised_count_matches_group_disposition_survivor_count():
    objects = [
        _product_obj("obj_01"),
        _product_obj("obj_02", same_product_as="obj_01"),
    ]
    assert gip.resolve_authorised_product_count(objects) == 2


def test_authorised_count_none_when_no_branded_products_to_reason_about():
    assert gip.resolve_authorised_product_count([]) is None
    assert gip.resolve_authorised_product_count(None) is None


def test_authorised_count_real_osea_fixture_is_two():
    assert gip.resolve_authorised_product_count(OSEA_BLUEPRINT["objects"]) == 2


# ---- _rule7_product_policy: count-aware, verbatim-preserving for the common case ----

def test_rule7_default_count_preserves_original_wording_verbatim():
    text = gip._rule7_product_policy(include_product=True, authorised_product_count=1)
    assert "exactly one bottle, and it is that one" in text
    assert "NEVER add a second bottle" in text


def test_rule7_multi_instance_count_states_the_resolved_number_not_unconditional_one():
    text = gip._rule7_product_policy(include_product=True, authorised_product_count=2)
    assert "exactly 2 Besque bottles are authorised" in text
    assert "NEVER add a second bottle" not in text


def test_rule7_productless_mode_ignores_count():
    text = gip._rule7_product_policy(include_product=False, authorised_product_count=2)
    assert "PRODUCTLESS MODE" in text
    assert "Besque bottles are authorised" not in text


def test_brand_rules_default_still_reproduces_original_rule7_verbatim():
    # Regression lock for the pre-existing test_brand_rules_default_reproduces_prior_
    # rules_verbatim contract (tests/test_generate_image_prompt.py) - brand_rules()
    # called with no authorised_product_count argument must still default to 1 and
    # produce byte-identical rule 7 text to before this fix.
    text = gip.brand_rules()
    assert "exactly one bottle, and it is that one" in text
    assert "NEVER add a second bottle" in text


# ---- _substitute_object_line: per-object differentiation ----

def test_substitute_object_line_two_instances_are_not_byte_identical():
    obj_a = _product_obj("obj_01", description="standard-size bottle, on the left",
                          bbox=[0.05, 0.28, 0.42, 0.68])
    obj_b = _product_obj("obj_02", description="jumbo-size bottle, on the right",
                          bbox=[0.35, 0.32, 0.52, 0.65])
    line_a = gip._substitute_object_line(obj_a, "product", None, obj_a["description"], {},
                                          product_instance_count=2)
    line_b = gip._substitute_object_line(obj_b, "product", None, obj_b["description"], {},
                                          product_instance_count=2)
    assert line_a != line_b
    assert "standard-size" in line_a and "jumbo-size" not in line_a
    assert "jumbo-size" in line_b and "standard-size" not in line_b
    assert "[0.05, 0.28, 0.42, 0.68]" in line_a
    assert "[0.35, 0.32, 0.52, 0.65]" in line_b


def test_substitute_object_line_single_product_no_instance_language():
    obj = _product_obj("obj_01", description="the only product")
    line = gip._substitute_object_line(obj, "product", None, obj["description"], {},
                                        product_instance_count=1)
    assert "instance" not in line.lower()
    assert "the only product" in line


# ---- Contradiction guard: scan a BUILT prompt for internally-consistent bottle counts ----

_BOTTLE_COUNT_PATTERN = re.compile(
    r"exactly (\d+|one) (?:Besque )?(?:item|bottle)s?", re.IGNORECASE
)


def _extract_bottle_counts(prompt):
    counts = []
    for match in _BOTTLE_COUNT_PATTERN.finditer(prompt):
        raw = match.group(1).lower()
        counts.append(1 if raw == "one" else int(raw))
    return counts


def _assert_no_contradiction(prompt, expected_count):
    """The regression lock: every 'exactly N bottle(s)' statement anywhere in a built
    prompt must agree with the code-computed authorised count, and the OLD
    unconditional "never a second bottle" phrasing must never coexist with a resolved
    count above 1. Fails loudly if a future edit reintroduces either a stray
    unconditional assertion or a differently-worded, disagreeing count statement -
    this is deliberately a scan over the ASSEMBLED prompt, not a check on any one
    clause function in isolation, so it catches a disagreement between ANY two
    sections, not just the three specific ones this session fixed."""
    counts = _extract_bottle_counts(prompt)
    assert counts, "no explicit bottle-count statement found in the prompt at all"
    assert all(c == expected_count for c in counts), (
        f"prompt makes inconsistent bottle-count claims: {counts} (expected every "
        f"one to equal {expected_count})"
    )
    if expected_count > 1:
        assert "NEVER add a second bottle" not in prompt, (
            "prompt states an unconditional 'never a second bottle' rule while also "
            f"authorising {expected_count} bottles elsewhere - this is exactly the "
            "three-voices contradiction this fix exists to prevent"
        )


def test_no_contradiction_osea_same_product_two_instances():
    prompt = gip.build_image_prompt(
        OSEA_BLUEPRINT, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    authorised = gip.resolve_authorised_product_count(OSEA_BLUEPRINT["objects"])
    assert authorised == 2
    _assert_no_contradiction(prompt, authorised)
    # The two product SUBSTITUTE bullets must no longer be byte-identical (item 3).
    substitute_bullets = re.findall(r"\(\d+\) SUBSTITUTE: this position held[^(]*", prompt)
    product_bullets = [b for b in substitute_bullets if "competitor product" in b]
    assert len(product_bullets) == 2
    assert product_bullets[0] != product_bullets[1]


def test_no_contradiction_two_different_products_one_wins():
    different = json.loads(json.dumps(OSEA_BLUEPRINT))
    for obj in different["objects"]:
        if obj.get("object_id") in ("obj_03", "obj_04"):
            obj.pop("same_product_as", None)
    prompt = gip.build_image_prompt(
        different, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    authorised = gip.resolve_authorised_product_count(different["objects"])
    assert authorised == 1
    _assert_no_contradiction(prompt, authorised)
    assert "NEVER add a second bottle" in prompt
    assert "ABSENT: the OSEA Undaria Algae Body Oil standard-size" in prompt


def test_no_contradiction_ordinary_single_product_reference(monkeypatch=None):
    single = json.loads(json.dumps(OSEA_BLUEPRINT))
    single["objects"] = [
        obj for obj in single["objects"] if obj.get("object_id") != "obj_04"
    ]
    single["layout_detail"]["product_count"] = 1
    prompt = gip.build_image_prompt(
        single, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    authorised = gip.resolve_authorised_product_count(single["objects"])
    assert authorised == 1
    _assert_no_contradiction(prompt, authorised)


# ---- Cross-path invariants (item 5): every path that builds a prompt honours the
# SAME computed count, with no extra wiring needed at the call site. ----

def test_fresh_generate_path_honours_computed_count():
    # generate_image (the fresh-generate caller) never passes product_count either -
    # this IS that call shape (edit_mode=True, no product_count override).
    prompt = gip.build_image_prompt(
        OSEA_BLUEPRINT, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    _assert_no_contradiction(prompt, 2)


def test_regenerate_path_honours_computed_count_with_no_extra_wiring():
    # Mirrors pipeline.py's _regenerate_existing_draft call to build_image_prompt
    # (src/pipeline.py ~line 825) - it never passes product_count, relying entirely on
    # the object-driven resolution to kick in automatically. If a future edit ever
    # threads a stale product_count through the regenerate path, this test's assertion
    # would catch the resulting disagreement, not just a missing kwarg.
    prompt = gip.build_image_prompt(
        OSEA_BLUEPRINT, product=PRODUCT, include_product=True,
        text_in_image=False, headline=None, subtext=None, edit_mode=True,
        offer_text=None, operator_instruction="", retheme_colours=True,
        brand_palette=None, realism=None, cta_text=None, panel_copy=None,
        testimonial=None, clone_mode=False, object_copy=None,
    )
    _assert_no_contradiction(prompt, 2)


def test_object_removal_path_reduces_authorised_count_and_stays_consistent():
    # Mirrors dashboard.py's object-removal edit call to build_image_prompt
    # (target == "object" branch) - blueprint_with_object_dropped forces one product's
    # disposition to "drop" in a COPY of the blueprint before the prompt is rebuilt.
    modified, target_object = gip.blueprint_with_object_dropped(OSEA_BLUEPRINT, "obj_04")
    assert target_object is not None
    authorised = gip.resolve_authorised_product_count(modified["objects"])
    assert authorised == 1, "removing one of two same-product instances must reduce the count"
    prompt = gip.build_image_prompt(
        modified, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    _assert_no_contradiction(prompt, authorised)
    assert "NEVER add a second bottle" in prompt


def test_targeted_edit_path_never_states_a_bottle_count():
    # The delta-instruction-only edit path (build_targeted_edit_instruction /
    # apply_targeted_edit) never rebuilds rule 7 / SCENE OBJECTS / _edit_mode_
    # instruction at all - it is structurally exempt from this contradiction class,
    # never a candidate for the guard above, since it makes no count claim to
    # disagree with anything else in the first place.
    descriptor = {"target": "headline", "attribute": "headline", "label": "headline",
                  "current_value": "You'll Wish You Went Jumbo"}
    instruction = gip.build_targeted_edit_instruction(descriptor, "change", "New headline")
    # "bottle" legitimately appears here (it's in the fixed preservation-terms list,
    # _PRESERVATION_TERMS) - what must never appear is a COUNT claim about it.
    assert not _BOTTLE_COUNT_PATTERN.search(instruction)
    assert "PRODUCT POLICY" not in instruction
    assert "authorised" not in instruction.lower()


def test_object_removal_instruction_template_never_states_a_bottle_count():
    # "bottle" legitimately appears here (it's part of the removed object's own
    # description) - what must never appear is a COUNT claim about how many remain.
    instruction = gip.build_object_removal_instruction("jumbo-size pump bottle")
    assert not _BOTTLE_COUNT_PATTERN.search(instruction)
    assert "PRODUCT POLICY" not in instruction
    assert "authorised" not in instruction.lower()
