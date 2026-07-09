"""One-off manual test: live Apify scrape, mapped and filtered to image ads."""
from dotenv import load_dotenv
load_dotenv()

from src import scrape

ads = scrape.scrape_ads("CeraVe", max_results=50, image_only=True)
print(f"Image ads returned: {len(ads)}\n")
for ad in ads[:3]:
    print("ad_id:", ad["ad_id"])
    print("page:", ad["page_name"])
    print("text:", ad["text"][:80])
    print("media:", ad["media_type"])
    print("link:", ad["destination_url"][:60])
    print("-" * 40)
