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
import re
import subprocess
from pathlib import Path

from .jobs import JobStore
from .paths import examples_dir, repo_root, signoff_scripts, skills_dir
from .pipeline import STAGE_LABEL, Pipeline


class Runtime:
    """Process-wide handles the tools use."""
    store: JobStore | None = None
    pipeline: Pipeline | None = None
    platform: str = "nangate45"


RT = Runtime()
REGISTRY: dict[str, dict] = {}


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
      "sign-off, automatic repair, and graph-dataset construction. Returns a job_id immediately; "
      "the job runs for minutes to hours. Each design is either a built-in example name or a "
      "directory/file path of Verilog sources on this machine.",
      _obj({"designs": {"type": "array", "description": "designs to run",
                        "items": {"type": "object", "properties": {
                            "example": {"type": "string", "description": "built-in example name"},
                            "path": {"type": "string", "description": "RTL directory or file path"},
                            "top": {"type": "string", "description": "top module (optional; inferred if omitted)"},
                            "name": {"type": "string", "description": "display name (optional)"}}}},
            "platform": {"type": "string", "enum": ["nangate45"], "description": "process platform"},
            "output": {"type": "string", "enum": ["graph"], "description": "dataset type"},
            "title": {"type": "string"}}, ["designs"]))
def start_job(designs: list, platform: str = "nangate45", output: str = "graph", title: str = "") -> dict:
    sources = []
    for d in designs:
        if d.get("example"):
            p = examples_dir() / d["example"]
            if not p.is_dir():
                return {"error": "no such example: %s" % d["example"]}
            sources.append({"name": d.get("name") or d["example"], "path": str(p), "top": d.get("top")})
        elif d.get("path"):
            p = Path(d["path"]).expanduser()
            if not p.exists():
                return {"error": "path does not exist: %s" % p}
            sources.append({"name": d.get("name") or p.stem, "path": str(p.resolve()), "top": d.get("top")})
        else:
            return {"error": "each design needs 'example' or 'path'"}
    if not sources:
        return {"error": "no designs given"}
    jid = RT.pipeline.submit(sources, platform=platform, title=title, output=output)
    return {"job_id": jid, "designs": [s["name"] for s in sources],
            "note": "running in background; use job_status to follow progress"}


@tool("Progress of a job: overall status and, per design, the current stage and verdicts so far.",
      _obj({"job_id": {"type": "string"}}, ["job_id"]))
def job_status(job_id: str) -> dict:
    j = RT.store.job(job_id)
    if not j:
        return {"error": "no such job"}
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
    r = subprocess.run(["python3", str(signoff_scripts() / "reports" / "diagnose_signoff_fix.py"),
                        d["project"], "--check", check, "--list"], cwd=repo_root(),
                       capture_output=True, text=True, timeout=300)
    try:
        plan = json.loads(r.stdout)
    except ValueError:
        return {"error": "diagnosis produced no plan", "stderr": r.stderr[-800:]}
    return {"check": check, "plan": plan}


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


@tool("Cancel a running job.", _obj({"job_id": {"type": "string"}}, ["job_id"]))
def cancel_job(job_id: str) -> dict:
    RT.pipeline.cancel(job_id)
    return {"job_id": job_id, "cancel_requested": True}


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
    r = subprocess.run(["bash", str(signoff_scripts() / "flow" / "check_env.sh"), "--platform", platform],
                       cwd=repo_root(), capture_output=True, text=True, timeout=300)
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    return {"ok": r.returncode == 0, "summary": "\n".join(lines[-25:])}
