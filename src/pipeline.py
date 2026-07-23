"""End-to-end pipeline: scrape -> dedupe -> image -> blueprint -> copy -> Slack.

One scheduled run across the watchlist. Each ad is failure-isolated: one bad
ad or failed stage is skipped cleanly without stopping the run.
"""
import logging
from src import dedupe, scrape, assets, deconstruct, generate_copy, generate_image_prompt, slack_review, compliance
from src.retry import with_retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipeline")


def process_ad(ad, product=None, reference_bytes=None):
    """Run one ad through the full pipeline. Returns processed/skipped/failed."""
    ad_id = ad.get("ad_id")
    if not ad_id:
        return "failed"
    try:
        if not dedupe.is_new(ad_id):
            log.info("Ad %s already seen, skipping", ad_id)
            return "skipped"

        image_bytes = assets.download_image_bytes(ad["image_url"])
        image_path = assets.download_image(ad["image_url"], ad_id)
        blueprint = deconstruct.deconstruct_image(
            image_bytes=image_bytes,
            ad_id=ad_id,
            source_page=ad.get("page_name", ""),
            captured_at=ad.get("start_date", ""),
            destination_url=ad.get("destination_url", ""),
        )
        copy = generate_copy.generate_copy_live(blueprint)
        ok, issues = compliance.check_compliance(copy, ad.get("page_name", ""), ad.get("text", ""))
        if not ok:
            log.warning("Ad %s failed compliance check: %s", ad_id, issues)
            return "failed"
        try:
            draft_image = generate_image_prompt.generate_image(blueprint, ad_id, product=product, reference_bytes=reference_bytes)
        except Exception as e:
            log.warning("Image generation slow/failed for %s, continuing without draft image: %s", ad_id, e)
            draft_image = None

        dedupe.save_artifact(
            ad_id=ad_id,
            page_name=ad.get("page_name", ""),
            image_path=image_path,
            blueprint=blueprint,
            generated_copy=copy,
            draft_image=draft_image,
            metadata={
                "start_date": ad.get("start_date", ""),
                "cta": ad.get("cta", ""),
                "destination_url": ad.get("destination_url", ""),
                "media_type": ad.get("media_type", ""),
            },
        )

        dedupe.mark_seen(ad_id, ad.get("page_name", ""))

        try:
            slack_review.post_review(ad, blueprint, copy, image_ref=draft_image or image_path)
            log.info("Ad %s processed and posted to Slack", ad_id)
        except Exception as e:
            log.warning("Ad %s saved but Slack post failed: %s", ad_id, e)

        return "processed"
    except Exception as e:
        log.error("Ad %s failed: %s", ad_id, e)
        return "failed"


def run_once(max_per_competitor=5, competitor_id=None, should_stop=None, product_id=None):
    """One scheduled run across the watchlist, or a single competitor if
    competitor_id is given. should_stop is an optional zero-arg callable
    checked between ads/competitors to cooperatively halt the run early."""
    from src.config_check import validate_config
    validate_config()
    dedupe.init_db()
    dedupe.init_decisions()
    dedupe.init_artifacts()
    dedupe.init_competitors()
    dedupe.init_products()
    product = dedupe.get_product(product_id) if product_id else None
    reference_bytes = None
    if product and product.get("image_key"):
        try:
            from google.cloud import storage as _storage
            import os as _os
            _blob = _storage.Client().bucket(_os.getenv("ASSET_BUCKET", "besque-ad-intel-assets")).blob(product["image_key"])
            if _blob.exists():
                reference_bytes = _blob.download_as_bytes()
        except Exception as _e:
            print(f"Reference photo fetch failed (non-fatal): {_e}")
    should_stop = should_stop or (lambda: False)

    competitors = dedupe.get_competitors()
    if competitor_id is not None:
        competitors = [c for c in competitors if c.get("id") == competitor_id]
    summary = {"processed": 0, "skipped": 0, "failed": 0}

    for competitor in competitors:
        if should_stop():
            log.info("Stop requested, halting run.")
            break
        name = competitor.get("name", "?")
        try:
            ads = with_retry(lambda: scrape.scrape_ads(name, max_results=max_per_competitor, page_id=competitor.get("page_id")),
                             attempts=2, delay=2)
        except Exception as e:
            log.error("Scrape failed for %s: %s (clean skip)", name, e)
            continue
        for ad in ads:
            if should_stop():
                log.info("Stop requested, halting run.")
                break
            summary[process_ad(ad, product=product, reference_bytes=reference_bytes)] += 1

    log.info("Run complete: %s", summary)
    return summary


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    run_once()
