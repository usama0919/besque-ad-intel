"""Tests for dashboard.py's /api/run, /api/run/status, /api/run/stop - specifically the
LOCAL_RUN branch (in-process pipeline.run_once via _run_pipeline_bg) and the guarantee
that the Cloud Run Job path is byte-for-byte unchanged when LOCAL_RUN is unset.

google-cloud-run is listed in requirements.txt but not installed in ./venv (same gap
CLAUDE.md documents for google-cloud-storage/Pillow) - `from google.cloud import run_v2`
genuinely fails to import here, so monkeypatch.setattr("google.cloud.run_v2....", ...)
can't resolve its dotted path. _install_fake_run_v2 injects a fake module directly into
sys.modules instead, which the lazy `from google.cloud import run_v2` inside api_run/
api_run_status will pick up without needing the real package installed."""
import sys
import types
import dashboard


def _install_fake_run_v2(monkeypatch, jobs_client=None, executions_client=None):
    fake = types.ModuleType("google.cloud.run_v2")

    class RunJobRequest:
        class Overrides:
            class ContainerOverride:
                def __init__(self, env=None):
                    self.env = env or []

            def __init__(self, container_overrides=None):
                self.container_overrides = container_overrides or []

        def __init__(self, name=None, overrides=None):
            self.name = name
            self.overrides = overrides

    class EnvVar:
        def __init__(self, name, value):
            self.name = name
            self.value = value

    fake.RunJobRequest = RunJobRequest
    fake.EnvVar = EnvVar
    if jobs_client is not None:
        fake.JobsClient = jobs_client
    if executions_client is not None:
        fake.ExecutionsClient = executions_client
    monkeypatch.setitem(sys.modules, "google.cloud.run_v2", fake)
    return fake


def _reset_run_status():
    dashboard._run_status["running"] = False
    dashboard._run_status["last_summary"] = None
    dashboard._run_status["stop_requested"] = False
    dashboard._run_status["execution"] = None
    dashboard._run_status["mode"] = None
    dashboard._run_thread = None
    # run_progress is DB-backed (path-agnostic) - a prior test's competitor progress must
    # not leak into the next test's api_run_status assertions.
    from src import dedupe
    dedupe.init_run_progress()
    dedupe.set_run_progress("", 0, 0)


# ---- /api/run: LOCAL_RUN=1 path ----

def test_api_run_local_starts_background_thread_and_returns_immediately(monkeypatch):
    monkeypatch.setenv("LOCAL_RUN", "1")
    _reset_run_status()
    captured = {}

    def fake_run_once(**kwargs):
        captured.update(kwargs)
        return {"processed": 1, "skipped": 0, "failed": 0}

    monkeypatch.setattr("src.pipeline.run_once", fake_run_once)

    resp = dashboard.api_run(n=3, competitor_id=2, category="body_oil", product_id=1,
                              angle_id=7, realism="ugc_native", text_in_image=True,
                              include_product=False, body_area="knees", offer_text="20% off",
                              edit_mode=True, operator_instruction="make the background warmer",
                              check_output=True, retheme_colours=False)
    import json
    body = json.loads(resp.body)
    assert body == {"ok": True, "started": True}
    assert dashboard._run_status["mode"] == "local"

    dashboard._run_thread.join(timeout=5)
    assert not dashboard._run_thread.is_alive()

    # every run-strip param must have reached pipeline.run_once
    assert captured["max_per_competitor"] == 3
    assert captured["competitor_id"] == 2
    assert captured["category"] == "body_oil"
    assert captured["product_id"] == 1
    assert captured["angle_id"] == 7
    assert captured["realism"] == "ugc_native"
    assert captured["text_in_image"] is True
    assert captured["include_product"] is False
    assert captured["body_area"] == "knees"
    assert captured["offer_text"] == "20% off"
    assert captured["edit_mode"] is True
    assert captured["operator_instruction"] == "make the background warmer"
    assert captured["check_output"] is True
    assert captured["retheme_colours"] is False

    assert dashboard._run_status["running"] is False
    assert dashboard._run_status["last_summary"] == {"processed": 1, "skipped": 0, "failed": 0}


