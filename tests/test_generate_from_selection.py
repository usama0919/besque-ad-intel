"""Tests for pipeline.generate_from_selection (Chunk 4) - generation driven by an
explicit list of chosen scraped_ads.ad_id values rather than scrape order. All
live stages (deconstruct/copy/compliance/image/Slack) monkeypatched - no network,
no spend. Real DB for scraped_ads/seen_ads/artifacts, uuid-suffixed rows, cleaned
up in finally."""
import uuid
import pytest
from src import pipeline, dedupe

# 2026-08-19 (pool-send routing fix): explicit_selection is exclusively pool send
# (generate_from_selection, dashboard.py's POST /api/generate) - there is no other
# production caller. Pool send is now a permanent fresh-generation entry point, so
# process_ad no longer routes into _regenerate_existing_draft/regenerate_from_stored_prompt
# under any value of `regenerate`. The ten tests below assert the OLD contract for that
# exact call path (deconstruct/copy skipped, regenerate_from_stored_prompt invoked, stored
# blueprint/prompt reused) and are now false statements about the only real caller -
# skipped, not deleted or rewritten, pending the commit-2 decision on whether a dedicated
# (non-pool) regenerate entry point gets built and, if so, what re-targets this coverage.
_POOL_ROUTING_FIX_SKIP_REASON = (
    "2026-08-19 pool-send routing fix: explicit_selection is exclusively pool send, which "
    "is now a permanent fresh-generation entry point - process_ad no longer calls "
    "_regenerate_existing_draft/regenerate_from_stored_prompt under any `regenerate` value, "
    "so this test's asserted contract (deconstruct/copy skipped, stored prompt/blueprint "
    "reused) is no longer true of the only production caller. Retained, not deleted or "
    "rewritten, pending the commit-2 decision on a dedicated regenerate entry point."
)

# 2026-08-19 (pool-send routing fix, second pass): the "Item 7a" already_generated skip
# in process_ad - `if explicit_selection and not regenerate: existing =
# dedupe.get_artifact(...); if existing: return "already_generated"` - is REMOVED
# structurally, not gated behind another conditional. Pool send is now unconditionally a
# fresh generation for every selected ad, including one that already has an artifact - it
# must run deconstruct/copy/image, never skip and spend nothing. The test below asserts
# exactly the removed contract for the only production caller (pool send /
# generate_from_selection) - skipped, not deleted or rewritten, for the same reason as the
# ten above.
_POOL_ROUTING_FIX_ALREADY_GENERATED_SKIP_REASON = (
    "2026-08-19 pool-send routing fix (second pass): the already_generated skip was "
    "removed from process_ad structurally - pool send is now unconditionally a fresh "
    "generation, so an ad with an existing artifact runs deconstruct/copy/image like any "
    "other selected ad, never skips and spends nothing. This test asserts the removed "
    "contract for the only production caller (pool send / generate_from_selection). "
    "Retained, not deleted or rewritten, pending the commit-2 decision on a dedicated "
    "regenerate/already-generated-aware entry point, if one is built."
)


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


# ---- BatchAdConfig (2026-08-18) ----
#
# Two DISTINCT things are tested below, deliberately not conflated into one test, after
# a mutation check (build cfg ONCE outside generate_from_selection's per-ad loop instead
# of fresh per ad, then re-run both tests) showed they exercise different mechanisms:
#
# 1. test_..._resolves_realism_per_ad_from_production_style: realism="(auto)" (None) for
#    BOTH ads - resolution to a concrete register happens entirely inside
#    build_image_prompt/generate_image from that call's OWN blueprint.production_style,
#    a mechanism that has never depended on BatchAdConfig at all (confirmed: cfg.realism
#    stays None for every ad in this scenario whether cfg is built once or fresh per ad,
#    since no per_ad_overrides ever sets it). This test still fails on a genuine
#    per-ad-blueprint-threading bug (e.g. every ad accidentally reusing ad 1's blueprint)
#    - it's real coverage, just not of the freeze mechanism specifically.
# 2. test_..._resolves_realism_per_ad_from_per_ad_override: the freeze itself. Two ads,
#    per_ad_overrides sets a DIFFERENT explicit realism per ad_id. Verified by the same
#    mutation (cfg built once, using whichever ad's override happened to be in scope) -
#    this one DOES fail: ad 2 silently receives ad 1's override instead of its own. This
#    is the test that actually proves "resolved once per ad, never re-read from shared
#    state" - test 1 alone would not have caught that regression.
#
# Both are fully DB-independent (dedupe.get_scraped_ads_by_ad_ids/init_*/get_product/
# get_angle stubbed, same pattern as _mock_dedupe_for_scope_guard above) so they can run
# with no Postgres reachable, unlike this file's real-DB tests elsewhere.

class _FakeGenaiClient:
    def __init__(self, *a, **k):
        self.models = self

    def generate_content(self, model, contents, config=None):
        part = type("Part", (), {"inline_data": type("Data", (), {"data": b"fake-png-bytes"})()})()
        candidate = type("Candidate", (), {"content": type("Content", (), {"parts": [part]})()})()
        return type("Response", (), {"candidates": [candidate]})()


