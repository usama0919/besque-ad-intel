# besque-ad-intel — working notes for Claude

## Prompt-only guardrails do not bind on the image path — read this before adding
## an eighth instruction
Four rounds of increasingly explicit `brand_rules()`/writer instructions failed to stop
leaks from reference ads into generated drafts. A single live sweep on 2026-08-04 shipped
all six of these, each one directly forbidden in text at the time:
1. A competitor's "GLOW APPROVED" seal reproduced intact, despite rule 9 explicitly
   banning every competitor brand mark.
2. "SELLING FAST — ONLY 100 SETS LEFT!" (an unauthorised scarcity/stock-count claim).
3. "SUMMER SALE" tiled as the entire background (the offer ban was read as applying to
   badges only, not a full-background pattern).
4. "RM15 OFF FIRST ORDER — USE CODE NEW15" — an invented promo code that doesn't exist.
5. "+25% VISIBLY MORE MOISTURISED SKIN*" — an efficacy claim we cannot substantiate.
6. An invented customer testimonial.

The pattern is conclusive, not a string of unlucky prompts: **the model does not reliably
obey a text instruction about what NOT to render, especially in edit mode, where a real
photograph is the input and the forbidden content is already sitting in the pixels.**
`src/output_critic.py` (Prompt 4, Item 1) exists because of this — it inspects the
GENERATED image after the fact and flags what actually rendered, which is the only check
in this codebase that has ever been proven to catch these categories.

**When a new leak of this shape turns up, the fix is to extend `output_critic.CRITIC_SYSTEM`'s
checklist (and `CITED_RULE_IDS` if it ties to a numbered rule), not to add another
prompt clause to `brand_rules()`/the writer/`_edit_mode_instruction`.** A ninth rule or a
longer STRICT block has the same failure mode as the first eight rules already there —
more prompt text is not the lever that has ever worked for this class of bug.

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

## Known gaps (as of 2026-08-04)
- `brand_voice`, `approved_claims`, `approved_testimonials` are empty at every real call
  site — still owed by Harry — compliance mechanical checks are correspondingly
  strict-by-default until real approved material is wired through.
- `edit_image` can't restore `include_product` on a re-edit — there's no column to read
  it back from (unlike `text_in_image`/`generated_copy`, which the artifact row does
  carry). Known, unfixed gap.
