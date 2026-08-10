"""Tests for POST /api/generate, GET /api/generate/status, POST /api/generate/stop,
and GET /api/pool/cards's angle-aware already_generated flag (Chunk 5). Real DB
rows (uuid-suffixed, cleaned up in finally). Gemini/Claude mocked throughout via
pipeline's own stage modules - no network, no spend."""
import threading
import uuid
import dashboard
from fastapi.testclient import TestClient
from src import dedupe, pipeline


def _make_competitor():
    dedupe.init_competitors()
    name = f"__test_{uuid.uuid4().hex[:8]}__"
    return dedupe.add_competitor(name, "999999", "")


def _seed_scraped_ad(competitor_id, ad_id=None):
    dedupe.init_scraped_ads()
    ad_id = ad_id or f"GEN_{uuid.uuid4().hex[:8]}"
    raw = {"ad_archive_id": ad_id, "page_name": "Brand", "media_type": "IMAGE",
           "images": ["http://x/img.jpg"], "ad_creative_bodies": ["body"]}
    dedupe.upsert_scraped_ad(ad_id=ad_id, competitor_id=competitor_id, image_url="http://x/img.jpg",
                              raw_meta=raw, media_type="IMAGE")
    return ad_id


def _cleanup(competitor_id, ad_ids):
    dedupe.init_generate_jobs()  # some tests never hit an endpoint that creates the table
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM scraped_ads WHERE competitor_id=%s", (competitor_id,))
        cur.execute("DELETE FROM seen_ads WHERE ad_id = ANY(%s)", (ad_ids,))
        cur.execute("DELETE FROM artifacts WHERE ad_id = ANY(%s)", (ad_ids,))
        # generate_jobs is keyed by an opaque job_id, not competitor_id/ad_id - the
        # `?|` operator finds every job whose ad_ids JSON array contains any of
        # THIS test's ad_ids, since job_id itself is never captured at every call site.
        cur.execute("DELETE FROM generate_jobs WHERE ad_ids ?| %s", (ad_ids,))
        conn.commit()
    dedupe.delete_competitor(competitor_id)


def _join_generate_thread(timeout=10):
    assert dashboard._generate_thread is not None, "no background generate thread was started"
    dashboard._generate_thread.join(timeout=timeout)
    assert not dashboard._generate_thread.is_alive(), "background generate thread did not finish in time"


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


# ---- POST /api/generate + GET /api/generate/status ----

def test_api_generate_starts_in_background_and_status_reports_progress_and_result(monkeypatch):
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    _mock_success(monkeypatch)
    try:
        client = TestClient(dashboard.app)
        r = client.post("/api/generate", json={"ad_ids": [ad_id]})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["started"] is True
        job_id = body["job_id"]
        assert job_id

        _join_generate_thread()

        status_r = client.get(f"/api/generate/status?job_id={job_id}")
        assert status_r.status_code == 200
        status = status_r.json()
        assert status["status"] == "done"
        assert status["progress"] == {ad_id: "processed"}  # live per-ad progress, not just final
        assert status["result"]["processed"] == 1
        assert status["result"]["by_ad"] == {ad_id: "processed"}
    finally:
        _cleanup(cid, [ad_id])


def test_api_generate_missing_ad_ids_is_400():
    client = TestClient(dashboard.app)
    r = client.post("/api/generate", json={})
    assert r.status_code == 400
    r2 = client.post("/api/generate", json={"ad_ids": []})
    assert r2.status_code == 400


def test_api_generate_status_none_for_unknown_job():
    client = TestClient(dashboard.app)
    r = client.get("/api/generate/status?job_id=no-such-job")
    assert r.status_code == 200
    assert r.json() == {"status": "none", "progress": {}, "result": None, "error": None}


# ---- Item 1: the existing per-run inputs reach generate_from_selection intact ----

