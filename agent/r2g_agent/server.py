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
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, agent_loop, batch, config, frontend, housekeeping, knowledge, tools, platforms
from . import project as rtl_project
from .jobs import JobStore
from .llm import make_adapter
from .paths import PKG_DIR, r2g_home
from .pipeline import STAGE_LABEL, STAGES, Pipeline
from .deletion import DeletionManager

STATE: dict = {}


@asynccontextmanager
async def _lifespan(_app):
    if not STATE:
        init_runtime(resume=True)      # the long-lived process resumes unfinished designs
    STATE['deletions'].start()
    yield
    STATE['deletions'].close()
    if tools.RT.pipeline:
        tools.RT.pipeline.shutdown()


app = FastAPI(title="r2g agent", version=__version__, lifespan=_lifespan)


def init_runtime(cfg: config.Config | None = None, resume: bool = False) -> None:
    cfg = cfg or config.load()
    store = JobStore()
    tools.RT.store = store
    tools.RT.pipeline = Pipeline(store, workers=cfg.workers, cores_per_design=cfg.cores_per_design,
                                 ingest=cfg.ingest, min_free_gb=cfg.min_free_gb,
                                 keep_intermediate=cfg.keep_intermediate)
    tools.RT.pipeline.recover(resume=resume)
    tools.RT.platform = cfg.platform
    STATE.update(cfg=cfg, sessions=agent_loop.SessionStore())
    STATE['deletions'] = DeletionManager(store, STATE['sessions'], tools.RT.pipeline)


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
    for k in ("provider", "model", "base_url", "api_key_env", "protocol"):
        if k in m:
            setattr(cfg.model, k, m[k] or "")
    for k in ("temperature", "max_tokens"):
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
    if not STATE['sessions'].exists(sid):
        raise HTTPException(404, '对话不存在或已删除')
    return {"id": sid, "messages": STATE["sessions"].load(sid)}


@app.post("/api/chat")
async def chat(req: Request):
    body = await req.json()
    sid = body.get("session_id") or STATE["sessions"].create()
    if not STATE['sessions'].exists(sid) or STATE['sessions'].is_deleting(sid):
        raise HTTPException(409, '对话正在删除或已删除，请新建对话')
    text = (body.get("message") or "").strip()
    if not text:
        raise HTTPException(400, "empty message")
    try:
        adapter = make_adapter(STATE["cfg"].model)
    except ValueError as e:
        def err():
            yield _sse({"type": "session", "session_id": sid})
            yield _sse({"type": "error", "message": str(e)})
        return StreamingResponse(err(), media_type="text/event-stream")

    def gen():
        yield _sse({"type": "session", "session_id": sid})
        for ev in agent_loop.run_turn(adapter, STATE["sessions"], sid, text):
            yield _sse(ev)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- jobs -------------------------------------------------------------------
@app.delete('/api/jobs/{target}')
async def delete_job(target: str, req: Request):
    return await _request_delete('job', target, req)


@app.delete('/api/sessions/{target}')
async def delete_session(target: str, req: Request):
    return await _request_delete('session', target, req)


async def _request_delete(kind: str, target: str, req: Request):
    try:
        body = await req.json()
    except ValueError:
        raise HTTPException(400, '请先确认删除及生成数据清理')
    if not isinstance(body, dict) or body.get('confirm') is not True:
        raise HTTPException(400, '请先确认删除及生成数据清理')
    try:
        result = STATE['deletions'].request(kind, target)
    except KeyError:
        raise HTTPException(404, '任务或对话不存在')
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    STATE['deletions'].start()
    return JSONResponse(result, status_code=200 if result['status'] == 'done' else 202)


@app.get('/api/deletions/{kind}/{target}')
def deletion_status(kind: str, target: str):
    result = tools.RT.store.deletion(kind, target)
    if not result:
        raise HTTPException(404, '尚未请求删除')
    return result


@app.get("/api/examples")
def examples():
    return tools.list_examples()


@app.get("/api/jobs")
def jobs():
    return tools.RT.store.jobs(50)


@app.post("/api/jobs")
async def create_job(req: Request):
    body = await req.json()
    r = tools.start_job(body.get("designs") or [], platform=body.get("platform", "nangate45"),
                        output=body.get("output", "graph"), title=body.get("title", ""),
                        params=body.get("params") or None)
    if "error" in r:
        raise HTTPException(400, r["error"])
    return r


@app.get("/api/jobs/{jid}")
def job(jid: str):
    j = tools.RT.store.job(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j


@app.get("/api/jobs/{jid}/events")
def job_events(jid: str, after: int = 0, tail: int = 0):
    return tools.RT.store.events(jid, after=after, tail=min(max(tail, 0), 2000))


@app.get("/api/platforms")
def available_platforms():
    return {"platforms": [{"id": name, "label": platforms.LABELS[name]} for name in platforms.PLATFORMS]}


@app.get("/api/params")
def params(platform: str = "nangate45"):
    try:
        return tools.flow_params(platform)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/batch/scan")
async def batch_scan(req: Request):
    """A folder of design folders (an upload, or a path on this machine) -> candidates."""
    body = await req.json()
    try:
        return batch.scan(Path(body.get("path") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/jobs/{jid}/cancel")
def cancel(jid: str):
    result = tools.cancel_job(jid)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


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


@app.get('/api/jobs/{jid}/designs/{name}/stage-views')
def stage_views(jid: str, name: str, version: str | None = None):
    from . import stage_views as views
    if version not in (None, 'final'):
        raise HTTPException(400, 'unknown stage view version')
    project = _design_project(jid, name)
    rows = views.views(project, tools.RT.store.design(jid, name), version=version)
    for row in rows:
        row.pop('image', None)
        row.pop('kind', None)
    return {'stages': rows}


@app.get('/api/jobs/{jid}/designs/{name}/stage-views/{stage}')
def stage_view_image(jid: str, name: str, stage: str, version: str | None = None):
    from . import stage_views as views
    from .lifecycle import resource_lock
    if version not in (None, 'final'):
        raise HTTPException(400, 'unknown stage view version')
    if stage not in views.IMAGE_STAGES:
        raise HTTPException(404, 'unknown stage')
    with resource_lock('job', jid):
        project = _design_project(jid, name)
        item = next((r for r in views.views(project, tools.RT.store.design(jid, name), version=version) if r['stage'] == stage), None)
        if item is None:
            raise HTTPException(404, 'no snapshot for this stage')
        if not item['available']:
            raise HTTPException(404, item['caption'])
        try:
            data, media = views.image(project, item)
        except (OSError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc
    return Response(data, media_type=media, headers={'Cache-Control': 'private, max-age=30',
                    'Content-Security-Policy': "default-src 'none'; sandbox", 'X-Content-Type-Options': 'nosniff'})


@app.get("/api/jobs/{jid}/designs/{name}/log/{stage}")
def log(jid: str, name: str, stage: str, lines: int = 200):
    return tools.read_log(jid, name, stage, lines)


@app.get("/api/jobs/{jid}/designs/{name}/reports")
def reports(jid: str, name: str):
    p = _design_project(jid, name) / "reports"
    out = {}
    for f in ("drc.json", "lvs.json", "ppa.json", "graph_dataset.json"):
        try:
            out[f[:-5]] = json.loads((p / f).read_text())
        except (OSError, ValueError):
            pass
    v = p / "verify.json"
    if v.is_file():
        res = json.loads(v.read_text()).get("results") or []
        out["verify"] = {"passed": sum(1 for r in res if r.get("ok")), "total": len(res)}
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
