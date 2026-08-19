"""Regression protection for the silent-failure class the 2026-08-19 critic-retry-hole
fix belongs to: process_ad's own image-generation/critic retry loop (src/pipeline.py,
around MAX_IMAGE_ATTEMPTS/the corrective-retry `for` loop) must never let a HIGH-
confidence finding be LOST TO A FAILURE - a retry that errors, returns no image, is
skipped, or whose own critic check itself never runs - and must never let review_status
land on "ok" when the critic mechanism failed to produce any verdict at all. Neither is
the same as "a HIGH finding can never be cleared": a later attempt that actually
completes and comes back with a genuinely clean verdict of its own DOES correctly
supersede an earlier HIGH finding and end review_status="ok" - see
test_retry_succeeded_clean_ends_ok below, the one case in this file that expects "ok",
not "failed-review".

Confirmed live on artifact 1386: four HIGH findings including C6 nudity/sexualised
content and C1 subject identity, saved as review_status="ok" because attempt 2's own
generate_image() call raised and the loop broke via the missing-image path before ever
reaching the review_status="failed-review" assignment, which lived only inside the
"retry exhausted and still HIGH" branch.

All live stages monkeypatched - no network, no spend, no real DB (uses the same
DB-independent mocking pattern tests/test_pipeline.py's own corrective-retry-loop tests
use, `_mock_dedupe_db`/`_mock_ad_and_early_stages`, duplicated locally here rather than
imported across test files, matching this codebase's existing per-file convention)."""
import uuid
from src import pipeline


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


def _mock_persistence(monkeypatch):
    """Captures save_artifact's draft_image and every update_artifact_findings call, in
    order, as (findings, review_status) tuples - and every record_warning call as
    (kind, detail). Returns the three lists/dict for the test to assert against."""
    saved = {}
    findings_calls = []
    warnings = []
    monkeypatch.setattr(pipeline.dedupe, "save_artifact", lambda **k: saved.update(k))
    monkeypatch.setattr(
        pipeline.dedupe, "update_artifact_findings",
        lambda ad_id, findings, angle_id=None, review_status="ok": findings_calls.append((findings, review_status)),
    )
    monkeypatch.setattr(pipeline.dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))
    return saved, findings_calls, warnings


def _write_png(tmp_path, name, content=b"fake"):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + content)
    return str(p)


HIGH_1386_SHAPE = [
    {"category": "TESTIMONIAL - GARBLED TEXT (C2 / rule 6)",
     "description": "authorised quote reads 'just one bottle' but rendered as 'juter one bottle'",
     "confidence": "high"},
    {"category": "UNAUTHORISED TEXT / EXTRA CTA (rule 6)",
     "description": "a SHOP NOW button is rendered though no CTA was authorised",
     "confidence": "high"},
    {"category": "NUDITY OR SEXUALISED CONTENT (C6)",
     "description": "subject shown in a low-cut bikini top, framing reads as sexualised",
     "confidence": "high"},
    {"category": "SUBJECT IDENTITY (C1 / compliance rule)",
     "description": "the generated subject is recognisably the same individual as the reference",
     "confidence": "high"},
]


# ---- 1/6: reproduce artifact 1386's exact shape ----

def test_high_on_attempt1_then_generate_image_raises_on_retry_ends_failed_review(monkeypatch, tmp_path):
    """Artifact 1386's exact shape: attempt 1 finds 4 HIGH findings (including C6/C1),
    attempt 2's own generate_image() raises. The draft must be KEPT (attempt 1's), and the
    artifact must end review_status='failed-review' with a record_warning row - not "ok"
    with nothing in the warnings feed, which is what shipped live before this fix."""
    _mock_dedupe_db(monkeypatch)
    ad_id, ad = _mock_ad_and_early_stages(monkeypatch)
    saved, findings_calls, warnings = _mock_persistence(monkeypatch)

    draft_v1 = _write_png(tmp_path, "draft_v1.png", b"v1")

    gen_calls = []
    def fake_generate_image(bp, aid, product=None, reference_images=None, **k):
        gen_calls.append(k)
        if len(gen_calls) == 1:
            return draft_v1
        raise TypeError("'NoneType' object is not iterable")  # attempt 2: Gemini returned no candidates
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", fake_generate_image)

    check_calls = []
    def fake_check_draft(*a, **k):
        check_calls.append(1)
        return HIGH_1386_SHAPE
    monkeypatch.setattr(pipeline.output_critic, "check_draft", fake_check_draft)

    assert pipeline.process_ad(ad, check_output=True) == "processed"
    assert len(gen_calls) == 2, "must have attempted the retry, not given up after attempt 1"
    assert len(check_calls) == 1, "attempt 2 never produced an image, so its own critic pass never ran"
    assert saved["draft_image"] == draft_v1, "attempt 1's draft must be kept, never lost"
    assert findings_calls == [(HIGH_1386_SHAPE, "failed-review")], (
        "the ACTUAL high findings must be persisted alongside failed-review, not an empty "
        "list next to a status nobody can explain"
    )
    assert any(kind == "critic_high_after_retry" for kind, detail in warnings), (
        "a record_warning row must fire even though the retry itself never completed"
    )


