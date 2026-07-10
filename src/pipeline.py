"""End-to-end pipeline: scrape -> dedupe -> image -> blueprint -> copy -> Slack.

One scheduled run across the watchlist. Each ad is failure-isolated: one bad
ad or failed stage is skipped cleanly without stopping the run.
"""
import logging
from src import dedupe, config_loader, scrape, assets, deconstruct, generate_copy, generate_image_prompt, slack_review
from src.retry import with_retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipeline")


def process_ad(ad):
    """Run one ad through the full pipeline. Returns processed/skipped/failed."""
    ad_id = ad.get("ad_id")
    if not ad_id:
        return "failed"
    try:
        if not dedupe.is_new(ad_id):
            log.info("Ad %s already seen, skipping", ad_id)
            return "skipped"

        image_path = assets.download_image(ad["image_url"], ad_id)
        blueprint = deconstruct.deconstruct_image(
            image_path=image_path,
            ad_id=ad_id,
            source_page=ad.get("page_name", ""),
            captured_at=ad.get("start_date", ""),
            destination_url=ad.get("destination_url", ""),
        )
        copy = generate_copy.generate_copy_live(blueprint)
        draft_image = generate_image_prompt.generate_image(blueprint, ad_id)
        slack_review.post_review(ad, blueprint, copy, image_ref=draft_image or image_path)

        dedupe.mark_seen(ad_id, ad.get("page_name", ""))
        log.info("Ad %s processed and posted to Slack", ad_id)
        return "processed"
    except Exception as e:
        log.error("Ad %s failed: %s", ad_id, e)
        return "failed"


def run_once(max_per_competitor=5):
    """One scheduled run across the watchlist."""
    dedupe.init_db()
    dedupe.init_decisions()
    competitors = config_loader.get_competitors()
    summary = {"processed": 0, "skipped": 0, "failed": 0}

    for competitor in competitors:
        name = competitor.get("name", "?")
        try:
            ads = with_retry(lambda: scrape.scrape_ads(name, max_results=max_per_competitor),
                             attempts=2, delay=2)
        except Exception as e:
            log.error("Scrape failed for %s: %s (clean skip)", name, e)
            continue
        for ad in ads:
            summary[process_ad(ad)] += 1

    log.info("Run complete: %s", summary)
    return summary


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    run_once()
