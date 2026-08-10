"""Besque Ad Intelligence - Web Dashboard.
Read-only view + approve/reject + run trigger. Uses existing pipeline/db.
"""
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

load_dotenv()
from src import dedupe, assets, validator

app = FastAPI(title="Besque Ad Intelligence")


@app.on_event("startup")
def _init_tables():
    """Every dedupe.init_* call is CREATE TABLE IF NOT EXISTS - idempotent, and safe to
    run once per process start rather than once per request. Until this commit, each of
    the 25 call sites below ran on EVERY matching request, each opening its own
    connection (dedupe.get_conn has no pooling until the next commit) - /api/artifacts
    alone made 3 of these on every 3-second dashboard poll tick, before it ever touched a
    row of actual data. Moved here, once, covering every init_* this file calls anywhere
    in a request handler."""
    dedupe.init_artifacts()
    dedupe.init_angles()
    dedupe.init_angle_language()
    dedupe.init_decisions()
    dedupe.init_run_progress()
    dedupe.init_competitors()
    dedupe.init_products()
    dedupe.init_brand_settings()
    dedupe.init_pipeline_warnings()
    dedupe.init_scraped_ads()
    dedupe.init_fetch_jobs()
    dedupe.init_generate_jobs()


# Serve the saved ad images
ASSET_DIR = Path("assets")
ASSET_DIR.mkdir(exist_ok=True)
@app.get("/assets/{filename}")
def get_asset(filename: str):
    local = ASSET_DIR / filename
    if local.exists():
        return Response(local.read_bytes(), media_type="image/png")
    try:
        from google.cloud import storage
        bucket_name = assets.asset_bucket_name()
        blob = storage.Client().bucket(bucket_name).blob(filename)
        if blob.exists():
            return Response(blob.download_as_bytes(), media_type="image/png")
    except Exception as e:
        print(f"Bucket fetch failed: {e}")
    return Response(status_code=404)

templates = Jinja2Templates(directory="templates")

_run_status = {"running": False, "last_summary": None, "stop_requested": False, "execution": None,
               "mode": None}
# Set only so tests can join() deterministically instead of sleep-polling for completion.
_run_thread = None
# Same reasoning as _run_thread above, for api_fetch_pool's background thread.
_fetch_thread = None
# Same reasoning again, for api_generate's background thread (Chunk 5).
_generate_thread = None


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    # Categories/styles come from the blueprint schema so the dropdowns can't drift from
    # the enums validator.is_valid() actually enforces.
    return templates.TemplateResponse(
        request, "dashboard.html", {
            "product_categories": validator.product_categories(),
            "production_styles": validator.production_styles(),
        }
    )


@app.get("/pool", response_class=HTMLResponse)
def pool_page(request: Request):
    """Chunk 3: the browse-and-pick grid, on its own route/template rather than
    folded into dashboard.html - a separate page keeps the existing review
    workflow untouched while this one gets its own competitor selector, Fetch
    trigger, and grid, all reading GET /api/pool/cards and POST+GET /api/fetch."""
    return templates.TemplateResponse(request, "pool.html", {})


@app.get("/api/artifacts")
def api_artifacts():
    rows = dedupe.get_artifacts_full(limit=500)
    # id -> angle dict, so each card can show which angle it was generated for without
    # a per-row lookup. Small table, fetched once per request.
    angles_by_id = {a["id"]: a for a in dedupe.get_angles()}
    # Make datetimes / paths JSON-friendly
    out = []
    for r in rows:
        img = r.get("image_path") or ""
        draft = r.get("draft_image") or ""
        angle_id = r.get("angle_id")
        angle = angles_by_id.get(angle_id) if angle_id else None
        out.append({
            "ad_id": r["ad_id"],
            "page_name": r.get("page_name", ""),
            "original_image": ("/assets/" + os.path.basename(img.replace("\\", "/"))) if img else "",
            "draft_image": ("/assets/" + os.path.basename(draft.replace("\\", "/"))) if draft else "",
            "blueprint": r.get("blueprint") or {},
            "copy": r.get("generated_copy") or {},
            "image_prompt": r.get("image_prompt") or "",
            "copy_prompt": r.get("copy_prompt") or "",
            "model_info": r.get("model_info") or "",
            "decision": r.get("decision"),
            "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M") if r.get("created_at") else "",
            # angle_id/angle_name are about OUR generation choice for this artifact - kept
            # distinct from blueprint.angle, which is Claude's read of the competitor ad's
            # own angle. Cards and edit/decide calls key off angle_id, never bp.angle.
            "angle_id": angle_id,
            "angle_name": angle["name"] if angle else "",
            "text_in_image": bool(r.get("text_in_image")),
            # Auditability (Step 2): a reviewer looking at a wrong draft must be able to
            # see whether the operator asked for it, not just infer it from image_prompt.
            "operator_instruction": r.get("operator_instruction") or "",
            # Output critic (Prompt 4, Item 1): surface, never act - these are shown on
            # the card for a human to weigh, never auto-rejected or auto-regenerated.
            "critic_findings": r.get("critic_findings") or [],
            # Reference-format flag (Prompt 4, Item 4): a FLAG, never a filter - the
            # reference's own composition argued for a multi-product/bundle message that
            # rule 7 collapsed to one Besque bottle.
            "format_flag": r.get("format_flag") or "",
            # Silent-override audit (2026-08-05): the reference had no product to
            # substitute, so include_product was overridden off for this draft even
            # though the operator asked for one.
            "product_override_note": r.get("product_override_note") or "",
            # Critic gate (2026-08-10): 'ok' | 'failed-review' - written by
            # update_artifact_findings when the corrective retry still comes back
            # HIGH-confidence. Drives dashboard.html's Failed Review badge directly;
            # the template never re-derives this from critic_findings/confidence itself.
            "review_status": r.get("review_status") or "ok",
        })
    return JSONResponse(out)


@app.get("/api/decisions")
def api_decisions():
    rows = dedupe.get_decisions()[-20:][::-1]
    return JSONResponse([
        {"ad_id": r[0], "decision": r[1], "at": r[2].strftime("%Y-%m-%d %H:%M"), "reason": (r[3] if len(r) > 3 else "") or ""}
        for r in rows
    ])


@app.post("/api/decision/{ad_id}/{decision}")
def api_decision(ad_id: str, decision: str, reason: str = "", angle_id: int = None):
    if decision not in ("approve", "reject"):
        return JSONResponse({"ok": False, "error": "bad decision"}, status_code=400)
    dedupe.record_decision(ad_id, decision, reason, angle_id=angle_id)
    return JSONResponse({"ok": True, "ad_id": ad_id, "decision": decision})


