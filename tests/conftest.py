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

# dashboard.py's shared-password gate (added 2026-08-21) 401s/redirects every
# request with no valid auth cookie, including every dashboard.app TestClient call
# across the test suite - none of them know about the gate, and none should have to
# be individually edited to set one. Patched here, once, for the whole suite:
# dashboard.SHARED_ACCESS_PASSWORD is reassigned to a fixed test value (the gate
# reads this as a module global on every request, not a value captured once at
# decoration time, so reassigning it after import still takes effect), and
# TestClient.__init__ is wrapped so every TestClient(dashboard.app) instance -
# constructed dozens of different ways across test_dashboard.py/
# test_generate_endpoints.py - automatically carries the matching cookie, with zero
# changes to any existing test call site. fastapi.testclient.TestClient is a
# re-export of this exact class (confirmed via source), so patching this one class
# covers both import spellings.
_TEST_AUTH_PASSWORD = "test-only-password-not-a-real-secret"


@pytest.fixture(autouse=True)
def _dashboard_auth_gate_bypass(monkeypatch):
    import dashboard
    from starlette.testclient import TestClient as _TestClient

    monkeypatch.setattr(dashboard, "SHARED_ACCESS_PASSWORD", _TEST_AUTH_PASSWORD)

    original_init = _TestClient.__init__

    def _init_with_auth_cookie(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.cookies.set(dashboard.AUTH_COOKIE_NAME, _TEST_AUTH_PASSWORD)

    monkeypatch.setattr(_TestClient, "__init__", _init_with_auth_cookie)