- `get_artifacts_full`'s own default is still `LIMIT 50`, but both real call sites
  (`dashboard.py`'s `/api/artifacts` and `/api/page_lookup`) explicitly pass `limit=500`,
  so this is no longer the practical gap it was — only matters again if a new caller
  forgets to override the default, or artifact count ever exceeds 500.
- Bottle fidelity is blocked on Pillow compositing: the product cutout is parked at
  `product_assets/` but nothing composites it into the generated draft yet, so bottle
  accuracy still depends entirely on Gemini interpreting `visual_description` +
  reference photos correctly.
- Slack posting fails with `invalid_auth` (`slack_review.post_review`) — non-fatal to the
  pipeline, but review cards aren't reaching Slack.
- 2 ads remain unclassified after the backfill: one schema-validation failure, one
  transient Anthropic `529`. Left untouched, no category invented.
- Only competitors id=42 (Bangn Body) and id=48 (The Ayurveda Experience) are tagged
  `body_oil` — a category-scoped run currently hits just those two.
- **Product-matched reference selection is deliberately deferred.** Nothing in the
  pipeline picks a reference ad based on how well it matches the selected product (e.g. by
  category, format, or prior performance) — every scraped ad in the candidate pool is
  treated the same. Left alone pending evidence it's actually needed; don't build it
  speculatively.
- **`operator_instruction` pre-fill is per-browser, not per-user** — it's read/written via
  `localStorage` (`dashboard.html`'s `loadOperatorInstruction()`/`runPipeline()`), so the
  same operator switching machines or browsers won't see their last-used steering carry
  over. Fine for the single-operator-per-session workflow today; would need a server-side
  per-user setting (like `brand_settings`) to fix properly.
- Nothing deployed since 27 Jul 2026 — everything below (all of Prompt B, edit mode, and
  Prompt 4 items 1-5) is committed and pushed to `main`, but Cloud Run is still running
  the 27 Jul build; none of it is live.

## Prompt B — messaging angles + Claude prompt-writer pass (COMPLETE as of 2026-07-30)

All five original parts, plus every follow-on fix found during real runs, are implemented,
tested, and committed on `main` (not deployed — see Known gaps). Summary, newest-relevant
first:

- **Parts 1-5**: `angles` table + CRUD + sidebar panel; (ad × angle) dedup on
  `seen_ads`/`artifacts`; `illustrated` as a 4th `production_style`; the four run-strip
  operator controls (Angle/Realism/Text-in-image/Include-product); conditional
  `brand_rules(include_product, text_in_image, headline, subtext)` replacing the flat
  `BRAND_RULES` constant; the Claude prompt-writer pass
  (`generate_image_prompt_writer.write_creative_description`) sitting on top of
  `build_image_prompt`.
- **4b/4c**: free-text `body_area` and `offer_text` per-run inputs; two CSS
  stacking-context layout fixes (stats bar vs. run strip; Edit Angle modal vs. stats bar).
- **LOCAL_RUN**: `LOCAL_RUN=1` env var makes `/api/run` call the in-process runner
  (`_run_pipeline_bg`) instead of triggering the Cloud Run Job, so the Run Pipeline button
  can actually exercise local code. **Run the dashboard as `uvicorn dashboard:app` with no
  `--reload`** when testing this — `--reload` restarts the process on file changes, which
  kills the background thread `_run_pipeline_bg` runs on mid-run. Unset (the default),
  behaviour is byte-for-byte the old Cloud-Run-Job-only path.
- **Two dashboard bugs**: sidebar Remove/category-save buttons weren't a JS wiring bug at
  all — the sidebar had no explicit `z-index` and lost to `.sticky-controls` (explicit
  `z-index:90`) regardless of DOM order, visually covering and click-blocking it; fixed by
  giving the sidebar `z-index:95`. Separately, `PUT /api/competitors/{id}` had the same
  bug `POST` had on 27 Jul — `page_id=(page_id if page_id else name)` overwrote a real
  numeric `page_id` with the name whenever `page_id` wasn't supplied; fixed by passing
  `page_id` straight through and having `dedupe.update_competitor` skip that column in its
  `UPDATE` entirely when `page_id is None`.
- **Parts A/B/C** (found from live runs of the writer): the writer wasn't told
  `text_in_image`/`include_product`/`headline`/`subtext`, so it invented headline text and
  multi-bottle scenes that `brand_rules()` then had to override, and Gemini discarded the
  whole composition rather than reconciling the contradiction; the blueprint schema grew
  `creative_objective`/`target_audience`/`typography`/expanded `layout_detail` (all
  optional, schema-driven, zero `validator.py` changes needed); `test_writer_rule6_agreement.py`
  asserts the writer's prompt and `brand_rules()`'s rule 6/7 always agree in both
  `text_in_image`/`include_product` states.
- **Final two writer fixes** (2026-07-30): realism now resolves to
  `blueprint.production_style.style` when not explicitly given, so `"(auto)"` on the run
  strip means the reference ad's own detected style, not silence — and the writer states
  explicitly that `high_spec_studio`/`ugc_native`/`hybrid` mean photographic while
  `illustrated` means drawn, closing a bug where a photographic reference produced fully
  illustrated output. The writer also now bans any offer/badge/price/percentage when
  `offer_text` is empty (closing a competitor-discount leak: a draft rendered "20% OFF"
  lifted from the competitor's own `creative_objective`) and unconditionally bans naming
  any product category besides body oil, even when something quoted from the competitor
  ad names one (closing a "Bye-Bye, Body Lotion" bug).

**Root cause common to Parts A and the final two fixes**: the writer and `brand_rules()`
are two independent functions with no shared state — whatever mode flag one enforces
mechanically, the other must be told the *same* value explicitly, or it free-associates
from the competitor ad's own blueprint fields (`visual.subject`, `creative_objective`,
`typography.hierarchy_levels`) and describes a scene the guardrails then have to override.
Gemini's behaviour when that happens is not to comply with the override — it discards the
whole composition. So every mode flag `brand_rules()` reads (`text_in_image`,
`include_product`, and now effectively `realism`/`offer_text`/product category) must
always reach `_build_user_prompt` too, stated last and framed as STRICT/overrides-anything-
above. Never let the writer read a signal that isn't also gated mechanically downstream.

**Migration ran** (`migrate_angles.sql`) — the live DB and code now agree. Note:
`seen_ads_ad_angle_uq` is an **expression** unique index
(`ON seen_ads (ad_id, COALESCE(angle_id, 0))`), not a table constraint — it will show up in
`pg_indexes`/`\d seen_ads` but **not** in `pg_constraint`, so a check that queries
`pg_constraint` for it will wrongly report it missing.

**Six angles seeded** (`seed_angles.py`): `crepey_skin`, `glp1`, `bruising`, `sun_damage`,
`loose_skin` (`body_area` deliberately left blank — not yet confirmed, never guessed),
`menopause`.

**Team's confirmed answers** (govern how the run-strip controls must behave — don't
"simplify" these away):
- `body_area` is **per-run only**, never read from `angles.body_area` — body area varies
  every run even for the same angle. The angle's own `body_area` (where set) is a UI
  pre-fill *suggestion*, always overridable, never authoritative.
- `offer_text` is **per-run free text**, not always a discount — it can be any offer
  wording (bundle, gift-with-purchase, free shipping), not necessarily a percentage-off.
  Don't build offer-specific parsing/validation around it assuming a `%` or `£` pattern.
- The prompt-writer's output is **reviewed after generation, not before** — there's no
  operator preview/approval step between the writer producing `creative_description` and
  it being handed to Gemini. Review happens on the resulting draft image on the card, same
  as every other draft.

## Edit mode — reproduce the reference, substitute the product (2026-08-01 onward)
This is what finally produced recognisable clones, after generate mode's from-scratch
approach (a text description handed to Gemini, no image reference) reliably drifted from
the source composition. Off by default (`edit_mode` toggle on the run strip, team confirmed
usage is roughly 50/50 generate-vs-edit) — generate mode is unchanged when it's off.

- The competitor's own ad image is passed to Gemini as an input `Part` (`google.genai`
  `Part.from_bytes`), attached FIRST, ahead of the product's own reference photos, with
  framing text distinguishing the two roles explicitly: one is the ad to reproduce, the
  others are the Besque product to substitute in. `pipeline.process_ad` reuses the SAME
  bytes already downloaded for `deconstruct_image` — never a second download.
