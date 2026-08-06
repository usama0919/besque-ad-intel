"""Tests for import_reviews.py's pure filter_and_map_rows (Chunk 9, C1, 2026-08-06) - no
DB, no file I/O, no network. The product-scope resolution (build_shopify_id_to_product_id_map)
and the actual DB writes are exercised separately, against the real DB, in
tests/test_dedupe_products.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import import_reviews  # noqa: E402


MAPPING = {"MBO1": 1, "MBO2": 1, "SHOWER": 2}


def _row(**overrides):
    row = {
        "id": "R1", "status": "Active", "rating": "5", "email": "x@example.com",
        "nickname": "Jane D.", "full_name": "Jane Doe", "review": "A perfectly ordinary review with plenty of characters.",
        "date": "2026-01-01T00:00:00.000Z", "productId": "MBO1", "handle": "magic-body-oil",
        "variant": "1 Bottle", "verified_purchase": "true", "incentivized": "",
    }
    row.update(overrides)
    return row


def test_out_of_scope_product_is_dropped():
    """SHOWER is a DIFFERENT tracked product (product_id=2), not an unknown id - a row
    for it must be attributed to product 2, never silently dropped OR miscounted as
    product 1's. This is the product-agnostic behaviour itself: multiple products
    resolved in one pass, each by its own mapping entry."""
    out_rows, counts = import_reviews.filter_and_map_rows([_row(productId="SHOWER")], MAPPING)
    assert len(out_rows) == 1
    assert out_rows[0]["product_id"] == 2
    assert counts["after_product_scope"] == 1


def test_unknown_product_id_is_dropped():
    out_rows, counts = import_reviews.filter_and_map_rows([_row(productId="NOT_TRACKED")], MAPPING)
    assert out_rows == []
    assert counts["after_product_scope"] == 0


def test_incentivized_dropped():
    out_rows, counts = import_reviews.filter_and_map_rows([_row(incentivized="true")], MAPPING)
    assert out_rows == []
    assert counts["after_incentivized"] == 0


def test_unverified_dropped():
    out_rows, counts = import_reviews.filter_and_map_rows([_row(verified_purchase="")], MAPPING)
    assert out_rows == []
    assert counts["after_verified"] == 0


def test_non_active_status_dropped():
    for status in ("Pending", "Rejected"):
        out_rows, counts = import_reviews.filter_and_map_rows([_row(status=status)], MAPPING)
        assert out_rows == []
        assert counts["after_active"] == 0


def test_low_rating_dropped():
    out_rows, counts = import_reviews.filter_and_map_rows([_row(rating="3")], MAPPING)
    assert out_rows == []
    assert counts["after_rating"] == 0


def test_trivially_short_text_dropped():
    out_rows, counts = import_reviews.filter_and_map_rows([_row(review="Love it")], MAPPING)
    assert out_rows == []
    assert counts["after_text_length"] == 0


def test_empty_text_dropped():
    out_rows, counts = import_reviews.filter_and_map_rows([_row(review="")], MAPPING)
    assert out_rows == []


def test_qualifying_row_survives_and_maps_fields_correctly():
    out_rows, counts = import_reviews.filter_and_map_rows([_row()], MAPPING)
    assert len(out_rows) == 1
    row = out_rows[0]
    assert row["review_id"] == "R1"
    assert row["product_id"] == 1  # our internal id, resolved via the mapping
    assert row["shopify_product_id"] == "MBO1"  # raw Shopify id kept as-is, never cast
    assert isinstance(row["shopify_product_id"], str)
    assert row["nickname"] == "Jane D."
    assert row["rating"] == 5
    assert row["medical_flag"] is None
    # NEVER full_name, NEVER email - not even present as keys to store accidentally
    assert "full_name" not in row
    assert "email" not in row


def test_medical_flag_is_stored_not_dropped():
    """A medically-flagged review is NOT excluded from import - it's stored WITH the
    flag set, still counted in final_stored, separately broken out in the counts."""
    out_rows, counts = import_reviews.filter_and_map_rows(
        [_row(review="I use it post hip replacement surgery and it helps so much.")], MAPPING,
    )
    assert len(out_rows) == 1
    assert out_rows[0]["medical_flag"] == "surgery"
    assert counts["final_stored"] == 1
    assert counts["medical_flagged_within_stored"] == 1
    assert counts["usable_excluding_medical"] == 0


def test_productid_never_cast_to_number():
    """The exact corruption class named in the task: productId read as TEXT throughout,
    never int()/float(), so a large Shopify id can never render as 1.6E+13."""
    out_rows, _ = import_reviews.filter_and_map_rows([_row(productId="MBO1")], MAPPING)
    assert out_rows[0]["shopify_product_id"] == "MBO1"
    assert "E+" not in out_rows[0]["shopify_product_id"]


def test_funnel_order_matches_report_exactly():
    """One row that fails EVERY filter - counts must decrement in the documented order
    (product scope -> incentivized -> verified -> active -> rating -> text length), not
    just produce a correct final number by accident."""
    rows = [
        _row(id="A", productId="NOT_TRACKED"),                  # fails product scope
        _row(id="B", incentivized="true"),                     # fails incentivized
        _row(id="C", verified_purchase=""),                    # fails verified
        _row(id="D", status="Pending"),                         # fails active
        _row(id="E", rating="2"),                                # fails rating
        _row(id="F", review="hi"),                               # fails text length
        _row(id="G"),                                             # survives everything
    ]
    out_rows, counts = import_reviews.filter_and_map_rows(rows, MAPPING)
    assert counts["total"] == 7
    assert counts["after_product_scope"] == 6   # dropped SHOWER
    assert counts["after_incentivized"] == 5    # dropped incentivized
    assert counts["after_verified"] == 4        # dropped unverified
    assert counts["after_active"] == 3          # dropped Pending
    assert counts["after_rating"] == 2          # dropped rating=2
    assert counts["after_text_length"] == 1     # dropped "hi"
    assert [r["review_id"] for r in out_rows] == ["G"]