def _mock_dedupe_fully_db_independent(monkeypatch, rows):
    """rows: {ad_id: raw_ad_dict} - raw_ad_dict is handed straight through as the mapped
    ad (scrape._map_ad stubbed to identity), so it must already look like scrape._map_ad's
    own output ({"ad_id", "page_name", "image_url", ...})."""
    for fn in ("init_db", "init_artifacts", "init_scraped_ads", "init_angles",
               "init_angle_language", "init_products", "init_pipeline_warnings"):
        monkeypatch.setattr(pipeline.dedupe, fn, lambda: None)
    from src import config_check
    monkeypatch.setattr(config_check, "validate_config", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "get_product", lambda pid: None)
    monkeypatch.setattr(pipeline.dedupe, "get_angle", lambda aid: None)
    monkeypatch.setattr(pipeline.dedupe, "get_artifact", lambda *a, **k: None)
    # mark_seen runs unconditionally near the end of process_ad's success path - missed
    # on the first pass of writing this helper, which let it fall through to the REAL
    # dedupe.mark_seen and hit whatever DATABASE_URL is actually configured. Under
    # pytest that's conftest.py's forced port-5433 (refused, safe) - but a plain script
    # bypassing conftest has no such guard, which is exactly what happened during this
    # task's own verification and wrote two rows into the real seen_ads table (cleaned
    # up by hand afterward). Mocked here so this mistake can't recur via this helper.
    monkeypatch.setattr(pipeline.dedupe, "mark_seen", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.dedupe, "get_scraped_ads_by_ad_ids",
                        lambda ad_ids: {aid: {"raw_meta": rows[aid], "competitor_id": 1}
                                        for aid in ad_ids if aid in rows})
    monkeypatch.setattr(pipeline.dedupe, "update_scraped_ad_status", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: None)
    monkeypatch.setattr(pipeline.scrape, "_map_ad", lambda raw_meta: raw_meta)
    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: {"headline": "H", "primary_text": "P",
                                                         "image_subtext": "S", "cta": "C"})
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text, **k: (True, []))
    monkeypatch.setattr(pipeline.slack_review, "post_review", lambda *a, **k: {"ts": "123"})


def _mock_genai(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.generate_image_prompt, "genai",
                        type("obj", (), {"Client": _FakeGenaiClient}))
    monkeypatch.setattr(pipeline.generate_image_prompt, "ASSET_DIR", tmp_path)


def test_generate_from_selection_resolves_realism_per_ad_from_production_style(monkeypatch, tmp_path):
    """Two ads, one selection, realism left at "(auto)" (None) for both - one reference
    was detected illustrated, the other ugc. Each ad's resolved register must match ITS
    OWN reference, never the other ad's, and never collapse to one shared value for the
    whole batch. Style names are validator.production_styles()'s real three values
    (high_spec/illustrated/ugc) - confirmed by direct inspection, not guessed.

    NOTE: verified by mutation (see this section's header comment) that this test does
    NOT depend on BatchAdConfig's per-ad freeze - realism stays None either way here, so
    the differentiation comes purely from build_image_prompt reading each call's own
    blueprint. Kept as real, separate coverage of per-ad blueprint threading; see
    test_generate_from_selection_resolves_realism_per_ad_from_per_ad_override below for
    the freeze itself."""
    ad_id_1, ad_id_2 = "AD1", "AD2"
    blueprints = {
        ad_id_1: {"format": "hero", "angle": "a", "production_style": {"style": "illustrated"}},
        ad_id_2: {"format": "hero", "angle": "a", "production_style": {"style": "ugc"}},
    }
    rows = {
        ad_id_1: {"ad_id": ad_id_1, "page_name": "Brand", "image_url": "http://x/img.jpg"},
        ad_id_2: {"ad_id": ad_id_2, "page_name": "Brand", "image_url": "http://x/img.jpg"},
    }
    _mock_dedupe_fully_db_independent(monkeypatch, rows)
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda ad_id=None, **k: blueprints[ad_id])
    _mock_genai(monkeypatch, tmp_path)

    resolved_prompts = {}

    def on_ad_done(ad_id, result):
        # Sequential processing only (see generate_image.last_prompt's own documented
        # single-shared-attribute caveat) - captured immediately after THIS ad's
        # process_ad call returns, before the next ad's call can overwrite it.
        resolved_prompts[ad_id] = pipeline.generate_image_prompt.generate_image.last_prompt

    result = pipeline.generate_from_selection([ad_id_1, ad_id_2], on_ad_done=on_ad_done)
    assert result["processed"] == 2

    style_guidance = pipeline.generate_image_prompt_writer.STYLE_GUIDANCE
    assert style_guidance["illustrated"] in resolved_prompts[ad_id_1]
    assert style_guidance["ugc"] in resolved_prompts[ad_id_2]
    assert style_guidance["ugc"] not in resolved_prompts[ad_id_1]
    assert style_guidance["illustrated"] not in resolved_prompts[ad_id_2]