def test_api_run_local_resets_stop_requested_from_a_previous_run(monkeypatch):
    """A Stop click on a prior run must not immediately kill the next one."""
    monkeypatch.setenv("LOCAL_RUN", "1")
    _reset_run_status()
    dashboard._run_status["stop_requested"] = True
    monkeypatch.setattr("src.pipeline.run_once", lambda **k: {"processed": 0})

    dashboard.api_run()
    # stop_requested is reset synchronously, before the thread starts - no join needed
    # to observe it, but join anyway so the test doesn't leak a running thread.
    assert dashboard._run_status["stop_requested"] is False
    dashboard._run_thread.join(timeout=5)


def test_api_run_local_does_not_touch_cloud_run(monkeypatch):
    """LOCAL_RUN=1 must never import/call google.cloud.run_v2 - if it did, this would
    raise (no real GCP credentials in the test environment) instead of returning ok."""
    monkeypatch.setenv("LOCAL_RUN", "1")
    _reset_run_status()
    monkeypatch.setattr("src.pipeline.run_once", lambda **k: {"processed": 0})

    resp = dashboard.api_run()
    import json
    assert json.loads(resp.body) == {"ok": True, "started": True}
    dashboard._run_thread.join(timeout=5)


def test_run_pipeline_bg_records_exception_as_error_summary(monkeypatch):
    _reset_run_status()

    def boom(**k):
        raise RuntimeError("scrape failed")

    monkeypatch.setattr("src.pipeline.run_once", boom)
    dashboard._run_status["running"] = True

    dashboard._run_pipeline_bg(2)

    assert dashboard._run_status["running"] is False
    assert dashboard._run_status["last_summary"] == {"error": "scrape failed"}


# ---- /api/run: Cloud Run Job path (LOCAL_RUN unset) must be unchanged ----

class _FakeOp:
    metadata = type("obj", (), {"name": "projects/x/locations/y/jobs/z/executions/1"})()


class _FakeJobsClient:
    def __init__(self, *a, **k):
        pass

    def run_job(self, request):
        _FakeJobsClient.last_request = request
        return _FakeOp()


def test_api_run_job_path_when_local_run_unset(monkeypatch):
    monkeypatch.delenv("LOCAL_RUN", raising=False)
    _reset_run_status()
    _install_fake_run_v2(monkeypatch, jobs_client=_FakeJobsClient)

    def _no_thread(*a, **k):
        raise AssertionError("Thread must not be constructed on the Job path")

    monkeypatch.setattr(dashboard.threading, "Thread", _no_thread)

    resp = dashboard.api_run(n=2, competitor_id=5)
    import json
    assert json.loads(resp.body) == {"ok": True, "started": True}
    assert dashboard._run_status["mode"] == "job"
    assert dashboard._run_status["running"] is True


def test_api_run_job_path_ignores_category_in_env_vars(monkeypatch):
    """category is accepted (for the LOCAL_RUN path) but the Job path's env var list is
    unchanged - it must not appear as a new RUN_CATEGORY env var."""
    monkeypatch.delenv("LOCAL_RUN", raising=False)
    _reset_run_status()
    _install_fake_run_v2(monkeypatch, jobs_client=_FakeJobsClient)

    dashboard.api_run(n=2, competitor_id=5, category="body_oil")
    env_names = [e.name for e in _FakeJobsClient.last_request.overrides.container_overrides[0].env]
    assert "RUN_CATEGORY" not in env_names
    assert set(env_names) == {
        "RUN_COMPETITOR_ID", "RUN_MAX_PER_COMPETITOR", "RUN_PRODUCT_ID", "RUN_ANGLE_ID",
        "RUN_REALISM", "RUN_TEXT_IN_IMAGE", "RUN_INCLUDE_PRODUCT", "RUN_BODY_AREA", "RUN_OFFER_TEXT",
        "RUN_EDIT_MODE", "RUN_INSTRUCTION", "RUN_CHECK_OUTPUT", "RUN_RETHEME_COLOURS",
    }


