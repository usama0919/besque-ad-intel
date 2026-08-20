"""Text layer completion, Part B (2026-08-20): claim label and evidence resolve
together.

Live case: ad 1357229623024367 split each competitor bullet into a label
("Clinically Proven") and an evidence sub-line ("95% saw results by week 6").
Evidence substituted (a stat-shaped efficacy claim, correctly caught by the
pre-existing check), label kept - the competitor's own claim survived while only
its proof was replaced.

The CURRENT real stored blueprint for that ad no longer has this literal split
(deconstruct is non-deterministic across runs - a re-run can classify the same
reference differently), so these tests use a SYNTHETIC fixture reproducing the
exact shape: two text objects (a label, an evidence line) both naming the same
serves_object_id (an icon/badge they're both attached to) - "siblings under one
bullet," per _text_object_link_groups's own docstring."""
from src import deconstruct


def _label_evidence_pair(label_disposition="keep", evidence_disposition="drop",
                          served_id="obj_icon_01"):
    label = {
        "object_id": "obj_label", "kind": "text", "description": "Clinically Proven",
        "text_purpose": "other", "ownership": "generic", "carries_brand_mark": False,
        "serves_object_id": served_id, "disposition": label_disposition,
    }
    evidence = {
        "object_id": "obj_evidence", "kind": "text",
        "description": "95% saw results by week 6",
        "text_purpose": "other", "ownership": "generic", "carries_brand_mark": False,
        "serves_object_id": served_id, "disposition": evidence_disposition,
    }
    icon = {
        "object_id": served_id, "kind": "graphic", "role": "secondary",
        "description": "a checkmark icon", "ownership": "generic",
        "carries_brand_mark": False, "disposition": "keep",
    }
    return [label, icon, evidence]


# ---- _text_object_link_groups ----

def test_link_groups_finds_siblings_sharing_serves_object_id():
    objects = _label_evidence_pair()
    groups = deconstruct._text_object_link_groups(objects)
    assert len(groups) == 1
    assert set(groups[0]) == {"obj_label", "obj_evidence"}


def test_link_groups_excludes_the_served_non_text_object_itself():
    """The icon being served is the thing LABELLED, not a second claim needing
    alignment - only kind=='text' objects are ever grouped."""
    objects = _label_evidence_pair()
    groups = deconstruct._text_object_link_groups(objects)
    all_grouped = {oid for group in groups for oid in group}
    assert "obj_icon_01" not in all_grouped


def test_link_groups_direct_serves_object_id_between_two_text_objects():
    objects = [
        {"object_id": "obj_a", "kind": "text", "text_purpose": "other"},
        {"object_id": "obj_b", "kind": "text", "text_purpose": "other",
         "serves_object_id": "obj_a"},
    ]
    groups = deconstruct._text_object_link_groups(objects)
    assert groups == [["obj_a", "obj_b"]] or set(groups[0]) == {"obj_a", "obj_b"}


def test_link_groups_shared_part_of_target():
    objects = [
        {"object_id": "obj_a", "kind": "text", "text_purpose": "other", "part_of": "obj_x"},
        {"object_id": "obj_b", "kind": "text", "text_purpose": "other", "part_of": "obj_x"},
    ]
    groups = deconstruct._text_object_link_groups(objects)
    assert len(groups) == 1
    assert set(groups[0]) == {"obj_a", "obj_b"}


def test_link_groups_lone_unlinked_text_object_never_grouped():
    objects = [{"object_id": "obj_a", "kind": "text", "text_purpose": "headline"}]
    assert deconstruct._text_object_link_groups(objects) == []


def test_link_groups_no_false_grouping_across_unrelated_objects():
    objects = [
        {"object_id": "obj_a", "kind": "text", "text_purpose": "headline"},
        {"object_id": "obj_b", "kind": "text", "text_purpose": "subtext"},
    ]
    assert deconstruct._text_object_link_groups(objects) == []


# ---- align_linked_text_dispositions ----