def test_generate_from_selection_resolves_realism_per_ad_from_per_ad_override(monkeypatch, tmp_path):
    """The per-ad freeze itself: per_ad_overrides sets a DIFFERENT explicit realism per
    ad_id, on blueprints that don't declare a production_style at all (so there's no
    blueprint-driven fallback that could accidentally make this pass for the wrong
    reason - only the override can be the source of a resolved style here). If
    BatchAdConfig were built once for the whole selection (using whichever ad's override
    happened to be in scope at that point) instead of fresh per ad inside the loop, ad 2
    would silently receive ad 1's override - confirmed live during this task by
    temporarily moving the cfg construction outside generate_from_selection's loop and
    re-running: this test fails under that mutation, where the blueprint-only test above
    does not."""
    ad_id_1, ad_id_2 = "AD1", "AD2"
    blueprint = {"format": "hero", "angle": "a"}  # no production_style on either ad
    rows = {
        ad_id_1: {"ad_id": ad_id_1, "page_name": "Brand", "image_url": "http://x/img.jpg"},
        ad_id_2: {"ad_id": ad_id_2, "page_name": "Brand", "image_url": "http://x/img.jpg"},
    }
    _mock_dedupe_fully_db_independent(monkeypatch, rows)
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: dict(blueprint))
    _mock_genai(monkeypatch, tmp_path)

    resolved_prompts = {}

    def on_ad_done(ad_id, result):
        resolved_prompts[ad_id] = pipeline.generate_image_prompt.generate_image.last_prompt

    result = pipeline.generate_from_selection(
        [ad_id_1, ad_id_2],
        per_ad_overrides={ad_id_1: {"realism": "illustrated"}, ad_id_2: {"realism": "ugc"}},
        on_ad_done=on_ad_done,
    )
    assert result["processed"] == 2

    style_guidance = pipeline.generate_image_prompt_writer.STYLE_GUIDANCE
    assert style_guidance["illustrated"] in resolved_prompts[ad_id_1]
    assert style_guidance["ugc"] in resolved_prompts[ad_id_2]
    assert style_guidance["ugc"] not in resolved_prompts[ad_id_1]
    assert style_guidance["illustrated"] not in resolved_prompts[ad_id_2]


# ---- item 4 (2026-08-06): product scope guard - refused BEFORE any paid call, for the
# WHOLE selection, with a clear reason - never a silent per-ad skip. Fully DB-independent
# (every dedupe touchpoint stubbed) so this gives real signal with no Postgres reachable. ----

def _mock_dedupe_for_scope_guard(monkeypatch, product=None):
    monkeypatch.setattr(pipeline.dedupe, "init_db", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "init_artifacts", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "init_scraped_ads", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "init_angles", lambda: None)
    # init_angle_language was added to generate_from_selection after this helper was
    # written - missing stub here made every test using this helper (including the
    # pre-existing product-scope-guard ones) silently depend on a reachable :5433 test
    # Postgres, defeating the "fully DB-independent" purpose stated in this file's own
    # module docstring.
    monkeypatch.setattr(pipeline.dedupe, "init_angle_language", lambda: None)
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


# ---- Reference photo total-fetch-failure guard - refused BEFORE any paid call, for the
# WHOLE selection, same shape as the product scope guard above. A total failure (every
# configured key failed to fetch) means include_product has nothing to substitute the
# Besque bottle with; a partial failure (at least one key fetched) must NOT refuse - that
# case still has a real photo to work from. Fully DB-independent, same reasoning as the
# product scope guard tests above. ----

def test_generate_from_selection_refuses_when_all_reference_images_fail(monkeypatch):
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))
    _mock_dedupe_for_scope_guard(monkeypatch, product={"id": 1, "name": "Besque Magic Body Oil"})
    monkeypatch.setattr(
        pipeline, "fetch_reference_images",
        lambda product: ([], ("reference_photo_fetch_failed",
                               "Product 'Besque Magic Body Oil' (id=1): 2 of 2 configured "
                               "reference image(s) failed to fetch - k1.png: 403; k2.png: 403")),
    )
    process_ad_calls = []
    monkeypatch.setattr(pipeline, "process_ad", lambda *a, **k: process_ad_calls.append(1) or "processed")

    result = pipeline.generate_from_selection(["AD1", "AD2"], product_id=1)

    assert result["failed"] == 2
    assert result["processed"] == 0
    assert result["by_ad"] == {"AD1": "failed", "AD2": "failed"}
    assert "error" in result and "Besque Magic Body Oil" in result["error"]
    assert "re-auth" in result["error"].lower() or "credentials" in result["error"].lower()
    assert process_ad_calls == []  # refused before any paid call, not one ad even attempted
    kinds = [k for k, _ in warnings]
    assert "reference_photo_fetch_failed" in kinds  # the original warning is still recorded
    assert "reference_photo_fetch_refused" in kinds  # AND the refusal itself


