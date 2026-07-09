"""One-off manual test: makes a single live Claude vision call on the sample ad.
Not part of the pytest suite (that stays mock-only, no spend)."""
from dotenv import load_dotenv
load_dotenv()

from src import deconstruct

blueprint = deconstruct.deconstruct_image(
    image_path="samples/sample_ad.jpg",
    ad_id="SAMPLE_001",
    source_page="TestBrand",
    captured_at="2026-07-09T00:00:00Z",
    destination_url="https://example.com",
)

import json
print(json.dumps(blueprint, indent=2))
print("\nBlueprint validated and returned successfully.")