def test_api_run_job_path_edit_mode_env_var(monkeypatch):
    monkeypatch.delenv("LOCAL_RUN", raising=False)
    _reset_run_status()
    _install_fake_run_v2(monkeypatch, jobs_client=_FakeJobsClient)

    dashboard.api_run(n=2, competitor_id=5, edit_mode=True)
    env = {e.name: e.value for e in _FakeJobsClient.last_request.overrides.container_overrides[0].env}
    assert env["RUN_EDIT_MODE"] == "1"

    dashboard.api_run(n=2, competitor_id=5, edit_mode=False)
    env = {e.name: e.value for e in _FakeJobsClient.last_request.overrides.container_overrides[0].env}
    assert env["RUN_EDIT_MODE"] == "0"


def test_api_run_job_path_operator_instruction_env_var(monkeypatch):
    monkeypatch.delenv("LOCAL_RUN", raising=False)
    _reset_run_status()
    _install_fake_run_v2(monkeypatch, jobs_client=_FakeJobsClient)

    dashboard.api_run(n=2, competitor_id=5, operator_instruction="keep it minimal")
    env = {e.name: e.value for e in _FakeJobsClient.last_request.overrides.container_overrides[0].env}
    assert env["RUN_INSTRUCTION"] == "keep it minimal"

    dashboard.api_run(n=2, competitor_id=5)
    env = {e.name: e.value for e in _FakeJobsClient.last_request.overrides.container_overrides[0].env}
    assert env["RUN_INSTRUCTION"] == ""


def test_api_run_job_path_check_output_env_var(monkeypatch):
    monkeypatch.delenv("LOCAL_RUN", raising=False)
    _reset_run_status()
    _install_fake_run_v2(monkeypatch, jobs_client=_FakeJobsClient)

    dashboard.api_run(n=2, competitor_id=5, check_output=True)
    env = {e.name: e.value for e in _FakeJobsClient.last_request.overrides.container_overrides[0].env}
    assert env["RUN_CHECK_OUTPUT"] == "1"

    dashboard.api_run(n=2, competitor_id=5, check_output=False)
    env = {e.name: e.value for e in _FakeJobsClient.last_request.overrides.container_overrides[0].env}
    assert env["RUN_CHECK_OUTPUT"] == "0"


def test_api_run_job_path_retheme_colours_env_var(monkeypatch):
    monkeypatch.delenv("LOCAL_RUN", raising=False)
    _reset_run_status()
    _install_fake_run_v2(monkeypatch, jobs_client=_FakeJobsClient)

    dashboard.api_run(n=2, competitor_id=5, retheme_colours=True)
    env = {e.name: e.value for e in _FakeJobsClient.last_request.overrides.container_overrides[0].env}
    assert env["RUN_RETHEME_COLOURS"] == "1"

    dashboard.api_run(n=2, competitor_id=5, retheme_colours=False)
    env = {e.name: e.value for e in _FakeJobsClient.last_request.overrides.container_overrides[0].env}
    assert env["RUN_RETHEME_COLOURS"] == "0"


# ---- /api/run/status ----

def test_api_run_status_local_mode_reports_from_memory(monkeypatch):
    _reset_run_status()
    dashboard._run_status["mode"] = "local"
    dashboard._run_status["running"] = True
    dashboard._run_status["last_summary"] = None

    resp = dashboard.api_run_status()
    import json
    assert json.loads(resp.body) == {"running": True, "last_summary": None, "progress": None}


def test_api_run_status_reports_progress_when_a_competitor_is_running(monkeypatch):
    """Step 3: progress is DB-backed (dedupe.run_progress) so it's readable the same way
    regardless of run mode - set it directly here (as pipeline.run_once would) and confirm
    api_run_status surfaces it without needing mode='local'."""
    from src import dedupe
    _reset_run_status()
    dashboard._run_status["mode"] = "local"
    dedupe.set_run_progress("OSEA", 2, 6)

    resp = dashboard.api_run_status()
    import json
    body = json.loads(resp.body)
    assert body["progress"] == {"competitor_name": "OSEA", "competitor_index": 2, "competitor_total": 6}


def test_api_run_status_local_mode_reports_completion(monkeypatch):
    _reset_run_status()
    dashboard._run_status["mode"] = "local"
    dashboard._run_status["running"] = False
    dashboard._run_status["last_summary"] = {"processed": 3, "skipped": 1, "failed": 0}

    resp = dashboard.api_run_status()
    import json
    assert json.loads(resp.body) == {"running": False, "last_summary": {"processed": 3, "skipped": 1, "failed": 0},
                                      "progress": None}