def test_generate_from_selection_proceeds_on_partial_reference_fetch_failure(monkeypatch):
    """At least one configured image fetched - must NOT refuse, only warn (unchanged
    behaviour) - there is still a real photo for Gemini to work from."""
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda *a, **k: None)
    _mock_dedupe_for_scope_guard(monkeypatch, product={"id": 1, "name": "Besque Magic Body Oil"})
    monkeypatch.setattr(
        pipeline, "fetch_reference_images",
        lambda product: ([b"one-real-photo"],
                          ("reference_photo_fetch_failed",
                           "Product 'Besque Magic Body Oil' (id=1): 1 of 2 configured "
                           "reference image(s) failed to fetch - k2.png: 403")),
    )
    calls = []
    monkeypatch.setattr(pipeline.dedupe, "get_scraped_ads_by_ad_ids", lambda ad_ids: calls.append(ad_ids) or {})

    result = pipeline.generate_from_selection(["AD1"], product_id=1)
    assert calls == [["AD1"]]  # reached the real selection loop - never refused
    assert "error" not in result


def test_generate_from_selection_reference_guard_skipped_when_include_product_false(monkeypatch):
    """include_product=False for the whole batch - no bottle is being composited in, so
    a total reference-fetch failure is irrelevant and must not refuse."""
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda *a, **k: None)
    _mock_dedupe_for_scope_guard(monkeypatch, product={"id": 1, "name": "Besque Magic Body Oil"})
    monkeypatch.setattr(
        pipeline, "fetch_reference_images",
        lambda product: ([], ("reference_photo_fetch_failed", "all failed")),
    )
    calls = []
    monkeypatch.setattr(pipeline.dedupe, "get_scraped_ads_by_ad_ids", lambda ad_ids: calls.append(ad_ids) or {})

    result = pipeline.generate_from_selection(["AD1"], product_id=1, include_product=False)
    assert calls == [["AD1"]]
    assert "error" not in result


def test_generate_from_selection_reference_guard_ignores_no_reference_photo_kind(monkeypatch):
    """"no_reference_photo" (nothing configured at all) is a config gap, not a fetch
    failure - must never trip this guard, only the existing warning-only path."""
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda *a, **k: None)
    _mock_dedupe_for_scope_guard(monkeypatch, product={"id": 1, "name": "Besque Magic Body Oil"})
    monkeypatch.setattr(
        pipeline, "fetch_reference_images",
        lambda product: ([], ("no_reference_photo", "Product 'Besque Magic Body Oil' (id=1) has no reference images configured")),
    )
    calls = []
    monkeypatch.setattr(pipeline.dedupe, "get_scraped_ads_by_ad_ids", lambda ad_ids: calls.append(ad_ids) or {})

    result = pipeline.generate_from_selection(["AD1"], product_id=1)
    assert calls == [["AD1"]]
    assert "error" not in result


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


# ---- used_headlines (2026-08-11, same-run copy convergence fix): a shared, run-scoped
# list threaded through process_ad into generate_copy_live - real process_ad code, not a
# re-mocked simulation. generate_copy_live itself is spied on (not fully replaced) so the
# ACTUAL append-after-compliance-pass logic in process_ad runs for real. ----

def _mock_success_with_copy_spy(monkeypatch, headline="H", image_subtext="S"):
    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {"format": "hero", "angle": "a"})
    calls = []

    def spy(bp, product=None, **k):
        calls.append(k.get("used_headlines"))
        return {"headline": headline, "primary_text": "P", "image_subtext": image_subtext, "cta": "C"}
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live", spy)
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text, **k: (True, []))
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: "draft.png")
    monkeypatch.setattr(pipeline.slack_review, "post_review", lambda *a, **k: {"ts": "123"})
    return calls


