"""Tests for the pipeline orchestrator. All live stages monkeypatched - no network, no spend."""
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


def _mock_all_stages(monkeypatch):
    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {"format": "hero", "angle": "a"})
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live", lambda bp, product=None, **k: {"headline": "H", "primary_text": "P", "cta": "C"})
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text: (True, []))
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", lambda bp, aid, product=None, reference_images=None, **k: "draft.png")
    monkeypatch.setattr(pipeline.slack_review, "post_review", lambda *a, **k: {"ts": "123"})
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: None)


def test_process_ad_full_flow_mocked(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "2026-01-01", "destination_url": "http://x", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    assert pipeline.process_ad(ad) == "processed"
    assert dedupe.is_new(ad_id) is False


def test_process_ad_passes_product_to_copy_and_image(monkeypatch):
    """Regression guard. run_once resolved the product and process_ad forwarded it to
    generate_image but NOT to generate_copy_live, so every copy prompt rendered
    "(no specific product selected)" and the model refused with stop_reason='end_turn'.
    Assert the dict reaches BOTH stages, so dropping either kwarg fails here."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    product = {"id": 1, "name": "Magic Body Oil", "description": "seven cold-pressed oils",
               "ingredients": "almond; rosehip", "hero_claim": "Visibly firms",
               "image_key": "product_1_ref.png", "category": "body_oil"}

    _mock_all_stages(monkeypatch)
    seen = {}

    def capture_copy(bp, product=None):
        seen["copy"] = product
        return {"headline": "H", "primary_text": "P", "cta": "C"}

    def capture_image(bp, aid, product=None, reference_images=None, **k):
        seen["image"] = product
        seen["reference_images"] = reference_images
        return "draft.png"

    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live", capture_copy)
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", capture_image)

    reference_images = [b"photo-1-bytes", b"photo-2-bytes", b"photo-3-bytes"]
    assert pipeline.process_ad(ad, product=product, reference_images=reference_images) == "processed"

    # Identity, not equality: if the kwarg is dropped the stub defaults to None and this fails.
    assert seen["copy"] is product, "product did not reach generate_copy_live"
    assert seen["image"] is product, "product did not reach generate_image"

    # All three reference images must arrive, not just the first.
    assert seen["reference_images"] == reference_images, "not all reference images reached generate_image"

    # The four fields the copy prompt actually needs must be present on what arrived.
    for key in ("name", "description", "ingredients", "hero_claim"):
        assert key in seen["copy"], f"{key} missing from product handed to generate_copy_live"


def test_process_ad_persists_text_in_image_on_artifact(monkeypatch):
    """text_in_image must reach save_artifact so the artifact row records which mode
    generated it, for the dashboard's future overlay-suppression logic."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    captured = {}

    def capture_save_artifact(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(pipeline.dedupe, "save_artifact", capture_save_artifact)

    assert pipeline.process_ad(ad, text_in_image=True) == "processed"
    assert captured["text_in_image"] is True


def test_process_ad_forwards_toggles_and_copy_to_generate_image(monkeypatch):
    """Regression guard (Part 4): include_product/text_in_image must actually reach
    generate_image, not just sit as unused process_ad parameters - along with the
    generated copy's headline/primary_text, which rule 6's text-in-image allow-list needs
    to know what's actually permitted."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    captured = {}

    def capture_image(bp, aid, product=None, reference_images=None, angle_slug=None,
                       include_product=True, text_in_image=False, headline=None, subtext=None, **k):
        captured.update(include_product=include_product, text_in_image=text_in_image,
                         headline=headline, subtext=subtext)
        return "draft.png"

    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", capture_image)

    assert pipeline.process_ad(ad, include_product=False, text_in_image=True) == "processed"
    assert captured["include_product"] is False
    assert captured["text_in_image"] is True
    assert captured["headline"] == "H"
    assert captured["subtext"] == "P"


def test_process_ad_forwards_angle_realism_body_area_offer_text_to_generate_image(monkeypatch):
    """Part 5 regression guard: messaging_angle/realism/body_area/offer_text must reach
    generate_image_prompt.generate_image, which is the only place any of them are actually
    consumed (by the Claude prompt-writer pass). Without this, the run-strip controls
    thread all the way to process_ad and then silently go nowhere."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    captured = {}

    def capture_image(bp, aid, **kwargs):
        captured.update(kwargs)
        return "draft.png"

    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", capture_image)

    angle = {"id": 7, "slug": "crepey_skin", "name": "Crepey Skin"}
    assert pipeline.process_ad(ad, messaging_angle=angle, realism="ugc_native",
                                body_area="knees", offer_text="20% off") == "processed"
    assert captured["messaging_angle"] is angle
    assert captured["realism"] == "ugc_native"
    assert captured["body_area"] == "knees"
    assert captured["offer_text"] == "20% off"


def test_process_ad_warns_when_text_in_image_requested_but_headline_missing(monkeypatch):
    """If copy generation produces no usable headline (e.g. an empty string) while
    text_in_image was requested, rule 6 silently falls back to the blanket text ban -
    a text-free image with no visible explanation. Must record a pipeline_warning."""
    dedupe.init_db()
    dedupe.init_artifacts()
    dedupe.init_pipeline_warnings()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: {"headline": "", "primary_text": "P", "cta": "C"})
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    assert pipeline.process_ad(ad, text_in_image=True) == "processed"
    assert any(kind == "text_in_image_no_headline" for kind, detail in warnings)


def test_effective_image_keys_prefers_multi_image_set():
    product = {"image_key": "legacy.png", "image_keys": ["a.png", "b.png"]}
    assert pipeline.effective_image_keys(product) == ["a.png", "b.png"]


def test_effective_image_keys_falls_back_to_legacy_image_key():
    """Products created before the multi-image change only have image_key set -
    effective_image_keys must still find that single photo."""
    product = {"image_key": "legacy.png", "image_keys": []}
    assert pipeline.effective_image_keys(product) == ["legacy.png"]


def test_effective_image_keys_empty_when_neither_set():
    assert pipeline.effective_image_keys({"image_key": "", "image_keys": []}) == []
    assert pipeline.effective_image_keys(None) == []


def test_fetch_reference_images_warns_when_none_configured(monkeypatch):
    product = {"id": 1, "name": "Magic Body Oil", "image_key": "", "image_keys": []}
    images, warning = pipeline.fetch_reference_images(product)
    assert images == []
    assert warning is not None
    kind, detail = warning
    assert kind == "no_reference_photo"
    assert "Magic Body Oil" in detail


def test_fetch_reference_images_fetches_all_configured(monkeypatch):
    product = {"id": 1, "name": "Magic Body Oil", "image_key": "", "image_keys": ["k1.png", "k2.png"]}

    class FakeBlob:
        def __init__(self, key):
            self.key = key
        def exists(self):
            return True
        def download_as_bytes(self):
            return f"bytes-for-{self.key}".encode()

    class FakeBucket:
        def blob(self, key):
            return FakeBlob(key)

    class FakeClient:
        def bucket(self, name):
            return FakeBucket()

    monkeypatch.setattr(pipeline.assets, "asset_bucket_name", lambda: "fake-bucket")
    import google.cloud.storage as gcs_storage
    monkeypatch.setattr(gcs_storage, "Client", FakeClient)

    images, warning = pipeline.fetch_reference_images(product)
    assert warning is None
    assert images == [b"bytes-for-k1.png", b"bytes-for-k2.png"]


def test_fetch_reference_images_warns_on_partial_failure(monkeypatch):
    product = {"id": 1, "name": "Magic Body Oil", "image_key": "", "image_keys": ["k1.png", "missing.png"]}

    class FakeBlob:
        def __init__(self, key):
            self.key = key
        def exists(self):
            return self.key != "missing.png"
        def download_as_bytes(self):
            return b"ok-bytes"

    class FakeBucket:
        def blob(self, key):
            return FakeBlob(key)

    class FakeClient:
        def bucket(self, name):
            return FakeBucket()

    monkeypatch.setattr(pipeline.assets, "asset_bucket_name", lambda: "fake-bucket")
    import google.cloud.storage as gcs_storage
    monkeypatch.setattr(gcs_storage, "Client", FakeClient)

    images, warning = pipeline.fetch_reference_images(product)
    assert images == [b"ok-bytes"]  # the one that succeeded, not silently dropped without a trace
    assert warning is not None
    kind, detail = warning
    assert kind == "reference_photo_fetch_failed"
    assert "missing.png" in detail


def test_process_ad_compliance_fail_is_failed(monkeypatch):
    """Also verifies the fail-soft retry: a compliance failure must trigger exactly one
    retry (2 attempts total) before giving up, and the final failure must be recorded
    as a visible warning - not just logged - per the "counter nobody sees is the same
    silent failure in a new coat" requirement from the multi-image work."""
    dedupe.init_db()
    dedupe.init_pipeline_warnings()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    # Force compliance to fail on every attempt
    call_count = {"n": 0}

    def always_fail(copy, name, text):
        call_count["n"] += 1
        return (False, ["competitor name"])

    monkeypatch.setattr(pipeline.compliance, "check_compliance", always_fail)
    try:
        assert pipeline.process_ad(ad) == "failed"
        assert call_count["n"] == 2, "expected exactly one retry (2 attempts), not immediate failure"
        warnings = dedupe.get_recent_warnings(limit=50)
        assert any(ad_id in w["detail"] and w["kind"] == "compliance_failed" for w in warnings), \
            "compliance failure must be recorded as a visible warning, not just logged"
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM pipeline_warnings WHERE detail LIKE %s", (f"%{ad_id}%",))
            conn.commit()


_FAKE_COMPETITORS = [
    {"id": 1, "name": "OSEA", "page_id": "1", "category": "body_oil"},
    {"id": 2, "name": "CeraVe", "page_id": "2", "category": "moisturizer"},
    {"id": 3, "name": "Kiehl's", "page_id": "3", "category": "body_oil"},
]


def _mock_competitor_selection(monkeypatch):
    monkeypatch.setattr(pipeline.dedupe, "get_competitors", lambda: _FAKE_COMPETITORS)
    monkeypatch.setattr(pipeline.scrape, "scrape_ads", lambda *a, **k: [])


def test_run_once_competitor_id_selects_exactly_one(monkeypatch):
    """Regression guard. Adding the category filter to run_once must not disturb the
    existing single-competitor path: competitor_id alone, or competitor_id together
    with an (irrelevant) category, must both still select exactly one competitor.
    We prove "which competitors were selected" by recording every name scrape_ads
    was called with, rather than asserting on run_once's return value."""
    _mock_competitor_selection(monkeypatch)
    selected = []
    monkeypatch.setattr(pipeline.scrape, "scrape_ads", lambda name, page_id=None: selected.append(name) or [])

    pipeline.run_once(competitor_id=2)
    assert selected == ["CeraVe"]

    # competitor_id must win even if a category is also passed.
    selected.clear()
    pipeline.run_once(competitor_id=2, category="body_oil")
    assert selected == ["CeraVe"]


def test_run_once_empty_string_category_is_not_a_filter(monkeypatch):
    """Regression guard. category="" must behave like category=None (run every
    competitor), NOT like a filter matching competitors with no category set -
    otherwise an empty dropdown selection would silently scope a run down to
    only untagged competitors instead of running everything."""
    _mock_competitor_selection(monkeypatch)
    selected = []
    monkeypatch.setattr(pipeline.scrape, "scrape_ads", lambda name, page_id=None: selected.append(name) or [])

    pipeline.run_once(category="")
    assert selected == ["OSEA", "CeraVe", "Kiehl's"]


def test_run_once_category_selects_matching_competitors(monkeypatch):
    _mock_competitor_selection(monkeypatch)
    selected = []
    monkeypatch.setattr(pipeline.scrape, "scrape_ads", lambda name, page_id=None: selected.append(name) or [])

    pipeline.run_once(category="body_oil")
    assert selected == ["OSEA", "Kiehl's"]


def test_run_once_no_filter_hits_all_competitors(monkeypatch):
    _mock_competitor_selection(monkeypatch)
    selected = []
    monkeypatch.setattr(pipeline.scrape, "scrape_ads", lambda name, page_id=None: selected.append(name) or [])

    pipeline.run_once()
    assert selected == ["OSEA", "CeraVe", "Kiehl's"]


def test_run_once_threads_realism_and_toggles_to_process_ad(monkeypatch):
    """Regression guard for the run-strip controls (Parts 3-4b): realism, text_in_image,
    include_product, body_area, and offer_text must reach process_ad unchanged. This is
    the "verify locally via pipeline.run_once(...)" check - /api/run only affects the
    deployed Cloud Run image, never local code, so this is the only way to prove the
    threading actually works."""
    _mock_competitor_selection(monkeypatch)
    monkeypatch.setattr(pipeline.scrape, "scrape_ads",
                        lambda name, page_id=None: [{"ad_id": "A1", "page_name": name}])
    captured = []
    monkeypatch.setattr(pipeline, "process_ad", lambda ad, **kwargs: captured.append(kwargs) or "processed")

    pipeline.run_once(competitor_id=2, realism="ugc_native", text_in_image=True, include_product=False,
                       body_area="knees", offer_text="20% off launch week")

    assert len(captured) == 1
    assert captured[0]["realism"] == "ugc_native"
    assert captured[0]["text_in_image"] is True
    assert captured[0]["include_product"] is False
    assert captured[0]["body_area"] == "knees"
    assert captured[0]["offer_text"] == "20% off launch week"


def test_run_once_body_area_is_independent_of_angle_body_area(monkeypatch):
    """Body area varies every run and is NOT fixed per angle (team confirmed) - run_once
    must forward the explicit per-run body_area, never read it off the resolved angle's
    own body_area column. A regression here would mean angles.body_area silently became
    authoritative again, exactly what was ruled out."""
    _mock_competitor_selection(monkeypatch)
    monkeypatch.setattr(pipeline.scrape, "scrape_ads",
                        lambda name, page_id=None: [{"ad_id": "A1", "page_name": name}])
    monkeypatch.setattr(pipeline.dedupe, "get_angle",
                        lambda aid: {"id": aid, "slug": "crepey_skin", "body_area": "elbow and forearm"})
    captured = []
    monkeypatch.setattr(pipeline, "process_ad", lambda ad, **kwargs: captured.append(kwargs) or "processed")

    pipeline.run_once(competitor_id=2, angle_id=1, body_area="knees")

    assert captured[0]["body_area"] == "knees"


def test_process_ad_failure_isolated(monkeypatch):
    dedupe.init_db()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}

    def boom(url, aid):
        raise RuntimeError("download failed")
    monkeypatch.setattr(pipeline.assets, "download_image", boom)
    assert pipeline.process_ad(ad) == "failed"
