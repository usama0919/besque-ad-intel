"""Startup config validation. Fails fast with a clear message if required
environment variables are missing, rather than crashing mid-run.
"""
import os

REQUIRED = {
    "DATABASE_URL": "Postgres connection for dedupe/artifacts",
    "ANTHROPIC_API_KEY": "Claude vision + copy generation",
    "APIFY_TOKEN": "Meta Ad Library scrape",
    "REPLICATE_API_TOKEN": "Flux draft image generation",
    "SLACK_BOT_TOKEN": "Posting review cards to Slack",
    "SLACK_CHANNEL": "Target Slack channel",
}

OPTIONAL = {
    "SLACK_APP_TOKEN": "Socket Mode listener for Approve/Reject buttons",
    "STORAGE_BACKEND": "Storage backend (local default, gcs for production)",
}


def validate_config(required=REQUIRED):
    """Raise a clear error listing any missing required vars."""
    missing = [f"  - {k} ({desc})" for k, desc in required.items() if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            "Missing required environment variables:\n"
            + "\n".join(missing)
            + "\n\nCopy .env.example to .env and fill these in."
        )
    return True