def _run_pipeline_bg(n, competitor_id=None, category=None, product_id=None, angle_id=None,
                      realism=None, text_in_image=False, include_product=True,
                      body_area=None, offer_text=None, edit_mode=False, operator_instruction=None,
                      check_output=False, retheme_colours=True):
    """LOCAL_RUN's in-process runner - was dead code (api_run always hit the Cloud Run Job
    path) until LOCAL_RUN=1 made it reachable. Runs pipeline.run_once with every run-strip
    param, exactly as job_runner.py does for a real deployed Job."""
    try:
        from src import pipeline
        _run_status["last_summary"] = pipeline.run_once(
            max_per_competitor=n,
            competitor_id=competitor_id,
            category=category,
            product_id=product_id,
            angle_id=angle_id,
            realism=realism,
            text_in_image=text_in_image,
            include_product=include_product,
            body_area=body_area,
            offer_text=offer_text,
            edit_mode=edit_mode,
            operator_instruction=operator_instruction,
            check_output=check_output,
            retheme_colours=retheme_colours,
            should_stop=lambda: _run_status["stop_requested"],
        )
    except Exception as e:
        _run_status["last_summary"] = {"error": str(e)}
    finally:
        _run_status["running"] = False


@app.post("/api/run")
def api_run(n: int = 2, competitor_id: int = None, category: str = "", product_id: int = None,
            angle_id: int = None, realism: str = "", text_in_image: bool = False,
            include_product: bool = True, body_area: str = "", offer_text: str = "",
            edit_mode: bool = False, operator_instruction: str = "", check_output: bool = False,
            retheme_colours: bool = True):
    """Trigger the pipeline. Two paths:

    - LOCAL_RUN=1: runs _run_pipeline_bg (pipeline.run_once) in a background thread, in
      this process, against local code - for verifying a change before it's deployed.
    - LOCAL_RUN unset (default): triggers the Cloud Run Job exactly as before this env var
      existed - the last DEPLOYED image, not local changes. This path is UNCHANGED.

    body_area/offer_text are per-run free-text operator inputs, threaded exactly like
    realism (not persisted anywhere, not sourced from the angle). body_area in particular
    must never be read from angles.body_area here - the team confirmed body area varies
    every run and isn't fixed per angle; that column is only ever a UI pre-fill suggestion
    in dashboard.html's onAngleChange().

    edit_mode defaults to False - the team confirmed edit-vs-generate usage is about
    50/50, so today's generate-only path must keep working unchanged.

    operator_instruction (Step 2) is the "Extra direction for this run" free-text field -
    threaded exactly like body_area/offer_text, but IS persisted (onto the artifact, by
    pipeline.py) since it must be auditable on the review card. The env var is named
    RUN_INSTRUCTION (not RUN_OPERATOR_INSTRUCTION) - shorter, matching the field's actual
    length risk more than the other RUN_* names' pattern.

    check_output (Prompt 4, Item 1) gates the output critic - defaults to False since it's
    an extra vision call per ad, real cost that multiplies across a sweep.

    retheme_colours (Prompt 4, Item 5) defaults to True - the operator disables it only
    for the doc's own stated exception (an angle that specifically calls for the
    reference's own colours), which is also today's already-validated faithful-clone
    behaviour.
    """
    if os.getenv("LOCAL_RUN") == "1":
        global _run_thread
        # Reset stop_requested - a previous run's Stop click must not immediately kill
        # this new one. The Job path below doesn't need this: it never reads the flag.
        _run_status["stop_requested"] = False
        _run_status["running"] = True
        _run_status["last_summary"] = None
        _run_status["mode"] = "local"
        _run_thread = threading.Thread(
            target=_run_pipeline_bg,
            kwargs=dict(
                n=n, competitor_id=competitor_id, category=(category or None), product_id=product_id,
                angle_id=angle_id, realism=(realism or None), text_in_image=text_in_image,
                include_product=include_product, body_area=(body_area or None),
                offer_text=(offer_text or None), edit_mode=edit_mode,
                operator_instruction=(operator_instruction or None),
                check_output=check_output, retheme_colours=retheme_colours,
            ),
            daemon=True,
        )
        _run_thread.start()
        return JSONResponse({"ok": True, "started": True})

    # ---- Cloud Run Job path below is UNCHANGED from before LOCAL_RUN existed ----
    _run_status["mode"] = "job"  # bookkeeping only, read by api_run_status - does not
                                 # affect this path's own behaviour or response.
    from google.cloud import run_v2
    project = os.getenv("GCP_PROJECT", "besque-martech")
    region = os.getenv("GCP_REGION", "europe-west2")
    job = os.getenv("PIPELINE_JOB", "besque-pipeline")
    job_path = f"projects/{project}/locations/{region}/jobs/{job}"
    overrides = run_v2.RunJobRequest.Overrides(
        container_overrides=[
            run_v2.RunJobRequest.Overrides.ContainerOverride(
                env=[
                    run_v2.EnvVar(name="RUN_COMPETITOR_ID", value=str(competitor_id) if competitor_id is not None else ""),
                    run_v2.EnvVar(name="RUN_MAX_PER_COMPETITOR", value=str(n)),
                    run_v2.EnvVar(name="RUN_PRODUCT_ID", value=str(product_id) if product_id is not None else ""),
                    run_v2.EnvVar(name="RUN_ANGLE_ID", value=str(angle_id) if angle_id is not None else ""),
                    run_v2.EnvVar(name="RUN_REALISM", value=realism or ""),
                    run_v2.EnvVar(name="RUN_TEXT_IN_IMAGE", value="1" if text_in_image else "0"),
                    run_v2.EnvVar(name="RUN_INCLUDE_PRODUCT", value="1" if include_product else "0"),
                    run_v2.EnvVar(name="RUN_BODY_AREA", value=body_area or ""),
                    run_v2.EnvVar(name="RUN_OFFER_TEXT", value=offer_text or ""),
                    run_v2.EnvVar(name="RUN_EDIT_MODE", value="1" if edit_mode else "0"),
                    run_v2.EnvVar(name="RUN_INSTRUCTION", value=operator_instruction or ""),
                    run_v2.EnvVar(name="RUN_CHECK_OUTPUT", value="1" if check_output else "0"),
                    run_v2.EnvVar(name="RUN_RETHEME_COLOURS", value="1" if retheme_colours else "0"),
                ]
            )
        ]
    )
    try:
        client = run_v2.JobsClient()
        op = client.run_job(request=run_v2.RunJobRequest(name=job_path, overrides=overrides))
        _run_status["execution"] = op.metadata.name
        _run_status["running"] = True
        _run_status["last_summary"] = None
        return JSONResponse({"ok": True, "started": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/run/stop")
def api_run_stop():
    _run_status["stop_requested"] = True
    return JSONResponse({"ok": True})


def _current_progress():
    """Which competitor run_once is on right now, DB-backed (dedupe.run_progress) rather
    than an in-memory variable - the Cloud Run Job path is a separate process with no
    shared memory with the dashboard, so this is the only channel that works the same way
    for both run paths (same reasoning as pipeline_warnings). Returns None on any error or
    when nothing is running, never raises - this is supplementary status, never
    load-bearing for running/last_summary."""
    try:
        p = dedupe.get_run_progress()
        if not p or not p.get("competitor_name"):
            return None
        return {"competitor_name": p["competitor_name"], "competitor_index": p["competitor_index"],
                "competitor_total": p["competitor_total"]}
    except Exception:
        return None


@app.get("/api/run/status")
def api_run_status():
    """Report latest pipeline run state.

    A LOCAL_RUN-triggered run has no Cloud Run execution to query - report directly from
    the in-memory _run_status the background thread is updating. Otherwise (mode is "job",
    or no run has been triggered yet this process), fall through to the ORIGINAL stateless
    GCP Executions query, completely unchanged. "progress" (which competitor is currently
    running) is added to BOTH branches identically, since it's DB-backed and path-agnostic."""
    progress = _current_progress()
    if _run_status.get("mode") == "local":
        return JSONResponse({"running": _run_status["running"], "last_summary": _run_status["last_summary"],
                              "progress": progress})
    try:
        from google.cloud import run_v2
        project = os.getenv("GCP_PROJECT", "besque-martech")
        region = os.getenv("GCP_REGION", "europe-west2")
        job = os.getenv("PIPELINE_JOB", "besque-pipeline")
        parent = f"projects/{project}/locations/{region}/jobs/{job}"
        client = run_v2.ExecutionsClient()
        latest = None
        for ex in client.list_executions(parent=parent):
            latest = ex
            break
        if latest is None:
            return JSONResponse({"running": False, "last_summary": None, "progress": progress})
        running = (latest.running_count or 0) > 0
        summary = None
        if not running:
            summary = {"succeeded": latest.succeeded_count or 0, "failed": latest.failed_count or 0}
        return JSONResponse({"running": running, "last_summary": summary, "progress": progress})
    except Exception as e:
        return JSONResponse({"running": False, "last_summary": {"error": str(e)}, "progress": progress})

@app.post("/api/edit_image/{ad_id}")
async def api_edit_image(ad_id: str, request: Request):
    """Edit a draft image with a natural-language instruction via nano banana."""
    body = await request.json()
    instruction = (body.get("instruction") or "").strip()
    aspect = (body.get("aspect") or "1:1").strip()
    # angle_id disambiguates which artifact row (an ad can now have one per angle) - see
    # dedupe.get_artifact's docstring. None matches the pre-angle single-row behaviour.
    angle_id = body.get("angle_id")
    if not instruction:
        return JSONResponse({"ok": False, "error": "instruction required"}, status_code=400)
    art = dedupe.get_artifact(ad_id, angle_id=angle_id)
    if art is None:
        return JSONResponse({"ok": False, "error": "artifact not found"}, status_code=404)
    angle = dedupe.get_angle(angle_id) if angle_id else None
    angle_slug = angle["slug"] if angle else None
    # Fetch the current draft image bytes (local first, then bucket). Read back the
    # ACTUAL stored path rather than reconstructing "{ad_id}_draft.png" - an angle-variant
    # draft lives at a different ({ad_id}__{slug}) stem (see generate_image_prompt._draft_stem).
    filename = os.path.basename((art.get("draft_image") or f"{ad_id}_draft.png").replace("\\", "/"))
    current = None
    local = ASSET_DIR / filename
    if local.exists():
        current = local.read_bytes()
    else:
        try:
            from google.cloud import storage
            blob = storage.Client().bucket(assets.asset_bucket_name()).blob(filename)
            if blob.exists():
                current = blob.download_as_bytes()
        except Exception:
            pass
    if current is None:
        return JSONResponse({"ok": False, "error": "no existing draft image to edit"}, status_code=404)
    from src import generate_image_prompt
    # Restore the ORIGINAL generation's rule-6 mode rather than falling back to
    # brand_rules()'s hardcoded defaults - without this, editing a text-in-image draft
    # would silently drop its baked-in headline. Read from the stored artifact, never
    # ask the operator to re-specify. include_product has no column to read back from
    # (a known gap, not fixed here) - edit_image's rule 7 stays at its default either way.
    generated_copy = art.get("generated_copy") or {}
    result = generate_image_prompt.edit_image(
        current, instruction, ad_id, aspect=aspect, angle_slug=angle_slug,
        text_in_image=bool(art.get("text_in_image")),
        headline=generated_copy.get("headline"),
        subtext=generated_copy.get("primary_text"),
        current_prompt=art.get("image_prompt"),
    )
    if result is None:
        return JSONResponse({"ok": False, "error": "image edit failed"})
    # Record the prompt that actually produced the PNG now on disk, so the Edit modal stops
    # showing the original generation prompt after an edit. Read straight after the call,
    # the same way pipeline.py picks up generate_image.last_prompt.
    img_prompt = getattr(generate_image_prompt.edit_image, "last_prompt", "")
    if img_prompt:
        # Non-fatal: the edited image is already saved, so a bookkeeping failure here must
        # not report the edit itself as failed.
        try:
            dedupe.update_artifact_image_prompt(ad_id, img_prompt, angle_id=angle_id)
        except Exception as e:
            print(f"[api_edit_image] ad_id={ad_id} prompt record failed (non-fatal): {e}")
    return JSONResponse({"ok": True, "ad_id": ad_id})


def _stem_from_artifact(art, ad_id):
    """The {stem} draft-file prefix for an artifact - read from its OWN stored
    draft_image path (never reconstructed from ad_id/angle_slug), the same approach
    api_edit_image already uses, so an angle-variant stem is never guessed wrong."""
    filename = os.path.basename((art.get("draft_image") or f"{ad_id}_draft.png").replace("\\", "/"))
    return filename[:-len("_draft.png")] if filename.endswith("_draft.png") else ad_id


@app.get("/api/draft_versions")
def api_draft_versions(ad_id: str, angle_id: int = None):
    """List every saved version of this ad's draft, oldest first, plus the current
    draft last. has_prompt tells the UI whether Restore is safe for that version - one
    saved before this feature existed has no recoverable prompt sidecar."""
    art = dedupe.get_artifact(ad_id, angle_id=angle_id)
    if art is None:
        return JSONResponse({"ok": False, "error": "artifact not found"}, status_code=404)
    from src import generate_image_prompt
    angle = dedupe.get_angle(angle_id) if angle_id else None
    angle_slug = angle["slug"] if angle else None
    stem = _stem_from_artifact(art, ad_id)
    versions = generate_image_prompt.list_draft_versions(ad_id, angle_slug=angle_slug)
    out = [
        {"version": v["version"], "url": f"/assets/{stem}_draft_v{v['version']}.png",
         "has_prompt": v["has_prompt"]}
        for v in versions
    ]
    out.append({"version": "current", "url": f"/assets/{stem}_draft.png",
                "has_prompt": bool((art.get("image_prompt") or "").strip())})
    return JSONResponse({"ok": True, "versions": out})


@app.post("/api/draft_version/restore")
async def api_restore_draft_version(request: Request):
    """Make a prior version the current draft. Versions the outgoing draft first (so
    nothing is destroyed) and updates the artifact's image_prompt to the RESTORED
    version's own prompt, never the discarded one - a subsequent edit/regenerate then
    runs against a correctly paired image+prompt, not a mismatched one.

    Fails (400) if the requested version has no recoverable prompt sidecar - restoring
    an image without its matching prompt would silently mispair the two, exactly what
    every later edit must never work from."""
    body = await request.json()
    ad_id = (body.get("ad_id") or "").strip()
    version = body.get("version")
    angle_id = body.get("angle_id")
    if not ad_id or version is None:
        return JSONResponse({"ok": False, "error": "ad_id and version required"}, status_code=400)
    try:
        version = int(version)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "version must be an integer"}, status_code=400)
    art = dedupe.get_artifact(ad_id, angle_id=angle_id)
    if art is None:
        return JSONResponse({"ok": False, "error": "artifact not found"}, status_code=404)
    from src import generate_image_prompt
    angle = dedupe.get_angle(angle_id) if angle_id else None
    angle_slug = angle["slug"] if angle else None

    restored_prompt = generate_image_prompt.read_version_prompt(ad_id, version, angle_slug=angle_slug)
    if not restored_prompt:
        return JSONResponse({
            "ok": False,
            "error": f"no stored prompt for version {version} - refusing to restore "
                     f"(would pair a restored image with a mismatched prompt)",
        }, status_code=400)
    version_bytes = generate_image_prompt.read_version_bytes(ad_id, version, angle_slug=angle_slug)
    if version_bytes is None:
        return JSONResponse({"ok": False, "error": f"version {version} image not found"}, status_code=404)

    current_prompt = art.get("image_prompt") or ""
    versioned = generate_image_prompt.version_current_draft(ad_id, angle_slug=angle_slug, current_prompt=current_prompt)
    generate_image_prompt.overwrite_current_draft(ad_id, version_bytes, angle_slug=angle_slug)
    dedupe.update_artifact_image_prompt(ad_id, restored_prompt, angle_id=angle_id)
    return JSONResponse({"ok": True, "ad_id": ad_id, "restored_version": version, "versioned_previous_as": versioned})


