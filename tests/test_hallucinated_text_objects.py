"""Hallucinated-text-object guard (2026-08-19).

Root cause: deconstruct.py sends the ad's scraped Facebook caption (ad_creative_bodies,
via scrape._map_ad -> pipeline.process_ad -> deconstruct.deconstruct_image's ad_text
param) to Claude alongside the image, as "source of truth for the headline and offer"
(BLUEPRINT_PROMPT). Until this session, nothing told Claude that objects[] may describe
ONLY what is visibly present in the attached image, as distinct from that scraped copy.
Confirmed live on two artifacts: the model folded the scraped caption's testimonial/CTA
text into objects[] entries with a defaulted full-frame bbox ([0.0, 0.0, 1.0, 1.0]),
while the SAME blueprint's own layout_detail.text_zone/legibility_notes correctly
recorded that no text is baked into the image at all. This file reproduces both known
shapes STRUCTURALLY (no real ad_id/page_id, per CLAUDE.md's own standing instruction on
this) and locks the fix: deconstruct.drop_hallucinated_text_objects, called
unconditionally inside generate_image_prompt.build_image_prompt, CO-LOCATED with the
dedupe.record_warning call that traces what it drops (2026-08-19, second pass) - an
earlier version of this fix called the same drop function a second time, separately, in
each of build_image_prompt's two production callers, purely to fire the warning; that
left a real gap (a future third caller of build_image_prompt would drop objects with no
warning at all - the same silent-defect shape this whole fix exists to close, just moved
up one level). Co-locating means there is nothing left for a caller to forget.

The invariant under test: a kind=="text" object that does not correspond to text
visibly present in the reference image must never reach build_image_prompt, and its
removal must never be silent.
"""
from src import deconstruct, generate_image_prompt


def _base_bp(objects, text_zone=None, legibility_notes=None, ad_id="AD_TEST"):
    bp = {
        "ad_id": ad_id,
        "visual": {"layout": "flat lay", "subject": "", "palette_mood": "warm",
                   "text_placement": "lower"},
        "objects": objects,
    }
    if text_zone is not None or legibility_notes is not None:
        bp["layout_detail"] = {"text_zone": text_zone or ""}
        bp["legibility_notes"] = legibility_notes or ""
    return bp


def _text_obj(object_id, description, bbox, text_purpose=None, **overrides):
    obj = {
        "object_id": object_id, "kind": "text", "bbox": list(bbox),
        "description": description, "disposition": "substitute",
    }
    if text_purpose is not None:
        obj["text_purpose"] = text_purpose
    obj.update(overrides)
    return obj


def _person_obj(object_id="obj_person", description="woman on a sofa", bbox=(0.0, 0.0, 1.0, 1.0)):
    return {"object_id": object_id, "kind": "person", "bbox": list(bbox),
            "description": description, "disposition": "substitute"}


# ---- The discriminator: a legitimate in-image text object still passes ----

def test_drop_hallucinated_text_objects_keeps_legitimate_inimage_text():
    """Without this test, the suite would be trivially satisfied by a filter that drops
    every kind=='text' object unconditionally. A real headline object - bounded bbox
    (not full-frame), and a blueprint whose text_zone/legibility_notes make no
    'no in-image text' claim - must survive unchanged."""
    headline = _text_obj(
        "obj_headline", "main headline reading 'Skin that finally feels like yours'",
        bbox=(0.05, 0.05, 0.9, 0.15), text_purpose="headline",
    )
    bp = _base_bp([headline], text_zone="headline top-centre, product mid-frame",
                  legibility_notes="headline is bold and fully legible at feed size")
    kept, dropped = deconstruct.drop_hallucinated_text_objects(bp)
    assert kept == [headline]
    assert dropped == []


# ---- The two signals, each tested independently ----

