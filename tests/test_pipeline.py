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
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: {"headline": "H", "primary_text": "P", "image_subtext": "S", "cta": "C"})
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text, **k: (True, []))
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


# ---- Regression guard (2026-08-05): every process_ad test above mocks
# dedupe.save_artifact away via _mock_all_stages, so none of them can catch process_ad
# passing a kwarg the REAL save_artifact doesn't accept (or vice versa) - a signature
# drift between the two would pass this whole file's other tests silently. This one
# deliberately does NOT mock save_artifact, hitting the real test DB, so a kwarg mismatch
# raises TypeError here instead of shipping unnoticed. ----

def test_process_ad_end_to_end_writes_a_real_unmocked_artifact_row(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
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
    # dedupe.save_artifact deliberately NOT mocked - that's the entire point of this test.
    try:
        assert pipeline.process_ad(ad) == "processed"
        rows = dedupe.get_artifacts_full(limit=500)
        match = next(r for r in rows if r["ad_id"] == ad_id)
        assert match["format_flag"] == ""
        assert match["product_override_note"] == ""
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (ad_id,))
            conn.commit()


# ---- Prompt 4, Item 3: hard-block medical/clinical/anatomical references BEFORE
# generation - not a judgment call, so this skips outright, never flags for review ----

def test_process_ad_hard_blocks_medical_reference_before_generation(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    dedupe.init_pipeline_warnings()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {
        "format": "before_after",
        "product_category": {"category": "not_product",
                             "signals": ["hemorrhoid treatment demonstration"]},
        "visual": {"subject": "anatomical before/after illustration"},
    })
    copy_calls = []
    image_calls = []
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live", lambda *a, **k: copy_calls.append(1))
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", lambda *a, **k: image_calls.append(1))
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    assert pipeline.process_ad(ad) == "skipped"
    assert copy_calls == []  # never reaches copy generation
    assert image_calls == []  # never reaches image generation
    assert any(kind == "hard_blocked_medical" for kind, detail in warnings)
    assert dedupe.is_new(ad_id) is False  # marked seen - never re-analysed on a future run


def test_process_ad_does_not_hard_block_ordinary_skincare_reference(monkeypatch):
    """Regression guard: the hard block must not become a blanket "not_product" filter -
    an ordinary tester/ambassador-recruitment ad (no medical signal) must proceed normally."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {
        "format": "founder_story",
        "product_category": {"category": "not_product", "signals": ["ambassador recruitment"]},
        "visual": {"subject": "founder telling her brand story"},
    })

    assert pipeline.process_ad(ad) == "processed"


# ---- Task H, Part 1 (2026-08-07); REVERSED same day (reference usability gate
# reversal, see CLAUDE.md): a reference with nothing to clone (no product, no headline,
# no text zone, no offer) is STILL a usable scene - the ad proceeds to generation, with
# the product/copy ADDED rather than substituted, never skipped.

def _nothing_to_clone_blueprint(**overrides):
    bp = {
        "format": "lifestyle_scene",
        "layout_detail": {"product_count": 0},
        "product_category": {"category": "other", "signals": []},
        "visual": {"subject": "woman in a robe by a window"},
    }
    bp.update(overrides)
    return bp


def test_process_ad_proceeds_and_adds_when_reference_has_nothing_to_clone(monkeypatch):
    """product_count==0 AND no headline AND no text zone AND no offer - REVERSED
    2026-08-07: this is still a usable scene, never skipped. With include_product and
    text_in_image on, both elements are ADDED (nothing in the reference to substitute
    into), and that provenance is persisted for a reviewer to see."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda **k: _nothing_to_clone_blueprint())
    captured = {}
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: captured.update(k))
    try:
        result = pipeline.process_ad(ad, edit_mode=True, include_product=True, text_in_image=True)
        assert result == "processed"
        assert captured["element_provenance"] == {"product": "added", "text": "added"}
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM seen_ads WHERE ad_id = %s", (ad_id,))
            conn.commit()


def test_process_ad_does_not_skip_when_reference_has_a_headline(monkeypatch):
    """product_count==0 but a real headline exists - there IS something to clone, so this
    must proceed, never be conflated with the true nothing-to-clone case."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda **k: _nothing_to_clone_blueprint(headline_verbatim="Feel confident again"))
    try:
        assert pipeline.process_ad(ad) == "processed"
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM seen_ads WHERE ad_id = %s", (ad_id,))
            conn.commit()


def test_process_ad_does_not_skip_when_reference_has_a_text_bearing_zone(monkeypatch):
    """product_count==0, no headline_verbatim, but a text-bearing structural zone
    (sub_line/body_copy/cta) exists - still something to clone."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: _nothing_to_clone_blueprint(
        structural_zones=[{"zone_type": "sub_line", "position": "top", "container": "none", "detail": "tagline"}],
    ))
    try:
        assert pipeline.process_ad(ad) == "processed"
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM seen_ads WHERE ad_id = %s", (ad_id,))
            conn.commit()


