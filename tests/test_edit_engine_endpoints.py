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
    # Fail-closed (2026-08-14): person_face/age has no real per-artifact stored value
    # anywhere in the data model, so it is never emitted - see _person_face_controls.
    assert ("person_face", "age") not in targets


def test_edit_capabilities_endpoint_404_when_missing(monkeypatch):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: None)
    resp = _client().get("/artifact/999/edit-capabilities")
    assert resp.status_code == 404


# ---- Stage 5 (2026-08-17): is_legacy/legacy_scene_summary - a blueprint with no
# `objects` key predates the objects schema and gets a read-only text summary instead
# of erroring or silently showing nothing ----

def test_edit_capabilities_endpoint_flags_legacy_blueprint_with_scene_summary(monkeypatch):
    # _artifact()'s default blueprint has no `objects` key at all - the legacy shape.
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact(
        blueprint={
            "format": "hero", "face_present": {"has_face": False}, "layout_detail": {},
            "scene_elements": [{"element": "wooden tray", "role": "staging",
                                "essential": True, "depicts_competitor_category": False}],
            "structural_zones": [{"zone_type": "brand_wordmark", "position": "top-left",
                                  "container": "none", "detail": ""}],
        },
    ))
    resp = _client().get("/artifact/42/edit-capabilities")
    body = resp.json()
    assert body["is_legacy"] is True
    assert any("wooden tray" in line for line in body["legacy_scene_summary"])
    assert any("brand_wordmark" in line for line in body["legacy_scene_summary"])


def test_edit_capabilities_endpoint_not_legacy_when_objects_present(monkeypatch):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact(
        blueprint={
            "format": "hero", "face_present": {"has_face": False}, "layout_detail": {},
            "objects": [
                {"object_id": "obj_01", "kind": "prop", "description": "wooden tray",
                 "bbox": [0, 0.5, 1, 0.5], "colours": [], "ownership": "generic",
                 "role": "environment", "carries_brand_mark": False,
                 "persuasive_function": "staging", "disposition": "keep"},
            ],
        },
    ))
    resp = _client().get("/artifact/42/edit-capabilities")
    body = resp.json()
    assert body["is_legacy"] is False
    assert body["legacy_scene_summary"] == []
    targets = {(c["target"], c["attribute"]) for c in body["controls"]}
    assert ("object", "obj_01") in targets


def test_edit_endpoint_rejects_unknown_control(monkeypatch):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    logged = {}
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: logged.update(k) or 1)
    # this fixture's layout_detail carries no has_corner_badge at all - no badge control
    # is derived, so this must fail closed before ever reaching image/DB work.
    resp = _client().post("/artifact/42/edit", json={"target": "badge", "attribute": "corner_badge", "operation": "change", "new_value": "smaller"})
    assert resp.status_code == 400
    assert logged.get("outcome") == "rejected"


def test_edit_endpoint_rejects_product_placement_unconditionally(monkeypatch):
    """Server-side hole closure (2026-08-16): product identity/placement is never
    editable through this endpoint, regardless of whether this artifact would
    otherwise offer the control at all - the modal already hides it, but that's a
    UI-only guarantee; a direct API call must be rejected here too."""
    monkeypatch.setattr(
        dedupe, "get_artifact_by_id",
        lambda aid: _artifact(element_provenance={"product": "substituted"}),
    )
    logged = {}
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: logged.update(k) or 1)
    resp = _client().post("/artifact/42/edit", json={
        "target": "product", "attribute": "placement", "operation": "change",
        "new_value": "move the bottle to the left",
    })
    assert resp.status_code == 400
    assert logged.get("outcome") == "rejected"
    assert "not editable" in resp.json()["error"]