def test_generate_from_selection_single_ad_sees_no_used_headlines(monkeypatch):
    """A single-ad run must stay byte-identical to before this feature existed - the
    empty run-scoped list means generate_copy_live never even receives the kwarg (see
    process_ad's own `if used_headlines:` guard before adding it to copy_kwargs)."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    calls = _mock_success_with_copy_spy(monkeypatch)
    try:
        result = pipeline.generate_from_selection([ad_id])
        assert result["processed"] == 1
        assert len(calls) == 1
        assert calls[0] is None  # used_headlines kwarg never even passed - empty list is falsy
    finally:
        _cleanup(cid, [ad_id])


def test_generate_from_selection_second_ad_sees_first_ads_accepted_copy(monkeypatch):
    """The second ad's generate_copy_live call must receive the first ad's own
    headline/image_subtext once it passed compliance - real process_ad appending logic,
    not a mocked simulation."""
    cid = _make_competitor()
    ad1 = _seed_scraped_ad(cid)
    ad2 = _seed_scraped_ad(cid)
    calls = _mock_success_with_copy_spy(monkeypatch, headline="Go Jumbo & Save", image_subtext="7 oils. One blend.")
    try:
        result = pipeline.generate_from_selection([ad1, ad2])
        assert result["processed"] == 2
        assert len(calls) == 2
        assert calls[0] is None  # first ad: nothing used yet
        assert calls[1] == [{"headline": "Go Jumbo & Save", "image_subtext": "7 oils. One blend."}]
    finally:
        _cleanup(cid, [ad1, ad2])


def test_generate_from_selection_used_headlines_is_one_shared_list_object(monkeypatch):
    """generate_from_selection must create ONE list for the whole call and hand the SAME
    object to every process_ad invocation - not a fresh list per ad, which would silently
    reset awareness back to empty every time."""
    cid = _make_competitor()
    ad1 = _seed_scraped_ad(cid)
    ad2 = _seed_scraped_ad(cid)
    captured = []
    monkeypatch.setattr(pipeline, "process_ad",
                        lambda ad, **k: captured.append(k.get("used_headlines")) or "processed")
    try:
        pipeline.generate_from_selection([ad1, ad2])
        assert len(captured) == 2
        assert captured[0] is captured[1]  # same list object, not two independent empty lists
    finally:
        _cleanup(cid, [ad1, ad2])


def test_generate_from_selection_failed_compliance_does_not_add_to_used_headlines(monkeypatch):
    """A rejected attempt's copy must never be recorded as "already used" - ad2 must see
    an EMPTY used_headlines, proving ad1's rejected copy (which failed on every retry)
    was never appended, only copy that actually passed compliance would be."""
    cid = _make_competitor()
    ad1 = _seed_scraped_ad(cid)
    ad2 = _seed_scraped_ad(cid)
    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {"format": "hero", "angle": "a"})
    calls = []

    def spy(bp, product=None, **k):
        calls.append(k.get("used_headlines"))
        return {"headline": "Rejected Headline", "primary_text": "P",
                "image_subtext": "Rejected sub", "cta": "C"}
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live", spy)
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text, **k: (False, ["bad"]))
    try:
        result = pipeline.generate_from_selection([ad1, ad2])
        assert result["failed"] == 2
        assert len(calls) == 4  # MAX_COPY_ATTEMPTS=2 retries, per ad, both always failing
        assert all(c is None for c in calls)  # never once saw a used_headlines entry
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM pipeline_warnings WHERE detail LIKE %s OR detail LIKE %s",
                        (f"%{ad1}%", f"%{ad2}%"))
            conn.commit()
        _cleanup(cid, [ad1, ad2])


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


@pytest.mark.skip(reason=_POOL_ROUTING_FIX_ALREADY_GENERATED_SKIP_REASON)
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


@pytest.mark.skip(reason=_POOL_ROUTING_FIX_SKIP_REASON)
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


# ---- Critic gate on regenerate (2026-08-10) - CHECK-ONLY, no retry. See
# pipeline._regenerate_existing_draft's own docstring for why: regenerate_from_stored_prompt
# has no hook for a second, critic-feedback delta on top of the operator's own delta, and
# forcing one in risks the artifact-1136 failure mode (a prompt that simultaneously demands
# and forbids the same element). ----

@pytest.mark.skip(reason=_POOL_ROUTING_FIX_SKIP_REASON)
def test_generate_from_selection_regenerate_check_output_high_sets_failed_review(monkeypatch):
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
    (tmp_asset_dir / f"{ad_id}_draft.png").write_bytes(b"OLD-DRAFT-BYTES")
    new_draft = tmp_asset_dir / f"{ad_id}_draft_new.png"
    new_draft.write_bytes(b"NEW-DRAFT-BYTES")
    monkeypatch.setattr(pipeline.generate_image_prompt, "regenerate_from_stored_prompt",
                        lambda *a, **k: str(new_draft))
    monkeypatch.setattr(pipeline.output_critic, "check_draft",
                        lambda *a, **k: [{"category": "unauthorised text", "description": "x", "confidence": "high"}])
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True, instruction="fix it", check_output=True)
        assert result["by_ad"][ad_id] == "processed"
        row = dedupe.get_artifact(ad_id)
        assert row["review_status"] == "failed-review"
    finally:
        _cleanup(cid, [ad_id])


@pytest.mark.skip(reason=_POOL_ROUTING_FIX_SKIP_REASON)
def test_generate_from_selection_regenerate_check_output_clean_sets_ok(monkeypatch):
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
    (tmp_asset_dir / f"{ad_id}_draft.png").write_bytes(b"OLD-DRAFT-BYTES")
    new_draft = tmp_asset_dir / f"{ad_id}_draft_new.png"
    new_draft.write_bytes(b"NEW-DRAFT-BYTES")
    monkeypatch.setattr(pipeline.generate_image_prompt, "regenerate_from_stored_prompt",
                        lambda *a, **k: str(new_draft))
    monkeypatch.setattr(pipeline.output_critic, "check_draft", lambda *a, **k: [])
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True, instruction="fix it", check_output=True)
        assert result["by_ad"][ad_id] == "processed"
        row = dedupe.get_artifact(ad_id)
        assert row["review_status"] == "ok"
    finally:
        _cleanup(cid, [ad_id])


@pytest.mark.skip(reason=_POOL_ROUTING_FIX_SKIP_REASON)
def test_generate_from_selection_regenerate_check_output_false_leaves_review_status_untouched(monkeypatch):
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
        image_prompt="STORED PROMPT TEXT",
    )
    dedupe.update_artifact_findings(
        ad_id, [{"category": "x", "description": "y", "confidence": "high"}], review_status="failed-review",
    )
    from src import generate_image_prompt
    import tempfile
    from pathlib import Path as _Path
    tmp_asset_dir = _Path(tempfile.mkdtemp())
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_asset_dir)
    (tmp_asset_dir / f"{ad_id}_draft.png").write_bytes(b"OLD-DRAFT-BYTES")
    new_draft = tmp_asset_dir / f"{ad_id}_draft_new.png"
    new_draft.write_bytes(b"NEW-DRAFT-BYTES")
    monkeypatch.setattr(pipeline.generate_image_prompt, "regenerate_from_stored_prompt",
                        lambda *a, **k: str(new_draft))
    critic_calls = []
    monkeypatch.setattr(pipeline.output_critic, "check_draft", lambda *a, **k: critic_calls.append(1) or [])
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True, instruction="fix it", check_output=False)
        assert result["by_ad"][ad_id] == "processed"
        assert critic_calls == [], "check_output=False must never call the critic"
        row = dedupe.get_artifact(ad_id)
        assert row["review_status"] == "failed-review"  # untouched, NOT reset to 'ok' by the regenerate
    finally:
        _cleanup(cid, [ad_id])


@pytest.mark.skip(reason=_POOL_ROUTING_FIX_SKIP_REASON)
def test_process_ad_regenerate_fallthrough_check_output_false_leaves_review_status_untouched(monkeypatch):
    """Task F point 2 fallthrough (existing row, unreadable draft image) + check_output=False:
    process_ad's OWN save_artifact(regenerate=True) call must not silently clear a prior
    failed-review flag via its DELETE+INSERT - the prior critic_findings/review_status must
    be fetched BEFORE that DELETE and explicitly carried forward, same pattern as
    _regenerate_existing_draft's own fix."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    dedupe.update_artifact_findings(
        ad_id, [{"category": "x", "description": "y", "confidence": "high"}], review_status="failed-review",
    )
    _mock_success(monkeypatch)
    # Force _regenerate_existing_draft's own "no current draft image could be read" branch
    # (Task F point 2), so it returns None and process_ad falls through to a fresh generation.
    monkeypatch.setattr(pipeline.generate_image_prompt, "_current_draft_bytes", lambda aid, slug: None)
    critic_calls = []
    monkeypatch.setattr(pipeline.output_critic, "check_draft", lambda *a, **k: critic_calls.append(1) or [])
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True, check_output=False)
        assert result["by_ad"][ad_id] == "processed"
        assert critic_calls == [], "check_output=False must never call the critic"
        row = dedupe.get_artifact(ad_id)
        assert row["review_status"] == "failed-review"  # carried forward, NOT reset to 'ok'
    finally:
        _cleanup(cid, [ad_id])


