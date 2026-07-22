# Besque Ad Intelligence — Project Status

_Last updated: end of current work session. Written for picking work back up after a break._

## Done

**Nano banana image generation (Vertex AI)**
`src/generate_image_prompt.py::generate_image()` calls Vertex AI, not the Gemini Developer API:
```python
client = genai.Client(vertexai=True, project="besque-martech", location="global")
response = client.models.generate_content(model="gemini-3.1-flash-image", contents=prompt)
```
- `location` must be `"global"` — `"us-central1"` 404s for this model.
- Model ID on Vertex is `gemini-3.1-flash-image` (no `-preview` suffix; that suffix is Gemini Developer API only).
- Auth is ADC (`gcloud auth application-default login`, quota project `besque-martech`) — no API key, since the org blocks API keys.
- Confirmed working end-to-end against real scraped ads (OSEA, CeraVe) with real inline PNG bytes written to `assets/<ad_id>_draft.png`.
- Default compute service account (`181780124756-compute@developer.gserviceaccount.com`) granted `roles/aiplatform.user` on `besque-martech`, for when Cloud Run deploy succeeds.

**Section 1 — Competitor management (DB-backed)**
- New `competitors` table in Postgres (`id`, `name`, `page_id`, `created_at`) via `src/dedupe.py` (`init_competitors`, `add_competitor`, `get_competitors`, `update_competitor`, `delete_competitor`).
- `dashboard.py`: `GET/POST /api/competitors`, `PUT/DELETE /api/competitors/{id}`. Add is a pure INSERT — never overwrites existing rows (this replaced the old `/api/add-competitor`, which used to overwrite `config/watchlist.yaml` entirely).
- Dashboard UI: Competitors panel in the sidebar — add box, per-row Edit (inline rename) + Remove buttons.
- `src/pipeline.py::run_once()` now reads competitors from `dedupe.get_competitors()`, not `config_loader`/`watchlist.yaml`.
- Migrated the original 8 competitors into the table: OSEA, CeraVe, The Ordinary, Paula's Choice, GoPure Skin Care, JSHealth Vitamins, L'OCCITANE, Grüns.

**Section 2 — Select-to-run + Stop + search**
- Clicking a competitor's name (not Edit/Remove) selects it — highlighted row, shown as "Selected: X" next to Run Pipeline.
- Run Pipeline is disabled until a competitor is selected; runs **only** that competitor via `pipeline.run_once(competitor_id=...)`.
- Stop button: `POST /api/run/stop` sets a cooperative `stop_requested` flag, checked between competitors/ads in `run_once()`. Not a hard kill — an in-flight ad finishes processing before the run actually halts.
- Search box filters the already-fetched `/api/artifacts` data client-side (competitor, headline, copy, blueprint angle) — no new scrape triggered.

**Other fixes made along the way**
- Pipeline bug: Slack posting was happening *before* `save_artifact`/`mark_seen`, so a Slack auth failure caused the whole ad to be discarded. Moved Slack post to after save, wrapped in its own try/except (logs a warning, doesn't block the save).
- Junk test artifacts (4 rows: `page_name` "Brand"/"TestBrand", single-char headline/copy) deleted from production `artifacts` table (and matching `seen_ads` rows).
- `requirements.txt` gaps found and fixed (all with verified real PyPI versions): `fastapi`, `uvicorn`, `google-genai`, `jinja2`, `MarkupSafe` were missing.
- `Dockerfile` CMD changed from one-shot `python -m src.pipeline` to `uvicorn dashboard:app` on `$PORT`, so it can actually serve as a Cloud Run web service.
- `run_all.py` reads `PORT` env var instead of hardcoding 8080.
- Cloud infra provisioned in `besque-martech`: Cloud SQL Postgres instance `besque-db` (europe-west2, `db-f1-micro`), `besque` database, Secret Manager secrets (`anthropic-api-key`, `apify-token`, `gemini-api-key`, `database-url`, `besque-db-password`).

## Not Done

- **Section 3**: edit draft copy + regenerate image with an instruction box — not started.
- **Cloud Run deploy still failing at the Docker build step.** Got past two IAM gaps (`storage.objectViewer`, `logging.logWriter` on the default compute SA) but the actual build failure text was never captured (Cloud Logging returned empty, Docker Desktop wasn't running locally to reproduce) — we pivoted to debugging nano banana locally instead. Needs a fresh `gcloud run deploy --source .` attempt and this time getting the real build log.
- **Rotate exposed keys.** `ANTHROPIC_API_KEY`, `APIFY_TOKEN`, `GEMINI_API_KEY`, and the Cloud SQL `postgres` user password all appeared in plaintext in this chat conversation at some point (pasted directly, or shown in a `.env` diff). All four should be rotated and the corresponding Secret Manager secrets + `.env` updated before this goes anywhere near production.
- **Repo move to `hblmartech` org** — not started.
- **Atria-style trend/feedback features** — not started.
- **Feed Besque Drive product images into nano banana** (as reference images, not just text prompts) — not started.

## Known Issues

- **Pipeline is slow**: ~10–30s per ad (scrape + Claude vision + copy + Vertex image generation), so a run across several competitors takes minutes. No batching/parallelism yet.
- **"Add & Scan" removed**: the old top-bar competitor input + auto-scan-on-add button is gone, replaced by the Section 1/2 flow (add via panel, then select + Run Pipeline explicitly). Anything expecting the old single-button add-and-scan behavior needs to use the new two-step flow.
- `config_loader.py` / `config/watchlist.yaml` are now dead code for the pipeline (still covered by their own direct unit tests) — not deleted.
- `config_check.py` still requires `REPLICATE_API_TOKEN` as a startup-required env var even though image generation no longer uses Replicate — harmless while the placeholder value still sits in `.env`, but should eventually be swapped for a Vertex/ADC check.
- Two processes were seen listening on port 8080 during Section 2 testing (PIDs 16544 and 7440) — unclear which is "the" dev server; needs a clean restart to pick up latest code.

## Restart Locally

```powershell
# Stop whatever's running on 8080
taskkill /F /IM python.exe

# From the repo root, with the venv's Python:
cd "C:\Users\Besque Device (AI)\besque-ad-intel"
.\venv\Scripts\python.exe -m uvicorn dashboard:app --host 127.0.0.1 --port 8080
```

Then open `http://127.0.0.1:8080/`.
