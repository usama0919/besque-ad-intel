"""Single-flight guard on generation (2026-08-21) - only one generation batch
(pipeline.generate_from_selection OR pipeline.run_once) may run at a time across the
WHOLE service, enforced by a Postgres advisory lock (dedupe.generation_single_flight_guard),
never an in-process flag - a second attempt while one is running must return
immediately with a clear message naming who started it and when, never queue and
never fail silently.

Real DB for the lock table itself (dedupe.generation_lock) - the whole point is that
this is enforced at the Postgres level, not mockable away. The two pipeline wrapper
functions are tested by monkeypatching their own `_locked` implementation, isolating
the wrapper's own logic (acquire -> delegate -> release, or refuse) from the huge
existing function bodies those `_locked` names wrap."""
import threading

import pytest

from src import dedupe, pipeline


def _clear_lock():
    """Best-effort: if a previous failed test left the lock held (it shouldn't -
    every acquire in this file happens inside a `with`/try-finally), release it so
    later tests aren't affected by test order."""
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (dedupe.GENERATION_LOCK_KEY,))
        cur.execute("UPDATE generation_lock SET running = false WHERE id = 1")
        conn.commit()


@pytest.fixture(autouse=True)
def _clean_lock_state():
    dedupe.init_generation_lock()
    _clear_lock()
    yield
    _clear_lock()


# ---- dedupe.generation_single_flight_guard itself ----

def test_second_acquire_refused_while_first_holds_it():
    started = threading.Event()
    release = threading.Event()

    def holder():
        with dedupe.generation_single_flight_guard("holder-thread"):
            started.set()
            release.wait(timeout=5)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert started.wait(timeout=5)

    with pytest.raises(dedupe.GenerationAlreadyRunningError) as exc_info:
        with dedupe.generation_single_flight_guard("second-caller"):
            pass
    assert "holder-thread" in str(exc_info.value)

    release.set()
    t.join(timeout=5)


def test_lock_released_after_normal_exit_and_after_exception():
    with dedupe.generation_single_flight_guard("normal-run"):
        pass
    assert dedupe.get_generation_lock_status()["running"] is False

    with pytest.raises(ValueError):
        with dedupe.generation_single_flight_guard("crashing-run"):
            raise ValueError("boom")
    assert dedupe.get_generation_lock_status()["running"] is False

    # Lock genuinely released, not just the status row - a third acquire must succeed.
    with dedupe.generation_single_flight_guard("third-run"):
        pass


def test_status_reports_who_and_when_while_held():
    with dedupe.generation_single_flight_guard("named-run"):
        status = dedupe.get_generation_lock_status()
        assert status["running"] is True
        assert status["started_by"] == "named-run"
        assert status["started_at"] is not None
    assert dedupe.get_generation_lock_status()["running"] is False


# ---- pipeline.generate_from_selection wrapper ----

def test_generate_from_selection_refuses_when_lock_held(monkeypatch):
    def _should_not_run(*a, **k):
        raise AssertionError("_generate_from_selection_locked must not run when refused")
    monkeypatch.setattr(pipeline, "_generate_from_selection_locked", _should_not_run)

    reported = []
    with dedupe.generation_single_flight_guard("other-batch"):
        result = pipeline.generate_from_selection(
            ["AD1", "AD2"], on_ad_done=lambda ad_id, r: reported.append((ad_id, r)),
        )

    assert result["failed"] == 2
    assert result["processed"] == 0
    assert result["by_ad"] == {"AD1": "failed", "AD2": "failed"}
    assert "other-batch" in result["error"]
    assert reported == [("AD1", "failed"), ("AD2", "failed")]


def test_generate_from_selection_delegates_and_releases_lock_on_success(monkeypatch):
    calls = []

    def _fake_locked(ad_ids, **kwargs):
        calls.append(ad_ids)
        assert dedupe.get_generation_lock_status()["running"] is True
        return {"processed": 1, "skipped": 0, "failed": 0, "already_generated": 0, "by_ad": {"AD1": "processed"}}
    monkeypatch.setattr(pipeline, "_generate_from_selection_locked", _fake_locked)

    result = pipeline.generate_from_selection(["AD1"])
    assert calls == [["AD1"]]
    assert result["processed"] == 1
    assert dedupe.get_generation_lock_status()["running"] is False


def test_generate_from_selection_releases_lock_even_if_locked_call_raises(monkeypatch):
    def _fake_locked(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(pipeline, "_generate_from_selection_locked", _fake_locked)

    with pytest.raises(RuntimeError):
        pipeline.generate_from_selection(["AD1"])
    assert dedupe.get_generation_lock_status()["running"] is False


# ---- pipeline.run_once wrapper ----

def test_run_once_refuses_when_lock_held(monkeypatch):
    def _should_not_run(*a, **k):
        raise AssertionError("_run_once_locked must not run when refused")
    monkeypatch.setattr(pipeline, "_run_once_locked", _should_not_run)

    with dedupe.generation_single_flight_guard("a pool send"):
        result = pipeline.run_once(competitor_id=42)

    assert result == {
        "processed": 0, "skipped": 0, "failed": 0, "reference_photo_warning": None,
        "by_competitor": {}, "error": result["error"],
    }
    assert "a pool send" in result["error"]


def test_run_once_delegates_and_releases_lock_on_success(monkeypatch):
    def _fake_locked(**kwargs):
        assert dedupe.get_generation_lock_status()["running"] is True
        return {"processed": 3, "skipped": 0, "failed": 0, "by_competitor": {}}
    monkeypatch.setattr(pipeline, "_run_once_locked", _fake_locked)

    result = pipeline.run_once(competitor_id=42)
    assert result["processed"] == 3
    assert dedupe.get_generation_lock_status()["running"] is False


# ---- The two entry points share ONE lock, not two independent ones ----

def test_generate_from_selection_and_run_once_share_the_same_lock(monkeypatch):
    monkeypatch.setattr(pipeline, "_run_once_locked",
                         lambda **k: (_ for _ in ()).throw(AssertionError("must not run")))

    with dedupe.generation_single_flight_guard("a pool send in progress"):
        result = pipeline.run_once(competitor_id=1)

    assert result["error"] and "a pool send in progress" in result["error"]
