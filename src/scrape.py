"""Live Apify Meta Ad Library scrape. Wired at kickoff.

Calls the Apify actor, filters to static image ads, and maps each result
to the ad dict shape the pipeline expects (ad_id, page_name, text, link, etc.).
"""
import os
import re
from apify_client import ApifyClient

APIFY_ACTOR = os.getenv("APIFY_ACTOR_ID", "automly/facebook-ad-library-scraper")


def _extract_ad_id(snapshot_url):
    """Pull the numeric ad id out of the ad_snapshot_url (?id=NNNN)."""
    if not snapshot_url:
        return None
    m = re.search(r"[?&]id=(\d+)", snapshot_url)
    return m.group(1) if m else None


def _map_ad(raw):
    """Map one raw Apify result to the pipeline's ad dict."""
    bodies = raw.get("ad_creative_bodies") or []
    # skip template placeholders like {{product.brand}}
    text = next((b for b in bodies if "{{" not in b), bodies[0] if bodies else "")
    return {
        "ad_id": _extract_ad_id(raw.get("ad_snapshot_url")),
        "page_name": raw.get("page_name", ""),
        "text": text,
        "media_type": raw.get("media_type", ""),
        "start_date": raw.get("ad_delivery_start_time", ""),
        "cta": raw.get("cta_type", ""),
        "destination_url": raw.get("link_url", ""),
        "snapshot_url": raw.get("ad_snapshot_url", ""),
    }


def scrape_ads(search_term, max_results=50, image_only=True):
    """Run the Apify actor for one search term. Returns a list of mapped ad dicts.
    Filters to static image ads when image_only is True."""
    token = os.getenv("APIFY_TOKEN")
    if not token:
        raise ValueError("APIFY_TOKEN must be set")

    client = ApifyClient(token)
    run_input = {"searchTerms": [search_term], "maxResults": max_results}
    run = client.actor(APIFY_ACTOR).call(run_input=run_input)

    ads = []
    for raw in client.dataset(run.default_dataset_id).iterate_items():
        if image_only and raw.get("media_type") != "IMAGE":
            continue
        mapped = _map_ad(raw)
        if mapped["ad_id"]:  # skip anything we can't dedupe
            ads.append(mapped)
    return ads
