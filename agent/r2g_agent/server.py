"""Local web service: the API the UI talks to, plus the built UI itself.

Started by `r2g ui`. Binds to 127.0.0.1 by default; reach it from another
machine through an SSH tunnel rather than exposing it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, agent_loop, batch, config, frontend, housekeeping, knowledge, tools
from . import project as rtl_project
from .jobs import JobStore
from .llm import make_adapter
from .paths import PKG_DIR, r2g_home
from .pipeline import STAGE_LABEL, STAGES, Pipeline
from .tool_outputs import ToolOutputError
from .turns import TurnConflict, TERMINAL
from .event_bridge import BridgeWorker
from .repair_api import router as repair_router, install as install_repairs

STATE: dict = {}
_bridge: BridgeWorker | None = None


@asynccontextmanager
async def _lifespan(_app):
    if not STATE:
        init_runtime(resume=True)      # the long-lived process resumes unfinished designs
    yield
    if STATE.get("bridge"):
        STATE["bridge"].stop()
    if tools.RT.pipeline:
        tools.RT.pipeline.shutdown()


app = FastAPI(title="r2g agent", version=__version__, lifespan=_lifespan)
app.include_router(repair_router)


def init_runtime(cfg: config.Config | None = None, resume: bool = False, *, model_reviews: bool = True) -> None:
    global _bridge
    if _bridge:
        _bridge.stop()
    cfg = cfg or config.load()
    store = JobStore()
    tools.RT.store = store
    tools.RT.pipeline = Pipeline(store, workers=cfg.workers, cores_per_design=cfg.cores_per_design,
                                 ingest=cfg.ingest, min_free_gb=cfg.min_free_gb,
                                 keep_intermediate=cfg.keep_intermediate)
    tools.RT.pipeline.recover(resume=resume)
    tools.RT.platform = cfg.platform
    sessions = agent_loop.SessionStore()
    from .execution import HostExecutor
    _bridge = BridgeWorker(sessions, store, lambda: make_adapter(cfg.model), model_reviews=model_reviews)
    STATE.update(cfg=cfg, sessions=sessions, bridge=_bridge, executor=HostExecutor(sessions, store))
    install_repairs(STATE)
    _bridge.start()


def _sse(obj: dict) -> str:
    return "data: %s\n\n" % json.dumps(obj, ensure_ascii=False, default=str)


# --- meta & config ----------------------------------------------------------
@app.get("/api/health")
def health():
    return {"version": __version__, "stages": [{"id": s, "label": STAGE_LABEL[s]} for s in STAGES],
            "config": config.public_view(STATE["cfg"]),
            "knowledge": dict(knowledge.summary(), sync=getattr(tools.RT.pipeline, "knowledge", None)),
            "machine": {"threads": os.cpu_count(), "free_gb": round(housekeeping.free_gb(), 1)}}


@app.get("/api/config")
def get_config():
    return config.public_view(STATE["cfg"])


@app.post("/api/config")
async def set_config(req: Request):
    body = await req.json()
    cfg = STATE["cfg"]
    m = body.get("model") or {}
    for field, low, high in (("context_max_chars", 8192, 1000000), ("context_recent_turns", 1, 8)):
        if field in m and (type(m[field]) is not int or not low <= m[field] <= high):
            raise HTTPException(422, "%s must be an integer between %d and %d" % (field, low, high))
    for k in ("provider", "model", "base_url", "api_key_env", "protocol"):
        if k in m:
            setattr(cfg.model, k, m[k] or "")
    for k in ("temperature", "max_tokens", "context_max_chars", "context_recent_turns"):
        if k in m:
            setattr(cfg.model, k, m[k])
    if "thinking" in m:
        cfg.model.thinking = bool(m["thinking"])
    if m.get("api_key"):
        config.store_api_key(cfg.model.provider, m["api_key"])
    for k in ("workers", "cores_per_design", "min_free_gb"):
        if k in body:
            setattr(cfg, k, int(body[k]))
    if "keep_intermediate" in body:
        cfg.keep_intermediate = bool(body["keep_intermediate"])
    config.save(cfg)
    pl = tools.RT.pipeline
    if pl:                       # take effect now, not at the next restart
        pl.workers, pl.cores = max(1, cfg.workers), cfg.cores_per_design
        pl.min_free_gb, pl.keep_intermediate = cfg.min_free_gb, cfg.keep_intermediate
    return config.public_view(cfg)


@app.post("/api/config/test")
def test_model():
    """One tiny request with one tool: proves the endpoint, the key, and tool calling."""
    probe = [{"name": "ping", "description": "Reply by calling this tool with ok=true.",
              "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}]
    t0 = time.time()
    try:
        ad = make_adapter(STATE["cfg"].model)
    except ValueError as e:
        return {"ok": False, "message": str(e)}
    text, called, err = "", False, None
    for ev in ad.stream([{"role": "user", "content": "Call the ping tool with ok=true."}], probe, "Use tools."):
        if ev["type"] == "text":
            text += ev["delta"]
        elif ev["type"] == "tool_call" and ev["name"] == "ping":
            called = True
        elif ev["type"] == "error":
            err = ev["message"]
    ms = int((time.time() - t0) * 1000)
    if err:
        return {"ok": False, "message": err, "latency_ms": ms}
    return {"ok": called, "tool_calling": called, "latency_ms": ms,
            "message": "连接正常，支持工具调用" if called else "能连通，但模型没有调用工具——该模型可能不支持函数调用"}


# --- chat -------------------------------------------------------------------
@app.get("/api/sessions")
def sessions():
    return STATE["sessions"].list()


@app.get("/api/sessions/{sid}")
def session(sid: str):
    return {"id": sid, "messages": STATE["sessions"].load(sid),
            "model_calls": STATE["sessions"].model_calls(sid), "turns": STATE["sessions"].turns.list(sid)}


@app.get("/api/sessions/{sid}/tool-outputs/{artifact_id}")
def tool_output(sid: str, artifact_id: str, offset: int = Query(0, ge=0),
                limit: int = Query(4000, ge=1, le=4000), query: str = Query("", max_length=256)):
    try:
        return STATE["sessions"].outputs.read(sid, artifact_id, offset, limit, query)
    except ToolOutputError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/chat")
async def chat(req: Request):
    body = await req.json()
    if not isinstance(body, dict) or not isinstance(body.get("message"), str) or not body["message"].strip():
        raise HTTPException(400, "message must be a nonempty string")
    for name in ("request_id", "session_id", "job_id", "grant_id"):
        if body.get(name) is not None and (not isinstance(body[name], str) or not 1 <= len(body[name]) <= 200):
            raise HTTPException(400, name + " must contain 1–200 characters")
    store = STATE["sessions"]
    key = body.get("request_id") or uuid.uuid4().hex
    previous = store.turns.by_key(key) if isinstance(key, str) else None
    sid = body.get("session_id") or (previous["session_id"] if previous else store.create())
    text = body["message"].strip()
    try:
        job_id = body.get("job_id") or store.turns.latest_job(sid)
        if previous and not body.get("job_id"):
            job_id = previous["payload"].get("job_id")
        if body.get("job_id"):
            with store.turns.connection() as c:
                if not c.execute("SELECT 1 FROM job_links WHERE session_id=? AND job_id=?", (sid, job_id)).fetchone():
                    raise ValueError("job is not linked to this session")
        grant_id = body.get("grant_id")
        if grant_id:
            from . import repairs
            grant_id = repairs.validate_chat_grant(store.path, grant_id, sid, job_id)
        # On a retry keep the original host job binding even if a later turn
        # linked another job; user payload changes still conflict below.
        turn = STATE["bridge"].enqueue(sid, text, request_id=key, job_id=job_id, grant_id=grant_id)
    except TurnConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return StreamingResponse(_turn_stream(turn["id"], req), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _turn_stream(tid: str, request: Request, after: int = 0):
    turns = STATE["sessions"].turns
    turn = turns.get(tid)
    yield _sse({"type": "session", "session_id": turn["session_id"], "turn_id": tid})
    while True:
        events = turns.events(tid, after)
        for event in events:
            after = event["event_id"]
            yield "id: %d\n%s" % (after, _sse(event))
        turn = turns.get(tid)
        if turn["status"] in TERMINAL and not turns.events(tid, after, limit=1):
            return
        if await request.is_disconnected():
            return  # The worker continues and commits events independently.
        await asyncio.sleep(0.1)


@app.get("/api/turns/{tid}")
def turn_detail(tid: str):
    turn = STATE["sessions"].turns.get(tid)
    if not turn:
        raise HTTPException(404, "unknown turn")
    return {**turn, "actions": STATE["sessions"].turns.actions(tid)}


@app.get("/api/turns/{tid}/events")
def turn_events(tid: str, after: int = Query(0, ge=0)):
    turns = STATE["sessions"].turns
    turn = turns.get(tid)
    if not turn:
        raise HTTPException(404, "unknown turn")
    return {"turn": turn, "events": turns.events(tid, after)}


@app.get("/api/turns/{tid}/stream")
def turn_stream(tid: str, req: Request, after: int = Query(0, ge=0)):
    if not STATE["sessions"].turns.get(tid):
        raise HTTPException(404, "unknown turn")
    return StreamingResponse(_turn_stream(tid, req, after), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- jobs -------------------------------------------------------------------
@app.get("/api/examples")
def examples():
    return tools.list_examples()


@app.get("/api/jobs")
def jobs():
    return tools.RT.store.jobs(50)


@app.post("/api/jobs")
async def create_job(req: Request):
    body = await req.json()
    return await asyncio.to_thread(_execute_direct, "start_job", body)


@app.post("/api/jobs/batch")
async def create_batch(req: Request):
    return await asyncio.to_thread(_execute_direct, "start_batch", await req.json())


def _execute_direct(name: str, body: dict, *, job_id: str | None = None):
    from .execution import response
    if not isinstance(body, dict):
        raise HTTPException(400, "request must be an object")
    try:
        executor = STATE["executor"]
        context = executor.host_context("web", request_id=body.get("request_id"),
                                        session_id=body.get("session_id"), job_id=job_id)
        arguments = {k: v for k, v in body.items() if k not in {"request_id", "session_id"}}
        if job_id:
            if "job_id" in arguments and arguments["job_id"] != job_id:
                raise ValueError("job_id does not match the route")
            arguments["job_id"] = job_id
        receipt = executor.execute(name, arguments, context)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result = response(receipt)
    if receipt["status"] in {"queued", "running"}:
        return JSONResponse(result, status_code=202)
    if "error" in result:
        code = 409 if receipt["status"] in {"conflict", "recovery_required"} else 400
        return JSONResponse(result, status_code=code)
    return result


@app.get("/api/jobs/{jid}")
def job(jid: str):
    j = tools.RT.store.job(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j


@app.get("/api/jobs/{jid}/events")
def job_events(jid: str, after: int = 0, tail: int = 0):
    return tools.RT.store.events(jid, after=after, tail=min(max(tail, 0), 2000))


@app.get("/api/params")
def params(platform: str = "nangate45"):
    return tools.flow_params(platform)


@app.post("/api/batch/scan")
async def batch_scan(req: Request):
    """A folder of design folders (an upload, or a path on this machine) -> candidates."""
    body = await req.json()
    try:
        return batch.scan(Path(body.get("path") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/jobs/{jid}/cancel")
async def cancel(jid: str, req: Request):
    if tools.RT.store.job(jid) is None:
        raise HTTPException(404, "no such job")
    body = await req.json() if await req.body() else {}
    return await asyncio.to_thread(_execute_direct, "cancel_job", body, job_id=jid)


@app.get("/api/jobs/{jid}/designs/{name}/download")
def design_download(jid: str, name: str):
    from .package import package_design
    try:
        p = package_design(tools.RT.store, jid, name)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return FileResponse(p, filename=p.name, media_type="application/gzip")


@app.post("/api/jobs/{jid}/package")
def package_now(jid: str):
    """Package the designs that have passed so far, while the job is still running."""
    from .package import package_job
    j = tools.RT.store.job(jid)
    if not j:
        raise HTTPException(404, "no such job")
    p = package_job(tools.RT.store, jid, partial=True)
    return {"path": str(p), "url": "/api/jobs/%s/download?partial=1" % jid,
            "designs": sum(1 for d in j["designs"] if d["status"] == "passed")}


@app.get("/api/jobs/{jid}/download")
def download(jid: str, partial: int = 0):
    if partial:
        p = r2g_home() / "datasets" / ("r2g_dataset_%s_partial.tar.gz" % jid)
        if not p.is_file():
            raise HTTPException(404, "not packaged yet")
        return FileResponse(p, filename=p.name, media_type="application/gzip")
    info = tools.dataset_info(jid)
    if not info.get("path"):
        raise HTTPException(404, info.get("note") or "no dataset")
    return FileResponse(info["path"], filename=Path(info["path"]).name, media_type="application/gzip")


def _design_project(jid: str, name: str) -> Path:
    d = tools.RT.store.design(jid, name)
    if not d:
        raise HTTPException(404, "no such design")
    return Path(d["project"])


@app.get("/api/jobs/{jid}/designs/{name}/layout.png")
def layout(jid: str, name: str):
    p = _design_project(jid, name) / "reports" / "layout.png"
    if not p.is_file():
        raise HTTPException(404, "no preview yet")
    return FileResponse(p, media_type="image/png")


@app.get("/api/jobs/{jid}/designs/{name}/log/{stage}")
def log(jid: str, name: str, stage: str, lines: int = 200):
    return tools.read_log(jid, name, stage, lines)


@app.get("/api/jobs/{jid}/designs/{name}/reports")
def reports(jid: str, name: str):
    p = _design_project(jid, name) / "reports"
    out = {}
    for f in ("drc.json", "lvs.json", "rcx.json", "ppa.json", "graph_dataset.json", "verification_review.json"):
        try:
            out["validation" if f == "verification_review.json" else f[:-5]] = json.loads((p / f).read_text())
        except (OSError, ValueError):
            pass
    v = p / "verify.json"
    if v.is_file():
        res = json.loads(v.read_text()).get("results") or []
        out["verify"] = {"passed": sum(1 for r in res if r.get("ok")), "total": len(res)}
    from .validation import review
    out["validation"] = review(p.parent)  # Fresh read-only review; saved PASS is not its own evidence.
    return out


@app.get("/api/jobs/{jid}/designs/{name}/images")
def images(jid: str, name: str):
    """Image-dataset previews and channel metadata of one design."""
    d = _design_project(jid, name) / "images"
    meta = d / "image_meta.json"
    if not meta.is_file():
        return {"available": False}
    m = json.loads(meta.read_text())
    pngs = sorted(p.name for p in (d / "png").glob("*.png"))
    return {"available": True, "shape": m.get("shape"), "feature_stage": m.get("feature_stage"),
            "features": m.get("features"), "labels": m.get("labels"),
            "gcell_step_um": m.get("gcell_step_um"), "pngs": pngs}


@app.get("/api/jobs/{jid}/designs/{name}/images/{fname}")
def image_png(jid: str, name: str, fname: str):
    if not re.fullmatch(r"[a-z_]+\.png", fname):
        raise HTTPException(400, "bad name")
    p = _design_project(jid, name) / "images" / "png" / fname
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    """RTL files from the browser -> a folder start_job can take as a path."""
    dest = r2g_home() / "uploads" / (time.strftime("%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6])
    dest.mkdir(parents=True)
    saved, skipped = [], []
    for f in files:
        rel = Path(*[re.sub(r"[^A-Za-z0-9_.\-]", "_", part) for part in Path(f.filename or "x.v").parts
                     if part not in ("..", "/", "")])
        # HDL plus the data files $readmem reads (frontend.DATA_SUFFIXES), so a
        # .txt/.mif ROM image is not dropped here and then reported missing
        if rel.suffix.lower() not in rtl_project.HDL_SUFFIXES + frontend.DATA_SUFFIXES:
            skipped.append(str(rel))
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as fh:
            shutil.copyfileobj(f.file, fh)
        saved.append(str(rel))
    if not saved:
        shutil.rmtree(dest)
        raise HTTPException(400, "no Verilog/SystemVerilog files in the upload")
    return {"path": str(dest), "files": saved, "skipped": skipped}


# --- UI ---------------------------------------------------------------------
DIST = PKG_DIR / "web" / "dist"
if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{full_path:path}")
def spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404)
    f = DIST / full_path
    if full_path and f.is_file():
        return FileResponse(f)
    if (DIST / "index.html").is_file():
        return FileResponse(DIST / "index.html")
    return JSONResponse({"message": "r2g agent API is running; the web UI is not built yet "
                                    "(cd webui && npm install && npm run build)"})