def test_align_realigns_disagreeing_group_to_strictest_value():
    objects = _label_evidence_pair(label_disposition="keep", evidence_disposition="drop")
    dispositions = {"obj_label": "keep", "obj_icon_01": "keep", "obj_evidence": "drop"}
    updated, changed = deconstruct.align_linked_text_dispositions(objects, dispositions)
    assert updated["obj_label"] == "drop"
    assert updated["obj_evidence"] == "drop"
    assert updated["obj_icon_01"] == "keep"  # never grouped, untouched
    assert len(changed) == 1
    group_ids, resolved_value = changed[0]
    assert set(group_ids) == {"obj_label", "obj_evidence"}
    assert resolved_value == "drop"


def test_align_drop_beats_substitute_beats_keep():
    objects = [
        {"object_id": "a", "kind": "text", "text_purpose": "other", "serves_object_id": "x"},
        {"object_id": "b", "kind": "text", "text_purpose": "other", "serves_object_id": "x"},
        {"object_id": "c", "kind": "text", "text_purpose": "other", "serves_object_id": "x"},
    ]
    dispositions = {"a": "keep", "b": "substitute", "c": "drop"}
    updated, changed = deconstruct.align_linked_text_dispositions(objects, dispositions)
    assert updated == {"a": "drop", "b": "drop", "c": "drop"}
    assert len(changed) == 1


def test_align_already_unanimous_group_untouched_and_not_reported():
    objects = _label_evidence_pair(label_disposition="drop", evidence_disposition="drop")
    dispositions = {"obj_label": "drop", "obj_icon_01": "keep", "obj_evidence": "drop"}
    updated, changed = deconstruct.align_linked_text_dispositions(objects, dispositions)
    assert updated == dispositions
    assert changed == []


def test_align_never_mutates_input_dict():
    objects = _label_evidence_pair()
    original = {"obj_label": "keep", "obj_icon_01": "keep", "obj_evidence": "drop"}
    original_copy = dict(original)
    deconstruct.align_linked_text_dispositions(objects, original)
    assert original == original_copy


# ---- End-to-end: deconstruct-time resolution (_resolve_object_dispositions) ----

def _mock_warnings(monkeypatch):
    from src import dedupe
    captured = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning",
                         lambda kind, detail: captured.append((kind, detail)))
    return captured


def test_deconstruct_time_aligns_label_kept_when_evidence_drops(monkeypatch):
    """The live shape: a stat-shaped evidence line drops on its own merits (the
    restored stat-claim check), an unbranded/no-purpose label would otherwise
    survive as 'keep' - Part B forces it to align to 'drop' too."""
    warnings = _mock_warnings(monkeypatch)
    blueprint = {"ad_id": "AD1", "objects": [
        {"object_id": "obj_label", "kind": "text", "description": "See The Difference",
         "text_purpose": "other", "ownership": "generic", "carries_brand_mark": False,
         "serves_object_id": "obj_icon_01", "disposition": "keep"},
        {"object_id": "obj_icon_01", "kind": "graphic", "role": "secondary",
         "description": "a checkmark icon", "ownership": "generic",
         "carries_brand_mark": False, "disposition": "keep"},
        {"object_id": "obj_evidence", "kind": "text",
         "description": "95% saw results by week 6",
         "text_purpose": "other", "ownership": "generic", "carries_brand_mark": False,
         "serves_object_id": "obj_icon_01", "disposition": "keep"},
    ]}
    resolved = deconstruct._resolve_object_dispositions(blueprint)
    by_id = {o["object_id"]: o["disposition"] for o in resolved["objects"]}
    assert by_id["obj_evidence"] == "drop"  # stat-shaped, drops on its own
    assert by_id["obj_label"] == "drop"  # aligned to match its linked sibling
    assert by_id["obj_icon_01"] == "keep"  # never part of the text-only group
    kinds = [k for k, _ in warnings]
    assert "linked_text_disposition_aligned" in kinds
    detail = next(d for k, d in warnings if k == "linked_text_disposition_aligned")
    assert "obj_label" in detail and "obj_evidence" in detail


