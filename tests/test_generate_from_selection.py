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
        assert result == {"processed": 1, "skipped": 0, "failed": 0, "already_generated": 0,
                          "by_ad": {ad_id: "processed"}}
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
        assert result == {"processed": 0, "skipped": 0, "failed": 1, "already_generated": 0,
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


def test_generate_from_selection_already_generated_skip_spends_nothing(monkeypatch):
    """Chunk 5, Item 7 fix: the ordering defect is closed by checking for an
    existing artifact BEFORE any paid call. regenerate=False (the default) means
    re-selecting an already-generated ad must return "already_generated" having
    called neither deconstruct nor generate_image - not "processed" with a
    silently discarded result (the old, now-fixed, reported conflict)."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    deconstruct_calls = []
    image_calls = []
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda **k: deconstruct_calls.append(1) or {"format": "hero", "angle": "a"})
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda *a, **k: image_calls.append(1))
    try:
        result = pipeline.generate_from_selection([ad_id])  # regenerate defaults False
        assert result["by_ad"][ad_id] == "already_generated"
        assert result["already_generated"] == 1
        assert deconstruct_calls == [], "must never pay for deconstruct on an already-generated ad"
        assert image_calls == [], "must never reach generate_image on an already-generated ad"
        assert len(dedupe.get_artifacts(ad_id)) == 1  # unchanged - still just the original
        row = dedupe.get_scraped_ads(competitor_id=cid)[0]
        assert row["status"] == "already_generated"
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_regenerate_versions_the_draft_and_writes_new_content(monkeypatch):
    """regenerate=True is the operator's explicit ask (the grid marked this card
    already-generated and they selected it anyway) - it must version the
    outgoing draft (edit_image's own scheme, reused) and actually replace the
    artifact content, using the REAL (unmocked) save_artifact and a REAL
    (unmocked) version_current_draft to prove the file gets preserved."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    from src import generate_image_prompt
    import tempfile
    from pathlib import Path as _Path
    tmp_asset_dir = _Path(tempfile.mkdtemp())
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_asset_dir)
    # Simulate an existing draft PNG on disk for this ad, at the exact stem
    # version_current_draft/generate_image both key off.
    (tmp_asset_dir / f"{ad_id}_draft.png").write_bytes(b"OLD-DRAFT-BYTES")

    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {"format": "hero", "angle": "a"})
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: {"headline": "New", "primary_text": "P",
                                                         "image_subtext": "S", "cta": "C"})
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text, **k: (True, []))
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: str(tmp_asset_dir / f"{aid}_draft.png"))
    monkeypatch.setattr(pipeline.slack_review, "post_review", lambda *a, **k: {"ts": "123"})
    # save_artifact and version_current_draft deliberately NOT mocked.
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True)
        assert result["by_ad"][ad_id] == "processed"
        assert (tmp_asset_dir / f"{ad_id}_draft_v1.png").exists(), \
            "the pre-regenerate draft must be preserved as a version, not just overwritten"
        assert (tmp_asset_dir / f"{ad_id}_draft_v1.png").read_bytes() == b"OLD-DRAFT-BYTES"
        rows = dedupe.get_artifacts(ad_id)
        assert len(rows) == 1  # replaced in place (DELETE+INSERT), not appended as a second row
        row = dedupe.get_scraped_ads(competitor_id=cid)[0]
        assert row["status"] == "processed"
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_reports_progress_via_on_ad_done_callback(monkeypatch):
    """on_ad_done must fire once per ad with its actual result - the mechanism
    dashboard.py's POST /api/generate uses for live per-ad progress (Chunk 5,
    Item 4)."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    _mock_success(monkeypatch)
    seen = []
    try:
        pipeline.generate_from_selection([ad_id], on_ad_done=lambda aid, result: seen.append((aid, result)))
        assert seen == [(ad_id, "processed")]
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_on_ad_done_exception_does_not_abort_run(monkeypatch):
    """A progress-reporting bug must never abort an otherwise-successful generation."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    _mock_success(monkeypatch)

    def boom(ad_id, result):
        raise RuntimeError("progress sink is down")
    try:
        result = pipeline.generate_from_selection([ad_id], on_ad_done=boom)
        assert result["by_ad"][ad_id] == "processed"
    finally:
        _cleanup(cid, [ad_id])


# ---- Item 7b: save_artifact's regenerate param is an explicit per-call override,
# defaulting to preserve today's FORCE_REPROCESS-driven behaviour ----

def test_save_artifact_regenerate_true_replaces_regardless_of_module_flag(monkeypatch):
    ad_id = f"SAV_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(dedupe, "FORCE_REPROCESS", False)  # module flag says "don't"
    dedupe.init_artifacts()
    try:
        dedupe.save_artifact(ad_id=ad_id, page_name="Brand", image_path="x", blueprint={"v": 1},
                             generated_copy={}, draft_image="d1.png", metadata={})
        dedupe.save_artifact(ad_id=ad_id, page_name="Brand", image_path="x", blueprint={"v": 2},
                             generated_copy={}, draft_image="d2.png", metadata={}, regenerate=True)
        rows = dedupe.get_artifacts(ad_id)
        assert len(rows) == 1
        assert rows[0][1]["v"] == 2  # replaced, not left as v==1 or duplicated
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_save_artifact_regenerate_false_no_ops_regardless_of_module_flag(monkeypatch):
    ad_id = f"SAV_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(dedupe, "FORCE_REPROCESS", True)  # module flag says "do"
    dedupe.init_artifacts()
    try:
        dedupe.save_artifact(ad_id=ad_id, page_name="Brand", image_path="x", blueprint={"v": 1},
                             generated_copy={}, draft_image="d1.png", metadata={})
        dedupe.save_artifact(ad_id=ad_id, page_name="Brand", image_path="x", blueprint={"v": 2},
                             generated_copy={}, draft_image="d2.png", metadata={}, regenerate=False)
        rows = dedupe.get_artifacts(ad_id)
        assert len(rows) == 1
        assert rows[0][1]["v"] == 1  # explicit False wins over the module flag - untouched
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


def test_save_artifact_regenerate_none_preserves_module_flag_behaviour(monkeypatch):
    """The default (no regenerate param passed) must behave EXACTLY as before this
    chunk - existing callers (run_once via process_ad with explicit_selection=False)
    are unaffected."""
    ad_id = f"SAV_{uuid.uuid4().hex[:8]}"
    dedupe.init_artifacts()
    try:
        monkeypatch.setattr(dedupe, "FORCE_REPROCESS", False)
        dedupe.save_artifact(ad_id=ad_id, page_name="Brand", image_path="x", blueprint={"v": 1},
                             generated_copy={}, draft_image="d1.png", metadata={})
        dedupe.save_artifact(ad_id=ad_id, page_name="Brand", image_path="x", blueprint={"v": 2},
                             generated_copy={}, draft_image="d2.png", metadata={})
        assert dedupe.get_artifacts(ad_id)[0][1]["v"] == 1  # no-op, FORCE_REPROCESS was False

        monkeypatch.setattr(dedupe, "FORCE_REPROCESS", True)
        dedupe.save_artifact(ad_id=ad_id, page_name="Brand", image_path="x", blueprint={"v": 3},
                             generated_copy={}, draft_image="d3.png", metadata={})
        assert dedupe.get_artifacts(ad_id)[0][1]["v"] == 3  # replaced, FORCE_REPROCESS was True
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()
