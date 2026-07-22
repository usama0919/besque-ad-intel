"""Besque Ad Intelligence - Web Dashboard.
Read-only view + approve/reject + run trigger. Uses existing pipeline/db.
"""
import os
import threading
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

load_dotenv()
from src import dedupe

app = FastAPI(title="Besque Ad Intelligence")

# Serve the saved ad images
ASSET_DIR = Path("assets")
ASSET_DIR.mkdir(exist_ok=True)
app.mount("/assets", StaticFiles(directory="assets"), name="assets")

templates = Jinja2Templates(directory="templates")

_run_status = {"running": False, "last_summary": None, "stop_requested": False}


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
            "original_image": "/" + img.replace("\\", "/") if img else "",
            "draft_image": "/" + draft.replace("\\", "/") if draft else "",
            "blueprint": r.get("blueprint") or {},
            "copy": r.get("generated_copy") or {},
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
def api_decision(ad_id: str, decision: str):
    if decision not in ("approve", "reject"):
        return JSONResponse({"ok": False, "error": "bad decision"}, status_code=400)
    dedupe.record_decision(ad_id, decision)
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
def api_run(n: int = 2, competitor_id: int = None):
    if _run_status["running"]:
        return JSONResponse({"ok": False, "error": "already running"})
    _run_status["running"] = True
    _run_status["last_summary"] = None
    _run_status["stop_requested"] = False
    threading.Thread(target=_run_pipeline_bg, args=(n, competitor_id), daemon=True).start()
    return JSONResponse({"ok": True, "started": True})


@app.post("/api/run/stop")
def api_run_stop():
    _run_status["stop_requested"] = True
    return JSONResponse({"ok": True})


@app.get("/api/run/status")
def api_run_status():
    return JSONResponse(_run_status)

@app.get("/api/competitors")
def api_competitors():
    dedupe.init_competitors()
    rows = dedupe.get_competitors()
    return JSONResponse([{"id": r["id"], "name": r["name"], "page_id": r["page_id"]} for r in rows])


@app.post("/api/competitors")
def api_add_competitor(name: str):
    """Append a new competitor to the watchlist table. Never overwrites existing rows."""
    dedupe.init_competitors()
    new_id = dedupe.add_competitor(name, name)
    return JSONResponse({"ok": True, "id": new_id, "name": name, "page_id": name})


@app.put("/api/competitors/{competitor_id}")
def api_update_competitor(competitor_id: int, name: str):
    dedupe.update_competitor(competitor_id, name, name)
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