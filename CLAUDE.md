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

## Competitor `category` axis mirrors products — `category=""` is NOT a filter
`competitors.category` (TEXT DEFAULT '') was added by explicit `ALTER TABLE ... ADD
COLUMN IF NOT EXISTS` — like `products.category`, `CREATE TABLE IF NOT EXISTS` in
`init_competitors()` is a no-op against an already-existing table, so a new column
always needs its own migration.

`run_once(..., category=None)` (`pipeline.py:146`) selects every competitor tagged with
`category`, unless `competitor_id` is also given (that wins). The filter is
`elif category:` (`pipeline.py:176`) — a **truthy** check, not `is not None` — so
`category=""` behaves exactly like `category=None` (no filter, every competitor runs).
An `is not None` check would instead match every *untagged* competitor, the opposite of
what an empty dropdown selection should mean.

## Product `image_keys` (up to 4) — `image_key` is FROZEN
`products.image_keys` (JSONB, default `'[]'`) holds a product's fixed reference-photo
set, capped at `dedupe.MAX_PRODUCT_IMAGES` (4, `dedupe.py:289`). The legacy single
`image_key` column is **frozen** — new uploads never write it, only `image_keys`. Always
resolve the effective set through `pipeline.effective_image_keys(product)`
(`pipeline.py:18`), which falls back to `[image_key]` for pre-multi-image products;
never read `image_keys`/`image_key` directly elsewhere.

Verified in the dashboard: reference photos reliably carry the label artwork and the
glass/oil colour — but anything the photos disagree on across the set, or don't make
unambiguous (e.g. pump colour, which face is "front"), gets averaged or guessed wrong by
Gemini unless it's also stated explicitly in `visual_description`
(`generate_image_prompt.py:24`). Confirmed cases: photos alone produced a wholly gold
pump (the real one is a black head on a gold collar) and a back-facing label — both
fixed only once `visual_description` said so in words.

## `pipeline_warnings` — the only run-status channel that works for both run paths
`dashboard.py`'s `/api/run/status` (`api_run_status`, `dashboard.py:143`) reports Cloud
Run **execution** counts (`dashboard.py:161`) — it never reads `run_once`'s return dict;
that return value only reaches anything via the dead local-thread path
(`_run_pipeline_bg`, never actually called — `/api/run` always triggers the Cloud Run
Job). So anything `run_once` wants a human to see has to be written to the DB from
inside `pipeline.py` itself: `dedupe.record_warning(kind, detail)` into
`pipeline_warnings` (`dedupe.py:325-353`), read back by `GET /api/warnings`
(`dashboard.py:407`) and rendered as a dismissible banner. This is the **only**
path-agnostic way to surface a run-time problem — don't add a return-dict counter and
assume anyone sees it.

## Compliance guardrails — `compliance_rules.py` (prompt) + `compliance.py` (mechanical)
Six rules (C1-C6), added after a generated draft fabricated a customer testimonial.
`COMPLIANCE_RULES` (`src/compliance_rules.py`) is a single shared constant imported by
both `generate_image_prompt.py` (appended after `BRAND_RULES` rule 7 — 6/7 unmodified)
and `generate_copy.py`. Never duplicate this text in either file.

**C1, C4, C6 are prompt-only for images — no mechanical enforcement.** Only C2
(fabricated testimonials / unapproved numeric claims) has a mechanical backstop, and
it's regex/keyword pattern-matching, not semantic understanding.

Discount percentages ("20% off") are exempt via a **tight adjacency regex**
(`DISCOUNT_PERCENTAGE_PATTERN`, `compliance.py:75`), not a character-distance window — a
window was tried first and wrongly exempted an unrelated fabricated efficacy percentage
sitting a few words away in the same copy.

`process_ad` retries copy generation once on a compliance failure (`MAX_COPY_ATTEMPTS =
2`, `pipeline.py:83`), feeding the specific issues back via `compliance_feedback` (same
blueprint reused). Final failure is recorded via
`dedupe.record_warning("compliance_failed", ...)` (`pipeline.py:101`), not just logged.

## Working conventions
- **Show the diff before writing.** Say which hunks are mine and which are pre-existing
  uncommitted work — this repo usually has some.
- Edit via the Edit tool. Never rewrite a file by computing string offsets and splicing
  (`s[:i] + new + s[j:]`) — especially not a live template.
- No destructive DB or filesystem commands without asking; dry-run and show counts first.

