"""Native-path bottle identity fix (2026-08-19). Confirmed live, four ads on
2026-08-19 (21:37 among them): Gemini invented a wholly wrong bottle - wrong typeface,
no gold border bands, no MAGIC wordmark, wrong cert icons - with _bottle_identity_clause
and _bottle_geometry_clause BOTH present in the prompt. Text-only identity facts do not
reliably bind on the image path (the same class of failure CLAUDE.md documents
repeatedly). The product cutout (a real, background-removed photo) was already being
attached as an image Part (2026-08-16) but with no framing distinguishing it from any
other configured product reference photo - this file covers the fix: an explicit
authority framing on the native path only, and the silent-failure fix to
_fetch_product_cutout_bytes (a failure was visible only as a python log.warning,
cached process-wide forever, meaning one transient failure at process start could
silently starve every generation after it)."""
import io
from PIL import Image
from src import generate_image_prompt, dedupe


def _png_bytes(width=100, height=100, color=(200, 150, 100)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _blueprint_no_objects():
    """No objects[] at all - _composite_gate fails at gate 1 ("found 0"), so
    should_composite is always False here regardless of anything else. The simplest
    reliable way to force the native path."""
    return {"visual": {"layout": "portrait, subject centered"}}


def _blueprint_with_substitute_product():
    """Mirrors _composite_gate's own requirements exactly - one product object,
    disposition substitute, valid bbox, no held language, no overlap, soft light -
    so _composite_gate returns proceed=True and should_composite is True."""
    return {
        "objects": [
            {"object_id": "obj_01", "kind": "product", "disposition": "substitute",
             "bbox": [0.3, 0.4, 0.2, 0.35], "description": "amber body oil bottle"},
        ],
        "background": {"light": "soft warm light from upper-left"},
    }


class _CapturingGenaiClient:
    """Same pattern as test_edit_mode.py's own _CapturingGenaiClient - captures the
    exact `contents` passed to generate_content so tests can inspect which Parts were
    attached and what framing text accompanied them."""
    last_contents = None

    def __init__(self, *a, **k):
        self.models = self

    def generate_content(self, model, contents, config=None):
        _CapturingGenaiClient.last_contents = contents
        part = type("Part", (), {"inline_data": type("Data", (), {"data": b"fake-png-bytes"})()})()
        candidate = type("Candidate", (), {"content": type("Content", (), {"parts": [part]})()})()
        return type("Response", (), {"candidates": [candidate]})()


def _install_fake_client(monkeypatch, tmp_path):
    monkeypatch.setattr(generate_image_prompt, "genai", type("obj", (), {"Client": _CapturingGenaiClient}))
    monkeypatch.setattr(generate_image_prompt, "ASSET_DIR", tmp_path)


# ---- Authority framing: native path only ----

def test_native_path_attaches_cutout_and_labels_it_authoritative(monkeypatch, tmp_path):
    _install_fake_client(monkeypatch, tmp_path)
    cutout_bytes = _png_bytes(color=(255, 0, 0))
    monkeypatch.setattr(generate_image_prompt, "_fetch_product_cutout_bytes", lambda: cutout_bytes)

    generate_image_prompt.generate_image(
        _blueprint_no_objects(), "AD_NATIVE", include_product=True, realism="ugc",
    )
    contents = _CapturingGenaiClient.last_contents
    assert isinstance(contents, list)
    assert any(getattr(p, "inline_data", None) and p.inline_data.data == cutout_bytes for p in contents[:-1])
    assert "CLEAN, BACKGROUND-REMOVED CUTOUT OF THE REAL BESQUE BOTTLE" in contents[-1]
    # 2026-08-19 (attempt A, shape gap): the photo is now authoritative for proportions/
    # silhouette/hardware form too, not just identity - the old exclusion sentence
    # ("geometry for shape, this image for identity") must be GONE, and the geometry
    # clause's numbers must be reframed as a cross-check, never a separate sufficient
    # source for shape.
    assert "geometry for shape, this image for identity" not in contents[-1]
    assert "the bottle's actual proportions, silhouette" in contents[-1]
    assert "cross-check" in contents[-1]
    # _bottle_geometry_clause itself is UNCHANGED and still composed into the prompt -
    # this attempt only changes what the framing text says its ROLE is, never removes
    # or edits the numbers themselves.
    assert generate_image_prompt._bottle_geometry_clause() in contents[-1]


def test_compositing_path_attaches_cutout_but_does_not_label_it(monkeypatch, tmp_path):
    """should_composite=True: the cutout gets pasted directly after generation and
    Gemini is told not to draw a bottle at all - an identity-authority framing has
    nothing useful to bind to, so it must not appear, even though the cutout is still
    attached (unchanged existing behaviour, not narrowed by this fix)."""
    _install_fake_client(monkeypatch, tmp_path)
    cutout_bytes = _png_bytes(color=(255, 0, 0))
    monkeypatch.setattr(generate_image_prompt, "_fetch_product_cutout_bytes", lambda: cutout_bytes)

    generate_image_prompt.generate_image(
        _blueprint_with_substitute_product(), "AD_COMPOSITE", include_product=True, realism="ugc",
    )
    contents = _CapturingGenaiClient.last_contents
    assert isinstance(contents, list)
    assert any(getattr(p, "inline_data", None) and p.inline_data.data == cutout_bytes for p in contents[:-1])
    assert "CLEAN, BACKGROUND-REMOVED CUTOUT OF THE REAL BESQUE BOTTLE" not in contents[-1]


def test_illustrated_style_still_omits_cutout_and_framing(monkeypatch, tmp_path):
    """Existing behaviour (2026-08-16), confirmed unchanged: illustrated register never
    gets the cutout at all (a real photo must not bleed photographic register into a
    drawing) - so there is nothing for the authority framing to attach to either."""
    _install_fake_client(monkeypatch, tmp_path)
    fetch_calls = []

    def _spy_fetch():
        fetch_calls.append(1)
        return _png_bytes()
    monkeypatch.setattr(generate_image_prompt, "_fetch_product_cutout_bytes", _spy_fetch)

    generate_image_prompt.generate_image(
        _blueprint_no_objects(), "AD_ILLUSTRATED", include_product=True, realism="illustrated",
    )
    assert fetch_calls == []  # never even attempted
    contents = _CapturingGenaiClient.last_contents
    prompt_text = contents[-1] if isinstance(contents, list) else contents
    assert "CLEAN, BACKGROUND-REMOVED CUTOUT OF THE REAL BESQUE BOTTLE" not in prompt_text


def test_no_product_no_cutout_no_framing(monkeypatch, tmp_path):
    """include_product=False: no bottle belongs in the scene at all, so no cutout and
    no framing - existing productless-mode behaviour, confirmed unaffected."""
    _install_fake_client(monkeypatch, tmp_path)
    fetch_calls = []
    monkeypatch.setattr(generate_image_prompt, "_fetch_product_cutout_bytes",
                        lambda: fetch_calls.append(1) or _png_bytes())

    generate_image_prompt.generate_image(
        _blueprint_no_objects(), "AD_NO_PRODUCT", include_product=False, realism="ugc",
    )
    assert fetch_calls == []
    contents = _CapturingGenaiClient.last_contents
    prompt_text = contents[-1] if isinstance(contents, list) else contents
    assert "CLEAN, BACKGROUND-REMOVED CUTOUT OF THE REAL BESQUE BOTTLE" not in prompt_text


def test_fetch_failure_never_claims_a_photo_is_attached(monkeypatch, tmp_path):
    """cutout_bytes is falsy when the fetch fails - the authority framing (and the
    Part itself) must never appear, never a framing claiming a photo is attached when
    none actually is."""
    _install_fake_client(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_image_prompt, "_fetch_product_cutout_bytes", lambda: None)

    generate_image_prompt.generate_image(
        _blueprint_no_objects(), "AD_FETCH_FAILED", include_product=True, realism="ugc",
    )
    contents = _CapturingGenaiClient.last_contents
    prompt_text = contents[-1] if isinstance(contents, list) else contents
    assert "CLEAN, BACKGROUND-REMOVED CUTOUT OF THE REAL BESQUE BOTTLE" not in prompt_text


# ---- _fetch_product_cutout_bytes: silent-failure fix ----

class _FakeBlobMissing:
    def exists(self):
        return False


class _FakeBucketMissing:
    def blob(self, key):
        return _FakeBlobMissing()


class _FakeStorageClientMissing:
    def __init__(self, *a, **k):
        pass

    def bucket(self, name):
        return _FakeBucketMissing()


class _FakeBlobFound:
    def exists(self):
        return True

    def download_as_bytes(self):
        return b"real-cutout-bytes"


class _FakeBucketFound:
    def blob(self, key):
        return _FakeBlobFound()


class _FakeStorageClientFound:
    def __init__(self, *a, **k):
        pass

    def bucket(self, name):
        return _FakeBucketFound()


def _reset_cutout_cache(monkeypatch):
    monkeypatch.setattr(generate_image_prompt, "_product_cutout_cache_populated", False)
    monkeypatch.setattr(generate_image_prompt, "_product_cutout_bytes_cache", None)
    monkeypatch.setattr(generate_image_prompt, "_product_cutout_fetch_failure_warned", False)


def test_fetch_failure_records_a_pipeline_warning(monkeypatch):
    _reset_cutout_cache(monkeypatch)
    monkeypatch.setattr("google.cloud.storage.Client", _FakeStorageClientMissing)
    warnings = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    result = generate_image_prompt._fetch_product_cutout_bytes()

    assert result is None
    assert len(warnings) == 1
    assert warnings[0][0] == "product_cutout_fetch_failed"
    assert "asset bucket" in warnings[0][1]


def test_fetch_failure_warning_recorded_only_once_per_process(monkeypatch):
    """The cache is sticky (by design, unchanged) - a second call within the same
    process must NOT record a second warning, or an operator would get spammed on
    every single ad rather than once per process lifetime."""
    _reset_cutout_cache(monkeypatch)
    monkeypatch.setattr("google.cloud.storage.Client", _FakeStorageClientMissing)
    warnings = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    generate_image_prompt._fetch_product_cutout_bytes()
    generate_image_prompt._fetch_product_cutout_bytes()
    generate_image_prompt._fetch_product_cutout_bytes()

    assert len(warnings) == 1


def test_fetch_success_never_records_a_warning(monkeypatch):
    _reset_cutout_cache(monkeypatch)
    monkeypatch.setattr("google.cloud.storage.Client", _FakeStorageClientFound)
    warnings = []
    monkeypatch.setattr(dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    result = generate_image_prompt._fetch_product_cutout_bytes()

    assert result == b"real-cutout-bytes"
    assert warnings == []


def test_fetch_exception_also_records_a_warning(monkeypatch):
    """The existing except-Exception branch (network error, auth failure, anything)
    must reach the same warning path as a missing blob - both are "fetch failed"."""
    _reset_cutout_cache(monkeypatch)

    class _RaisingClient:
        def __init__(self, *a, **k):
            raise RuntimeError("simulated ADC failure")
    monkeypatch.setattr("google.cloud.storage.Client", _RaisingClient)
    warnings = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    result = generate_image_prompt._fetch_product_cutout_bytes()

    assert result is None
    assert len(warnings) == 1
    assert warnings[0][0] == "product_cutout_fetch_failed"


# ---- Retry-on-failure fix (2026-08-19): failure must NOT be cached, so a transient
# blip that clears on its own is picked up by the very next call - no restart needed.
# Only the warning stays limited to once per process. ----

def test_failed_fetch_is_not_cached_next_call_retries_and_succeeds(monkeypatch):
    """The actual behaviour this task requests: a failed fetch followed by a
    successful one attaches the cutout on the second call - failure does not poison
    the process the way a cached success (correctly) does."""
    _reset_cutout_cache(monkeypatch)
    attempts = []

    class _FlakyClient:
        def __init__(self, *a, **k):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("simulated transient GCS blip")

        def bucket(self, name):
            return _FakeBucketFound()
    monkeypatch.setattr("google.cloud.storage.Client", _FlakyClient)
    warnings = []
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning", lambda kind, detail: warnings.append((kind, detail)))

    first = generate_image_prompt._fetch_product_cutout_bytes()
    second = generate_image_prompt._fetch_product_cutout_bytes()

    assert first is None
    assert second == b"real-cutout-bytes"
    assert len(attempts) == 2  # genuinely retried, not short-circuited by a cached failure
    assert len(warnings) == 1  # the fetch retried twice, but the warning still fired only once


def test_success_is_still_cached_after_a_prior_failure(monkeypatch):
    """Once a retry succeeds, that success IS cached process-wide as normal - a third
    call must not trigger a third fetch attempt at all."""
    _reset_cutout_cache(monkeypatch)
    attempts = []

    class _FlakyClient:
        def __init__(self, *a, **k):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("simulated transient GCS blip")

        def bucket(self, name):
            return _FakeBucketFound()
    monkeypatch.setattr("google.cloud.storage.Client", _FlakyClient)
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning", lambda *a, **k: None)

    generate_image_prompt._fetch_product_cutout_bytes()  # fails, attempt 1
    generate_image_prompt._fetch_product_cutout_bytes()  # succeeds, attempt 2, now cached
    third = generate_image_prompt._fetch_product_cutout_bytes()  # must use the cache

    assert third == b"real-cutout-bytes"
    assert len(attempts) == 2  # no third real fetch attempt


def test_repeated_failures_retry_every_call_never_cached(monkeypatch):
    """A sustained outage (every call fails) must genuinely retry every single time,
    never settle into a cached None - the whole point of this fix."""
    _reset_cutout_cache(monkeypatch)
    monkeypatch.setattr("google.cloud.storage.Client", _FakeStorageClientMissing)
    monkeypatch.setattr(dedupe, "init_pipeline_warnings", lambda: None)
    monkeypatch.setattr(dedupe, "record_warning", lambda *a, **k: None)

    results = [generate_image_prompt._fetch_product_cutout_bytes() for _ in range(3)]

    assert results == [None, None, None]
    assert generate_image_prompt._product_cutout_cache_populated is False
