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
                              edit_mode=True)
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
        "RUN_EDIT_MODE",
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


# ---- /api/run/status ----

def test_api_run_status_local_mode_reports_from_memory(monkeypatch):
    _reset_run_status()
    dashboard._run_status["mode"] = "local"
    dashboard._run_status["running"] = True
    dashboard._run_status["last_summary"] = None

    resp = dashboard.api_run_status()
    import json
    assert json.loads(resp.body) == {"running": True, "last_summary": None}


def test_api_run_status_local_mode_reports_completion(monkeypatch):
    _reset_run_status()
    dashboard._run_status["mode"] = "local"
    dashboard._run_status["running"] = False
    dashboard._run_status["last_summary"] = {"processed": 3, "skipped": 1, "failed": 0}

    resp = dashboard.api_run_status()
    import json
    assert json.loads(resp.body) == {"running": False, "last_summary": {"processed": 3, "skipped": 1, "failed": 0}}


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
    assert json.loads(resp.body) == {"running": False, "last_summary": {"succeeded": 2, "failed": 0}}


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
