"""Pipeline orchestrator: chains the stages for one scheduled run.

Live steps (scrape, storage, real API calls, Slack post) are stubbed here
and get wired in at kickoff once credentials/accounts are in place. The
control flow, dedupe, and error isolation are real and testable now.
"""
import logging
from src import dedupe, config_loader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipeline")


def fetch_ads_stub(competitor):
    """Placeholder for the Apify scrape. Returns a list of ad dicts.
    Replaced with the live Apify call at kickoff."""
    return []


def process_ad(ad):
    """Process a single ad through dedupe -> (deconstruct -> generate -> review).
    Returns 'skipped', 'processed', or 'failed'. Never raises — failures are
    isolated so one bad ad can't corrupt the whole run."""
    ad_id = ad.get("ad_id")
    if not ad_id:
        log.warning("Ad missing ad_id, skipping")
        return "failed"
    try:
        if not dedupe.is_new(ad_id):
            log.info("Ad %s already seen, skipping", ad_id)
            return "skipped"
        # Live stages wired in at kickoff:
        #   asset = store_asset(ad)
        #   blueprint = deconstruct(asset)
        #   copy = generate_copy(blueprint)
        #   image = generate_image(blueprint)
        #   post_to_slack(...)
        dedupe.mark_seen(ad_id, ad.get("page_name", ""))
        log.info("Ad %s processed", ad_id)
        return "processed"
    except Exception as e:
        log.error("Ad %s failed: %s", ad_id, e)
        return "failed"


def run_once(fetch_fn=fetch_ads_stub):
    """One scheduled run across the whole watchlist.
    Returns a summary dict. Designed to be idempotent and crash-safe."""
    dedupe.init_db()
    competitors = config_loader.get_competitors()
    summary = {"processed": 0, "skipped": 0, "failed": 0}

    for competitor in competitors:
        name = competitor.get("name", "?")
        try:
            ads = fetch_fn(competitor)
        except Exception as e:
            log.error("Scrape failed for %s: %s (clean skip)", name, e)
            continue  # failed scrape -> skip this competitor, no corrupted state
        for ad in ads:
            result = process_ad(ad)
            summary[result] += 1

    log.info("Run complete: %s", summary)
    return summary


if __name__ == "__main__":
    run_once()
