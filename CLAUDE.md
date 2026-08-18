# besque-ad-intel — working notes for Claude

## STANDING RULE (2026-08-17): never delete anything without asking first
**Never delete a function, clause, constant, or test without asking first.** If a
refactor makes something unreachable, STOP and report it — do not remove it and
record the loss in a commit message. "A real, documented capability loss" shipped
unasked is not an acceptable thing to ship. This is not a style preference: it is
what caused six confirmed capability losses across `6b82f60`/`a9b1e9f` (the
objects-array refactor), found only by a dedicated audit days later, one of them
already live in a real draft by the time it was found (a duplicate testimonial
rendered in two boxes on the same image).

**Corollary: never delete a test in the same commit as the code it covers.** Delete
the code, let the test fail, then decide what to do about the failure. A failing
test is information — it tells you exactly what behaviour the deletion removed, and
gives you one last chance to notice it mattered before it's too late to ask. A
deleted test is not information; it is silence, and silence is how six capability
losses shipped undetected long enough to need a forensic audit to find them.

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

- **Default test command is `python -m pytest tests/ -q`, scoped to the files a change
  actually touched — never the full suite, unless explicitly asked.** A full run of
  `tests/` takes ~18 minutes and reports 239 failures, every one of them
  `psycopg2.OperationalError` on port 5433 (connection refused), never a real
  regression: this machine's local Postgres 17 service runs on 5432 and is a dev DB,
  not the isolated test DB `tests/conftest.py` forces every test onto via
  `DATABASE_URL`. Running the scoped set for the files actually touched, then reading
  the pass/fail counts, is both faster and the only way a real failure isn't buried
  under 239 expected ones. **Never background a test run** — if the scoped run
  legitimately needs the full suite, run it in the foreground and wait; do not let it
  silently move to a background task the operator has to come back and check later.
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

### 15:13 sweep — five live-draft fixes, all removals/re-scopes, none a new clause

**STANDING DIAGNOSIS: deconstruct is not recording enough for the generation step to
act on.** Three of five items below (competitor branding, competitor product, bottle/
prop composition) trace to the SAME gap: the blueprint schema has no structured
inventory of competitor brand elements (no field distinct from `structural_zones`'
`brand_wordmark` - which only ever means "the ONE zone BESQUE substitutes into," not
"every competitor mark that must be removed"), and `layout_detail.product_count` is a
bare number that conflates "N of the reference's own products" with "N of the same
Besque bottle to render" (see `resolve_product_count`'s own docstring) - there is no
field distinguishing multiple DISTINCT competitor products from repeats of one, and no
field for a product's relative SCALE against its own surrounding props. Rules 9 and 7
therefore have nothing structured to check against beyond what a prompt clause can
catch by asking nicely - and asking nicely is the exact thing this codebase has
repeatedly proven does not reliably bind (see the top guardrails note). Not fixed this
session - recorded so a future schema pass (a `competitor_brand_elements` inventory on
`structural_zones` or its own field, plus a real product-identity/scale record instead
of a bare count) knows why it's needed, not just that it might be nice to have.

**Items 1+2 - competitor branding and competitor product surviving** (a "by THE BODY
FIRM" tagline beneath the substituted BESQUE logo; a competitor's cream jar beside the
substituted Besque bottle - both legal exposure). Rule 9 (`brand_rules`) already banned
both; it was losing to `_edit_mode_instruction`'s own "everything else in the scene...
carries over... exactly" catch-all (five near-identical sites, one per product branch)
and to `opening`'s "the overall structure and which non-person elements appear must
still carry over from the reference" - literally the opposite instruction, stated
CLOSER to the point of use than rule 9. Exactly the PERSON-clause shape from earlier
today (2026-08-10/12), just never extended to this category. Fixed by RE-SCOPING, not
adding a clause: a new shared `_non_carryover_exceptions_clause()` (one definition, five
call sites, so it can't drift the way five independently-typed copies would) excepts
competitor brand marks and competitor product/packaging from every "carries over
exactly" site, plus `opening`'s own two branches now name the same exception inline.
`_competitor_props_clause`'s existing PROP_KEYWORDS mechanism (diagrams/devices/
applicators) has this SAME unfixed gap - not touched this session, flagged as a sibling
case for whoever revisits this.

**Item 3 - rule 9's critic checklist entry passed both violations above.** Strengthened
to name wordmarks, "by X" endorsement lines, and competitor product/packaging
explicitly (was: "a competitor logo, seal, badge, or brand mark" - no product, no
tagline, no endorsement-line language). Added "competitor brand mark or product" to
`HIGH_CONFIDENCE_BY_DEFAULT` - it was reporting this category at all, but never
defaulting it to HIGH, unlike ten sibling categories already in that tuple.

**Item 4 - composition must adapt to the bottle, not the reverse** (a pool float sized
for the reference's squat jar wasn't rescaled for a tall narrow bottle; a second
product the reference showed was dropped rather than accounted for). `layout_detail.
product_count` IS recorded (see the standing diagnosis above for its limits) and
already drives one adaptation - `build_image_prompt`'s own `resolved_product_count > 1`
branch (added earlier the same day) already tells Gemini to resize/rebalance the
LAYOUT around a single bottle rather than reproduce a multi-product count. But
`_edit_mode_instruction`'s photographic-substitute branch said to place the bottle "at
its scale, matching the original shot's composition as faithfully as possible" - i.e.
match the REFERENCE product's own scale, the literal opposite of what's needed - and
its own "everything else carries over exactly" catch-all directly contradicted
`product_clause`'s resize instruction besides. Fixed: that branch no longer says "at
its scale"; it states the bottle's proportions are fixed (deferring to
`_bottle_fixed_clause`) and that a prop/holder/float sized for the reference's own
product is what adapts, never the bottle - plus an explicit "account for every distinct
product, never silently drop one" line. `_non_carryover_exceptions_clause()` gained a
third exception for this same reason.

**Item 5 - season/premise contradiction in copy** ("Show it off this spring" headline
over "Give your skin some love this winter" body copy - the REFERENCE ad's own copy
mixed seasons, and both were inherited verbatim). Fixed mechanically, not with a prompt
request: `generate_copy.validate_copy` now rejects copy naming more than one season
across headline/primary_text/image_subtext (`SEASON_PATTERNS`/`_seasons_mentioned`),
raising into the SAME retry loop `require_cta`/`require_image_subtext` already use.
Scoped to SEASON specifically, not "premise" generally - season names are
keyword-detectable the same way `compliance.py`'s other checks are; a broader tonal or
problem-aware-vs-solution-aware premise clash has no equivalent keyword and is NOT
covered by this check.

