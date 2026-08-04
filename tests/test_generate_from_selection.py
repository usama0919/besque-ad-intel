"""Tests for pipeline.generate_from_selection (Chunk 4) - generation driven by an
explicit list of chosen scraped_ads.ad_id values rather than scrape order. All
live stages (deconstruct/copy/compliance/image/Slack) monkeypatched - no network,
no spend. Real DB for scraped_ads/seen_ads/artifacts, uuid-suffixed rows, cleaned
up in finally."""
import uuid
from src import pipeline, dedupe


def _make_competitor():
    dedupe.init_competitors()
    name = f"__test_{uuid.uuid4().hex[:8]}__"
    return dedupe.add_competitor(name, "999999", "")


def _seed_scraped_ad(competitor_id, ad_id=None, page_name="Brand"):
    dedupe.init_scraped_ads()
    ad_id = ad_id or f"SEL_{uuid.uuid4().hex[:8]}"
    raw = {
        "ad_archive_id": ad_id, "page_name": page_name, "media_type": "IMAGE",
        "images": ["http://x/img.jpg"], "ad_delivery_start_time": "2026-01-01",
        "cta_type": "SHOP_NOW", "link_url": "http://x", "ad_creative_bodies": ["body"],
    }
    dedupe.upsert_scraped_ad(ad_id=ad_id, competitor_id=competitor_id, image_url="http://x/img.jpg",
                              raw_meta=raw, media_type="IMAGE")
    return ad_id


def _cleanup(competitor_id, ad_ids):
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM scraped_ads WHERE competitor_id=%s", (competitor_id,))
        cur.execute("DELETE FROM seen_ads WHERE ad_id = ANY(%s)", (ad_ids,))
        cur.execute("DELETE FROM artifacts WHERE ad_id = ANY(%s)", (ad_ids,))
        conn.commit()
    dedupe.delete_competitor(competitor_id)


def _mock_success(monkeypatch):
    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {"format": "hero", "angle": "a"})
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: {"headline": "H", "primary_text": "P",
                                                         "image_subtext": "S", "cta": "C"})
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text, **k: (True, []))
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: "draft.png")
    monkeypatch.setattr(pipeline.slack_review, "post_review", lambda *a, **k: {"ts": "123"})


# ---- Item 8: selection of one ad generates exactly one ----

def test_generate_from_selection_one_ad_generates_exactly_one(monkeypatch):
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    _mock_success(monkeypatch)
    try:
        result = pipeline.generate_from_selection([ad_id])
        assert result == {"processed": 1, "skipped": 0, "failed": 0, "by_ad": {ad_id: "processed"}}
        assert dedupe.is_new(ad_id) is False  # mark_seen ran
        row = dedupe.get_scraped_ads(competitor_id=cid)[0]
        assert row["status"] == "processed"  # item 6: moved off 'pool'
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_no_apify_call(monkeypatch):
    """No fetch happens here - the pool is already stored. scrape.scrape_ads_with_raw
    must never be called from this path."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    _mock_success(monkeypatch)

    def must_not_call(*a, **k):
        raise AssertionError("generate_from_selection must never call Apify")
    monkeypatch.setattr(pipeline.scrape, "scrape_ads_with_raw", must_not_call)
    monkeypatch.setattr(pipeline.scrape, "scrape_ads", must_not_call)
    try:
        result = pipeline.generate_from_selection([ad_id])
        assert result["processed"] == 1
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_unknown_ad_id_recorded_failed_not_raised():
    """One bad id in a multi-ad selection must not abort the rest."""
    cid = _make_competitor()
    try:
        result = pipeline.generate_from_selection(["NO_SUCH_AD_ID_XYZ"])
        assert result == {"processed": 0, "skipped": 0, "failed": 1,
                           "by_ad": {"NO_SUCH_AD_ID_XYZ": "failed"}}
    finally:
        dedupe.delete_competitor(cid)


# ---- Item 5: the stop check before the paid Gemini call must be reachable here ----

def test_generate_from_selection_stop_before_generate_is_reachable(monkeypatch):
    """Same guarantee process_ad already provides run_once (7b38414) - a stop
    request must be checked immediately before generate_image, not missed for the
    cost of one full image generation, on THIS path too."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {"format": "hero", "angle": "a"})
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: {"headline": "H", "primary_text": "P",
                                                         "image_subtext": "S", "cta": "C"})
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text, **k: (True, []))
    image_calls = []
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda *a, **k: image_calls.append(1))
    # First call is generate_from_selection's own between-ads check (must pass, so
    # process_ad actually gets called); the second is process_ad's own internal
    # check immediately before generate_image - THAT one must fire and stop it.
    calls = {"n": 0}

    def stop_only_before_generate():
        calls["n"] += 1
        return calls["n"] > 1
    try:
        result = pipeline.generate_from_selection([ad_id], should_stop=stop_only_before_generate)
        assert image_calls == [], "generate_image must never be reached once should_stop() is True"
        assert result["by_ad"][ad_id] == "skipped"
        assert result["skipped"] == 1
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_stop_between_ads_halts_remaining_selection(monkeypatch):
    cid = _make_competitor()
    ad_id_1 = _seed_scraped_ad(cid)
    ad_id_2 = _seed_scraped_ad(cid)
    _mock_success(monkeypatch)
    calls = {"n": 0}

    def stop_after_first():
        calls["n"] += 1
        return calls["n"] > 1
    try:
        result = pipeline.generate_from_selection([ad_id_1, ad_id_2], should_stop=stop_after_first)
        assert ad_id_1 in result["by_ad"]
        assert ad_id_2 not in result["by_ad"]  # never reached
    finally:
        _cleanup(cid, [ad_id_1, ad_id_2])


