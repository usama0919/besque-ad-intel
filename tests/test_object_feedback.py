"""Structured object-level feedback (2026-08-19) - persist only, no learning layer.
dedupe.object_feedback: a new, additive table + record_object_feedback/get_object_feedback,
wired into two existing endpoints so "which object_id and kind was wrong, and why" can be
recorded instead of only a whole-image reject/reason. Dashboard-endpoint tests mirror
tests/test_edit_engine_endpoints.py's/test_outcome_backfill.py's mocking style - every
dedupe/DB touchpoint monkeypatched, no real Postgres connection."""
import uuid
import dashboard
from fastapi.testclient import TestClient
from src import dedupe, generate_image_prompt, drift_check


def _client():
    return TestClient(dashboard.app)


def _artifact(**overrides):
    base = {
        "id": 42, "ad_id": "AD123", "page_name": "Besque", "draft_image": "AD123_draft.png",
        "generated_copy": {"headline": "H", "primary_text": "P", "image_subtext": "S", "cta": "Shop Now"},
        "offer_text": "Free shipping over £40",
        "angle_id": None, "text_in_image": True, "metadata": {}, "image_prompt": "orig prompt",
        "copy_prompt": "", "model_info": "", "format_flag": "", "product_override_note": "",
        "include_product": True, "retheme_colours": True, "realism": "ugc_native",
        "body_area": "legs", "product_id": 1, "element_provenance": {},
        "critic_findings": [], "review_status": "ok",
        "parent_artifact_id": None, "root_artifact_id": 42, "version_no": 1, "edit_event_id": None,
        "blueprint": {
            "format": "hero", "face_present": {"has_face": False}, "layout_detail": {},
            "objects": [
                {"object_id": "obj_02", "kind": "prop", "description": "a wooden tray",
                 "bbox": [0.25, 0.25, 0.25, 0.25], "colours": [], "ownership": "generic",
                 "role": "environment", "carries_brand_mark": False,
                 "persuasive_function": "staging", "disposition": "keep"},
            ],
        },
    }
    base.update(overrides)
    return base


def _object_removal_dedupe_mocks(monkeypatch):
    monkeypatch.setattr(dedupe, "get_product", lambda pid: {"id": pid, "name": "Besque Magic Body Oil"})
    monkeypatch.setattr(dedupe, "get_brand_settings", lambda: {"palette": "terracotta"})


# ---- POST /artifact/{id}/edit: operator-supplied reason ----

def test_edit_with_operator_reason_records_object_feedback(monkeypatch):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 9)
    monkeypatch.setattr(dedupe, "update_edit_event_result", lambda *a, **k: None)
    monkeypatch.setattr(dedupe, "insert_edit_artifact", lambda **k: 55)
    monkeypatch.setattr(dedupe, "get_angle", lambda aid: None)
    monkeypatch.setattr(dashboard, "_read_artifact_image_bytes",
                        lambda art, ad_id: (b"draft-bytes", "AD123_draft.png"))
    _object_removal_dedupe_mocks(monkeypatch)
    monkeypatch.setattr(generate_image_prompt, "_regenerate_image_bytes", lambda *a, **k: b"new-bytes")
    monkeypatch.setattr(drift_check, "check_drift", lambda *a, **k: {
        "method": "removal_zone", "checked": True, "drift_flag": False,
        "inside_pct": 5.0, "outside_pct": 0.1, "scatter_pct": None, "bbox": (0, 0, 1, 1)})
    feedback_calls = []
    monkeypatch.setattr(dedupe, "record_object_feedback",
                        lambda **k: feedback_calls.append(k) or 1)

    resp = _client().post("/artifact/42/edit", json={
        "target": "object", "attribute": "obj_02", "operation": "remove",
        "reason": "this tray isn't in the Besque brand palette",
    })
    assert resp.status_code == 200
    assert len(feedback_calls) == 1
    assert feedback_calls[0]["artifact_id"] == 42
    assert feedback_calls[0]["ad_id"] == "AD123"
    assert feedback_calls[0]["object_id"] == "obj_02"
    assert feedback_calls[0]["kind"] == "prop"
    assert feedback_calls[0]["reason"] == "this tray isn't in the Besque brand palette"
    assert feedback_calls[0]["source"] == "edit"


def test_edit_without_reason_records_no_object_feedback_on_success(monkeypatch):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 9)
    monkeypatch.setattr(dedupe, "update_edit_event_result", lambda *a, **k: None)
    monkeypatch.setattr(dedupe, "insert_edit_artifact", lambda **k: 55)
    monkeypatch.setattr(dedupe, "get_angle", lambda aid: None)
    monkeypatch.setattr(dashboard, "_read_artifact_image_bytes",
                        lambda art, ad_id: (b"draft-bytes", "AD123_draft.png"))
    _object_removal_dedupe_mocks(monkeypatch)
    monkeypatch.setattr(generate_image_prompt, "_regenerate_image_bytes", lambda *a, **k: b"new-bytes")
    monkeypatch.setattr(drift_check, "check_drift", lambda *a, **k: {
        "method": "removal_zone", "checked": True, "drift_flag": False,
        "inside_pct": 5.0, "outside_pct": 0.1, "scatter_pct": None, "bbox": (0, 0, 1, 1)})
    feedback_calls = []
    monkeypatch.setattr(dedupe, "record_object_feedback",
                        lambda **k: feedback_calls.append(k) or 1)

    resp = _client().post("/artifact/42/edit", json={
        "target": "object", "attribute": "obj_02", "operation": "remove",
    })
    assert resp.status_code == 200
    assert feedback_calls == []