class _FakeExecution:
    def __init__(self, running_count=0, succeeded_count=0, failed_count=0):
        self.running_count = running_count
        self.succeeded_count = succeeded_count
        self.failed_count = failed_count


class _FakeExecutionsClient:
    def __init__(self, *a, **k):
        pass

    def list_executions(self, parent):
        return iter([_FakeExecution(running_count=0, succeeded_count=2, failed_count=0)])


def test_api_run_status_job_mode_unchanged_gcp_query(monkeypatch):
    """Regression guard: when mode is not "local" (unset, or explicitly "job"), the
    ORIGINAL stateless GCP Executions query path must still run exactly as before."""
    _reset_run_status()
    _install_fake_run_v2(monkeypatch, executions_client=_FakeExecutionsClient)

    resp = dashboard.api_run_status()
    import json
    assert json.loads(resp.body) == {"running": False, "last_summary": {"succeeded": 2, "failed": 0},
                                      "progress": None}


# ---- /api/run/stop ----

def test_api_run_stop_sets_flag():
    _reset_run_status()
    dashboard.api_run_stop()
    assert dashboard._run_status["stop_requested"] is True


# ---- PUT /api/competitors/{id}: page_id must survive a page_id-less update ----
# Regression guard for the 2026-07-30 incident: page_id absent from the request wiped
# six verified numeric page_ids, because the route defaulted it to `name` (correct for
# add_competitor's brand-new-row case, wrong for an update of an existing, real page_id).

# ---- /api/warnings: created_at datetime must be JSON-serialisable ----
# Regression guard for the 2026-07-31 incident: /api/warnings raised TypeError ("Object of
# type datetime is not JSON serializable") the moment pipeline_warnings held a real row -
# it only ever returned 200 because the table was empty from 29 Jul, when it was added,
# until this was hit. The warnings banner has never once actually displayed a warning.

def test_api_warnings_200_with_a_real_warning_row(monkeypatch):
    from src import dedupe
    from fastapi.testclient import TestClient

    dedupe.init_pipeline_warnings()
    dedupe.record_warning("compliance_failed", "__test__ regression guard warning row")
    client = TestClient(dashboard.app)
    r = client.get("/api/warnings")
    assert r.status_code == 200
    body = r.json()
    assert any(w["detail"] == "__test__ regression guard warning row" for w in body)
    match = next(w for w in body if w["detail"] == "__test__ regression guard warning row")
    assert match["kind"] == "compliance_failed"
    assert isinstance(match["created_at"], str)


# ---- /api/artifacts: operator_instruction must surface on the review card (auditability) ----