## Known gaps (as of 2026-07-29)
- `brand_voice`, `approved_claims`, `approved_testimonials` are empty at every real call
  site — compliance mechanical checks are correspondingly strict-by-default until real
  approved material is wired through.
- Dashboard sidebar Remove/category-save buttons are broken in the frontend JS; the
  underlying API endpoints work (verified directly) — a `dashboard.html` wiring bug, not
  backend.
- Slack posting fails with `invalid_auth` (`slack_review.post_review`) — non-fatal to the
  pipeline, but review cards aren't reaching Slack.
- 2 ads remain unclassified after the backfill: one schema-validation failure, one
  transient Anthropic `529`. Left untouched, no category invented.
- Only competitors id=42 (Bangn Body) and id=48 (The Ayurveda Experience) are tagged
  `body_oil` — a category-scoped run currently hits just those two.
- Nothing deployed since 27 Jul 2026 — everything above is committed and pushed to
  `main`, but Cloud Run is still running the 27 Jul build.

## Work in progress — Prompt B (messaging angles + Claude prompt-writer pass)

**Done:**
- `angles` table + CRUD (`dedupe.py`: `init_angles`, `get_angles`, `get_angle`, `add_angle`,
  `update_angle`, `delete_angle`) and a sidebar management panel in `dashboard.html`
  (mirrors the products modal shape — angle has too many fields for the competitors-style
  inline-row edit).
- (ad × angle) dedup: `seen_ads`/`artifacts` keyed on `(ad_id, angle_id)` instead of
  `ad_id` alone (`is_new`, `mark_seen`, `save_artifact`, `get_artifact` all take
  `angle_id=None`, defaulting to today's exact pre-angle behaviour). Consequential fixes
  folded in: `update_artifact_copy`/`update_artifact_image_prompt`/`record_decision` also
  take `angle_id` (without it, editing or deciding on one angle-variant of an ad would
  silently affect every other angle-variant sharing that `ad_id`), and
  `get_artifacts_full`'s decision JOIN matches on angle too.
- Draft-image collision fix: `generate_image_prompt._draft_stem(ad_id, angle_slug)` keys
  the output PNG/blob so two angles' drafts for the same ad don't overwrite each other at
  the same `{ad_id}_draft.png` path. Threaded through `generate_image`,
  `_next_draft_version`, `edit_image`.
- `messaging_angle` threaded through `pipeline.run_once(angle_id=...)` →
  `process_ad(..., messaging_angle=...)`. Angle-blindness of `generate_copy_live` is
  deliberate for now — flagged in a comment at `pipeline.py`'s `copy_kwargs` line as the
  likely next mismatch once baked-in headlines are angle-specific.
- `dashboard.py`/`dashboard.html`: `/api/angles` CRUD routes; `/api/decision`,
  `/api/edit_image`, `/api/edit_copy` all accept `angle_id`; cards show an angle badge and
  pass `angle_id` through Approve/Reject/Edit.

**NOT started:**
- `illustrated` as a fourth `production_style` value (schema enum, `deconstruct.py`'s
  classifier prompt, `PRODUCTION_STYLE_GUIDANCE`, `validator.production_styles()`).
- The four run-strip operator controls (Angle/Realism/Text-in-image/Include-product
  dropdowns+toggles) — `run_once`/`process_ad` don't yet accept `realism`,
  `text_in_image`, or `include_product` at all; only `angle_id` exists so far.
- Conditional BRAND_RULES 6/7 (`brand_rules(text_in_image, include_product, ...)`
  replacing the flat `BRAND_RULES` constant) and the matching closing-paragraph fix in
  `build_image_prompt`.
- The Claude prompt-writer pass (`generate_image_prompt_writer.py`) and its wiring into
  `build_image_prompt` via `creative_description`.
- `text_in_image` is already a real column on `artifacts` (added defensively alongside
  `angle_id` in the same migration) but nothing sets it to anything other than `False` yet
  — the dashboard overlay-suppression logic that reads it hasn't been built either.

**Migration not yet run** — `migrate_angles.sql` (gitignored, repo root) has the full
`seen_ads`/`artifacts`/`review_decisions`/`angles` DDL, reviewed but not executed. Until it
runs, the live DB and this code disagree: `seen_ads`'s new `CREATE UNIQUE INDEX` in
`init_db()` fails with `UndefinedColumn` against the current schema, which is currently
failing 14 tests that call `run_once`/`is_new`/`mark_seen` (confirmed via a full pytest
run — not a logic bug, just the expected consequence of the live table predating this
column). `test_dedupe_angles.py` passes standalone since `angles` is a brand-new table
with no drift to hit.