@app.post("/api/edit_copy/{ad_id}")
async def api_edit_copy(ad_id: str, request: Request):
    """Revise the generated copy with a natural-language instruction via Claude."""
    body = await request.json()
    instruction = (body.get("instruction") or "").strip()
    angle_id = body.get("angle_id")
    if not instruction:
        return JSONResponse({"ok": False, "error": "instruction required"}, status_code=400)
    art = dedupe.get_artifact(ad_id, angle_id=angle_id)
    if art is None:
        return JSONResponse({"ok": False, "error": "artifact not found"}, status_code=404)
    import anthropic, json as _j
    prompt = (
        "You are a senior copywriter for Besque, a natural skincare brand for women 40+.\n"
        "Here is the current ad copy JSON:\n" + _j.dumps(art["generated_copy"], indent=2) + "\n\n"
        "Revise it according to this instruction: " + instruction + "\n"
        "Keep the same language as the current copy. Return ONLY the full revised JSON with the same fields, no preamble or markdown."
    )
    try:
        client = anthropic.Anthropic(timeout=60.0, max_retries=1)
        message = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=3072,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        new_copy = _j.loads(raw)
        dedupe.update_artifact_copy(ad_id, new_copy, angle_id=angle_id)
        return JSONResponse({"ok": True, "ad_id": ad_id, "copy": new_copy})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/competitors/{competitor_id}/accept_name")