**Item 6 - reference background texture carrying over** (a crepey-skin background
reproduced as a coral wrinkled surface; layout barely changed from the reference).
Confirmed by tracing the code, not assumed: the 5-8% variation clause (`opening`, both
`retheme_colours` branches) DOES reach the assembled edit-mode prompt every time -
`_edit_mode_instruction` is unconditionally part of `build_image_prompt`'s edit_mode
branch. Nothing contradicts the 5-8% NUMBER itself. What actually explains both
symptoms: the clause is deliberately subtle by design ("the same way two real
photographs of the same real scene, taken moments apart, are never pixel-identical" -
never meant to look visibly different, so "layout barely changed" may be working as
designed, not a bug), and the palette remap only re-maps HUE ("every hue in the scene...
re-maps to Besque's palette") - nothing tells Gemini to reinterpret a background whose
actual CONTENT is a body-part texture used as a graphic backdrop; recolouring skin
texture to Besque's terracotta/coral palette produces exactly a "coral wrinkled
surface." Not fixed this session (item 6 was scoped as report-only) - if this recurs,
the fix direction is a background-content clause distinct from the colour-only remap,
not a stronger version of the 5-8% number.

### End-of-day findings, 12 Aug

**Verify the server was restarted before believing any draft.** Twice today a draft
was read as a failed fix when the running uvicorn process predated the commit that was
supposed to have fixed it - the same "restart uvicorn after any commit touching src/"
gotcha from earlier sessions, recurring because nothing forces a check. Before treating
any draft as evidence a fix didn't work: check the FIRST timestamp in the running
process's own log against the commit time. If the process started before the commit,
the draft proves nothing about that commit either way.

**Vertex quota is a hard ceiling, not just bursty** - revises the earlier "bursty, not
a fixed ceiling" note from this same day, which was true of the two runs it was based
on but not the whole story. 12 Aug ~15:50: nine consecutive 429s ending in
`RESOURCE_EXHAUSTED` ("check quota"), three ads lost, zero images produced. A 429 on
the image call discards the deconstruct AND copy work already done for that ad, not
just the image step - all of it has to be redone from scratch on any retry, and the
critic's own retry loop doubles the image calls per ad, making the ceiling easier to
hit, not harder. Needs, in order: a quota raise from Usama (the actual limit, not a
code workaround); a long backoff specifically on `RESOURCE_EXHAUSTED` (distinct from
ordinary transient-error backoff - this one means "stop entirely for a while", not
"retry soon"); failed ads marked retryable with the blueprint kept, so a quota-caused
failure doesn't throw away deconstruct's work; and deliberate spacing between image
calls so a batch doesn't itself trigger the ceiling. None of this is built yet.

**Every 12 Aug commit is marked NOT VERIFIED LIVE**: `085eb16`, `a0cc74d`, `d9a308e`,
`a228539`. First action next session is a verification run - via Generate on a
never-drafted ad, never Regenerate (see the standing rule on this) - after a CONFIRMED
restart (see the point above).

**Still untouched from Sayali's doc**: batch scalability, and crepey skin reading as
synthetic. Not investigated this session, not forgotten either - recorded here so they
don't silently drop off the list.

**Production safety audit not done.** Prod is still on revision `00041` from 4 Aug. The
`production_style` enum rename and the seven newly-required blueprint fields (see the
4 Aug / 6 Aug notes) are breaking changes against rows written under the old schema.
Do not deploy before auditing what a fresh validation pass does to existing rows.

**Run tests as `python -m pytest`, never bare `pytest`.** Bare `pytest` omits the repo
root from `sys.path` and fails collection with `ModuleNotFoundError: No module named
'src'` - this LOOKS like a real failure (a red run, an error message naming a missing
module) but isn't one; it's an invocation mistake. Always `./venv/Scripts/python.exe -m
pytest ...` (or `python -m pytest` with the venv active), never a bare `pytest`
invocation, on this codebase.

**`_illustrated_elements_clause` is wired but INERT and UNVERIFIED.** Added to
`generate_image_prompt.py` and threaded into all three `build_image_prompt` branches,
but nothing populates `blueprint.illustrated_elements` anywhere yet - no schema field,
no deconstruct.py extraction - so the clause reads a key that doesn't exist on any real
blueprint today and always returns `""` in practice. Open questions to resolve before
finishing it, not yet answered: whether the literal string `"illustrated"` this clause
gates on still matches the CURRENT `production_style.style` enum (renamed at some
point this session - confirm the enum's real values before trusting the string
comparison); whether it overlaps with `_scene_elements_clause` and
`_competitor_props_clause` on the same drawn object (three mechanisms that could all
have an opinion about the same illustrated prop, with no stated precedence between
them); what clause(s) run AFTER it in each of the three branches, in case one of them
also touches the same drawn elements; and whether its own example substitutes (a
citrus slice, a flower) are themselves an implied ingredient claim - the same class of
compliance risk (C3, unsubstantiated product facts) the clause's own docstring says it
was written to avoid. Do not consider this feature done until these are checked.
**Superseded 2026-08-13** - see `depicts_competitor_category` below, which replaced this
field entirely rather than answering its open questions.

## 2026-08-13

**SESSION 13 AUG 2026 - pushed: `426601b`, `754bf17`, `747706d`, `ede39ad`, `53501bd`,
`0417b93` (plus `d57134c` from 12 Aug, landed in this same push). ALL NOT VERIFIED LIVE**
- every commit message says so explicitly; no Generate run confirmed any of this against
a real ad this session. First action next session is that verification run, per the
standing rule below.

### Landed

**Transient Anthropic retry** (`754bf17`) - the "Transient Anthropic timeouts cost real
ads" gap from 12 Aug is built: `_call_claude_with_transient_retry` wraps ONLY the raw
`client.messages.create` call (never the parse/validation retry loop, a different
failure class) with exponential backoff (2/4/8s), up to 4 attempts, gated by
`_is_transient_anthropic_error` (connection/timeout/429/5xx - never a 4xx, which is a
different request, not a retry). `client(max_retries=0)` so this is the ONE mechanism
that owns retries, and every retry is logged with the exception's own class name.

**Small-scale label legibility** (`754bf17`) - a real gap in `a228539`'s wrap-fidelity
sentence ("legible, without warping") covered geometry but not information density: a
label rendered small (a thumbnail-scale product shot) still tried to fit the full
wordmark + certifications + fine print, none of it actually legible at that pixel count.
New instruction: at small scale, simplify to the BESQUE wordmark and product name
MINIMUM, dropping what won't render legibly - reconciled against `_bottle_fixed_clause`
so the two don't disagree about what's allowed to change.

**`depicts_competitor_category` replaces `illustrated_elements`** (`754bf17` schema
change, `747706d` classifier fix) - closes the INERT gap noted at the end of the 12 Aug
section above, but as a different field on `scene_elements`, not a fix to the old one.
Required per-entry (`schema/blueprint.schema.json`), tolerant on read (a pre-migration
row without it degrades to old behaviour, never a validation failure). `747706d` then
corrected the classification CRITERION itself, same day: was "is this the competitor's
own product category" (steak/spoon/hair-strand - literal category props only); now "does
this exist to make the competitor's argument" - a metaphor or diagram prop counts too
(a chain-and-padlock illustrating "locked fat" the product claims to unlock). Live
evidence for both directions of the old criterion being wrong: a chain-and-padlock
under-flagged, a distressed 3D character over-flagged. Human figures/faces/body parts
are EXCLUDED unconditionally, even when central to the argument or the metaphor itself -
that's the person-substitution path's job (`face_present`/PERSON), never this field's,
with no exception. The substitution clause is now ungated from `style=="illustrated"` -
fires in ANY register (a photographic chain-and-padlock needs substituting exactly as
much as a drawn one); only the DRAWING instruction (native-style vs. photorealistic)
stays register-conditional. Renamed to "COMPETITOR ELEMENTS TO SUBSTITUTE." Reconciled
against `_edit_mode_instruction`'s own carry-over language via a 4th exception in
`_non_carryover_exceptions_clause` - that catch-all was overriding the substitution the
same way it already had to be fixed for PERSON/competitor-brand-marks/product-count.
**Coverage gap, not yet closed**: this only affects blueprints DECONSTRUCTED after
`754bf17` - the field is populated at deconstruct time, never backfilled, so the ~77
existing artifact rows deconstructed before this commit still have no
`depicts_competitor_category` on their `scene_elements` entries at all, and will keep
cloning the competitor's own argument-props on every Regenerate until re-deconstructed.

**Rule 10 (subject age) rewritten** (`ede39ad`) - grey/silver hair, visible facial lines,
and mature skin texture are now each individually REQUIRED as a primary spec, with the
45-60 age bracket demoted to a secondary anchor rather than the sole criterion; states
explicitly that this is independent of hair COLOUR or texture specifically (a young-
reading subject with grey-dyed hair was passing the old wording). `high_spec`
`STYLE_GUIDANCE` reworded to drop youth-skewing editorial-campaign framing that was
fighting this rule from a different angle. A hardcoded `ad_id` found embedded in rule
10's own prompt text was removed - the exact class of leak "No `ad_id`... in `src/`"
already bans, just not caught until today.

**Bottle identity and integration clauses** (`ede39ad`) - new `_bottle_identity_clause`,
fed structurally from `product.visual_description`/`substance_colour`/`certifications`
(verified by a swapped-product test - change the product, the clause's stated facts
change with it, never a hardcoded description), STRICT-weighted immediately after
`brand_rules()` in all three `build_image_prompt` branches. New
`_bottle_integration_clause` requires the bottle read as a participating object (held/
applied/resting, hand-scale, contact shadow, grip mechanics), with an explicit override
for a reference that shows a floating packshot - this is the SAME clause my own later
work this session (see Item 3 below) found in contradiction with `_bottle_register_clause`
and reworded, not a separate finding.

**Colour-neutral scene elements** (`ede39ad`) - fixed at deconstruct: `scene_elements`
noun phrases must not encode colour (e.g. "wooden shelf," never "dark walnut shelf"), so
the retheme-colours instruction downstream has nothing left to contradict. Same shape as
every other "fix the contradiction, don't add a rule to arbitrate it" finding this
codebase keeps hitting.

**C8 - unsubstantiated ingredient/formulation claims** (`ede39ad`) - scoped explicitly
against C3's own "improves skin texture"/"deeply hydrating" exception so the two
categories cannot overlap by construction; mechanical backstop in `compliance.py`;
deliberately NOT added to `HIGH_CONFIDENCE_BY_DEFAULT` in the critic (the live evidence
was confirmed in generated COPY, now mechanically blocked there - not a confirmed
IMAGE-only escape). This is the precedent Item 2 below explicitly followed for C9.

**Copy duplication, borrowed attribution, and the lighting/integration contradiction**
(`53501bd`) - three items, one session, root-caused and fixed together:
- Item 1: `_text_purpose_clause` and `_text_zone_copy_clause` were two independent
  blueprint-derived clauses that could each independently commission NEW Besque copy for
  the SAME underlying reference text block (one by FUNCTION via `text_purpose`, one by
  ZONE via `structural_zones`) - live evidence, a draft rendered the same closing
  statement twice, in different wording. Fixed via `_dedupe_text_purpose_against_zones`:
  a `text_purpose` entry whose `placement` matches a `structural_zones.position` already
  covered by the zone-copy clause is dropped - a MECHANICAL position-string match, the
  same "position string, verbatim" contract `_text_zone_copy_clause` already relied on,
  never a guess at semantic similarity.
- Item 2: neither clause banned reusing the reference's own sentence structure/phrasing/
  nouns - both now do. Separately and more importantly: `_redact_personal_attribution`
  (regex, shape-based - "Firstname L.", "attributed to X", an em-dash signature, or
  `testimonial_zones`'s own `"attribution"` JSON key) strips a personal name from
  reference-derived text BEFORE it reaches a copy prompt at all, applied to the raw
  blueprint dump and to `text_verbatim`/`detail` individually. New rule **C9 (borrowed
  personal attribution)** plus a mechanical backstop
  (`compliance.check_borrowed_personal_attribution`, always-on) catches anything that
  slips through on the output side. Moving `select_testimonial_review` earlier in
  `process_ad` to thread its attribution into the compliance check was TRIED and
  REVERTED - it made a DB read reachable from paths that previously returned "failed"
  before ever needing it, breaking 72 `test_pipeline.py` tests; the default `""`
  (nothing authorized) turned out to be correct anyway, since the real testimonial never
  flows into copy generation to begin with.
- Item 3: `_bottle_register_clause` anchored the bottle's shadow/grounding to the
  reference's own observed `scene_lighting` facts and demanded an EXACT match, while
  `_bottle_integration_clause` (`ede39ad`, same day) mandates a contact/grip shadow the
  reference may never have shown (a floating packshot has no contact point to observe a
  shadow from at all) - a genuine contradiction, not model unreliability. Reworded, not
  stacked: the reference's facts now inform the SCENE's character (direction/hardness/
  colour-temp/grain); contact/grip shadow and grounding are explicitly deferred to
  `_bottle_integration_clause`'s actual composition. Also gated on `style` -
  `style=="illustrated"` now skips `scene_lighting` entirely, fixing a live "Not
  applicable - no photographic lighting" leak (deconstruct.py doesn't leave photographic-
  only fields blank for an illustrated reference; it writes a value like that, which the
  old code read as a real observed fact and asserted verbatim). Explicitly did NOT touch
  bottle identity or the material realism clause. Handles and avatars NOT yet covered as
  of this commit - see the next one.

**Borrowed account identity in UGC chrome** (`0417b93`) - same day, later, extending C9
rather than adding a C10 per explicit instruction: live evidence, a competitor's real
Instagram handle (`@fitness_ty`) survived verbatim, with no surname-initial, em-dash, or
attribution verb, so none of C9's existing shape-patterns caught it.
`PERSONAL_NAME_ATTRIBUTION_PATTERN` extended to `@handle`-shaped tokens (shared between
`compliance.py`'s mechanical check and `generate_copy.py`'s input-side redaction, so both
recognise the identical shape). **Documented, tested residual gap**: a BARE handle with
no `@` is shape-indistinguishable from an ordinary compound word - not matched, on
purpose, to avoid constant false-positives; the account-chrome fix below covers the case
this regex structurally cannot (a handle rendered as UI chrome, never as copy text). The
actual leak vector was `_structural_zones_clause`'s testimonial-card styling instruction
("match this reference's own styling for the card") - it said nothing about WHOSE account
the card belongs to, so it read as license to reproduce the avatar and handle as
"styling." Carve-out added at that exact point of use (the "closer wins" lesson from 12
Aug, applied again): chrome LAYOUT may be reproduced, chrome CONTENT (avatar, handle,
display name) must be Besque's own or removed. Separately answered a standing question:
does a face inside reproduced account chrome get C1/rule 10? Textually yes (both already
say "any person"/"any human subject"), but nothing said so EXPLICITLY, leaving room for
the nearby, more specific card-styling instruction to win by default - the same
competing-instruction shape as every other "prompt-only doesn't bind" finding this file
already tracks. Fixed by naming avatar/profile-picture faces explicitly in the PERSON
clause and in the critic's SUBJECT AGE VIOLATION and SUBJECT IDENTITY checklist entries.
C9's account-chrome half has NO mechanical backstop, unlike its name/handle-in-text half
- same as C1, since it's pixels, not a string a regex can scan.

### Standing rules learned today

- **Claude Code's own end-of-session state reports are not authoritative - verify
  against `git log` and `git status`.** `d57134c` was reported as uncommitted at one
  session's end when it had already been pushed. Don't trust a self-report of "this is
  committed" or "this is still pending" without checking.
- **Run tests as `python -m pytest`, never bare `pytest`** - reconfirmed today, see the
  12 Aug section above for why (bare `pytest` drops the repo root from `sys.path`).
- **A PowerShell window started with `uvicorn ... *>` (redirecting all streams) shows
  nothing on screen, and closing that window kills the server** - it looks idle/dead
  from the terminal alone either way. Verify the server is actually up via
  `Get-NetTCPConnection -LocalPort 8000 -State Listen`, and verify it's running code from
  AFTER your last commit by checking the log file's `LastWriteTime` against the commit
  timestamp - the same "confirm a restart happened" discipline the 12 Aug section already
  established, now with the actual PowerShell commands to do it.
- **PowerShell 5.1 has no `utf8NoBOM` encoding** - `Out-File -Encoding utf8` in 5.1 still
  writes a BOM (see this file's own PowerShell tool notes on this); anything reading the
  file downstream as strict UTF-8 without BOM tolerance will choke on it. No workaround
  recorded yet beyond knowing to check for it.
- **Where things actually live, restated because it was asked today**: a blueprint is
  `artifacts.blueprint` (`dedupe.save_artifact`'s own column), not anything on `seen_ads`
  - `seen_ads` is ONLY a dedupe ledger (see "Two dedup gates" above), it carries no
  blueprint content at all. The DB connection helper is `get_conn()` in `src/dedupe.py`
  (`dedupe.py:94`), the one thing every DB-backed module and test ultimately calls
  through.

### Open, not built

- **The unadaptable-reference gate, and the staged-progression detector that should
  feed it.** Not built this session - recommended shape, for whoever picks this up: a
  deconstruct-time `argument_adaptable` boolean plus `unadaptable_reason` (same pattern
  as `content_safety.hard_block_reason` - a stated reason, never a bare flag), and
  `mark_seen` on a gated-out ad so a future run doesn't burn a fresh (paid) deconstruct
  on the same unadaptable reference every time it's re-scraped.
- **Production safety audit still outstanding, and the count has grown again.** Prod is
  still on revision `00041` from 4 Aug. The 12 Aug section already flagged seven newly-
  required blueprint fields plus the `production_style` enum rename as breaking changes
  against pre-existing rows; `747706d`'s `depicts_competitor_category` adds an EIGHTH
  required field today, on the same schema, with the same problem - do not deploy without
  auditing what a fresh validation pass does to every row written before whichever of
  these commits actually shipped.
- **Vertex quota raise still not done.** Same ask as the 12 Aug section (a real quota
  increase from Usama, not a code workaround) - still open, still blocking nothing today
  only because nothing ran live against it this session.

### Also recorded today

- **`angle_id` was NULL on artifacts 1240-1245**, which is why their copy read as stock
  filler instead of angle-specific language (`generate_copy`'s `angle_language` clause
  has nothing to write from when no angle was selected for the run - see the 2026-08-10
  section above). This is OPERATOR BEHAVIOUR, not a bug - the run that produced these
  ads simply didn't have an angle selected. Action item, not a code fix: `/pool` should
  surface an unset angle to the operator BEFORE generating, so this is a deliberate
  choice rather than a silent default.

## 2026-08-14 — Dynamic Edit System (Steps 1-4) + a generation-side bug found while calibrating it

### GENERATION-SIDE BUG, found live: `element_provenance.product="added"` does not mean a product was rendered
Artifact 1250 (`ad_id=1015875454522971`): `layout_detail.product_count=0` (the
COMPETITOR reference genuinely had no product in frame), `include_product=True`,
`element_provenance.product="added"` (the pipeline's own record that a product was
supposed to be added to the scene) - but the rendered draft has **no bottle anywhere**,
confirmed by direct visual inspection of the full frame including the bottom banner
region. Compare artifact 1249 (`product_count=2`, `element_provenance.product=
"substituted"`), where the bottle genuinely is in the pixels - substitution (replacing
an already-existing structural zone) appears to reliably render; addition (inserting a
product into a scene that never had one) does not, at least in this case, despite the
pipeline recording success either way.

**This is a generation-pipeline bug (`process_ad`'s provenance bookkeeping /
`generate_image`'s add-product path), not an Edit System bug** - `element_provenance`
is written at generation time, long before any edit exists. Not fixed here - needs
someone to look at why the "added" path silently produced no bottle while reporting
success. The Edit System's own `_product_control` (below) now refuses to trust
`element_provenance.product=="added"` at all as a consequence, but that's a
containment measure on the edit side, not a fix to the underlying render failure.

### Dynamic Edit System - Steps 1-4 complete, on branch `feat/dynamic-edit-system`
Schema (parent/root/version_no/edit_event_id lineage + `edit_events` table),
`src/edit_capability.py` (dynamic control derivation, fail-closed), the targeted-edit
engine (`POST /artifact/{id}/edit`, delta-instruction-only, brand-wordmark protection),
Part B's Edit modal (renders whatever `/edit-capabilities` returns, version strip with
revert), and Step 4's drift check (`src/drift_check.py`) are all built and committed to
`feat/dynamic-edit-system` (not `main`, not merged).

**Part A verified live**: artifact 1251→1252, a one-word headline edit, measured
0.076% pixel change outside the headline zone vs 11.898% inside - the "v1 image is the
baseline, delta instruction only" design holds.

**Two real bugs found and fixed during live verification, not just designed against**:
`save_artifact`'s own (ad_id, angle_id) dedupe-skip gate silently discarded every
edit-created row (`insert_edit_artifact` now goes through `insert_artifact_row_
unconditional`, bypassing that gate entirely - an edit must always create a new row
regardless of how many rows already share that ad_id); and the headline/subtext
controls were gated on "any `text_purpose` entry exists" rather than a genuinely
headline-shaped one, which on artifact 1251 wrongly counted the COMPETITOR's own
`purpose="other"` wordmark/tagline text as headline structure, even though no headline
was ever rendered into that artifact's pixels at all.

**Drift check, two methods**: ZONE (headline/subtext/cta/person_face/badge, when a
position is recorded) compares % changed inside vs outside that region -
`DRIFT_OUTSIDE_ZONE_THRESHOLD_PCT=1.0`, ~13x Part A's measured 0.076%. CONTAINMENT
(product/prop/person_body/offer/banner, or any zone-target lacking a recorded
position) - only lighting/background/typography still skip outright as genuine
whole-frame effects. Containment does 4-connected-component labelling over the
changed-pixel mask and flags drift when too much of the changed-pixel mass sits
outside the single largest component.

**Containment threshold calibrated against two real edits** (product reposition/
rescale, artifact 1249→1253; prop removal, artifact 1250→1254 - both confirmed
visually clean). Raw (no size filter): 28.33% and 17.10% - already past a first
15%-estimate on edits with nothing wrong. Diagnosed why: 17,814 and 3,775 connected
components respectively, the overwhelming majority 1-4px specks - ambient
regeneration noise on textured surfaces (water sparkle/caustics), not real secondary
edits. Added `MIN_COMPONENT_SIZE_PX=50` (components below this are dropped before
scatter_pct is computed, excluded from both numerator and denominator) - the SAME two
clean edits then measure 9.36% and 2.81%. `CONTAINMENT_SCATTER_THRESHOLD_PCT` set to
25.0 from those numbers - only ~2.7x headroom (deliberately less than the zone
threshold's ~13x), because these two containment edits have real, legitimate secondary
structure just outside the main blob (re-rendered label sub-regions on the moved
bottle, water distortion at the edited object's edge) that Part A's near-perfect
headline edit didn't have - the "clean" baseline is inherently noisier for this method.

**Product control now requires agreement between two signals, fails closed
otherwise**: `layout_detail.product_count > 0 AND element_provenance.product ==
"substituted"`. `"added"` is never trusted alone - see the generation-side bug above
for exactly why. `include_product` (operator intent for the run) is deliberately not
part of the predicate either - 1250 shows intent and provenance can both agree with
each other while still being wrong about what's in the pixels.

**ADC note**: mid-session, the live product-edit calibration call failed with
`google.auth.exceptions.RefreshError: Reauthentication is needed` - expired
Application Default Credentials, exactly the class of failure this file's own
"Operational gotchas" section already names. Re-running `gcloud auth application-
default login` and restarting uvicorn resolved it; not a code bug.

### OPEN, known weakness: `reference_has_product()` is default-TRUE on absent data, not fail-closed
`src/generate_image_prompt.py:87` - `return not (product_category_bp == "not_product" or
layout_detail_bp.get("product_count") == 0)`. This returns True unless the blueprint
EXPLICITLY says `product_count == 0` or `category == "not_product"` - a blueprint that
simply has no `layout_detail` at all (missing data) reads exactly the same as one that
positively confirms a product in frame. That is the opposite of the fail-closed
principle `edit_capability._product_control` uses for the SAME question on the edit
path - that predicate requires TWO positive, confirmed signals in agreement
(`layout_detail.product_count > 0 AND element_provenance.product == "substituted"`)
before trusting that a product is present, and withholds the control otherwise.
`reference_has_product` instead assumes presence by default and only backs off on
explicit negative evidence, which is a materially weaker standard for a value that
`_edit_mode_instruction`/`build_image_prompt` use to decide SUBSTITUTE vs. ADD (and, as
of the 2026-08-14 double-product fix above, whether to suppress product_clause's
placement sentence) on the GENERATION path. Not changed in this session - flagged as a
known weakness, not fixed. A future fix direction would mirror `_product_control`'s
own shape: require positive confirmation (a real `product_count`) rather than treating
"no data either way" as "yes."

## 2026-08-16 — bottle-realism edit control, real product-zone drift check, fixed
bottle geometry, deconstruct-time shape stripping

**Committed on `feat/dynamic-edit-system`: `5bcc118`, `f69a787`, `25c3a08`** (still
unmerged/undeployed, continuing directly from the 13-14 Aug Dynamic Edit System work
above).

### Bottle-realism-only edit control (`5bcc118`), superseding the 2026-08-15 version
The 2026-08-15 product-realism control (`build_product_realism_edit_instruction`)
dynamically ASSEMBLED its instruction from `target_style` + blueprint (wordmark
protection, preservation list) and attached the product's own reference photos -
exactly the "construct the delta from field text" shape this rebuild replaces.

`src/realism_deltas.py` (new) now holds ONE pre-authored delta sentence per FIXED value
- `ugc_native`, `high_spec`, `hybrid`, `illustrated` (`REALISM_VALUES`, `REALISM_DELTAS`)
- independent of whatever `production_style.style` the schema currently allows or an
older row still carries. Each delta names the bottle's render register only, states
the label's content/wording/icons/proportions/position unchanged, states everything
else in the image unchanged, and never restates label colour/typeface/wordmark -
identity is `products.visual_description`'s job. `build_product_realism_edit_instruction`
was deleted entirely, not left as dead code.

`edit_capability._product_realism_control`'s descriptor now carries `options` straight
from `realism_deltas.REALISM_VALUES`, so the modal and the delta text can never drift
apart; `current_value` stays exactly as stored (e.g. a pre-rename `"ugc"` or `"hybrid"`),
never coerced to `options[0]`.

`POST /artifact/{id}/edit`'s realism branch now sends ONLY the v1 draft image plus the
exact pre-authored sentence - no reference photos, no blueprint fields, no stored
prompt. Unknown values are rejected with a 400 naming `REALISM_VALUES`.

Modal (`templates/dashboard.html`): Product — Realism renders as a segmented picker
over the four options (explicit Apply button; selecting a segment never fires the edit
by itself); a stored value matching none of the four shows a `"current: <value>"` chip
instead of silently preselecting the first option. Product (placement) renders as
static read-only text - no textarea, no Apply button; no UI path edits product
identity.

Tests (`tests/test_realism_deltas.py`): no delta contains BESQUE/MAGIC/maroon/
terracotta/serif/sans-serif/vegan/cruelty/cylindrical/collar/pump; every delta states
the unchanged-elsewhere clause; applying a realism change creates a new artifact row
without mutating v1; an unknown stored value's `current_value` stays outside `options`
rather than being coerced to `options[0]` (the exact contract the chip-vs-preselect UI
decision reads).

### Real product-zone drift check, `drift_method` audit trail, server-side placement block (`f69a787`)
Found live: on a realism apply, `target=product` had no recorded zone position, so
`drift_check.check_drift` always fell back to CONTAINMENT - "is the changed region one
coherent blob," never "did it land on the right part of the frame." A single
contiguous edit relocated entirely to the wrong region of the image passed that check
outright, since one blob is one blob regardless of where it sits.

Fixed: `drift_check._product_zone_position` reads `layout_detail.zone_positions` - a
real, already-populated deconstruct-time field (e.g. `"product mid-frame"`) - for a
phrase naming "product" or "bottle", parsed into a bbox via the SAME
`_parse_position_to_bbox` every other zone target already uses. When present, `product`
edits now use the ZONE method (change inside vs. outside that region, threshold 1.0%);
falls back to containment only when `zone_positions` has nothing product-shaped
recorded - never a fabricated zone.

`edit_events.drift_method` (new column, `CREATE` + explicit `ALTER TABLE ADD COLUMN IF
NOT EXISTS` - the established self-migration pattern) records which method actually
ran - `"zone"`/`"containment"`/`"skip"` - on EVERY apply, not just a drifted one, so a
containment fallback is visible on the row itself rather than indistinguishable from a
real zone pass just because both happened to report `drift_flag=false`.

`POST /artifact/{id}/edit` now rejects `target=product, attribute=placement`
unconditionally with a 400, checked BEFORE `edit_capability.find_control` so it's
rejected even on an artifact where the control wouldn't otherwise be derived at all.
The modal already rendered this field read-only, but that was a UI-only guarantee - a
direct API call is now blocked server-side too.

### Fixed bottle geometry clause + deconstruct-time shape-language stripping (`25c3a08`)
`generate_image_prompt._bottle_geometry_clause()` is a single hardcoded constant - no
arguments, no blueprint/artifact/DB reads - stating the Besque bottle's actual
proportions in numbers (4.33x total-height-to-body-width; a 2.85-body-width
straight-sided cylinder body; a 0.21-body-width shoulder; a 0.75x0.63-body-width gold
collar; a 0.43-body-width pump stem; a 0.38-body-width lever overhang). Composed into
all three `build_image_prompt` branches (edit-mode, writer, template), gated on
`effective_include_product` same as its `_bottle_identity_clause`/`_bottle_integration_
clause` siblings. Never composed into the realism-only targeted edit path
(`src/realism_deltas.py`), which sends only its own pre-authored delta - the two
systems' geometry handling is now deliberately disjoint, not merely non-overlapping by
accident.

Every other place that used to describe bottle shape/proportions in its OWN words was
folded to defer to this one clause instead of re-stating it - the "second, differently-
worded geometry statement nearer the point of use" contradiction shape this codebase
keeps re-finding (see the 12 Aug central finding above): `_bottle_geometry_source_clause`
no longer enumerates categories (silhouette, height-to-width ratio, neck/shoulder/base,
pump/collar hardware, label shape) - it names nothing itself and points at the fixed
clause; `_edit_mode_instruction`'s illustrated substitute/ADD branches no longer list
"proportions" among what a reference photo may be used to "confirm" (shape is fixed
regardless of whether a photo is attached, so nothing derives it from one); the
"work from silhouette, colour, and the label name alone" no-photo fallback dropped
"silhouette" for the same reason.

**Deconstruct-time filter** (`src/deconstruct.py`, `strip_bottle_shape_language`):
scrubs bottle/container geometry vocabulary from exactly the THREE blueprint fields
traced to actually reach assembled prompt text as free text - `product_category.
signals[]` and `visual.subject` (both quoted verbatim by `_competitor_props_clause`
when they also match a `PROP_KEYWORD`) and `layout_detail.zone_positions[]` (folded into
`_scene_composition_facts`' placement sentence, and now also the drift-check zone
above). A matching list entry is dropped WHOLE (never partially word-scrubbed, which
risks a grammatically broken fragment); `visual.subject` is blanked to `""` rather than
the key removed (still schema-valid - a required string, not a required non-empty
one). Wired into `deconstruct_from_response`, applied AFTER schema validation
(narrowing a list or blanking a string never invalidates an already-valid blueprint),
logged by field name whenever anything is actually dropped. `scene_elements` is
deliberately NOT filtered - its own classifier instruction already excludes the product
entirely ("every element OTHER THAN the product"), and blanket-filtering it would strip
legitimate prop detail (a "round tray", a "curved mirror") with no traced benefit.

**Product cutout**: `generate_image()` now fetches `product_assets/
besque_magic_body_oil_cutout.png` from the asset bucket and attaches it as an EXTRA
reference Part on every non-illustrated, `include_product=True` generate call -
alongside the product's own configured reference photos, gated the same way
(`include_product`) plus a style check mirroring `build_image_prompt`'s own
operator-realism-else-observed-style precedence. Illustrated is excluded for the same
reason every other photographic reference is withheld there. Result is cached
process-wide (fetched once, success or failure, per process) so a slow GCS/ADC issue
costs one hit, never one per generation call - measured live at ~4-5s per attempt
uncached, which would otherwise have doubled `test_edit_mode.py`'s own runtime.
**Uploaded to `gs://besque-ad-intel-assets/product_assets/besque_magic_body_oil_
cutout.png`** the same night, after ADC was refreshed (it was expired earlier in the
session - `RefreshError`, same class of failure this file's own "Operational
gotchas" section already names) - confirmed via `gsutil ls -l` (529384 bytes, matching
the local file exactly) and a direct `_fetch_product_cutout_bytes()` call returning
real PNG bytes, never `None`.

Tests: `tests/test_bottle_geometry.py` (new) - the clause is byte-identical across
every realism value/scene type/product count and absent when `include_product=False`;
no deleted competing phrase survives in any of the three branches; the realism-edit
deltas contain neither the clause nor its distinctive numeric facts; a blueprint
carrying shape language in the three filtered fields is proven clean end-to-end
(`deconstruct.strip_bottle_shape_language` → `build_image_prompt`) while arrangement-
only content survives untouched; the strip function is a no-op on already-clean input
and never mutates the caller's original blueprint dict.

### Standing rule added this session
**Default test command is `python -m pytest tests/ -q`, scoped to the files a change
actually touched - never the full suite unless explicitly asked.** A full `tests/` run
takes ~18 minutes and reports 239 failures, every one of them `psycopg2.
OperationalError` on port 5433 (connection refused), never a real regression - this
machine's local Postgres 17 service runs on 5432 and is a dev DB, not the isolated test
DB `tests/conftest.py` forces every test onto. Never background a test run - if a
scoped run legitimately needs the full suite, run it in the foreground and wait.

## 2026-08-17 — objects-array refactor + compliance-aware text substitution restored
**Two commits, same branch (`feat/dynamic-edit-system`), same day: `6b82f60` (the
refactor itself) then `a9b1e9f` (this session, restoring what the refactor dropped).
NEITHER IS VERIFIED LIVE** - no Gemini/Claude call and no running dashboard was
exercised for either commit. **The first live test owed on this branch is a Generate
(never Regenerate - see the standing rule on this elsewhere in this file) on the OSEA
reference set ad.** Do not deploy before that verification run.

### The objects inventory is now the ONLY per-element blueprint field
`6b82f60` replaced FIVE separate top-level blueprint fields - `scene_elements`,
`structural_zones`, `typography_zones`, `testimonial_zones`, and `text_purpose` (the
array) - with a single required `objects[]` array on the schema
(`schema/blueprint.schema.json`). Each entry carries `object_id`/`kind`/`bbox`/
`ownership`/`role`/`carries_brand_mark`/`persuasive_function`/`disposition`, and (this
session, `a9b1e9f`) a `text_purpose` string required whenever `kind=="text"`.

**This is a BREAKING SCHEMA CHANGE, not an additive one.** Every artifact row
deconstructed before `6b82f60` has none of the five old fields' replacement and no
`objects` key at all - roughly 300 existing rows at the time of the refactor, never
backfilled or migrated (a deliberate choice, not an oversight - see `6b82f60`'s own
commit message). These rows are NOT invalid-and-broken: `edit_capability.
legacy_scene_summary`/`is_legacy` and `generate_image_prompt.build_image_prompt` both
detect the missing `objects` key and degrade to a **read-only** summary of the old
fields (dashboard/edit-modal display only) rather than raising. `validator.
validation_error` correctly reports these rows as failing the CURRENT schema - that's
expected, not a bug to "fix" by loosening the schema back down. A pre-refactor row can
be viewed and its draft regenerated via Regenerate (which rebuilds the prompt from
scratch, per the 2026-08-06 fix elsewhere in this file), but its blueprint itself will
never gain a real `objects` array without a fresh Generate against the source ad.

### `resolve_disposition` is the ONLY thing that decides an object's fate
`src/deconstruct.py`'s `resolve_disposition(obj, context=None)` is now the single
mechanical authority over what happens to every object in a blueprint - never the
model's own prompted guess, never a second uncoordinated clause in
`generate_image_prompt.py` with its own opinion. Three previously-separate concerns are
now resolved here, in one place:
- **Ownership**: `competitor_branded`/`carries_brand_mark` can never resolve to "keep"
  regardless of `kind` or `text_purpose` - a product-kind object substitutes, every
  other kind drops.
- **`text_purpose`** (added this session, `a9b1e9f`, rules read out of git history -
  see below): for `kind=="text"`, `award`/`disclaimer` always drop; `headline`/
  `subtext`/`cta`/`product_callout` always substitute; `offer`/`price_anchor`/
  `certification`/`testimonial` substitute ONLY when this run's `context` actually
  supplies a matching value (`offer_text`/`certifications`/`testimonial`), else drop -
  never left for Gemini to invent a number, cert, or quote it wasn't given. `other` (or
  no `text_purpose` at all, e.g. a legacy row) passes the model's own guess through
  unchanged UNLESS the object is branded, in which case ownership still wins.
- **Competitor argument props**: folded in from the now-deleted
  `generate_image_prompt._competitor_props_clause` - a prop matching
  `_is_competitor_argument_prop`'s keyword set (diagram/applicator/device/etc.) drops
  even when its `ownership` reads as "generic," because it exists only to make the
  competitor's own argument.

**Why one mechanism, not three**: before this session, `_competitor_props_clause`
(image-prompt-side, keyword matching on free text) and `resolve_disposition`
(ownership-only) could in principle disagree about the same object's fate with no
coordination between them - exactly the two-independent-systems contradiction shape
this file has hit repeatedly (see the 12 Aug section's "STANDING DIAGNOSIS"). Folding
prop-keyword matching into `resolve_disposition` and repointing `text_purpose` through
the same function closes that gap structurally, not by adding a precedence sentence.

### The dual-resolution design - READ THIS BEFORE ASSUMING A STORED DISPOSITION IS STALE OR WRONG
`resolve_disposition` is called TWICE per object, at two different times, with two
different `context` values, and **both calls are correct - this is not a bug**:

1. **At deconstruct time** (`deconstruct._resolve_object_dispositions`, inside
   `deconstruct_from_response`), with `context=None` (nothing run-specific exists yet -
   deconstruction happens once per ad, before any operator has chosen a per-run offer
   or generation has picked a testimonial). This resolves the CONTEXT-FREE purposes
   correctly and permanently (headline/subtext/cta/product_callout/award/disclaimer,
   plus ownership/prop-keyword drops) and STORES the result on `blueprint.objects[].
   disposition` in the artifact row. For offer/price_anchor/certification/testimonial,
   this first call has no context to check against, so these ALWAYS resolve to `drop`
   here - that stored value is not the final answer for these four purposes.
2. **Again inside `generate_image_prompt._objects_clause`**, at PROMPT-BUILD time, for
   any `kind=="text"` object that still carries a `text_purpose` - this time with the
   REAL context for THIS run (`offer_text`, `product.certifications`,
   `select_testimonial_review`'s pick). This second call can and correctly does
   OVERRIDE the stored value: an object stored as `disposition="drop"` (no context
   existed at deconstruct time) can resolve to `"substitute"` here once a real
   offer/testimonial exists for this specific run - and the reverse never happens for
   these purposes (they never resolve to `"keep"` at either call site).

**The consequence to remember**: `artifacts.blueprint.objects[].disposition`, as
literally stored in the database, can legitimately DISAGREE with what a given
generation call actually rendered for an offer/certification/testimonial object - a
row showing `disposition: "drop"` does NOT mean the object was dropped from every past
or future draft of that ad; it means no context existed on the run that produced that
STORED value. Reading the stored blueprint cold (a DB query, a dashboard JSON dump) and
concluding "this object was always dropped/never substituted" is exactly the mistake
this note exists to head off. To know what actually happened on a SPECIFIC generation,
check that call's own `offer_text`/`testimonial`/`product.certifications` inputs, not
the blueprint's stored disposition alone. Non-text kinds and text objects with no
`text_purpose` at all are NOT subject to this - they resolve once, identically, at
either call site, since they carry no context-gated purpose.

### Known open, neither fixed this session
- **Per-zone copy generation and the "communicative purpose" copy-steering clause are
  DELETED with no replacement.** The pre-refactor system (`generate_copy.
  text_zone_targets`/`_text_zone_copy_clause`/`_cta_zone_clause`/`_text_purpose_clause`)
  gave each `sub_line`/`body_copy`/`product_callout` zone its OWN distinct line of
  Besque copy (position-matched into `panel_copy`), and separately told the copywriter
  what JOB each reference text block was doing (offer-led vs. problem-hook vs.
  product-description) so generated copy matched that function. Both are gone -
  `product_callout` now substitutes with only the bare Besque product name (per this
  session's own task scope), and there is no signal left telling `generate_copy.py`
  what register a reference's text was written in. This is a real, deliberate capability
  loss, not an oversight - recorded here so nobody goes looking for it as a "regression"
  without checking this note first.
- **Scene lighting facts are always empty, and nothing downstream knows it.** The
  objects-array refactor's `deconstruct.py` BLUEPRINT_PROMPT collapsed `visual.
  scene_lighting`'s six sub-fields (`light_direction`/`hardness`/`colour_temperature`/
  `shadow_behaviour`/`grain`/`depth_of_field`) plus `layout_detail.background_type`
  into a single `background.{surface, colour, light}` object - but
  `generate_image_prompt._scene_lighting_facts`/`_bottle_register_clause`/
  `_register_clause` (and `build_image_prompt`'s own `scene_lighting = visual.get(
  "scene_lighting") or {}` line) still read the OLD six-field shape from `visual.
  scene_lighting`, which no new blueprint ever populates. Every one of these silently
  gets `{}` and falls through to its "not extracted" fallback wording on every new
  blueprint - `pipeline.py:1169`'s own warning log (`"blueprint.visual.scene_lighting
  not extracted this run"`) now fires on literally every ad, not just the genuine
  gaps it was written to catch. `edit_capability._background_control`/
  `_lighting_control` WERE fixed this session to read the new `background` object
  (Stage 3 of the restoration task explicitly required it for the edit controls) -
  this is the SAME underlying collapse, just the prompt-assembly side of it, still
  unfixed. Found, not fixed - flagged as a separate adjacent bug per this session's own
  working convention (log a different-owner bug found mid-fix separately, don't fold
  it in). Next step, not started: either restore the six-field detail to `background`
  (a deconstruct.py prompt change) or rewrite `_scene_lighting_facts` et al. to work
  from `background.light`'s single free-text phrase instead.

## 2026-08-17 — SESSION 2: legacy re-deconstruct, per-object copy restored, Route B
## compositing verified live, Layer A regression protection, deletion audit, three-
## voices product-count fix, three more safeguards restored

**Pushed today (all timestamps confirmed via `git show`, all same calendar date
despite this session's own code comments wrongly dating some of this work
2026-08-18/2026-08-19 — those are typos in source comments, not real dates; fix
them next time that file is touched, not urgent enough to justify a code-only
edit on its own):**

`f13971f` (09:52) → `584c1f8` (10:40) → `af9ee8b` (12:44) → `0a703d5` (13:42) →
`2eed7e3` (15:02) → `4fcdd30` (15:34) → `57a39e0` (15:39) → `8a50fd7` (17:24).

None of these are merged to `main` or deployed — still on `feat/dynamic-edit-system`,
same standing status as every prior session on this branch. Only ONE thing below is
confirmed **VERIFIED LIVE** against a real Gemini call (the Route B gate firing);
everything else this session was proven by unit/integration test and direct
interpreter sanity-checks, not yet by a real Generate call. The standing rule on this
(verify via Generate on a never-drafted ad, never Regenerate) still hasn't been
exercised for most of today's work — first action next session.

### `f13971f` — dead-key consumers repointed at `background`, per-object bboxes
Fixes the exact gap the PRIOR "2026-08-17" session (above) flagged as still open:
"`Scene lighting facts are always empty, and nothing downstream knows it`."
`_scene_lighting_facts`/`_bottle_register_clause`/`_register_clause`/`drift_check.py`
were still reading `visual.scene_lighting`'s old six-field shape, which no blueprint
produced by the objects-array refactor (`6b82f60`) ever populates - silently
degrading to the "not extracted" fallback on every single new ad, not just genuine
gaps. Repointed at `background.{surface,colour,light}` (the field the refactor
actually collapsed everything into) and at real per-object `bbox` values for
zone-position facts, instead of the deleted `layout_detail.zone_positions` free-text
list. **The known-open note above about this exact gap is now stale - left in place
rather than deleted (per the standing rule this session added), corrected here
instead.**

### `584c1f8` — legacy blueprints re-deconstruct on the regenerate path
**Why this mattered, not just what it did**: the objects-array refactor (`6b82f60`,
2026-08-17 earlier session) shipped a NEW required schema field (`objects[]`) with
**no backfill and no migration** - confirmed then, ~300 existing artifact rows had no
`objects` key at all. `_regenerate_existing_draft` (`pipeline.py`) rebuilds its prompt
from the artifact's OWN stored blueprint - meaning every one of those ~300 rows had
been running the objects model against **zero real data** since the refactor
shipped, silently degrading through `_objects_clause`'s own empty-objects fallback
(no SUBSTITUTE/KEEP/DROP lines, no closure sentence) on every Regenerate click, with
nothing surfacing that this was happening. Fixed: a legacy blueprint (no `objects`
key) is now re-deconstructed from the source ad image before the prompt rebuilds,
so Regenerate on an old artifact actually exercises the objects model for the first
time rather than perpetuating a blueprint that predates it. `_objects_clause`'s
empty-objects case (a blueprint that STILL has no `objects` after this, e.g. a
re-deconstruct failure) now logs at ERROR by name instead of silently degrading -
the same "make silent returns loud" principle as everywhere else this codebase has
applied it this session.

### `af9ee8b` — per-object copy generation restored, job-inheritance rule
Root-cause fix for identical generated copy repeating across multiple text objects on
one draft (confirmed live shape: four Instagram-DM-bubble objects on one reference,
all four rendered the IDENTICAL generated sentence) - the deleted
`generate_copy.text_zone_targets`/`_text_zone_copy_clause` machinery restored from
git history, re-keyed to `object_id` (never a free-text position string, which the
old pre-objects-model version matched by - object_id is exact, position strings
never were). Per the operator's explicit amendment this session: the `text_purpose ==
"other"` bucket is the PRIMARY failure path (the four DM bubbles are all
`text_purpose="other"`, no other recognised purpose reaches this fallback for a text
object) and now gets real, per-object generated copy driven by
description/persuasive_function/role/reading-order, never a shared generic line.
Restored from history, not reinvented, per the operator's explicit instruction that
session.

### `0a703d5` — `serves_object_id` re-evaluation, object removal rebuilds the prompt, bbox leak fixed
Two related problems, one mechanism (an object existing "in service of" another -
e.g. a hand holding a product - has no relational field and doesn't get re-evaluated
when the object it serves changes disposition), plus one bug found while in this
code:
- `serves_object_id` (schema addition) records which OTHER object a text/prop object
  exists only to support. `deconstruct._resolve_object_dispositions` now runs a
  second pass: any object naming a `serves_object_id` is re-resolved against what the
  object it serves ACTUALLY resolved to (substituted/dropped) - a hand serving a
  product that gets substituted or dropped has no independent reason to survive
  unchanged, closing the "the hand carries over unchanged" leak observed repeatedly
  before this (also seen with hair). Single-hop only, documented as a scoping limit,
  not a silent gap.
- The operator's object-removal edit control previously only inpainted v1 pixels with
  an isolated delta instruction, no view of the rest of the composition. Now marks
  the target object's disposition `"drop"` in a COPY of the blueprint
  (`generate_image_prompt.blueprint_with_object_dropped`), rebuilds the FULL prompt
  via `build_image_prompt` (so `_objects_clause` emits a real ABSENT line plus the
  closure sentence - the same mechanism a fresh generation already uses to remove an
  object cleanly), then regenerates against the v1 draft, following
  `_regenerate_existing_draft`'s own shape for resolving stored inputs.
- **Bug found and fixed while in this code**: the literal text `"0.38 0.43"` (a raw
  bbox fragment) was rendering into generated images, traced to the raw blueprint
  JSON dump leaking into the copy prompt - fixed and covered by a dedicated test.

### `2eed7e3` — Route B Pillow compositing, **gate fired live for the first time at 16:29 on ad `2767866756880226`**
Gate-scoped Pillow compositing of the real bottle cutout into the generated draft
(`composite_product`), instead of letting Gemini redraw the bottle freehand -
`_composite_gate` decides, from blueprint-level facts alone (never the generated
image), whether compositing is safe: exactly one substitute-marked product object
with a usable bbox, not held/gripped, and `background.light` not hard/directional.
Composites bottom-anchored/horizontally-centred with a conservative brightness match
and a subtle contact shadow; `_bottle_geometry_clause`/`_bottle_identity_clause` are
suppressed from the prompt ONLY when compositing will proceed (decided BEFORE
`build_image_prompt` is called, since the gate is a pure function of blueprint
facts - the ordering problem this required solving). Generate path only; regenerate/
targeted-edit paths deliberately untouched this session. **Confirmed live at 16:29 on
ad `2767866756880226`: the gate fired and the composite ran** - the first real
evidence this mechanism actually engages against a real ad, not just its own test
suite.

### `4fcdd30` / `57a39e0` — Layer A regression protection
**What it covers**: (1) `tests/fixtures/blueprints/` - real-shaped blueprint fixtures
(one general-purpose, one the real OSEA "You'll Wish You Went Jumbo" two-product
reference) asserting schema validity, every `objects[]` entry has required fields,
and - critically, per an explicit correction this session - disposition assertions
call `deconstruct.resolve_disposition(obj, context)` directly rather than reading the
STORED `disposition` field, since the dual-resolution design means the stored value
can legitimately disagree with what a real run actually resolves. (2) A non-empty
clause guard: every `_..._clause`/`_..._facts`/`brand_rules` function in
`generate_image_prompt.py`, found by INTROSPECTION (name pattern, not a hand-
maintained list), must return non-empty text given a fully-populated valid scenario.
(3) Silent returns made loud: of 9 total `return ""` sites found by systematic grep,
4 got a new `log.error` (missing data that should normally be present -
`_scene_lighting_facts`, `_scene_composition_facts`, `_register_clause`, one
`_objects_clause` defensive path) and 4 were deliberately left silent (normal,
frequently-empty per-run toggles - `_operator_instruction_clause`,
`_critic_feedback_clause`, `_semantic_split_clause`,
`_suppressed_container_exception` - logging ERROR on these would fire on the common
case and bury real signal, the opposite of this task's own purpose).

**What it does NOT cover** - said plainly, since "regression protection" invites
assuming more coverage than exists: no Gemini/Claude calls, no image bytes, no live
generation of any kind. It cannot catch a prompt that assembles correctly but
produces a bad IMAGE, a compliance leak that only shows up in real pixels, or
anything the output critic exists for. It also only fully covers the ONE real
fixture (OSEA) in depth - the general-purpose fixture is synthetic, not drawn from a
real ad, pending the operator supplying more real `ad_id`s for future fixtures.

### Deletion audit — six GONE items found, three restored today, three still open
A dedicated audit (two parallel forks, one for `src/`, one for deleted tests) of
everything `6b82f60`/`a9b1e9f` removed from `src/` with no replacement, cross-checked
against actual current file contents. Six confirmed GONE with no equivalent
anywhere: **(1)** the duplicate-testimonial guard (`testimonial_placed`, old
`_structural_zones_clause`) - **CONFIRMED LIVE**, a real draft rendered the identical
review ("Nice and smooth... — Margaret P.") in two boxes; **(2)** the stat-claim
badge force-removal (`STAT_CLAIM_PATTERNS`/`_is_stat_shaped_zone`) - worse than
merely gone, a stat-shaped badge falling to `text_purpose="other"` with disposition
`substitute` was getting FRESH Besque wording written into it by `_object_copy_clause`,
an unsubstantiated efficacy claim, the exact violation class from 31 Jul; **(3)** the
aggregate-review-bar-vs-single-quote distinction
(`structural_zones[].social_proof_kind`); **(4)** per-zone typography detail
(`typography_zones`); **(5)** testimonial card/rating styling detail
(`testimonial_zones` - content is restored via `text_purpose="testimonial"`, HOW it
was visually presented is not); **(6)** the copy "communicative purpose" steering
clause (`_text_purpose_clause` - the pre-existing "Known open" note directly above
this section, already correctly flagged as not fixed).

**Restored this session** (see `8a50fd7` below): (1) and (3), the two confirmed
compliance/duplication risks, plus (2) the stat-claim guard given the severity of the
"gets fresh wording written into it" finding. **Still open, not restored, no fix
direction chosen yet**: typography zones, testimonial styling detail, and the copy
purpose-steering clause - all three are capability losses, correctly still
documented as such per the standing rule at the top of this file, not silently
re-deleted from the record just because they're not this session's fix.

### `8a50fd7`, part 1 — three-voices product-count fix + contradiction guard
Diagnosed and fixed a live failure on the real OSEA two-product reference: rule 7
(`_rule7_product_policy`) said "exactly one bottle... NEVER add a second"; SCENE
OBJECTS emitted two byte-identical SUBSTITUTE bullets for the two competitor product
objects; `_edit_mode_instruction` said "substitute a Besque item [for each]" - three
voices, two answers, in the SAME prompt. The critic reported neither bottle was ever
replaced - not "one wins" (the older 8-bottle failure shape), literally neither.

Fix: `deconstruct.resolve_product_group_dispositions` computes, from the objects
inventory alone (a new optional `same_product_as` field distinguishing "same product,
different size/format" from "genuinely different products"), whether multiple
product objects should ALL substitute (matching the reference's own count) or exactly
ONE should (the rest dropping, freed space closing into the composition). Every
clause that used to independently claim a count - rule 7, `_edit_mode_instruction`,
`product_clause`'s `>1` branch, `_substitute_object_line`'s per-object text - now
reads the SAME computed value. Important nuance found mid-fix: a `>1` count is only
trusted for "render N bottles" when it's genuinely grounded in objects-model evidence
(`product_count_source == "objects"`) - a legacy blueprint with no `objects` array
still collapses to exactly one bottle, unchanged since the original 2026-08-12 fix,
or this restoration would have reintroduced the 8-bottle bug for every pre-objects-
model row.

**The regression lock this task explicitly required**: a generic contradiction-guard
scanner (`tests/test_product_count_contradiction.py`) extracts every "exactly N
bottle(s)" statement from a BUILT prompt and fails if any two disagree, or if the old
unconditional "NEVER add a second bottle" phrasing coexists with a resolved count
above 1 - scoped to catch a disagreement between ANY two sections, not tied to the
three specific clauses this session happened to fix. Cross-path invariant tests
confirm fresh generate, regenerate, and object removal all inherit the fix
automatically (none of them pass `product_count` explicitly); targeted edit and the
object-removal delta template are confirmed structurally exempt (they never state a
bottle count at all).

### `8a50fd7`, part 2 — duplicate-testimonial guard, stat-claim removal, aggregate-vs-quote restored
The three items from the deletion audit above, restored purely additively (nothing
deleted, per the standing rule this session added):
- `deconstruct.resolve_testimonial_dispositions(objects, context)` - coordinates
  across ALL `text_purpose=="testimonial"` objects in one call; at most one ever
  resolves to `"substitute"` (the first eligible one, when a real testimonial was
  supplied this run), every other one drops. Fixes the confirmed-live duplicate-quote
  bug directly.
- New optional schema field `objects[].social_proof_kind` (`"single_quote"` |
  `"aggregate"` | null, absent defaults to `"single_quote"` for back-compat with
  every pre-existing blueprint). An `"aggregate"`-marked object (a review-count/star-
  average bar, e.g. "Rated 4.8 by 12,000 customers") is NEVER eligible to win the one
  substitute slot, even when it's the only testimonial-purposed object present -
  Besque has no approved aggregate figure (still "HELD pending Harry", unchanged from
  earlier sessions).
- `deconstruct.STAT_CLAIM_PATTERNS`/`_is_stat_shaped_text` recovered verbatim from
  `6b82f60~1` (reuses `compliance.py`'s own numeric/ratio/timescale patterns,
  unchanged). Wired into `_resolve_text_disposition`, checked BEFORE
  `_TEXT_PURPOSE_ALWAYS_SUBSTITUTE` (`product_callout` is unconditionally in that
  set, so a stat-shaped callout must be intercepted before reaching it). Deliberately
  scoped to `product_callout`/`other`/no-purpose only, matching the original code's
  exact scope - NOT headline/subtext, whose wording is already governed elsewhere and
  never copies the reference's claim verbatim; forcing those SLOTS to drop would
  delete a headline position Besque's own wording still needs to occupy.

All three: `tests/test_restored_safeguards.py`, 18 tests, each naming the specific
pre-`6b82f60` test it restores (verified against `git show 6b82f60~1` directly, not
taken from the audit summary on trust).

### Still open, not fixed this session
- **Bottle fidelity beyond surface-placed compositing.** Route B only composites the
  real cutout for the narrow gate `_composite_gate` clears (one substitute product,
  usable bbox, not held, non-directional light) - held/gripped placements and
  anything the gate rejects still depend entirely on Gemini drawing the bottle from
  `_bottle_geometry_clause`/`_bottle_identity_clause` text alone, same reliability as
  before this session.
- **Inherited hand / `serves_object_id` coverage gap.** The relational field only
  exists on blueprints deconstructed AFTER `0a703d5` - every pre-existing row has no
  `serves_object_id` on any object and gets none of the re-evaluation benefit; never
  backfilled, same "coverage gap, not yet closed" shape as every other schema
  addition this branch has made.
- **The unadaptable-reference gate**, and the staged-progression detector that should
  feed it - recommended shape (a `argument_adaptable` boolean + `unadaptable_reason`,
  plus `mark_seen` so a future run doesn't re-pay for the same unadaptable reference)
  was written up in an earlier session; still not built.
- **Women-only product constraint** - not audited or enforced anywhere this session;
  flagged as open, no fix direction chosen.
- **Vertex image-call retry** - the 12 Aug finding (a 429/`RESOURCE_EXHAUSTED` burst
  discards deconstruct AND copy work for that ad, not just the image step) still has
  no dedicated long-backoff/retryable-failure handling; unchanged this session.
- **DB connection pool liveness** - the `ThreadedConnectionPool` (`maxconn=10`) has no
  liveness/health check; a dropped pooled connection mid-run still surfaces as
  "random 500s and blank pool-card images," same diagnosis as the 2026-08-10 note,
  no fix attempted this session.
- **`already_generated` guard** - not investigated this session; flagged as open by
  name only, no detail recorded yet on what gap this actually covers or doesn't.
- **Test hygiene for CI** - the ~40 untracked scratch files at repo root, the
  `TEST_`/`PIPE_`/`ART_`-prefixed rows in `seen_ads` (1,497 of 1,670 as of the last
  count), and the port-5433 local-Postgres dependency for `tests/` all remain
  exactly as documented in earlier sessions - none touched today.
- **Layer B golden set** - Layer A (this session) is unit/fixture-level regression
  protection only; a golden set of real generated drafts checked against expected
  visual/compliance outcomes (the actual gap a fixture can't close - see "what Layer A
  does NOT cover" above) does not exist yet and wasn't started this session.

### A note on the auto-commit anomaly, again
Every commit this session (`f13971f` through `8a50fd7`) landed in `git log` without
an explicit `git commit` call at the point of the corresponding task - reported
transparently at the end of each task in-session, consistent with every prior
occurrence of this same unexplained mechanism. Still no hook or setting found that
explains it. Not investigated further this session; recorded here so the pattern
stays visible across sessions rather than being re-discovered from scratch each time.

## 2026-08-17 (continued) — 15-Aug-vs-today prompt comparison: not a clean regression either way

Operator report: good drafts 14-15 Aug, worse since. Compared by building real prompts
(never by reading diffs alone) from a temp worktree at `5bc97cd` (last commit of 15
Aug - **branch `known-good-15aug` points at this commit for future comparisons**)
against HEAD, using the real 14-Aug artifact closest to that date (id 1279, ad_id
`993666990086399`, "Norse Organics", 2026-08-14 11:35 UTC - no artifacts exist from
15 Aug itself) and the OSEA two-product fixture for HEAD, both under `edit_mode=True`
with the real current product record. Verdict: **differently shaped, weaker in three
specific respects, stronger in others - not a clean regression.**

### WEAKER - three fixes owed, in priority order

1. **`product_callout` collapses to the bare product name.** Demonstrated by feeding
   the SAME four real 14-Aug callout descriptions through today's code: "Deeply
   Nourishing" / "Skin-Soothing" / "Visibly Softening" / "Fast-Absorbing" all render
   as "Besque Magic Body Oil" four times. Root cause: `objects_context` is built
   ONCE per draft in `build_image_prompt`; `_substitute_object_line`'s
   `product_callout` branch returns `context["product_name"]` for every object,
   with no per-object differentiation. The SAME shared-value defect is confirmed in
   the `certification` branch (the whole joined certifications list gets stamped
   into every certification-purposed object, not one certification per badge),
   and in `offer`/`price_anchor`/`cta` when a reference has more than one of them.
   `testimonial` is NOT affected the same way - `resolve_testimonial_dispositions`
   already restricts at most one testimonial-purposed object to ever win the
   substitute slot, so the shared-value shape in that branch's code is never
   actually exercised by more than one object.

   Fixing `product_callout` is NOT a pure code fix - it reopens a compliance
   decision made deliberately this session: callout content was narrowed to the
   bare product name specifically to stop each callout inventing its own
   unsubstantiated benefit claim. A real per-callout fix needs `approved_claims`
   from Harry to draw from, not just a code change that lets the model write
   whatever benefit text it likes per object again.

   **Certification is the safe one to fix first** - splitting an ALREADY-
   AUTHORISED list (`product.certifications`) one-per-badge instead of the whole
   list into every badge raises no compliance question at all; it's a clean
   per-object routing fix with no open question blocking it.

2. **Testimonial vs. aggregate can INVERT on blueprints not yet re-deconstructed
   with `social_proof_kind`.** Demonstrated: with `social_proof_kind` absent (the
   real state of every blueprint predating that field), the aggregate star-rating
   bar wins the one real-testimonial substitute slot, and the actual single-quote
   object - the one that should have won - gets dropped instead. The safeguard
   (`resolve_testimonial_dispositions`, restored this session) is correct once the
   field is populated; the gap is coverage, not logic - confirmed by re-running the
   same real data with `social_proof_kind` correctly set, which resolved it correctly.

3. **Competitor-argument-prop detection narrowed from open-ended model judgement to
   ten fixed keywords.** 15 Aug trusted the model's own semantic call
   (`depicts_competitor_category`, no keyword list) to decide whether a prop exists
   to make the competitor's argument. Today's mechanical backstop
   (`deconstruct._is_competitor_argument_prop`) only matches `("diagram",
   "illustration", "device", "applicator", "inset", "anatomical", "prop stand",
   "wand", "roller", "dropper tool")` - ten fixed words that do NOT include
   "chain"/"padlock", despite this exact file naming the chain-and-padlock-
   illustrating-locked-fat case as the MOTIVATING EXAMPLE for this feature (see the
   13 Aug section above). Confirmed live: feeding that exact object through
   `resolve_disposition` with the model's own guess wrong (`"keep"`) returns
   `"keep"` unchanged - the mechanical net does not catch its own canonical case.

### STRONGER today, for balance

Explicit fixed bottle-geometry numbers (independent of whether a reference photo is
attached, unlike 15 Aug's photo-only source of truth); the SCENE OBJECTS closure
sentence (15 Aug had no whole-scene completeness statement at all); Route B
compositing (removes the bottle-drawing task from Gemini entirely for placements its
gate accepts); the count-aware, contradiction-guarded product-count mechanism
(15 Aug had a bare number with no same-vs-different-product distinction); typography
filtering that actually excludes dropped objects (15 Aug's own docstring claimed this
but the code never enforced it).

### Also confirmed this session: `87000ab` did not stop the double bottle

`87000ab` ("request empty product-shaped space when compositing, so Gemini does not
also draw a bottle") was **verified live and found NOT to fix the live symptom it was
built for** - confirmed with a restart at 18:23 against an 18:22 commit (so the
running process was definitely on the fixed code), and the double bottle still
appeared. Not yet root-caused further this session. Do not treat `87000ab` as closing
the double-bottle issue until this is re-investigated.

Separately, the composite gate rejected every held placement it was shown - the grip
gate is doing its job of refusing to composite a held product, per its own design.
Across all stored blueprints with a non-empty `objects` array, held/gripped placements
are ~19% (5 of 26 classified; N=26, small and directional only, not a stable
statistic) - this is the number that should decide whether held-placement compositing
is worth building, not a guess.

## 2026-08-18 — BatchAdConfig: found already built (11 Aug), `run_once` was the actual gap

Asked to "build BatchAdConfig as specced on 10 Aug." It already existed -
`@dataclass(frozen=True) class BatchAdConfig` (`pipeline.py:276`), built 2026-08-11
(commit `1c3ca6c`), holding `angle_id`/`realism`/`body_area`/`text_in_image`/
`include_product`/`edit_mode`/`offer_text`/`operator_instruction`. `generate_from_selection`
already builds a fresh instance per ad inside its loop (merging `per_ad_overrides`) and
passes `config=cfg` into `process_ad`, which overwrites its own locals from `config` when
given (`process_ad`, line ~1131) before anything downstream reads them - `realism="(auto)"`
(reaches here as `None`) already resolves PER AD inside `build_image_prompt`/`generate_image`
from that ad's own `blueprint.production_style.style`, since each ad gets its own freshly-
deconstructed blueprint regardless of this plumbing.

**The actual gap: `run_once` never built a `BatchAdConfig` at all** - it passed its own
enclosing-scope locals (`realism`, `body_area`, etc., identical for every ad across the
whole scheduled sweep) straight into `process_ad` with `config=None`, so `process_ad`'s
config-override block never ran for this call path. Fixed: a fresh `BatchAdConfig` is now
built inside `run_once`'s per-ad loop (mirroring `generate_from_selection`'s own
construction) and passed as `config=cfg`. `run_once` has no per-ad-override input, so
every field still holds the same run-strip value for every ad in the sweep - that's
unchanged and correct, a scheduled sweep genuinely has one operator-set config today. What
changed is that `process_ad` now receives it structurally rather than via closure locals.
Purely additive - the existing raw kwargs into `process_ad` were left in place (user
explicitly chose "leave it alone" over removing the redundant dual path in
`generate_from_selection`'s equivalent call, so the same call shape was kept here too).

**First version of the test was wrong - it didn't actually test the freeze.** Asked
directly "does it fail if the per-ad freeze is removed", checked by mutation (temporarily
moved `cfg = BatchAdConfig(...)` outside `generate_from_selection`'s per-ad loop, re-ran,
reverted): a test asserting only "ad1=illustrated, ad2=ugc, both auto-detected from each
ad's own `blueprint.production_style`" **still passed** under that mutation, because
`realism` stays `None` for every ad in that scenario regardless of whether `cfg` is built
once or per-ad - the differentiation there comes entirely from `build_image_prompt`
reading each call's own blueprint, a mechanism that has never depended on `BatchAdConfig`
at all. Real coverage (catches a genuine per-ad-blueprint-threading bug), but not coverage
of the freeze specifically - kept as
`test_generate_from_selection_resolves_realism_per_ad_from_production_style`, with its
own docstring saying so.

Added a second test that actually exercises the freeze -
`test_generate_from_selection_resolves_realism_per_ad_from_per_ad_override`: two ads, no
`production_style` on either blueprint, `per_ad_overrides` sets a DIFFERENT explicit
`realism` per ad_id. Confirmed by the SAME mutation that this one DOES fail (ad 2 silently
inherits ad 1's override) where the first test does not - this is the one that actually
proves "resolved once per ad, never re-read from shared state." Both are fully
DB-independent (`_mock_dedupe_fully_db_independent`, same pattern as this file's own
`_mock_dedupe_for_scope_guard`), unlike the rest of this test file, so they can run and be
mutation-verified with no Postgres reachable.

**Caught while writing the test**: `validator.production_styles()`/
`generate_image_prompt_writer.STYLE_GUIDANCE` only have three real keys today -
`high_spec`/`illustrated`/`ugc` - confirmed by direct inspection, not assumed. An existing,
already-passing test (`test_run_once_threads_realism_and_toggles_to_process_ad`) uses
`realism="ugc_native"` as an example value, which is NOT one of the three - harmless there
only because that test mocks `process_ad` entirely and never reaches `STYLE_GUIDANCE`, but
worth knowing before copying that string as if it were a valid style anywhere it actually
matters (`build_image_prompt`/the writer would silently fall through to
`DEFAULT_STYLE_GUIDANCE` for it, same as any other unrecognized value).

**SAFETY INCIDENT, same session, while doing the DB-free verification above.** A
standalone script (not the test suite - a throwaway script run directly with
`python script.py` to get a full traceback pytest's own log line was hiding) has no
`conftest.py` in its import path, so nothing forced `DATABASE_URL` to the test port -
it read `.env`'s real value and connected straight to `34.105.137.192:5432/besque`, the
exact address `conftest.py` has a hardcoded `_FORBIDDEN_MARKERS` guard to refuse test
COLLECTION against (a guard that only ever runs inside pytest, so it can't protect a
script that bypasses pytest entirely). `dedupe.mark_seen()` - called unconditionally near
the end of `process_ad`'s success path - wasn't mocked in that script, and wrote two real
rows into the real `seen_ads` table: `ad_id IN ('AD1','AD2')`, `page_name='Brand'`,
`angle_id=NULL`. Caught immediately by re-reading the log line the exception handler had
been swallowing (`dedupe: creating connection pool` - a pool doesn't get created against
a REFUSED connection, which is what should have happened), confirmed via a read-only
`SELECT` (exactly those 2 rows, nothing in `artifacts`/`scraped_ads` - both mocked
no-ops in that script), then deleted with the operator's explicit go-ahead
(`DELETE FROM seen_ads WHERE ad_id IN ('AD1','AD2')`, confirmed 0 rows remaining
afterward). `"AD1"`/`"AD2"` can never be real Facebook `ad_archive_id`s (those are
purely numeric, per this file's own standing note on identifying test-shaped
`seen_ads` rows), so this was unambiguous test pollution, not a risk of deleting real
data - but the near-miss is the lesson: **`dedupe.mark_seen` is now the ninth function
this codebase has found calling `get_conn()` from a path that looked already-covered.**
Fixed by adding it to `_mock_dedupe_fully_db_independent` (now the committed test helper
mocks it too, so this exact mistake can't recur via this helper) - but the standing
lesson is broader: **any ad-hoc verification script that imports `src.dedupe` needs the
SAME production-IP guard `conftest.py` gives real tests, and does not get it for free.**
Next person writing a throwaway DB-adjacent script: either run it through pytest (even a
single inline `def test_x(): ...` in a scratch file, so `conftest.py` actually loads), or
manually check `DATABASE_URL` against the forbidden markers before importing `dedupe` at
all - do not assume "I mocked the functions I could think of" is equivalent to
`conftest.py`'s guard.

Both new tests pass on the real (unmutated) code, confirmed via pytest (not a standalone
script) - safe, since `conftest.py`'s port-5433 override was active for that run.