@pytest.mark.skip(reason=_POOL_ROUTING_FIX_SKIP_REASON)
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


@pytest.mark.skip(reason=_POOL_ROUTING_FIX_SKIP_REASON)
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


@pytest.mark.skip(reason=_POOL_ROUTING_FIX_SKIP_REASON)
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


@pytest.mark.skip(reason=_POOL_ROUTING_FIX_SKIP_REASON)
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


@pytest.mark.skip(reason=_POOL_ROUTING_FIX_SKIP_REASON)
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


# ---- 2026-08-19: pool-send routing fix - regression tests (six, as specified) ----
#
# Pool send (generate_from_selection, dashboard.py's POST /api/generate) is the ONLY
# production caller that ever sets process_ad's explicit_selection=True - confirmed by
# grepping every call site of process_ad and of _regenerate_existing_draft. These six
# tests assert ROUTING, not output: that pool send always reaches the real fresh-
# generation path (deconstruct -> copy -> generate_image) and never
# _regenerate_existing_draft/regenerate_from_stored_prompt, regardless of `regenerate`,
# an existing artifact, a stored blueprint, or a stored prompt.

def test_pool_send_existing_artifact_calls_generate_image_not_regenerate_from_stored_prompt(monkeypatch):
    """Test 1/6: an ad that already has an artifact must still run the REAL
    fresh-generation path when pool send asks to regenerate it - never
    regenerate_from_stored_prompt, which would replay THIS ad's own prior blueprint/copy
    instead of re-deriving everything from the pool-selected reference image."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "old_hero"}, generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
        image_prompt="STORED PROMPT TEXT",
    )
    _mock_success(monkeypatch)
    generate_image_calls = []
    regenerate_calls = []
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k:
                            generate_image_calls.append(1) or "draft.png")
    monkeypatch.setattr(pipeline.generate_image_prompt, "regenerate_from_stored_prompt",
                        lambda *a, **k: regenerate_calls.append(1) or "should-not-be-called.png")
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True)
        assert result["by_ad"][ad_id] == "processed"
        assert generate_image_calls == [1], "pool send must call generate_image for an already-generated ad"
        assert regenerate_calls == [], "pool send must never call regenerate_from_stored_prompt"
    finally:
        _cleanup(cid, [ad_id])


def test_pool_send_regenerate_flag_leak_guard_still_calls_generate_image(monkeypatch):
    """Test 2/6: regenerate=True on a pool-send request with NO prior history for this ad
    must behave exactly like any other first-time pool send - generate_image is called,
    regenerate_from_stored_prompt never is. Guards against a future change reading
    `regenerate` anywhere in process_ad's fresh-path branch and rerouting on it."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()  # no existing artifact row at all
    _mock_success(monkeypatch)
    generate_image_calls = []
    regenerate_calls = []
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k:
                            generate_image_calls.append(1) or "draft.png")
    monkeypatch.setattr(pipeline.generate_image_prompt, "regenerate_from_stored_prompt",
                        lambda *a, **k: regenerate_calls.append(1) or "should-not-be-called.png")
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True)
        assert result["by_ad"][ad_id] == "processed"
        assert generate_image_calls == [1], "regenerate=True must not prevent a normal pool-send generation"
        assert regenerate_calls == [], "regenerate=True must never call regenerate_from_stored_prompt"
    finally:
        _cleanup(cid, [ad_id])