def api_accept_name(competitor_id: int, accept: bool = True):
    comps = dedupe.get_competitors()
    comp = next((x for x in comps if x["id"] == competitor_id), None)
    if comp is None:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if accept and comp.get("suggested_name"):
        dedupe.update_competitor(competitor_id, name=comp["suggested_name"],
                                  page_id=(comp.get("page_id") or comp["suggested_name"]),
                                  category=comp.get("category") or "")
    dedupe.set_suggested_name(competitor_id, "")
    return JSONResponse({"ok": True})


@app.get("/api/page_lookup")
def api_page_lookup(q: str = ""):
    """Read-only: group existing artifacts by page_name into Meta-style cards.
    q filters by name (case-insensitive substring). Empty q returns all pages."""
    rows = dedupe.get_artifacts_full(limit=500)
    ql = (q or "").strip().lower()
    pages = {}
    for r in rows:
        pn = (r.get("page_name") or "").strip()
        if not pn:
            continue
        if ql and ql not in pn.lower():
            continue
        p = pages.get(pn)
        img = r.get("image_path") or ""
        preview = ("/assets/" + os.path.basename(img.replace("\\", "/"))) if img else ""
        if p is None:
            pages[pn] = {"page_name": pn, "ad_count": 1,
                         "latest": r.get("created_at"), "preview": preview}
        else:
            p["ad_count"] += 1
            if r.get("created_at") and (not p["latest"] or r["created_at"] > p["latest"]):
                p["latest"] = r["created_at"]
            if not p["preview"] and preview:
                p["preview"] = preview
    # include tracked competitors that have no scraped ads yet
    try:
        existing_lower = {k.lower() for k in pages.keys()}
        for comp in dedupe.get_competitors():
            cname = (comp.get("name") or "").strip()
            if not cname:
                continue
            if ql and ql not in cname.lower():
                continue
            if cname.lower() not in existing_lower:
                pages[cname] = {"page_name": cname, "ad_count": 0,
                                "latest": None, "preview": ""}
    except Exception:
        pass
    out = []
    for p in pages.values():
        out.append({"page_name": p["page_name"], "ad_count": p["ad_count"],
                    "latest": p["latest"].strftime("%d %b %Y") if p["latest"] else "",
                    "preview": p["preview"]})
    out.sort(key=lambda x: x["ad_count"], reverse=True)
    return JSONResponse(out)

