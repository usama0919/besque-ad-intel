"""Tests for export_drafts.py's failed-review exclusion (critic gate, 2026-08-10) -
a draft the output critic still found HIGH-confidence after its one corrective retry
must never reach an export by default."""
import uuid

from src import dedupe
import export_drafts


def _make_artifact(review_status):
    dedupe.init_artifacts()
    ad_id = f"ART_{uuid.uuid4().hex[:8]}"
    dedupe.save_artifact(
        ad_id=ad_id, page_name="TestBrand", image_path="assets/x.jpg",
        blueprint={"format": "hero"}, generated_copy={"headline": "H"},
        draft_image="assets/x_draft.png",
        metadata={"cta": "Shop", "destination_url": "http://x"},
    )
    dedupe.update_artifact_findings(
        ad_id,
        [{"category": "unauthorised text", "description": "x", "confidence": "high"}]
        if review_status == "failed-review" else [],
        review_status=review_status,
    )
    return ad_id


def test_fetch_rows_excludes_failed_review_by_default():
    ok_id = _make_artifact("ok")
    failed_id = _make_artifact("failed-review")
    try:
        rows, excluded_count = export_drafts.fetch_rows(competitor_id=None, angle_arg=None,
                                                          since_arg=None, limit=None)
        ad_ids = {r["ad_id"] for r in rows}
        assert ok_id in ad_ids
        assert failed_id not in ad_ids
        assert excluded_count >= 1
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id IN (%s, %s)", (ok_id, failed_id))
            conn.commit()


def test_fetch_rows_includes_failed_review_when_flag_set():
    failed_id = _make_artifact("failed-review")
    try:
        rows, excluded_count = export_drafts.fetch_rows(competitor_id=None, angle_arg=None,
                                                          since_arg=None, limit=None,
                                                          include_failed=True)
        assert failed_id in {r["ad_id"] for r in rows}
        assert excluded_count == 0  # nothing was excluded - include_failed=True skips the count query
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM artifacts WHERE ad_id=%s", (failed_id,))
            conn.commit()
