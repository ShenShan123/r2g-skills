"""Tools of the bare-agent arm: the EDA operations R2G uses, without its orchestration.

The model gets what an engineer at a terminal would have: read files (project,
ORFS checkout), write files inside its own project, run the ORFS flow from any
stage, run the same signoff script R2G runs (DRC + LVS + RCX, same decks), read
logs, and declare completion. It does NOT get R2G's diagnosis, recipe library,
knowledge base, repair loop or its skill documentation: the R2G skill tree is
outside every readable root.

Every effect is confined by the harness, not by the prompt: writes only under
the project's constraints/, rtl/ and hooks/; ORFS platform files are read-only;
RTL writes are journaled so a semantics review can follow; budgets are counted
here and enforced by run_bare.py.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

STAGES = ("synth", "floorplan", "place", "cts", "route", "finish")
MAX_LINES = 300
MAX_CHARS = 20000
WRITE_ROOTS = ("constraints", "rtl", "hooks")


class ToolError(Exception):
    pass


class BareTools:
    def __init__(self, project: Path, platform: str, repo: Path, orfs_root: Path,
                 audit_env: dict, stage_timeout: int = 21600, cores: int = 8):
        self.project = project.resolve()
        self.platform = platform
        self.repo = repo.resolve()
        self.orfs = orfs_root.resolve()
        self.audit_env = audit_env
        self.stage_timeout = stage_timeout
        self.cores = cores
        self.counts = {"run_flow": 0, "run_signoff": 0}
        self.eda_seconds = 0.0
        self.writes: list[dict] = []
        self.finished: dict | None = None

    # -- sandbox ---------------------------------------------------------------
    def _resolve(self, path: str, write: bool = False) -> Path:
        p = Path(path)
        p = (self.project / p if not p.is_absolute() else p).resolve()
        if write:
            roots = [self.project / r for r in WRITE_ROOTS]
        else:
            roots = [self.project, self.orfs / "flow" / "scripts", self.orfs / "flow" / "platforms" / self.platform,
                     self.orfs / "flow" / "Makefile", self.orfs / "flow" / "util", self.orfs / "docs"]
        # roots resolved like the path: an ORFS root whose flow/ entries are
        # symlinks (203's shadow root on /data, 2026-10-29) must stay readable
        roots = [r.resolve() for r in roots]
        if not any(p == r or r in p.parents for r in roots):
            raise ToolError("path outside the allowed %s roots: %s" % ("write" if write else "read", path))
        return p

    # -- tools -----------------------------------------------------------------
    def list_dir(self, path: str = ".") -> dict:
        p = self._resolve(path)
        if not p.is_dir():
            raise ToolError("not a directory: %s" % path)
        items = sorted(p.iterdir())[:400]
        return {"path": str(p), "entries": [x.name + ("/" if x.is_dir() else "") for x in items]}

    def read_file(self, path: str, start: int = 1, end: int = 200) -> dict:
        p = self._resolve(path)
        if not p.is_file():
            raise ToolError("not a file: %s" % path)
        lines = p.read_text(errors="replace").splitlines()
        start, end = max(1, int(start)), min(len(lines), int(end), int(start) + MAX_LINES - 1)
        text = "\n".join(lines[start - 1:end])[:MAX_CHARS]
        return {"path": str(p), "lines": "%d-%d of %d" % (start, end, len(lines)), "text": text}

    def write_file(self, path: str, content: str) -> dict:
        p = self._resolve(path, write=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        before = p.read_text(errors="replace") if p.is_file() else None
        p.write_text(content)
        rel = str(p.relative_to(self.project))
        self.writes.append({"path": rel, "created": before is None, "rtl": rel.startswith("rtl/"),
                            "at": time.time(), "bytes": len(content)})
        return {"written": rel, "bytes": len(content)}

    def run_flow(self, from_stage: str = "synth") -> dict:
        if from_stage not in STAGES:
            raise ToolError("from_stage must be one of %s" % ", ".join(STAGES))
        if getattr(self, "force_synth_first", False) and self.counts["run_flow"] == 0 and from_stage != "synth":
            # fallback from R2G's project: its live ORFS workspace is gone, so a
            # resume has nothing to resume from (run_orfs.sh would refuse it)
            from_stage = "synth"
        self.counts["run_flow"] += 1
        env = {"NUM_CORES": str(self.cores), "OMP_NUM_THREADS": str(self.cores),
               "ORFS_TIMEOUT": str(self.stage_timeout)}
        if from_stage != "synth":
            env["FROM_STAGE"] = from_stage
        rc = self._sh("flow", ["bash", str(self.repo / "r2g-skills/signoff-loop/scripts/flow/run_orfs.sh"),
                               str(self.project), self.platform], env,
                      timeout=len(STAGES) * self.stage_timeout + 1800)
        self._prune_archives()
        run = self._latest_run()
        failed = None
        if run and (run / "stage_log.jsonl").is_file():
            for line in (run / "stage_log.jsonl").read_text().splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("status"):
                    failed = e.get("stage")
        tail = self._tail(run / "flow.log" if run else None, 60)
        return {"returncode": rc, "status": "ok" if rc == 0 else "failed", "failed_stage": failed,
                "run": run.name if run else None, "log_tail": tail}

    def run_signoff(self) -> dict:
        """Full DRC + LVS (+RCX) with the same script and decks R2G uses."""
        self.counts["run_signoff"] += 1
        reports = self.project / "reports"
        for name in ("drc.json", "lvs.json", "rcx.json"):
            (reports / name).unlink(missing_ok=True)
        env = {"DRC_TIMEOUT": "21600", "LVS_TIMEOUT": "21600", "NETGEN_TIMEOUT": "21600",
               "R2G_POWERED_NETLIST_TIMEOUT": "21600"}
        rc = self._sh("signoff", ["bash", str(self.repo / "tools/run_signoff.sh"), str(self.project),
                                  self.platform], env, timeout=3 * 21600 + 3600)
        return {"returncode": rc, **self.signoff_summary()}

    def read_log(self, name: str = "flow.log", lines: int = 80, grep: str = "") -> dict:
        """Logs of the latest flow run (flow.log, logs/<stage>.log) or signoff (drc/, lvs/)."""
        run = self._latest_run()
        cands = []
        if "/" in name:
            # a path rather than a bare log name: read it inside the sandbox
            cands.append(self._resolve(name))
            name = Path(name).name
        if run:
            cands += [run / name, run / "logs" / name]
            cands += list((run / "logs").rglob(name)) if (run / "logs").is_dir() else []
        cands += [self.project / "lvs" / name, self.project / "drc" / name, self.project / "agent_logs" / name]
        p = next((c for c in cands if c.is_file()), None)
        if p is None:
            available = sorted({x.name for x in (run / "logs").rglob("*.log")}) if run and (run / "logs").is_dir() else []
            raise ToolError("no log named %s; available: %s" % (name, ", ".join(available[:60])))
        text = p.read_text(errors="replace").splitlines()
        if grep:
            rx = re.compile(grep)
            text = [l for l in text if rx.search(l)]
        n = max(1, min(int(lines), MAX_LINES))
        return {"path": str(p.relative_to(self.project)) if self.project in p.parents else str(p),
                "text": "\n".join(text[-n:])[:MAX_CHARS], "matched_lines": len(text)}

    def finish(self, summary: str) -> dict:
        self.finished = {"summary": summary, "at": time.time()}
        return {"ok": True}

    # -- helpers ---------------------------------------------------------------
    def signoff_summary(self) -> dict:
        out = {}
        for name in ("drc", "lvs"):
            p = self.project / "reports" / (name + ".json")
            d = json.loads(p.read_text()) if p.is_file() else {}
            s = {"status": d.get("status") or "missing"}
            if name == "drc":
                s["total_violations"] = d.get("total_violations")
                cats = d.get("categories") or {}
                s["categories"] = {k: (v or {}).get("count") for k, v in sorted(
                    cats.items(), key=lambda kv: -((kv[1] or {}).get("count") or 0))[:15]}
            else:
                s["mismatch_count"] = d.get("mismatch_count")
                s["mismatch_class"] = d.get("mismatch_class")
            out[name] = s
        return out

    def _prune_archives(self, keep: int = 2):
        """Drop layout databases from all but the newest `keep` archived runs.

        A resume reads the live ORFS workspace, not these copies; logs, reports
        and stage logs stay, so the transcript and the audit remain complete."""
        runs = sorted((self.project / "backend").glob("RUN_*"), key=lambda p: p.stat().st_mtime)
        for run in runs[:-keep]:
            for pattern in ("*.odb", "*.gds", "*.def", "*.spef", "*.v.gz"):
                for f in run.rglob(pattern):
                    f.unlink(missing_ok=True)

    def _latest_run(self):
        runs = sorted((self.project / "backend").glob("RUN_*"), key=lambda p: p.stat().st_mtime)
        return runs[-1] if runs else None

    @staticmethod
    def _tail(path, n):
        if not path or not Path(path).is_file():
            return ""
        return "\n".join(Path(path).read_text(errors="replace").splitlines()[-n:])[:MAX_CHARS]

    def _sh(self, stage: str, cmd: list, env: dict, timeout: int) -> int:
        e = dict(os.environ)
        e.update(self.audit_env)
        e.update(env)
        e["R2G_ORFS_AUDIT_STAGE"] = stage
        e["R2G_ORFS_AUDIT_EXECUTION"] = uuid.uuid4().hex
        # Per-design memory bound (2026-10-30: an FPGA-fabric floorplan held 172 GB
        # on 203 and swapped the host). R2G_BARE_MEM_MAX (e.g. "64G") runs the EDA
        # step in a transient cgroup scope where systemd-run --user exists; inside
        # the 216 container the launcher's podman --memory bounds it instead.
        mem = os.environ.get("R2G_BARE_MEM_MAX")
        if mem and shutil.which("systemd-run"):
            cmd = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=%s" % mem,
                   "-p", "MemorySwapMax=0"] + cmd
        log = self.project / "agent_logs" / ("%s.log" % stage)
        log.parent.mkdir(exist_ok=True)
        t0 = time.monotonic()
        with log.open("ab") as fh:
            fh.write(("\n$ %s\n" % " ".join(cmd)).encode())
            fh.flush()
            p = subprocess.Popen(cmd, cwd=self.repo, env=e, stdout=fh, stderr=subprocess.STDOUT,
                                 start_new_session=True)
            try:
                rc = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, 15)
                time.sleep(30)
                try:
                    os.killpg(p.pid, 9)
                except ProcessLookupError:
                    pass
                rc = 124
        self.eda_seconds += time.monotonic() - t0
        return rc


SPECS = [
    {"name": "list_dir", "description": "List a directory inside the project or the ORFS checkout (flow/scripts, flow/platforms/<platform>, flow/util, docs).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}},
    {"name": "read_file", "description": "Read lines of a text file inside the project or the ORFS checkout (at most 300 lines per call).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start": {"type": "integer"},
                                                      "end": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Create or overwrite a file under the project's constraints/, rtl/ or hooks/ directories (e.g. constraints/config.mk, a Tcl hook referenced from config.mk).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]}},
    {"name": "run_flow", "description": "Run the OpenROAD-flow-scripts flow for this project from the given stage through finish (synth, floorplan, place, cts, route, finish). Earlier stages are reused from the last run. Returns status, failing stage and the log tail. Takes minutes to hours.",
     "parameters": {"type": "object", "properties": {"from_stage": {"type": "string", "enum": list(STAGES)}}}},
    {"name": "run_signoff", "description": "Run full-deck DRC and LVS (and RCX) on the latest completed layout with the platform's signoff decks. Returns DRC status, violation counts per rule, and LVS status. Takes minutes to hours.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "read_log", "description": "Read the end of a log of the latest run: flow.log, a stage log such as 5_1_grt.log, or signoff logs (6_lvs.log, drc_run.log). Optional regex filter.",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "lines": {"type": "integer"},
                                                      "grep": {"type": "string"}}}},
    {"name": "finish", "description": "Declare the task finished and summarize what was done and the final DRC/LVS state.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}},
]
