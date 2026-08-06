"""Import Shopify review-app export rows into product_reviews (Chunk 9, C1 - 2026-08-06).

Product-agnostic by design: scoping is resolved ENTIRELY from products.shopify_product_ids
(read fresh from the DB, never a hardcoded Python set of ids) - a raw CSV row whose
productId isn't listed under any product is simply not ours and is skipped. Adding a
second live product later is a config change (populate that product's own
shopify_product_ids via dedupe.set_shopify_product_ids), never an edit to this script.

Filter funnel, in order (matches the approved C1 count of 18,873 usable rows, computed
BEFORE this script existed, over the SAME CSV):
  1. product scope (via products.shopify_product_ids)
  2. drop incentivized
  3. drop unverified
  4. Active status only
  5. rating >= 4
  6. drop empty/trivially short text (< MIN_REVIEW_CHARS)
Medical-flag matches are NOT dropped here - they are STORED with medical_flag set to the
matched src.content_safety.MEDICAL_KEYWORDS term, so they stay in the corpus (visible,
auditable) but are excluded from generation by default via dedupe.get_reviews_for_product's
own exclude_medical_flag=True. This is why this script's own final stored count is larger
than 18,873 - that number was the "safe to use today" count, not "safe to keep at all".

nickname only - full_name and email are never read from the source row into anything
stored, not even in memory beyond the single row being processed.

Read-only against the CSV; writes only to product_reviews via dedupe.insert_product_reviews
(idempotent - existing review_ids are fetched once up front and skipped, see
dedupe.get_existing_review_ids). Never touches products.shopify_product_ids itself - that
mapping is reviewed and set separately (see set_shopify_product_ids.py), on purpose, so a
review import run can never silently redefine which Shopify products are in scope.

Usage: python import_reviews.py [--csv PATH] [--dry-run]
"""
import argparse
import csv
import sys

from dotenv import load_dotenv
load_dotenv()

from src import dedupe, content_safety  # noqa: E402  (load_dotenv must run first)

DEFAULT_CSV = "data/reviews.YZKtfwMrgW.csv"
MIN_REVIEW_CHARS = 10


def _medical_flag(text):
    t = (text or "").lower()
    return next((kw for kw in content_safety.MEDICAL_KEYWORDS if kw in t), None)


def build_shopify_id_to_product_id_map():
    """The ONLY place product scope is resolved - read fresh from products.shopify_product_ids
    every run, never cached in this script as a constant. A shopify productId listed under
    more than one product is a data error in the products table itself, not something this
    importer silently resolves - raises loudly instead of guessing which product wins."""
    mapping = {}
    for product in dedupe.get_products():
        for shopify_id in (product.get("shopify_product_ids") or []):
            if shopify_id in mapping:
                raise ValueError(
                    f"Shopify productId {shopify_id!r} is listed under both product "
                    f"{mapping[shopify_id]} and product {product['id']} - fix products."
                    f"shopify_product_ids before importing."
                )
            mapping[shopify_id] = product["id"]
    return mapping


def filter_and_map_rows(rows, shopify_id_to_product_id):
    """Pure function (no DB writes) - returns the list of dicts ready for
    dedupe.insert_product_reviews, plus a stage-by-stage count breakdown for reporting."""
    counts = {"total": len(rows)}

    scoped = [r for r in rows if r["productId"] in shopify_id_to_product_id]
    counts["after_product_scope"] = len(scoped)

    after_incentivized = [r for r in scoped if r["incentivized"].strip().lower() != "true"]
    counts["after_incentivized"] = len(after_incentivized)

    after_verified = [r for r in after_incentivized if r["verified_purchase"].strip().lower() == "true"]
    counts["after_verified"] = len(after_verified)

    after_active = [r for r in after_verified if r["status"].strip() == "Active"]
    counts["after_active"] = len(after_active)

    def _rating(r):
        try:
            return float(r["rating"])
        except (ValueError, TypeError):
            return 0

    after_rating = [r for r in after_active if _rating(r) >= 4]
    counts["after_rating"] = len(after_rating)

    after_text = [r for r in after_rating if len((r["review"] or "").strip()) >= MIN_REVIEW_CHARS]
    counts["after_text_length"] = len(after_text)
    counts["final_stored"] = len(after_text)

    out_rows = []
    medical_count = 0
    for r in after_text:
        flag = _medical_flag(r["review"])
        if flag:
            medical_count += 1
        out_rows.append({
            "review_id": r["id"],
            "product_id": shopify_id_to_product_id[r["productId"]],
            "shopify_product_id": r["productId"],   # kept as the raw string - never cast
            "handle": r["handle"],
            "variant": r["variant"],
            "nickname": r["nickname"],               # NEVER full_name, NEVER email
            "rating": int(float(r["rating"])) if r["rating"] else None,
            "review_date": r["date"] or None,
            "review_text": r["review"],
            "char_length": len(r["review"]),
            "medical_flag": flag,
        })
    counts["medical_flagged_within_stored"] = medical_count
    counts["usable_excluding_medical"] = counts["final_stored"] - medical_count
    return out_rows, counts


def main():
    parser = argparse.ArgumentParser(description="Import Shopify reviews into product_reviews.")
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--dry-run", action="store_true", help="report counts only, write nothing")
    args = parser.parse_args()

    dedupe.init_products()
    dedupe.init_product_reviews()

    shopify_id_to_product_id = build_shopify_id_to_product_id_map()
    if not shopify_id_to_product_id:
        print("No product has any shopify_product_ids configured - nothing to scope against. "
              "Run set_shopify_product_ids.py first.")
        sys.exit(1)
    print(f"Product scope resolved from products.shopify_product_ids: {shopify_id_to_product_id}")

    # productId must never become a float - read every field as str via csv.DictReader,
    # which is already the default (no int()/float() cast anywhere on productId itself).
    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    out_rows, counts = filter_and_map_rows(rows, shopify_id_to_product_id)

    print()
    for key in ("total", "after_product_scope", "after_incentivized", "after_verified",
                "after_active", "after_rating", "after_text_length"):
        print(f"{key}: {counts[key]}")
    print(f"final_stored (pre-existing-check): {counts['final_stored']}")
    print(f"  of which medical_flag set (stored, excluded from generation by default): "
          f"{counts['medical_flagged_within_stored']}")
    print(f"  usable_excluding_medical: {counts['usable_excluding_medical']}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    existing = dedupe.get_existing_review_ids()
    new_rows = [r for r in out_rows if r["review_id"] not in existing]
    skipped_existing = len(out_rows) - len(new_rows)
    dedupe.insert_product_reviews(new_rows)
    print(f"\nInserted: {len(new_rows)}  (skipped, already imported: {skipped_existing})")


if __name__ == "__main__":
    main()
