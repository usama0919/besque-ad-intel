"""Tests for the products multi-image (image_keys) and pipeline_warnings additions
to src/dedupe.py. Uses the real DB connection (same pattern as the rest of the
suite - no mocking of dedupe itself), cleaning up every row it creates."""
import uuid
from src import dedupe


def _make_product(**kw):
    dedupe.init_products()
    name = f"__test_{uuid.uuid4().hex[:8]}__"
    return dedupe.add_product(name, **kw)


def test_add_product_image_appends_in_order():
    pid = _make_product()
    try:
        dedupe.add_product_image(pid, "a.png")
        dedupe.add_product_image(pid, "b.png")
        assert dedupe.get_product(pid)["image_keys"] == ["a.png", "b.png"]
    finally:
        dedupe.delete_product(pid)


def test_add_product_image_enforces_cap():
    pid = _make_product()
    try:
        for i in range(dedupe.MAX_PRODUCT_IMAGES):
            dedupe.add_product_image(pid, f"k{i}.png")
        try:
            dedupe.add_product_image(pid, "one_too_many.png")
            assert False, "expected ValueError past the cap"
        except ValueError:
            pass
        # Rejected add must not have been partially applied.
        assert len(dedupe.get_product(pid)["image_keys"]) == dedupe.MAX_PRODUCT_IMAGES
    finally:
        dedupe.delete_product(pid)


def test_remove_product_image_removes_only_the_named_key():
    pid = _make_product()
    try:
        dedupe.add_product_image(pid, "a.png")
        dedupe.add_product_image(pid, "b.png")
        dedupe.remove_product_image(pid, "a.png")
        assert dedupe.get_product(pid)["image_keys"] == ["b.png"]
    finally:
        dedupe.delete_product(pid)


def test_legacy_image_key_untouched_by_multi_image_functions():
    """image_key is frozen: add_product carries it forward as before, and the new
    image_keys functions never read or write it."""
    pid = _make_product()
    try:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE products SET image_key=%s WHERE id=%s", ("legacy.png", pid))
            conn.commit()
        dedupe.add_product_image(pid, "new.png")
        p = dedupe.get_product(pid)
        assert p["image_key"] == "legacy.png"
        assert p["image_keys"] == ["new.png"]
    finally:
        dedupe.delete_product(pid)


def test_visual_description_round_trips_through_add_and_update():
    pid = _make_product(visual_description="amber glass bottle")
    try:
        assert dedupe.get_product(pid)["visual_description"] == "amber glass bottle"
        dedupe.update_product(pid, "renamed", "", "", "", visual_description="updated desc")
        assert dedupe.get_product(pid)["visual_description"] == "updated desc"
    finally:
        dedupe.delete_product(pid)


def test_substance_colour_round_trips_through_add_and_update():
    """Item 6b (2026-08-04): substance_colour is a separate self-migrating column, not
    parsed out of visual_description - same round-trip shape as visual_description above."""
    pid = _make_product(substance_colour="bright golden-amber oil")
    try:
        assert dedupe.get_product(pid)["substance_colour"] == "bright golden-amber oil"
        dedupe.update_product(pid, "renamed", "", "", "", substance_colour="updated colour")
        assert dedupe.get_product(pid)["substance_colour"] == "updated colour"
    finally:
        dedupe.delete_product(pid)


def test_substance_colour_defaults_to_empty_string():
    pid = _make_product()
    try:
        assert dedupe.get_product(pid)["substance_colour"] == ""
    finally:
        dedupe.delete_product(pid)


def test_pipeline_warnings_record_and_fetch_recent():
    dedupe.init_pipeline_warnings()
    marker = uuid.uuid4().hex[:8]
    dedupe.record_warning("no_reference_photo", f"test detail {marker}")
    try:
        recent = dedupe.get_recent_warnings(limit=50)
        match = next((w for w in recent if marker in w["detail"]), None)
        assert match is not None
        assert match["kind"] == "no_reference_photo"
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM pipeline_warnings WHERE detail=%s", (f"test detail {marker}",))
            conn.commit()
