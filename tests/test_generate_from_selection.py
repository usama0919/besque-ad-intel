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


# ---- Chunk 6.1, Item 1: the five run-strip toggles reach process_ad intact ----

def test_generate_from_selection_forwards_the_five_toggles_to_process_ad(monkeypatch):
    """A live run produced images with no baked-in copy because these were
    never threaded through at all - process_ad silently ran with its own
    text_in_image=False default. Every value below is flipped from process_ad's
    own default to prove it actually arrives, not just that a default matches
    by coincidence."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    captured = {}

    def fake_process_ad(ad, **kwargs):
        captured.update(kwargs)
        return "processed"
    monkeypatch.setattr(pipeline, "process_ad", fake_process_ad)
    try:
        pipeline.generate_from_selection(
            [ad_id], text_in_image=True, include_product=False, edit_mode=True,
            check_output=True, retheme_colours=False,
        )
        assert captured["text_in_image"] is True
        assert captured["include_product"] is False
        assert captured["edit_mode"] is True
        assert captured["check_output"] is True
        assert captured["retheme_colours"] is False
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_toggle_defaults_match_process_ad():
    """Omitted entirely, generate_from_selection's own defaults must match
    process_ad's exactly - same names, same defaults, nothing invented.

    include_product/edit_mode/retheme_colours default to None, not True/False
    (Task F, point 1, 2026-08-07) - None means "the caller genuinely did not supply
    this," which process_ad normalizes to a concrete bool for its own use while also
    handing the raw None-or-value to the regenerate resolver, so a live override can be
    told apart from a caller that never touched the field at all. text_in_image/
    check_output are unaffected - out of scope for the regenerate precedence fix (see
    process_ad's own docstring)."""
    import inspect
    sig = inspect.signature(pipeline.generate_from_selection)
    assert sig.parameters["text_in_image"].default is False
    assert sig.parameters["include_product"].default is None
    assert sig.parameters["edit_mode"].default is None
    assert sig.parameters["check_output"].default is False
    assert sig.parameters["retheme_colours"].default is None
    assert sig.parameters["realism"].default is None


# ---- item 2 (2026-08-06): realism reaches process_ad - every draft generated through
# this path previously ran with realism=None regardless of what pool.html's operator
# picked, because pool.html had no control for it at all ----

def test_generate_from_selection_forwards_realism_to_process_ad(monkeypatch):
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    captured = {}

    def fake_process_ad(ad, **kwargs):
        captured.update(kwargs)
        return "processed"
    monkeypatch.setattr(pipeline, "process_ad", fake_process_ad)
    try:
        pipeline.generate_from_selection([ad_id], realism="illustrated")
        assert captured["realism"] == "illustrated"
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_realism_omitted_forwards_none(monkeypatch):
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    captured = {}

    def fake_process_ad(ad, **kwargs):
        captured.update(kwargs)
        return "processed"
    monkeypatch.setattr(pipeline, "process_ad", fake_process_ad)
    try:
        pipeline.generate_from_selection([ad_id])
        assert captured["realism"] is None
    finally:
        _cleanup(cid, [ad_id])


# ---- item 4 (2026-08-06): product scope guard - refused BEFORE any paid call, for the
# WHOLE selection, with a clear reason - never a silent per-ad skip. Fully DB-independent
# (every dedupe touchpoint stubbed) so this gives real signal with no Postgres reachable. ----

def _mock_dedupe_for_scope_guard(monkeypatch, product=None):
    monkeypatch.setattr(pipeline.dedupe, "init_db", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "init_artifacts", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "init_scraped_ads", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "init_angles", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "init_products", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "get_product", lambda pid: product)
    monkeypatch.setattr(pipeline.dedupe, "get_angle", lambda aid: None)
    # validate_config is imported LOCALLY inside generate_from_selection
    # (from src.config_check import validate_config), re-resolved from the module's
    # namespace at call time - patching the module attribute here is what actually
    # reaches it, not patching a (nonexistent) pipeline.validate_config.
    from src import config_check
    monkeypatch.setattr(config_check, "validate_config", lambda: None)


def test_generate_from_selection_refuses_out_of_scope_product(monkeypatch):
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))
    _mock_dedupe_for_scope_guard(monkeypatch, product={"id": 2, "name": "Besque Shower Oil"})
    process_ad_calls = []
    monkeypatch.setattr(pipeline, "process_ad", lambda *a, **k: process_ad_calls.append(1) or "processed")

    result = pipeline.generate_from_selection(["AD1", "AD2"], product_id=2)

    assert result["failed"] == 2
    assert result["processed"] == 0
    assert result["by_ad"] == {"AD1": "failed", "AD2": "failed"}
    assert "error" in result and "Besque Shower Oil" in result["error"]
    assert process_ad_calls == []  # refused before any paid call, not one ad even attempted
    assert warnings and warnings[0][0] == "product_scope_refused"
    assert "Besque Shower Oil" in warnings[0][1]


