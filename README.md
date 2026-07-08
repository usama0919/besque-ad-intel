# Besque - Competitor Ad Intelligence Pipeline

An end-to-end pipeline that detects new competitor ads in the Meta Ad Library,
deconstructs each into a structured creative blueprint, generates a Besque-adapted
draft (copy + image prompt + draft image), and delivers it to a Slack review queue.

Built as a proof of concept: the focus is reliable end-to-end operation, not
launch-ready creative.

## Architecture

The pipeline runs on a daily schedule and moves each new ad through five stages:

    Apify scrape -> dedupe (Postgres) -> asset capture (GCS)
      -> Claude vision (blueprint JSON) -> copy + image-prompt + draft image
      -> Slack review queue -> decision persisted

Each stage is independent and failure-isolated: a bad ad or a failed scrape is
skipped cleanly without corrupting state, and only genuinely new ads incur any
API cost.

## Components (src/)

- config_loader.py  - loads the config-driven competitor watchlist and settings
- dedupe.py         - Postgres dedupe store (UNIQUE ad_id); prevents duplicate alerts
- validator.py      - validates blueprints against the JSON schema
- deconstruct.py    - Claude vision -> structured creative blueprint
- generate_copy.py  - blueprint -> Besque-adapted headline/body/CTA
- generate_image_prompt.py - blueprint visual -> image-generation prompt
- slack_review.py   - formats the per-ad Slack review message (approve/reject)
- retry.py          - retry-with-backoff and clean-skip helpers
- pipeline.py       - orchestrates one scheduled run across the watchlist

Schema: schema/blueprint.schema.json. Config: config/watchlist.yaml.

## Setup

1. Create and activate a virtual environment, then install dependencies:

       python -m venv venv
       venv\Scripts\Activate.ps1
       pip install -r requirements.txt

2. Create a local Postgres database named "besque".
3. Copy .env.example to .env and fill in real credentials.

## Configuration - adding/removing competitors

Edit config/watchlist.yaml:

    competitors:
      - name: "Competitor Name"
        page_id: "META_PAGE_ID"

No code changes needed; the loader reads this file each run.

## Running

    python -m src.pipeline        # one scheduled run
    python -m pytest tests/ -v    # run the test suite

## Testing

The suite covers dedupe, schema validation, blueprint parsing, copy/image-prompt
generation, Slack formatting, retry/recovery, and the orchestrator (including
failed-scrape clean-skip and second-run dedupe). External API calls are mocked,
so tests run with no credentials and no spend.

## Known failure modes

- Failed scrape: logged and skipped for that competitor; run continues, no corrupted state.
- Malformed blueprint from the model: rejected by validator.py; the ad is marked failed.
- Ad missing an ID: isolated as a failed item; does not crash the run.
- Duplicate ads: filtered by the Postgres dedupe store before any API spend.

## Estimated per-ad run costs (indicative)

- Apify (Meta Ad Library scrape): ~$0.05-0.10 per ad
- Claude vision + copy: ~$0.02-0.05 per ad
- Image generation (single pass): ~$0.04-0.08 per ad (model-dependent)
- Total: ~$0.10-0.20 per ad processed

Dedupe runs before any paid call, so cost is incurred only on genuinely new ads.

## Out of scope (per brief)

Image quality tuning, brand-style anchoring, video/carousel ads, auto-publishing,
and performance-feedback loops.
