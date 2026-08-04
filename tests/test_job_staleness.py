"""Tests for fetch_jobs/generate_jobs staleness self-recovery - a background
thread that dies before calling finish_*_job must not block that competitor's
fetches forever (fetch_jobs, via try_start_fetch_job) or leave a poller
watching 'running' forever (generate_jobs, via get_generate_job). This is what
actually happened live on 2026-08-04 for competitor 1 after the missing-Pillow
crash. Real DB rows, cleaned up in finally. fetch_jobs.competitor_id has no FK
constraint, so arbitrary fake ids are safe to use without a real competitor row."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from src import dedupe


def _seed_fetch_job(competitor_id, seconds_ago):
    dedupe.init_fetch_jobs()
    started = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO fetch_jobs (competitor_id, status, result, error, started_at, finished_at)
               VALUES (%s, 'running', NULL, NULL, %s, NULL)
               ON CONFLICT (competitor_id) DO UPDATE
               SET status='running', result=NULL, error=NULL, started_at=%s, finished_at=NULL""",
            (competitor_id, started, started),
        )
        conn.commit()


def _seed_generate_job(job_id, seconds_ago, ad_ids=None):
    dedupe.init_generate_jobs()
    started = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO generate_jobs (id, status, ad_ids, progress, result, error, stop_requested, started_at, finished_at)
               VALUES (%s, 'running', %s, '{}'::jsonb, NULL, NULL, false, %s, NULL)""",
            (job_id, json.dumps(ad_ids or []), started),
        )
        conn.commit()


def _cleanup_fetch_job(competitor_id):
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM fetch_jobs WHERE competitor_id=%s", (competitor_id,))
        conn.commit()


def _cleanup_generate_job(job_id):
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM generate_jobs WHERE id=%s", (job_id,))
        conn.commit()


# ---- fetch_jobs: try_start_fetch_job reclaims a stale slot ----

def test_try_start_fetch_job_reclaims_a_stale_running_row():
    cid = 900001
    _seed_fetch_job(cid, seconds_ago=dedupe.FETCH_JOB_STALE_SECONDS + 60)
    try:
        assert dedupe.try_start_fetch_job(cid) is True
        job = dedupe.get_fetch_job(cid)
        assert job["status"] == "running"  # freshly reclaimed, not stale anymore
    finally:
        _cleanup_fetch_job(cid)


def test_try_start_fetch_job_still_blocks_a_fresh_running_row():
    cid = 900002
    _seed_fetch_job(cid, seconds_ago=5)  # well under the timeout
    try:
        assert dedupe.try_start_fetch_job(cid) is False
    finally:
        _cleanup_fetch_job(cid)


# ---- fetch_jobs: get_fetch_job self-heals on read ----

def test_get_fetch_job_self_heals_a_stale_running_row_and_persists_it():
    cid = 900003
    _seed_fetch_job(cid, seconds_ago=dedupe.FETCH_JOB_STALE_SECONDS + 60)
    try:
        job = dedupe.get_fetch_job(cid)
        assert job["status"] == "error"
        assert "stale" in job["error"]
        assert job["result"] is None
        # persisted, not a read-time illusion - a second independent read agrees
        again = dedupe.get_fetch_job(cid)
        assert again["status"] == "error"
    finally:
        _cleanup_fetch_job(cid)


def test_get_fetch_job_does_not_touch_a_fresh_running_row():
    cid = 900004
    _seed_fetch_job(cid, seconds_ago=5)
    try:
        job = dedupe.get_fetch_job(cid)
        assert job["status"] == "running"
        assert job["error"] is None
    finally:
        _cleanup_fetch_job(cid)


# ---- generate_jobs: get_generate_job self-heals on read ----

def test_get_generate_job_self_heals_a_stale_running_row_and_persists_it():
    job_id = f"STALE_{uuid.uuid4().hex[:8]}"
    _seed_generate_job(job_id, seconds_ago=dedupe.GENERATE_JOB_STALE_SECONDS + 60, ad_ids=["A1"])
    try:
        job = dedupe.get_generate_job(job_id)
        assert job["status"] == "error"
        assert "stale" in job["error"]
        assert job["result"] is None
        again = dedupe.get_generate_job(job_id)
        assert again["status"] == "error"
    finally:
        _cleanup_generate_job(job_id)


def test_get_generate_job_does_not_touch_a_fresh_running_row():
    job_id = f"STALE_{uuid.uuid4().hex[:8]}"
    _seed_generate_job(job_id, seconds_ago=5, ad_ids=["A1"])
    try:
        job = dedupe.get_generate_job(job_id)
        assert job["status"] == "running"
        assert job["error"] is None
    finally:
        _cleanup_generate_job(job_id)