def test_drop_hallucinated_text_objects_catches_contradiction_with_normal_bbox():
    """Signal 1 (contradiction) alone, with signal 2 (full-frame bbox) explicitly absent
    - a NORMAL, non-full-frame bbox - proves the contradiction check is not silently
    riding on the bbox check to do its job."""
    testimonial = _text_obj(
        "obj_testimonial", "customer testimonial quote",
        bbox=(0.1, 0.6, 0.5, 0.2), text_purpose="testimonial",
    )
    bp = _base_bp([testimonial],
                  text_zone="external to image - full ad copy delivered as scraped text",
                  legibility_notes="no text is overlaid on the image itself")
    kept, dropped = deconstruct.drop_hallucinated_text_objects(bp)
    assert kept == []
    assert len(dropped) == 1
    assert dropped[0]["object_id"] == "obj_testimonial"
    assert dropped[0]["reasons"] == ["contradicts_no_in_image_text"]


def test_drop_hallucinated_text_objects_catches_fullframe_bbox_without_contradiction():
    """Signal 2 (full-frame bbox) alone, with signal 1 (contradiction) explicitly absent
    - text_zone/legibility_notes make no 'no in-image text' claim - proves the bbox
    check is not silently riding on the contradiction check to do its job."""
    testimonial = _text_obj(
        "obj_testimonial", "customer testimonial quote",
        bbox=(0.0, 0.0, 1.0, 1.0), text_purpose="testimonial",
    )
    bp = _base_bp([testimonial], text_zone="testimonial card centred in frame",
                  legibility_notes="fully legible at feed size")
    kept, dropped = deconstruct.drop_hallucinated_text_objects(bp)
    assert kept == []
    assert len(dropped) == 1
    assert dropped[0]["object_id"] == "obj_testimonial"
    assert dropped[0]["reasons"] == ["full_frame_bbox"]


def test_drop_hallucinated_text_objects_near_fullframe_bbox_also_caught():
    """'Near-full-frame', not only an exact [0,0,1,1] - area 0.9025 clears the
    threshold without being a literal full-frame bbox."""
    obj = _text_obj("obj_t", "long narrative text", bbox=(0.0, 0.0, 0.95, 0.95))
    bp = _base_bp([obj])
    kept, dropped = deconstruct.drop_hallucinated_text_objects(bp)
    assert kept == []
    assert dropped[0]["reasons"] == ["full_frame_bbox"]


def test_drop_hallucinated_text_objects_large_but_legitimate_bbox_not_caught():
    """A genuinely large in-image text block (a big bold headline spanning most of the
    frame's width and half its height, area 0.6) must not be caught by the bbox check -
    proves the threshold discriminates 'large' from 'full-frame', not just 'large'."""
    obj = _text_obj("obj_headline", "large bold headline", bbox=(0.0, 0.0, 1.0, 0.6))
    bp = _base_bp([obj])
    kept, dropped = deconstruct.drop_hallucinated_text_objects(bp)
    assert kept == [obj]
    assert dropped == []


# ---- Both known real shapes, reproduced structurally (no ad_id/page_id) ----

def test_drop_hallucinated_text_objects_catches_artifact_shape_caption_below_image():
    """Reproduces the first known shape: three caption-derived text objects, a
    layout_detail.text_zone stating the caption sits BELOW the image (not overlaid),
    and legibility_notes confirming no text is overlaid - matching structure only."""
    text_objects = [
        _text_obj("obj_06", "hook line from the scraped caption", bbox=(0.0, 0.0, 1.0, 1.0)),
        _text_obj("obj_07", "body narrative from the scraped caption", bbox=(0.0, 0.0, 1.0, 1.0)),
        _text_obj("obj_08", "CTA line from the scraped caption", bbox=(0.3, 0.9, 0.4, 0.06)),
    ]
    person = _person_obj()
    bp = _base_bp([person] + text_objects,
                  text_zone="caption below image (not overlaid)",
                  legibility_notes="no text is overlaid on the image itself; all copy "
                                    "is in the caption and fully legible at feed size")
    kept, dropped = deconstruct.drop_hallucinated_text_objects(bp)
    assert kept == [person]
    assert {d["object_id"] for d in dropped} == {"obj_06", "obj_07", "obj_08"}


