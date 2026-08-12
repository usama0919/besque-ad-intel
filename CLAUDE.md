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

**Confirmed a third and fourth time, 2026-08-06** (Grüns GLP-1 illustrated ad): an FDA
disclaimer-removal instruction and an illustrated-bottle-must-not-be-photorealistic
instruction were BOTH present in the persisted prompt on BOTH attempts of the
corrective-retry loop — attempt 2 even quoted the critic's own finding back at Gemini
verbatim, naming the disclaimer's exact text — and Gemini ignored both, twice. **The
fix that actually worked was structural, not textual**: dropping the product's
photographic reference images entirely when the register is `illustrated`
(`_edit_mode_instruction`), so there's no photograph for Gemini to lean on regardless
of the prompt. The disclaimer leak has no structural fix yet and still relies on the
critic/retry loop as the sole backstop. **The corollary this adds: when a wording-only
fix keeps failing, look for a structural lever — an input you can withhold or change —
before writing a ninth sentence.** See "fabricated testimonials" in Known gaps below for
this exact violation class recurring on a different input (customer quotes rather than a
disclaimer) — also fixed 2026-08-06, and also via a structural change (a real stored
review or nothing), not more wording.

## `regenerate` froze an ad's prompt forever — fixed 2026-08-06, commit `45b183d`
Until `45b183d`, `pipeline._regenerate_existing_draft` never called `build_image_prompt`
again — it read the artifact's OWN stored `image_prompt` text verbatim and just
appended `_regenerate_delta_clause(instruction)` on top
(`generate_image_prompt.regenerate_from_stored_prompt`). So any ad that already had a
draft carried its ORIGINAL generation's prompt for life: no later rule, guardrail, or
compliance fix could ever reach it through Regenerate, no matter how many times an
operator clicked it. **Drafts the team actively iterates on via Regenerate were
therefore the LEAST protected against a later fix, not the most** — the opposite of
what anyone would assume from the UI.

Found live: the Grüns GLP-1 illustrated-bottle fix (see the guardrails note above)
appeared to silently not work on a real ad, even after confirming the source was
correct and the process had been restarted after the fix commit. The actual cause
traced two levels deep — first to `_regenerate_existing_draft`'s frozen prompt, then to
the fact that the specific ad tested had already been regenerated once *before* the fix
landed, so its stored prompt (and everything descended from it) predated the fix
entirely and no restart could ever have changed that.

**Fixed**: `_regenerate_existing_draft` now REBUILDS the prompt every time via
`build_image_prompt`, from the artifact's own stored inputs (`blueprint`,
`generated_copy`, `text_in_image`, `operator_instruction`, plus six newly-added
nullable artifact columns — `include_product`/`retheme_colours`/`realism`/`body_area`/
`offer_text`/`product_id`, self-migrating in `dedupe.init_artifacts()`) — THEN appends
the operator's delta instruction on top. A stored input that comes back NULL (a
pre-migration row, or a caller that never passed it) is logged by name with what it was
defaulted to, never silently guessed. Same function also now falls back to a normal
first generation when no artifact exists yet, instead of failing the ad (was:
`"regenerate requested but no existing artifact for angle_id=None"` — same root cause,
the function assumed history always exists once Regenerate is requested).

**The standing lesson, not just the bug**: verifying an image-path fix by clicking
Regenerate on an ad that already has a draft proves NOTHING about whether the fix
works, because Regenerate's own mechanism can mask an unrelated, already-fixed bug
indefinitely. **Always verify a prompt/rule fix via Generate on an ad that has never
been drafted before — never via Regenerate.**

## Operational gotchas (learned 3 Aug 2026)

- **Restart uvicorn after any commit touching `src/`.** No `--reload` (deliberate —
  the watcher kills runs mid-flight), so Python holds each module as first imported.
  This cost an afternoon: every ad failed on a `save_artifact` kwarg that was
  demonstrably present in the repo, the venv, and a live `inspect.signature()`.
  The process had new `pipeline.py` and old `dedupe.py`.
- **A burst of Google failures usually means expired ADC, not a code bug.**
  Re-auth *and* restart. Permanent fix is a service-account key, deferred to the
  security pass.
- **The long silent scrape is not a hang.** `client.actor().call()` blocks until
  Apify reaches a terminal state; the SDK's `_stream_log` thread dies on
  `impit.TimeoutException` and takes all progress visibility with it. Heartbeat
  now covers this.
- **A green suite has been wrong three times.** It asserts prompt assembly, not
  that components were told consistent things. `process_ad` tests mock
  `save_artifact`, so caller/callee mismatches are invisible.
- **Tests must not write to the prod DB.** Five manual cleanups in one day.
- **Claude Code stalls on background commands.** It cannot wake itself. Say
  "report now from what you already have — plain text, no tool calls."
- **Never `update_product` / `update_competitor` for a single field.** Read-modify-write;
  this shape wiped six verified page IDs. Demand targeted SQL and see it first.

## Environment
- Windows 11 + **PowerShell**. No bash syntax: no heredocs, no inline `VAR=x cmd`, no
  `&&`/`||` (use `;` or `if ($?) { }`), `2>$null` not `2>/dev/null`.
- Python lives in `./venv` — run tests as `./venv/Scripts/python.exe -m pytest tests/ -q`.
- `assets/` is gitignored; add throwaway root scripts to `.gitignore` too.
- `google-cloud-storage` must be **installed in `./venv`**, not merely listed in
  `requirements.txt`. Bucket reads are wrapped in bare `except Exception: pass`
  (`backfill_classify.py`) or downgraded to `NotImplementedError` (`assets.py:60`), so a
  missing package looks exactly like missing data.
- **`Pillow` was missing from `requirements.txt` entirely (not merely uninstalled) until
  2026-08-04** — found live, the first deploy since 27 Jul. `generate_image_prompt.py`'s
  `from PIL import Image` (`Image.open(...)`, used for the edit-mode aspect-ratio-inherits-
  from-reference feature) was added 2026-08-03 (`8bf7a2b`), well after the 27 Jul build, so
  there's no mystery about "how it worked before" - it didn't exist yet in anything ever
  deployed. It sat latent and unexercised for a full day because nothing had redeployed to
  actually run that import path in the dashboard *service* process (the Cloud Run *Job*
  path, `job_runner.py`, imports `pipeline` unconditionally at module level too, so it was
  equally broken - just never executed either). **The test suite could not have caught
  this**: tests run against the local `./venv`, where Pillow *is* installed (this file's own
  instruction above), so nothing anywhere verifies the deployed container's actual
  dependency closure - a package present locally but missing from `requirements.txt` is
  invisible to every test that's ever green. Fixed by pinning `Pillow==12.3.0` (matching the
  local venv's installed version) in `requirements.txt`.

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

