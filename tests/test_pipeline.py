"""Tests for the pipeline orchestrator using fake fetch functions (no live calls)."""
import uuid
from src import pipeline


def _fake_fetch_two_ads(competitor):
    uid = uuid.uuid4().hex[:8]
    return [
        {"ad_id": f"PIPE_{uid}_1", "page_name": competitor.get("name", "")},
        {"ad_id": f"PIPE_{uid}_2", "page_name": competitor.get("name", "")},
    ]


def _fetch_that_raises(competitor):
    raise RuntimeError("simulated scrape failure")


def test_run_processes_new_ads():
    summary = pipeline.run_once(fetch_fn=_fake_fetch_two_ads)
    assert summary["processed"] >= 2
    assert summary["failed"] == 0


def test_failed_scrape_skips_cleanly():
    summary = pipeline.run_once(fetch_fn=_fetch_that_raises)
    assert summary == {"processed": 0, "skipped": 0, "failed": 0}


def test_second_run_dedupes():
    fixed = [{"ad_id": "PIPE_FIXED_1", "page_name": "X"},
             {"ad_id": "PIPE_FIXED_2", "page_name": "X"}]

    def fixed_fetch(competitor):
        return fixed

    pipeline.run_once(fetch_fn=fixed_fetch)
    summary = pipeline.run_once(fetch_fn=fixed_fetch)
    assert summary["skipped"] >= 2
    assert summary["processed"] == 0


def test_ad_missing_id_is_failed_not_crash():
    def fetch_bad(competitor):
        return [{"page_name": "no id here"}]
    summary = pipeline.run_once(fetch_fn=fetch_bad)
    assert summary["failed"] >= 1