def test_process_ad_does_not_skip_when_reference_has_an_offer(monkeypatch):
    """product_count==0, no headline, no text zone, but an offer exists - still something
    to clone."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: _nothing_to_clone_blueprint(
        offer={"type": "discount", "value": "20%", "mechanic": "sitewide"},
    ))
    try:
        assert pipeline.process_ad(ad) == "processed"
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM seen_ads WHERE ad_id = %s", (ad_id,))
            conn.commit()


# ---- Prompt 4, Item 4: format flag - FLAG, never a filter, always processed normally ----

def test_process_ad_persists_format_flag_when_reference_is_a_bundle(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {
        "format": "offer_led",
        "layout_detail": {"product_count": 6},
        "offer": {"mechanic": "5 for $109 bundle"},
        "product_category": {"category": "body_oil", "signals": []},
        "visual": {"subject": "six bottles arranged in a range"},
    })
    captured = {}
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: captured.update(k))

    assert pipeline.process_ad(ad) == "processed"  # a flag never blocks or fails the ad
    assert captured["format_flag"] == "reference was a 6-product bundle offer"


def test_process_ad_format_flag_empty_string_when_no_mismatch(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    captured = {}
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: captured.update(k))

    assert pipeline.process_ad(ad) == "processed"
    assert captured["format_flag"] == ""


# ---- Silent-override audit (2026-08-05); REVERSED 2026-08-07 (reference usability gate
# reversal, see CLAUDE.md): resolve_effective_include_product no longer overrides
# include_product off, so the override warning/note this audit added can never fire
# again - replaced by element_provenance, which now records the ADD outcome instead. ----

def _no_product_in_reference_blueprint():
    """product_count == 0, but WITH a headline, deliberately, so this stays a "no
    product, but something else to clone" reference, distinct from Task H's "nothing
    here to clone at all" (product_count == 0 AND no headline AND no text zone AND no
    offer) - the two are different fixtures, and this one must not accidentally trip the
    other while testing the product-specific ADD path."""
    return {
        "format": "before_after",
        "layout_detail": {"product_count": 0},
        "product_category": {"category": "firming", "signals": []},
        "visual": {"subject": "before/after skin comparison, no product in frame"},
        "headline_verbatim": "See the difference in 30 days",
    }


def test_process_ad_never_overrides_include_product_off_when_reference_has_no_product(monkeypatch):
    """REVERSED 2026-08-07: the "product_override_no_reference_product" warning must
    never fire again - a productless reference now gets the product ADDED, never
    silently suppressed, so there's nothing to warn about."""
    dedupe.init_db()
    dedupe.init_artifacts()
    dedupe.init_pipeline_warnings()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda **k: _no_product_in_reference_blueprint())
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    assert pipeline.process_ad(ad, edit_mode=True, include_product=True) == "processed"
    assert not any(kind == "product_override_no_reference_product" for kind, detail in warnings)


def test_process_ad_persists_added_provenance_when_reference_has_no_product(monkeypatch):
    """REVERSED 2026-08-07: product_override_note is now always empty for a new
    generation (nothing sets it any more) - element_provenance is the new signal, and it
    must read "added" for the product here (there was nothing in the reference to
    substitute into), never "none" (the product still renders - it just wasn't
    suppressed) and never "substituted" (there was nothing to substitute)."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda **k: _no_product_in_reference_blueprint())
    captured = {}
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: captured.update(k))

    assert pipeline.process_ad(ad, edit_mode=True, include_product=True) == "processed"
    assert captured["product_override_note"] == ""
    assert captured["element_provenance"]["product"] == "added"


def test_process_ad_no_override_note_when_operator_already_disabled_product(monkeypatch):
    """include_product=False is the operator's own choice, not an override - nothing to
    report, and element_provenance reads "none" for the product (never authorised, so
    never rendered at all - distinct from "added"/"substituted", which both mean it WAS
    rendered)."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda **k: _no_product_in_reference_blueprint())
    captured = {}
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: captured.update(k))
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    assert pipeline.process_ad(ad, edit_mode=True, include_product=False) == "processed"
    assert captured["product_override_note"] == ""
    assert captured["element_provenance"]["product"] == "none"
    assert warnings == []


def test_process_ad_persists_substituted_provenance_when_reference_has_a_product(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {
        "format": "product_hero", "layout_detail": {"product_count": 1},
        "product_category": {"category": "body_oil", "signals": []},
    })
    captured = {}
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: captured.update(k))
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    assert pipeline.process_ad(ad, edit_mode=True, include_product=True) == "processed"
    assert captured["product_override_note"] == ""
    assert captured["element_provenance"]["product"] == "substituted"
    assert warnings == []


def test_process_ad_critic_receives_same_include_product_generator_used(monkeypatch, tmp_path):
    """REVERSED 2026-08-07: effective_include_product no longer diverges from the
    operator's raw toggle (a reference with no product to substitute now gets one ADDED,
    never suppressed) - the critic must still be told the SAME value the generator
    actually used, which is now always just include_product itself."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda **k: _no_product_in_reference_blueprint())
    draft_path = tmp_path / "draft.png"
    draft_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: str(draft_path))
    captured = {}
    monkeypatch.setattr(pipeline.output_critic, "check_draft",
                        lambda image_bytes, brand_rules_text, **k: captured.update(k) or [])
    monkeypatch.setattr(pipeline.dedupe, "update_artifact_findings", lambda *a, **k: None)

    assert pipeline.process_ad(ad, edit_mode=True, include_product=True, check_output=True) == "processed"
    assert captured["include_product"] is True


# ---- Silent-override audit item 2: a pasted operator brief past
# generate_image_prompt_writer.MAX_OPERATOR_INSTRUCTION_CHARS is silently truncated by
# clip_operator_instruction - same defect class as the product override above. ----

def test_process_ad_records_warning_when_operator_instruction_truncated(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    dedupe.init_pipeline_warnings()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    long_instruction = "x" * (pipeline.generate_image_prompt_writer.MAX_OPERATOR_INSTRUCTION_CHARS + 50)
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    assert pipeline.process_ad(ad, operator_instruction=long_instruction) == "processed"
    matches = [detail for kind, detail in warnings if kind == "operator_instruction_truncated"]
    assert len(matches) == 1
    assert str(len(long_instruction)) in matches[0]


def test_process_ad_no_warning_when_operator_instruction_within_limit(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    assert pipeline.process_ad(ad, operator_instruction="make it brighter") == "processed"
    assert not any(kind == "operator_instruction_truncated" for kind, detail in warnings)


# ---- Stop-button responsiveness (2026-08-05): run_once's own should_stop is only
# checked between ads/competitors - a click mid-ad couldn't interrupt an in-flight paid
# Gemini call. process_ad must check it once more immediately before that call. ----

def test_process_ad_stops_before_image_generation_when_should_stop_true(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    image_calls = []
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda *a, **k: image_calls.append(1) or "draft.png")

    assert pipeline.process_ad(ad, should_stop=lambda: True) == "skipped"
    assert image_calls == []  # the paid call must never happen


def test_process_ad_should_stop_false_proceeds_to_image_generation(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    image_calls = []
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda *a, **k: image_calls.append(1) or "draft.png")

    assert pipeline.process_ad(ad, should_stop=lambda: False) == "processed"
    assert image_calls == [1]


def test_process_ad_should_stop_none_default_proceeds_normally(monkeypatch):
    """None (a test or the writer calling process_ad directly, exactly as every other
    existing test in this file does) must behave as "never stop" - not raise, not skip."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)

    assert pipeline.process_ad(ad) == "processed"