def test_generate_from_selection_reports_refusal_per_ad_via_on_ad_done(monkeypatch):
    """Never a silent skip - the SAME on_ad_done callback dashboard.py's job-progress
    polling relies on must fire "failed" for every ad, not just the aggregate counts."""
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda *a, **k: None)
    _mock_dedupe_for_scope_guard(monkeypatch, product={"id": 2, "name": "Besque Shower Oil"})
    seen = []
    pipeline.generate_from_selection(["AD1"], product_id=2, on_ad_done=lambda aid, r: seen.append((aid, r)))
    assert seen == [("AD1", "failed")]


def test_generate_from_selection_allows_enabled_product(monkeypatch):
    """product_id=1 (Magic Body Oil) must pass the guard and reach the real selection
    loop - proven by get_scraped_ads_by_ad_ids actually being called, not just the
    absence of a refusal."""
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda *a, **k: None)
    _mock_dedupe_for_scope_guard(monkeypatch, product={"id": 1, "name": "Besque Magic Body Oil"})
    calls = []
    monkeypatch.setattr(pipeline.dedupe, "get_scraped_ads_by_ad_ids", lambda ad_ids: calls.append(ad_ids) or {})

    result = pipeline.generate_from_selection(["AD1"], product_id=1)
    assert calls == [["AD1"]]
    assert result["failed"] == 1  # AD1 not found in scraped_ads (mocked empty) - a
    # different, unrelated failure reason, proving the guard itself didn't fire


def test_generate_from_selection_no_product_id_is_unaffected(monkeypatch):
    """product_id=None (no product selected at all) must never trip the guard."""
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda *a, **k: None)
    _mock_dedupe_for_scope_guard(monkeypatch, product=None)
    calls = []
    monkeypatch.setattr(pipeline.dedupe, "get_scraped_ads_by_ad_ids", lambda ad_ids: calls.append(ad_ids) or {})

    pipeline.generate_from_selection(["AD1"])
    assert calls == [["AD1"]]


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