# ---- 2/6: a HIGH finding on attempt 1 survives any FAILURE on the retry (errors, no
# image, skipped, or its own critic never running) and ends failed-review - but is
# correctly SUPERSEDED when the retry actually completes and comes back genuinely clean,
# which must end 'ok'. Four cases, one shared harness. ----

def _high_then(monkeypatch, tmp_path, retry_behavior):
    """Shared harness: attempt 1 always finds HIGH_1386_SHAPE[:1] (one HIGH finding).
    retry_behavior configures what attempt 2 does. Returns (saved, findings_calls,
    warnings, gen_calls, check_calls)."""
    _mock_dedupe_db(monkeypatch)
    ad_id, ad = _mock_ad_and_early_stages(monkeypatch)
    saved, findings_calls, warnings = _mock_persistence(monkeypatch)

    draft_v1 = _write_png(tmp_path, "draft_v1.png", b"v1")
    draft_v2 = _write_png(tmp_path, "draft_v2.png", b"v2")

    gen_calls = []
    def fake_generate_image(bp, aid, product=None, reference_images=None, **k):
        gen_calls.append(k)
        if len(gen_calls) == 1:
            return draft_v1
        return retry_behavior["generate"](draft_v2)
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image", fake_generate_image)

    check_calls = []
    def fake_check_draft(*a, **k):
        check_calls.append(1)
        if len(check_calls) == 1:
            return HIGH_1386_SHAPE[:1]
        return retry_behavior["check"]()
    monkeypatch.setattr(pipeline.output_critic, "check_draft", fake_check_draft)

    result = pipeline.process_ad(ad, check_output=True)
    return result, saved, findings_calls, warnings, gen_calls, check_calls


def test_retry_succeeded_clean_ends_ok(monkeypatch, tmp_path):
    """The retry produces a NEW draft and its OWN critic pass genuinely comes back clean -
    this must end review_status='ok', exactly as the OLD design already got right. The
    invariant this fix closes is narrower than "never downgrade": a HIGH finding must
    never be LOST TO A FAILURE (an errored/no-image/skipped retry, or one whose own critic
    never ran - see the other three cases below), not that a genuine fix can never clear
    it. A real, completed, clean re-check is exactly the case that SHOULD clear it."""
    result, saved, findings_calls, warnings, gen_calls, check_calls = _high_then(
        monkeypatch, tmp_path,
        {"generate": lambda path: path, "check": lambda: []},
    )
    assert result == "processed"
    assert len(gen_calls) == 2 and len(check_calls) == 2
    assert findings_calls[-1] == ([], "ok"), "a genuinely clean retry must supersede attempt 1's HIGH finding"
    assert not any(kind == "critic_high_after_retry" for kind, detail in warnings)


def test_retry_raised_still_ends_failed_review(monkeypatch, tmp_path):
    """The retry's own generate_image() call raises outright (artifact 1386's shape,
    covered end-to-end in test 1/6 above; this is the same scenario inside the shared
    parametrised harness)."""
    result, saved, findings_calls, warnings, gen_calls, check_calls = _high_then(
        monkeypatch, tmp_path,
        {"generate": lambda path: (_ for _ in ()).throw(RuntimeError("Gemini call failed")), "check": lambda: []},
    )
    assert result == "processed"
    assert len(gen_calls) == 2 and len(check_calls) == 1  # attempt 2 never reached its own critic pass
    assert findings_calls[-1][1] == "failed-review"
    assert findings_calls[-1][0] == HIGH_1386_SHAPE[:1]
    assert any(kind == "critic_high_after_retry" for kind, detail in warnings)


