# Besque - Competitor Ad Intelligence Pipeline

End-to-end pipeline that detects new competitor image ads in the Meta Ad Library,
deconstructs each into a structured creative blueprint, generates a Besque-adapted
draft (copy + image prompt + draft image), runs a compliance check, and delivers it
to a Slack review queue with working approve/reject capture. All artifacts persisted
with timestamps.

Proof of concept: reliable end-to-end operation, not launch-ready creative.

## Architecture

Daily run, each new ad through the pipeline:

    Apify scrape -> dedupe (Postgres) -> download + verify image
      -> Claude vision (blueprint JSON) -> Besque copy + image prompt + Flux draft image
      -> compliance check -> Slack review card (Approve/Reject)
      -> all artifacts + decision persisted (Postgres, timestamped)

Each stage is failure-isolated: a bad ad or failed scrape is skipped cleanly with no
corrupted state. Dedupe runs before any paid call, so cost is incurred only on new ads.

## Components (src/)

- config_loader.py         - config-driven competitor watchlist
- config_check.py          - fail-fast validation of required env vars
- scrape.py                - Apify Meta Ad Library scrape, filtered to image ads
- dedupe.py                - Postgres: dedupe, decisions, artifact persistence
- assets.py                - pluggable storage (local active; GCS drop-in)
- deconstruct.py           - Claude vision -> blueprint (byte-verified media type)
- validator.py             - validates blueprints against the JSON schema
- generate_copy.py         - blueprint -> Besque headline/body/CTA
- generate_image_prompt.py - image prompt + single-pass Flux draft image
- compliance.py            - checks output for competitor names / verbatim copy
- slack_review.py          - Slack review card with Approve/Reject buttons
- slack_listener.py        - Socket Mode listener; persists button decisions
- retry.py                 - retry-with-backoff + clean-skip helpers
- pipeline.py              - orchestrates one run
- scheduler.py             - daily-cadence runner

Schema: schema/blueprint.schema.json. Config: config/watchlist.yaml.

## Setup

    python -m venv venv
    venv\Scripts\Activate.ps1
    pip install -r requirements.txt

Create local Postgres database "besque". Copy .env.example to .env and fill in
Apify, Anthropic, Replicate, and Slack tokens.

## Configuration - adding/removing competitors

Edit config/watchlist.yaml:

    competitors:
      - name: "Competitor Name"
        page_id: "Competitor Name"

No code changes needed. Note: keyword search returns any page mentioning the term;
for brand-only results, scrape by the competitor's specific Ad Library page - the
recommended production refinement.

## Storage

Assets are stored via a pluggable backend (src/assets.py). LocalStorage is active
for the PoC. GCS is a drop-in (STORAGE_BACKEND=gcs) - the brief's GCS project was
not provisioned for the PoC, so local storage is used with the full metadata record.

## Running

    python -m src.pipeline           # one run
    python -m src.scheduler          # daily-cadence loop
    python -m src.slack_listener     # button-decision listener (run alongside)
    python -m pytest tests/ -q       # test suite (mocked, no spend)

Production: prefer the OS scheduler (cron / Task Scheduler / GCP Cloud Scheduler)
calling `python -m src.pipeline` once daily.

## Testing

46 tests, mocked external APIs (no credentials, no spend). Covers dedupe, schema
validation, blueprint/copy parsing, image-prompt generation, compliance, Slack
formatting, retry/recovery, artifact + decision persistence, config validation,
storage backends, and the orchestrator - including the 3-consecutive-run
zero-duplicate acceptance criterion.

## Known failure modes

- Failed scrape: retried, then skipped for that competitor; run continues, no corrupted state.
- Malformed blueprint: rejected by validator; ad marked failed, not propagated.
- Non-compliant copy (competitor name/verbatim): flagged, not posted.
- Ad missing an ID: isolated as failed; does not crash the run.
- Duplicate ads: filtered before any paid call.
- Unusual image formats (PNG/WebP/GIF): media type detected from bytes.
- Missing credentials: fails fast at startup with a clear message.

## Estimated per-ad run costs

- Apify scrape: ~$0.001 per ad
- Claude vision + copy: ~$0.02-0.04 per ad
- Flux draft image (single pass): ~$0.003 per ad
- Total: ~$0.03-0.05 per new ad. Dedupe means only new ads incur cost.

## Out of scope (per brief)

Image quality iteration, brand-style anchoring, product-render consistency,
video/carousel ads, auto-publishing, performance-feedback loops.
