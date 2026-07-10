# Besque - Competitor Ad Intelligence Pipeline

End-to-end pipeline that detects new competitor image ads in the Meta Ad Library,
deconstructs each into a structured creative blueprint, generates a Besque-adapted
draft (copy + image prompt + draft image), and delivers it to a Slack review queue
with working approve/reject capture.

Proof of concept: the focus is reliable end-to-end operation, not launch-ready creative.

## Architecture

Daily run, each new ad through five stages:

    Apify scrape -> dedupe (Postgres) -> download + verify image
      -> Claude vision (blueprint JSON) -> Besque copy + image prompt + draft image (Flux)
      -> Slack review card (Approve/Reject) -> decision persisted (Postgres)

Each stage is failure-isolated: a bad ad or failed scrape is skipped cleanly with
no corrupted state. Dedupe runs before any paid call, so cost is incurred only on
genuinely new ads.

## Components (src/)

- config_loader.py         - config-driven competitor watchlist
- scrape.py                - Apify Meta Ad Library scrape, filtered to image ads
- dedupe.py                - Postgres dedupe store + review-decision persistence
- assets.py                - downloads + stores the real ad image (auditable record)
- deconstruct.py           - Claude vision -> structured blueprint (byte-verified media type)
- validator.py             - validates blueprints against the JSON schema
- generate_copy.py         - blueprint -> Besque headline/body/CTA (compliance-guarded)
- generate_image_prompt.py - blueprint visual -> image prompt + single-pass Flux draft image
- slack_review.py          - Slack review card with Approve/Reject buttons
- slack_listener.py        - Socket Mode listener; persists button decisions
- retry.py                 - retry-with-backoff + clean-skip helpers
- pipeline.py              - orchestrates one scheduled run
- scheduler.py             - daily-cadence runner

Schema: schema/blueprint.schema.json. Config: config/watchlist.yaml.

## Setup

    python -m venv venv
    venv\Scripts\Activate.ps1
    pip install -r requirements.txt

Create a local Postgres database "besque". Copy .env.example to .env and fill in:
Apify token, Anthropic key, Replicate token, Slack bot + app tokens.

## Configuration - adding/removing competitors

Edit config/watchlist.yaml:

    competitors:
      - name: "Competitor Name"
        page_id: "Competitor Name"

No code changes needed. Note: searching by keyword returns any page mentioning the
term; for brand-only results, scrape by the competitor's specific Ad Library page.
This is the recommended production refinement.

## Running

    python -m src.pipeline           # one run
    python -m src.scheduler          # daily-cadence loop
    python -m src.slack_listener     # button-decision listener (run alongside)
    python -m pytest tests/ -v       # test suite (mocked, no spend)

In production, prefer the OS scheduler (cron / Task Scheduler / GCP Cloud Scheduler)
calling `python -m src.pipeline` once daily.

## Testing

35 tests covering dedupe, schema validation, blueprint/copy parsing, image-prompt
generation, Slack formatting, retry/recovery, decision capture, and the orchestrator
(full flow, dedupe on re-run, clean-skip on failed scrape). External APIs are mocked -
tests run with no credentials and no spend.

## Known failure modes

- Failed scrape: retried, then skipped for that competitor; run continues, no corrupted state.
- Malformed blueprint: rejected by validator; ad marked failed, not propagated.
- Ad missing an ID: isolated as failed; does not crash the run.
- Duplicate ads: filtered by dedupe store before any paid call.
- Unusual image formats (PNG/WebP/GIF): media type detected from bytes, not extension.

## Estimated per-ad run costs

- Apify scrape: ~$0.001 per ad (pay-per-result, ~$0.70/1,000)
- Claude vision + copy: ~$0.02-0.04 per ad
- Flux draft image (single pass): ~$0.003 per ad
- Total: ~$0.03-0.05 per new ad processed

Dedupe means only genuinely new ads incur cost.

## Out of scope (per brief)

Image quality iteration, brand-style anchoring, product-render consistency, video/
carousel ads, auto-publishing, performance-feedback loops.