def test_drop_hallucinated_text_objects_catches_artifact_shape_external_scraped_text():
    """Reproduces the second known shape: a full-frame testimonial object plus a
    smaller-bbox CTA object, both caption-derived, with layout_detail.text_zone and
    legibility_notes both stating the copy is external/not overlaid."""
    testimonial = _text_obj(
        "obj_07", "long-form first-person narrative testimonial",
        bbox=(0.0, 0.0, 1.0, 1.0), text_purpose="testimonial",
    )
    cta = _text_obj(
        "obj_08", "CTA button label reading 'SHOP NOW'",
        bbox=(0.3, 0.93, 0.4, 0.06), text_purpose="cta",
    )
    person = _person_obj()
    bp = _base_bp(
        [person, testimonial, cta],
        text_zone="external to image - full ad copy delivered as scraped text block "
                   "below or alongside the image in the feed unit",
        legibility_notes="no text is overlaid on the image itself; all copy is "
                          "delivered as external scraped text - fully legible at any size",
    )
    kept, dropped = deconstruct.drop_hallucinated_text_objects(bp)
    assert kept == [person]
    dropped_by_id = {d["object_id"]: d["reasons"] for d in dropped}
    assert set(dropped_by_id) == {"obj_07", "obj_08"}
    # obj_07 trips BOTH signals (full-frame AND the blueprint-level contradiction);
    # obj_08 trips only the contradiction (its own bbox is not full-frame) - the
    # blueprint-level signal still catches it, proving signal 2 applies per-blueprint,
    # not only to the object that also happens to be full-frame.
    assert set(dropped_by_id["obj_07"]) == {"full_frame_bbox", "contradicts_no_in_image_text"}
    assert dropped_by_id["obj_08"] == ["contradicts_no_in_image_text"]


# ---- End-to-end: build_image_prompt itself never receives the hallucinated object ----
#
# build_image_prompt now performs a DB write (dedupe.record_warning) on the rare path
# where it actually drops something, so every test below that constructs a hallucinated
# object mocks dedupe - see the co-location note in build_image_prompt's own docstring
# for why this trade-off (no longer a strictly pure function) was made deliberately.

def _mock_dedupe_warnings(monkeypatch):
    from src import dedupe
    warnings = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))
    return warnings


def test_build_image_prompt_never_receives_hallucinated_text_object(monkeypatch):
    _mock_dedupe_warnings(monkeypatch)
    testimonial = _text_obj(
        "obj_07", "long-form first-person narrative testimonial beginning "
                  "'I finally found something that actually works for my skin.'",
        bbox=(0.0, 0.0, 1.0, 1.0), text_purpose="testimonial",
    )
    person = _person_obj(description="woman with grey hair on a sofa")
    bp = _base_bp(
        [person, testimonial],
        text_zone="external to image - full ad copy delivered as scraped text",
        legibility_notes="no text is overlaid on the image itself",
    )
    prompt = generate_image_prompt.build_image_prompt(bp)
    # The legitimate object still reaches the prompt - proves this isn't just an empty
    # SCENE OBJECTS block from the whole objects list being wiped out.
    assert "woman with grey hair on a sofa" in prompt
    # The hallucinated object's own description must not appear at all.
    assert "long-form first-person narrative testimonial" not in prompt
    assert "I finally found something that actually works for my skin" not in prompt


def test_build_image_prompt_caption_derived_text_never_leaks_any_substring(monkeypatch):
    """Leak-guard, same pattern as the per-object brand-field leak test
    (test_objects_clause_end_to_end_brand_field_never_leaks_into_prompt,
    tests/test_generate_image_prompt.py): given a blueprint containing a caption-derived
    text object, no substring of that caption's distinctive wording appears anywhere in
    build_image_prompt's output - checked against several distinct fragments, not just
    the object's own `description` field, since a leak could in principle occur via a
    different route (e.g. quoted verbatim in generated_copy) even if `description`
    itself were somehow scrubbed."""
    _mock_dedupe_warnings(monkeypatch)
    caption_fragments = [
        "Still have loose crept skin",
        "Kathleen T.",
        "juter one bottle",
    ]
    testimonial = _text_obj(
        "obj_07",
        "long-form first-person narrative ad copy: 'Still have loose crept skin but "
        "better moisturized after juter one bottle' - attributed to Kathleen T.",
        bbox=(0.0, 0.0, 1.0, 1.0), text_purpose="testimonial",
    )
    bp = _base_bp(
        [_person_obj(), testimonial],
        text_zone="external to image - full ad copy delivered as scraped text",
        legibility_notes="no text is overlaid on the image itself",
    )
    prompt = generate_image_prompt.build_image_prompt(bp, testimonial={
        "quote": "Still have loose crept skin but better moisturized after just one bottle",
        "attribution": "Kathleen T.",
    })
    for fragment in caption_fragments:
        assert fragment not in prompt, f"{fragment!r} leaked into the built prompt"


