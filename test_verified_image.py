from dotenv import load_dotenv
load_dotenv()
import os
from apify_client import ApifyClient
from src import scrape, assets, deconstruct

# Reuse last run - no new scrape
client = ApifyClient(os.getenv("APIFY_TOKEN"))
runs = client.actor("automly/facebook-ad-library-scraper").runs().list(limit=1).items
items = list(client.dataset(runs[0].default_dataset_id).iterate_items())

# Find first image ad, map it
ad = None
for raw in items:
    if raw.get("media_type") == "IMAGE" and raw.get("images"):
        ad = scrape._map_ad(raw)
        break

print("Ad:", ad["ad_id"], "-", ad["page_name"])

# Download the real image (verifiable record)
path = assets.download_image(ad["image_url"], ad["ad_id"])
print("Image saved to:", path)

# Run the saved image through vision
bp = deconstruct.deconstruct_image(
    image_path=path,
    ad_id=ad["ad_id"],
    source_page=ad["page_name"],
    captured_at=ad["start_date"],
    destination_url=ad["destination_url"],
)
import json
print(json.dumps(bp, indent=2))
