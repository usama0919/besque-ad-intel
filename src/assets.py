"""Asset storage. Pluggable backend: local now, GCS drop-in for production.

The brief specifies GCS, provisioned at kickoff. Since no GCS project was
provided for the PoC, LocalStorage is active. Swapping to GCS is a single
adapter (GCSStorage below) selected via the STORAGE_BACKEND env var - no
pipeline changes required.
"""
import os
import httpx
from pathlib import Path

ASSET_DIR = Path(os.getenv("ASSET_DIR", "assets"))


class LocalStorage:
    """Stores assets on the local filesystem (active for the PoC)."""

    def __init__(self, base_dir=ASSET_DIR):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True)

    def save_bytes(self, data: bytes, key: str) -> str:
        dest = self.base_dir / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        return str(dest)


class GCSStorage:
    """Drop-in Google Cloud Storage backend (production).

    Wiring at kickoff (when the GCS project is provided):
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(os.getenv("GCS_BUCKET"))
        blob = bucket.blob(key)
        blob.upload_from_string(data)
        return f"gs://{bucket.name}/{key}"

    Selected by STORAGE_BACKEND=gcs; requires GCS_BUCKET and
    GOOGLE_APPLICATION_CREDENTIALS. No pipeline changes needed.
    """

    def __init__(self, bucket=None):
        raise NotImplementedError(
            "GCSStorage is the production adapter; not provisioned for the PoC. "
            "See docstring for the wiring."
        )


def get_storage():
    """Return the active storage backend based on STORAGE_BACKEND env var."""
    backend = os.getenv("STORAGE_BACKEND", "local").lower()
    if backend == "gcs":
        return GCSStorage()
    return LocalStorage()


_storage = None


def _backend():
    global _storage
    if _storage is None:
        _storage = get_storage()
    return _storage


def download_image(image_url, ad_id):
    """Download the ad image and store it via the active backend. Returns the stored path/URI."""
    with httpx.stream("GET", image_url, timeout=30, follow_redirects=True) as r:
        r.raise_for_status()
        data = r.read()
    return _backend().save_bytes(data, f"{ad_id}.jpg")
