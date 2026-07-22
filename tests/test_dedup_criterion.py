"""Verifies the acceptance criterion: zero duplicate alerts across 3 consecutive runs."""
import uuid
from src import pipeline, dedupe


def test_three_consecutive_runs_zero_duplicates(monkeypatch):
    dedupe.init_db()
    # A fixed set of ads returned every run
    fixed_ads = [
        {"ad_id": f"RUN3_{uuid.uuid4().hex[:8]}", "page_name": "Brand",
         "image_url": "http://x/a.jpg", "start_date": "", "destination_url": "", "cta": "", "media_type": "IMAGE"}
        for _ in range(3)
    ]

    # Mock all live stages so no network/spend
    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {"format": "hero", "angle": "a"})
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live", lambda bp: {"headline": "H", "primary_text": "P", "cta": "C"})
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", lambda bp, aid: "draft.png")
    monkeypatch.setattr(pipeline.slack_review, "post_review", lambda *a, **k: {"ts": "1"})
    monkeypatch.setattr(pipeline, "with_retry", lambda fn, **k: fn())
    monkeypatch.setattr(pipeline.scrape, "scrape_ads", lambda name, max_results=5: fixed_ads)
    monkeypatch.setattr(pipeline.dedupe, "get_competitors", lambda: [{"name": "Brand"}])
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: None)

    r1 = pipeline.run_once()
    r2 = pipeline.run_once()
    r3 = pipeline.run_once()

    # Run 1 processes all; runs 2 and 3 must process ZERO (all deduped)
    assert r1["processed"] == 3
    assert r2["processed"] == 0
    assert r3["processed"] == 0
    assert r2["skipped"] == 3 and r3["skipped"] == 3
