"""ASAP7 backend of the bare-agent tools (memlab216 only: Calibre lives there).

Same tool surface, budgets and sandbox as BareTools; only the two EDA operations differ, because ASAP7 is not one
of the skill's open-deck platforms:
  run_flow    ORFS in the frozen baseline image (r2g-orfs:26Q3) with the frozen pin-access OpenROAD, exactly the
              baseline driver's container invocation (experiments/orfs_spec.py), DESIGN_CONFIG = the project's
              constraints/config.mk (which carries the frozen apparatus lines), WORK_HOME persistent in the project
              so a later stage can be re-run on top of the earlier ones. 6 h per run (the baseline's flow bound),
              64 GB container memory (the protocol's per-step bound).
  run_signoff the baseline's Calibre signoff (asap7_signoff.py: official r1p7 DRC, flat, A/B partition, 6 h per
              group; transistor LVS against the PEX library), unchanged; its verdict is written as reports/drc.json
              and reports/lvs.json in the fields run_bare.verdict() grades (status, total_violations, categories,
              drc_mode, gds_sha256, run_tag).
Each completed run is archived as backend/RUN_<n>/ (results/6_final.*, logs/, reports/, flow.log) so the agent can
read its logs and the harness can bind the reports to the layout. The layout identity is the flow's
results/6_final.gds; signoff regenerates its inputs from that run's 6_final.odb/.gds deterministically and records
the digests of what it checked in signoff.json.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from bare_tools import STAGES, BareTools, ToolError

IMAGE = os.environ.get("R2G_IMAGE", "r2g-orfs:26Q3")
BASE = Path(os.environ.get("ASAP7_BASELINE_ROOT", "/proj/workarea/yangao/asap7_baseline"))
OPENROAD = BASE / "apparatus/openroad_pinaccess"
SIGNOFF = Path(os.environ.get("ASAP7_SIGNOFF_PY", str(BASE / "asap7_signoff.py")))
FLOW_TIMEOUT = 21600
MEMORY = os.environ.get("R2G_BARE_CONTAINER_MEMORY", "64g")
# memlab203 (no container runtime): R2G_ASAP7_NATIVE_ORFS=<ORFS checkout> runs the same make invocation natively
# with a clean environment (orfs_spec.py --native-orfs); memory is bounded by the host watchdog (64 GB marker).
NATIVE_ORFS = os.environ.get("R2G_ASAP7_NATIVE_ORFS", "")
NATIVE_LD = os.environ.get("R2G_ASAP7_NATIVE_LD", "")
CALIBRE_SETUP = os.environ.get("R2G_CALIBRE_SETUP", "module load calibre/2025.1_16.10 >/dev/null 2>&1")
STAGE_LOG_PREFIX = {"synth": "1_", "floorplan": "2_", "place": "3_", "cts": "4_", "route": "5_", "finish": "6_"}


def _sha(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class BareToolsASAP7(BareTools):

    def _design(self) -> str:
        import re
        cfg = (self.project / "constraints/config.mk").read_text()
        m = re.search(r"^\s*export\s+DESIGN_NAME\s*=\s*(\S+)", cfg, re.M)
        if not m:
            raise ToolError("constraints/config.mk has no DESIGN_NAME")
        return m.group(1)

    def _container(self) -> str:
        return "bare7_%s_%d" % (self.project.parent.name, os.getpid())

    def run_flow(self, from_stage: str = "synth") -> dict:
        if from_stage not in STAGES:
            raise ToolError("from_stage must be one of %s" % ", ".join(STAGES))
        work = self.project / "backend" / "work"
        if from_stage != "synth" and not (work / "results").is_dir():
            from_stage = "synth"                          # nothing to resume from
        self.counts["run_flow"] += 1
        if from_stage == "synth":
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        cfg = self.project / "constraints/config.mk"
        mk = "make DESIGN_CONFIG=%s WORK_HOME=%s" % (cfg, work)
        later = STAGES[STAGES.index(from_stage):]
        clean = " && ".join("%s clean_%s" % (mk, s) for s in later) if from_stage != "synth" else "true"
        name = self._container()
        env = None
        if NATIVE_ORFS:
            cmd = ["bash", "-c", "cd %s/flow && %s && timeout %d %s finish" % (NATIVE_ORFS, clean, FLOW_TIMEOUT, mk)]
            env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"), "LANG": "C.UTF-8",
                   "NUM_CORES": str(self.cores), "OMP_NUM_THREADS": str(self.cores),
                   "LD_LIBRARY_PATH": NATIVE_LD, "OPENROAD_EXE": str(OPENROAD)}
            # the memory watchdog maps a step to its attempt through these (mem_bound_watchdog.py)
            env.update({k: v for k, v in self.audit_env.items() if k.startswith("R2G_ORFS_AUDIT")})
        else:
            subprocess.run(["podman", "rm", "-f", name], capture_output=True, check=False)
            cmd = ["podman", "run", "--rm", "--name", name, "--memory", MEMORY, "--memory-swap", MEMORY,
                   "-v", "%s:%s:Z" % (self.project, self.project),
                   "-v", "%s:/opt/r2g/openroad:ro" % OPENROAD, "-e", "OPENROAD_EXE=/opt/r2g/openroad",
                   "-e", "NUM_CORES=%d" % self.cores, "-e", "OMP_NUM_THREADS=%d" % self.cores]
            # rootless podman on 216 ignores --memory (cgroup v1): the 64 GB bound is the host watchdog's
            # (mem_bound_watchdog.py), which maps a step to its attempt through these variables
            for k, v in self.audit_env.items():
                if k.startswith("R2G_ORFS_AUDIT"):
                    cmd += ["-e", "%s=%s" % (k, v)]
            cmd += [IMAGE, "bash", "-lc", "cd $ORFS_ROOT/flow && %s && timeout %d %s finish" % (clean, FLOW_TIMEOUT, mk)]
        n = len(list((self.project / "backend").glob("RUN_*"))) + 1
        run = self.project / "backend" / ("RUN_%03d" % n)
        run.mkdir(parents=True)
        t0 = time.monotonic()
        with open(run / "flow.log", "wb") as fh:
            fh.write(("$ run_flow from %s\n" % from_stage).encode())
            fh.flush()
            p = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True, env=env)
            try:
                rc = p.wait(timeout=FLOW_TIMEOUT + 1800)
            except subprocess.TimeoutExpired:
                if NATIVE_ORFS:
                    import signal
                    os.killpg(p.pid, signal.SIGTERM); time.sleep(20)
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    subprocess.run(["podman", "stop", "-t", "10", name], capture_output=True, check=False)
                rc = 124
        self.eda_seconds += time.monotonic() - t0
        if (self.project.parent / "MEMORY_BOUND.json").is_file() or rc == 137:
            (self.project.parent / "MEMORY_BOUND.json").write_text(json.dumps(
                {"bound": MEMORY, "step": "run_flow", "returncode": rc, "at": time.time()}))
        d = self._design()
        for sub in ("results", "logs", "reports"):
            src = work / sub / "asap7" / d / "base"
            if src.is_dir():
                shutil.copytree(src, run / sub, dirs_exist_ok=True)
        final = run / "results" / "6_final.gds"
        failed = None
        if rc != 0 or not final.is_file():
            logs = sorted((run / "logs").glob("*.log"), key=lambda q: q.name) if (run / "logs").is_dir() else []
            failed = next((s for s, pre in STAGE_LOG_PREFIX.items() if logs and logs[-1].name.startswith(pre)),
                          from_stage)
            (run / "stage_log.jsonl").write_text(json.dumps({"stage": failed, "status": "failed", "rc": rc}) + "\n")
        else:
            # the signoff script reads a harvested run dir: 6_final.{gds,odb} + config.mk at its top
            for f in ("6_final.gds", "6_final.odb", "6_final.v", "6_final.sdc"):
                if (run / "results" / f).is_file():
                    os.link(run / "results" / f, run / f)
            shutil.copyfile(cfg, run / "config.mk")
        self._prune_archives()
        return {"returncode": rc, "status": "ok" if failed is None else "failed", "failed_stage": failed,
                "run": run.name, "log_tail": self._tail(run / "flow.log", 60)}

    # Runs are ordered by their NUMBER, never by mtime (harness defect 2026-11-19): RUN_<n> holds top-level hard
    # links to its final layout, so _prune_archives deleting them touched an OLD run directory's mtime and made it
    # "latest" -- the agent's signoff/log tools and the final verification then used a pruned run with no layout.
    def _runs(self):
        return sorted((self.project / "backend").glob("RUN_*"), key=lambda p: int(p.name.split("_")[1]))

    def _latest_run(self):
        runs = self._runs()
        return runs[-1] if runs else None

    def _prune_archives(self, keep: int = 2):
        for run in self._runs()[:-keep]:
            for pattern in ("*.odb", "*.gds", "*.def", "*.spef", "*.v.gz"):
                for f in run.rglob(pattern):
                    f.unlink(missing_ok=True)

    def run_signoff(self) -> dict:
        self.counts["run_signoff"] += 1
        reports = self.project / "reports"
        reports.mkdir(exist_ok=True)
        run = self._latest_run()
        if not run or not (run / "6_final.gds").is_file():
            # the latest flow run left no layout: say so, and keep the previous reports (they name their run_tag)
            return {"returncode": 2, "error": "the latest run (%s) has no completed layout; run_flow first"
                    % (run.name if run else None), **self.signoff_summary()}
        for name in ("drc.json", "lvs.json", "rcx.json"):
            (reports / name).unlink(missing_ok=True)
        out = self.project / "signoff_runs" / run.name
        shutil.rmtree(out, ignore_errors=True)
        out.parent.mkdir(exist_ok=True)
        cmd = ["bash", "-lc", "%s; exec python3 %s %s %s --drc-budget 21600 "
               "--lvs-budget 21600" % (CALIBRE_SETUP, SIGNOFF, run, out)]
        rc = self._sh("signoff", cmd, {}, timeout=2 * 21600 + 3600)
        s = json.loads((out / "signoff.json").read_text()) if (out / "signoff.json").is_file() else {}
        self._write_reports(run, s)
        for src, dst in (("drc_A/drc_report", "drc/drc_report"), ("drc_A/run.log", "drc/drc_run.log"),
                         ("lvs/mask.lvs.rep", "lvs/6_lvs.log")):
            if (out / src).is_file():
                (self.project / dst).parent.mkdir(exist_ok=True)
                shutil.copyfile(out / src, self.project / dst)
        return {"returncode": rc, **self.signoff_summary()}

    def _write_reports(self, run: Path, s: dict):
        gds = _sha(run / "results" / "6_final.gds")
        viol = s.get("drc_violations") if isinstance(s.get("drc_violations"), dict) else {}
        total = sum(int(v) for v in viol.values()) if viol else (0 if s.get("drc") == "clean" else None)
        drc_status = {"clean": "clean", "violations": "fail", "timeout": "timeout"}.get(s.get("drc"), s.get("drc") or "failed")
        common = {"gds_sha256": gds, "run_tag": run.name, "checker": "calibre-2025.1 asap7 r1p7 (official deck)",
                  "signoff_json": str(self.project / "signoff_runs" / run.name / "signoff.json"),
                  "deck_drc_sha256": s.get("deck_drc_sha256"), "deck_lvs_sha256": s.get("deck_lvs_sha256"),
                  "checked_gds_sha256": (s.get("inputs") or {}).get("drc_gds_sha256")}
        (self.project / "reports/drc.json").write_text(json.dumps({
            "status": drc_status, "total_violations": total, "drc_mode": "full",
            "categories": {k: {"count": int(v), "description": k} for k, v in sorted(viol.items(), key=lambda kv: -int(kv[1]))},
            "groups": {"A": s.get("drc_group_a"), "B": s.get("drc_group_b")}, **common}, indent=1))
        lvs = s.get("lvs")
        (self.project / "reports/lvs.json").write_text(json.dumps({
            "status": "clean" if lvs == "CORRECT" else ("fail" if lvs == "INCORRECT" else (lvs or "failed")),
            "mismatch_count": 0 if lvs == "CORRECT" else None,
            "mismatch_class": None if lvs == "CORRECT" else "calibre_lvs_" + str(lvs).lower(),
            "detail": s.get("lvs_detail"), **common}, indent=1))