def test_generate_from_selection_regenerate_versions_draft_and_applies_delta_to_stored_prompt(monkeypatch):
    """regenerate=True REBUILDS the image prompt from current code and the artifact's
    stored inputs (2026-08-06 - see pipeline._regenerate_existing_draft), then applies
    the operator's instruction as a delta on top - never a fresh deconstruct/copy run
    (those stay reused from the existing artifact). Versions the outgoing draft first
    (edit_image's scheme, reused), using REAL (unmocked) save_artifact and
    version_current_draft to prove the file is preserved."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
        image_prompt="STORED PROMPT TEXT",
    )
    from src import generate_image_prompt
    import tempfile
    from pathlib import Path as _Path
    tmp_asset_dir = _Path(tempfile.mkdtemp())
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_asset_dir)
    # Simulate an existing draft PNG on disk for this ad, at the exact stem
    # version_current_draft/regenerate_from_stored_prompt both key off.
    (tmp_asset_dir / f"{ad_id}_draft.png").write_bytes(b"OLD-DRAFT-BYTES")

    deconstruct_calls = []
    copy_calls = []
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda **k: deconstruct_calls.append(1) or {"format": "hero", "angle": "a"})
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: copy_calls.append(1) or {"headline": "New"})
    monkeypatch.setattr(pipeline.generate_image_prompt, "regenerate_from_stored_prompt",
                        lambda *a, **k: str(tmp_asset_dir / f"{ad_id}_draft.png"))
    # save_artifact and version_current_draft deliberately NOT mocked.
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True, instruction="fix the bottle")
        assert result["by_ad"][ad_id] == "processed"
        assert deconstruct_calls == [], "regenerate must never call deconstruct"
        assert copy_calls == [], "regenerate must never generate fresh copy"
        assert (tmp_asset_dir / f"{ad_id}_draft_v1.png").exists(), \
            "the pre-regenerate draft must be preserved as a version, not just overwritten"
        assert (tmp_asset_dir / f"{ad_id}_draft_v1.png").read_bytes() == b"OLD-DRAFT-BYTES"
        rows = dedupe.get_artifacts(ad_id)
        assert len(rows) == 1  # replaced in place (DELETE+INSERT), not appended as a second row
        assert rows[0][2]["headline"] == "Old"  # generated_copy carried forward unchanged
        row = dedupe.get_scraped_ads(competitor_id=cid)[0]
        assert row["status"] == "processed"
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_regenerate_rebuilds_prompt_from_current_code(monkeypatch):
    """THE property that was silently false until 2026-08-06: a rule present in CURRENT
    build_image_prompt/brand_rules/compliance code must appear in a regenerated draft's
    prompt, even though that rule postdates the draft's original generation. Proven by
    storing a deliberately stale, rule-free image_prompt on the existing artifact, then
    asserting the prompt actually handed to regenerate_from_stored_prompt is a fresh
    rebuild containing real, current guardrail text the stale stored prompt never had -
    discovered live when the Grüns GLP-1 illustrated-mode fix silently never reached an
    ad that had already been regenerated once before the fix landed."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "hero", "production_style": {"style": "high_spec_studio"}},
        generated_copy={"headline": "Old", "image_subtext": "Sub", "cta": "Shop"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
        image_prompt="STALE PROMPT TEXT WITH NONE OF THE CURRENT RULES IN IT",
        text_in_image=True,
    )
    from src import generate_image_prompt
    import tempfile
    from pathlib import Path as _Path
    tmp_asset_dir = _Path(tempfile.mkdtemp())
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_asset_dir)
    (tmp_asset_dir / f"{ad_id}_draft.png").write_bytes(b"OLD-DRAFT-BYTES")

    captured = {}

    def fake_regenerate(current_image_bytes, stored_prompt, instruction, ad_id, angle_slug=None):
        captured["prompt"] = stored_prompt
        return str(tmp_asset_dir / f"{ad_id}_draft.png")

    monkeypatch.setattr(pipeline.generate_image_prompt, "regenerate_from_stored_prompt", fake_regenerate)
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True, instruction="fix the bottle")
        assert result["by_ad"][ad_id] == "processed"
        rebuilt = captured["prompt"]
        assert "STALE PROMPT TEXT WITH NONE OF THE CURRENT RULES IN IT" not in rebuilt
        assert "STRICT RULES - NEVER VIOLATE" in rebuilt  # brand_rules(), current code
        assert "C1. NO REAL PEOPLE" in rebuilt  # COMPLIANCE_RULES, current code
        assert "9) SOURCE IMAGE IS THE COMPETITOR'S OWN AD" in rebuilt  # edit-mode rule 9
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_regenerate_falls_back_to_normal_generation_when_no_artifact(monkeypatch):
    """regenerate=True with NO existing artifact for this (ad_id, angle_id) must fall
    back to a normal first generation - never fail an ad just for having no history.
    2026-08-06 fix for the live error "regenerate requested but no existing artifact for
    angle_id=None": same root cause as the frozen-prompt bug, this function assumed
    history always exists once regenerate is requested."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    _mock_success(monkeypatch)  # deliberately no existing artifact at all
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True)
        assert result["by_ad"][ad_id] == "processed"
        assert len(dedupe.get_artifacts(ad_id)) == 1
        row = dedupe.get_scraped_ads(competitor_id=cid)[0]
        assert row["status"] == "processed"
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_regenerate_live_input_overrides_stored(monkeypatch):
    """Task F, point 1 (2026-08-07): live operator input for THIS regenerate call must
    win over the stored artifact value - reproduces the exact bug shape ads
    1888339248562394/1194229189228603 hit (stored False/None, operator switched it ON
    live, artifact still ended up True/False from the stored value or a hardcoded
    default because the live value never reached _regenerate_existing_draft at all)."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "hero", "production_style": {"style": "high_spec_studio"}},
        generated_copy={"headline": "Old", "image_subtext": "Sub", "cta": "Shop"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
        image_prompt="STORED PROMPT TEXT",
        include_product=False, retheme_colours=False,
    )
    from src import generate_image_prompt
    import tempfile
    from pathlib import Path as _Path
    tmp_asset_dir = _Path(tempfile.mkdtemp())
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_asset_dir)
    (tmp_asset_dir / f"{ad_id}_draft.png").write_bytes(b"OLD-DRAFT-BYTES")
    monkeypatch.setattr(pipeline.generate_image_prompt, "regenerate_from_stored_prompt",
                        lambda *a, **k: str(tmp_asset_dir / f"{ad_id}_draft.png"))
    try:
        # Operator explicitly turns BOTH toggles ON live, overriding the stored False.
        result = pipeline.generate_from_selection(
            [ad_id], regenerate=True, include_product=True, retheme_colours=True,
        )
        assert result["by_ad"][ad_id] == "processed"
        art = dedupe.get_artifact(ad_id)
        assert art["include_product"] is True, "live True must win over stored False"
        assert art["retheme_colours"] is True, "live True must win over stored False"
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_regenerate_missing_draft_image_falls_back_preserving_edit_mode(monkeypatch):
    """Task F, point 2 (2026-08-07): the artifact ROW exists but its draft image does not
    (ad 1888339248562394 - "regenerate requested but no current draft image could be
    read") - must fall back to a normal first generation, same as the missing-artifact
    case, rather than failing the ad. The fallback must preserve the operator's live
    edit_mode (ad 2577024936146615 - a fallback that silently ran edit_mode=False turned
    a requested clone into a generic ad) - proven here by asserting generate_image is
    actually called with edit_mode=True."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "hero", "production_style": {"style": "high_spec_studio"}},
        generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    from src import generate_image_prompt
    import tempfile
    from pathlib import Path as _Path
    # Deliberately empty - no {ad_id}_draft.png written, simulating the image being gone
    # while the artifact row survives.
    tmp_asset_dir = _Path(tempfile.mkdtemp())
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_asset_dir)

    _mock_success(monkeypatch)
    captured = {}

    def fake_generate_image(bp, aid, product=None, reference_images=None, **k):
        captured.update(k)
        return "draft.png"
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", fake_generate_image)
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True, edit_mode=True)
        assert result["by_ad"][ad_id] == "processed", "must fall back, never fail, on a missing draft image"
        assert captured.get("edit_mode") is True, "the live edit_mode=True must survive the fallback"
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_regenerate_fails_loudly_without_stored_blueprint(monkeypatch):
    """No blueprint on the existing artifact must fail, never silently rebuild a prompt
    from nothing - blueprint is the one truly required input for a rebuild (unlike
    include_product/realism/etc, which have safe defaults)."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint=None, generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    deconstruct_calls = []
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda **k: deconstruct_calls.append(1) or {"format": "hero", "angle": "a"})
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True)
        assert result["by_ad"][ad_id] == "failed"
        assert deconstruct_calls == []
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