def test_deconstruct_time_no_warning_when_group_already_agrees(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    blueprint = {"ad_id": "AD1", "objects": [
        {"object_id": "obj_a", "kind": "text", "text_purpose": "headline",
         "description": "Great Skin Awaits", "ownership": "generic",
         "carries_brand_mark": False, "serves_object_id": "obj_x", "disposition": "keep"},
        {"object_id": "obj_b", "kind": "text", "text_purpose": "subtext",
         "description": "Try it today", "ownership": "generic",
         "carries_brand_mark": False, "serves_object_id": "obj_x", "disposition": "keep"},
        {"object_id": "obj_x", "kind": "graphic", "role": "secondary",
         "description": "a banner", "ownership": "generic",
         "carries_brand_mark": False, "disposition": "keep"},
    ]}
    deconstruct._resolve_object_dispositions(blueprint)
    assert not any(k == "linked_text_disposition_aligned" for k, _ in warnings)


# ---- End-to-end: generation-time resolution (_objects_clause) ----

def test_objects_clause_aligns_label_and_evidence_to_drop():
    from src import generate_image_prompt as gip
    objects = [
        {"object_id": "obj_label", "kind": "text", "description": "See The Difference",
         "text_purpose": "other", "ownership": "generic", "carries_brand_mark": False,
         "serves_object_id": "obj_icon_01", "disposition": "keep"},
        {"object_id": "obj_icon_01", "kind": "graphic", "role": "secondary",
         "description": "a checkmark icon", "ownership": "generic",
         "carries_brand_mark": False, "disposition": "keep"},
        {"object_id": "obj_evidence", "kind": "text",
         "description": "95% saw results by week 6",
         "text_purpose": "other", "ownership": "generic", "carries_brand_mark": False,
         "serves_object_id": "obj_icon_01", "disposition": "keep"},
    ]
    clause = gip._objects_clause(objects, {}, ad_id="FIXTURE_label_evidence")
    assert "ABSENT: the See The Difference" in clause
    assert "KEEP: See The Difference" not in clause
    assert "ABSENT: the 95% saw results" in clause


def test_objects_clause_records_linked_alignment_warning(monkeypatch):
    from src import generate_image_prompt as gip
    warnings = _mock_warnings(monkeypatch)
    objects = [
        {"object_id": "obj_label", "kind": "text", "description": "See The Difference",
         "text_purpose": "other", "ownership": "generic", "carries_brand_mark": False,
         "serves_object_id": "obj_icon_01", "disposition": "keep"},
        {"object_id": "obj_icon_01", "kind": "graphic", "role": "secondary",
         "description": "a checkmark icon", "ownership": "generic",
         "carries_brand_mark": False, "disposition": "keep"},
        {"object_id": "obj_evidence", "kind": "text",
         "description": "95% saw results by week 6",
         "text_purpose": "other", "ownership": "generic", "carries_brand_mark": False,
         "serves_object_id": "obj_icon_01", "disposition": "keep"},
    ]
    gip._objects_clause(objects, {}, ad_id="AD1")
    kinds = [k for k, _ in warnings]
    assert "linked_text_disposition_aligned" in kinds


def test_build_image_prompt_end_to_end_label_evidence_never_leaks(monkeypatch):
    monkeypatch.setattr("src.dedupe.init_pipeline_warnings", lambda: None)
    monkeypatch.setattr("src.dedupe.record_warning", lambda kind, detail: None)
    from src import generate_image_prompt as gip
    from tests.blueprint_fixtures import load_blueprint_fixture
    blueprint = load_blueprint_fixture("osea_two_products_both_substitute")
    blueprint = dict(blueprint)
    blueprint["objects"] = list(blueprint["objects"]) + _label_evidence_pair(
        label_disposition="keep", evidence_disposition="keep",
    )
    product = {
        "name": "Magic Body Oil",
        "visual_description": "a tall cylindrical bottle with a gold collar and black pump",
        "substance_colour": "golden-amber oil",
        "certifications": ["Vegan", "Cruelty Free", "100% Natural"],
    }
    prompt = gip.build_image_prompt(
        blueprint, product=product, include_product=True, edit_mode=True, realism=None,
    )
    assert "KEEP: Clinically Proven" not in prompt
    assert "KEEP: 95% saw results" not in prompt
