"""Downloads an ad image to local storage for verifiable, auditable analysis."""
import os
import httpx
from pathlib import Path

ASSET_DIR = Path(os.getenv("ASSET_DIR", "assets"))


def download_image(image_url, ad_id):
    """Download the ad image and save it as assets/<ad_id>.jpg. Returns the path."""
    ASSET_DIR.mkdir(exist_ok=True)
    dest = ASSET_DIR / f"{ad_id}.jpg"
    with httpx.stream("GET", image_url, timeout=30, follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return str(dest)