- The Claude prompt-writer pass is **skipped entirely** in edit mode, even when an angle
  is selected — the reference image IS the creative brief; a text `creative_description`
  from the writer would just fight it. `build_image_prompt`'s `edit_mode` branch replaces
  the writer/template scene text with `_edit_mode_instruction`'s own assembly.
- Rule 9 (`_RULE_9_SOURCE_IMAGE_IS_THE_COMPETITORS_AD`, edit-mode-only, additive to
  `brand_rules()`) states explicitly that every competitor brand mark — logo, emblem,
  watermark, roundel, badge, seal — must be removed, not just the product itself; a corner
  mark or seal counts exactly as much as the product label. Still prompt-only (see the
  guardrails note above) — the critic is the actual backstop.
- Colour palette substitution (Prompt 4, Item 5) is stated as ONE integrated instruction
  with the reproduce-faithfully instruction, not two competing ones: "geometry is
  preserved, colour is substituted." A separate `retheme_colours` toggle (default ON, per
  the team's own doc) governs this; OFF reverts byte-for-byte to the original
  faithful-colour-clone wording. The palette itself is DATA (`brand_settings` table,
  editable from the dashboard sidebar), never a hardcoded string.

## Operator instruction — free-text steering, fixed precedence position (Step 2, 2026-08-02)
The "Extra direction for this run" field on the run strip. Threaded like every other
run-strip control: `api_run` → `RUN_INSTRUCTION` env var → `job_runner` → `run_once` →
`process_ad` → `generate_image`.