def test_edit_endpoint_rejects_product_placement_even_when_control_would_be_absent(monkeypatch):
    # This fixture's default element_provenance={} means _product_control wouldn't
    # even be derived (fails the substituted-agreement gate) - the placement request
    # must still be rejected by the unconditional check, not fall through to the
    # generic "no editable control" 400 for the wrong reason.
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    logged = {}
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: logged.update(k) or 1)
    resp = _client().post("/artifact/42/edit", json={
        "target": "product", "attribute": "placement", "operation": "change",
        "new_value": "move the bottle to the left",
    })
    assert resp.status_code == 400
    assert "not editable via this endpoint" in resp.json()["error"]


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
    def fake_apply(source_bytes, instruction, reference_images=None):
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
    # drift_method is recorded on EVERY apply (2026-08-16), not only a drifted one -
    # here mocked as "skip", and it must reach update_edit_event_result verbatim.
    assert results[0][1]["drift_method"] == "skip"


def test_containment_fallback_drift_method_is_recorded_not_silent(monkeypatch):
    """The gap this closes: containment and zone can both report drift_flag=False,
    but they answer different questions (zone: did the change land in the recorded
    region; containment: is the change spatially coherent, wherever it is). Without
    drift_method on the row, a containment fallback is indistinguishable from a real
    zone pass. Here check_drift reports "containment" with no drift - the row must
    still show "containment", not blank/"zone"/anything implying a real zone existed."""
    monkeypatch.setattr(dedupe, "get_artifact_by_id",
                         lambda aid: _artifact(element_provenance={"product": "substituted"}))
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 9)
    results = []
    monkeypatch.setattr(dedupe, "update_edit_event_result", lambda *a, **k: results.append((a, k)))
    monkeypatch.setattr(dedupe, "insert_edit_artifact", lambda **k: 55)
    monkeypatch.setattr(dedupe, "get_angle", lambda aid: None)
    monkeypatch.setattr(dashboard, "_read_artifact_image_bytes",
                         lambda art, ad_id: (b"draft-bytes", "AD123_draft.png"))
    monkeypatch.setattr(drift_check, "check_drift", lambda *a, **k: {
        "method": "containment", "checked": True, "drift_flag": False, "inside_pct": None,
        "outside_pct": None, "scatter_pct": 3.0, "bbox": (0, 0, 10, 10)})
    monkeypatch.setattr(generate_image_prompt, "apply_targeted_edit", lambda *a, **k: b"new-image-bytes")

    resp = _client().post("/artifact/42/edit", json={
        "target": "product", "attribute": "realism", "operation": "change",
        "new_value": "illustrated",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["drift_method"] == "containment"
    assert results[0][1]["drift_method"] == "containment"
    assert results[0][1]["drift_flag"] is False


def test_edit_endpoint_rejects_person_face_age_since_no_control_is_ever_emitted(monkeypatch):
    # Fail-closed (2026-08-14): person_face/age is never emitted by
    # derive_edit_capabilities (see _person_face_controls) since no per-artifact age
    # value exists anywhere in the data model - so this target/attribute pair can never
    # resolve to a control, and the edit is rejected before any image/DB work, the same
    # as any other unknown control (test_edit_endpoint_rejects_unknown_control). This
    # replaces the old test_edit_endpoint_clamps_younger_age_request, which exercised a
    # path that no longer exists; clamp_person_age itself is still unit-tested directly
    # in tests/test_edit_capability.py.
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact())
    logged = {}
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: logged.update(k) or 9)

    resp = _client().post("/artifact/42/edit", json={
        "target": "person_face", "attribute": "age", "operation": "change",
        "new_value": "make her look younger, like early 20s",
    })
    assert resp.status_code == 400
    assert logged.get("outcome") == "rejected"


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


# ---- Preservation list must not name the edit's own target (2026-08-14, live
# incident artifact 1259): a product edit's change instruction and the fixed
# preservation list both named "product"/"bottle" in the same instruction, one asking
# for a change, the other demanding no change - the photoreal bottle survived the edit
# unchanged. Hardcoded expected strings throughout, never derived from
# _PRESERVATION_TERMS/_TARGET_EXCLUDED_PRESERVATION_TERMS - a test built from the same
# constants the function reads would pass even if those constants were wrong. ----

