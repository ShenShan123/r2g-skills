"""The tools a model may call. Each is a plain function returning JSON-able data.

The same registry backs the built-in agent loop and the MCP server, so any
MCP client (Claude Code, Cursor, Cherry Studio, ...) gets exactly the tools the
web UI's agent has.

Tools report; they never let the caller assert an outcome. There is no tool to
mark a design passed -- a design passes only inside the pipeline, when DRC and
LVS are clean and the dataset verifier is green.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from contextvars import ContextVar
from pathlib import Path

from . import diagnosis, knowledge, platforms
from . import project as project_mod
from .jobs import JobStore
from .paths import examples_dir, repo_root, signoff_scripts, skills_dir
from .pipeline import STAGE_LABEL, Pipeline


class Runtime:
    """Process-wide handles the tools use."""
    store: JobStore | None = None
    pipeline: Pipeline | None = None
    platform: str = "nangate45"


RT = Runtime()
CURRENT_SESSION = ContextVar('r2g_session', default=None)
REGISTRY: dict[str, dict] = {}

# Replay (measuring another model's orchestration of finished R2G jobs without re-running EDA):
# R2G_REPLAY_MAP names a JSON {design name: {"job_id", "platform", "output"}} of recorded jobs in this
# server's store. start_job then attaches the design's recorded job instead of submitting a pipeline
# (the pipeline makes no model calls, so every model call of a run is in the chats), and job_status
# answers with the job as it looked when submitted until the measurement runner first polls it over
# HTTP -- the moment a real run moves from the submission chat to the explanation chat.
# R2G_REPLAY_LOG records every attach, argument divergence and early peek. Off when unset.
_REPLAY: dict | None = None
REPLAY_PENDING: set = set()


def replay_map() -> dict:
    global _REPLAY
    if _REPLAY is None:
        p = os.environ.get("R2G_REPLAY_MAP")
        _REPLAY = json.loads(Path(p).read_text()) if p else {}
    return _REPLAY


def replay_platforms() -> list:
    """Process platforms the tool schemas offer: the supported ones, plus (replay only) any platform a
    recorded job in R2G_REPLAY_MAP ran on (asap7's recipe stage is replayed from recorded runs; R2G's
    own flow does not run it)."""
    extra = sorted({r.get("platform") for r in replay_map().values()} - set(platforms.PLATFORMS) - {None})
    return list(platforms.PLATFORMS) + extra


def replay_log(rec: dict) -> None:
    p = os.environ.get("R2G_REPLAY_LOG")
    if p:
        with open(p, "a") as f:
            f.write(json.dumps({**rec, "session": CURRENT_SESSION.get()}) + "\n")


def tool(description: str, parameters: dict):
    def deco(fn):
        REGISTRY[fn.__name__] = {"fn": fn, "spec": {"name": fn.__name__, "description": description,
                                                     "parameters": parameters}}
        return fn
    return deco


def specs() -> list[dict]:
    return [t["spec"] for t in REGISTRY.values()]


def call(name: str, arguments: dict) -> dict:
    if name not in REGISTRY:
        return {"error": "unknown tool %s" % name}
    if REPLAY_PENDING and name != "job_status" and (arguments or {}).get("job_id") in REPLAY_PENDING:
        replay_log({"event": "early_peek", "tool": name, "job_id": arguments["job_id"]})
    try:
        return REGISTRY[name]["fn"](**(arguments or {}))
    except TypeError as e:
        return {"error": "bad arguments: %s" % e}
    except Exception as e:                                     # noqa: BLE001
        return {"error": str(e)}


def _obj(props: dict, required: list | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


# ---------------------------------------------------------------------------
@tool("List the built-in example RTL designs that can be run immediately (good for a quick demo).",
      _obj({}))
def list_examples() -> dict:
    out = []
    for d in sorted(examples_dir().iterdir()):
        if d.is_dir():
            meta = json.loads((d / "design.json").read_text()) if (d / "design.json").is_file() else {}
            out.append({"name": d.name, "top": meta.get("top"), "description": meta.get("description", "")})
    return {"examples": out}


@tool("Start a background job that takes RTL designs through synthesis, place-and-route, DRC/LVS "
      "sign-off, automatic repair, and dataset construction (graph or image). Returns a job_id immediately; "
      "the job runs for minutes to hours. Each design is either a built-in example name or a "
      "directory/file path of Verilog sources on this machine.",
      _obj({"designs": {"type": "array", "description": "designs to run",
                        "items": {"type": "object", "properties": {
                            "example": {"type": "string", "description": "built-in example name"},
                            "path": {"type": "string", "description": "RTL directory or file path"},
                            "top": {"type": "string", "description": "top module (optional; inferred if omitted)"},
                            "name": {"type": "string", "description": "display name (optional)"}}}},
            "platform": {"type": "string", "enum": replay_platforms(), "description": "process platform"},
            "output": {"type": "string", "enum": ["graph", "image", "signoff"],
                       "description": "dataset type: 'graph' = PyG graph datasets (b..f views + netlist graph); "
                                      "'image' = CircuitNet-style GCell feature maps (features from the placement "
                                      "layout, congestion/IR-drop label maps from the routed one); 'signoff' = "
                                      "stop once DRC and LVS are clean, no dataset. Ask the user if "
                                      "they did not say which."},
            "title": {"type": "string"}, "params": {"type": "object", "description": "flow parameters overriding r2g's measured defaults (omit to use the defaults; call flow_params first). Keys: period_ns, period_tiers, utilization, area_allowance, pdn_floor_um, die_side_um, io_pct, setup_uncertainty_pct, hold_uncertainty_ns, place_density_lb_addon"}}, ["designs"]))
def start_job(designs: list, platform: str = "nangate45", output: str = "graph", title: str = "",
              params: dict | None = None) -> dict:
    if not isinstance(designs, list):
        return {"error": "designs must be a list of {example|path, top, name} objects"}
    if len(designs) > 5000:
        return {"error": "at most 5000 designs per job (got %d)" % len(designs)}
    sources = []
    for d in designs:
        if not isinstance(d, dict):
            return {"error": "each design must be an object with 'example' or 'path'"}
        bad = [k for k in ("example", "path", "top", "name") if d.get(k) is not None and not isinstance(d[k], str)]
        if bad:
            return {"error": "design field %s must be a string" % bad[0]}
        top = (d.get("top") or "").strip() or None
        if top and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top):
            return {"error": "not a valid Verilog module name for top: %r" % top}
        if d.get("example"):
            if not re.fullmatch(r"[A-Za-z0-9_]+", d["example"]) or not (examples_dir() / d["example"]).is_dir():
                return {"error": "no such example: %s" % d["example"]}
            p = examples_dir() / d["example"]
            sources.append({"name": d.get("name") or d["example"], "path": str(p), "top": top})
        elif d.get("path"):
            p = Path(d["path"]).expanduser()
            if not p.is_absolute():
                return {"error": "path must be absolute: %s" % d["path"]}
            if not p.exists():
                return {"error": "path does not exist: %s" % p}
            try:
                # (replay attaches a recorded job by name and reads no RTL)
                if not replay_map() and not project_mod.probe_hdl(p):
                    return {"error": "no Verilog/SystemVerilog (.v/.sv) files under %s" % p}
            except ValueError as e:
                return {"error": str(e)}
            if not replay_map():
                # the top module is settled before the design takes a queue slot: an undecidable top used to
                # surface minutes later as "内部错误：cannot infer the top module", a wrong one as a synthesis failure
                err = _check_top(p, top, check_named=len(designs) <= 200)
                if err:
                    return {"error": err}
            sources.append({"name": d.get("name") or p.stem, "path": str(p.resolve()), "top": top})
        else:
            return {"error": "each design needs 'example' or 'path'"}
    if not sources:
        return {"error": "no designs given"}
    if replay_map():
        # a recorded job is found by design name, or by its source directory's name (the measurement copies each
        # design to inputs/<name>): a model that picks its own design name would get a real job in a live run
        recs = [replay_map().get(s["name"]) or replay_map().get(Path(s["path"]).name) for s in sources]
        if len(sources) != 1 or not recs[0]:
            replay_log({"event": "no_recorded_job", "designs": [s["name"] for s in sources]})
            return {"error": "replay: no recorded job for %s" % [s["name"] for s in sources]}
        rec = recs[0]
        div = {k: [v, rec.get(k)] for k, v in (("platform", platform), ("output", output)) if rec.get(k) not in (None, v)}
        if sources[0]["name"] not in replay_map():
            div["name"] = [sources[0]["name"], Path(sources[0]["path"]).name]
        if params:
            div["params"] = [params, None]
        replay_log({"event": "attach", "design": sources[0]["name"], "job_id": rec["job_id"], "divergence": div})
        REPLAY_PENDING.add(rec["job_id"])
        return {"job_id": rec["job_id"], "designs": [s["name"] for s in sources],
                "note": "running in background; use job_status to follow progress"}
    try:
        jid = RT.pipeline.submit(sources, platform=platform, title=title, output=output, params=params,
                                 session_id=CURRENT_SESSION.get())
    except ValueError as e:
        return {"error": str(e)}
    return {"job_id": jid, "designs": [s["name"] for s in sources],
            "note": "running in background; use job_status to follow progress"}


@tool("The flow parameters r2g uses by default (from the selected platform frozen baseline: clock period tiers "
      "by cell count, 50% utilization, area allowance, power-grid minimum, SDC margins) and what each "
      "may be set to. Use it to explain the defaults, or before passing `params` to start_job/start_batch "
      "when the user wants their own (e.g. a fixed clock period).",
      _obj({"platform": {"type": "string", "enum": replay_platforms()}}))
def flow_params(platform: str = "nangate45") -> dict:
    if platform not in platforms.PLATFORMS and platform in replay_platforms():
        replay_log({"event": "flow_params_replay_platform", "platform": platform})
        return {"platform": platform, "defaults": None,
                "note": "replay: this platform's recorded jobs ran with its frozen baseline parameters; start_job uses them"}
    from . import profile
    return profile.describe(platform)


@tool("Scan a folder whose subfolders are designs (one design per subfolder; a folder with only HDL "
      "files is one design). Lists each with its top module, or why its top could not be decided "
      "(those are skipped). Use before start_batch to tell the user what will run.",
      _obj({"path": {"type": "string", "description": "folder on this machine"}}, ["path"]))
def scan_batch(path: str) -> dict:
    from . import batch
    try:
        r = batch.scan(Path(path))
    except ValueError as e:
        return {"error": str(e)}
    bad = [d for d in r["designs"] if d["problem"]]
    return {"root": r["root"], "total": r["total"], "will_run": r["ok"], "skipped": r["skipped"],
            "first": [{k: d[k] for k in ("name", "top", "files")} for d in r["designs"] if not d["problem"]][:20],
            "skipped_designs": [{"name": d["name"], "problem": d["problem"]} for d in bad][:30]}


@tool("Submit every design in a folder of design folders as ONE job (see scan_batch). For tens to "
      "thousands of designs; they queue under the machine-wide concurrency limit and survive a server "
      "restart. Designs whose top module cannot be decided are skipped and listed.",
      _obj({"path": {"type": "string"}, "output": {"type": "string", "enum": ["graph", "image"]},
            "platform": {"type": "string", "enum": replay_platforms()}, "title": {"type": "string"},
            "params": {"type": "object", "description": "flow parameters overriding r2g's measured defaults (omit to use the defaults; call flow_params first). Keys: period_ns, period_tiers, utilization, area_allowance, pdn_floor_um, die_side_um, io_pct, setup_uncertainty_pct, hold_uncertainty_ns, place_density_lb_addon"}}, ["path", "output"]))
def start_batch(path: str, output: str = "graph", platform: str = "nangate45", title: str = "",
                params: dict | None = None) -> dict:
    from . import batch
    try:
        r = batch.scan(Path(path))
    except ValueError as e:
        return {"error": str(e)}
    src = batch.sources(r)
    if not src:
        return {"error": "no design with a decidable top module under %s" % path}
    try:
        jid = RT.pipeline.submit(src, platform=platform, output=output, params=params,
                                 session_id=CURRENT_SESSION.get(),
                                 title=title or "批量 · %s · %d 个设计" % (Path(r["root"]).name, len(src)))
    except ValueError as e:
        return {"error": str(e)}
    return {"job_id": jid, "submitted": len(src), "skipped": r["skipped"],
            "skipped_designs": [{"name": d["name"], "problem": d["problem"]} for d in r["designs"] if d["problem"]][:30],
            "note": "queued in background; use job_status for counts"}


@tool("Progress of a job: overall status and, per design, the current stage and verdicts so far. "
      "A job with many designs is summarised: counts, failure reasons grouped, and the running ones.",
      _obj({"job_id": {"type": "string"}}, ["job_id"]))
def job_status(job_id: str) -> dict:
    j = RT.store.job(job_id)
    if not j:
        return {"error": "no such job"}
    if job_id in REPLAY_PENDING:
        # replay: the job as it looked right after submission (a real run sees it just starting)
        return {"job_id": job_id, "status": "running", "title": j["title"], "result": None,
                "designs": [{"name": d["name"], "status": "running", "stage": STAGE_LABEL.get("prepare", "prepare"),
                             "sizing": None, "signoff": None, "failed_stage": None, "reason": None}
                            for d in j["designs"]]}
    if len(j["designs"]) > 30:
        return _batch_summary(j)
    designs = []
    for d in j["designs"]:
        det = d.get("detail") or {}
        designs.append({"name": d["name"], "status": d["status"],
                        "stage": STAGE_LABEL.get(d["stage"], d["stage"]),
                        "sizing": {k: det.get("sizing", {}).get(k) for k in ("cells", "die_side_um", "period_ns")}
                        if det.get("sizing") else None,
                        "signoff": det.get("signoff"), "failed_stage": det.get("failed_stage"),
                        "reason": det.get("reason")})
    return {"job_id": job_id, "status": j["status"], "title": j["title"], "result": j.get("result"),
            "designs": designs}


def _batch_summary(j: dict) -> dict:
    counts, reasons, running = {}, {}, []
    for d in j["designs"]:
        counts[d["status"]] = counts.get(d["status"], 0) + 1
        det = d.get("detail") or {}
        if d["status"] == "failed":
            # file names and numbers out, so one yosys error across 2000 files is one group
            msg = re.sub(r"^[^：]*失败：", "", det.get("reason") or "")
            msg = re.sub(r"^[^\s：]+\.s?vh?[^：]*：", "", msg)
            key = "%s: %s" % (det.get("failed_stage"), re.sub(r"\d+", "N", msg)[:60])
            r = reasons.setdefault(key, {"count": 0, "examples": []})
            r["count"] += 1
            if len(r["examples"]) < 3:
                r["examples"].append(d["name"])
        elif d["status"] == "running":
            running.append({"name": d["name"], "stage": STAGE_LABEL.get(d["stage"], d["stage"])})
    return {"job_id": j["id"], "status": j["status"], "title": j["title"], "total": len(j["designs"]),
            "counts": counts, "failure_reasons": sorted(reasons.items(), key=lambda kv: -kv[1]["count"])[:10],
            "running": running[:20], "result": j.get("result"),
            "note": "use design_detail(job_id, name) for one design"}


@tool("List recent jobs with their status counts.", _obj({"limit": {"type": "integer"}}))
def list_jobs(limit: int = 10) -> dict:
    return {"jobs": [{"job_id": j["id"], "title": j["title"], "status": j["status"], "counts": j["counts"]}
                     for j in RT.store.jobs(limit)]}


@tool("Detailed record of one design in a job: sizing, sign-off verdicts, repair attempts and the "
      "most recent events. Use it to explain a failure.",
      _obj({"job_id": {"type": "string"}, "design": {"type": "string"}}, ["job_id", "design"]))
def design_detail(job_id: str, design: str) -> dict:
    d = RT.store.design(job_id, design)
    if not d:
        return {"error": "no such design in job"}
    evs = [e for e in RT.store.events(job_id, limit=2000) if e["design"] == design][-15:]
    return {"design": design, "status": d["status"], "stage": d["stage"], "detail": d["detail"],
            "events": [{"stage": e["stage"], "kind": e["kind"], "message": e["message"]} for e in evs]}


@tool("Read the tail of a stage log of one design (stages: synth, flow, signoff, repair, graph, verify).",
      _obj({"job_id": {"type": "string"}, "design": {"type": "string"},
            "stage": {"type": "string"}, "lines": {"type": "integer"}}, ["job_id", "design", "stage"]))
def read_log(job_id: str, design: str, stage: str, lines: int = 60) -> dict:
    d = RT.store.design(job_id, design)
    if not d:
        return {"error": "no such design in job"}
    p = Path(d["project"]) / "agent_logs" / ("%s.log" % re.sub(r"[^a-z]", "", stage))
    if not p.is_file():
        return {"error": "no log for stage %s" % stage}
    tail = p.read_text(errors="replace").splitlines()[-max(5, min(lines, 300)):]
    return {"stage": stage, "log_tail": "\n".join(tail)}


@tool("Ask the sign-off repair engine which fixes it would try for a design's DRC, LVS or routing "
      "problem, ranked by past success (read-only; does not change anything).",
      _obj({"job_id": {"type": "string"}, "design": {"type": "string"},
            "check": {"type": "string", "enum": ["drc", "lvs", "route", "timing"]}},
           ["job_id", "design", "check"]))
def diagnose(job_id: str, design: str, check: str) -> dict:
    d = RT.store.design(job_id, design)
    if not d:
        return {"error": "no such design in job"}
    r = subprocess.run(diagnosis.command(d["project"], check), cwd=repo_root(),
                       capture_output=True, text=True, timeout=300,
                       env={**os.environ, **knowledge.env()})
    if r.returncode:
        return {"error": "diagnosis failed", "stderr": r.stderr[-800:]}
    try:
        plan = json.loads(r.stdout)
    except ValueError:
        return {"error": "diagnosis produced no plan", "stderr": r.stderr[-800:]}
    return {"check": check, "plan": plan}


@tool("Read a design's RTL source as the job received it (the copy in the job's project), with line "
      "numbers, to point at the exact line of a synthesis error. Only files inside that design's rtl/ "
      "folder can be read.",
      _obj({"job_id": {"type": "string"}, "design": {"type": "string"},
            "file": {"type": "string", "description": "file name or path relative to rtl/; omit to list files"},
            "start": {"type": "integer"}, "end": {"type": "integer"}}, ["job_id", "design"]))
def read_rtl(job_id: str, design: str, file: str = "", start: int = 1, end: int = 200) -> dict:
    d = RT.store.design(job_id, design)
    if not d:
        return {"error": "no such design in job"}
    root = (Path(d["project"]) / "rtl").resolve()
    files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    if not file:
        return {"files": files}
    p = (root / file).resolve()
    if root not in p.parents or not p.is_file():          # no escaping the design's rtl/ folder
        return {"error": "not a file of this design", "files": files[:50]}
    lines = p.read_text(errors="replace").splitlines()
    start = max(1, start); end = min(len(lines), max(start, end), start + 400)
    return {"file": file, "total_lines": len(lines),
            "text": "\n".join("%5d  %s" % (i, lines[i - 1]) for i in range(start, end + 1))}


@tool("Look up the r2g knowledge base of known flow/sign-off failure patterns by keyword or error "
      "code (e.g. 'PDN-0185', 'antenna', 'PPL-0024'). Returns matching sections.",
      _obj({"query": {"type": "string"}}, ["query"]))
def knowledge_lookup(query: str) -> dict:
    doc = skills_dir() / "signoff-loop" / "references" / "failure-patterns.md"
    text = doc.read_text(errors="replace")
    secs = re.split(r"(?m)^(?=#{2,4} )", text)
    q = [w.lower() for w in re.split(r"\s+", query.strip()) if w]
    hits = []
    for s in secs:
        low = s.lower()
        score = sum(low.count(w) for w in q)
        if score and all(w in low for w in q):
            hits.append((score, s))
    hits.sort(key=lambda x: -x[0])
    return {"matches": [{"heading": s.splitlines()[0].lstrip("# "), "excerpt": s[:1500]} for _, s in hits[:3]],
            "total": len(hits)}


@tool("Search public GitHub repositories for RTL by keyword (e.g. 'uart', 'risc-v core', 'fft'). "
      "Returns candidate repositories ranked by rtl-acquire's quality score. Use only when the user "
      "asks you to find designs; then fetch_rtl one of them.",
      _obj({"keyword": {"type": "string"}, "count": {"type": "integer", "description": "1-30, default 10"}},
           ["keyword"]))
def search_rtl(keyword: str, count: int = 10) -> dict:
    from . import rtl_search
    try:
        return {"repos": rtl_search.search(keyword, max(1, min(int(count), 30)))}
    except Exception as e:                                      # noqa: BLE001
        return {"error": str(e)}


@tool("Shallow-clone one repository found by search_rtl (or given by the user) and list the designs in "
      "it: one per directory, with top-module candidates and any modules it uses but does not define. "
      "Only a self_contained design can be submitted as is: start_job with its `path` and a `top`. "
      "Always tell the user the repository's licence.",
      _obj({"url": {"type": "string", "description": "https://github.com/<owner>/<repo>"}}, ["url"]))
def fetch_rtl(url: str) -> dict:
    from . import rtl_search
    try:
        return rtl_search.fetch(url)
    except Exception as e:                                      # noqa: BLE001
        return {"error": str(e)}


@tool("Disk use of the user's r2g workspace: size per job, packaged datasets, free space. Read-only; "
      "to free space tell the user to run `r2g clean` (it lists first and deletes only with --yes).",
      _obj({}))
def disk_usage() -> dict:
    from . import housekeeping
    rep = housekeeping.disk_report(RT.store)
    rep["jobs"] = sorted(rep["jobs"], key=lambda r: -r["bytes"])[:20]
    return rep


@tool("Cancel a running job.", _obj({"job_id": {"type": "string"}}, ["job_id"]))
def cancel_job(job_id: str) -> dict:
    result = RT.pipeline.cancel(job_id)
    return {"job_id": job_id, **result} if result else {"error": "no such job"}


def _check_top(p: Path, top: str | None, check_named: bool = True) -> str | None:
    """None when the design's top module is decided, else the message for the user."""
    from . import batch
    try:
        if top is None:
            c = batch._candidate(p, p.name if p.is_dir() else p.stem)
            if c["problem"]:
                tops, _ = project_mod.module_candidates(project_mod.find_hdl(p))
                return "%s：%s。请为该设计指定顶层模块 top%s" % (
                    p.name, c["problem"], "（候选：%s）" % ", ".join(tops[:20]) if tops else "")
        elif check_named:
            _, defined = project_mod.module_candidates(project_mod.find_hdl(p))
            if defined and top not in defined:
                return "%s：RTL 中没有名为 %s 的模块（已定义：%s）" % (
                    p.name, top, ", ".join(defined[:20]) + ("…" if len(defined) > 20 else ""))
    except (ValueError, OSError) as e:
        return "%s：%s" % (p.name, e)
    return None


