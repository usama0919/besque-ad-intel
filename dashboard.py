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
from src import dedupe

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
        bucket_name = os.getenv("ASSET_BUCKET", "besque-ad-intel-assets")
        blob = storage.Client().bucket(bucket_name).blob(filename)
        if blob.exists():
            return Response(blob.download_as_bytes(), media_type="image/png")
    except Exception as e:
        print(f"Bucket fetch failed: {e}")
    return Response(status_code=404)

templates = Jinja2Templates(directory="templates")

_run_status = {"running": False, "last_summary": None, "stop_requested": False, "execution": None}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/api/artifacts")
def api_artifacts():
    dedupe.init_artifacts()
    rows = dedupe.get_artifacts_full(limit=30)
    # Make datetimes / paths JSON-friendly
    out = []
    for r in rows:
        img = r.get("image_path") or ""
        draft = r.get("draft_image") or ""
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
        })
    return JSONResponse(out)


@app.get("/api/decisions")
def api_decisions():
    dedupe.init_decisions()
    rows = dedupe.get_decisions()[-20:][::-1]
    return JSONResponse([
        {"ad_id": a, "decision": d, "at": t.strftime("%Y-%m-%d %H:%M")}
        for a, d, t in rows
    ])


@app.post("/api/decision/{ad_id}/{decision}")
def api_decision(ad_id: str, decision: str, reason: str = ""):
    if decision not in ("approve", "reject"):
        return JSONResponse({"ok": False, "error": "bad decision"}, status_code=400)
    dedupe.record_decision(ad_id, decision, reason)
    return JSONResponse({"ok": True, "ad_id": ad_id, "decision": decision})


def _run_pipeline_bg(n, competitor_id=None):
    try:
        from src import pipeline
        _run_status["last_summary"] = pipeline.run_once(
            max_per_competitor=n,
            competitor_id=competitor_id,
            should_stop=lambda: _run_status["stop_requested"],
        )
    except Exception as e:
        _run_status["last_summary"] = {"error": str(e)}
    finally:
        _run_status["running"] = False


@app.post("/api/run")
def api_run(n: int = 2, competitor_id: int = None, product_id: int = None):
    """Trigger the pipeline as a Cloud Run Job (runs to completion, isolated)."""
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


@app.get("/api/run/status")
def api_run_status():
    """Report latest pipeline job execution state (stateless, instance-safe)."""
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
            return JSONResponse({"running": False, "last_summary": None})
        running = (latest.running_count or 0) > 0
        summary = None
        if not running:
            summary = {"succeeded": latest.succeeded_count or 0, "failed": latest.failed_count or 0}
        return JSONResponse({"running": running, "last_summary": summary})
    except Exception as e:
        return JSONResponse({"running": False, "last_summary": {"error": str(e)}})

@app.post("/api/edit_image/{ad_id}")
async def api_edit_image(ad_id: str, request: Request):
    """Edit a draft image with a natural-language instruction via nano banana."""
    body = await request.json()
    instruction = (body.get("instruction") or "").strip()
    aspect = (body.get("aspect") or "1:1").strip()
    if not instruction:
        return JSONResponse({"ok": False, "error": "instruction required"}, status_code=400)
    art = dedupe.get_artifact(ad_id)
    if art is None:
        return JSONResponse({"ok": False, "error": "artifact not found"}, status_code=404)
    # fetch the current draft image bytes (local first, then bucket)
    filename = f"{ad_id}_draft.png"
    current = None
    local = ASSET_DIR / filename
    if local.exists():
        current = local.read_bytes()
    else:
        try:
            from google.cloud import storage
            blob = storage.Client().bucket(os.getenv("ASSET_BUCKET", "besque-ad-intel-assets")).blob(filename)
            if blob.exists():
                current = blob.download_as_bytes()
        except Exception:
            pass
    if current is None:
        return JSONResponse({"ok": False, "error": "no existing draft image to edit"}, status_code=404)
    from src import generate_image_prompt
    result = generate_image_prompt.edit_image(current, instruction, ad_id, aspect=aspect)
    if result is None:
        return JSONResponse({"ok": False, "error": "image edit failed"})
    return JSONResponse({"ok": True, "ad_id": ad_id})