def test_pool_send_existing_stored_blueprint_deconstruct_still_runs(monkeypatch):
    """Test 3/6: an ad with a real stored blueprint on its existing artifact must still
    get a fresh deconstruct call from pool send - the stored blueprint must never be
    reused in place of re-deriving it from the pool-selected reference image."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "old_hero", "objects": [{"object_id": "old", "kind": "text"}]},
        generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    _mock_success(monkeypatch)
    deconstruct_calls = []
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda **k: deconstruct_calls.append(1) or {"format": "hero", "angle": "a"})
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True)
        assert result["by_ad"][ad_id] == "processed"
        assert deconstruct_calls == [1], "pool send must always deconstruct, even with a stored blueprint present"
    finally:
        _cleanup(cid, [ad_id])


def test_pool_send_existing_stored_prompt_still_calls_generate_image_with_fresh_prompt(monkeypatch):
    """Test 4/6: an ad with a stored image_prompt on its existing artifact must still get
    a genuinely fresh prompt built and persisted - the stored prompt text must never
    survive into the new artifact row, proving the run didn't quietly fall back to
    replaying it."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "old_hero"}, generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
        image_prompt="STALE STORED PROMPT TEXT",
    )
    _mock_success(monkeypatch)
    generate_image_calls = []
    regenerate_calls = []

    def fake_generate_image(bp, aid, product=None, reference_images=None, **k):
        generate_image_calls.append(1)
        fake_generate_image.last_prompt = "FRESHLY BUILT PROMPT TEXT"
        return "draft.png"
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", fake_generate_image)
    monkeypatch.setattr(pipeline.generate_image_prompt, "regenerate_from_stored_prompt",
                        lambda *a, **k: regenerate_calls.append(1) or "should-not-be-called.png")
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True)
        assert result["by_ad"][ad_id] == "processed"
        assert generate_image_calls == [1]
        assert regenerate_calls == []
        art = dedupe.get_artifact(ad_id)
        assert art["image_prompt"] == "FRESHLY BUILT PROMPT TEXT"
        assert "STALE STORED PROMPT TEXT" not in (art["image_prompt"] or "")
    finally:
        _cleanup(cid, [ad_id])


def test_pool_send_deconstruct_receives_the_pool_selected_ads_own_reference_image(monkeypatch):
    """Test 5/6: deconstruct must run against THIS ad's own reference image (its scraped
    image_url/bytes and its own ad_id) - not merely be called at all. Distinguishes a
    correct fresh deconstruct from one that accidentally reused another ad's or a stored
    artifact's image."""
    cid = _make_competitor()
    ad_id = f"SEL_{uuid.uuid4().hex[:8]}"
    own_url = f"http://x/{ad_id}.jpg"
    own_bytes = f"REFERENCE-BYTES-FOR-{ad_id}".encode()
    dedupe.init_scraped_ads()
    raw = {
        "ad_archive_id": ad_id, "page_name": "Brand", "media_type": "IMAGE",
        "images": [own_url], "ad_delivery_start_time": "2026-01-01",
        "cta_type": "SHOP_NOW", "link_url": "http://x", "ad_creative_bodies": ["body"],
    }
    dedupe.upsert_scraped_ad(ad_id=ad_id, competitor_id=cid, image_url=own_url,
                              raw_meta=raw, media_type="IMAGE")
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/other.jpg",
        blueprint={"format": "old_hero"}, generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    _mock_success(monkeypatch)
    captured = {}
    monkeypatch.setattr(pipeline.assets, "download_image_bytes",
                        lambda url: own_bytes if url == own_url else b"WRONG-BYTES")
    monkeypatch.setattr(pipeline.assets, "download_image",
                        lambda url, aid: "fake.jpg" if url == own_url and aid == ad_id else "wrong.jpg")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda **k: captured.update(k) or {"format": "hero", "angle": "a"})
    try:
        result = pipeline.generate_from_selection([ad_id], regenerate=True)
        assert result["by_ad"][ad_id] == "processed"
        assert captured.get("ad_id") == ad_id
        assert captured.get("image_bytes") == own_bytes, \
            "deconstruct must receive the pool-selected ad's own downloaded reference bytes"
    finally:
        _cleanup(cid, [ad_id])