def test_run_once_forwards_should_stop_to_process_ad(monkeypatch):
    """run_once must thread its OWN should_stop into process_ad, not just check it between
    ads - that's the exact gap this item closes."""
    dedupe.init_db()
    dedupe.init_artifacts()
    dedupe.init_decisions()
    dedupe.init_competitors()
    dedupe.init_products()
    dedupe.init_angles()
    dedupe.init_angle_language()
    dedupe.init_run_progress()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(pipeline.dedupe, "get_competitors",
                        lambda: [{"id": 999999, "name": "TestBrand", "page_id": "TestBrand"}])
    monkeypatch.setattr(pipeline, "with_retry", lambda fn, **k: fn())
    monkeypatch.setattr(pipeline.scrape, "scrape_ads", lambda name, page_id=None: [
        {"ad_id": ad_id, "page_name": "TestBrand", "image_url": "http://x/img.jpg",
         "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    ])
    captured = {}

    def fake_process_ad(ad, **k):
        captured["should_stop"] = k.get("should_stop")
        return "processed"

    monkeypatch.setattr(pipeline, "process_ad", fake_process_ad)
    my_should_stop = lambda: False

    pipeline.run_once(competitor_id=999999, should_stop=my_should_stop)
    assert captured["should_stop"] is my_should_stop


# ---- Prompt 4, Item 5: retheme_colours threads through to generate_image ----

def test_process_ad_forwards_retheme_colours_to_generate_image(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    captured = {}

    def capture_image(bp, aid, **k):
        captured.update(k)
        return "draft.png"

    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", capture_image)

    assert pipeline.process_ad(ad, retheme_colours=False) == "processed"
    assert captured["retheme_colours"] is False


def test_process_ad_retheme_colours_defaults_true(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    captured = {}

    def capture_image(bp, aid, **k):
        captured.update(k)
        return "draft.png"

    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", capture_image)

    assert pipeline.process_ad(ad) == "processed"
    assert captured["retheme_colours"] is True


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

    def capture_copy(bp, product=None, **k):
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
    generated copy's headline/image_subtext, which rule 6's text-in-image allow-list needs
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
    assert captured["subtext"] == "S"


def test_process_ad_never_passes_primary_text_as_image_subtext(monkeypatch):
    """Regression guard for the 2026-07-31 incident: primary_text is long-form Facebook
    post body copy (~80 words) - passing it as subtext meant rule 6 permitted rendering
    the ENTIRE thing as in-scene typography. subtext must come from image_subtext ONLY,
    and fall back to None (headline-only), never to primary_text, when image_subtext is
    missing or empty."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    long_primary_text = "This firming ritual " * 20  # ~80 words, matching the real incident
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: {"headline": "H", "primary_text": long_primary_text, "cta": "C"})
    captured = {}

    def capture_image(bp, aid, product=None, reference_images=None, subtext=None, **k):
        captured["subtext"] = subtext
        return "draft.png"

    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", capture_image)

    assert pipeline.process_ad(ad, text_in_image=True) == "processed"
    assert captured["subtext"] is None
    assert long_primary_text not in (captured["subtext"] or "")


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


def test_process_ad_forwards_offer_text_to_copy_and_compliance(monkeypatch):
    """offer_text must reach BOTH generate_copy_live (so the copy prompt's OFFER section
    reflects it) and compliance.check_compliance (so check_unauthorized_offer actually
    runs) - a competitor offer leaked through copy this time (blueprint.offer, via
    generate_copy) even though the image side was already fixed, so offer_text must not
    stop at generate_image."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    captured = {}

    def capture_copy(bp, product=None, **k):
        captured["copy_offer_text"] = k.get("offer_text")
        return {"headline": "H", "primary_text": "P", "cta": "C"}

    def capture_compliance(copy, name, text, **k):
        captured["compliance_offer_text"] = k.get("offer_text")
        return (True, [])

    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live", capture_copy)
    monkeypatch.setattr(pipeline.compliance, "check_compliance", capture_compliance)

    assert pipeline.process_ad(ad, offer_text="20% off") == "processed"
    assert captured["copy_offer_text"] == "20% off"
    assert captured["compliance_offer_text"] == "20% off"


# ---- EDIT MODE (2026-08-01): reuse the SAME competitor bytes already downloaded for
# deconstruct, never a second download; only forward them when edit_mode is actually on ----

def test_process_ad_forwards_competitor_bytes_to_generate_image_when_edit_mode_on(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    captured = {}

    def capture_image(bp, aid, **k):
        captured.update(k)
        return "draft.png"

    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", capture_image)

    assert pipeline.process_ad(ad, edit_mode=True) == "processed"
    assert captured["edit_mode"] is True
    # _mock_all_stages stubs download_image_bytes to return b"fake-bytes" - process_ad must
    # reuse THAT exact value (the same bytes already downloaded for deconstruct_image),
    # never re-download.
    assert captured["competitor_image_bytes"] == b"fake-bytes"


def test_process_ad_does_not_forward_competitor_bytes_when_edit_mode_off(monkeypatch):
    """edit_mode defaults to False - the team confirmed edit-vs-generate is about 50/50,
    so today's generate-only path must keep working unchanged: no competitor bytes at all,
    even though they were still downloaded for the deconstruct call."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    captured = {}

    def capture_image(bp, aid, **k):
        captured.update(k)
        return "draft.png"

    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", capture_image)

    assert pipeline.process_ad(ad) == "processed"
    assert captured["edit_mode"] is False
    assert captured["competitor_image_bytes"] is None


# ---- Step 2 (2026-08-02): operator instruction field ----

def test_process_ad_forwards_operator_instruction_to_generate_image(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    captured = {}

    def capture_image(bp, aid, **k):
        captured.update(k)
        return "draft.png"

    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", capture_image)

    assert pipeline.process_ad(ad, operator_instruction="make the background warmer") == "processed"
    assert captured["operator_instruction"] == "make the background warmer"


def test_process_ad_persists_operator_instruction_on_artifact(monkeypatch):
    """Auditability requirement: a reviewer looking at a wrong draft must be able to see
    whether the operator asked for it - stored alongside image_prompt, not just used
    transiently during generation."""
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

    assert pipeline.process_ad(ad, operator_instruction="show the oil being poured") == "processed"
    assert captured["operator_instruction"] == "show the oil being poured"


def test_process_ad_persists_empty_operator_instruction_as_empty_string(monkeypatch):
    """operator_instruction=None (no instruction given) must persist as "" - never as the
    Python literal None reaching a NOT NULL-shaped column read back elsewhere as null."""
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

    assert pipeline.process_ad(ad) == "processed"
    assert captured["operator_instruction"] == ""


# ---- Prompt 4, Item 1: output critic - a SAFETY control, non-blocking, runs AFTER
# save_artifact, never fails the run, never surfaces low confidence, defaults off ----

def test_process_ad_does_not_call_critic_when_check_output_off(monkeypatch):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    calls = []
    monkeypatch.setattr(pipeline.output_critic, "check_draft", lambda *a, **k: calls.append(1) or [])

    assert pipeline.process_ad(ad) == "processed"  # check_output defaults to False
    assert calls == []


def test_process_ad_calls_critic_after_save_artifact_when_check_output_on(monkeypatch, tmp_path):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    draft_path = tmp_path / "draft.png"
    draft_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: str(draft_path))

    call_order = []
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: call_order.append("save_artifact"))
    captured = {}

    def fake_check_draft(image_bytes, brand_rules_text, **k):
        call_order.append("check_draft")
        captured["image_bytes"] = image_bytes
        captured["brand_rules_text"] = brand_rules_text
        captured.update(k)
        return [{"category": "testimonial", "description": "fabricated quote", "confidence": "high"}]

    monkeypatch.setattr(pipeline.output_critic, "check_draft", fake_check_draft)
    findings_calls = []
    monkeypatch.setattr(pipeline.dedupe, "update_artifact_findings",
                        lambda ad_id, findings, angle_id=None: findings_calls.append((ad_id, findings, angle_id)))

    assert pipeline.process_ad(ad, check_output=True) == "processed"
    # save_artifact must happen BEFORE check_draft - never before, per the safety
    # requirement that a slow/failed check can never lose an already-persisted draft.
    assert call_order == ["save_artifact", "check_draft"]
    assert captured["image_bytes"] == b"\x89PNG\r\n\x1a\nfakepngbytes"
    assert "STRICT RULES" in captured["brand_rules_text"]  # a real brand_rules() call, not a stub
    assert findings_calls == [(ad_id, [{"category": "testimonial", "description": "fabricated quote",
                                         "confidence": "high"}], None)]


def test_process_ad_critic_uses_the_same_flags_as_generation(monkeypatch, tmp_path):
    """"the same rules" - brand_rules_text handed to the critic must reflect the ACTUAL
    generation flags (edit_mode here), not brand_rules()'s bare defaults."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    draft_path = tmp_path / "draft.png"
    draft_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: str(draft_path))
    captured = {}
    monkeypatch.setattr(pipeline.output_critic, "check_draft",
                        lambda image_bytes, brand_rules_text, **k: captured.update(brand_rules_text=brand_rules_text) or [])
    monkeypatch.setattr(pipeline.dedupe, "update_artifact_findings", lambda *a, **k: None)

    assert pipeline.process_ad(ad, check_output=True, edit_mode=True) == "processed"
    assert "SOURCE IMAGE IS THE COMPETITOR'S OWN AD" in captured["brand_rules_text"]


# ---- Silent-hang investigation follow-up (2026-08-04): the critic must never be told
# something the generator wasn't told. check_draft used to receive copy.get("headline")
# unconditionally - generate_copy_live always produces a headline whether or not
# text_in_image was requested, so a text_in_image=False run told the critic a headline
# WAS authorised while rule 6 (correctly) told the generator to render none - a real HIGH
# "Missing authorised text" false positive. Both sides must now derive from
# generate_image_prompt.effective_authorised_text. ----

def test_process_ad_critic_headline_gated_by_text_in_image_false(monkeypatch, tmp_path):
    """_mock_all_stages' generate_copy_live always returns a truthy headline regardless of
    text_in_image - this is deliberately the exact shape of the live bug: text_in_image=False
    here must still gate the critic's headline/subtext to None, not pass "H"/"S" through
    just because generate_copy_live produced them."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    draft_path = tmp_path / "draft.png"
    draft_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: str(draft_path))
    captured = {}
    monkeypatch.setattr(pipeline.output_critic, "check_draft",
                        lambda image_bytes, brand_rules_text, **k: captured.update(k) or [])
    monkeypatch.setattr(pipeline.dedupe, "update_artifact_findings", lambda *a, **k: None)

    assert pipeline.process_ad(ad, check_output=True, text_in_image=False) == "processed"
    assert captured["headline"] is None
    assert captured["subtext"] is None


def test_process_ad_critic_headline_passed_through_when_text_in_image_true(monkeypatch, tmp_path):
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    draft_path = tmp_path / "draft.png"
    draft_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: str(draft_path))
    captured = {}
    monkeypatch.setattr(pipeline.output_critic, "check_draft",
                        lambda image_bytes, brand_rules_text, **k: captured.update(k) or [])
    monkeypatch.setattr(pipeline.dedupe, "update_artifact_findings", lambda *a, **k: None)

    assert pipeline.process_ad(ad, check_output=True, text_in_image=True) == "processed"
    assert captured["headline"] == "H"
    assert captured["subtext"] == "S"


def test_rule6_and_critic_authorised_text_never_contradict():
    """The highest-value test in this set: it fails on the CLASS of bug (two independent
    derivations of "is text authorised" drifting apart) rather than the one instance
    already fixed above. Pins literal substrings from the actual current source text
    rather than deriving the expected value from effective_authorised_text itself - a test
    that read its expectation back from the code under test would pass vacuously if that
    code's condition ever changed. All 410 tests existing before this one passed while the
    live bug shipped, because nothing asserted the generator and critic were told the same
    thing - this is what closes that gap."""
    from src import generate_image_prompt, output_critic
    headline, subtext = "Then & Now", "7 oils. Deeper hydration. Visibly firmer skin."

    # text_in_image=False: rule 6 (the generator) must forbid rendering it...
    rule6_off = generate_image_prompt._rule6_text_policy(text_in_image=False, headline=headline, subtext=subtext)
    assert "NEVER render any headline" in rule6_off
    # ...and the critic must be told the same thing, not the raw headline regardless.
    eff_headline_off, eff_subtext_off = generate_image_prompt.effective_authorised_text(False, headline, subtext)
    critic_prompt_off = output_critic._build_user_prompt("(rules)", headline=eff_headline_off,
                                                          subtext=eff_subtext_off)
    assert "NONE - no text was authorised for this image" in critic_prompt_off
    assert headline not in critic_prompt_off

    # text_in_image=True with a real headline: rule 6 must permit exactly it...
    rule6_on = generate_image_prompt._rule6_text_policy(text_in_image=True, headline=headline, subtext=subtext)
    assert f'the headline "{headline}"' in rule6_on
    # ...and the critic must be told exactly that same headline, not "none authorised".
    eff_headline_on, eff_subtext_on = generate_image_prompt.effective_authorised_text(True, headline, subtext)
    critic_prompt_on = output_critic._build_user_prompt("(rules)", headline=eff_headline_on,
                                                         subtext=eff_subtext_on)
    assert headline in critic_prompt_on
    assert "NONE - no text was authorised for this image" not in critic_prompt_on


def test_process_ad_records_warning_and_leaves_card_unflagged_when_critic_fails(monkeypatch, tmp_path):
    dedupe.init_db()
    dedupe.init_artifacts()
    dedupe.init_pipeline_warnings()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)
    draft_path = tmp_path / "draft.png"
    draft_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: str(draft_path))
    monkeypatch.setattr(pipeline.output_critic, "check_draft", lambda *a, **k: None)
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))
    findings_calls = []
    monkeypatch.setattr(pipeline.dedupe, "update_artifact_findings", lambda *a, **k: findings_calls.append(1))

    assert pipeline.process_ad(ad, check_output=True) == "processed"
    assert any(kind == "critic_failed" for kind, detail in warnings)
    assert findings_calls == []  # card left unflagged - a failed check is not a finding


def test_process_ad_critic_exception_never_fails_an_otherwise_successful_run(monkeypatch):
    """_mock_all_stages' generate_image returns "draft.png" - a path that doesn't exist,
    so reading it raises. That must be swallowed, never surfaced as a run failure."""
    dedupe.init_db()
    dedupe.init_artifacts()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    _mock_all_stages(monkeypatch)

    assert pipeline.process_ad(ad, check_output=True) == "processed"


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


# ---- Corrective-retry loop (2026-08-05/06): observe (generate) -> evaluate (critic) ->
# correct (feed findings back) -> re-generate, capped at ONE retry. Real bug this closes:
# a real draft reproduced a competitor's product name, body copy, CTA, and product
# category verbatim, was correctly flagged HIGH on all 8 counts, and was still saved as an
# ordinary-looking pending draft - the critic was reporting, not gating.
#
# These 5 tests stub every dedupe DB touchpoint (not just save_artifact, unlike
# _mock_all_stages above) so they give real signal about pipeline.py's control flow with
# no live Postgres reachable - this sandbox has none, and chunk 7's real test-db is out of
# scope for today. Nothing here talks to a database, real or fake.

def _mock_dedupe_db(monkeypatch):
    monkeypatch.setattr(pipeline.dedupe, "init_db", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "init_artifacts", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "is_new", lambda ad_id, angle_id=None: True)
    monkeypatch.setattr(pipeline.dedupe, "mark_seen", lambda *a, **k: None)


def _mock_ad_and_early_stages(monkeypatch):
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image", lambda **k: {"format": "hero", "angle": "a"})
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: {"headline": "H", "primary_text": "P",
                                                         "image_subtext": "S", "cta": "C"})
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text, **k: (True, []))
    return ad_id, ad


# Test 1/5: one HIGH finding -> exactly one regenerate call, and the critic's own
# findings are present in the corrections passed to that regenerate call. Deliberately
# does NOT use _mock_all_stages - generate_image is a custom fake that distinguishes the
# pre-retry vs post-retry call by inspecting critic_feedback itself, which a blanket stub
# returning one fixed value could never prove.
def test_process_ad_one_high_finding_triggers_exactly_one_retry_with_corrections(monkeypatch, tmp_path):
    _mock_dedupe_db(monkeypatch)
    ad_id, ad = _mock_ad_and_early_stages(monkeypatch)
    saved = {}
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: saved.update(k))
    findings_calls = []
    monkeypatch.setattr(pipeline.dedupe, "update_artifact_findings",
                        lambda ad_id, findings, angle_id=None: findings_calls.append(findings))

    draft_v1 = tmp_path / "draft_v1.png"
    draft_v1.write_bytes(b"\x89PNG\r\n\x1a\nv1")
    draft_v2 = tmp_path / "draft_v2.png"
    draft_v2.write_bytes(b"\x89PNG\r\n\x1a\nv2")

    gen_calls = []
    def fake_generate_image(bp, aid, product=None, reference_images=None, **k):
        gen_calls.append(k)
        return str(draft_v2) if k.get("critic_feedback") else str(draft_v1)
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", fake_generate_image)

    check_calls = []
    def fake_check_draft(image_bytes, brand_rules_text, **k):
        check_calls.append(image_bytes)
        if len(check_calls) == 1:
            return [{"category": "product category mismatch",
                     "description": "rendered a mist instead of the authorised body oil",
                     "confidence": "high"}]
        return []  # the retry came back clean
    monkeypatch.setattr(pipeline.output_critic, "check_draft", fake_check_draft)

    assert pipeline.process_ad(ad, check_output=True) == "processed"
    assert len(gen_calls) == 2  # attempt 1 + exactly one retry
    assert gen_calls[0].get("critic_feedback") is None
    assert gen_calls[1]["critic_feedback"] == [
        "product category mismatch: rendered a mist instead of the authorised body oil"
    ]
    assert saved["draft_image"] == str(draft_v2)  # the clean retry, not the flagged v1
    assert findings_calls == [[]]  # final findings are the clean retry's


# Test 2/5: the retry still comes back HIGH -> the draft is saved (never discarded) but
# must not look like a normal pending draft - proven both by its persisted
# critic_findings AND by its actual absence from dedupe.get_pending_artifacts, exercised
# for real against an in-memory fake table, not asserted by reading the source.
def test_process_ad_retry_still_high_persists_as_failed_review_and_excluded_from_pending(monkeypatch, tmp_path):
    _mock_dedupe_db(monkeypatch)
    ad_id, ad = _mock_ad_and_early_stages(monkeypatch)
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    draft_path = tmp_path / "draft.png"
    draft_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: str(draft_path))

    high_finding = [{"category": "unauthorised text", "description": "competitor headline survived",
                      "confidence": "high"}]
    monkeypatch.setattr(pipeline.output_critic, "check_draft", lambda *a, **k: high_finding)

    fake_rows = {}
    def fake_save_artifact(**k):
        fake_rows[k["ad_id"]] = {**k, "critic_findings": [], "decision": None}
    def fake_update_artifact_findings(aid, findings, angle_id=None):
        fake_rows[aid]["critic_findings"] = findings
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", fake_save_artifact)
    monkeypatch.setattr(pipeline.dedupe, "update_artifact_findings", fake_update_artifact_findings)
    monkeypatch.setattr(dedupe, "get_artifacts_full", lambda limit=500: list(fake_rows.values()))

    assert pipeline.process_ad(ad, check_output=True) == "processed"
    assert fake_rows[ad_id]["draft_image"] == str(draft_path)  # saved, never discarded
    assert fake_rows[ad_id]["critic_findings"] == high_finding
    assert any(kind == "critic_high_after_retry" for kind, detail in warnings)

    pending = dedupe.get_pending_artifacts()
    assert all(a["ad_id"] != ad_id for a in pending)  # absent from the real pending-queue query


# Test 3/5: the retry succeeding persists a normal, unflagged draft - no different from a
# draft that was clean on the first attempt.
def test_process_ad_retry_comes_back_clean_persists_normally(monkeypatch, tmp_path):
    _mock_dedupe_db(monkeypatch)
    ad_id, ad = _mock_ad_and_early_stages(monkeypatch)
    saved = {}
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: saved.update(k))
    findings_calls = []
    monkeypatch.setattr(pipeline.dedupe, "update_artifact_findings",
                        lambda ad_id, findings, angle_id=None: findings_calls.append(findings))
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    draft_path = tmp_path / "draft.png"
    draft_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: str(draft_path))

    check_calls = []
    def fake_check_draft(*a, **k):
        check_calls.append(1)
        if len(check_calls) == 1:
            return [{"category": "efficacy claim", "description": "unapproved percentage", "confidence": "high"}]
        return []
    monkeypatch.setattr(pipeline.output_critic, "check_draft", fake_check_draft)

    assert pipeline.process_ad(ad, check_output=True) == "processed"
    assert len(check_calls) == 2  # first attempt flagged, retry re-checked
    assert findings_calls == [[]]
    assert not any(kind == "critic_high_after_retry" for kind, detail in warnings)
    assert saved["draft_image"] == str(draft_path)


# Test 4/5: a HIGH finding on the RETRY's own output must never trigger a second retry -
# the cap is exactly one, proven by call COUNT on both generate_image and check_draft, not
# by reading MAX_IMAGE_ATTEMPTS in the source.
def test_process_ad_retry_still_high_never_triggers_a_second_retry(monkeypatch, tmp_path):
    _mock_dedupe_db(monkeypatch)
    ad_id, ad = _mock_ad_and_early_stages(monkeypatch)
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: None)
    monkeypatch.setattr(pipeline.dedupe, "update_artifact_findings", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda *a, **k: None)

    draft_path = tmp_path / "draft.png"
    draft_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    gen_calls = []
    def fake_generate_image(bp, aid, product=None, reference_images=None, **k):
        gen_calls.append(1)
        return str(draft_path)
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", fake_generate_image)

    check_calls = []
    high = [{"category": "unauthorised text", "description": "still wrong", "confidence": "high"}]
    def fake_check_draft(*a, **k):
        check_calls.append(1)
        return high
    monkeypatch.setattr(pipeline.output_critic, "check_draft", fake_check_draft)

    assert pipeline.process_ad(ad, check_output=True) == "processed"
    assert len(gen_calls) == 2    # attempt 1 + exactly one retry, never a third
    assert len(check_calls) == 2  # both attempts checked, never a third check


# Test 5/5: a critic EXCEPTION (not check_draft's normal None-on-failure return, an actual
# raise reaching pipeline's own try/except around the call) must never lose the draft or
# trigger a retry it has no verdict to justify.
def test_process_ad_critic_exception_saves_draft_without_retrying(monkeypatch, tmp_path):
    _mock_dedupe_db(monkeypatch)
    ad_id, ad = _mock_ad_and_early_stages(monkeypatch)
    saved = {}
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: saved.update(k))
    findings_calls = []
    monkeypatch.setattr(pipeline.dedupe, "update_artifact_findings", lambda *a, **k: findings_calls.append(1))
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    draft_path = tmp_path / "draft.png"
    draft_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    gen_calls = []
    def fake_generate_image(bp, aid, product=None, reference_images=None, **k):
        gen_calls.append(1)
        return str(draft_path)
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", fake_generate_image)

    def boom(*a, **k):
        raise RuntimeError("critic API unavailable")
    monkeypatch.setattr(pipeline.output_critic, "check_draft", boom)

    assert pipeline.process_ad(ad, check_output=True) == "processed"
    assert len(gen_calls) == 1  # no retry - the critic never returned a verdict to correct against
    assert saved["draft_image"] == str(draft_path)  # nothing lost
    assert findings_calls == []  # never flagged - a failed check is not a finding
    assert any(kind == "critic_failed" for kind, detail in warnings)


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

    def always_fail(copy, name, text, **k):
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
                       body_area="knees", offer_text="20% off launch week", edit_mode=True,
                       operator_instruction="make the background warmer")

    assert len(captured) == 1
    assert captured[0]["realism"] == "ugc_native"
    assert captured[0]["text_in_image"] is True
    assert captured[0]["include_product"] is False
    assert captured[0]["body_area"] == "knees"
    assert captured[0]["offer_text"] == "20% off launch week"
    assert captured[0]["edit_mode"] is True
    assert captured[0]["operator_instruction"] == "make the background warmer"


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


# ---- Step 3: by_competitor summary + DB-backed run progress ----

def test_run_once_summary_includes_by_competitor_breakdown(monkeypatch):
    """A category sweep's total is illegible without this: image yield varies hugely per
    brand (CLAUDE.md: ~1/10 to 8/10 across pages), so a low total is the pool, not a bug -
    an operator needs to see per-competitor ads_seen/processed to tell the difference."""
    monkeypatch.setattr(pipeline.dedupe, "get_competitors", lambda: _FAKE_COMPETITORS)

    def fake_scrape(name, page_id=None):
        return [{"ad_id": f"{name}_1", "page_name": name}, {"ad_id": f"{name}_2", "page_name": name}] \
            if name == "OSEA" else []

    monkeypatch.setattr(pipeline.scrape, "scrape_ads", fake_scrape)
    monkeypatch.setattr(pipeline, "process_ad", lambda ad, **k: "processed")

    summary = pipeline.run_once(category="body_oil")  # matches OSEA + Kiehl's
    assert set(summary["by_competitor"].keys()) == {"OSEA", "Kiehl's"}
    assert summary["by_competitor"]["OSEA"] == {"ads_seen": 2, "processed": 2, "skipped": 0,
                                                  "failed": 0, "error": None}
    assert summary["by_competitor"]["Kiehl's"] == {"ads_seen": 0, "processed": 0, "skipped": 0,
                                                     "failed": 0, "error": None}


def test_run_once_by_competitor_records_scrape_error(monkeypatch):
    monkeypatch.setattr(pipeline.dedupe, "get_competitors", lambda: _FAKE_COMPETITORS[:1])  # just OSEA
    # Skip with_retry's real sleep-and-retry - not what this test is checking.
    monkeypatch.setattr(pipeline, "with_retry", lambda fn, **k: fn())

    def boom(*a, **k):
        raise RuntimeError("scrape service down")

    monkeypatch.setattr(pipeline.scrape, "scrape_ads", boom)

    summary = pipeline.run_once(competitor_id=1)
    assert summary["by_competitor"]["OSEA"]["error"] == "scrape service down"
    assert summary["by_competitor"]["OSEA"]["ads_seen"] == 0


def test_run_once_updates_and_clears_run_progress(monkeypatch):
    """dedupe.set_run_progress must be called once per competitor (so the dashboard can
    show which one is running) and cleared to empty at the end - DB-backed, not an
    in-memory variable, since the Cloud Run Job path is a separate process."""
    dedupe.init_run_progress()
    _mock_competitor_selection(monkeypatch)
    monkeypatch.setattr(pipeline.scrape, "scrape_ads", lambda name, page_id=None: [])

    calls = []
    orig_set = dedupe.set_run_progress
    monkeypatch.setattr(pipeline.dedupe, "set_run_progress",
                        lambda name, idx, total: calls.append((name, idx, total)))

    pipeline.run_once()

    assert calls[:-1] == [("OSEA", 1, 3), ("CeraVe", 2, 3), ("Kiehl's", 3, 3)]
    assert calls[-1] == ("", 0, 0)  # cleared at the end


# ---- Chunk 4, Item 4: max_per_competitor must cap ATTEMPTS, not successes ----

def test_run_once_attempt_counting_guard_stops_after_cap_even_when_ads_fail(monkeypatch):
    """The old success-only counter let the loop keep trying ad after ad past the
    requested cap until enough of them succeeded - observed live as a 1-ad
    request processing nine. Every ad that reaches deconstruct is a real paid
    attempt and must count against the cap whether it ends up processed,
    hard-blocked, or failed - proven here by an ad that ALWAYS fails compliance,
    so it never once returns "processed": with max_per_competitor=1, only ONE of
    the 3 available ads must ever reach deconstruct."""
    monkeypatch.setattr(pipeline.dedupe, "get_competitors",
                        lambda: [{"id": 1, "name": "Brand", "page_id": "1"}])
    ad_ids = [f"PIPE_{uuid.uuid4().hex[:8]}" for _ in range(3)]
    ads = [{"ad_id": aid, "page_name": "Brand", "image_url": "http://x/img.jpg",
            "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
           for aid in ad_ids]
    monkeypatch.setattr(pipeline.scrape, "scrape_ads", lambda *a, **k: ads)
    monkeypatch.setattr(pipeline.assets, "download_image", lambda url, aid: "fake.jpg")
    monkeypatch.setattr(pipeline.assets, "download_image_bytes", lambda url: b"fake-bytes")
    deconstruct_calls = []
    monkeypatch.setattr(pipeline.deconstruct, "deconstruct_image",
                        lambda **k: deconstruct_calls.append(k.get("ad_id")) or {"format": "hero", "angle": "a"})
    monkeypatch.setattr(pipeline.generate_copy, "generate_copy_live",
                        lambda bp, product=None, **k: {"headline": "H", "primary_text": "P",
                                                         "image_subtext": "S", "cta": "C"})
    # Always fails - process_ad returns "failed" for every ad, never "processed".
    monkeypatch.setattr(pipeline.compliance, "check_compliance", lambda copy, name, text, **k: (False, ["x"]))
    monkeypatch.setattr(pipeline.dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda *a, **k: None)

    summary = pipeline.run_once(max_per_competitor=1, competitor_id=1)

    assert len(deconstruct_calls) == 1, \
        "only the first ad's attempt should count against the cap - the loop must stop there, not try all 3"
    assert summary["failed"] == 1
    assert summary["processed"] == 0


def test_run_once_parity_normal_run_unaffected_by_the_attempt_counting_fix(monkeypatch):
    """Chunk 4 parity: for the ordinary case (nothing fails), the attempt-counting
    fix must behave identically to the old success-counting check - process
    exactly max_per_competitor ads and stop. The existing Run Pipeline button's
    everyday behaviour must be unchanged."""
    monkeypatch.setattr(pipeline.dedupe, "get_competitors",
                        lambda: [{"id": 1, "name": "Brand", "page_id": "1"}])
    ad_ids = [f"PIPE_{uuid.uuid4().hex[:8]}" for _ in range(3)]
    ads = [{"ad_id": aid, "page_name": "Brand", "image_url": "http://x/img.jpg",
            "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}
           for aid in ad_ids]
    monkeypatch.setattr(pipeline.scrape, "scrape_ads", lambda *a, **k: ads)
    _mock_all_stages(monkeypatch)
    try:
        summary = pipeline.run_once(max_per_competitor=2, competitor_id=1)
        assert summary["processed"] == 2
        assert summary["skipped"] == 0
        assert summary["failed"] == 0
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM seen_ads WHERE ad_id = ANY(%s)", (ad_ids,))
            conn.commit()


def test_process_ad_failure_isolated(monkeypatch):
    dedupe.init_db()
    ad_id = f"PIPE_{uuid.uuid4().hex[:8]}"
    ad = {"ad_id": ad_id, "page_name": "brand", "image_url": "http://x/img.jpg",
          "start_date": "", "destination_url": "", "text": "", "cta": "", "media_type": "IMAGE"}

    def boom(url, aid):
        raise RuntimeError("download failed")
    monkeypatch.setattr(pipeline.assets, "download_image", boom)
    assert pipeline.process_ad(ad) == "failed"


# ---- select_testimonial_review (2026-08-06, fabricated-testimonials fix): real review
# or nothing, never invented. Pure unit tests - dedupe.get_reviews_for_product is
# monkeypatched, no DB connection at all. ----

def _quote_blueprint():
    return {"structural_zones": [
        {"zone_type": "social_proof", "position": "lower-third", "social_proof_kind": "single_quote"},
    ]}


def _fake_review(id_, text, nickname="Jane D."):
    return {"id": id_, "review_text": text, "char_length": len(text), "nickname": nickname}


def test_select_testimonial_review_none_when_no_single_quote_zone(monkeypatch):
    """No social_proof/single_quote zone at all - must skip the DB read entirely."""
    calls = []
    monkeypatch.setattr(pipeline.dedupe, "get_reviews_for_product", lambda *a, **k: calls.append(1) or [])
    result = pipeline.select_testimonial_review({"structural_zones": []}, {"id": 1}, "AD1")
    assert result is None
    assert calls == []


def test_select_testimonial_review_none_when_no_product_id(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline.dedupe, "get_reviews_for_product", lambda *a, **k: calls.append(1) or [])
    result = pipeline.select_testimonial_review(_quote_blueprint(), None, "AD1")
    assert result is None
    assert calls == []


def test_select_testimonial_review_none_when_no_reviews_exist(monkeypatch):
    monkeypatch.setattr(pipeline.dedupe, "get_reviews_for_product", lambda *a, **k: [])
    result = pipeline.select_testimonial_review(_quote_blueprint(), {"id": 1}, "AD1")
    assert result is None


def test_select_testimonial_review_excludes_reviews_too_long_for_an_image_line(monkeypatch):
    long_review = _fake_review(1, "x" * 500)
    monkeypatch.setattr(pipeline.dedupe, "get_reviews_for_product", lambda *a, **k: [long_review])
    result = pipeline.select_testimonial_review(_quote_blueprint(), {"id": 1}, "AD1", max_chars=140)
    assert result is None


def test_select_testimonial_review_picks_a_real_review(monkeypatch):
    reviews = [_fake_review(1, "This oil changed my skin.", "Jane D.")]
    monkeypatch.setattr(pipeline.dedupe, "get_reviews_for_product", lambda *a, **k: reviews)
    result = pipeline.select_testimonial_review(_quote_blueprint(), {"id": 1}, "AD1")
    assert result == {"quote": "This oil changed my skin.", "attribution": "Jane D."}


def test_select_testimonial_review_is_deterministic_for_the_same_ad(monkeypatch):
    """The SAME ad must pick the SAME review every time - a real review disappearing and
    reappearing on repeat runs would itself look like a bug."""
    reviews = [_fake_review(i, f"Review number {i} is great.") for i in range(20)]
    monkeypatch.setattr(pipeline.dedupe, "get_reviews_for_product", lambda *a, **k: reviews)
    first = pipeline.select_testimonial_review(_quote_blueprint(), {"id": 1}, "SAME_AD_ID")
    second = pipeline.select_testimonial_review(_quote_blueprint(), {"id": 1}, "SAME_AD_ID")
    assert first == second


def test_select_testimonial_review_excludes_reviews_that_read_as_complaints(monkeypatch):
    """Found live 2026-08-06: a genuine 5-star, short-enough review whose TEXT is a
    shipping complaint ("I have not received it yet and it's been weeks since I ordered
    it") passed the rating+length filter cleanly - star rating alone doesn't mean the
    text reads as a positive testimonial."""
    complaint = _fake_review(
        1, "Well I do love it but I have not received it yet and it's been weeks "
           "since I ordered it", "Shannon R.",
    )
    positive = _fake_review(2, "This oil changed my skin.", "Jane D.")
    monkeypatch.setattr(pipeline.dedupe, "get_reviews_for_product", lambda *a, **k: [complaint, positive])
    result = pipeline.select_testimonial_review(_quote_blueprint(), {"id": 1}, "AD1")
    assert result == {"quote": "This oil changed my skin.", "attribution": "Jane D."}


def test_select_testimonial_review_falls_back_attribution_when_nickname_empty(monkeypatch):
    review = _fake_review(1, "Great product.", nickname="")
    monkeypatch.setattr(pipeline.dedupe, "get_reviews_for_product", lambda *a, **k: [review])
    result = pipeline.select_testimonial_review(_quote_blueprint(), {"id": 1}, "AD1")
    assert result == {"quote": "Great product.", "attribution": ""}


def test_stable_index_is_stable_across_calls():
    """Unlike builtin hash() on a string, which is randomised per-process - this must
    give the identical answer every call, in every process."""
    assert pipeline._stable_index("AD1", 10) == pipeline._stable_index("AD1", 10)
    # A real cross-process guarantee can't be asserted in one test run, but a hardcoded
    # sha256-based expectation pins the algorithm itself against silent drift.
    import hashlib
    digest = hashlib.sha256(b"AD1").hexdigest()
    assert pipeline._stable_index("AD1", 10) == int(digest, 16) % 10