def test_api_generate_inputs_reach_generate_from_selection_intact(monkeypatch):
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    captured = {}

    def fake_generate_from_selection(ad_ids, **kwargs):
        captured["ad_ids"] = ad_ids
        captured.update(kwargs)
        return {"processed": 0, "skipped": 0, "failed": 0, "already_generated": 0, "by_ad": {}}
    monkeypatch.setattr(pipeline, "generate_from_selection", fake_generate_from_selection)
    try:
        client = TestClient(dashboard.app)
        r = client.post("/api/generate", json={
            "ad_ids": [ad_id], "angle_id": 7, "body_area": "  arms  ",
            "offer_text": "  20% off  ", "instruction": "  warmer tones  ",
            "product_id": 1, "regenerate": True,
            "text_in_image": True, "include_product": False, "edit_mode": True,
            "check_output": True, "retheme_colours": False,
        })
        assert r.status_code == 200
        _join_generate_thread()
        assert captured["ad_ids"] == [ad_id]
        assert captured["angle_id"] == 7
        assert captured["body_area"] == "arms"
        assert captured["offer_text"] == "20% off"
        assert captured["instruction"] == "warmer tones"
        assert captured["product_id"] == 1
        assert captured["regenerate"] is True
        # Chunk 6.1, Item 1 - every value flipped from its default here, to
        # prove they actually reach through rather than just passing along
        # whatever the default happens to be.
        assert captured["text_in_image"] is True
        assert captured["include_product"] is False
        assert captured["edit_mode"] is True
        assert captured["check_output"] is True
        assert captured["retheme_colours"] is False
        assert callable(captured["should_stop"])
        assert callable(captured["on_ad_done"])
    finally:
        _cleanup(cid, [ad_id])