def test_pool_send_fresh_path_reaches_composite_gate_placement_and_cutout_framing():
    """Test 6/6: static call-graph contract, not a runtime call. BFS's the real source of
    process_ad (src/pipeline.py) and generate_image_prompt.py, following every Call node
    reachable from process_ad, and asserts _composite_gate, find_supported_placement_bbox,
    composite_product, and _cutout_authority_framing are all in that reachable set. This
    must fail the day a future change routes pool send's fresh path through some other
    function whose body never references these four - e.g. a reintroduced regenerate
    branch, or a new image-generation function that skips compositing/placement
    entirely. Deliberately NOT a mocked runtime invocation: find_supported_placement_bbox
    is only reachable via resolve_no_product_placement, which process_ad calls directly
    (pipeline.py) - not from inside generate_image_prompt.generate_image itself - so the
    contract has to span both modules' real source, not just one function's body."""
    import ast
    import inspect
    from src import pipeline as _pipeline_module
    from src import generate_image_prompt as _gip_module

    def _function_defs(module):
        tree = ast.parse(inspect.getsource(module))
        defs = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs[node.name] = node
        return defs

    functions_by_name = {}
    functions_by_name.update(_function_defs(_pipeline_module))
    functions_by_name.update(_function_defs(_gip_module))

    def _called_names(func_node):
        names = set()
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    names.add(node.func.attr)
        return names

    reachable = set()
    stack = ["process_ad"]
    while stack:
        name = stack.pop()
        if name in reachable or name not in functions_by_name:
            continue
        reachable.add(name)
        for callee in _called_names(functions_by_name[name]):
            if callee not in reachable:
                stack.append(callee)

    for target in ("_composite_gate", "find_supported_placement_bbox",
                   "composite_product", "_cutout_authority_framing"):
        assert target in reachable, (
            f"{target} is no longer reachable from process_ad's fresh-generation path - "
            f"a future change must not route pool send through a function that skips it"
        )


def test_pool_send_existing_artifact_regenerate_false_still_runs_fresh_and_replaces_row(monkeypatch):
    """Test 7 (2026-08-19, second pass): pool.html no longer sends `regenerate` at all,
    so the REAL production case is regenerate omitted/False with an existing artifact -
    not regenerate=True, which every test above exercises. Proves two things the other
    six tests can't: (1) process_ad's own already_generated skip is gone structurally, not
    merely bypassed by a flag value it happens to receive; (2) save_artifact's own
    dedupe-skip gate (dedupe.py: SELECT 1 ... if found: return, with no INSERT) does NOT
    silently swallow the fresh generation - a real risk once the already_generated skip
    was removed without also forcing save_artifact's own regenerate=True for
    explicit_selection (see pipeline.py's own comment at that call site). A weaker
    assertion like `result == "processed"` would NOT catch this - process_ad returns
    "processed" regardless of whether save_artifact actually wrote anything, so the
    discriminating check is the STORED ROW's content, not the return string."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/old.jpg",
        blueprint={"format": "old_hero"}, generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
        image_prompt="STALE STORED PROMPT TEXT",
    )
    _mock_success(monkeypatch)
    try:
        result = pipeline.generate_from_selection([ad_id])  # regenerate omitted, as pool.html now sends it
        assert result["by_ad"][ad_id] == "processed", \
            "must never return already_generated - that skip is gone, not just bypassed"
        assert result["already_generated"] == 0
        rows = dedupe.get_artifacts(ad_id)
        assert len(rows) == 1, "replaced in place (DELETE+INSERT), not left untouched or duplicated"
        assert rows[0][1] == {"format": "hero", "angle": "a"}, \
            "the OLD blueprint must be gone - a silent save_artifact no-op would leave {'format': 'old_hero'} here"
        art = dedupe.get_artifact(ad_id)
        assert art["image_prompt"] != "STALE STORED PROMPT TEXT"
        row = dedupe.get_scraped_ads(competitor_id=cid)[0]
        assert row["status"] == "processed"
    finally:
        _cleanup(cid, [ad_id])


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
