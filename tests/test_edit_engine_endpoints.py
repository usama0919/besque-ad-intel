"""Tests for GET /artifact/{id}/edit-capabilities and POST /artifact/{id}/edit (Dynamic
Edit System, Steps 2-3). Every dedupe/DB touchpoint and the Gemini call are monkeypatched
- no real Postgres connection, no network, no spend. Mirrors the mocking style already
used in tests/test_generate_endpoints.py (monkeypatch the module attribute the endpoint
actually calls through)."""
import dashboard
from fastapi.testclient import TestClient
from src import dedupe, generate_image_prompt, drift_check


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


def _client():
    return TestClient(dashboard.app)


def test_edit_capabilities_endpoint_returns_derived_controls(monkeypatch):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    resp = _client().get("/artifact/42/edit-capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    targets = {(c["target"], c["attribute"]) for c in body["controls"]}
    assert ("cta", "text") in targets
    assert ("offer", "text") in targets
    assert ("person_face", "age") in targets


def test_edit_capabilities_endpoint_404_when_missing(monkeypatch):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: None)
    resp = _client().get("/artifact/999/edit-capabilities")
    assert resp.status_code == 404


def test_edit_endpoint_rejects_unknown_control(monkeypatch):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    logged = {}
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: logged.update(k) or 1)
    # this fixture's layout_detail carries no has_corner_badge at all - no badge control
    # is derived, so this must fail closed before ever reaching image/DB work.
    resp = _client().post("/artifact/42/edit", json={"target": "badge", "attribute": "corner_badge", "operation": "change", "new_value": "smaller"})
    assert resp.status_code == 400
    assert logged.get("outcome") == "rejected"


def test_edit_endpoint_rejects_disallowed_operation(monkeypatch):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 1)
    # cta control only allows "change", never "remove"
    resp = _client().post("/artifact/42/edit", json={"target": "cta", "attribute": "text", "operation": "remove"})
    assert resp.status_code == 400
    assert "not allowed" in resp.json()["error"]


def test_edit_endpoint_rejects_offer_text_with_unsubstantiated_claim(monkeypatch):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    logged = {}
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: logged.update(k) or 1)
    resp = _client().post("/artifact/42/edit", json={
        "target": "offer", "attribute": "text", "operation": "change",
        "new_value": "Save 3x more effective results today",
    })
    assert resp.status_code == 400
    assert "issues" in resp.json()
    assert logged.get("outcome") == "rejected"


def test_edit_endpoint_success_path_creates_new_artifact_and_calls_gemini_with_delta_only(monkeypatch):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    events = []
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: (events.append(k), 7)[1])
    results = []
    monkeypatch.setattr(dedupe, "update_edit_event_result", lambda *a, **k: results.append((a, k)))
    monkeypatch.setattr(dedupe, "insert_edit_artifact", lambda **k: 43)
    monkeypatch.setattr(dedupe, "get_angle", lambda aid: None)
    monkeypatch.setattr(dashboard, "_read_artifact_image_bytes", lambda art, ad_id: (b"source-bytes", "AD123_draft.png"))
    monkeypatch.setattr(drift_check, "check_drift", lambda *a, **k: {
        "method": "skip", "checked": False, "drift_flag": False, "inside_pct": None,
        "outside_pct": None, "scatter_pct": None, "bbox": None})

    captured_call = {}
    def fake_apply(source_bytes, instruction):
        captured_call["source_bytes"] = source_bytes
        captured_call["instruction"] = instruction
        return b"new-image-bytes"
    monkeypatch.setattr(generate_image_prompt, "apply_targeted_edit", fake_apply)

    resp = _client().post("/artifact/42/edit", json={
        "target": "offer", "attribute": "text", "operation": "change",
        "new_value": "Free gift with orders over £50",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["artifact_id"] == 43
    assert body["parent_artifact_id"] == 42

    # CORE RULE: the Gemini call must never see the assembled prompt/prose - only the
    # source image bytes and a short delta instruction naming the one change.
    assert captured_call["source_bytes"] == b"source-bytes"
    assert "orig prompt" not in captured_call["instruction"]
    assert "Free gift with orders over £50" in captured_call["instruction"]
    assert "FINAL and CORRECT" in captured_call["instruction"]

    assert events[0]["outcome"] == "pending"
    assert results[0][0] == (7, 43)


def test_edit_endpoint_clamps_younger_age_request(monkeypatch):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 9)
    monkeypatch.setattr(dedupe, "update_edit_event_result", lambda *a, **k: None)
    monkeypatch.setattr(dedupe, "insert_edit_artifact", lambda **k: 44)
    monkeypatch.setattr(dedupe, "get_angle", lambda aid: None)
    monkeypatch.setattr(dashboard, "_read_artifact_image_bytes", lambda art, ad_id: (b"source-bytes", "AD123_draft.png"))
    monkeypatch.setattr(generate_image_prompt, "apply_targeted_edit", lambda *a, **k: b"new-image-bytes")
    monkeypatch.setattr(drift_check, "check_drift", lambda *a, **k: {
        "method": "skip", "checked": False, "drift_flag": False, "inside_pct": None,
        "outside_pct": None, "scatter_pct": None, "bbox": None})

    resp = _client().post("/artifact/42/edit", json={
        "target": "person_face", "attribute": "age", "operation": "change",
        "new_value": "make her look younger, like early 20s",
    })
    assert resp.status_code == 200
    assert resp.json()["clamped"] is True


# ---- Never target the brand wordmark - build_targeted_edit_instruction ----

def test_instruction_always_includes_wordmark_protection_named_by_position():
    blueprint = {"structural_zones": [
        {"zone_type": "brand_wordmark", "position": "top-centre", "container": "none", "detail": "d"},
    ]}
    descriptor = {"target": "background", "attribute": "type", "label": "Background", "current_value": "pool"}
    instruction = generate_image_prompt.build_targeted_edit_instruction(
        descriptor, "change", "a bathroom counter", blueprint=blueprint)
    assert "brand wordmark" in instruction.lower()
    assert "top-centre" in instruction
    assert "never" in instruction.lower()


def test_instruction_includes_generic_wordmark_protection_when_no_zone_recorded():
    descriptor = {"target": "prop", "attribute": "shelf", "label": "shelf", "current_value": "wood"}
    instruction = generate_image_prompt.build_targeted_edit_instruction(
        descriptor, "change", "metal", blueprint={})
    assert "brand wordmark" in instruction.lower()


def test_instruction_wordmark_protection_present_even_when_editing_headline():
    descriptor = {"target": "headline", "attribute": "text", "label": "Headline", "current_value": "old"}
    instruction = generate_image_prompt.build_targeted_edit_instruction(
        descriptor, "change", "new headline", blueprint=None)
    assert "brand wordmark" in instruction.lower()


# ---- Step 4: drift check + one automatic retry, then stop ----

def _setup_common_mocks(monkeypatch, extra_dedupe=None):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 100)
    monkeypatch.setattr(dedupe, "update_edit_event_result", lambda *a, **k: None)
    monkeypatch.setattr(dedupe, "insert_edit_artifact", lambda **k: 200)
    monkeypatch.setattr(dedupe, "get_angle", lambda aid: None)
    monkeypatch.setattr(dashboard, "_read_artifact_image_bytes", lambda art, ad_id: (b"source-bytes", "AD123_draft.png"))
    if extra_dedupe:
        for name, fn in extra_dedupe.items():
            monkeypatch.setattr(dedupe, name, fn)


