"""The RTL -> signed-off layout -> graph dataset pipeline, one design at a time.

Stages, each a call into an existing skill script (nothing re-implemented):

  prepare   project.create                      RTL copied, synthesis config
  synth     signoff-loop run_orfs.sh (synth)    cells, area, IO pins
  size      project.apply_sizing                explicit die + binned period
  flow      run_orfs.sh FROM_STAGE=floorplan    reuses the synthesis result
  signoff   tools/run_signoff.sh                DRC + LVS + RCX verdicts
  repair    fix_signoff.sh (if not dual-clean)  diagnose -> apply -> re-check
  graph     def-graph run_graphs.sh             b..f graphs + netlist graph
  verify    tools/verify_graph_dataset.py       independent re-derivation
  preview   render_gds_preview.py               layout picture for the UI

A design PASSES only when the deterministic checks say so: DRC clean, LVS
clean, graph manifest ok, verifier green. No verdict (a crashed or timed-out
checker) is not a pass. The model never writes a verdict; it can only read one.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import frontend
from . import project as proj
from .jobs import CANCELLED, FAILED, PASSED, RUNNING, JobStore
from .paths import defgraph_scripts, r2g_home, repo_root, signoff_scripts, skills_dir, tools_dir

STAGES = ["prepare", "synth", "size", "flow", "signoff", "repair", "graph", "verify", "preview"]
# the "graph" stage builds whichever dataset the job asked for (graph or image)
OUTPUTS = ("graph", "image")
STAGE_LABEL = {"prepare": "准备工程", "synth": "综合", "size": "定尺寸与时钟", "flow": "布局布线",
               "signoff": "签核 DRC/LVS", "repair": "自动修复", "graph": "生成数据集", "verify": "数据集校验",
               "preview": "版图预览"}


class Cancelled(Exception):
    pass


def graph_python() -> str | None:
    """The torch venv def-graph pins (R2G_GRAPH_PYTHON), for the verifier."""
    if os.environ.get("R2G_GRAPH_PYTHON"):
        return os.environ["R2G_GRAPH_PYTHON"]
    pin = skills_dir() / "def-graph" / "references" / "env.local.sh"
    if pin.is_file():
        m = re.search(r'^export R2G_GRAPH_PYTHON="?([^"\n]+)"?', pin.read_text(), re.M)
        if m:
            return m.group(1)
    return None


def latest_run(project: Path) -> Path | None:
    runs = sorted((project / "backend").glob("RUN_*"), key=lambda p: p.stat().st_mtime)
    return runs[-1] if runs else None


def read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def signoff_verdict(project: Path) -> dict:
    drc = read_json(project / "reports" / "drc.json")
    lvs = read_json(project / "reports" / "lvs.json")
    v = {
        "drc": drc.get("status") or "missing",
        "drc_violations": drc.get("total_violations"),
        "lvs": lvs.get("status") or "missing",
        "lvs_mismatches": lvs.get("mismatch_count"),
    }
    v["dual_clean"] = v["drc"] == "clean" and v["lvs"] == "clean"
    return v


class Pipeline:
    """Runs designs of a job on a thread pool; one subprocess tree per stage."""

    def __init__(self, store: JobStore, workers: int = 2, cores_per_design: int = 8,
                 ingest: bool = True, stage_timeout: int = 6 * 3600):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=max(1, workers))
        self.cores = cores_per_design
        self.ingest = ingest
        self.stage_timeout = stage_timeout
        self._procs: dict[tuple, subprocess.Popen] = {}
        self._cancel: set[str] = set()
        self._lock = threading.Lock()

    # -- public -------------------------------------------------------------
    def submit(self, sources: list[dict], platform: str = "nangate45", title: str = "",
               output: str = "graph") -> str:
        """sources: [{"name":..., "path":..., "top": optional}]"""
        if output not in OUTPUTS:
            raise ValueError("output must be one of %s" % ", ".join(OUTPUTS))
        designs, seen = [], set()
        for s in sources:
            name = re.sub(r"[^A-Za-z0-9_]+", "_", s.get("name") or Path(s["path"]).name)
            while name in seen:
                name += "_x"
            seen.add(name)
            designs.append({"name": name, "detail": {"source": str(s["path"]), "top": s.get("top")}})
        spec = {"platform": platform, "output": output, "sources": sources}
        jid = self.store.create_job(title or "%d 个设计 · %s · %s" % (len(designs), platform, output),
                                    spec, designs)
        ws = r2g_home() / "workspace" / jid
        for d in designs:
            # FLOW_VARIANT comes from the project folder name, so it carries the
            # job id: two jobs on one design must never share ORFS result dirs
            pdir = ws / ("%s__%s" % (d["name"], jid))
            self.store.set_design(jid, d["name"], project=str(pdir))
            self.pool.submit(self._run_design, jid, d["name"], Path(d["detail"]["source"]),
                             d["detail"].get("top"), pdir, platform, output)
        self.store.set_job(jid, RUNNING)
        threading.Thread(target=self._watch_job, args=(jid,), daemon=True).start()
        return jid

    def cancel(self, jid: str) -> None:
        self._cancel.add(jid)
        with self._lock:
            for (j, _d), p in list(self._procs.items()):
                if j == jid and p.poll() is None:
                    try:
                        os.killpg(p.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
        self.store.event(jid, None, "job", "cancel", "已请求取消")

    # -- internals ------------------------------------------------------------
    def _watch_job(self, jid: str) -> None:
        # never let one transient error (a busy database on a loaded machine)
        # end the watcher: a dead watcher leaves a finished job "running" forever
        while True:
            try:
                if self.finalize_if_done(jid):
                    return
            except Exception:                                   # noqa: BLE001
                pass
            time.sleep(2)

    def finalize_if_done(self, jid: str) -> bool:
        """Close a job whose designs have all ended: package and record. True if closed."""
        j = self.store.job(jid)
        if not j or j["status"] not in (RUNNING, "pending"):
            return True
        sts = [d["status"] for d in j["designs"]]
        if not all(s in (PASSED, FAILED, CANCELLED) for s in sts):
            return False
        n_ok = sts.count(PASSED)
        status = CANCELLED if jid in self._cancel else ("done" if n_ok else FAILED)
        result = {"passed": n_ok, "total": len(sts)}
        if n_ok:
            from .package import package_job
            try:
                result["package"] = str(package_job(self.store, jid))
            except Exception as e:                              # noqa: BLE001
                result["package_error"] = str(e)
        self.store.set_job(jid, status, result)
        self.store.event(jid, None, "job", "done",
                         "任务结束：%d/%d 个设计通过并产出数据集" % (n_ok, len(sts)), result)
        return True

    def recover(self) -> list[str]:
        """At start-up: close jobs that finished while no watcher was alive, and
        mark designs a previous server process was running as interrupted (their
        threads died with it; nothing will ever finish them)."""
        touched = []
        for j in self.store.jobs(500):
            if j["status"] not in (RUNNING, "pending"):
                continue
            full = self.store.job(j["id"])
            for d in full["designs"]:
                if d["status"] in (RUNNING, "pending"):
                    self.store.set_design(j["id"], d["name"], status=FAILED,
                                          detail={"failed_stage": d["stage"],
                                                  "reason": "服务重启时中断，未完成（可重新提交）"})
                    self.store.event(j["id"], d["name"], d["stage"], "fail", "服务重启时中断，未完成（可重新提交）")
            self.finalize_if_done(j["id"])
            touched.append(j["id"])
        return touched

    def _sh(self, jid: str, name: str, stage: str, cmd: list[str], project: Path,
            env: dict | None = None, timeout: int | None = None) -> int:
        if jid in self._cancel:
            raise Cancelled()
        logdir = project / "agent_logs"
        logdir.mkdir(parents=True, exist_ok=True)
        e = dict(os.environ)
        e.update({"NUM_CORES": str(self.cores), "OMP_NUM_THREADS": str(self.cores),
                  "ORFS_TIMEOUT": str(self.stage_timeout)})
        e.update(env or {})
        with open(logdir / ("%s.log" % stage), "ab") as log:
            log.write(("\n$ %s\n" % " ".join(cmd)).encode())
            log.flush()
            p = subprocess.Popen(cmd, cwd=repo_root(), env=e, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
            with self._lock:
                self._procs[(jid, name)] = p
            try:
                rc = p.wait(timeout=timeout or self.stage_timeout)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGTERM)
                time.sleep(10)
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGKILL)
                rc = 124
            finally:
                with self._lock:
                    self._procs.pop((jid, name), None)
        if jid in self._cancel:
            raise Cancelled()
        return rc

    def _stage(self, jid: str, name: str, stage: str, msg: str | None = None) -> None:
        self.store.set_design(jid, name, stage=stage, status=RUNNING)
        self.store.event(jid, name, stage, "start", msg or STAGE_LABEL[stage])

    def _ok(self, jid, name, stage, msg, data=None):
        self.store.event(jid, name, stage, "ok", msg, data)

    def _fail(self, jid, name, stage, msg, data=None):
        self.store.event(jid, name, stage, "fail", msg, data)
        self.store.set_design(jid, name, status=FAILED, detail={"failed_stage": stage, "reason": msg})

    def _run_design(self, jid: str, name: str, src: Path, top: str | None, pdir: Path,
                    platform: str, output: str = "graph") -> None:
        try:
            self._run_design_inner(jid, name, src, top, pdir, platform, output)
        except Cancelled:
            self.store.set_design(jid, name, status=CANCELLED)
            self.store.event(jid, name, "job", "cancel", "已取消")
        except Exception as e:                                   # noqa: BLE001
            self._fail(jid, name, self.store.design(jid, name).get("stage") or "prepare",
                       "内部错误：%s" % e)

    def _run_design_inner(self, jid, name, src, top, pdir, platform, output="graph") -> None:
        S = str(signoff_scripts())
        # prepare
        self._stage(jid, name, "prepare")
        meta = proj.create(src, pdir, platform, top=top, name=name)
        self._ok(jid, name, "prepare", "顶层模块 %s（%s），%d 个源文件"
                 % (meta["top"], meta.get("top_source") or "指定", len(meta["files"])),
                 {"top": meta["top"], "top_source": meta.get("top_source")})
        self.store.set_design(jid, name, detail={"top": meta["top"], "top_source": meta.get("top_source"),
                                                 "frontend_fixes": meta["frontend_fixes"]})
        for fx in meta["frontend_fixes"]:
            self._ok(jid, name, "prepare", "前端修复：%s（%s）" % (fx["detail"], fx["file"]), fx)
        for m in meta["missing_data_files"]:
            self._fail_soft(jid, name, "prepare", "缺少数据文件：%s 里的 $readmem 读取 %s，%s；请补传这个文件"
                            % (m["file"], m["reference"], m["reason"]), m)
        # synth, then at most one re-run per frontend recipe whose signature is in the log
        self._stage(jid, name, "synth")
        tried: list[str] = []
        while True:
            rc = self._sh(jid, name, "synth", ["bash", S + "/flow/run_orfs.sh", str(pdir), platform], pdir,
                          env={"ORFS_STAGES": "synth"})
            run = latest_run(pdir)
            stat = run / "reports_orfs" / "synth_stat.txt" if run else None
            if rc == 0 and stat and stat.is_file():
                break
            hits = frontend.match_synth_failure(self._synth_log_text(pdir, run),
                                                run / "results" / "1_2_yosys.v" if run else None, tried)
            if not hits:
                msg = "综合失败（rc=%s）" % rc
                if meta["missing_data_files"]:
                    msg += "：缺少数据文件 %s" % "、".join(m["reference"] for m in meta["missing_data_files"])
                self._fail(jid, name, "synth", msg, {"log_tail": self._tail(pdir, "synth"),
                                                     "frontend_tried": tried})
                return
            tried += [h["id"] for h in hits]
            meta = proj.add_synth_hooks(pdir, [h["id"] for h in hits])
            for h in hits:
                fx = {"recipe": h["id"], "file": "constraints/frontend_fix.tcl", "detail": h["label"]}
                meta["frontend_fixes"].append(fx)
                self._ok(jid, name, "synth", "前端修复：%s，重新综合" % h["label"], fx)
            (pdir / "metadata.json").write_text(json.dumps(meta, indent=2))
            self.store.set_design(jid, name, detail={"frontend_fixes": meta["frontend_fixes"]})
        # size
        self._stage(jid, name, "size")
        sizing = proj.apply_sizing(pdir, stat, run / "results" / "1_2_yosys.v")
        self.store.set_design(jid, name, detail={"sizing": sizing})
        self._ok(jid, name, "size", "%d 个单元，die %g µm，时钟 %.1f ns（%s 决定尺寸）"
                 % (sizing["cells"], sizing["die_side_um"], sizing["period_ns"],
                    {"area": "面积", "pins": "引脚数", "pdn_floor": "电源网格下限"}[sizing["binding"]]),
                 sizing)
        if sizing["cells"] < 5000:
            cores = min(self.cores, 4)    # small designs: more threads only thrash
        else:
            cores = self.cores
        # flow
        self._stage(jid, name, "flow")
        rc = self._sh(jid, name, "flow", ["bash", S + "/flow/run_orfs.sh", str(pdir), platform], pdir,
                      env={"FROM_STAGE": "floorplan", "NUM_CORES": str(cores), "OMP_NUM_THREADS": str(cores)})
        if rc != 0:
            self._fail(jid, name, "flow", "布局布线失败（rc=%s）：%s" % (rc, self._last_error(pdir)),
                       {"log_tail": self._tail(pdir, "flow")})
            self._ingest(jid, name, pdir)
            return
        self._ok(jid, name, "flow", "布局布线完成")
        # signoff
        self._stage(jid, name, "signoff")
        self._sh(jid, name, "signoff", ["bash", str(tools_dir() / "run_signoff.sh"), str(pdir), platform], pdir)
        v = signoff_verdict(pdir)
        self.store.set_design(jid, name, detail={"signoff": v})
        (self._ok if v["dual_clean"] else self._fail_soft)(
            jid, name, "signoff", "DRC %s（%s 处），LVS %s" % (v["drc"], v["drc_violations"] or 0, v["lvs"]), v)
        # repair
        if not v["dual_clean"]:
            self._stage(jid, name, "repair", "自动修复：诊断 → 修改 → 重跑 → 重新签核")
            self._sh(jid, name, "repair", ["bash", S + "/flow/fix_signoff.sh", str(pdir), platform,
                                           "--check", "both", "--max-iters", "3"], pdir)
            v = signoff_verdict(pdir)
            fixes = [json.loads(l) for l in (pdir / "reports" / "fix_log.jsonl").read_text().splitlines()
                     if l.strip()] if (pdir / "reports" / "fix_log.jsonl").is_file() else []
            self.store.set_design(jid, name, detail={"signoff": v, "repair": fixes[-5:]})
            if not v["dual_clean"]:
                self._fail(jid, name, "repair", "修复后仍未双清：DRC %s，LVS %s" % (v["drc"], v["lvs"]), v)
                self._ingest(jid, name, pdir)
                return
            self._ok(jid, name, "repair", "修复成功，DRC/LVS 均已清零", v)
        self._ingest(jid, name, pdir)
        if output == "image":
            n_ok, n_all = self._image_dataset(jid, name, pdir, platform)
            if n_ok is None:
                return
            self._preview(jid, name, pdir)
            self.store.set_design(jid, name, status=PASSED, stage="done",
                                  detail={"verify": {"passed": n_ok, "total": n_all}, "output": "image"})
            self.store.event(jid, name, "done", "ok", "完成：签核通过，图像数据集已校验")
            return
        # graph
        self._stage(jid, name, "graph")
        rc = self._sh(jid, name, "graph", ["bash", str(defgraph_scripts() / "flow" / "run_graphs.sh"),
                                           str(pdir), platform], pdir)
        man = read_json(pdir / "reports" / "graph_dataset.json")
        if rc != 0 or man.get("status") not in ("ok", "ok_with_label_gaps"):
            self._fail(jid, name, "graph", "转图失败（rc=%s，status=%s）" % (rc, man.get("status")))
            return
        self._ok(jid, name, "graph", "图数据集已生成（%s）" % man.get("status"),
                 {"status": man.get("status"), "graph_kind": man.get("graph_kind")})
        # verify
        self._stage(jid, name, "verify")
        gp = graph_python()
        vj = pdir / "reports" / "verify.json"
        rc = self._sh(jid, name, "verify", [gp or "python3", str(tools_dir() / "verify_graph_dataset.py"),
                                            str(pdir), "--json", str(vj)], pdir)
        res = read_json(vj).get("results") or []
        n_ok = sum(1 for r in res if r.get("ok"))
        if rc != 0 or not res or n_ok != len(res):
            # name the failing checks: "288/295" alone tells nobody whether it is a
            # stale input, a broken label or a physically extreme but genuine value
            bad = [r.get("check") for r in res if not r.get("ok")]
            self.store.set_design(jid, name, detail={"verify": {"passed": n_ok, "total": len(res),
                                                                "failed_checks": bad[:20]}})
            self._fail(jid, name, "verify", "数据集校验未通过：%d/%d，未通过项：%s"
                       % (n_ok, len(res), "；".join(bad[:5]) + ("……" if len(bad) > 5 else "")),
                       {"failed_checks": bad[:20]})
            return
        self._ok(jid, name, "verify", "数据集校验通过：%d/%d 项" % (n_ok, len(res)),
                 {"checks": len(res)})
        self._preview(jid, name, pdir)
        self.store.set_design(jid, name, status=PASSED, stage="done",
                              detail={"verify": {"passed": n_ok, "total": len(res)}})
        self.store.event(jid, name, "done", "ok", "完成：签核通过，数据集已校验")

    def _preview(self, jid, name, pdir: Path) -> None:
        """Layout picture for the UI (best effort, never decides anything)."""
        self._stage(jid, name, "preview")
        gds = latest_run(pdir) / "final" / "6_final.gds"
        if gds.is_file():
            self._sh(jid, name, "preview", ["python3", str(signoff_scripts() / "dashboard" / "render_gds_preview.py"),
                                            str(gds), str(pdir / "reports" / "layout.png"), "1200"], pdir, timeout=300)

    def _image_dataset(self, jid, name, pdir: Path, platform: str):
        """def-graph run_images.sh: GCell feature maps from the placement DEF,
        labels from the routed result, then its independent verifier. Returns
        (passed, total) or (None, None) after recording the failure."""
        self._stage(jid, name, "graph", "生成图像数据集（特征取自放置后版图，标签取自布线后）")
        rc = self._sh(jid, name, "graph", ["bash", str(defgraph_scripts() / "flow" / "run_images.sh"),
                                           str(pdir), platform], pdir)
        rep = read_json(pdir / "reports" / "image_dataset.json")
        v = rep.get("verify") or {}
        if rc != 0 or rep.get("status") != "ok":
            bad = v.get("failed_checks") or []
            self._fail(jid, name, "verify" if v else "graph",
                       "图像数据集未通过（status=%s）%s" % (rep.get("status"), rep.get("reason") or "")
                       + ("，未通过项：" + "；".join(bad[:5]) if bad else ""), rep)
            return None, None
        self.store.set_design(jid, name, detail={"image": {"shape": rep.get("shape"), "features": rep.get("features"),
                                                           "labels": rep.get("labels")}})
        self._ok(jid, name, "verify", "图像数据集校验通过：%d/%d 项（%s 网格，%d 个特征通道 + %d 个标签通道）"
                 % (v["passed"], v["total"], "×".join(map(str, rep.get("shape") or [])),
                    len(rep.get("features") or []), len(rep.get("labels") or [])), rep)
        return v["passed"], v["total"]

    def _fail_soft(self, jid, name, stage, msg, data=None):
        self.store.event(jid, name, stage, "warn", msg, data)

    def _ingest(self, jid, name, pdir: Path) -> None:
        """Every flow -- clean, failed or partial -- goes into the knowledge store."""
        if not self.ingest:
            return
        ing = skills_dir() / "signoff-loop" / "knowledge" / "ingest_run.py"
        try:
            self._sh(jid, name, "ingest", ["python3", str(ing), str(pdir)], pdir, timeout=600)
        except Cancelled:
            raise
        except Exception:                                         # noqa: BLE001
            pass

    @staticmethod
    def _synth_log_text(pdir: Path, run: Path | None) -> str:
        """The agent's synth log plus every ORFS log of the run (yosys writes its
        warnings there, not to stdout)."""
        parts = []
        for p in [pdir / "agent_logs" / "synth.log"] + (sorted((run / "logs").rglob("*.log")) if run else []):
            try:
                parts.append(p.read_text(errors="replace"))
            except OSError:
                pass
        return "\n".join(parts)

    @staticmethod
    def _tail(pdir: Path, stage: str, n: int = 40) -> str:
        p = pdir / "agent_logs" / ("%s.log" % stage)
        try:
            return "\n".join(p.read_text(errors="replace").splitlines()[-n:])
        except OSError:
            return ""

    @staticmethod
    def _last_error(pdir: Path) -> str:
        run = latest_run(pdir)
        if not run:
            return "无运行记录"
        for f in sorted((run / "logs").rglob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:6]:
            m = re.findall(r"\[ERROR [A-Z]+-\d+\][^\n]{0,120}", f.read_text(errors="replace"))
            if m:
                return m[-1]
        return "见日志"
