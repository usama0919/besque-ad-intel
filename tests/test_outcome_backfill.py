"""Tests for the Dynamic Edit System's outcome backfill (2026-08-14): approve/reject
attaches a judgment to the SPECIFIC edit_events row that produced the artifact version
being judged (via result_artifact_id), and a new edit supersedes its source's own
still-pending edit_event. Every dedupe/DB touchpoint is monkeypatched - no real
Postgres connection, no network, no spend. Mirrors tests/test_edit_engine_endpoints.py's
mocking style."""
import dashboard
from fastapi.testclient import TestClient
from src import dedupe, generate_image_prompt, drift_check


def _client():
    return TestClient(dashboard.app)


# ---- api_decision: approve/reject attaches outcome to the CURRENT version's edit_event ----

def test_approve_sets_approved_on_the_current_versions_edit_event(monkeypatch):
    monkeypatch.setattr(dedupe, "record_decision", lambda *a, **k: None)
    monkeypatch.setattr(dedupe, "get_artifact", lambda ad_id, angle_id=None: {"edit_event_id": 77})
    calls = []
    monkeypatch.setattr(dedupe, "set_edit_event_outcome", lambda eid, outcome: calls.append((eid, outcome)))

    resp = _client().post("/api/decision/AD123/approve")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert calls == [(77, "approved")]


def test_reject_sets_rejected_on_the_current_versions_edit_event(monkeypatch):
    monkeypatch.setattr(dedupe, "record_decision", lambda *a, **k: None)
    monkeypatch.setattr(dedupe, "get_artifact", lambda ad_id, angle_id=None: {"edit_event_id": 88})
    calls = []
    monkeypatch.setattr(dedupe, "set_edit_event_outcome", lambda eid, outcome: calls.append((eid, outcome)))

    resp = _client().post("/api/decision/AD123/reject")
    assert resp.status_code == 200
    assert calls == [(88, "rejected")]


def test_approve_v1_with_no_edit_event_is_a_noop_not_an_error(monkeypatch):
    monkeypatch.setattr(dedupe, "record_decision", lambda *a, **k: None)
    # v1 row - never produced by an edit, edit_event_id is NULL.
    monkeypatch.setattr(dedupe, "get_artifact", lambda ad_id, angle_id=None: {"edit_event_id": None})
    calls = []
    monkeypatch.setattr(dedupe, "set_edit_event_outcome", lambda eid, outcome: calls.append((eid, outcome)))

    resp = _client().post("/api/decision/AD123/approve")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True  # the ad-level decision still succeeds
    assert calls == []  # but nothing was written to edit_events - a no-op, not an error


def test_approve_unknown_ad_id_is_a_noop_not_an_error(monkeypatch):
    monkeypatch.setattr(dedupe, "record_decision", lambda *a, **k: None)
    monkeypatch.setattr(dedupe, "get_artifact", lambda ad_id, angle_id=None: None)
    calls = []
    monkeypatch.setattr(dedupe, "set_edit_event_outcome", lambda eid, outcome: calls.append((eid, outcome)))

    resp = _client().post("/api/decision/UNKNOWN/approve")
    assert resp.status_code == 200
    assert calls == []


# ---- api_apply_edit: a new edit supersedes its source's own still-pending edit_event ----

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
            "format": "hero",
            "text_purpose": [{"text_verbatim": "x", "purpose": "cta", "placement": "bottom"}],
            "structural_zones": [{"zone_type": "cta", "position": "bottom", "container": "none", "detail": "d"}],
            "face_present": {"has_face": True, "prominence": "primary", "location": "centre"},
            "scene_elements": [],
            "layout_detail": {"product_count": 1},
        },
    }
    base.update(overrides)
    return base


def _mock_successful_edit(monkeypatch, source_edit_event_id):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact(edit_event_id=source_edit_event_id))
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 100)
    monkeypatch.setattr(dedupe, "update_edit_event_result", lambda *a, **k: None)
    monkeypatch.setattr(dedupe, "insert_edit_artifact", lambda **k: 200)
    monkeypatch.setattr(dedupe, "get_angle", lambda aid: None)
    monkeypatch.setattr(dashboard, "_read_artifact_image_bytes", lambda art, ad_id: (b"source-bytes", "AD123_draft.png"))
    monkeypatch.setattr(generate_image_prompt, "apply_targeted_edit", lambda *a, **k: b"new-image-bytes")
    monkeypatch.setattr(drift_check, "check_drift", lambda *a, **k: {
        "method": "skip", "checked": False, "drift_flag": False,
        "inside_pct": None, "outside_pct": None, "scatter_pct": None, "bbox": None})


def test_new_edit_supersedes_a_still_pending_source_edit_event(monkeypatch):
    _mock_successful_edit(monkeypatch, source_edit_event_id=55)
    calls = []
    monkeypatch.setattr(dedupe, "supersede_pending_edit_event", lambda eid: calls.append(eid))

    resp = _client().post("/artifact/42/edit", json={
        "target": "cta", "attribute": "text", "operation": "change", "new_value": "Buy Now",
    })
    assert resp.status_code == 200
    assert calls == [55]  # the SOURCE's own edit_event, not the new one (100)


def test_editing_a_v1_source_with_no_edit_event_never_calls_supersede(monkeypatch):
    _mock_successful_edit(monkeypatch, source_edit_event_id=None)
    calls = []
    monkeypatch.setattr(dedupe, "supersede_pending_edit_event", lambda eid: calls.append(eid))

    resp = _client().post("/artifact/42/edit", json={
        "target": "cta", "attribute": "text", "operation": "change", "new_value": "Buy Now",
    })
    assert resp.status_code == 200
    assert calls == []


# ---- supersede_pending_edit_event: never downgrades an already-judged event (guard is
# a SQL WHERE clause, so this is verified as a orchestration-level contract here - the
# guard itself is exercised for real in the dedupe.py source, not re-implemented) ----

def test_revert_never_touches_outcome(monkeypatch):
    # Reverting does not judge anything - the revert endpoint must never call either
    # set_edit_event_outcome or supersede_pending_edit_event.
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact(id=42, edit_event_id=None))
    monkeypatch.setattr(dedupe, "get_artifact_lineage", lambda root_id: [
        {"id": 42, "version_no": 1, "draft_image": "x.png", "created_at": None, "drift_flag": False},
    ])
    monkeypatch.setattr(dedupe, "insert_artifact_row_unconditional", lambda **k: 201)
    outcome_calls = []
    supersede_calls = []
    monkeypatch.setattr(dedupe, "set_edit_event_outcome", lambda eid, outcome: outcome_calls.append((eid, outcome)))
    monkeypatch.setattr(dedupe, "supersede_pending_edit_event", lambda eid: supersede_calls.append(eid))

    resp = _client().post("/artifact/42/revert")
    assert resp.status_code == 200
    assert outcome_calls == []
    assert supersede_calls == []