def test_preservation_list_drops_product_and_bottle_for_product_target():
    descriptor = {"target": "product", "attribute": "placement", "label": "Product",
                  "current_value": "1 bottle(s)"}
    instruction = generate_image_prompt.build_targeted_edit_instruction(
        descriptor, "change", "One bottle only - no separate photographic bottle anywhere in the image",
        blueprint=None)
    assert ("Every other pixel in the image - layout, all other text, colours, "
            "background, lighting, composition - must be") in instruction


def test_preservation_list_drops_background_for_background_target():
    descriptor = {"target": "background", "attribute": "type", "label": "Background",
                  "current_value": "pool"}
    instruction = generate_image_prompt.build_targeted_edit_instruction(
        descriptor, "change", "a bathroom counter", blueprint=None)
    assert ("Every other pixel in the image - layout, product, bottle, all other text, "
            "colours, lighting, composition - must be") in instruction


def test_preservation_list_drops_lighting_for_lighting_target():
    descriptor = {"target": "lighting", "attribute": "scene_lighting", "label": "Lighting",
                  "current_value": "soft, warm"}
    instruction = generate_image_prompt.build_targeted_edit_instruction(
        descriptor, "change", "harsh, cool", blueprint=None)
    assert ("Every other pixel in the image - layout, product, bottle, all other text, "
            "colours, background, composition - must be") in instruction


def test_preservation_list_unfiltered_for_text_target():
    """Text targets already read "all other text" - the word "other" already excludes
    whichever text-shaped target changed, so nothing is dropped for them; the full
    original list must survive unchanged."""
    descriptor = {"target": "headline", "attribute": "text", "label": "Headline",
                  "current_value": "old headline"}
    instruction = generate_image_prompt.build_targeted_edit_instruction(
        descriptor, "change", "new headline", blueprint=None)
    assert ("Every other pixel in the image - layout, product, bottle, all other text, "
            "colours, background, lighting, composition - must be") in instruction


# ---- Product realism control (2026-08-16): the edit call sends ONLY the v1 draft
# image plus a pre-authored delta sentence from src/realism_deltas.py - never a
# blueprint-driven instruction, never reference photos, never the stored prompt ----

