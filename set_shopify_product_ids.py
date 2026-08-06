"""One-off: populate products.shopify_product_ids for a given internal product_id.
Targeted single-column UPDATE via dedupe.set_shopify_product_ids - never update_product
(read-modify-write, already wiped verified data once for a different table). Replaces the
whole list; run again with the full set if it ever needs correcting, never call twice to
append.

Usage: python set_shopify_product_ids.py PRODUCT_ID ID1 ID2 ID3 ...
"""
import sys
from dotenv import load_dotenv
load_dotenv()
from src import dedupe  # noqa: E402


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    product_id = int(sys.argv[1])
    shopify_ids = sys.argv[2:]
    product = dedupe.get_product(product_id)
    if product is None:
        print(f"product {product_id} not found")
        sys.exit(1)
    print(f"Product {product_id} ({product['name']!r})")
    print(f"  current shopify_product_ids: {product['shopify_product_ids']}")
    print(f"  about to set to:             {shopify_ids}")
    print(f"  SQL: UPDATE products SET shopify_product_ids=%s WHERE id=%s -- ({shopify_ids!r}, {product_id})")
    dedupe.set_shopify_product_ids(product_id, shopify_ids)
    updated = dedupe.get_product(product_id)
    print(f"  now: {updated['shopify_product_ids']}")


if __name__ == "__main__":
    main()
