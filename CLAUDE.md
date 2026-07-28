# besque-ad-intel — working notes for Claude

## Environment
- Windows 11 + **PowerShell**. No bash syntax: no heredocs, no inline `VAR=x cmd`, no
  `&&`/`||` (use `;` or `if ($?) { }`), `2>$null` not `2>/dev/null`.
- Python lives in `./venv` — run tests as `./venv/Scripts/python.exe -m pytest tests/ -q`.
- `assets/` is gitignored; add throwaway root scripts to `.gitignore` too.
- `google-cloud-storage` and `Pillow` must be **installed in `./venv`**, not merely listed in
  `requirements.txt`. Bucket reads are wrapped in bare `except Exception: pass`
  (`backfill_classify.py`) or downgraded to `NotImplementedError` (`assets.py:60`), so a
  missing package looks exactly like missing data.

## Deploy — `ship.ps1` auto-commits
It runs `git add -A src templates dashboard.py job_runner.py requirements.txt`, commits,
and pushes to `main` before deploying — don't leave unrelated work-in-progress in those
paths when shipping. It then deploys Cloud Run service `besque-dashboard` and updates
Job `besque-pipeline` (project `besque-martech`, region `europe-west2`).

## Two ways the pipeline runs
- **Local / dashboard Run button**: `pipeline.run_once(...)` in-process on a background
  thread (honours `should_stop`). Env from `.env`; `STORAGE_BACKEND` defaults to `local`.
- **Cloud Run Job `besque-pipeline`**: entrypoint `job_runner.py`, which takes no
  arguments — it reads `RUN_COMPETITOR_ID`, `RUN_MAX_PER_COMPETITOR`, `RUN_PRODUCT_ID`
  from the environment and runs with `STORAGE_BACKEND=gcs`.
- Works-locally-fails-deployed is usually that local/gcs split (local `assets/` vs bucket).

## Two dedup gates — both bypassed by `FORCE_REPROCESS=1`
1. `pipeline.process_ad` → `dedupe.is_new(ad_id)` against `seen_ads` (`ad_id` is its PK).
2. `dedupe.save_artifact` → `SELECT 1 FROM artifacts WHERE ad_id` and returns early.

Deleting `artifacts` rows does **not** clear `seen_ads`, so the ad stays invisible to
gate 1 until that row is removed too.

`FORCE_REPROCESS=1` makes `save_artifact` **DELETE then re-insert** (`dedupe.py:113`), and
`generate_image` rewrites `assets/<ad_id>_draft.png` at the same key with no version backup —
only `edit_image` versions, via `_next_draft_version`. A forced re-run therefore replaces the
draft with no fallback. Both modules read the flag at **import time** (`dedupe.py:9`,
`pipeline.py:11`), so a value left in the shell applies to every later run in that session:
always `Remove-Item Env:\FORCE_REPROCESS` after testing, or it presents as a
flipping-image bug.

## Scrape wide, process narrow — two independent caps
- `SCRAPE_FETCH_CAP` (default **50**) is the only thing that sets Apify's `maxAds`
  (`scrape.py:62`). The dashboard's ad-count dropdown does not affect it.
- `scrape_ads(max_results=...)` is an explicit per-call override; `None` means use the env
  var. Deliberately **not** wired to `max_per_competitor` — that caps how many *new* ads get
  processed *after* the `seen_ads` gate, this caps the candidate pool fetched *before* it.
- Apify ignores its own `mediaType: "image"` input; `image_only` filters client-side
  (`scrape.py:75`). Yield is low and varies by page — measured 11/50 on one page and 17/60
  across six others (per-page 1/10 to 8/10), so roughly a quarter. Size the cap for that.

## A numeric `page_id` bypasses the name gate
`use_page` is true when `page_id` is set and differs from `name` (`scrape.py:63`), and it
short-circuits `_page_matches` (`scrape.py:78`). A valid-but-wrong ID then scrapes the wrong
brand silently, with nothing checking the name. Probe any new ID before a full run and
confirm the `page_name` and `page_id` carried on the returned ads.

## `product=` must reach both live calls
`process_ad` passes the product dict to `generate_copy_live` (`pipeline.py:39`) **and**
`generate_image` (`pipeline.py:45`). Dropping it from the copy call renders "None supplied."
for the product facts, and Claude then declines the task in prose — `stop_reason='end_turn'`,
which surfaces as `Expecting value: line 1 column 1 (char 0)` out of
`json_response.extract_json`. A refusal disguised as a parse error. Guarded by
`test_process_ad_passes_product_to_copy_and_image`.

## `ASSET_BUCKET` is canonical — resolve only via `assets.asset_bucket_name()`
`ASSET_BUCKET or GCS_BUCKET or "besque-ad-intel-assets"` (`assets.py:17`). The `GCS_BUCKET`
fallback is **load-bearing**: `ship.ps1` still sets the legacy name on the deployed service.
Don't remove it without updating deploy config first.

## `artifacts.ad_id` has no unique constraint
`id SERIAL` is the only key, so duplicate `ad_id` rows are possible. `get_artifact` reads
`ORDER BY id DESC LIMIT 1`. Update or delete a single row by `id`, never by `ad_id`.

## Working conventions
- **Show the diff before writing.** Say which hunks are mine and which are pre-existing
  uncommitted work — this repo usually has some.
- Edit via the Edit tool. Never rewrite a file by computing string offsets and splicing
  (`s[:i] + new + s[j:]`) — especially not a live template.
- No destructive DB or filesystem commands without asking; dry-run and show counts first.