def test_build_image_prompt_records_warning_directly_without_generate_image(monkeypatch):
    """Proves co-location, not just that SOME caller eventually warns: calling
    build_image_prompt DIRECTLY - bypassing generate_image and
    pipeline._regenerate_existing_draft entirely, the only two callers that used to be
    trusted to call the warning separately - still fires the warning. This is the test
    that would have caught the original design's gap: a hypothetical third caller of
    build_image_prompt (or a test like this one) that never knew it needed to also call
    drop_hallucinated_text_objects itself for the trace."""
    warnings = _mock_dedupe_warnings(monkeypatch)
    testimonial = _text_obj(
        "obj_07", "long-form first-person narrative testimonial",
        bbox=(0.0, 0.0, 1.0, 1.0), text_purpose="testimonial",
    )
    bp = _base_bp(
        [_person_obj(), testimonial],
        text_zone="external to image - full ad copy delivered as scraped text",
        legibility_notes="no text is overlaid on the image itself",
        ad_id="AD_DIRECT_CALL",
    )
    generate_image_prompt.build_image_prompt(bp)
    assert len(warnings) == 1
    kind, detail = warnings[0]
    assert kind == "hallucinated_text_object_dropped"
    assert "obj_07" in detail
    assert "AD_DIRECT_CALL" in detail


def test_build_image_prompt_records_no_warning_when_nothing_dropped(monkeypatch):
    """Negative control for the direct-call test above."""
    warnings = _mock_dedupe_warnings(monkeypatch)
    headline = _text_obj(
        "obj_headline", "main headline", bbox=(0.05, 0.05, 0.9, 0.15), text_purpose="headline",
    )
    bp = _base_bp([_person_obj(), headline],
                  text_zone="headline top-centre, product mid-frame",
                  legibility_notes="headline is bold and fully legible at feed size")
    generate_image_prompt.build_image_prompt(bp)
    assert warnings == []


# ---- Visible trace via the two real production callers - both still work now that they
# no longer call the drop function themselves, only inherit it from build_image_prompt ----

class _CapturingGenaiClient:
    """Minimal stand-in for genai.Client, same shape as tests/test_edit_mode.py's own -
    duplicated here rather than imported so this file stays self-contained (per this
    task's own instruction to keep it in a dedicated file)."""
    last_contents = None

    def __init__(self, *a, **k):
        self.models = self

    def generate_content(self, model, contents, config=None):
        _CapturingGenaiClient.last_contents = contents
        part = type("Part", (), {"inline_data": type("Data", (), {"data": b"fake-png-bytes"})()})()
        candidate = type("Candidate", (), {"content": type("Content", (), {"parts": [part]})()})()
        return type("Response", (), {"candidates": [candidate]})()