**Precedence is fixed and tested, not incidental**: `build_image_prompt` inserts it
(`_operator_instruction_clause`) immediately after `brand_rules()` (rules 1-9 + compliance
C1-C6), and *before* whatever supplies the scene text (`creative_description` /
`_edit_mode_instruction` / the template). The clause states its own boundary in the prompt
text itself — "can NEVER grant a permission the rules above forbid" — so it can steer HOW
a scene looks without ever being able to authorise something a rule bans. Tests assert
this both by string position and by confirming instructions like "add a 50% off badge" or
"keep the competitor's logo" don't touch the corresponding guardrail text.

Persisted on the artifact (`operator_instruction` column, self-migrating) and shown on the
card, so a reviewer can see whether the operator asked for a wrong-looking draft. Pre-filled
from `localStorage` — see the per-browser-not-per-user gap noted above.

## Category picker, projected totals, run progress (Step 3, 2026-08-03)
`run_once(category=...)` already existed server-side; this made it reachable from the UI.
- Run-strip category `<select>`, options built dynamically from the DISTINCT categories
  actually present on competitors (never the fixed product-category schema enum), plus
  "All competitors" and a blank default. Selecting a category visibly clears any selected
  competitor in the sidebar (and vice versa) so the operator can always see which mode is
  active; the status line shows `"Category: body_oil — 6 competitors"`.
- Ad count is **per-competitor** — a category sweep multiplies by the number of matching
  competitors, so "2 ads" across 6 brands is 12 generations, not 2. A projected-total span
  next to Run states this before the run starts: `"= up to 12 generations across 6
  competitors"`.
- New `run_progress` table (single-row, self-migrating, same reasoning as
  `pipeline_warnings`): DB-backed, not an in-memory variable, because the Cloud Run Job
  path is a separate process with no shared memory with the dashboard — this is the only
  channel that can report "which competitor is running now" for BOTH run paths.
  `/api/run/status` surfaces it identically regardless of mode; the dashboard's progress
  line shows the live competitor name/index once available, falling back to the old
  elapsed-time guess only before the first real value lands.
