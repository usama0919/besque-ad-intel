# besque-ad-intel — working notes for Claude

## Environment
- Windows 11 + **PowerShell**. No bash syntax: no heredocs, no inline `VAR=x cmd`, no
  `&&`/`||` (use `;` or `if ($?) { }`), `2>$null` not `2>/dev/null`.
- Python lives in `./venv` — run tests as `./venv/Scripts/python.exe -m pytest tests/ -q`.
- `assets/` is gitignored; add throwaway root scripts to `.gitignore` too.

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

## `artifacts.ad_id` has no unique constraint
`id SERIAL` is the only key, so duplicate `ad_id` rows are possible. `get_artifact` reads
`ORDER BY id DESC LIMIT 1`. Update or delete a single row by `id`, never by `ad_id`.

## Working conventions
- **Show the diff before writing.** Say which hunks are mine and which are pre-existing
  uncommitted work — this repo usually has some.
- Edit via the Edit tool. Never rewrite a file by computing string offsets and splicing
  (`s[:i] + new + s[j:]`) — especially not a live template.
- No destructive DB or filesystem commands without asking; dry-run and show counts first.