# ---- Item 6: status transitions ----

def test_generate_from_selection_status_transitions_through_generating_to_terminal(monkeypatch):
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    seen_statuses = []
    orig_update = dedupe.update_scraped_ad_status
    monkeypatch.setattr(pipeline.dedupe, "update_scraped_ad_status",
                        lambda aid, comp_id, status: (seen_statuses.append(status), orig_update(aid, comp_id, status)))
    _mock_success(monkeypatch)
    try:
        pipeline.generate_from_selection([ad_id])
        assert seen_statuses == ["generating", "processed"]
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_status_reflects_failure(monkeypatch):
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {"format": "hero", "angle": "a"})
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: {"headline": "H", "primary_text": "P",
                                                         "image_subtext": "S", "cta": "C"})
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text, **k: (False, ["x"]))
    monkeypatch.setattr(pipeline.dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda *a, **k: None)
    try:
        result = pipeline.generate_from_selection([ad_id])
        assert result["by_ad"][ad_id] == "failed"
        row = dedupe.get_scraped_ads(competitor_id=cid)[0]
        assert row["status"] == "failed"
    finally:
        _cleanup(cid, [ad_id])


# ---- Item 7: explicit selection overrides the seen_ads skip, but NOT save_artifact's
# own separate gate - demonstrating the reported (not worked around) conflict ----

def test_generate_from_selection_overrides_seen_ads_skip(monkeypatch):
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.mark_seen(ad_id, "Brand")  # already seen, no artifact saved for it though
    assert dedupe.is_new(ad_id) is False
    _mock_success(monkeypatch)
    try:
        result = pipeline.generate_from_selection([ad_id])
        # explicit_selection=True bypassed the seen_ads skip - it actually ran,
        # not a silent "skipped" for being already seen.
        assert result["by_ad"][ad_id] == "processed"
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_does_not_bypass_save_artifact_gate_reported_conflict(monkeypatch):
    """Documents the real, reported gap: explicit_selection bypasses process_ad's
    seen_ads check, but save_artifact has its OWN separate gate (an existing
    artifacts row for this (ad_id, angle_id) + FORCE_REPROCESS unset -> silent
    no-op). Re-selecting an ad that already has a saved artifact does NOT
    produce a new artifact row without FORCE_REPROCESS=1 - proven here with the
    REAL (unmocked) save_artifact, not the usual _mock_success stand-in."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    before_rows = len(dedupe.get_artifacts(ad_id))
    assert before_rows == 1

    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {"format": "hero", "angle": "a"})
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: {"headline": "New", "primary_text": "P",
                                                         "image_subtext": "S", "cta": "C"})
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text, **k: (True, []))
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: "draft.png")
    monkeypatch.setattr(pipeline.slack_review, "post_review", lambda *a, **k: {"ts": "123"})
    # save_artifact deliberately NOT mocked - that's the entire point of this test.
    try:
        result = pipeline.generate_from_selection([ad_id])
        # explicit_selection got PAST the seen_ads skip and process_ad reports
        # "processed" (save_artifact's own early-return isn't visible in the
        # return value at all) - but no NEW artifact row was actually written.
        assert result["by_ad"][ad_id] == "processed"
        after_rows = len(dedupe.get_artifacts(ad_id))
        assert after_rows == before_rows == 1, (
            "save_artifact's own gate silently no-ops without FORCE_REPROCESS=1, "
            "even though explicit_selection bypassed the seen_ads check - the "
            "reported conflict, not a regression in this test"
        )
    finally:
        _cleanup(cid, [ad_id])