- `run_once`'s summary gained `by_competitor: {name: {ads_seen, processed, skipped,
  failed, error}}` — image yield varies hugely per brand (CLAUDE.md's own earlier note:
  roughly 1/10 to 8/10 across pages), so a thin sweep total needs this breakdown to read
  as the pool, not a mystery.

**Measured sweep timing (2026-08-04, real run)**: 12 drafts across 6 competitors completed
in 24:26 — roughly 2 minutes per ad. Processing is **sequential**, not parallelised across
competitors or ads, so this scales linearly: a 50-ad sweep is on the order of 100 minutes,
not a fixed cost. Size category-sweep ad counts with this in mind.

## Prompt 4 — compliance hardening after the six-violation sweep (2026-08-04)
Seven items, ordered by risk, each its own commit so they can be bisected independently.

**Landed (committed on `main`, not deployed):**
1. **Output critic** (`src/output_critic.py`) — see the guardrails note at the top of this
   file. Post-hoc vision inspection of the GENERATED draft; the only check proven to catch
   the six-violation class. Non-blocking (runs after `save_artifact`, never fails a run),
   surface-only (never auto-rejects/regenerates), high/medium confidence only. Gated by a
   `check_output` run-strip toggle (off by default — real cost, an extra vision call per
   ad). `CITED_RULE_IDS` ties its checklist to actual rule numbers so it can't silently
   drift from `brand_rules()` without a test noticing.
2. **Two compliance holes**: `FIRST_PERSON_PATTERN` only matched literal "I" — extended to
   catch first-person possessive testimonial phrasing ("my new staple") with no "I" at
   all. New `check_unauthorized_efficacy_claim` (always-on, unlike the offer check) catches
   ratio/timescale efficacy claims ("3x more effective", "in just 7 days") that the
   existing percentage-only `NUMERIC_CLAIM_PATTERN` didn't reach — extended to both the
   copy path (mechanical) and the image path (prompt-only, since `approved_claims` isn't
   threaded to images at all).
3. **Hard-block medical/clinical/intimate-health/anatomically-explicit references**
   (`src/content_safety.py`) — NOT a judgment call, blocks before generation ever starts.
   Reuses signals already extracted by the classifier (`product_category.signals`,
   `visual.subject`, etc.) rather than a new blueprint field; only blocks on the
   combination of a medical keyword AND a non-product-like category, so a loose word
   choice ("hair treatment") against an ordinary `body_oil` classification never triggers
   it.
4. **Flag (never filter) references whose format can't carry a single-product message**
   (`src/reference_format.py`) — `layout_detail.product_count > 1`, `creative_format` of
   `offer_led`/`comparison`, or a bundle mechanic in `offer.mechanic`. Surfaced on the card
   ("reference was a 6-product bundle offer"), never gates generation — filtering shrinks
   an already-thin pool, flagging never does.
5. **Colour palette + typography substitution** — see the Edit mode section above.
   `TYPOGRAPHY_GUIDANCE` maps `creative_format` to a Besque typeface style (clean
   sans-serif for direct-response, elegant serif for premium/editorial, handwritten marker
   for testimonial-style) rather than copying the reference's own font, with the same
   import-time coverage assertion pattern as `PRODUCTION_STYLE_GUIDANCE`.
   **Caught during implementation, not by a live run**: the new `palette` parameter
   collided with an existing local variable of the same name (`visual.get("palette_mood",
   ...)`) inside `build_image_prompt`, silently shadowing the caller's value before
   `_edit_mode_instruction` ever saw it — renamed to `brand_palette` throughout. A reminder
   that a parameter name alone doesn't guarantee no collision in a large function.

**Not started:**
6. **Three edit-mode corrections.** *Why these matter*: aspect ratio is currently forced to
   a hardcoded "Square 1:1" even in edit mode, so a portrait reference comes out square —
   a clone that changes shape isn't a clone; needs to inherit from the reference and stay
   1:1-forced only in generate mode. The product-substance instruction currently says
   "match the product" rather than naming the actual colour from
   `products.visual_description` ("bright golden-amber oil") — pointing at a colour is
   weaker than naming it. Suppressing text currently leaves the CONTAINER behind empty
   (a real draft rendered a green "Don't Miss Out!" oval with no text in it, and six empty
   callout bubbles) — must remove the badge/pill/oval/button/banner/ribbon itself, not
   just its contents. The offer ban needs explicit scarcity/stock-count/promo-code/
   sale-wallpaper coverage — "SUMMER SALE" survived as a tiled background because the
   existing ban read as applying to badges only, not a full-background pattern.
7. **Prompt length check.** *Why it matters*: every item above has added more text to the
   assembled prompt (rules 1-9, compliance C1-C6, operator instruction, edit-mode
   instruction, palette, typography, efficacy ban, offer ban...) — nobody has yet checked
   the total against Gemini's actual input token/character limit, so a long prompt could
   be silently truncated with no visible symptom beyond a worse draft.