def test_retry_returned_no_candidates_still_ends_failed_review(monkeypatch, tmp_path):
    """The retry's generate_image() returns None cleanly (Gemini returned no candidates -
    generate_image_prompt.py's own `if image_bytes is None: return None`), distinct from
    raising: no exception, just a falsy return that pipeline.py's own `if not
    new_draft_image:` branch must treat identically to the raised case."""
    result, saved, findings_calls, warnings, gen_calls, check_calls = _high_then(
        monkeypatch, tmp_path,
        {"generate": lambda path: None, "check": lambda: []},
    )
    assert result == "processed"
    assert len(gen_calls) == 2 and len(check_calls) == 1
    assert findings_calls[-1][1] == "failed-review"
    assert findings_calls[-1][0] == HIGH_1386_SHAPE[:1]
    assert any(kind == "critic_high_after_retry" for kind, detail in warnings)


def test_retry_still_dirty_still_ends_failed_review(monkeypatch, tmp_path):
    """The retry succeeds and its OWN critic pass ALSO finds a HIGH finding - the
    already-existing "retry exhausted and still HIGH" case, kept green by the
    restructuring (now resolved after the loop, not inline in this branch)."""
    result, saved, findings_calls, warnings, gen_calls, check_calls = _high_then(
        monkeypatch, tmp_path,
        {"generate": lambda path: path, "check": lambda: HIGH_1386_SHAPE[1:2]},
    )
    assert result == "processed"
    assert len(gen_calls) == 2 and len(check_calls) == 2
    assert findings_calls[-1][1] == "failed-review"
    assert findings_calls[-1][0] == HIGH_1386_SHAPE[1:2], "must persist the LATEST high finding when the retry is still dirty"
    assert any(kind == "critic_high_after_retry" for kind, detail in warnings)


# ---- 3/6: critic itself failing to run must mark for manual review, never leave
# review_status unchanged-and-clean ----

def test_critic_api_connection_error_marks_for_manual_review(monkeypatch, tmp_path):
    """The APIConnectionError case: output_critic.check_draft raises (a network/API
    failure surfacing from inside pipeline.py's own wrapping try/except around the call
    - never check_draft's own internal retry-then-CRITIC_CHECK_FAILED_FINDING contract,
    which already converts an internal API/parse failure into a synthetic HIGH finding
    and is exercised instead by test_high_on_attempt1... above via a real HIGH finding).
    Before this fix: recorded a 'critic_failed' warning but left review_status at its
    loop-initial 'ok' - a check that never ran was indistinguishable from a check that
    ran and found nothing."""
    _mock_dedupe_db(monkeypatch)
    ad_id, ad = _mock_ad_and_early_stages(monkeypatch)
    saved, findings_calls, warnings = _mock_persistence(monkeypatch)

    draft_path = _write_png(tmp_path, "draft.png")
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: draft_path)

    def raise_connection_error(*a, **k):
        raise ConnectionError("APIConnectionError: Connection error.")
    monkeypatch.setattr(pipeline.output_critic, "check_draft", raise_connection_error)

    assert pipeline.process_ad(ad, check_output=True) == "processed"
    assert saved["draft_image"] == draft_path, "the draft must still be kept"
    assert any(kind == "critic_failed" for kind, detail in warnings)
    assert findings_calls == [([], "failed-review")], (
        "must be marked for manual review (failed-review), never left at 'ok' just "
        "because there happen to be no findings to show"
    )


def test_critic_missing_draft_file_marks_for_manual_review(monkeypatch, tmp_path):
    """The FileNotFoundError case: generate_image reports success but the path it
    returns doesn't actually exist on disk, so pipeline.py's own
    `_Path(draft_image).read_bytes()` raises BEFORE check_draft is ever called - the
    critic mechanism never got a chance to run at all, the same failure shape observed
    live on the (separate, unreachable-from-production) regenerate path
    ('FileNotFoundError: [Errno 2] No such file or directory')."""
    _mock_dedupe_db(monkeypatch)
    ad_id, ad = _mock_ad_and_early_stages(monkeypatch)
    saved, findings_calls, warnings = _mock_persistence(monkeypatch)

    missing_path = str(tmp_path / "this_file_does_not_exist.png")
    monkeypatch.setattr(pipeline.generate_image_prompt, "generate_image",
                        lambda bp, aid, product=None, reference_images=None, **k: missing_path)

    check_calls = []
    monkeypatch.setattr(pipeline.output_critic, "check_draft", lambda *a, **k: check_calls.append(1))

    assert pipeline.process_ad(ad, check_output=True) == "processed"
    assert check_calls == [], "check_draft must never even be reached - the disk read fails first"
    assert saved["draft_image"] == missing_path, "the draft path is still recorded, even though its bytes are unreadable"
    assert any(kind == "critic_failed" for kind, detail in warnings)
    assert findings_calls == [([], "failed-review")]