## `POST /api/competitors` still writes the brand NAME into `page_id` — open bug
`dashboard.py:753`: `resolved_page_id = page_id or name`. This is the exact bug already
fixed in the PUT handler (see "Two dashboard bugs" under 2026-08-04 below) — but the
ADD-competitor endpoint still has it. Found 2026-08-06: **38 of the 50 tracked
competitor rows had the brand's NAME sitting in `page_id`** instead of a real numeric
Facebook Ad Library id; 32 fixed by hand. **The fallback itself is still live** — every
new competitor added through this endpoint without an explicit numeric id will keep
reproducing this. Distinct from the note directly above: that one is about `scrape.py`
never validating an existing `page_id`; this one is about `dashboard.py` writing a
wrong `page_id` to begin with.

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

## Known gaps (as of 2026-08-04, additions dated 2026-08-06 marked inline)
- ~~**SEVERITY-CRITICAL: fabricated testimonials render on the IMAGE, not just in
  copy.**~~ **FIXED 2026-08-06, commit `808ddee`.** Generated claims were appearing
  inside quotation marks with a star rating, styled exactly like a real customer quote,
  despite `approved_testimonials` being empty at every call site (see below) and
  compliance rule C2 supposedly banning this — the EXACT violation category from the
  original six-violation sweep (top guardrails note, leak #6), recurring on the image
  path specifically. `pipeline.select_testimonial_review` now picks a REAL review from
  `product_reviews` (18,920 imported, see 2026-08-06 note above) for a `social_proof`
  `single_quote` zone — deterministic by `ad_id`, length-filtered, and excludes reviews
  that read as complaints despite a high star rating (a genuine 5-star review reading "I
  have not received it yet... it's been weeks since I ordered it" passed a naive
  rating+length filter cleanly during testing — keyword heuristic, same limitation
  `compliance.py` already accepts). No real review, or an `aggregate_bar` (no approved
  count/average exists, held pending Harry): the zone is REMOVED entirely, never left for
  Gemini to invent something — see `_structural_zones_clause`.
- **OPEN, 2026-08-06: bottle rendering register only fixed in ONE direction.** `fc73058`
  stops a photorealistic bottle appearing in an illustrated scene (drops reference
  photos, describes the bottle natively when `style=="illustrated"` — see the top
  guardrails note). The REVERSE has also been observed live: an illustrated-looking
  bottle in an otherwise photographic scene. Needs one general rule that binds both ways
  — `_edit_mode_instruction`'s photographic branch still unconditionally says "shown in
  the reference photo(s) that follow" with no corresponding check the other direction.
- **OPEN, 2026-08-06: double headline when `text_in_image` is on.** The HTML overlay
  (headline/offer rendered as a separate layer on the card) and the baked-in in-scene
  text (`_edit_mode_instruction`'s TEXT branch) can both be active at once, rendering the
  headline twice — once as real overlay HTML, once drawn into the image itself. Not yet
  root-caused or fixed; needs one to be suppressed when the other is active rather than
  both defaulting on independently.
- **OPEN, 2026-08-06: competitor product text still leaks into drafts.** Not yet
  root-caused this session — flagged for follow-up, no fix direction identified yet.
- **OPEN, 2026-08-07: pregnancy as a use context is not covered by any compliance rule
  (C1-C6).** Found live on artifact 1136 (`ad_id=1653458269057951`, OSEA): a corrective
  retry (triggered by the output-critic testimonial false positive fixed this session —
  see the testimonial-critic-awareness fix below) produced fabricated copy including "My
  baby bump is so so soft" — pregnancy framing rendered on an ad for Besque, a brand
  explicitly positioned for women 40+. `compliance_rules.py`'s own docstring already
  states C1/C4/C6 are prompt-only and only C2 has a mechanical backstop; pregnancy isn't
  even a prompt-only case — no rule among C1-C6 names it at all. C5 (medical/
  pharmaceutical claims, GLP-1) is the nearest neighbour but doesn't cover this: pregnancy
  appropriateness is an age/use-context question, not a drug-substitution claim. The
  output critic's own checklist doesn't have a dedicated category either — it happened to
  flag this specific instance at medium confidence, mislabeled as "C5 / brand-safety",
  which isn't really what C5 says. Not fixed — no rule added, no critic checklist item
  added. Needs a decision on what the actual policy should be (e.g. "never depict or
  imply pregnancy use") before writing a rule for it, not a guessed wording.
- **`DATABASE_URL` in `.env` pointing at a raw IP is correct, not a misconfiguration** —
  that's the documented local-dev route to Cloud SQL; Cloud Run itself connects via the
  socket path instead. `.env.example`'s `localhost` value is the thing that's out of date,
  not `.env`. Do not "fix" `.env` or the connection logic to point at localhost.
- **Real-DB tests run against that same Cloud SQL instance, not an isolated test DB** — the
  only safety net is try/finally cleanup in each test, not connection-level isolation. A
  leak found 2026-08-04: `test_dashboard.py` has at least one passing test (inserts an
  artifact with `page_name="TestBrand"`, `ad_id` prefixed `ART_`) whose insert isn't
  wrapped in the same try/finally the equivalent `test_core.py` tests use — it recurred on
  every full-suite run that day, one orphaned row per run, manually deleted each time.
  Scale check the same day found `seen_ads` carrying **1,497 non-numeric (test-shaped)
  rows out of 1,670 total** — `TEST_`/`PIPE_`/`RUN3_`-prefixed, accumulated across many
  past sessions (real Facebook `ad_archive_id`s are pure numeric, so none of this is
  mistaken real data), never cleaned up because nothing in the suite deletes from
  `seen_ads` after a `mark_seen` call. Left untouched given the size — deliberately NOT
  bulk-deleted without a decision on it. Scheduled for Chunk 7 alongside the `TEST_MODE`
  guard — not fixed yet. If you're hunting for orphaned rows in the meantime: real rows
  never have `__test_` in a name, `TestBrand` as a page_name, or an `ad_id` prefixed
  `TEST_`/`PIPE_`/`ART_`/`POOL_`/`FP_`/`CARD_`/`SEL_`/`RUN3_`.
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
  per-user setting (like `brand_settings`) to fix properly. `pool.html` (Chunk 5) reuses
  the SAME `localStorage` key for its own instruction field, deliberately, so the two
  pages share one steering history rather than drifting into two — but that also means
  this gap now applies to both pages identically, not just `dashboard.html`. Left as-is
  per instruction (2026-08-04); noting it here so it isn't lost, not fixing it now.
- ~~Nothing deployed since 27 Jul 2026~~ — **deployed 2026-08-04**, live at
  `besque-dashboard-00041-2md`. See the dated section below for what shipped and what
  broke on the way.

## 2026-08-04 — first deploy since 27 Jul, plus a live-use session (handover record)

### Deploy
- Live revision: **`besque-dashboard-00041-2md`**. `besque-pipeline` Job updated to the
  same image. This is everything through Prompt B, edit mode, Prompt 4, and Chunks 1-6.2
  (the ad pool: fetch/browse/generate) going live for the first time.
- **Pillow was missing from `requirements.txt` entirely — not merely uninstalled.**
  `generate_image_prompt.py`'s `from PIL import Image` (added 2026-08-03, commit `8bf7a2b`,
  for the edit-mode aspect-ratio feature — genuinely used, `Image.open(...)`, not dead code)
  predates any deploy since 27 Jul, so it sat latent for a day. Every path that imports
  `pipeline.py` broke in the container on the first deploy that actually exercised it:
  `POST /api/fetch`/`POST /api/generate` (new), **and** `job_runner.py` — the existing,
  already-relied-upon Run Pipeline button's Cloud Run Job entrypoint, which imports
  `pipeline` unconditionally at module level. Fixed by pinning `Pillow==12.3.0` (matching
  the local venv) and redeploying (`besque-dashboard-00041-2md`).
  **The test suite cannot catch this class of bug**: tests run against the local
  `./venv`, where Pillow *is* installed, so nothing anywhere verifies the deployed
  container's actual dependency closure. A package present locally but missing from
  `requirements.txt` is invisible to every test that's ever green. No fix in place for
  this gap yet — worth a CI step that builds the container and smoke-imports `pipeline`
  before trusting a green local suite pre-deploy.
- **Several columns exist in prod but aren't reproducible from code against a fresh
  database**: `seen_ads.angle_id`, `artifacts.angle_id`, `artifacts.text_in_image`,
  `competitors.category`, `products.category`/`image_key`/`image_keys`/`visual_description`
  are all baked directly into their table's `CREATE TABLE IF NOT EXISTS` column list, with
  no corresponding `ALTER TABLE ADD COLUMN`. Since those tables predate these columns, that
  statement is a no-op against the already-existing production tables — confirmed live via
  `information_schema` that all of them already exist in prod (via `migrate_angles.sql` for
  the angle ones, undocumented for the category/product ones), so this deploy was safe, but
  a fresh database built from this code alone would be missing every one of them. Not fixed
  - recorded so a future migration effort knows the full list.
- `try_start_fetch_job`/`get_fetch_job`/`get_generate_job` now self-recover a `'running'`
  row whose background thread died before calling `finish_*_job` (a killed process, the
  Pillow crash above) - previously permanent (competitor 1's `fetch_jobs` row got stuck this
  exact way live and was cleared with a targeted `UPDATE`). See `dedupe.py`'s
  `FETCH_JOB_STALE_SECONDS`/`GENERATE_JOB_STALE_SECONDS`.

### Open bugs found in live use (not fixed - reported for tomorrow)
- **`edit_mode` logs `False` on every ad despite the pool checkbox being ticked.** Traced
  the full chain and every hop is correct in the source as of `7e04451`:
  `pool.html`'s `editModeToggle.checked` → sent as `edit_mode` in the `POST /api/generate`
  body → `dashboard.py`'s `api_generate` reads it (`body.get("edit_mode", False)`) and
  forwards it into `pipeline.generate_from_selection(edit_mode=edit_mode)` →
  `generate_from_selection` forwards it into `process_ad(edit_mode=edit_mode)` → the log
  line itself (`"image generation starting (edit_mode=%s)"`) prints that exact parameter,
  with nothing between receiving it and printing it. No line silently overwrites it -
  ruled out a downstream fallback (e.g. missing reference bytes) as the explanation for
  what the *source* does. Leading hypothesis: a stale running process - this codebase
  already has one confirmed incident of exactly this shape ("Restart uvicorn after any
  commit touching `src/`" above), and the toggle-wiring commit (`60d7200`) postdates when
  the operator's local uvicorn was last known to be started. Unconfirmed - would need that
  process restarted to check, which wasn't done this session. If it was hit against the
  deployed revision instead, that's less likely (verified fresh post-fix) but not ruled out.
- **`body_area` is applied uniformly to every ad in a batch, with no awareness of what the
  reference actually shows.** Concrete failure: a product-only reference (no human subject)
  generated with `body_area="legs"` produced illustrated legs draped over the bottle. This
  is now the evidence for a real fix direction: derive body area from the reference image
  itself, with the operator's per-run input as an override rather than an unconditional
  value - not something to build speculatively, there's now a live failure to point at.
- **`artifacts.page_name` is stored with a trailing space**, so `export_drafts.py`'s
  `--competitor-id` (an `ILIKE` match against the competitor's tracked name) never matches
  in practice. Two things to fix: the comparison itself (trim both sides), and - more
  important - find where the trailing space is actually written (`process_ad`'s
  `save_artifact` call passes `page_name=ad.get("page_name", "")` straight through from the
  scraped ad dict; worth checking `scrape.py`'s `_map_ad` and Apify's own raw `page_name`
  field for where the space originates before just trimming it out downstream).
- **Compliance false positive**: `"blend of 7 cold-pressed oils that"` flagged as a verbatim
  competitor phrase reused. It's Besque's own product description, stored in `products`,
  not competitor language. Worth checking once `edit_mode` is resolved (per instruction,
  not investigated further this session).
- **Slack posting fails with `invalid_auth`** after every save - same as the existing Known
  gaps note above, reconfirmed live, still non-fatal to the pipeline.
- **Unexplained cleanup, now explained**: a `"Deleted: 0 review_decisions, 2 artifacts"`
  line appeared during the session. Traced to an untracked root script,
  `cleanup_testbrand.py` (not written or run by Claude this session, not in git) - a
  dry-run-by-default utility that deletes rows matching a `page_name` (default
  `"TestBrand"`) from `artifacts` + `review_decisions`, gated behind `--confirm`. Someone
  ran it with `--confirm` against the exact `TestBrand` leak this file's Known gaps section
  already documents. Not a mystery once found - just untracked, so worth `git add`-ing if
  it's going to be a standing tool.

### Apify findings
- **The Ad Library page id is not the profile page id.** Competitor 52's profile URL gives
  `61575713267532`, which returns zero ads; the actual Ad Library page id is
  `691427450714766`. Anyone adding a competitor from a profile link will hit this silently
  - `scrape.py` has no validation that would catch a valid-but-wrong numeric id (see the
  existing "A numeric `page_id` bypasses the name gate" note above - same root shape, worse
  because the wrong id here still looks completely plausible).
- The actor's real input schema (confirmed by reading it directly, not documentation) has
  **no sort/recency parameter and no `image_and_meme` media type**, but does have
  `startDateMin`/`startDateMax`/`activeStatus` - all three now wired through `fetch_pool`
  as of Chunk 6.2. `mediaType` handling is deliberately untouched (client-side filtering
  stays the only real gate, per the existing note on this above).
- **Apify is intermittent independent of anything in this codebase**: identical page and
  parameters returned 50 ads at 11:24 and 0 at 13:27 the same day, with the actor's own log
  reading `"Extracted 0 initial ads from HTML"`. Not a caching or param bug on our side -
  worth remembering before assuming a zero-ad result means something's broken here.

### Team decisions (from Sayali, 2026-08-04) - for a not-yet-built testimonial/review feature
- Quotes may be lightly reworded to fit, but the customer's name must be real.
- Both result phrases and blunt problem phrases are usable on images.
- Image direction may show either the problem or the result depending on whether the ad is
  problem-aware or solution-aware - a **new per-run creative input we do not currently
  have** anywhere in the pipeline.
- Operator picks body area freely; a single review may serve more than one angle.
- Testimonial is default-on with a per-run override; match on angle plus length.
- Show quote plus first name and initial only - no age, no timeframe.
- Reviews get a reuse cooldown; skip incentivized and unverified rows; Magic Body Oil only
  for now.
- **Do NOT state the shower-oil absorption mechanism as fact.** This directly contradicts
  the product doc's own Step 4, which instructs mechanistic explanatory subtext - the
  team's answer overrides the doc: mechanism may inform which customer language gets
  chosen, but must never appear as an assertion outside a real quote.
- Still unanswered: emotional register for loose skin, crepey skin, bruising, and
  menopause; and a separate, still-open bottle-description dispute.

### Reviews imported 2026-08-06 — `product_reviews` table, aggregate claim NOT cleared
18,920 real customer reviews imported (`import_reviews.py`), scoped via
`products.shopify_product_ids` to product_id=1 only, filtered to Active status +
rating≥4. Medical-flag rows are STORED with the matched keyword, not dropped — see C5
above, mechanism/medical language still needs a human check before use, this just keeps
the row instead of silently losing it.

**A published review-count/average is HELD pending Harry, and the data explains why**:
Active rows are 99.95% 4-5★, but ~20,000 Rejected rows are 89% 1-3★ — the two statuses
are clearly not a random sample of the same population, they're filtered by outcome.
Publishing an aggregate built only from the surviving (self-selected) Active set would
likely read as a substantiated efficacy/satisfaction claim, not neutral social proof.
Do not compute or surface an aggregate count/average until this is resolved — same
category of open regulatory question as the still-unanswered items directly above.

### Also as of today
- `export_drafts.py` exists at repo root - a standalone, read-only export utility (drafts +
  optional reference images + `manifest.csv`, zipped). Not committed yet - it's currently
  untracked, same as the ad-hoc root scripts. Never imports `dashboard.py`/`pipeline.py`.
- Chunk 6.1 added the five existing run-strip toggles (`text_in_image`/`include_product`/
  `edit_mode`/`check_output`/`retheme_colours`) to `pool.html`, threaded through
  `POST /api/generate` into `generate_from_selection` - same names/defaults as
  `dashboard.html`'s own run strip, nothing invented.

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
   **Confirmed firing end-to-end live, 2026-08-06**: a HIGH-confidence finding on
   attempt 1 → one corrective retry → still HIGH → saved and marked
   `critic_high_after_retry`, never left indistinguishable from a clean pending draft.
   First real proof this loop's designed behaviour actually happens, not just passes
   its own tests.
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
6. **Image resolution — `ImageConfig.image_size` (2026-08-06).** Never set anywhere before
   this, so every generation ran at Gemini's lowest tier by default — measured live, a
   1080x1920 reference produced a 768x1376 draft, under Meta's 1080x1350 minimum for a 4:5
   feed image. Now explicitly `"2K"` on every `ImageConfig` this module builds. Measured
   1K vs 2K on the same reference: 1536x2752 vs 768x1376 (exactly double, as expected),
   32.1s vs 22.9s generation time (+40%), 4.9MB vs 1.1MB file (~4.3x). Confirmed nothing
   downstream resizes/recompresses on save or serving - the size increase is real and
   compounds across a review screen showing many cards at once and across GCS storage.
   **Follow-up queued, not built**: serve a downscaled thumbnail on cards, full resolution
   only on click.

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

## 2026-08-07

**SESSION 7 AUG 2026 — pushed: `e27b9eb`, `5cfa8d6`, `d4eab44`, `b5de089`.**

### Landed
- **FRAMING.** Explicit `aspect_ratio` REINSTATED in edit mode. Omitting it is
  NONDETERMINISTIC, not merely imprecise: the same reference produced 0.5581 on one run
  and 0.322 on another with no ratio set. Forcing has one documented failure (a perfect
  1:1 reference with `"1:1"` explicitly set still returned 1.79:1) but usually
  constrains. Derived per reference via `derive_aspect_ratio`, never hardcoded. Generate
  mode never set it on the config at all — only the prompt-text "Square 1:1" line — and
  is unchanged pending its own probe. This answers the 6a question open since 3 Aug.
- **PRODUCT COUNT.** `layout_detail.product_count` now reaches the product clause.
  Rendering N of the SAME authorised bottle when the reference shows N products is
  CORRECT — reproducing composition, not faking a second SKU. Earlier "do not
  duplicate" wording was wrong and collapsed every reference to one bottle.
- **ZONE REPRODUCTION, generalised by zone TYPE not by string.** award/editorial/
  endorsement always REMOVES (Besque cannot imply an award it has not won); offer-shaped
  substitutes `offer_text`; cert-shaped substitutes `products.certifications`;
  price_anchor substitutes `offer_text`; product_callout substitutes product name.
- `products.certifications` JSONB added. Product 1 = `["Vegan","Cruelty Free","100%
  Natural"]`, verified against the real label image.
- **REGENERATE PRECEDENCE:** live operator input > stored artifact value > default. Live
  input was previously not consulted AT ALL on regenerate. Missing/unreadable draft
  image now falls back to a first generation preserving `edit_mode` instead of
  hard-failing.
- **CRITIC TESTIMONIAL-AWARENESS.** `check_draft` now receives the authorised
  testimonial; it previously flagged EVERY quote as a C2 fabrication, including real
  reviews `select_testimonial_review` had correctly picked.
- **PRE-RETRY CONTRADICTION FILTER**, general not testimonial-specific.

### Biggest finding
A false-positive critic finding fed back into a retry produced a prompt that
simultaneously demanded and forbade the same element. Gemini resolved the contradiction
by inventing THREE genuinely fabricated testimonials plus copy implying pregnancy use on
a 40+ product (artifact 1136). **A false positive did not just waste a paid call — it
manufactured a real violation.**

### Nothing-to-clone gate — added and REVERSED the same day
Skipping a reference with no product and no text zone contradicts the agreed order —
CLONE THE REFERENCE, THEN APPLY THE OPERATOR'S INSTRUCTION. Such a scene is still
usable: with `include_product` and `text_in_image` on, product and copy are ADDED rather
than substituted. Detection and the pool badge are kept as information, never a block.
The residual hard block on nudity or sexualised OUTPUT stays — it applies to what we
generate, never to which references are permitted.

### Open bugs, priority order
1. `/api/artifacts` 500s with `MemoryError`. `get_artifacts_full` returns every column
   including `image_prompt` and `copy_prompt` (~15KB/row) and the LIMIT was raised; the
   dashboard polls it every few seconds. Split into a card-shaped list endpoint plus a
   single-artifact detail endpoint, and paginate.
2. Offer pill not baked into the image — reference had an offer zone, `offer_text` was
   supplied, no pill appeared; the offer renders only as the HTML overlay outside the
   frame. `structural_zones` came back `None` on at least one artifact — suspected
   deconstruct extraction gap, unproven.
3. Stray product callout — a white rectangle with the product name appeared between two
   bottles in a position no reference zone occupied.
4. Substance properties not extracted — thin pale translucent oil in the reference
   rendered as a thick opaque blob reading as honey. `substance_colour` exists;
   behaviour (viscosity, opacity, how it pools and runs) does not. Same shape as
   `scene_lighting`.
5. Product placement not reproduced — reference bottle entered frame tipped from one
   side; draft rendered it upright, centred, mirrored. Count and size carry over;
   placement is re-invented.
6. Relative product size — a small-vs-jumbo reference rendered two near-identical
   bottles, losing the contrast that was the ad's argument.
7. Pregnancy as a use context is not covered by compliance_rules C1-C6 (see the
   2026-08-07 note under Known gaps above).
8. Several artifacts point at draft images that no longer exist.

### Text is DONE — angle vocabulary now feeds copy generation (2026-08-10)
`angle_language` has six rows (crepey_skin, glp1, bruising, sun_damage, loose_skin,
menopause), loaded from `docs/angle_language.md` — now committed in the repo — via
`scripts/load_angle_language.py`. `generate_copy.py` reads it through
`dedupe.get_angle_language(angle_slug)`, threaded into `build_copy_prompt`'s ANGLE
LANGUAGE section. See the 2026-08-10 section below for the three-tier treatment and the
TIER 1 headline requirement. The three standing overrides carry forward unchanged: never
invent a statistic or timeframe; mechanism never asserted as fact outside a real quote;
nickname and first initial only — no ages, no full names, no platform name.

### Working rules reconfirmed
- Restart uvicorn after every commit, FROM THE PROJECT DIRECTORY. Apify failed all
  morning with "authentication token is not valid" purely because uvicorn never loaded
  `.env`; the same token worked from the terminal. Cost three separate diagnoses in one
  day.
- Verify via GENERATE on a never-drafted ad, never via Regenerate.
- Prompt-only rules DO NOT BIND on the image path. A prompt stating PRODUCTLESS MODE
  four times still rendered a bottle.
- Name the files in every task; unscoped tasks cost 20+ minutes.
- Never run the full suite. All `:5433` failures in `tests/test_core.py` are
  pre-existing - a missing local test Postgres, not regressions. No fixed count: it was
  five when this rule was first written, is 19 as of 2026-08-11 evening, and will keep
  growing as DB-backed tests are added to that file - don't treat any number here as
  the invariant, only the file and the error signature (`psycopg2.OperationalError`,
  port 5433, connection refused).
- No `ad_id`, `page_id` or `competitor_id` in `src/`. Example ads are evidence of a
  failure, never the scope of a fix.

## 2026-08-10

**SESSION 10 AUG 2026 — pushed: `f38cd51`, `d042151`, `01e756e`, `88f979f`, `bb9dfe7`,
plus the earlier `92875cd`, `e480b57`, `d08e779`, `091c3b0` from the same day.**

### Angle language — complete
`scripts/load_angle_language.py` parses `docs/angle_language.md` into `angle_language`
(six rows, one per angle). `dedupe.init_angle_language()` is wired at five production
call sites: `classify_review_angles.py`, `dashboard.py` (one call inside the app-startup
`_init_tables()` hook — see below, not per-request), `seed_angles.py`, and
`src/pipeline.py` (twice — `generate_from_selection` and `run_once`).

`generate_copy._angle_language_clause` (commit `3cce4cb`, escalated `d08e779`) presents
three tiers, never flattened into one bag of vocabulary:
- **TIER 1 — WRITE FROM THIS (REQUIRED).** `common_phrases`. The headline MUST come from
  one of these, selected or adapted — escalated from "prefer" after two drafts from the
  same angle diverged (one used a TIER 1 phrase, one paraphrased `products.description`).
  No schema-changing escape hatch for a "no fit" case: lists run 22-45 entries per angle,
  so the model is told to pick the closest and adapt, never to fall back to product
  facts or invented phrasing.
- **TIER 2 — TONE ONLY, NEVER EMIT.** `core_angle`/`main_pain_point`. Context for
  register; no sentence may appear in output.
- **TIER 3 — REFERENCE ONLY, EXPLICITLY FORBIDDEN AS COPY.** `result_phrases`/
  `main_benefit` — customer-reported OUTCOMES, unsubstantiated efficacy claims if Besque
  asserts them directly. Permissible only inside a real stored quote (which this prompt
  never produces).

`PRODUCT` in `COPY_PROMPT` was reworded the same day to state outright it's a
**constraint** ("bounds what may be CLAIMED"), not a copy source — the two sections were
competing for the same job before this. `image_direction`/`best_verbatims` are never
read into the copy prompt at all (image-path-only / `select_testimonial_review`'s job).

**Watch item, not yet tripped**: TIER 3 lists forbidden phrases inside the prompt itself
— the same demand-and-forbid shape that produced artifact 1136. If a result phrase ever
shows up in a real headline, the fix is deleting TIER 3 from the prompt, not adding more
prohibition wording.

### `products.description` trimmed; `hero_claim` blanked — both done 2026-08-10
`products.description` (id=1) no longer asserts mechanism as fact — this is what the 4
Aug "compliance false positive" entry (`"blend of 7 cold-pressed oils that"` flagged as a
reused competitor phrase) actually was: not a false positive at all, but a real violation
of the "mechanism never asserted outside a real quote" override, sitting in Besque's own
product copy. **`hero_claim` blanked the same day** (verified via `dedupe.get_product(1)`:
before `"Visibly firms and tightens the skin with consistent use"`, after `""`) — it had
been an unsubstantiated efficacy claim handed to every copy/image prompt as an
authoritative "Key claim." **It was live in the copy prompt for every draft generated
earlier today**, before the blank; anything from today's session predating this fix may
carry that claim baked into its `copy_prompt`/`generated_copy` — worth a spot-check if any
of those drafts get promoted. Blank is a placeholder, not the intended end state: pending
real `approved_claims` from Harry. Until those land, `generate_image_prompt.py`'s
`build_image_prompt` (`"Key claim: {hero_claim}."` inside `product_desc`) renders that
line empty rather than invented, and `generate_copy.py`'s `_product_facts` — which already
drops any empty-string field before building the PRODUCT JSON blob — now omits
`hero_claim` from the copy prompt entirely rather than sending an empty one. Correct
fallback behaviour, not a fix in itself.

### Critic gate — `review_status`, badge, export exclusion, backfill, check-only regenerate
Live trigger: ad `820540537722129` — critic attempt 1 found 2 HIGH findings, the
corrective retry ran, attempt 2 STILL found 2 HIGH, and the artifact saved with no
failure signal anywhere. The retry loop worked; nothing acted on its result.

- `artifacts.review_status TEXT DEFAULT 'ok'` (`'ok'` | `'failed-review'`), added to both
  `CREATE TABLE` and the `ALTER TABLE` migration block — the 4 Aug schema-gap class (a
  column in `CREATE` but never in `ALTER`, so unreproducible against a fresh DB) must not
  repeat.
- `dedupe.update_artifact_findings` now writes `review_status` in the SAME `UPDATE` as
  `critic_findings` — the flag and the findings that justify it land together, never one
  without the other.
- Dashboard: a distinct amber "Failed Review" badge (`templates/dashboard.html`), driven
  directly by the stored `review_status` field, shown alongside (not instead of) the
  approve/reject/pending badge — they're independent axes.
- `export_drafts.py` excludes `review_status='failed-review'` by default; `--include-failed`
  overrides; prints the excluded count so an operator never silently gets fewer files
  with no signal why.
- **Backfilled 27 rows** live (`UPDATE artifacts SET review_status='failed-review' WHERE
  critic_findings @> '[{"confidence":"high"}]'::jsonb`), matching an independent direct
  count. Artifact 1136 (the fabricated-testimonials/pregnancy-framing incident) confirmed
  among them.
- `_regenerate_existing_draft` now runs the critic too, but **CHECK-ONLY, no retry, by
  design**: `regenerate_from_stored_prompt` has no hook for a second, critic-feedback
  delta layered on top of the operator's own delta — building one risks the exact
  artifact-1136 shape (a prompt that simultaneously demands and forbids the same
  element). Regenerate is already operator-driven: a `failed-review` result there is the
  operator's own cue to regenerate again with a better instruction, not something the
  pipeline should try to self-correct.

### `save_artifact(regenerate=True)` DELETE+INSERT resets any column absent from its INSERT list — a trap for future columns
`save_artifact`'s regenerate path is `DELETE FROM artifacts WHERE ad_id=... ` then a
fresh `INSERT` with an explicit column list (`dedupe.py`, ~22 columns). **Any column not
in that list silently resets to its schema default on every regenerate**, independent of
what any caller does before or after. This is exactly how `review_status`/
`critic_findings` were getting silently cleared — fixed in two places (`_regenerate_
existing_draft`, and `process_ad`'s own fallthrough path when an existing row's draft
image is unreadable) by fetching the PRIOR value via `dedupe.get_artifact` *before*
calling `save_artifact` (the DELETE destroys it) and explicitly rewriting it afterward
via `update_artifact_findings` — never by skipping the write, which does nothing against
a DELETE+INSERT that already fired. Audited the full column list: `archived` is also
missing from the INSERT and resets the same way, but it has zero readers/writers
anywhere in the codebase today (grepped, one hit total — its own column definition), so
it's a live no-op, not a current bug. **The general trap remains**: any NEW column added
to `artifacts` in the future needs either a place in this INSERT list, or the same
fetch-before-DELETE/rewrite-after pattern, or it will silently reset on every regenerate.

### Contradiction filter narrowed to testimonial-only, fail-open
`output_critic.drop_findings_contradicted_by_authorised` used to drop any finding whose
description merely *quoted* an authorised string (testimonial/offer/headline/subtext),
on the theory that quoting it proved the finding was re-flagging authorised content.
Live counter-evidence, one ad, one attempt: three TRUE POSITIVES — a leaked unauthorised
comparison label, a missing headline, a missing subtext — all quoted the authorised
headline while reporting something else entirely (an absence, or an unrelated leak), and
all three got silently dropped. Fixed: scope narrowed to testimonial only (the one
CONFIRMED motivating case, ad `1653458269057951`); a finding is dropped now ONLY when
BOTH the category reads as testimonial-shaped AND the authorised quote text itself
appears in the description — default is KEEP, category is model-authored free text so
matching it is treated as fragile by design, not smarter. `offer_text`/`headline`/
`subtext` real false positives are handled instead by telling the critic directly what's
authorised in `CRITIC_SYSTEM` (the OFFER and rule-6 bullets), the same pattern already
used for testimonials/labels — fixing the false positive at the source, not filtering it
after the fact.

### Connection pool — `maxconn=10`; Cloud SQL exhaustion looks like random 500s and blank images
`dedupe.get_conn()` was a bare `psycopg2.connect()` per call, no pooling, connections
never explicitly closed (only `with`-block commit/rollback, which doesn't close a
psycopg2 connection). Now `psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=10)`,
created lazily on first `get_conn()` call (never at import — the Cloud Run Job and the
dashboard both import `dedupe.py` and must not open connections they never use). A
unique key per call (not the pool's own default thread-id key) — the default would hand
a nested same-thread `get_conn()` call the SAME connection and let it get returned to the
pool early while an outer block still held it.

**Cloud SQL `max_connections=25`, 7 held by background workers** — 10 leaves headroom for
both the dashboard and the Job. Confirmed live: exhaustion doesn't look like a clean
error — it looks like **random 500s and pool-card images going blank on a subset of
reloads while the exact same files return 200 in the log and exist on disk**. Root cause
traced two levels deep: `/assets/{filename}` (`dashboard.py`) is a plain `def` route, not
`StaticFiles` — it shares the SAME sync-handler thread pool as every DB-bound route
(`api_artifacts`, etc., also plain `def`). When DB-bound handlers block waiting on an
exhausted connection pool, their threads sit occupied, and asset requests queue behind
them for a thread slot — which specific request stalls on any given reload is
essentially arrival-order, hence apparently random. Not expiry (a SEPARATE, also-real bug
fixed the same day — see the `image_url` refresh-on-refetch note in `dedupe.py`'s
`upsert_scraped_ad`), and not a bug in the image-serving code itself.

### PERSON clause — DISPROVEN live; do not re-attempt prompt-only
Commit `92875cd` added an explicit `PERSON:` row to `_edit_mode_instruction`'s enumerated
partition — REPRODUCE pose/framing/lighting, SUBSTITUTE the person's actual identity —
plus carved the person out of all five "everything else stays exactly" catch-alls, so
the new clause wouldn't be contradicted by them. **Tested live today: it did not work.**
The model still reproduced the real person's likeness from the attached reference photo.
This is the same "prompt-only guardrails do not bind on the image path" pattern already
proven (six times over, see the top note) for testimonials, disclaimers, and the
illustrated-bottle leak — PERSON now joins that list. **Do not re-attempt this as a
prompt-only fix** — writing a tenth sentence has the same failure mode as the ninth.
Next direction, structural not textual: **face → body** — crop or otherwise remove the
face from what's actually attached to Gemini as an image Part, the same class of fix
that worked for the illustrated-bottle leak (drop the photographic reference entirely
rather than ask the model not to look at it). Not built yet.

### Batch degradation is shared run-strip settings, NOT a state leak — verified by full code read, do not go looking for one again
Reported symptom: run one ad, output correct; run 15-20, later ads read like they were
built from the FIRST ad's idea, text repeats across images, copy goes generic. Full read
of `generate_from_selection`/`process_ad` today found **zero** state-leak mechanism:
grepped for mutation of shared dicts (`product`/`messaging_angle`/`blueprint`/
`angle_language`) via item-assignment or `.update()` — zero matches. Zero mutable
default arguments anywhere in `src/`. Zero module-level caches (`lru_cache`, `@cache`,
or a bare `{}`/`[]` accumulator). Every Claude/Gemini API client is instantiated fresh,
inside the function, on every call — none are module-level singletons, none thread
conversation history.

**The actual mechanism is structural, not a bug**: `generate_from_selection` resolves
`product`/`messaging_angle`/`reference_images` ONCE per batch call and passes the SAME
objects into `process_ad` for every `ad_id` in the loop — every creative control
(`angle_id`, `realism`, `body_area`, `offer_text`, `instruction`, `text_in_image`,
`include_product`, `edit_mode`, `retheme_colours`) is singular on this function's own
signature, not per-ad. `run_once` has the identical shape and says so in its own
docstring. Only the reference (blueprint) varies per ad; the angle's `common_phrases`
list, `core_angle`, product facts, and every run-strip toggle are identical for the
whole batch — which, given TIER 1's new headline requirement pulling from one finite
shared phrase list, is sufficient on its own to produce visible convergence/repetition
across a large batch. **Do not go looking for a code-level leak here again** — this was
checked exhaustively and the explanation is the run-strip's own by-design shape, not a
bug in `pipeline.py`.

### `.last_prompt` must be removed before any parallelism
`generate_image`/`edit_image`/`regenerate_from_stored_prompt` (`generate_image_prompt.py`),
`generate_copy_live` (`generate_copy.py`), and `write_creative_description`
(`generate_image_prompt_writer.py`) each stash their assembled prompt onto their OWN
function object as a single shared mutable attribute (`fn.last_prompt = prompt`), read
back by `pipeline.py`/`dashboard.py` immediately after the call that set it, purely for
persistence/logging. `generate_image_prompt.py:131-135`'s own comment already names this
as "the exact kind of hidden coupling" that caused a real past bug (the `text_in_image`
bug). It is safe TODAY only because processing is strictly sequential — no two ads or
requests ever run through the same function concurrently, so the write-then-immediate-
read pattern never crosses call boundaries. **This must be removed (return values
threaded explicitly instead) before any parallelism work starts** — the moment two calls
to the same function can overlap, this becomes a genuine cross-request data leak, not
just an architectural smell.

### OCCLUDE_PERSON — probed 2026-08-13, not viable as built, stays default OFF
`_derive_occlusion_box` keyword-matches `face_present.location`'s free text against a
generous fixed set of position buckets (upper/lower/left/right/centre) - but
`face_present.location` describes where the FACE is, not where the PERSON's whole body
is. On ad `1859386398364761` (`face_present.location` = "Upper-centre of frame, face
angled downward...") it derived `(0, 0, 768, 894)` on a 768x1376 source - full width, top
65% of the frame. That block covers the headline and any logo sitting in the upper
region while leaving the subject's lower body and clothing (the actual identity-bearing
region the whole exercise exists to occlude) fully exposed - the **inverse** of what
Item 1 needed. Confirmed via `scripts/occlusion_probe.py` (throwaway, not in `src/`) -
no Gemini call, no DB write, just the real stored image + the real stored
`face_present` value.

**Root cause**: the blueprint has no person/subject bounding box at all -
`face_present.location` was only ever designed to localise the FACE (for the
face-to-body substitution decision, Item 8), and a keyword-matched box derived from
face-location text has no way to also cover a body that may extend well outside the
region the face itself occupies. There is no segmentation/detection library in this
codebase to derive one directly from pixels either (evaluated and set aside earlier this
session - `opencv-python-headless` alone is a ~60MB wheel for a single detector).

**Next step, not built**: a `subject_bbox` field, populated by `deconstruct.py` itself
(the vision model can plausibly localise "where is the human subject" as its own
question, the same way it already localises `face_present`/`structural_zones`), giving
`_occlude_person_region` a real region to work from instead of inferring one from
face-location keywords. `OCCLUDE_PERSON` stays default OFF until that exists - flipping
it on today would reliably occlude the wrong part of the frame.

## 2026-08-12

**Committed as `085eb16`, 17 files. NOT VERIFIED LIVE.**

### Central finding — revises earlier conclusions about prompt-only rules
Several failures previously written up in this file as "the model does not reliably
obey a text instruction" were in fact **contradictions** — a second instruction, closer
to the point of use, telling the model to do the opposite of an earlier rule. Found
today: `_edit_mode_instruction` said to match the reference's apparent age, directly
overriding rule 10 (`_RULE_10_SUBJECT_AGE`); the PERSON clause said to REPRODUCE pose,
body position, and wardrobe, which is what was actually causing whole-person cloning,
not a rule the model was simply ignoring; `product_count` could request multiple
bottles against rule 7's one-bottle constraint; and the headline/subtext were stated
twice in edit mode (rule 6 plus `_edit_mode_instruction`'s own TEXT branch), three times
with an overlapping structural zone, which is what was producing rendered word doubling
("WOULD WOULD", "MADE FOR FOR") — not a rendering-fidelity issue. **All four fixed by
REMOVAL of the competing instruction, never by adding a counter-clause telling the model
which side to believe.** The standing lesson this revises: before concluding a rule
"does not bind" on the image path, audit for a second, more specific instruction telling
the model to do the opposite — that has now been the actual root cause in four separate
cases this codebase has hit, not model unreliability per se. The distinct, still-valid
cases from earlier sessions (testimonials, the FDA disclaimer, the illustrated-bottle
leak, PERSON likeness cloning from a photo reference) had no such competing instruction
findable on re-audit and remain filed as genuine prompt-only non-compliance — this
finding narrows which failures belong in that bucket, it does not empty it.

### Corrected standing rule — the `:5433` test failures
Both earlier versions of this rule in this file were wrong. `tests/conftest.py:6-9`
forces `DATABASE_URL` to port 5433 for the entire test session. Any test that reaches
`dedupe.get_conn()`, in **any** file, fails with `psycopg2.OperationalError, connection
refused` — this is pre-existing and unrelated to code changes. The invariant is the
**error signature** (`psycopg2.OperationalError`, port 5433, connection refused) —
never a file name, never a count; both prior versions of this rule tried to pin it to
`tests/test_core.py` and to a specific number, and both were wrong the moment a
different DB-backed test file or a growing test count came along. `tests/test_pipeline.py`
is fully mocked and stays a usable, DB-independent signal.

### Critic coverage gaps closed
The output critic is the only mechanism actually enforcing anything on the image path
(see the top guardrails note) — so a rule with no checklist entry is a rule with zero
enforcement, not just weaker enforcement. Rules 8 and 11 had no checklist entry;
`CITED_RULE_IDS` is now derived from the numbered rule functions by introspection
(`_numbered_rule_ids()`) so a newly added rule fails the suite until a matching checklist
entry is added, rather than silently shipping unenforced. `check_draft` previously
attached only the generated draft to the critic's vision call — it now also attaches the
reference image, without which a SUBJECT IDENTITY check would be permanently silent no
matter what the checklist says, since the critic would have nothing to compare the draft
against.

### Transient Anthropic timeouts cost real ads
Two separate runs, two ads lost, same cause: `deconstruct_image` only retries on
`BlueprintValidationError` and JSON parse failures, so a plain network timeout from
Anthropic propagates straight up and the ad fails outright. Highest-value cheap fix
still outstanding — not built today.

### Vertex 429s are bursty, not a fixed ceiling
Two runs of the same code, same ad count: run one hit twelve 429s and took 23m40s for 5
ads; run two hit zero 429s and took 14m34s for 5 ads. Not a quota ceiling being
consistently hit — bursty, unpredictable. The quota question for Usama is still open, as
is Cloud SQL dropping pooled connections mid-run (see the connection-pool note above —
this is a second, separate way pooled connections cause trouble, not the same incident).

### Constraint change from the team — latency is negotiable, quality is not
This reopens several places that were previously capped for speed: retry ceilings
(critic, copy, deconstruct — all currently capped at 2 attempts), a separate focused
vision call dedicated to `production_style` classification, and multi-pass deconstruct.
None of these are built yet — recorded here as the new constraint governing which future
fixes are worth doing, not as work in progress.

### Still open
- `subject_bbox` for occlusion (see the OCCLUDE_PERSON section above — unchanged).
- Oil realism (viscosity/opacity/how it pools — same gap noted 2026-08-07, still open).
- Bottle as a fixed asset (compositing the product cutout — still blocked on Pillow
  compositing work, per the existing Known gaps note).
- Crepey skin looking synthetic.
- `used_headlines` capped at 3 against a fixed 22-45 phrase pool per angle — raising the
  cap makes repetition ACROSS a batch worse, not better, since it lets more ads in the
  same run draw from the same already-small pool; the fix direction is expanding or
  rotating the pool, not raising the cap.
- `.last_prompt` must be removed before any parallelism (unchanged from the section
  above — restated here only because today's work touched adjacent code, not because
  anything about the finding changed).
- A production-safety audit before any deploy: prod is still on revision `00041` from 4
  Aug, and today's `production_style` enum rename plus the seven newly-required
  blueprint fields are breaking changes against rows written under the old schema —
  deploying without an audit risks failing validation on every pre-existing row.

### Repo hygiene
~40 untracked scratch files sitting in the repo root (`chk*.py`, `dump_*.txt`, `sweep.py`,
uvicorn logs, etc.) need either a `.gitignore` entry or a `scratch/` folder to live in —
not done today, just flagged so it doesn't keep accumulating silently.