def test_api_artifacts_surfaces_operator_instruction(monkeypatch):
    from src import dedupe
    from fastapi.testclient import TestClient
    import uuid

    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    dedupe.save_artifact(
        ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "H"},
        draft_image="assets/x_draft.png",
        metadata={"cta": "Shop", "destination_url": "http://x"},
        operator_instruction="make the background warmer",
    )
    try:
        client = TestClient(dashboard.app)
        r = client.get("/api/artifacts")
        assert r.status_code == 200
        body = r.json()
        match = next(a for a in body if a["ad_id"] == ad_id)
        assert match["operator_instruction"] == "make the background warmer"
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_api_artifacts_operator_instruction_empty_string_when_not_given(monkeypatch):
    from src import dedupe
    from fastapi.testclient import TestClient
    import uuid

    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    dedupe.save_artifact(
        ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "H"},
        draft_image="assets/x_draft.png",
        metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    try:
        client = TestClient(dashboard.app)
        r = client.get("/api/artifacts")
        body = r.json()
        match = next(a for a in body if a["ad_id"] == ad_id)
        assert match["operator_instruction"] == ""
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_api_artifacts_surfaces_critic_findings(monkeypatch):
    """Prompt 4, Item 1: findings must reach the card - surface, never act."""
    from src import dedupe
    from fastapi.testclient import TestClient
    import uuid

    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    dedupe.save_artifact(
        ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "H"},
        draft_image="assets/x_draft.png",
        metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    dedupe.update_artifact_findings(
        ad_id, [{"category": "testimonial", "description": "fabricated quote", "confidence": "high"}]
    )
    try:
        client = TestClient(dashboard.app)
        r = client.get("/api/artifacts")
        assert r.status_code == 200
        body = r.json()
        match = next(a for a in body if a["ad_id"] == ad_id)
        assert match["critic_findings"] == [
            {"category": "testimonial", "description": "fabricated quote", "confidence": "high"}
        ]
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_api_artifacts_critic_findings_empty_list_when_not_checked(monkeypatch):
    from src import dedupe
    from fastapi.testclient import TestClient
    import uuid

    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    dedupe.save_artifact(
        ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "H"},
        draft_image="assets/x_draft.png",
        metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    try:
        client = TestClient(dashboard.app)
        r = client.get("/api/artifacts")
        body = r.json()
        match = next(a for a in body if a["ad_id"] == ad_id)
        assert match["critic_findings"] == []
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_api_artifacts_surfaces_format_flag(monkeypatch):
    """Prompt 4, Item 4: the flag must reach the card - surface, never filter."""
    from src import dedupe
    from fastapi.testclient import TestClient
    import uuid

    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    dedupe.save_artifact(
        ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
        blueprint={"format": "offer_led"}, generated_copy={"headline": "H"},
        draft_image="assets/x_draft.png",
        metadata={"cta": "Shop", "destination_url": "http://x"},
        format_flag="reference was a 6-product bundle offer",
    )
    try:
        client = TestClient(dashboard.app)
        r = client.get("/api/artifacts")
        assert r.status_code == 200
        body = r.json()
        match = next(a for a in body if a["ad_id"] == ad_id)
        assert match["format_flag"] == "reference was a 6-product bundle offer"
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_api_artifacts_format_flag_empty_string_when_no_mismatch(monkeypatch):
    from src import dedupe
    from fastapi.testclient import TestClient
    import uuid

    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    dedupe.save_artifact(
        ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "H"},
        draft_image="assets/x_draft.png",
        metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    try:
        client = TestClient(dashboard.app)
        r = client.get("/api/artifacts")
        body = r.json()
        match = next(a for a in body if a["ad_id"] == ad_id)
        assert match["format_flag"] == ""
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_put_competitor_category_only_preserves_page_id(monkeypatch):
    import uuid
    from src import dedupe
    from fastapi.testclient import TestClient

    dedupe.init_competitors()
    name = f"__test_{uuid.uuid4().hex[:8]}__"
    cid = dedupe.add_competitor(name, page_id="1936234786698582", category="")
    try:
        client = TestClient(dashboard.app)
        # page_id genuinely absent from the query string - not sent as "".
        r = client.put(f"/api/competitors/{cid}?name={name}&category=body_oil")
        assert r.status_code == 200
        row = next(c for c in dedupe.get_competitors() if c["id"] == cid)
        assert row["page_id"] == "1936234786698582"
        assert row["category"] == "body_oil"
    finally:
        dedupe.delete_competitor(cid)


# ---- Prompt 4, Item 5: /api/brand_settings - palette is DATA, editable from the UI ----

def test_api_brand_settings_get_returns_current_palette(monkeypatch):
    from src import dedupe
    from fastapi.testclient import TestClient

    dedupe.init_brand_settings()
    original = dedupe.get_brand_settings()["palette"]
    try:
        client = TestClient(dashboard.app)
        r = client.get("/api/brand_settings")
        assert r.status_code == 200
        assert r.json()["palette"] == original
    finally:
        dedupe.update_brand_settings(original)


def test_api_brand_settings_post_updates_palette(monkeypatch):
    from src import dedupe
    from fastapi.testclient import TestClient

    dedupe.init_brand_settings()
    original = dedupe.get_brand_settings()["palette"]
    try:
        client = TestClient(dashboard.app)
        r = client.post("/api/brand_settings", json={"palette": "sage, cream, gold"})
        assert r.status_code == 200
        assert r.json()["palette"] == "sage, cream, gold"
        assert dedupe.get_brand_settings()["palette"] == "sage, cream, gold"
    finally:
        dedupe.update_brand_settings(original)