def test_drift_detected_triggers_exactly_one_retry_then_stops(monkeypatch):
    _setup_common_mocks(monkeypatch)
    apply_calls = []
    def fake_apply(source_bytes, instruction):
        apply_calls.append(instruction)
        return f"result-{len(apply_calls)}".encode()
    monkeypatch.setattr(generate_image_prompt, "apply_targeted_edit", fake_apply)

    check_calls = []
    def fake_check_drift(source_bytes, result_bytes, descriptor, blueprint):
        check_calls.append(result_bytes)
        # First attempt drifts; retry (2nd call) comes back clean.
        drifted = len(check_calls) == 1
        return {"method": "zone", "checked": True, "drift_flag": drifted,
                "inside_pct": 10.0, "outside_pct": 5.0 if drifted else 0.05,
                "scatter_pct": None, "bbox": (0, 0, 10, 10)}
    monkeypatch.setattr(drift_check, "check_drift", fake_check_drift)

    resp = _client().post("/artifact/42/edit", json={
        "target": "cta", "attribute": "text", "operation": "change", "new_value": "Buy Now",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert len(apply_calls) == 2  # exactly one retry, never more
    assert len(check_calls) == 2
    assert "NOTE: your previous attempt changed pixels outside" in apply_calls[1]
    assert body["drift_checked"] is True
    assert body["drift_flag"] is False  # retry's own verdict is final
    assert body["drift_outside_pct"] == 0.05


def test_drift_still_present_after_retry_is_still_returned_not_discarded(monkeypatch):
    _setup_common_mocks(monkeypatch)
    apply_calls = []
    monkeypatch.setattr(generate_image_prompt, "apply_targeted_edit",
                         lambda *a, **k: (apply_calls.append(1), b"result")[1])
    # Every check_drift call reports drift - the retry does NOT fix it.
    monkeypatch.setattr(drift_check, "check_drift", lambda *a, **k: {
        "method": "zone", "checked": True, "drift_flag": True, "inside_pct": 8.0,
        "outside_pct": 4.2, "scatter_pct": None, "bbox": (0, 0, 5, 5),
    })
    resp = _client().post("/artifact/42/edit", json={
        "target": "cta", "attribute": "text", "operation": "change", "new_value": "Buy Now",
    })
    assert resp.status_code == 200  # STILL returned, never silently discarded
    body = resp.json()
    assert len(apply_calls) == 2  # one retry, then stopped - not a retry loop
    assert body["ok"] is True
    assert body["drift_flag"] is True
    assert body["artifact_id"] == 200


def test_no_zone_target_skips_drift_check_and_never_retries(monkeypatch):
    # Uses a VALID control (cta) but mocks check_drift to return checked=False - the
    # real skip case (lighting/background/typography, see
    # test_drift_check.py::test_lighting_background_typography_still_skip_even_with_full_frame_change
    # for the actual skip-target coverage). What matters here is the ENDPOINT'S OWN
    # behaviour on checked=False: no retry, regardless of what drift_flag says.
    _setup_common_mocks(monkeypatch)
    apply_calls = []
    monkeypatch.setattr(generate_image_prompt, "apply_targeted_edit",
                         lambda *a, **k: (apply_calls.append(1), b"result")[1])
    check_calls = []
    def fake_check_drift(*a, **k):
        check_calls.append(1)
        return {"method": "skip", "checked": False, "drift_flag": False,
                "inside_pct": None, "outside_pct": None, "scatter_pct": None, "bbox": None}
    monkeypatch.setattr(drift_check, "check_drift", fake_check_drift)

    resp = _client().post("/artifact/42/edit", json={
        "target": "cta", "attribute": "text", "operation": "change", "new_value": "Buy Now",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert len(apply_calls) == 1  # never retries on checked=False
    assert len(check_calls) == 1
    assert body["drift_checked"] is False
    assert body["drift_flag"] is False
