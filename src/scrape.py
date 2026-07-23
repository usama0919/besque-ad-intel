"""Live Apify Meta Ad Library scrape. Maps results to the pipeline ad dict,
including a direct downloadable image URL for verifiable image-based analysis."""
import os
from apify_client import ApifyClient

APIFY_ACTOR = os.getenv("APIFY_ACTOR_ID", "automly/facebook-ad-library-scraper")


def _map_ad(raw):
    bodies = raw.get("ad_creative_bodies") or []
    text = next((b for b in bodies if "{{" not in b), bodies[0] if bodies else "")
    images = raw.get("images") or []
    return {
        "ad_id": raw.get("ad_archive_id"),
        "page_name": raw.get("page_name", ""),
        "text": text,
        "media_type": raw.get("media_type", ""),
        "image_url": images[0] if images else None,
        "start_date": raw.get("ad_delivery_start_time", ""),
        "cta": raw.get("cta_type", ""),
        "destination_url": raw.get("link_url", ""),
        "snapshot_url": raw.get("ad_snapshot_url", ""),
    }


def _page_matches(page_name, search_term):
    """True if the ad's page matches the competitor searched (case-insensitive)."""
    a = (page_name or "").strip().lower()
    b = (search_term or "").strip().lower()
    return bool(a) and bool(b) and (a in b or b in a)


def scrape_ads(search_term, max_results=50, image_only=True):
    """Run the Apify actor. Returns mapped ad dicts, filtered to image ads
    that have both an ad_id and a downloadable image URL."""
    token = os.getenv("APIFY_TOKEN")
    if not token:
        raise ValueError("APIFY_TOKEN must be set")

    client = ApifyClient(token)
    fetch_cap = int(os.getenv("SCRAPE_FETCH_CAP", "15"))
    run_input = {"searchTerms": [search_term], "maxResults": fetch_cap, "maxAds": fetch_cap}
    run = client.actor(APIFY_ACTOR).call(run_input=run_input)

    ads = []
    for raw in client.dataset(run.default_dataset_id).iterate_items():
        if image_only and raw.get("media_type") != "IMAGE":
            continue
        mapped = _map_ad(raw)
        if mapped["ad_id"] and mapped["image_url"] and _page_matches(mapped.get("page_name", ""), search_term):
            ads.append(mapped)
    return ads
