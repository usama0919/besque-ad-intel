"""Tests for the pipeline orchestrator. Live stages are monkeypatched so tests
run with no network, no API spend."""
import uuid
from src import pipeline, dedupe


def test_process_ad_missing_id_is_failed():
    assert pipeline.process_ad({"page_name": "x"}) == "failed"


def test_process_ad_dedupes_seen(monkeypatch):
    dedupe.init_db()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    dedupe.mark_seen(ad_id, "seen")
    ad = {"ad_id": ad_id, "page_name": "seen", "image_url": "x", "start_date": "", "destination_url": ""}
    assert pipeline.process_ad(ad) == "skipped"


def test_process_ad_full_flow_mocked(monkeypatch):
    dedupe.init_db()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "2026-01-01", "destination_url": "http://x"}

    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {"format": "hero", "angle": "a"})
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live", lambda bp: {"headline": "H", "primary_text": "P", "cta": "C"})
    monkeypatch.setattr(pipeline.slack_review, "post_review", lambda *a, **k: {"ts": "123"})

    assert pipeline.process_ad(ad) == "processed"
    assert dedupe.is_new(ad_id) is False  # marked seen after processing


def test_process_ad_failure_isolated(monkeypatch):
    dedupe.init_db()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": ""}

    def boom(url, aid):
        raise RuntimeError("download failed")
    monkeypatch.setattr(pipeline.assets, "download_image", boom)

    # A failure in a stage returns "failed", does not raise
    assert pipeline.process_ad(ad) == "failed"
