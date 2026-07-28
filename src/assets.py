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

DEFAULT_ASSET_BUCKET = "besque-ad-intel-assets"


def asset_bucket_name() -> str:
    """Resolve the asset bucket name. The one place this decision is made.

    ASSET_BUCKET is canonical. GCS_BUCKET is the legacy name that ship.ps1 still
    sets on the deployed service, so it stays honoured as a fallback - do not
    drop it without updating deploy config first.
    """
    return os.getenv("ASSET_BUCKET") or os.getenv("GCS_BUCKET") or DEFAULT_ASSET_BUCKET


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
        bucket = client.bucket(asset_bucket_name())
        blob = bucket.blob(key)
        blob.upload_from_string(data)
        return f"gs://{bucket.name}/{key}"

    Selected by STORAGE_BACKEND=gcs; requires ASSET_BUCKET (legacy GCS_BUCKET
    still honoured) and GOOGLE_APPLICATION_CREDENTIALS. No pipeline changes needed.
    """

    def __init__(self, bucket=None):
        try:
            from google.cloud import storage
        except ModuleNotFoundError as e:
            raise NotImplementedError(
                "GCS backend not provisioned: google-cloud-storage not installed"
            ) from e
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket or asset_bucket_name())

    def save_bytes(self, data: bytes, key: str) -> str:
        blob = self._bucket.blob(key)
        blob.upload_from_string(data)
        return key


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


def download_image_bytes(image_url):
    """Download an image and return raw bytes (no storage). Raises on HTTP error so a
    CDN error body is never passed to the vision call as if it were an image."""
    import httpx
    with httpx.stream("GET", image_url, timeout=30, follow_redirects=True) as r:
        r.raise_for_status()
        return r.read()


def download_image(image_url, ad_id):
    """Download the ad image and store it via the active backend. Returns the stored path/URI."""
    with httpx.stream("GET", image_url, timeout=30, follow_redirects=True) as r:
        r.raise_for_status()
        data = r.read()
    return _backend().save_bytes(data, f"{ad_id}.jpg")