def test_non_object_target_never_records_object_feedback_even_with_reason(monkeypatch):
    """Control: a reason field on a non-"object" target (e.g. cta text) is meaningless
    here - object_feedback is scoped strictly to target=="object". find_control is
    mocked directly (like the mechanical-rejection test below) so this exercises only
    the object_feedback wiring, not real control-derivation eligibility - the real
    blueprint here has no cta-purposed text object, so edit_capability's own real
    _cta_control would return None regardless of this test's own intent."""
    from src import edit_capability, generate_copy, compliance
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact(
        blueprint={"format": "hero", "objects": [], "layout_detail": {"product_count": 1}},
    ))
    monkeypatch.setattr(edit_capability, "find_control", lambda controls, target, attribute: {
        "target": "cta", "attribute": "text", "label": "CTA",
        "current_value": "Shop Now", "allowed_ops": ["change"],
        "blueprint_path": "objects[].text_purpose==cta + generated_copy.cta",
    } if target == "cta" else None)
    monkeypatch.setattr(compliance, "check_compliance", lambda *a, **k: (True, []))
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 9)
    monkeypatch.setattr(dedupe, "update_edit_event_result", lambda *a, **k: None)
    monkeypatch.setattr(dedupe, "insert_edit_artifact", lambda **k: 55)
    monkeypatch.setattr(dedupe, "get_angle", lambda aid: None)
    monkeypatch.setattr(dashboard, "_read_artifact_image_bytes",
                        lambda art, ad_id: (b"draft-bytes", "AD123_draft.png"))
    monkeypatch.setattr(generate_image_prompt, "apply_targeted_edit", lambda *a, **k: b"new-bytes")
    monkeypatch.setattr(drift_check, "check_drift", lambda *a, **k: {
        "method": "skip", "checked": False, "drift_flag": False,
        "inside_pct": None, "outside_pct": None, "scatter_pct": None, "bbox": None})
    feedback_calls = []
    monkeypatch.setattr(dedupe, "record_object_feedback",
                        lambda **k: feedback_calls.append(k) or 1)

    resp = _client().post("/artifact/42/edit", json={
        "target": "cta", "attribute": "text", "operation": "change", "new_value": "Buy Now",
        "reason": "wrong wording",
    })
    assert resp.status_code == 200
    assert feedback_calls == []


# ---- POST /artifact/{id}/edit: mechanical rejection also records object_feedback ----

def test_mechanical_rejection_records_object_feedback_with_reject_reason(monkeypatch):
    """Exercises the EARLY rejection path (_log_rejected, before insert_edit_event
    creates the pending row): find_control genuinely returns None for an attribute
    with no matching control, the real "no editable control" branch this codebase
    already had - not mocked to fabricate a descriptor that then hits a different
    rejection path (see the "object not found in blueprint" test below, which covers
    the LATER path via _log_rejected_after_pending instead)."""
    from src import edit_capability
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    monkeypatch.setattr(edit_capability, "find_control", lambda controls, target, attribute: None)
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 9)
    feedback_calls = []
    monkeypatch.setattr(dedupe, "record_object_feedback",
                        lambda **k: feedback_calls.append(k) or 1)

    resp = _client().post("/artifact/42/edit", json={
        "target": "object", "attribute": "obj_missing", "operation": "remove",
    })
    assert resp.status_code == 400
    assert len(feedback_calls) == 1
    assert feedback_calls[0]["object_id"] == "obj_missing"
    assert feedback_calls[0]["source"] == "edit_reject"
    assert "no editable control" in feedback_calls[0]["reason"]
    # obj_missing isn't in the stored blueprint's objects[] at all - kind resolves to
    # None rather than guessing, never invented.
    assert feedback_calls[0]["kind"] is None