@app.post("/api/edit_copy/{ad_id}")
async def api_edit_copy(ad_id: str, request: Request):
    """Revise the generated copy with a natural-language instruction via Claude."""
    body = await request.json()
    instruction = (body.get("instruction") or "").strip()
    if not instruction:
        return JSONResponse({"ok": False, "error": "instruction required"}, status_code=400)
    art = dedupe.get_artifact(ad_id)
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
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        new_copy = _j.loads(raw)
        dedupe.update_artifact_copy(ad_id, new_copy)
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
        dedupe.update_competitor(competitor_id, comp["suggested_name"], comp.get("page_id") or comp["suggested_name"])
    dedupe.set_suggested_name(competitor_id, "")
    return JSONResponse({"ok": True})


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
    new_id = dedupe.add_product(name, body.get("description", ""), body.get("ingredients", ""), body.get("hero_claim", ""))
    return JSONResponse({"ok": True, "id": new_id})


@app.post("/api/products/{product_id}")
async def api_update_product(product_id: int, request: Request):
    body = await request.json()
    dedupe.update_product(product_id, body.get("name", ""), body.get("description", ""), body.get("ingredients", ""), body.get("hero_claim", ""))
    return JSONResponse({"ok": True, "id": product_id})


@app.post("/api/products/{product_id}/photo")
async def api_product_photo(product_id: int, request: Request):
    """Upload a reference product photo. Body: raw image bytes. Stores to bucket."""
    data = await request.body()
    if not data or len(data) < 100:
        return JSONResponse({"ok": False, "error": "no image data"}, status_code=400)
    if len(data) > 10 * 1024 * 1024:
        return JSONResponse({"ok": False, "error": "image too large (max 10MB)"}, status_code=400)
    key = f"product_{product_id}_ref.png"
    try:
        from google.cloud import storage
        bucket = storage.Client().bucket(os.getenv("ASSET_BUCKET", "besque-ad-intel-assets"))
        bucket.blob(key).upload_from_string(data, content_type="image/png")
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"upload failed: {e}"})
    p = dedupe.get_product(product_id)
    if p is None:
        return JSONResponse({"ok": False, "error": "product not found"}, status_code=404)
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE products SET image_key=%s WHERE id=%s", (key, product_id))
        conn.commit()
    return JSONResponse({"ok": True, "image_key": key})


@app.post("/api/products/{product_id}/delete")
def api_delete_product(product_id: int):
    dedupe.delete_product(product_id)
    return JSONResponse({"ok": True, "id": product_id})


@app.get("/api/competitors")
def api_competitors():
    dedupe.init_competitors()
    rows = dedupe.get_competitors()
    return JSONResponse([{"id": r["id"], "name": r["name"], "page_id": r["page_id"], "suggested_name": r.get("suggested_name") or ""} for r in rows])


@app.post("/api/competitors")
def api_add_competitor(name: str):
    """Append a new competitor to the watchlist table. Never overwrites existing rows."""
    dedupe.init_competitors()
    new_id = dedupe.add_competitor(name, name)
    return JSONResponse({"ok": True, "id": new_id, "name": name, "page_id": name})


@app.put("/api/competitors/{competitor_id}")
def api_update_competitor(competitor_id: int, name: str, page_id: str = None):
    dedupe.update_competitor(competitor_id, name, page_id if page_id else name)
    return JSONResponse({"ok": True, "id": competitor_id, "name": name})


@app.delete("/api/competitors/{competitor_id}")
def api_delete_competitor(competitor_id: int):
    dedupe.delete_competitor(competitor_id)
    return JSONResponse({"ok": True, "id": competitor_id})
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