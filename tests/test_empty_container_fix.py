"""Empty-container fix (2026-08-20). Live case: ad 1693447485074085 rendered a
five-item numbered list where four items were empty numerals - steps 1, 2, 3, 5
dropped, step 4 substituted, and the numbering scaffolding survived because it
belongs to the parent object, which resolved to "keep" independently of what
happened to its own text_content.

Fixed: when a "keep" object's text_content is non-empty and resolves ENTIRELY to
"drop", the object itself is force-dropped too, rather than surviving as a
container with nothing left inside it. Applied at both resolution points -
deconstruct time (deconstruct._resolve_object_dispositions) and generation time
(generate_image_prompt._objects_clause, re-resolved fresh against real run
context) - consistent with the dual-resolution design already established for
every other context-gated field on this schema. Every occurrence records a
pipeline_warnings row."""
import json

from src import deconstruct, generate_image_prompt as gip
from tests.blueprint_fixtures import load_blueprint_fixture

OSEA_BLUEPRINT = load_blueprint_fixture("osea_two_products_both_substitute")

PRODUCT = {
    "name": "Magic Body Oil",
    "visual_description": "a tall cylindrical bottle with a gold collar and black pump",
    "substance_colour": "golden-amber oil",
    "certifications": ["Vegan", "Cruelty Free", "100% Natural"],
}


