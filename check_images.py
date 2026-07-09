from dotenv import load_dotenv
load_dotenv()
import os, json
from apify_client import ApifyClient

client = ApifyClient(os.getenv("APIFY_TOKEN"))
runs = client.actor("automly/facebook-ad-library-scraper").runs().list(limit=1).items
items = list(client.dataset(runs[0].default_dataset_id).iterate_items())

# find first ad that is IMAGE type and show its images field
for ad in items:
    if ad.get("media_type") == "IMAGE":
        print("ad_archive_id:", ad.get("ad_archive_id"))
        print("images field:")
        print(json.dumps(ad.get("images"), indent=2)[:800])
        break