def test_api_generate_toggle_defaults_match_dashboard_run_strip(monkeypatch):
    """When omitted from the body, the five toggles must default EXACTLY like
    dashboard.html's /api/run: text_in_image/edit_mode/check_output off,
    include_product/retheme_colours on."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    captured = {}

    def fake_generate_from_selection(ad_ids, **kwargs):
        captured.update(kwargs)
        return {"processed": 0, "skipped": 0, "failed": 0, "already_generated": 0, "by_ad": {}}
    monkeypatch.setattr(pipeline, "generate_from_selection", fake_generate_from_selection)
    try:
        client = TestClient(dashboard.app)
        r = client.post("/api/generate", json={"ad_ids": [ad_id]})
        assert r.status_code == 200
        _join_generate_thread()
        assert captured["text_in_image"] is False
        assert captured["include_product"] is True
        assert captured["edit_mode"] is False
        assert captured["check_output"] is False
        assert captured["retheme_colours"] is True
    finally:
        _cleanup(cid, [ad_id])


# ---- item 2 (2026-08-06): realism reaches generate_from_selection, constrained to
# validator.production_styles() - the pool run-strip dropdown's whole point ----

def test_api_generate_realism_reaches_generate_from_selection(monkeypatch):
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    captured = {}

    def fake_generate_from_selection(ad_ids, **kwargs):
        captured.update(kwargs)
        return {"processed": 0, "skipped": 0, "failed": 0, "already_generated": 0, "by_ad": {}}
    monkeypatch.setattr(pipeline, "generate_from_selection", fake_generate_from_selection)
    try:
        client = TestClient(dashboard.app)
        r = client.post("/api/generate", json={"ad_ids": [ad_id], "realism": "illustrated"})
        assert r.status_code == 200
        _join_generate_thread()
        assert captured["realism"] == "illustrated"
    finally:
        _cleanup(cid, [ad_id])


def test_api_generate_realism_omitted_defaults_to_none(monkeypatch):
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    captured = {}

    def fake_generate_from_selection(ad_ids, **kwargs):
        captured.update(kwargs)
        return {"processed": 0, "skipped": 0, "failed": 0, "already_generated": 0, "by_ad": {}}
    monkeypatch.setattr(pipeline, "generate_from_selection", fake_generate_from_selection)
    try:
        client = TestClient(dashboard.app)
        r = client.post("/api/generate", json={"ad_ids": [ad_id]})
        assert r.status_code == 200
        _join_generate_thread()
        assert captured["realism"] is None
    finally:
        _cleanup(cid, [ad_id])


def test_api_generate_rejects_invalid_realism():
    """Constrained dropdown, not free text - an unrecognised value must never reach
    generate_from_selection/process_ad, where it would silently fail to match any
    STYLE_GUIDANCE key and produce (auto) behaviour with no signal why."""
    client = TestClient(dashboard.app)
    r = client.post("/api/generate", json={"ad_ids": ["X"], "realism": "cartoonish"})
    assert r.status_code == 400
    assert "realism" in r.json()["error"]


def test_api_production_styles_matches_validator():
    client = TestClient(dashboard.app)
    r = client.get("/api/production_styles")
    assert r.status_code == 200
    from src import validator
    assert r.json() == validator.production_styles()


def test_api_generate_product_scoping_respected(monkeypatch):
    """product_id must reach generate_from_selection (and from there, process_ad's
    product/reference_images) - a real product row, not a constant."""
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_products()
    product_id = dedupe.add_product("Magic Body Oil Test", category="body_oil")
    _mock_success(monkeypatch)
    captured_products = []
    orig_generate_copy = pipeline.generate_copy.generate_copy_live

    def capturing_copy(bp, product=None, **k):
        captured_products.append(product)
        return {"headline": "H", "primary_text": "P", "image_subtext": "S", "cta": "C"}
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live", capturing_copy)
    try:
        client = TestClient(dashboard.app)
        r = client.post("/api/generate", json={"ad_ids": [ad_id], "product_id": product_id})
        assert r.status_code == 200
        _join_generate_thread()
        assert len(captured_products) == 1
        assert captured_products[0]["id"] == product_id
        assert captured_products[0]["name"] == "Magic Body Oil Test"
    finally:
        _cleanup(cid, [ad_id])
        dedupe.delete_product(product_id)


# ---- Item 3: skip path spends nothing when already generated and not regenerating ----

def test_api_generate_already_generated_skip_spends_nothing(monkeypatch):
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    dedupe.init_artifacts()
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "Old"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    deconstruct_calls = []
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda **k: deconstruct_calls.append(1) or {"format": "hero", "angle": "a"})
    try:
        client = TestClient(dashboard.app)
        r = client.post("/api/generate", json={"ad_ids": [ad_id]})  # regenerate defaults False
        _join_generate_thread()
        status = client.get(f"/api/generate/status?job_id={r.json()['job_id']}").json()
        assert status["result"]["by_ad"][ad_id] == "already_generated"
        assert deconstruct_calls == []
    finally:
        _cleanup(cid, [ad_id])


# ---- Item 5: Stop must reach the pre-Gemini check, not just run between ads ----

def test_api_generate_stop_halts_before_gemini_call(monkeypatch):
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    entered = threading.Event()
    release = threading.Event()

    def blocking_deconstruct(**k):
        entered.set()
        release.wait(timeout=5)
        return {"format": "hero", "angle": "a"}
    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", blocking_deconstruct)
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: {"headline": "H", "primary_text": "P",
                                                         "image_subtext": "S", "cta": "C"})
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text, **k: (True, []))
    image_calls = []
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda *a, **k: image_calls.append(1))
    try:
        client = TestClient(dashboard.app)
        r = client.post("/api/generate", json={"ad_ids": [ad_id]})
        job_id = r.json()["job_id"]

        # Wait until the background thread is ACTUALLY inside deconstruct (past
        # generate_from_selection's own between-ads check) before requesting
        # stop, so this proves the PRE-GEMINI check specifically - not a race
        # against the between-ads one, which runs earlier and would pass this
        # trivially without exercising the thing Item 5 actually asks for.
        assert entered.wait(timeout=5), "background thread never reached deconstruct"
        stop_r = client.post("/api/generate/stop", json={"job_id": job_id})
        assert stop_r.status_code == 200
        release.set()
        _join_generate_thread()

        status = client.get(f"/api/generate/status?job_id={job_id}").json()
        assert image_calls == [], "generate_image must never be reached once stop was requested"
        assert status["result"]["by_ad"][ad_id] == "skipped"
    finally:
        release.set()
        if dashboard._generate_thread is not None:
            dashboard._generate_thread.join(timeout=5)
        _cleanup(cid, [ad_id])


# ---- Item 3: GET /api/pool/cards's angle-aware already_generated flag ----

def test_api_pool_cards_already_generated_is_angle_specific():
    dedupe.init_scraped_ads()
    dedupe.init_artifacts()
    dedupe.init_angles()
    dedupe.init_angle_language()
    cid = _make_competitor()
    ad_id = _seed_scraped_ad(cid)
    angle_a = dedupe.add_angle("Angle A", f"angle-a-{uuid.uuid4().hex[:6]}")
    angle_b = dedupe.add_angle("Angle B", f"angle-b-{uuid.uuid4().hex[:6]}")
    dedupe.save_artifact(
        ad_id=ad_id, page_name="Brand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "H"},
        draft_image="assets/x_draft.png", metadata={"cta": "Shop", "destination_url": "http://x"},
        angle_id=angle_a,
    )
    try:
        client = TestClient(dashboard.app)
        r_a = client.get(f"/api/pool/cards?competitor_id={cid}&angle_id={angle_a}")
        assert r_a.json()["cards"][0]["already_generated"] is True

        r_b = client.get(f"/api/pool/cards?competitor_id={cid}&angle_id={angle_b}")
        assert r_b.json()["cards"][0]["already_generated"] is False

        r_none = client.get(f"/api/pool/cards?competitor_id={cid}")
        assert r_none.json()["cards"][0]["already_generated"] is False  # no angle_id given
    finally:
        _cleanup(cid, [ad_id])
        dedupe.delete_angle(angle_a)
        dedupe.delete_angle(angle_b)
