"""Besque Ad Intelligence - Web Dashboard.
Read-only view + approve/reject + run trigger. Uses existing pipeline/db.
"""
import os
import threading
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

load_dotenv()
from src import dedupe, assets, validator

app = FastAPI(title="Besque Ad Intelligence")

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


@app.get("/api/artifacts")
def api_artifacts():
    dedupe.init_artifacts()
    dedupe.init_angles()
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
        })
    return JSONResponse(out)


@app.get("/api/decisions")
def api_decisions():
    dedupe.init_decisions()
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
        dedupe.init_run_progress()
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
    dedupe.init_artifacts()
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
        dedupe.init_competitors()
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
    dedupe.init_products()
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
    dedupe.init_brand_settings()
    return JSONResponse(dedupe.get_brand_settings())


@app.post("/api/brand_settings")
async def api_update_brand_settings(request: Request):
    body = await request.json()
    palette = (body.get("palette") or "").strip()
    dedupe.init_brand_settings()
    dedupe.update_brand_settings(palette)
    return JSONResponse(dedupe.get_brand_settings())


@app.get("/api/angles")
def api_angles():
    dedupe.init_angles()
    return JSONResponse(dedupe.get_angles())


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
    dedupe.init_pipeline_warnings()
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
    dedupe.init_competitors()
    rows = dedupe.get_competitors()
    return JSONResponse([{"id": r["id"], "name": r["name"], "page_id": r["page_id"],
                          "suggested_name": r.get("suggested_name") or "",
                          "category": r.get("category") or ""} for r in rows])


@app.post("/api/competitors")
def api_add_competitor(name: str, page_id: str = "", category: str = ""):
    """Append a new competitor to the watchlist table. Never overwrites existing rows.
    page_id falls back to name when omitted, matching the PUT handler below."""
    dedupe.init_competitors()
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
    "card fields" out of raw_meta, that decision is chunk 3's and is blocked on an open
    question. limit defaults to 100 (explicit, not the get_artifacts_full-style 50) and
    is a real SQL LIMIT/OFFSET via dedupe.get_scraped_ads, not a fetch-all-then-slice."""
    dedupe.init_scraped_ads()
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
            }
            for r in rows
        ],
    })


@app.post("/api/fetch")
async def api_fetch_pool(request: Request):
    """Trigger pipeline.fetch_pool for one competitor - fetch-and-store only, same
    caveats as fetch_pool itself (no deconstruct/generation, no seen_ads/artifacts
    writes). Success response uses the dashboard's normal {"ok": True, ...}
    envelope, with the fetch_pool dict nested under "result" - supersedes this
    endpoint's original "return fetch_pool's dict verbatim" shape, since the
    Chunk 3 grid needs a consistent envelope across every /api/ endpoint it
    consumes more than it needs this one endpoint to be unwrapped. skipped is now
    a per-reason breakdown dict (not_image/wrong_page/no_image_url/duplicate) -
    fetch_pool's own change, passed through here unchanged.

    Runs pipeline.fetch_pool SYNCHRONOUSLY - it blocks on a live Apify call (observed
    ~4.5 minutes), so this request does not return until that finishes. See the
    blocking-behaviour note shipped alongside this endpoint for what that means for a
    browser caller and Cloud Run's request timeout; nothing here makes it async or
    backgrounds it."""
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
    from src import pipeline
    try:
        result = pipeline.fetch_pool(competitor_id, cap=cap)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    return JSONResponse({"ok": True, "result": result})


@app.get("/api/stats")
def api_stats():
    dedupe.init_artifacts()
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