def test_generate_image_records_warning_when_dropping_hallucinated_text_object(monkeypatch, tmp_path):
    """Per the standing rule (CLAUDE.md: 'no failure in the generation pipeline may
    produce an artifact that looks clean'), a rejected object must leave a visible
    trace - here, a pipeline_warnings row via dedupe.record_warning. Note the warning
    text names blueprint["ad_id"] (build_image_prompt has no separate ad_id parameter
    of its own), not generate_image's own ad_id argument - in production the two are
    always the same value (both derived from the same scraped ad at deconstruct time),
    so this fixture sets them to match, same as any real blueprint would."""
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    warnings = _mock_dedupe_warnings(monkeypatch)

    testimonial = _text_obj(
        "obj_07", "long-form first-person narrative testimonial",
        bbox=(0.0, 0.0, 1.0, 1.0), text_purpose="testimonial",
    )
    bp = _base_bp(
        [_person_obj(), testimonial],
        text_zone="external to image - full ad copy delivered as scraped text",
        legibility_notes="no text is overlaid on the image itself",
        ad_id="AD_HALLUCINATED_TEXT",
    )
    # include_product=False - keeps this test isolated to the text-object behaviour
    # under test, avoiding the separate (and separately warned-about)
    # product-cutout-fetch path entirely.
    generate_image_prompt.generate_image(bp, "AD_HALLUCINATED_TEXT", include_product=False)

    assert len(warnings) == 1
    kind, detail = warnings[0]
    assert kind == "hallucinated_text_object_dropped"
    assert "obj_07" in detail
    assert "AD_HALLUCINATED_TEXT" in detail


def test_generate_image_records_no_warning_when_objects_are_legitimate(monkeypatch, tmp_path):
    """Negative control for the test above - a clean blueprint (no hallucinated object)
    must not fire this warning at all, proving it's conditional, not unconditional
    noise on every generation."""
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)
    warnings = _mock_dedupe_warnings(monkeypatch)

    headline = _text_obj(
        "obj_headline", "main headline", bbox=(0.05, 0.05, 0.9, 0.15), text_purpose="headline",
    )
    bp = _base_bp([_person_obj(), headline],
                  text_zone="headline top-centre, product mid-frame",
                  legibility_notes="headline is bold and fully legible at feed size")
    generate_image_prompt.generate_image(bp, "AD_CLEAN", include_product=False)

    assert warnings == []


# ---- reference_has_text_zone (2026-08-19 extension): must also return False when the
# blueprint-level "no in-image text" signal fires, regardless of headline_verbatim/
# objects - closes the gap where pipeline.py:886 calls this directly on the RAW,
# unfiltered blueprint (for element_provenance["text"] bookkeeping) BEFORE
# build_image_prompt's own guard ever runs, which could otherwise record
# element_provenance.text="substituted" for an object the actual generation then
# correctly drops - the same record-vs-reality mismatch shape as the 2026-08-14
# element_provenance.product="added" bug (CLAUDE.md). ----

def test_reference_has_text_zone_signal_overrides_headline_verbatim():
    """The blueprint-level signal wins even when headline_verbatim is (contradictorily)
    populated - the same 'blueprint-level statement beats a single field' precedence
    deconstruct.drop_hallucinated_text_objects already uses."""
    bp = {
        "headline_verbatim": "Skin that finally feels like yours",
        "layout_detail": {"text_zone": "external to image - full ad copy delivered as scraped text"},
        "legibility_notes": "no text is overlaid on the image itself",
    }
    assert generate_image_prompt.reference_has_text_zone(bp) is False


def test_reference_has_text_zone_signal_overrides_hallucinated_text_purpose_object():
    """The exact shape of the real gap: an objects[] entry with text_purpose='headline'
    (which _objects_have_text_purpose alone would read as True) is overridden by the
    blueprint-level no-in-image-text signal."""
    hallucinated_headline = _text_obj(
        "obj_01", "hook line from the scraped caption", bbox=(0.0, 0.0, 1.0, 1.0),
        text_purpose="headline",
    )
    bp = _base_bp(
        [hallucinated_headline],
        text_zone="external to image - full ad copy delivered as scraped text",
        legibility_notes="no text is overlaid on the image itself",
    )
    assert generate_image_prompt.reference_has_text_zone(bp) is False


def test_reference_has_text_zone_unaffected_when_no_signal_present():
    """Regression guard for the three pre-existing tests in test_edit_mode.py
    (test_reference_has_text_zone_true_for_headline/true_for_text_bearing_object/
    false_when_neither) - none of them set layout_detail/legibility_notes at all, so
    this extension must be a no-op for every blueprint that doesn't trip the signal."""
    assert generate_image_prompt.reference_has_text_zone(
        {"headline_verbatim": "Feel confident again"}
    ) is True
    assert generate_image_prompt.reference_has_text_zone({}) is False
