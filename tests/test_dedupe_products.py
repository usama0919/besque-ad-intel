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


# ---- shopify_product_ids (Chunk 9, C1, 2026-08-06): the ONLY mapping the reviews
# importer resolves product scope from - never a hardcoded Python set. ----

def test_set_shopify_product_ids_defaults_to_empty_list():
    pid = _make_product()
    try:
        assert dedupe.get_product(pid)["shopify_product_ids"] == []
    finally:
        dedupe.delete_product(pid)


def test_set_shopify_product_ids_is_a_targeted_update_not_read_modify_write():
    """Setting shopify_product_ids must never touch any other column - same
    read-modify-write hazard update_product/update_competitor already have a documented
    incident for, just proven here for the new column specifically."""
    pid = _make_product(hero_claim="original hero claim", category="body_oil")
    try:
        dedupe.set_shopify_product_ids(pid, ["111", "222"])
        product = dedupe.get_product(pid)
        assert product["shopify_product_ids"] == ["111", "222"]
        assert product["hero_claim"] == "original hero claim"
        assert product["category"] == "body_oil"
    finally:
        dedupe.delete_product(pid)


def test_set_shopify_product_ids_replaces_the_whole_list():
    pid = _make_product()
    try:
        dedupe.set_shopify_product_ids(pid, ["111", "222"])
        dedupe.set_shopify_product_ids(pid, ["333"])
        assert dedupe.get_product(pid)["shopify_product_ids"] == ["333"]
    finally:
        dedupe.delete_product(pid)


def test_set_shopify_product_ids_raises_for_unknown_product():
    import pytest
    with pytest.raises(ValueError):
        dedupe.set_shopify_product_ids(999999999, ["1"])


# ---- product_reviews (Chunk 9, C1, 2026-08-06) ----

def _make_review(product_id, **overrides):
    row = {
        "review_id": f"__test_{uuid.uuid4().hex[:12]}__",
        "product_id": product_id,
        "shopify_product_id": "8094699356313",
        "handle": "magic-body-oil",
        "variant": "1 Bottle",
        "nickname": "Test T.",
        "rating": 5,
        "review_date": "2026-01-01T00:00:00.000Z",
        "review_text": "A perfectly ordinary test review with enough characters.",
        "char_length": 58,
        "medical_flag": None,
    }
    row.update(overrides)
    return row


def test_insert_and_get_reviews_for_product():
    dedupe.init_product_reviews()
    pid = _make_product()
    try:
        row = _make_review(pid)
        dedupe.insert_product_reviews([row])
        reviews = dedupe.get_reviews_for_product(pid)
        assert len(reviews) == 1
        assert reviews[0]["review_id"] == row["review_id"]
        assert reviews[0]["nickname"] == "Test T."
        # nickname stored, full_name/email never even columns on this table
        assert "full_name" not in reviews[0]
        assert "email" not in reviews[0]
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM product_reviews WHERE product_id=%s", (pid,))
            conn.commit()
        dedupe.delete_product(pid)


def test_insert_product_reviews_is_idempotent_via_on_conflict():
    dedupe.init_product_reviews()
    pid = _make_product()
    try:
        row = _make_review(pid)
        dedupe.insert_product_reviews([row])
        dedupe.insert_product_reviews([row])  # same review_id again
        reviews = dedupe.get_reviews_for_product(pid)
        assert len(reviews) == 1
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM product_reviews WHERE product_id=%s", (pid,))
            conn.commit()
        dedupe.delete_product(pid)


def test_get_reviews_for_product_excludes_medical_flag_by_default():
    dedupe.init_product_reviews()
    pid = _make_product()
    try:
        clean = _make_review(pid, medical_flag=None)
        flagged = _make_review(pid, medical_flag="surgery")
        dedupe.insert_product_reviews([clean, flagged])

        usable = dedupe.get_reviews_for_product(pid)
        assert [r["review_id"] for r in usable] == [clean["review_id"]]

        everything = dedupe.get_reviews_for_product(pid, exclude_medical_flag=False)
        assert len(everything) == 2
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM product_reviews WHERE product_id=%s", (pid,))
            conn.commit()
        dedupe.delete_product(pid)


def test_get_existing_review_ids_reflects_stored_rows():
    dedupe.init_product_reviews()
    pid = _make_product()
    try:
        row = _make_review(pid)
        assert row["review_id"] not in dedupe.get_existing_review_ids()
        dedupe.insert_product_reviews([row])
        assert row["review_id"] in dedupe.get_existing_review_ids()
    finally:
        with dedupe.get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM product_reviews WHERE product_id=%s", (pid,))
            conn.commit()
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