def test_object_not_found_in_blueprint_records_object_feedback_via_later_path(monkeypatch):
    """Covers _log_rejected_after_pending specifically: find_control DOES find a
    control (the object-remove control is derived from edit_capability's own real
    controls, which don't check the attribute matches an actual object), but the
    object itself is missing from the blueprint by the time blueprint_with_object_
    dropped runs - a rejection that happens AFTER insert_edit_event already created
    the pending row, so it must go through the later helper, not _log_rejected."""
    from src import edit_capability
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    monkeypatch.setattr(edit_capability, "find_control", lambda controls, target, attribute: {
        "target": "object", "attribute": attribute, "label": "a ghost object",
        "current_value": "a ghost object", "allowed_ops": ["remove"],
        "blueprint_path": "objects[].object_id",
    } if target == "object" else None)
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 9)
    monkeypatch.setattr(dedupe, "update_edit_event_result", lambda *a, **k: None)
    monkeypatch.setattr(dashboard, "_read_artifact_image_bytes",
                        lambda art, ad_id: (b"draft-bytes", "AD123_draft.png"))
    feedback_calls = []
    monkeypatch.setattr(dedupe, "record_object_feedback",
                        lambda **k: feedback_calls.append(k) or 1)

    resp = _client().post("/artifact/42/edit", json={
        "target": "object", "attribute": "obj_missing", "operation": "remove",
    })
    assert resp.status_code == 400
    assert len(feedback_calls) == 1
    assert feedback_calls[0]["object_id"] == "obj_missing"
    assert feedback_calls[0]["source"] == "edit_reject"
    assert "not found in this artifact's blueprint" in feedback_calls[0]["reason"]
    assert feedback_calls[0]["kind"] is None


def test_mechanical_rejection_and_operator_reason_both_recorded_independently(monkeypatch):
    """A request can both supply an operator reason AND fail mechanical validation -
    both get recorded, as two distinct rows with distinct source labels, never
    conflated into one."""
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 9)
    feedback_calls = []
    monkeypatch.setattr(dedupe, "record_object_feedback",
                        lambda **k: feedback_calls.append(k) or 1)

    # operation "remove" is allowed for the object control per edit_capability's own
    # derivation, but new_value is irrelevant for remove - force a rejection via an
    # operation the control doesn't allow instead, to exercise _log_rejected cleanly.
    resp = _client().post("/artifact/42/edit", json={
        "target": "object", "attribute": "obj_02", "operation": "change", "new_value": "x",
        "reason": "this looked wrong to me",
    })
    assert resp.status_code == 400
    sources = sorted(c["source"] for c in feedback_calls)
    assert sources == ["edit", "edit_reject"]


# ---- POST /api/decision/{ad_id}/{decision}: optional object_id ----

def test_reject_with_object_id_records_object_feedback(monkeypatch):
    monkeypatch.setattr(dedupe, "record_decision", lambda *a, **k: None)
    monkeypatch.setattr(dedupe, "get_artifact", lambda ad_id, angle_id=None: _artifact())
    feedback_calls = []
    monkeypatch.setattr(dedupe, "record_object_feedback",
                        lambda **k: feedback_calls.append(k) or 1)

    resp = _client().post(
        "/api/decision/AD123/reject",
        params={"reason": "wrong prop", "object_id": "obj_02"},
    )
    assert resp.status_code == 200
    assert len(feedback_calls) == 1
    assert feedback_calls[0]["ad_id"] == "AD123"
    assert feedback_calls[0]["object_id"] == "obj_02"
    assert feedback_calls[0]["kind"] == "prop"
    assert feedback_calls[0]["reason"] == "wrong prop"
    assert feedback_calls[0]["source"] == "operator_reject"


def test_reject_without_object_id_records_no_object_feedback(monkeypatch):
    """Control: today's every existing reject call (no object_id param) is completely
    unaffected - zero new rows, matching pre-existing behaviour exactly."""
    monkeypatch.setattr(dedupe, "record_decision", lambda *a, **k: None)
    monkeypatch.setattr(dedupe, "get_artifact", lambda ad_id, angle_id=None: _artifact())
    feedback_calls = []
    monkeypatch.setattr(dedupe, "record_object_feedback",
                        lambda **k: feedback_calls.append(k) or 1)

    resp = _client().post("/api/decision/AD123/reject", params={"reason": "not great"})
    assert resp.status_code == 200
    assert feedback_calls == []


def test_approve_with_object_id_records_no_object_feedback():
    """object_id is only meaningful on a reject, not an approve - even if supplied,
    an approve must never write a "this was wrong" row."""
    client = _client()
    import unittest.mock
    with unittest.mock.patch.object(dedupe, "record_decision"), \
         unittest.mock.patch.object(dedupe, "get_artifact", return_value=_artifact()), \
         unittest.mock.patch.object(dedupe, "record_object_feedback") as mock_feedback:
        resp = client.post("/api/decision/AD123/approve", params={"object_id": "obj_02"})
        assert resp.status_code == 200
        mock_feedback.assert_not_called()


# ---- dedupe layer: real DB round-trip (same standing port-5433 limitation as every
# other DB-backed test in this suite - see CLAUDE.md) ----

def test_record_and_get_object_feedback_round_trip():
    dedupe.init_object_feedback()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    try:
        new_id = dedupe.record_object_feedback(
            artifact_id=None, ad_id=ad_id, object_id="obj_02", kind="prop",
            reason="test reason", source="operator_reject",
        )
        assert isinstance(new_id, int)
        rows = dedupe.get_object_feedback(ad_id=ad_id)
        assert len(rows) == 1
        assert rows[0]["object_id"] == "obj_02"
        assert rows[0]["kind"] == "prop"
        assert rows[0]["reason"] == "test reason"
        assert rows[0]["source"] == "operator_reject"
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM object_feedback WHERE ad_id = %s", (ad_id,))
            conn.commit()