def _sub(object_id, content="Step text", **overrides):
    base = {
        "object_id": object_id, "content": content, "bbox": [0.1, 0.1, 0.2, 0.05],
        "text_purpose": "other", "ownership": "competitor_branded",
        "carries_brand_mark": True, "disposition": "drop",
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


def _numbered_item(object_id, sub_content, sub_disposition="drop", parent_disposition="keep"):
    return {
        "object_id": object_id, "kind": "graphic", "role": "secondary",
        "description": f"numbered list item {object_id}", "ownership": "generic",
        "carries_brand_mark": False, "disposition": parent_disposition,
        "text_content": [_sub(f"{object_id}_txt_01", sub_content, disposition=sub_disposition)],
    }


# ---- Resolution point 1: deconstruct time ----

def test_deconstruct_time_forces_drop_when_all_sub_objects_drop(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    blueprint = {"ad_id": "AD1", "objects": [
        _numbered_item("obj_01", "Wet hair thoroughly."),
    ]}
    resolved = deconstruct._resolve_object_dispositions(blueprint)
    assert resolved["objects"][0]["disposition"] == "drop"
    assert len(warnings) == 1
    kind, detail = warnings[0]
    assert kind == "empty_container_dropped"
    assert "obj_01" in detail
    assert "AD1" in detail


def test_deconstruct_time_does_not_force_drop_when_one_sub_object_survives(monkeypatch):
    """Step 4's own shape: the container's ONE text_content entry substitutes
    (real context or a non-branded purpose), so the container is not empty."""
    warnings = _mock_warnings(monkeypatch)
    blueprint = {"ad_id": "AD1", "objects": [
        {**_numbered_item("obj_04", "90 seconds, then rinse."),
         "text_content": [_sub("obj_04_txt_01", "90 seconds, then rinse.",
                                disposition="keep", ownership="generic", carries_brand_mark=False)]},
    ]}
    resolved = deconstruct._resolve_object_dispositions(blueprint)
    assert resolved["objects"][0]["disposition"] == "keep"
    assert warnings == []


def test_deconstruct_time_empty_text_content_never_forces_drop(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    blueprint = {"ad_id": "AD1", "objects": [
        {"object_id": "obj_09", "kind": "graphic", "role": "secondary",
         "description": "a plain graphic", "ownership": "generic",
         "carries_brand_mark": False, "disposition": "keep"},
    ]}
    resolved = deconstruct._resolve_object_dispositions(blueprint)
    assert resolved["objects"][0]["disposition"] == "keep"
    assert warnings == []


def test_deconstruct_time_object_already_substitute_unaffected(monkeypatch):
    """The rule only fires on disposition=='keep' - an object that independently
    resolves to 'substitute' (e.g. a branded product) must never be forced to
    'drop' just because its own baked-in text all dropped."""
    warnings = _mock_warnings(monkeypatch)
    blueprint = {"ad_id": "AD1", "objects": [
        {"object_id": "obj_10", "kind": "product", "role": "hero",
         "description": "a competitor bottle", "ownership": "competitor_branded",
         "carries_brand_mark": True, "disposition": "keep",
         "text_content": [_sub("obj_10_txt_01", "Competitor Brand Name")]},
    ]}
    resolved = deconstruct._resolve_object_dispositions(blueprint)
    assert resolved["objects"][0]["disposition"] == "substitute"
    assert warnings == []


# ---- Resolution point 2: generation time (_objects_clause) ----

def test_objects_clause_emits_absent_line_not_keep_when_all_sub_objects_drop(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    objects = [_numbered_item("obj_01", "Wet hair thoroughly.")]
    clause = gip._objects_clause(objects, {}, ad_id="FIXTURE_numbered_list")
    assert "ABSENT: the numbered list item obj_01" in clause
    assert "KEEP: the numbered list item obj_01" not in clause
    assert len(warnings) == 1
    assert warnings[0][0] == "empty_container_dropped_at_generation"


def test_objects_clause_keeps_container_when_context_resolves_one_slot():
    """Dual-resolution: a context-gated sub-object purpose stored 'drop' at
    deconstruct time can resolve 'substitute' here once real context exists -
    un-emptying the container at the SECOND resolution point specifically."""
    objects = [{
        "object_id": "obj_02", "kind": "graphic", "role": "secondary",
        "description": "an offer badge", "ownership": "generic",
        "carries_brand_mark": False, "disposition": "keep",
        "text_content": [_sub("obj_02_txt_01", "20% OFF", text_purpose="offer",
                               ownership="generic", carries_brand_mark=False)],
    }]
    clause_no_context = gip._objects_clause(objects, {}, ad_id="FIXTURE_offer_no_ctx")
    assert "ABSENT: the an offer badge" in clause_no_context or "ABSENT" in clause_no_context
    clause_with_offer = gip._objects_clause(
        objects, {"offer_text": "15% OFF TODAY"}, ad_id="FIXTURE_offer_with_ctx")
    assert "KEEP" in clause_with_offer


def test_objects_clause_does_not_force_drop_when_step_survives():
    objects = [
        _numbered_item("obj_01", "Wet hair thoroughly."),
        _numbered_item("obj_02", "Apply generously."),
        _numbered_item("obj_03", "Massage gently."),
        {**_numbered_item("obj_04", "90 seconds, then rinse."),
         "text_content": [_sub("obj_04_txt_01", "90 seconds, then rinse.",
                                disposition="keep", ownership="generic", carries_brand_mark=False)]},
        _numbered_item("obj_05", "Use regularly."),
    ]
    clause = gip._objects_clause(objects, {}, ad_id="FIXTURE_five_steps")
    assert clause.count("ABSENT: the numbered list item") == 4
    assert "KEEP: numbered list item obj_04" in clause


# ---- End-to-end via build_image_prompt ----

def test_build_image_prompt_end_to_end_empty_container_dropped(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    blueprint["objects"].append(_numbered_item("obj_90", "Wet hair thoroughly."))
    prompt = gip.build_image_prompt(
        blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    assert "ABSENT: the numbered list item obj_90" in prompt
    assert any(kind == "empty_container_dropped_at_generation" for kind, _ in warnings)


# ---- Part C extension (2026-08-20, text layer completion): serves_object_id-linked
# text, not nested as a text_content child - the real "empty pink sticky note" shape,
# ad 1746884313351902. The sticky itself (obj_05, kind=="graphic") had NO text_content
# at all; its own offer text was a SEPARATE top-level object (obj_06, kind=="text",
# text_purpose="offer") naming the sticky via serves_object_id. The original
# text_content-only version of the empty-container rule never looked at this shape at
# all, which is why the deconstruct-time fix alone did not close the live bug - the
# sticky survived as "keep" with nothing left inside it.

def _sticky_note_shape(offer_disposition="drop", sticky_disposition="keep"):
    sticky = {
        "object_id": "obj_05", "kind": "graphic", "role": "secondary",
        "description": "a pink sticky note", "ownership": "generic",
        "carries_brand_mark": False, "disposition": sticky_disposition,
    }
    offer_text = {
        "object_id": "obj_06", "kind": "text", "text_purpose": "offer",
        "description": "20% OFF this week only", "ownership": "generic",
        "carries_brand_mark": False, "serves_object_id": "obj_05",
        "disposition": offer_disposition,
    }
    return [sticky, offer_text]


def test_deconstruct_time_drops_container_served_by_text_with_no_offer_context(monkeypatch):
    """No run-specific offer_text exists yet at deconstruct time, so the offer
    object resolves to 'drop' (context-gated, nothing supplied) - the sticky it
    serves has no OTHER content, so it must force-drop too, never survive as a
    container with nothing left inside it."""
    warnings = _mock_warnings(monkeypatch)
    blueprint = {"ad_id": "AD_STICKY", "objects": _sticky_note_shape()}
    resolved = deconstruct._resolve_object_dispositions(blueprint)
    by_id = {o["object_id"]: o["disposition"] for o in resolved["objects"]}
    assert by_id["obj_06"] == "drop"
    assert by_id["obj_05"] == "drop"
    kind, detail = next((k, d) for k, d in warnings if k == "empty_container_dropped")
    assert "obj_05" in detail


def test_objects_clause_drops_container_served_by_text_when_offer_context_supplied():
    """Dual-resolution: once this run actually supplies an offer_text, the offer
    object resolves 'substitute' at generation time, and the sticky it serves
    survives - un-emptying the container at the SECOND resolution point, the same
    contract the nested text_content case already proves."""
    objects = _sticky_note_shape()
    clause_no_offer = gip._objects_clause(objects, {}, ad_id="AD_STICKY_NO_OFFER")
    assert "ABSENT: the a pink sticky note" in clause_no_offer
    clause_with_offer = gip._objects_clause(
        objects, {"offer_text": "15% OFF TODAY"}, ad_id="AD_STICKY_WITH_OFFER")
    assert "KEEP: a pink sticky note" in clause_with_offer


def test_objects_clause_records_generation_time_empty_container_warning_for_served_text(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    objects = _sticky_note_shape()
    gip._objects_clause(objects, {}, ad_id="AD_STICKY")
    kinds = [k for k, _ in warnings]
    assert "empty_container_dropped_at_generation" in kinds


def test_build_image_prompt_end_to_end_sticky_note_shape_dropped(monkeypatch):
    warnings = _mock_warnings(monkeypatch)
    blueprint = json.loads(json.dumps(OSEA_BLUEPRINT))
    blueprint["objects"].extend(_sticky_note_shape())
    prompt = gip.build_image_prompt(
        blueprint, product=PRODUCT, include_product=True, edit_mode=True, realism=None,
    )
    assert "ABSENT: the a pink sticky note" in prompt
    assert any(kind == "empty_container_dropped_at_generation" for kind, _ in warnings)
