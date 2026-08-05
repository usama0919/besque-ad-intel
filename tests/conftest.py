import os
import pytest
from dotenv import dotenv_values, load_dotenv

_env_file_values = dotenv_values()
os.environ["DATABASE_URL"] = (
    os.environ.get("TEST_DATABASE_URL")
    or _env_file_values.get("TEST_DATABASE_URL")
    or "postgresql://postgres:postgres@localhost:5433/besque_test"
)
load_dotenv()

_DATABASE_URL = os.environ["DATABASE_URL"]
_FORBIDDEN_MARKERS = ("34.105.137.192", "besque-martech:europe-west2:besque-db", "/cloudsql/")

if any(marker in _DATABASE_URL for marker in _FORBIDDEN_MARKERS):
    pytest.exit(
        f"DATABASE_URL points at the production database ({_DATABASE_URL!r}) - refusing "
        f"to collect tests. Point DATABASE_URL at a local/test Postgres instance.",
        returncode=1,
    )