@app.get("/api/products")
def api_products():
    return JSONResponse(dedupe.get_products())


@app.post("/api/products")
async def api_add_product(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    new_id = dedupe.add_product(name, body.get("description", ""), body.get("ingredients", ""),
                                body.get("hero_claim", ""), body.get("category", ""),
                                body.get("visual_description", ""), body.get("substance_colour", ""))
    return JSONResponse({"ok": True, "id": new_id})


@app.post("/api/products/{product_id}")
async def api_update_product(product_id: int, request: Request):
    body = await request.json()
    dedupe.update_product(product_id, body.get("name", ""), body.get("description", ""), body.get("ingredients", ""),
                          body.get("hero_claim", ""), body.get("category", ""),
                          body.get("visual_description", ""), body.get("substance_colour", ""))
    return JSONResponse({"ok": True, "id": product_id})


@app.post("/api/products/{product_id}/photo")
async def api_product_photo(product_id: int, request: Request):
    """Upload one reference product photo into the product's fixed photo set (up to
    dedupe.MAX_PRODUCT_IMAGES). Body: raw image bytes. Each upload gets a unique key -
    legacy image_key is frozen and never written by this path."""
    data = await request.body()
    if not data or len(data) < 100:
        return JSONResponse({"ok": False, "error": "no image data"}, status_code=400)
    if len(data) > 10 * 1024 * 1024:
        return JSONResponse({"ok": False, "error": "image too large (max 10MB)"}, status_code=400)
    p = dedupe.get_product(product_id)
    if p is None:
        return JSONResponse({"ok": False, "error": "product not found"}, status_code=404)
    if len(p["image_keys"]) >= dedupe.MAX_PRODUCT_IMAGES:
        return JSONResponse({"ok": False, "error": f"already has {dedupe.MAX_PRODUCT_IMAGES} reference images - remove one first"})
    import uuid
    key = f"product_{product_id}_ref_{uuid.uuid4().hex[:8]}.png"
    try:
        from google.cloud import storage
        bucket = storage.Client().bucket(assets.asset_bucket_name())
        bucket.blob(key).upload_from_string(data, content_type="image/png")
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"upload failed: {e}"})
    dedupe.add_product_image(product_id, key)
    return JSONResponse({"ok": True, "image_keys": dedupe.get_product(product_id)["image_keys"]})


@app.post("/api/products/{product_id}/photo/remove")
async def api_product_photo_remove(product_id: int, request: Request):
    """Remove one reference photo: deletes the stored blob and the image_keys entry
    together, so a remove never leaves an orphaned blob in the bucket."""
    body = await request.json()
    key = (body.get("key") or "").strip()
    if not key:
        return JSONResponse({"ok": False, "error": "key required"}, status_code=400)
    blob_error = None
    try:
        from google.cloud import storage
        storage.Client().bucket(assets.asset_bucket_name()).blob(key).delete()
    except Exception as e:
        blob_error = str(e)
    dedupe.remove_product_image(product_id, key)
    resp = {"ok": True, "image_keys": dedupe.get_product(product_id)["image_keys"]}
    if blob_error:
        # DB is now consistent either way; surface the blob-delete failure rather than
        # swallowing it, since it means the blob itself is now orphaned in the bucket.
        resp["warning"] = f"blob delete failed (now orphaned): {blob_error}"
    return JSONResponse(resp)


@app.post("/api/products/{product_id}/delete")
def api_delete_product(product_id: int):
    p = dedupe.get_product(product_id)
    blob_errors = []
    if p:
        all_keys = list(p["image_keys"]) + ([p["image_key"]] if p["image_key"] else [])
        for key in all_keys:
            try:
                from google.cloud import storage
                storage.Client().bucket(assets.asset_bucket_name()).blob(key).delete()
            except Exception as e:
                blob_errors.append(f"{key}: {e}")
    dedupe.delete_product(product_id)
    resp = {"ok": True, "id": product_id}
    if blob_errors:
        resp["warning"] = f"{len(blob_errors)} blob(s) failed to delete (now orphaned): " + "; ".join(blob_errors)
    return JSONResponse(resp)


@app.get("/api/brand_settings")
def api_brand_settings():
    return JSONResponse(dedupe.get_brand_settings())


@app.post("/api/brand_settings")
async def api_update_brand_settings(request: Request):
    body = await request.json()
    palette = (body.get("palette") or "").strip()
    dedupe.update_brand_settings(palette)
    return JSONResponse(dedupe.get_brand_settings())


@app.get("/api/angles")
def api_angles():
    return JSONResponse(dedupe.get_angles())


@app.get("/api/production_styles")
def api_production_styles():
    """The realism/production_style enum (item 2, 2026-08-06) - read from the schema via
    validator.production_styles(), same source deconstruct.py's classifier prompt and
    STYLE_GUIDANCE's coverage assertion already use, so the pool run-strip dropdown can
    never drift from what a blueprint can actually contain or what the generator can
    actually act on."""
    return JSONResponse(validator.production_styles())


@app.post("/api/angles")
async def api_add_angle(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    slug = (body.get("slug") or "").strip()
    if not name or not slug:
        return JSONResponse({"ok": False, "error": "name and slug required"}, status_code=400)
    new_id = dedupe.add_angle(name, slug, body.get("body_area", ""), body.get("default_realism", ""),
                              bool(body.get("includes_product", True)), body.get("notes", ""))
    return JSONResponse({"ok": True, "id": new_id})


@app.post("/api/angles/{angle_id}")
async def api_update_angle(angle_id: int, request: Request):
    body = await request.json()
    dedupe.update_angle(angle_id, body.get("name", ""), body.get("slug", ""), body.get("body_area", ""),
                        body.get("default_realism", ""), bool(body.get("includes_product", True)),
                        body.get("notes", ""))
    return JSONResponse({"ok": True, "id": angle_id})


@app.post("/api/angles/{angle_id}/delete")
def api_delete_angle(angle_id: int):
    dedupe.delete_angle(angle_id)
    return JSONResponse({"ok": True, "id": angle_id})


@app.get("/api/warnings")
def api_warnings():
    rows = dedupe.get_recent_warnings()
    # created_at is a raw datetime (dedupe.py never serialises it, same convention as
    # get_artifacts_full/get_decisions) - this table being empty until 30 Jul is the only
    # reason this endpoint ever returned 200: the warnings banner has never once actually
    # rendered a real warning, since JSONResponse's default encoder can't serialise a
    # datetime and raises TypeError before the response body is even built.
    return JSONResponse([
        {"id": r["id"], "kind": r["kind"], "detail": r["detail"],
         "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M") if r.get("created_at") else ""}
        for r in rows
    ])


