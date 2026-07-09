"""One-off manual test: posts a review package to Slack (no AI cost)."""
from dotenv import load_dotenv
load_dotenv()

from src import slack_review

ad = {
    "ad_id": "SAMPLE_001",
    "page_name": "CeraVe",
    "destination_url": "https://facebook.com/ads/library",
}
blueprint = {
    "format": "product_hero",
    "angle": "dermatologist-developed cleanser for oily skin",
}
copy = {
    "headline": "Mature Skin - Your cleanser should work as thoughtfully as you do.",
    "primary_text": "Besque's dermatologist-reviewed facial cleanser is crafted for women 40+, "
                    "with natural ingredients that respect your skin's changing needs.",
    "cta": "Discover Your Cleanser",
}

resp = slack_review.post_review(ad, blueprint, copy, image_ref="(draft image will appear here)")
print("Posted to Slack. Message timestamp:", resp["ts"])
