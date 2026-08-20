"""Generalised product-count fix (2026-08-20), following the tin+lid double-bottle
bug on artifact 1400 (ad_id 1188618079298343): a competitor tin and its own detached
lid were both marked kind=="product"/same_product_as, so deconstruct.resolve_
product_group_dispositions correctly (by its own rules) resolved BOTH to "substitute"
- rule 7 then authorised two Besque bottles for what the reference actually showed as
one product plus its lid. Fixed the CLASS, not the instance:

Part A (schema/deconstruct.py) - a new `part_of` field gives deconstruct a field for
"this is a component of another object", distinct from same_product_as ("this is a
separate instance of the same product").

Part B (generate_image_prompt.resolve_authorised_product_dispositions) - the single,
defensive derivation: part_of objects never count; a geometric backstop collapses any
group whose "substitute" members have conflicting bboxes even when same_product_as
wrongly links them; a ceiling clamps the derived count to whatever was actually
specified for the run. Every clamp/collapse records a pipeline_warnings row.

Part C (deconstruct.resolve_disposition) - a part_of component inherits its parent's
disposition, forced to "drop" once the parent substitutes - applied at both
resolution points (deconstruct time and _objects_clause), per the dual-resolution
design documented on the objects-array refactor.

Part D - single source of truth (_objects_clause routed through resolve_authorised_
product_dispositions instead of calling deconstruct.resolve_product_group_
dispositions directly) and a runtime assertion (_assert_product_count_consistent)
that raises if rule 7/product_clause/_edit_mode_instruction/_objects_clause ever
state disagreeing counts in the same built prompt.

Does NOT touch deconstruct.resolve_product_group_dispositions, generate_image_prompt.
_composite_gate, or any bottle-drawing site - those are out of scope (the composite-
path suppression gap is a separate, already-catalogued issue)."""
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


def _product_obj(object_id, **overrides):
    base = {
        "object_id": object_id, "kind": "product", "description": f"{object_id} bottle",
        "bbox": [0.1, 0.1, 0.3, 0.3], "colours": ["amber"], "ownership": "competitor_branded",
        "role": "secondary", "carries_brand_mark": True, "persuasive_function": "sold item",
        "disposition": "substitute",
    }
    base.update(overrides)
    return base


def _mock_warnings(monkeypatch):
    from src import dedupe
    captured = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning",
                         lambda kind, detail: captured.append((kind, detail)))
    return captured


# ---- Item 1: tin+lid via part_of -> count 1 ----