@app.get("/api/competitors")
def api_competitors():
    rows = dedupe.get_competitors()
    return JSONResponse([{"id": r["id"], "name": r["name"], "page_id": r["page_id"],
                          "suggested_name": r.get("suggested_name") or "",
                          "category": r.get("category") or ""} for r in rows])


@app.post("/api/competitors")
def api_add_competitor(name: str, page_id: str = "", category: str = ""):
    """Append a new competitor to the watchlist table. Never overwrites existing rows.
    page_id falls back to name when omitted, matching the PUT handler below."""
    resolved_page_id = page_id or name
    new_id = dedupe.add_competitor(name=name, page_id=resolved_page_id, category=category)
    return JSONResponse({"ok": True, "id": new_id, "name": name, "page_id": resolved_page_id, "category": category})


@app.put("/api/competitors/{competitor_id}")
def api_update_competitor(competitor_id: int, name: str, page_id: str = None, category: str = ""):
    """page_id absent from the request -> None -> update_competitor leaves the existing
    page_id untouched. Do NOT default it to name here: unlike POST (a brand-new row with
    no page_id yet), PUT can be a category-only or name-only edit of a row that already
    has a real, verified numeric page_id - defaulting to name would overwrite it. This
    exact mistake (copying add_competitor's "default to name" fallback into the update
    path) wiped six verified page_ids on 2026-07-30."""
    dedupe.update_competitor(competitor_id, name=name, page_id=page_id, category=category)
    return JSONResponse({"ok": True, "id": competitor_id, "name": name, "category": category})


@app.delete("/api/competitors/{competitor_id}")
def api_delete_competitor(competitor_id: int):
    dedupe.delete_competitor(competitor_id)
    return JSONResponse({"ok": True, "id": competitor_id})


@app.get("/api/pool")
def api_pool(competitor_id: int = None, status: str = "pool", limit: int = 100, offset: int = 0):
    """Dumb passthrough onto scraped_ads - deliberately does NOT derive/flatten/select
    "card fields" out of raw_meta. The Chunk 3 grid gets that from the SEPARATE
    GET /api/pool/cards below, so this endpoint's own contract (raw_meta in full,
    real SQL LIMIT/OFFSET) never changes underneath an existing caller. limit
    defaults to 100 (explicit, not the get_artifacts_full-style 50)."""
    rows = dedupe.get_scraped_ads(competitor_id=competitor_id, status=status, limit=limit, offset=offset)
    total = dedupe.count_scraped_ads(competitor_id=competitor_id, status=status)
    return JSONResponse({
        "total": total,
        "limit": limit,
        "offset": offset,
        "rows": [
            {
                "id": r["id"],
                "ad_id": r["ad_id"],
                "competitor_id": r["competitor_id"],
                "image_url": r["image_url"],
                "gcs_path": r["gcs_path"],
                "raw_meta": r["raw_meta"],
                "fetched_at": r["fetched_at"].strftime("%Y-%m-%d %H:%M:%S") if r.get("fetched_at") else "",
                "status": r["status"],
                "media_type": r.get("media_type") or "",
            }
            for r in rows
        ],
    })