@tool("Run designs of an earlier job again as a NEW job with the same platform, output and parameters "
      "(the old job and its results stay as they are). By default every failed or cancelled design; "
      "or name the designs to re-run.",
      _obj({"job_id": {"type": "string"},
            "designs": {"type": "array", "items": {"type": "string"}, "description": "design names (optional)"}},
           ["job_id"]))
def retry_designs(job_id: str, designs: list | None = None) -> dict:
    j = RT.store.job(job_id)
    if not j:
        return {"error": "no such job"}
    if RT.store.deletion("job", job_id):
        return {"error": "job is being deleted"}
    if designs is not None and (not isinstance(designs, list) or not all(isinstance(n, str) for n in designs)):
        return {"error": "designs must be a list of design names"}
    if designs:
        names = set(designs)
        unknown = names - {d["name"] for d in j["designs"]}
        if unknown:
            return {"error": "no such design in job: %s" % ", ".join(sorted(unknown))}
        picks = [d for d in j["designs"] if d["name"] in names]
        busy = [d["name"] for d in picks if d["status"] in ("pending", "running")]
        if busy:
            return {"error": "still running in this job: %s" % ", ".join(busy)}
    else:
        picks = [d for d in j["designs"] if d["status"] in ("failed", "cancelled")]
        if not picks:
            return {"error": "no failed or cancelled design to re-run in this job"}
    sources = []
    for d in picks:
        det = d.get("detail") or {}
        src = det.get("source")
        if not src or not Path(src).exists():
            return {"error": "the RTL of %s is no longer on disk (%s); submit it again" % (d["name"], src)}
        top = det["requested_top"] if "requested_top" in det else det.get("top")
        sources.append({"name": d["name"], "path": src, "top": top})
    spec = j.get("spec") or {}
    try:
        jid = RT.pipeline.submit(sources, platform=spec.get("platform", "nangate45"),
                                 output=spec.get("output", "graph"), params=spec.get("params") or None,
                                 title="重新运行 · %s" % re.sub(r"^(重新运行 · )+", "", j["title"] or ""), session_id=CURRENT_SESSION.get(),
                                 retry_of=job_id)
    except ValueError as e:
        return {"error": str(e)}
    return {"job_id": jid, "designs": [s["name"] for s in sources], "retry_of": job_id,
            "note": "running in background; use job_status to follow progress"}