def test_edit_endpoint_realism_uses_pre_authored_delta_only(monkeypatch):
    monkeypatch.setattr(
        dedupe, "get_artifact_by_id",
        lambda aid: _artifact(element_provenance={"product": "substituted"}),
    )
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 9)
    monkeypatch.setattr(dedupe, "update_edit_event_result", lambda *a, **k: None)
    insert_calls = []
    def fake_insert_edit_artifact(**k):
        insert_calls.append(k)
        return 55
    monkeypatch.setattr(dedupe, "insert_edit_artifact", fake_insert_edit_artifact)
    monkeypatch.setattr(dedupe, "get_angle", lambda aid: None)
    monkeypatch.setattr(dashboard, "_read_artifact_image_bytes",
                         lambda art, ad_id: (b"draft-bytes", "AD123_draft.png"))
    monkeypatch.setattr(drift_check, "check_drift", lambda *a, **k: {
        "method": "containment", "checked": True, "drift_flag": False, "inside_pct": None,
        "outside_pct": None, "scatter_pct": 3.0, "bbox": (0, 0, 10, 10)})

    captured = {}
    def fake_apply(source_bytes, instruction, reference_images=None):
        captured["source_bytes"] = source_bytes
        captured["instruction"] = instruction
        captured["reference_images"] = reference_images
        return b"new-image-bytes"
    monkeypatch.setattr(generate_image_prompt, "apply_targeted_edit", fake_apply)

    resp = _client().post("/artifact/42/edit", json={
        "target": "product", "attribute": "realism", "operation": "change",
        "new_value": "illustrated",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True

    from src import realism_deltas
    assert captured["source_bytes"] == b"draft-bytes"
    # No reference photos, no blueprint text, no stored prompt - exactly the
    # pre-authored sentence for this value, verbatim.
    assert captured["reference_images"] is None
    assert captured["instruction"] == realism_deltas.REALISM_DELTAS["illustrated"]
    assert "orig prompt" not in captured["instruction"]

    # A NEW artifact row is created (55) - v1 (42) is the parent, never mutated in
    # place: insert_edit_artifact receives the v1 dict as `source`, and its own
    # image_prompt/draft_image are untouched (a fresh new_image_prompt/new_draft_image
    # are passed alongside it, not written back onto v1 itself).
    assert body["artifact_id"] == 55
    assert body["parent_artifact_id"] == 42
    assert len(insert_calls) == 1
    assert insert_calls[0]["source"]["id"] == 42
    assert insert_calls[0]["source"]["image_prompt"] == "orig prompt"
    assert insert_calls[0]["new_draft_image"] != insert_calls[0]["source"]["draft_image"]


def test_edit_endpoint_realism_rejects_unknown_value(monkeypatch):
    monkeypatch.setattr(
        dedupe, "get_artifact_by_id",
        lambda aid: _artifact(element_provenance={"product": "substituted"}),
    )
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 9)
    monkeypatch.setattr(dedupe, "update_edit_event_result", lambda *a, **k: None)
    monkeypatch.setattr(dashboard, "_read_artifact_image_bytes",
                         lambda art, ad_id: (b"draft-bytes", "AD123_draft.png"))

    resp = _client().post("/artifact/42/edit", json={
        "target": "product", "attribute": "realism", "operation": "change",
        "new_value": "ultra_hd_4k",
    })
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


# ---- Stage 4 (2026-08-17): per-object remove control end-to-end - the fixed
# build_object_removal_instruction delta, and a removal_zone drift check ----

def test_edit_endpoint_object_removal_sends_fixed_delta_and_uses_removal_zone(monkeypatch):
    monkeypatch.setattr(dedupe, "get_artifact_by_id", lambda aid: _artifact(
        blueprint={
            "format": "hero", "face_present": {"has_face": False}, "layout_detail": {},
            "objects": [
                {"object_id": "obj_02", "kind": "prop", "description": "a wooden tray",
                 "bbox": [0.25, 0.25, 0.25, 0.25], "colours": [], "ownership": "generic",
                 "role": "environment", "carries_brand_mark": False,
                 "persuasive_function": "staging", "disposition": "keep"},
            ],
        },
    ))
    monkeypatch.setattr(dedupe, "insert_edit_event", lambda **k: 9)
    monkeypatch.setattr(dedupe, "update_edit_event_result", lambda *a, **k: None)
    monkeypatch.setattr(dedupe, "insert_edit_artifact", lambda **k: 55)
    monkeypatch.setattr(dedupe, "get_angle", lambda aid: None)
    monkeypatch.setattr(dashboard, "_read_artifact_image_bytes",
                         lambda art, ad_id: (b"draft-bytes", "AD123_draft.png"))

    captured = {}
    def fake_apply(source_bytes, instruction, reference_images=None):
        captured["instruction"] = instruction
        return b"new-image-bytes"
    monkeypatch.setattr(generate_image_prompt, "apply_targeted_edit", fake_apply)

    drift_calls = []
    def fake_check_drift(source_bytes, result_bytes, descriptor, blueprint):
        drift_calls.append((descriptor, blueprint))
        return {"method": "removal_zone", "checked": True, "drift_flag": False,
                "inside_pct": 5.0, "outside_pct": 0.1, "scatter_pct": None, "bbox": (0, 0, 1, 1)}
    monkeypatch.setattr(drift_check, "check_drift", fake_check_drift)

    resp = _client().post("/artifact/42/edit", json={
        "target": "object", "attribute": "obj_02", "operation": "remove",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["drift_method"] == "removal_zone"

    assert captured["instruction"] == (
        "Remove the a wooden tray entirely and close the space naturally with the "
        "surrounding surface and lighting. Everything else in the image is unchanged."
    )
    # The exact fixed template - never build_targeted_edit_instruction's generic
    # "attached image is FINAL and CORRECT... preservation list" wrapper.
    assert "FINAL and CORRECT" not in captured["instruction"]
    assert len(drift_calls) == 1
    assert drift_calls[0][0]["target"] == "object"
    assert drift_calls[0][0]["attribute"] == "obj_02"


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
    def fake_apply(source_bytes, instruction, reference_images=None):
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