def _parse_apify_date(value):
    """Parse one of Apify's ad_delivery_*_time strings into a datetime, or None if
    missing/unparseable. Callers must treat None as "unknown", never coerce it to
    a fake 0 - a missing start_time means "we can't judge this ad's age", not
    "just started"."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _days_running(start_raw, stop_raw):
    """(min(now, stop_time) if stop_time else now) - start_time, in whole days.
    The min() against now is deliberate: a scheduled FUTURE stop_time (an ad
    scheduled to keep running) must never inflate the figure by counting days
    that haven't happened yet. Returns None if start_time is missing/unparseable -
    the caller sorts these last, never treats None as 0 days. Negative results
    (a malformed/future start_time) are clamped to 0 rather than shown as
    negative, which would misread as "not running yet" rather than "bad data"."""
    start = _parse_apify_date(start_raw)
    if start is None:
        return None
    now = datetime.now(timezone.utc) if start.tzinfo is not None else datetime.utcnow()
    stop = _parse_apify_date(stop_raw)
    if stop is not None and (stop.tzinfo is None) != (start.tzinfo is None):
        # Mixed aware/naive inputs (shouldn't happen from one source, but Apify's
        # exact format isn't guaranteed) - normalise stop to start's awareness
        # rather than letting the subtraction below raise.
        stop = stop.replace(tzinfo=timezone.utc) if start.tzinfo is not None else stop.replace(tzinfo=None)
    end = min(now, stop) if stop is not None else now
    return max((end - start).days, 0)


def _has_unrendered_template_token(value):
    """True if value is a string carrying a literal Meta template token like
    {{product.name}} - DCO ads store the UNRENDERED template, not the resolved
    copy, so raw {{...}} placeholders can leak straight into
    ad_creative_bodies/ad_creative_link_titles/cta_text (Chunk 6, Part A, Item 1).
    The resolved copy isn't anywhere in the data, so this is purely a detector for
    "suppress this slot" - never an attempt to fill the token in."""
    return isinstance(value, str) and "{{" in value and "}}" in value


def _suppress_templated(value):
    """Drop a templated string to None; for a list, drop just the templated
    entries and keep any real ones - a DCO record can carry both {{...}} bodies
    and one real one across its variants."""
    if isinstance(value, list):
        return [v for v in value if not _has_unrendered_template_token(v)]
    return None if _has_unrendered_template_token(value) else value


@app.get("/api/pool/cards")
def api_pool_cards(competitor_id: int = None, status: str = None, limit: int = 200, angle_id: int = None):
    """Flattened, judgeable-fields-only view of the pool for the Chunk 3
    browse-and-pick grid - deliberately a SEPARATE endpoint from GET /api/pool
    above (which stays a dumb raw_meta-in-full passthrough) rather than a query
    flag on it, so /api/pool's existing callers can never silently get a
    different response shape from the same URL. This one derives exactly the
    fields the team judges an ad by from raw_meta SERVER-SIDE and ships only
    those to the browser - never the whole jsonb blob.

    status defaults to None (no filter): scraped_ads.status is angle-agnostic
    (moved off 'pool' by ANY generation, any angle), so filtering on it by
    default hid an ad already generated for one angle from every other angle's
    grid - the browse-everything contract this endpoint exists for.

    Sorted by days_running descending; ads with no parseable start_time sort
    last (never treated as 0 days - see _days_running). limit is explicit
    (default 200, no hardcoded 50) and applied AFTER sorting: the pool for this
    filter is fetched and sorted in full first (dedupe.get_scraped_ads with no
    SQL limit), so limiting here never truncates by database row order instead
    of by days_running.

    angle_id (Chunk 5, Item 3): each card's "already_generated" flag reflects
    whether an artifact already exists for THIS angle specifically (via
    dedupe.get_artifact_ad_ids), not scraped_ads.status (Chunk 4's flat,
    angle-agnostic status) - the same ad can be fresh for one angle and already
    generated for another, and the grid must show the CURRENTLY SELECTED angle's
    truth, not a single per-row flag that can't distinguish the two. angle_id
    omitted/None checks the "no angle" identity, same as everywhere else angle_id
    is used as a dedup key.

    ad_creative_bodies/ad_creative_link_titles/cta_text are suppressed (never
    printed as-is) when they carry a literal {{...}} Meta template token (Chunk
    6, Part A, Item 1) - DCO ads store Meta's UNRENDERED template, not the
    resolved copy, and the resolved text isn't in the data anywhere to recover.
    Suppressing the slot, not attempting to resolve the token."""
    rows = dedupe.get_scraped_ads(competitor_id=competitor_id, status=status, limit=None)
    generated_ad_ids = dedupe.get_artifact_ad_ids([r["ad_id"] for r in rows], angle_id=angle_id)
    cards = []
    for r in rows:
        meta = r.get("raw_meta") or {}
        cards.append({
            "ad_id": r["ad_id"],
            "image_url": r["image_url"],
            "media_type": r.get("media_type") or "",
            "is_active": meta.get("is_active"),
            "days_running": _days_running(meta.get("ad_delivery_start_time"), meta.get("ad_delivery_stop_time")),
            "ad_delivery_start_time": meta.get("ad_delivery_start_time"),
            "ad_delivery_stop_time": meta.get("ad_delivery_stop_time"),
            "ad_creative_bodies": _suppress_templated(meta.get("ad_creative_bodies") or []),
            "ad_creative_link_titles": _suppress_templated(meta.get("ad_creative_link_titles") or []),
            "cta_text": _suppress_templated(meta.get("cta_text")),
            "page_name": meta.get("page_name") or "",
            "fetched_at": r["fetched_at"].strftime("%Y-%m-%d %H:%M:%S") if r.get("fetched_at") else "",
            "already_generated": r["ad_id"] in generated_ad_ids,
        })
    cards.sort(key=lambda c: (c["days_running"] is None, -(c["days_running"] or 0)))
    return JSONResponse({"total": len(cards), "limit": limit, "cards": cards[:limit]})


@app.post("/api/fetch")
async def api_fetch_pool(request: Request):
    """Start pipeline.fetch_pool for one competitor on a background thread and
    return immediately - fetch-and-store only, same caveats as fetch_pool itself
    (no deconstruct/generation, no seen_ads/artifacts writes). Poll GET
    /api/fetch/status?competitor_id=... for progress/result, same pattern as
    /api/run + /api/run/status (a separate status endpoint, not folded into
    GET /api/pool - "is a fetch in flight" and "what's stored" are different
    questions, same as /api/run keeps them separate).

    Rejects a second concurrent fetch for a competitor already 'running'
    (409) rather than starting a duplicate Apify call - dedupe.try_start_fetch_job
    is a single atomic statement, not read-then-write, so this holds under a race
    between two near-simultaneous clicks. Competitor existence is checked here,
    BEFORE claiming the job slot, so an unknown competitor_id never creates a
    fetch_jobs row at all.

    start_date_min/start_date_max/active_status (Chunk 6.2): the same three
    fields threaded through to pipeline.fetch_pool - see its own docstring.
    active_status defaults to "active" (today's behaviour); "inactive"/"all" are
    what actually surfaces a page whose ads are all paused. mediaType handling
    is untouched."""
    body = await request.json()
    competitor_id = body.get("competitor_id")
    if competitor_id is None:
        return JSONResponse({"ok": False, "error": "competitor_id required"}, status_code=400)
    try:
        competitor_id = int(competitor_id)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "competitor_id must be an integer"}, status_code=400)
    cap = body.get("cap", 50)
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "cap must be an integer"}, status_code=400)
    if cap <= 0:
        return JSONResponse({"ok": False, "error": "cap must be a positive integer"}, status_code=400)
    start_date_min = (body.get("start_date_min") or "").strip() or None
    start_date_max = (body.get("start_date_max") or "").strip() or None
    active_status = (body.get("active_status") or "active").strip().lower()
    if active_status not in ("active", "inactive", "all"):
        return JSONResponse({"ok": False, "error": "active_status must be 'active', 'inactive', or 'all'"}, status_code=400)

    competitor = next((c for c in dedupe.get_competitors() if c["id"] == competitor_id), None)
    if not competitor:
        return JSONResponse({"ok": False, "error": f"competitor {competitor_id} not found"}, status_code=404)

    if not dedupe.try_start_fetch_job(competitor_id):
        return JSONResponse(
            {"ok": False, "error": f"a fetch is already running for competitor {competitor_id}"},
            status_code=409,
        )

    from src import pipeline

    def _run_fetch_pool():
        try:
            result = pipeline.fetch_pool(competitor_id, cap=cap, start_date_min=start_date_min,
                                          start_date_max=start_date_max, active_status=active_status)
            dedupe.finish_fetch_job(competitor_id, result=result)
        except Exception as e:
            # Must always reach finish_fetch_job - an uncaught exception here would
            # leave the row stuck on 'running' forever (try_start_fetch_job's WHERE
            # guard would then permanently refuse every future fetch for this
            # competitor).
            dedupe.finish_fetch_job(competitor_id, error=str(e))

    global _fetch_thread
    _fetch_thread = threading.Thread(target=_run_fetch_pool, daemon=True)
    _fetch_thread.start()
    return JSONResponse({"ok": True, "started": True, "competitor_id": competitor_id})


@app.get("/api/fetch/status")
def api_fetch_status(competitor_id: int):
    """Poll one competitor's fetch job - 'running'/'done'/'error', or 'none' if no
    fetch has ever run for it. result is fetch_pool's dict once status is 'done';
    error is the exception message once status is 'error'. Both are None
    otherwise."""
    job = dedupe.get_fetch_job(competitor_id)
    if job is None:
        return JSONResponse({"status": "none", "result": None, "error": None})
    return JSONResponse({"status": job["status"], "result": job["result"], "error": job["error"]})


def _bool_or_none(body, key):
    """None when `key` is genuinely absent from the request body - never silently
    defaulted here. Coerced to a real bool only when the key is present, whatever its
    own value (including a present-but-falsy `false`). Deciding what None means downstream
    is pipeline.py's job (process_ad normalizes it to a concrete default for its own use,
    and separately hands the raw None-or-value to the regenerate resolver) - this
    function's only job is to preserve "was it sent at all" (Task F, point 1, 2026-08-07)."""
    if key not in body:
        return None
    return bool(body[key])


