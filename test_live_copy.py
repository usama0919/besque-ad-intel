"""One-off manual test: live Claude copy generation from a real blueprint.
Not part of pytest (that stays mock-only, no spend)."""
from dotenv import load_dotenv
load_dotenv()

from src import deconstruct, generate_copy

# Reuse the live vision step to get a real blueprint, then generate copy from it.
blueprint = deconstruct.deconstruct_image(
    image_path="samples/sample_ad.jpg",
    ad_id="SAMPLE_001",
    source_page="TestBrand",
    captured_at="2026-07-09T00:00:00Z",
    destination_url="https://example.com",
)

copy = generate_copy.generate_copy_live(blueprint)

import json
print(json.dumps(copy, indent=2))
print("\nBesque-adapted copy generated and validated successfully.")