def test_tin_and_lid_via_part_of_collapses_to_one(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    objects = [
        _product_obj("obj_04", role="hero", bbox=[0.45, 0.42, 0.52, 0.52],
                     description="tin, open, showing balm inside"),
        _product_obj("obj_05", role="secondary", bbox=[0.72, 0.48, 0.25, 0.4],
                     part_of="obj_04", description="detached lid leaning against the tin"),
    ]
    assert gip.resolve_authorised_product_count(objects) == 1
    dispositions = gip.resolve_authorised_product_dispositions(objects)
    assert dispositions == {"obj_04": "substitute", "obj_05": "drop"}
    # part_of is a clean exclusion, not a conflict needing correction - no warning.
    assert warnings == []


def test_tin_and_lid_via_part_of_end_to_end_scene_objects_never_states_two_bottles():
    """The real bug shape, fixed: build_image_prompt (via _objects_clause, now routed
    through the single resolve_authorised_product_dispositions source) must never
    emit a second SUBSTITUTE line for the lid."""
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    for obj in blueprint["objects"]:
        if obj["object_id"] == "obj_04":
            obj.pop("same_product_as", None)
            obj["part_of"] = "obj_03"
    prompt = gip.build_image_prompt(
        blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    authorised = gip.resolve_authorised_product_count(blueprint["objects"])
    assert authorised == 1
    substitute_bullets = [
        line for line in prompt.split(")") if "SUBSTITUTE: this position held" in line
        and "competitor product" in line
    ]
    assert len(substitute_bullets) == 1
    assert "exactly 1 Besque bottle" in prompt.lower() or "exactly one bottle" in prompt.lower()


# ---- Item 2: geometric backstop - conflicting bboxes override a (wrong) same_product_as link ----

def test_overlapping_bboxes_collapse_even_when_same_product_as_links_them(monkeypatch):
    """The tin+lid bug BEFORE part_of existed: deconstruct had only same_product_as
    available, used it (incorrectly, since a lid is a component, not a separate
    instance), and resolve_product_group_dispositions correctly-by-its-own-rules
    returned both as substitute. The geometric backstop is the safety net for
    exactly this case - it does not depend on part_of ever being set at all."""
    warnings = _mock_warnings(monkeypatch)
    objects = [
        _product_obj("obj_04", role="hero", bbox=[0.45, 0.42, 0.52, 0.52]),
        _product_obj("obj_05", role="secondary", bbox=[0.72, 0.48, 0.25, 0.4],
                     same_product_as="obj_04"),
    ]
    # Confirm the premise: without the geometric backstop, this WOULD be 2.
    assert deconstruct.resolve_product_group_dispositions(objects) == {
        "obj_04": "substitute", "obj_05": "substitute",
    }
    assert gip.resolve_authorised_product_count(objects) == 1
    assert len(warnings) == 1
    kind, detail = warnings[0]
    assert kind == "product_count_corrected"
    assert "geometric_conflict" in detail


def test_no_relation_fields_and_overlapping_bboxes_still_yields_one_no_new_warning(monkeypatch):
    """Literal 'no same_product_as, no part_of' case: two unlinked branded product
    objects are already treated as genuinely DIFFERENT products by the pre-existing
    resolve_product_group_dispositions tiebreak (hero wins), which already collapses
    to exactly one substitute before the geometric backstop ever gets a chance to
    run - so this is safe by an EARLIER mechanism, not the new one, and records no
    geometric-conflict warning. Included to confirm the new code never regresses
    this already-safe case, not because it exercises the backstop itself."""
    warnings = _mock_warnings(monkeypatch)
    objects = [
        _product_obj("obj_a", role="secondary", bbox=[0.45, 0.42, 0.52, 0.52]),
        _product_obj("obj_b", role="hero", bbox=[0.72, 0.48, 0.25, 0.4]),
    ]
    assert gip.resolve_authorised_product_count(objects) == 1
    assert warnings == []


# ---- Item 3: ceiling clamp ----

def test_derived_count_above_ceiling_is_clamped_and_warned(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    objects = [
        _product_obj("obj_01", role="hero", bbox=[0.02, 0.1, 0.2, 0.6]),
        _product_obj("obj_02", role="secondary", bbox=[0.30, 0.1, 0.2, 0.6],
                     same_product_as="obj_01"),
        _product_obj("obj_03", role="secondary", bbox=[0.58, 0.1, 0.2, 0.6],
                     same_product_as="obj_01"),
    ]
    # Premise: three genuinely separate, non-conflicting instances would derive 3.
    assert gip.resolve_authorised_product_count(objects) == 3
    clamped = gip.resolve_authorised_product_count(
        objects, operator_product_count=None, layout_product_count=1,
    )
    assert clamped == 1
    assert len(warnings) == 1
    kind, detail = warnings[0]
    assert kind == "product_count_corrected"
    assert "ceiling_exceeded" in detail
    # The hero-ranked object must be the one kept.
    dispositions = gip.resolve_authorised_product_dispositions(
        objects, layout_product_count=1,
    )
    assert dispositions["obj_01"] == "substitute"
    assert dispositions["obj_02"] == "drop"
    assert dispositions["obj_03"] == "drop"


def test_operator_override_ceiling_wins_over_layout_count():
    objects = [
        _product_obj("obj_01", role="hero", bbox=[0.02, 0.1, 0.2, 0.6]),
        _product_obj("obj_02", role="secondary", bbox=[0.30, 0.1, 0.2, 0.6],
                     same_product_as="obj_01"),
    ]
    # No ceiling at all - both survive.
    assert gip.resolve_authorised_product_count(objects) == 2
    # Operator explicitly says 1, even though layout says something looser (or nothing).
    assert gip.resolve_authorised_product_count(
        objects, operator_product_count=1, layout_product_count=5,
    ) == 1


# ---- Item 4: genuine multi-instance, non-overlapping bboxes -> N preserved ----

def test_real_osea_fixture_genuine_two_instances_preserved():
    # The real OSEA reference's own bboxes overlap ~27% relative to the smaller box -
    # well under the 0.7 containment-ratio threshold - proving the backstop doesn't
    # collapse a legitimate side-by-side multi-instance reference.
    assert gip.resolve_authorised_product_count(OSEA_BLUEPRINT["objects"]) == 2


def test_synthetic_two_instances_clearly_separated_preserved(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    objects = [
        _product_obj("obj_01", role="hero", bbox=[0.02, 0.1, 0.3, 0.7]),
        _product_obj("obj_02", role="secondary", bbox=[0.5, 0.1, 0.3, 0.7],
                     same_product_as="obj_01"),
    ]
    assert gip.resolve_authorised_product_count(objects) == 2
    dispositions = gip.resolve_authorised_product_dispositions(objects)
    assert dispositions == {"obj_01": "substitute", "obj_02": "substitute"}
    assert warnings == []


# ---- Item 5: component with substituting parent resolves to drop, both resolution points ----

def test_resolve_disposition_part_of_substituting_parent_forces_drop():
    lid = _product_obj("obj_05", part_of="obj_04")
    assert deconstruct.resolve_disposition(
        lid, part_of_parent_disposition="substitute") == "drop"


def test_resolve_disposition_part_of_no_parent_info_defaults_drop_never_substitute():
    lid = _product_obj("obj_05", part_of="obj_04")
    assert deconstruct.resolve_disposition(lid) == "drop"


def test_resolve_disposition_part_of_inherits_keep_or_drop_from_parent():
    lid = _product_obj("obj_05", part_of="obj_04")
    assert deconstruct.resolve_disposition(lid, part_of_parent_disposition="keep") == "keep"
    assert deconstruct.resolve_disposition(lid, part_of_parent_disposition="drop") == "drop"


def test_deconstruct_time_resolution_forces_component_to_drop():
    blueprint = {
        "objects": [
            _product_obj("obj_04"),
            _product_obj("obj_05", part_of="obj_04"),
        ]
    }
    resolved = deconstruct._resolve_object_dispositions(blueprint)
    by_id = {o["object_id"]: o["disposition"] for o in resolved["objects"]}
    assert by_id == {"obj_04": "substitute", "obj_05": "drop"}


def test_objects_clause_never_emits_substitute_for_a_part_of_component():
    objects = [
        _product_obj("obj_04", role="hero"),
        _product_obj("obj_05", part_of="obj_04", role="secondary",
                     description="detached lid"),
    ]
    clause = gip._objects_clause(objects, {}, ad_id="FIXTURE_part_of")
    assert "SUBSTITUTE: this position held a competitor product" in clause
    assert clause.count("SUBSTITUTE: this position held a competitor product") == 1
    assert "detached lid" in clause  # named in the ABSENT line, not silently omitted
    assert "ABSENT: the detached lid" in clause


# ---- Item 6: suppress_bottle_identity=True together with count > 1 (previously uncovered) ----

def test_suppress_bottle_identity_true_with_multi_instance_count_no_contradiction():
    """Previously zero coverage combined suppress_bottle_identity=True with
    authorised_product_count>1 - this exact combination is what the D2 composite-gate
    widening (46005f5) made reachable for the first time. The known, separate,
    out-of-scope issue is that rule 7/product_clause's placement text isn't gated on
    suppress_bottle_identity (an instruction-suppression gap) - this test asserts
    only what IS in scope here: the NUMBER stated everywhere stays consistent, so
    _assert_product_count_consistent does not raise for this real combination."""
    prompt = gip.build_image_prompt(
        OSEA_BLUEPRINT, product=PRODUCT, include_product=True, edit_mode=True,
        realism=None, suppress_bottle_identity=True,
    )
    authorised = gip.resolve_authorised_product_count(OSEA_BLUEPRINT["objects"])
    assert authorised == 2
    # Compositing-aware text must still appear (identity/geometry suppressed)...
    assert "COMPOSITING" in prompt
    # ...while the count itself is still stated consistently everywhere.
    assert "exactly 2 Besque bottles are authorised" in prompt


# ---- Item 7: the prompt-build assertion fires when counts diverge ----

def test_assert_product_count_consistent_passes_when_all_counts_agree():
    prompt = "exactly 2 Besque bottles are authorised. Exactly 2 Besque bottle(s) belong here."
    gip._assert_product_count_consistent(prompt, 2, ad_id="FIXTURE_ok")  # must not raise


def test_assert_product_count_consistent_raises_on_divergent_counts():
    prompt = "exactly 2 Besque bottles are authorised. Exactly 1 Besque bottle(s) belong here."
    with pytest.raises(gip.ProductCountConsistencyError):
        gip._assert_product_count_consistent(prompt, 2, ad_id="FIXTURE_bad")


def test_assert_product_count_consistent_noop_when_no_count_statement_present():
    gip._assert_product_count_consistent("no count language here at all", 1)  # must not raise


def test_build_image_prompt_raises_when_a_site_states_a_different_count(monkeypatch):
    """Integration-level proof the assertion is actually wired into build_image_prompt:
    force _rule7_product_policy to lie about the count, confirm the mismatch is
    caught rather than silently shipped - this is the exact 17 Aug three-voices
    divergence shape, reproduced deliberately."""
    real_rule7 = gip._rule7_product_policy

    def _lying_rule7(include_product=True, authorised_product_count=1):
        return real_rule7(include_product=include_product, authorised_product_count=99)

    monkeypatch.setattr(gip, "_rule7_product_policy", _lying_rule7)
    with pytest.raises(gip.ProductCountConsistencyError):
        gip.build_image_prompt(
            OSEA_BLUEPRINT, product=PRODUCT, include_product=True, edit_mode=True,
            realism=None,
        )


def test_build_image_prompt_does_not_raise_for_the_ordinary_single_product_case():
    single = json.loads(json.dumps(OSEA_BLUEPRINT))
    single["objects"] = [o for o in single["objects"] if o.get("object_id") != "obj_04"]
    single["layout_detail"]["product_count"] = 1
    gip.build_image_prompt(
        single, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )  # must not raise