@app.post("/api/generate")
async def api_generate(request: Request):
    """Start pipeline.generate_from_selection on a background thread and return
    immediately (Chunk 5, Item 4) - same backgrounding pattern as POST /api/fetch:
    a multi-ad generation run is exactly the kind of multi-minute call that must
    never block the request. Poll GET /api/generate/status?job_id=... for live
    per-ad progress and the terminal state.

    Body: ad_ids (required, non-empty list), angle_id/body_area/offer_text/
    instruction/product_id (the existing per-run inputs, reused as-is - Item 1),
    regenerate (bool, default False - the operator's explicit ask after seeing a
    card marked already-generated, Item 3/7c). text_in_image/include_product/
    edit_mode/check_output/retheme_colours (Chunk 6.1, Item 1 - urgent live fix)
    are the same run-strip toggles dashboard.html's /api/run already exposes,
    same names and same defaults - a live run produced images with no baked-in
    copy because pool.html had no control for text_in_image at all and this
    endpoint silently fell back to generate_from_selection's/process_ad's
    text_in_image=False default with no way to override it.

    should_stop is a DB-backed poll of this job's own stop_requested flag (Item 5)
    - forwarded into generate_from_selection, which checks it BETWEEN ads and
    passes it into process_ad, which checks it once more immediately before the
    paid Gemini call. on_ad_done writes live progress into generate_jobs.progress
    after each ad finishes, not just once the whole selection is done."""
    body = await request.json()
    ad_ids = body.get("ad_ids")
    if not ad_ids or not isinstance(ad_ids, list):
        return JSONResponse({"ok": False, "error": "ad_ids (a non-empty list) required"}, status_code=400)
    angle_id = body.get("angle_id")
    body_area = (body.get("body_area") or "").strip() or None
    offer_text = (body.get("offer_text") or "").strip() or None
    instruction = (body.get("instruction") or "").strip() or None
    product_id = body.get("product_id")
    regenerate = bool(body.get("regenerate", False))
    text_in_image = bool(body.get("text_in_image", False))
    # None when the key is genuinely ABSENT from the request body - never conflated with
    # an explicit False (Task F, point 1, 2026-08-07). pool.html's checkboxes always send
    # a real true/false today (confirmed: `.checked` never omits the key), so this is a
    # no-op for that caller - but it's the only way pipeline.py's regenerate resolver
    # (resolve_regenerate_input) can ever tell "operator explicitly set false this call"
    # apart from "operator did not touch it this call" for ANY caller, including ones that
    # genuinely omit the key. Scoped to exactly the three fields Task F's regenerate
    # precedence fix covers - text_in_image/check_output are unaffected, unchanged below.
    include_product = _bool_or_none(body, "include_product")
    edit_mode = _bool_or_none(body, "edit_mode")
    check_output = bool(body.get("check_output", False))
    retheme_colours = _bool_or_none(body, "retheme_colours")
    realism = (body.get("realism") or "").strip() or None
    if realism is not None and realism not in validator.production_styles():
        return JSONResponse(
            {"ok": False, "error": f"realism must be one of {validator.production_styles()}, or omitted"},
            status_code=400,
        )

    job_id = uuid.uuid4().hex
    dedupe.start_generate_job(job_id, ad_ids)

    from src import pipeline

    def _on_ad_done(ad_id, result):
        dedupe.update_generate_job_progress(job_id, ad_id, result)

    def _job_should_stop():
        job = dedupe.get_generate_job(job_id)
        return bool(job and job.get("stop_requested"))

    def _run_generate():
        try:
            result = pipeline.generate_from_selection(
                ad_ids, angle_id=angle_id, body_area=body_area, offer_text=offer_text,
                instruction=instruction, product_id=product_id, regenerate=regenerate,
                text_in_image=text_in_image, include_product=include_product, edit_mode=edit_mode,
                check_output=check_output, retheme_colours=retheme_colours, realism=realism,
                should_stop=_job_should_stop, on_ad_done=_on_ad_done,
            )
            dedupe.finish_generate_job(job_id, result=result)
        except Exception as e:
            dedupe.finish_generate_job(job_id, error=str(e))

    global _generate_thread
    _generate_thread = threading.Thread(target=_run_generate, daemon=True)
    _generate_thread.start()
    return JSONResponse({"ok": True, "started": True, "job_id": job_id})


@app.get("/api/generate/status")
def api_generate_status(job_id: str):
    """Poll one generation job - 'running'/'done'/'error', or 'none' if job_id is
    unrecognised. progress is {ad_id: result} filled in live as each ad finishes;
    result is generate_from_selection's final summary dict once status is 'done'."""
    job = dedupe.get_generate_job(job_id)
    if job is None:
        return JSONResponse({"status": "none", "progress": {}, "result": None, "error": None})
    return JSONResponse({
        "status": job["status"], "progress": job["progress"] or {},
        "result": job["result"], "error": job["error"],
    })


@app.post("/api/generate/stop")
async def api_generate_stop(request: Request):
    """Request a running generation job halt - checked by that job's own
    background thread both between ads and immediately before the next paid
    Gemini call (Item 5), never just between ads."""
    body = await request.json()
    job_id = body.get("job_id")
    if not job_id:
        return JSONResponse({"ok": False, "error": "job_id required"}, status_code=400)
    dedupe.request_generate_job_stop(job_id)
    return JSONResponse({"ok": True})


@app.get("/api/stats")
def api_stats():
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM artifacts")
        total = cur.fetchone()[0]
        cur.execute("SELECT decision, COUNT(DISTINCT ad_id) FROM review_decisions GROUP BY decision")
        counts = dict(cur.fetchall())
    return JSONResponse({
        "total": total,
        "approved": counts.get("approve", 0),
        "rejected": counts.get("reject", 0),
    })