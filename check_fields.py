from dotenv import load_dotenv
load_dotenv()
import os
from apify_client import ApifyClient

client = ApifyClient(os.getenv("APIFY_TOKEN"))
# reuse the last run's dataset - no new scrape
runs = client.actor("automly/facebook-ad-library-scraper").runs().list(limit=1).items
dataset_id = runs[0].default_dataset_id
items = list(client.dataset(dataset_id).iterate_items())
first = items[0]
print("ALL FIELD NAMES:")
for k in first.keys():
    print(" -", k)
