"""Reference-format flag (Prompt 4, Item 4).

A bundle ad's structure exists to sell a bundle - six products, "5 for $109", a range
layout. Rule 7 correctly collapses it to one Besque bottle, but the composition still
argues for an offer we aren't making. Unlike content_safety.py (a hard block - never
cloned, no judgment call), this is a FLAG, never a filter: filtering shrinks an already
thin ad pool, flagging never does, and every flag becomes ranking signal later. Surfaced
on the card for a human to weigh, exactly like the output critic's findings.

Detected purely from data already in the blueprint (layout_detail.product_count,
creative_format, offer.mechanic) - no vision call, no new blueprint field beyond the flag
column itself."""
import re

BUNDLE_MECHANIC_KEYWORDS = (
    "bundle", "set of", "value pack", "multi-pack", "kit", "buy one get", "bogo",
)

# "5 for $109" - a quantity-for-price mechanic, the clearest single signal that an offer
# is a bundle rather than a single-product promotion.
BUNDLE_QUANTITY_PATTERN = re.compile(r"\b\d+\s+for\s+[$£€]\s?\d", re.IGNORECASE)

# creative_format values whose whole structure argues for more than one product or an
# offer comparison, independent of layout_detail.product_count.
OFFER_STRUCTURED_FORMATS = ("offer_led", "comparison")


def _is_bundle_offer(blueprint):
    offer = (blueprint or {}).get("offer") or {}
    text = " ".join(str(offer.get(k) or "") for k in ("type", "value", "mechanic")).lower()
    if any(kw in text for kw in BUNDLE_MECHANIC_KEYWORDS):
        return True
    return bool(BUNDLE_QUANTITY_PATTERN.search(text))


def format_flag_reason(blueprint):
    """Return a human-readable FLAG reason if this reference's own format can't carry a
    single-product message, else None. NEVER a filter/skip signal - the caller must save
    the artifact and surface this on the card, not act on it.

    Triggers (blueprint data only):
    - layout_detail.product_count > 1
    - creative_format of offer_led or comparison
    - offer.mechanic (or type/value) naming a bundle mechanic ("bundle", "5 for $109", etc.)
    """
    blueprint = blueprint or {}
    layout_detail = blueprint.get("layout_detail") or {}
    product_count = layout_detail.get("product_count")
    has_multi_product = isinstance(product_count, (int, float)) and product_count > 1
    creative_format = blueprint.get("creative_format")
    has_offer_format = creative_format in OFFER_STRUCTURED_FORMATS
    is_bundle = _is_bundle_offer(blueprint)

    if not (has_multi_product or has_offer_format or is_bundle):
        return None

    if has_multi_product and is_bundle:
        return f"reference was a {int(product_count)}-product bundle offer"
    if has_multi_product:
        return f"reference showed {int(product_count)} products in frame"
    if is_bundle:
        return "reference's offer had a bundle mechanic"
    return f"reference's own format was {creative_format}"