@tool("Where the finished dataset of a job is and what it contains (only designs that passed sign-off "
      "and verification are included).", _obj({"job_id": {"type": "string"}}, ["job_id"]))
def dataset_info(job_id: str) -> dict:
    j = RT.store.job(job_id)
    if not j:
        return {"error": "no such job"}
    res = j.get("result") or {}
    pkg = res.get("package")
    info = {"job_id": job_id, "status": j["status"], "passed": res.get("passed"), "total": res.get("total")}
    if pkg and Path(pkg).is_file():
        info.update(path=pkg, size_mb=round(Path(pkg).stat().st_size / 1e6, 2),
                    download_url="/api/jobs/%s/download" % job_id)
    else:
        info["note"] = "dataset not packaged yet (job still running or no design passed)"
    return info


@tool("Check that the EDA toolchain needed for the platform is installed and working.",
      _obj({"platform": {"type": "string"}}))
def check_environment(platform: str = "nangate45") -> dict:
    if platform not in platforms.PLATFORMS and platform in replay_platforms():
        # replay: the recorded jobs ran on that platform's own apparatus, which this host's check_env.sh does not cover
        replay_log({"event": "check_environment_replay_platform", "platform": platform})
        return {"ok": True, "platform": platform, "replay": True,
                "summary": "replay: this platform's recorded jobs ran on its signoff apparatus (for asap7: ORFS + "
                           "Calibre with the official r1p7 DRC/LVS decks); start_job attaches them",
                "knowledge": knowledge.summary()}
    r = subprocess.run(["bash", str(signoff_scripts() / "flow" / "check_env.sh"), "--platform", platform],
                       cwd=repo_root(), capture_output=True, text=True, timeout=300)
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    # the verdict for the platform asked about; check_env.sh's exit code also counts
    # platforms nobody asked for (asap7 is unsupported by design)
    ready = bool(re.search(r"^%s\s+STRICT-READY" % re.escape(platform), r.stdout, re.M))
    return {"ok": ready, "platform": platform, "check_env_rc": r.returncode,
            "summary": "\n".join(lines[-25:]), "knowledge": knowledge.summary()}
