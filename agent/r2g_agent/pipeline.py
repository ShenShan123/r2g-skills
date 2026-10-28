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

import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import diagnosis, frontend, housekeeping, knowledge, profile
from . import project as proj
from .jobs import CANCELLED, CANCELLING, FAILED, PASSED, RUNNING, JobStore
from .processes import stop_orphan as stop_execution
from .paths import defgraph_scripts, r2g_home, repo_root, signoff_scripts, skills_dir, tools_dir

STAGES = ["prepare", "synth", "size", "flow", "signoff", "repair", "graph", "verify", "preview"]
# the "graph" stage builds whichever dataset the job asked for (graph or image)
OUTPUTS = ("graph", "image")
STAGE_LABEL = {"prepare": "准备工程", "synth": "综合", "size": "定尺寸与时钟", "flow": "按最终时钟综合并布局布线",
               "signoff": "签核 DRC/LVS", "repair": "自动修复", "graph": "生成数据集", "verify": "数据集校验",
               "preview": "版图预览"}


class Cancelled(Exception):
    pass


class StopUnconfirmed(Exception):
    pass


MAX_RESTARTS = 2


def _proc_start(pid: int) -> str | None:
    """Kernel start time of a process (field 22 of /proc/<pid>/stat), None if gone."""
    try:
        stat = Path("/proc/%d/stat" % pid).read_text()
    except OSError:
        return None
    return stat.rsplit(")", 1)[1].split()[19]


# this process, as an owner a later process can test for liveness; the start time
# guards against a recycled pid
OWNER = "%d:%s" % (os.getpid(), _proc_start(os.getpid()))


def _group_members(pgid: int) -> list[int]:
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if int((d / "stat").read_text().rsplit(")", 1)[1].split()[2]) == pgid:
                    out.append(int(d.name))
            except (OSError, ValueError, IndexError):
                pass
    return out


def stop_orphan(pgid, project: str, grace: float = 10.0) -> bool:
    """Stop a dead server's stage process group -- only if some member's command
    line names this design's project (a recycled pgid is someone else's)."""
    if not pgid or not project:
        return False
    members = _group_members(int(pgid))
    ours = False
    for pid in members:
        try:
            if project.encode() in Path("/proc/%d/cmdline" % pid).read_bytes():
                ours = True
                break
        except OSError:
            pass
    if not ours:
        return False
    try:
        os.killpg(int(pgid), signal.SIGTERM)
        deadline = time.time() + grace
        while time.time() < deadline and _group_members(int(pgid)):
            time.sleep(0.5)
        if _group_members(int(pgid)):
            os.killpg(int(pgid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    return True


def owner_alive(owner: str | None) -> bool:
    if not owner:
        return False
    pid, _, start = owner.partition(":")
    return pid.isdigit() and _proc_start(int(pid)) == start


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


# KLayout's bound per DRC run. run_signoff.sh defaults to 1 h, and 5 of the first
# 57 designs of the n45 reproduction (61k-99k cells) hit it in the deck's antenna
# section (FreePDK45.lydrc:376) and read "stuck" -- the same layouts the experiment
# graded under 6 h (experiments/signoff45.py --timeout 21600). Same ruler, same bound.
DRC_TIMEOUT_S = 6 * 3600


def signoff_verdict(project: Path) -> dict:
    drc = read_json(project / "reports" / "drc.json")
    lvs = read_json(project / "reports" / "lvs.json")
    v = {
        "drc": drc.get("status") or "missing",
        "drc_violations": drc.get("total_violations"),
        "lvs": lvs.get("status") or "missing",
        "lvs_mismatches": lvs.get("mismatch_count"),
        "drc_scope": drc.get("scope_note") or drc.get("note"),
    }
    v["dual_clean"] = v["drc"] == "clean" and v["lvs"] == "clean"
    return v


class Pipeline:
    """Runs designs of a job on a thread pool; one subprocess tree per stage."""

    def __init__(self, store: JobStore, workers: int = 2, cores_per_design: int = 8,
                 ingest: bool = True, stage_timeout: int = 6 * 3600, min_free_gb: float = 50,
                 keep_intermediate: bool = False):
        self.store = store
        self.min_free_gb = min_free_gb
        self.keep_intermediate = keep_intermediate
        # the real limit is the machine-wide slots (self.workers, changeable while
        # running); the pool only needs enough threads to hold that many plus waiters
        self.pool = ThreadPoolExecutor(max_workers=max(32, workers * 2))
        self.workers = max(1, workers)
        self.cores = cores_per_design
        # Opt-in experiment control: preserve the requested thread budget even
        # for small designs. Normal interactive runs retain their adaptive cap.
        self.fixed_cores = os.environ.get("R2G_FIXED_CORES") == "1"
        self.ingest = ingest
        self.stage_timeout = stage_timeout
        self._procs: dict[tuple, subprocess.Popen] = {}
        # the user's own knowledge store (seeded from the shipped one, which the
        # skills refuse to write); every stage reads and writes this copy
        self.knowledge = knowledge.ensure() if ingest else {"action": "disabled"}
        self._cancel: set[str] = set()
        self.stop_grace = 10.0
        self._lock = threading.Lock()

    # -- public -------------------------------------------------------------
    def submit(self, sources: list[dict], platform: str = "nangate45", title: str = "",
               output: str = "graph", params: dict | None = None, session_id: str | None = None) -> str:
        """sources: [{"name":..., "path":..., "top": optional}]"""
        if output not in OUTPUTS:
            raise ValueError("output must be one of %s" % ", ".join(OUTPUTS))
        profile.merge(platform, params)          # a bad value is refused here, not per design
        designs, seen = [], set()
        for s in sources:
            name = re.sub(r"[^A-Za-z0-9_]+", "_", s.get("name") or Path(s["path"]).name)
            while name in seen:
                name += "_x"
            seen.add(name)
            designs.append({"name": name, "detail": {"source": str(s["path"]), "top": s.get("top")}})
        spec = {"platform": platform, "output": output, "sources": sources, "params": params or {}}
        if session_id:
            spec['session_id'] = session_id
        jid = self.store.create_job(title or "%d 个设计 · %s · %s" % (len(designs), platform, output),
                                    spec, designs)
        ws = r2g_home() / "workspace" / jid
        for d in designs:
            # FLOW_VARIANT comes from the project folder name, so it carries the
            # job id: two jobs on one design must never share ORFS result dirs
            pdir = ws / ("%s__%s" % (d["name"], jid))
            self.store.set_design(jid, d["name"], project=str(pdir), detail={"owner": OWNER})
            self.pool.submit(self._run_design, jid, d["name"], Path(d["detail"]["source"]),
                             d["detail"].get("top"), pdir, platform, output)
        self.store.set_job(jid, RUNNING)
        threading.Thread(target=self._watch_job, args=(jid,), daemon=True).start()
        return jid

    def shutdown(self) -> None:
        """Server stopping: stop every running stage group (the designs restart on
        the next start, so nothing may keep writing to their FLOW_VARIANT)."""
        with self._lock:
            procs = list(self._procs.values())
        for p in procs:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def cancel(self, jid: str) -> dict | None:
        result = self.store.request_cancel(jid)
        if result and result["cancel_requested"]:
            first_here = jid not in self._cancel
            self._cancel.add(jid)
            if not self.finalize_if_done(jid) and first_here:
                threading.Thread(target=self._watch_job, args=(jid,), daemon=True).start()
            result["status"] = self.store.job(jid)["status"]
        return result

    def _check_cancel(self, jid: str) -> None:
        if jid in self._cancel or self.store.cancel_requested(jid) or self.store.deletion('job', jid) or not self.store.job(jid):
            raise Cancelled()

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
        if not j or j["status"] not in (RUNNING, "pending", CANCELLING):
            return True
        sts = [d["status"] for d in j["designs"]]
        if not all(s in (PASSED, FAILED, CANCELLED) for s in sts):
            return False
        # the web server's watcher and a terminal `r2g status` can both get here;
        # only the one that claims the job packages it and writes "任务结束"
        if not self.store.claim_job(jid):
            return True
        n_ok = sts.count(PASSED)
        status = CANCELLED if self.store.cancel_requested(jid) else ("done" if n_ok else FAILED)
        result = {"passed": n_ok, "total": len(sts)}
        if n_ok and not self.store.deletion('job', jid):
            from .package import package_job
            try:
                result["package"] = str(package_job(self.store, jid))
            except Exception as e:                              # noqa: BLE001
                result["package_error"] = str(e)
        # Cancellation may arrive during packaging, after the initial read.
        if self.store.cancel_requested(jid):
            status = CANCELLED
        self.store.set_job(jid, status, result)
        self.store.event(jid, None, "job", "done",
                         "任务结束：%d/%d 个设计通过并产出数据集" % (n_ok, len(sts)), result)
        return True

    def recover(self, resume: bool = False) -> list[str]:
        """At start-up: close jobs that finished while no watcher was alive, and
        deal with designs whose owning process has died.

        Only a DEAD owner's designs are touched: `r2g status` or `r2g run` in a
        terminal also starts a runtime, and a live web server's designs are its own.

        resume=True (the long-lived web server): a dead owner's queued designs are
        queued again here and its interrupted ones restart from the beginning, so a
        restart in the middle of a 2000-design batch loses nothing. A design
        interrupted MAX_RESTARTS times fails instead of looping. resume=False (a
        terminal command): nothing is adopted -- the next web server will -- and
        nothing is marked failed."""
        touched = []
        for j in self.store.jobs(5000):
            if j["status"] not in (RUNNING, "pending", "finalizing", CANCELLING):
                continue
            jid = j["id"]
            full = self.store.job(jid)
            act = [d for d in full["designs"] if d["status"] in (RUNNING, "pending")]
            if any(owner_alive((d.get("detail") or {}).get("owner")) for d in act):
                continue            # another live process owns this job and will close it
            if j["status"] == "finalizing":
                # its closer died mid-package: let one runtime take it back
                if time.time() - (j.get("updated") or 0) > 600 and self.store.claim_job(jid, ("finalizing",), RUNNING):
                    self.finalize_if_done(jid)
                continue
            if not act:
                self.finalize_if_done(jid)
                touched.append(jid)
                continue
            if not resume:
                continue
            spec = full.get("spec") or {}
            n = 0
            for d in act:
                det = d.get("detail") or {}
                if not self.store.claim_design(jid, d["name"], det.get("owner"), OWNER):
                    continue
                if self.store.cancel_requested(jid):
                    safe = d["status"] == "pending" or stop_execution(
                        det.get("pgid"), d["project"], self.stop_grace,
                        expected_start=det.get("process_start"), execution_id=det.get("execution_id"))
                    if safe:
                        self.store.set_design(jid, d["name"], status=CANCELLED)
                    else:
                        self.store.event(jid, d["name"], "job", "warn", "停止尚未确认，保留执行记录供核对")
                    continue
                if d["status"] == RUNNING:
                    stop_orphan(det.get("pgid"), d["project"])
                if not det.get("source") or not d.get("project"):
                    self.store.set_design(jid, d["name"], status=FAILED,
                                          detail={"failed_stage": d["stage"], "reason": "记录不完整，无法恢复（可重新提交）"})
                    continue
                restarts = int(det.get("restarts") or 0) + (1 if d["status"] == RUNNING else 0)
                if restarts > MAX_RESTARTS:
                    msg = "运行中被中断了 %d 次，不再重试（可单独重新提交）" % restarts
                    self.store.set_design(jid, d["name"], status=FAILED,
                                          detail={"failed_stage": d["stage"], "reason": msg})
                    self.store.event(jid, d["name"], d["stage"], "fail", msg)
                    continue
                self.store.set_design(jid, d["name"], status="pending", stage="prepare",
                                      detail={"restarts": restarts})
                self.store.event(jid, d["name"], "prepare", "warn",
                                 "服务重启后从头重新运行" if d["status"] == RUNNING else "服务重启后继续排队")
                self.pool.submit(self._run_design, jid, d["name"], Path(det["source"]), det.get("top"),
                                 Path(d["project"]), spec.get("platform", "nangate45"), spec.get("output", "graph"))
                n += 1
            if n:
                self.store.event(jid, None, "job", "info", "服务重启后恢复了 %d 个未完成的设计" % n)
                threading.Thread(target=self._watch_job, args=(jid,), daemon=True).start()
            else:
                self.finalize_if_done(jid)
            touched.append(jid)
        return touched

    def _flow_cores(self, cells: int) -> int:
        return self.cores if self.fixed_cores or cells >= 5000 else min(self.cores, 4)

    def _sh(self, jid: str, name: str, stage: str, cmd: list[str], project: Path,
            env: dict | None = None, timeout: int | None = None) -> int:
        self._check_cancel(jid)
        logdir = project / "agent_logs"
        logdir.mkdir(parents=True, exist_ok=True)
        e = dict(os.environ)
        e.update({"NUM_CORES": str(self.cores), "OMP_NUM_THREADS": str(self.cores),
                  "ORFS_TIMEOUT": str(self.stage_timeout)})
        if self.ingest:
            e.update(knowledge.env())
        e.update(env or {})
        if stage in ('flow', 'repair'):
            e.update(R2G_STAGE_VIEW_PYTHON=sys.executable, R2G_STAGE_VIEW_PHASE=stage)
        invocation = uuid.uuid4().hex
        e["EDACODE_EXECUTION_ID"] = invocation
        if e.get("R2G_ORFS_AUDIT_FILE"):
            # Children include signoff restaging and repair wrappers, not just
            # the pipeline's explicit run_orfs invocations.
            audit_bin = str(signoff_scripts() / "flow" / "orfs_audit_bin")
            e.setdefault("R2G_ORFS_REAL_MAKE", shutil.which("make", path=e.get("PATH")) or "/usr/bin/make")
            e["PATH"] = audit_bin + os.pathsep + e.get("PATH", os.defpath)
            e.update(R2G_ORFS_AUDIT_PYTHON=sys.executable,
                     R2G_ORFS_AUDIT_JOB=jid, R2G_ORFS_AUDIT_DESIGN=name,
                     R2G_ORFS_AUDIT_STAGE=stage, R2G_ORFS_AUDIT_PROJECT=str(project),
                     R2G_ORFS_AUDIT_EXECUTION=invocation)
        with open(logdir / ("%s.log" % stage), "ab") as log:
            log.write(("\n$ %s\n" % " ".join(cmd)).encode())
            log.flush()
            self._check_cancel(jid)
            p = subprocess.Popen(cmd, cwd=repo_root(), env=e, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
            with self._lock:
                self._procs[(jid, name)] = p
            # the stage's process group, so a restarted server can stop it if this
            # process dies without doing so (it would race the re-run on one FLOW_VARIANT)
            start = _proc_start(p.pid)
            try:
                self.store.set_design(jid, name, detail={"pgid": p.pid, "process_start": start,
                                                       "execution_id": invocation})
                deadline = time.monotonic() + (timeout or self.stage_timeout)
                while True:
                    self._check_cancel(jid)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        rc = 124
                        break
                    try:
                        rc = p.wait(timeout=min(1.0, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                # Includes setsid() children in the bounded EDA checker. A TERM
                # delivered to the shell alone is not proof that tools stopped.
                stopped = stop_execution(p.pid, str(project), self.stop_grace,
                                         expected_start=start, execution_id=invocation)
                with self._lock:
                    self._procs.pop((jid, name), None)
                if not stopped:
                    raise StopUnconfirmed("无法确认子进程已停止，未标记为已取消")
                p.wait()
        self._check_cancel(jid)
        return rc

    def _stage(self, jid: str, name: str, stage: str, msg: str | None = None) -> None:
        self._check_cancel(jid)
        self.store.set_design(jid, name, stage=stage, status=RUNNING)
        self._check_cancel(jid)
        self.store.event(jid, name, stage, "start", msg or STAGE_LABEL[stage])

    def _ok(self, jid, name, stage, msg, data=None):
        self._capture_view(jid, name, stage)
        self.store.event(jid, name, stage, "ok", msg, data)

    def _capture_view(self, jid, name, stage):
        from .stage_views import capture
        design = self.store.design(jid, name)
        if not design or not design.get('project'):
            return
        try:
            capture(Path(design['project']), stage, design.get('detail') or {})
        except Exception as exc:
            # Visualisation is optional and cannot change a design's verdict.
            self.store.event(jid, name, stage, 'info', '阶段预览暂不可用：' + str(exc))

    def _fail(self, jid, name, stage, msg, data=None):
        self._check_cancel(jid)
        self._capture_view(jid, name, stage)
        self.store.event(jid, name, stage, "fail", msg, data)
        self.store.set_design(jid, name, status=FAILED, detail={"failed_stage": stage, "reason": msg})

    def _run_design(self, jid: str, name: str, src: Path, top: str | None, pdir: Path,
                    platform: str, output: str = "graph") -> None:
        from .lifecycle import resource_lock
        with resource_lock('job', jid):
            self._run_design_locked(jid, name, src, top, pdir, platform, output)

    def _run_design_locked(self, jid: str, name: str, src: Path, top: str | None, pdir: Path,
                           platform: str, output: str = "graph") -> None:
        def stop():
            self._check_cancel(jid)

        def waiting():
            self.store.event(jid, name, "prepare", "start",
                             "排队中：本机已有 %d 个设计在跑（上限 %d，网页、命令行、MCP 共用）"
                             % (housekeeping.slots_in_use(self.workers), self.workers))
        try:
            housekeeping.wait_for_disk(lambda: self.min_free_gb, should_stop=stop, poll=20,
                                       on_wait=lambda free: self.store.event(
                                           jid, name, "prepare", "warn",
                                           "磁盘只剩 %.0f GB（下限 %g GB），暂停开始新设计；清理出空间后自动继续"
                                           % (free, self.min_free_gb)))
            with housekeeping.design_slot(lambda: self.workers, should_stop=stop, on_wait=waiting):
                safe_cleanup = True
                try:
                    stop()
                    self._run_design_inner(jid, name, src, top, pdir, platform, output)
                    stop()
                    if not self.keep_intermediate and self.store.design(jid, name)["status"] == PASSED:
                        freed = housekeeping.slim_passed(pdir)
                        if freed:
                            self.store.event(jid, name, "job", "info", "已删除中间版图文件 %.1f MB（数据集与报告保留）"
                                             % (freed / 1e6))
                except StopUnconfirmed:
                    safe_cleanup = False
                    raise
                finally:
                    # the ORFS checkout's per-variant dirs duplicate backend/RUN_*
                    freed = housekeeping.drop_orfs_copies(pdir) if safe_cleanup else 0
                    if freed:
                        self.store.event(jid, name, "job", "info",
                                         "已清理 ORFS 目录中的重复中间文件 %.1f MB" % (freed / 1e6))
        except Cancelled:
            self.store.set_design(jid, name, status=CANCELLED)
            self.store.event(jid, name, "job", "cancel", "已取消")
        except StopUnconfirmed as e:
            self.store.set_design(jid, name, detail={"reason": str(e)})
            self.store.event(jid, name, "job", "warn", str(e))
        except Exception as e:                                   # noqa: BLE001
            if self.store.cancel_requested(jid):
                self.store.set_design(jid, name, status=CANCELLED)
                self.store.event(jid, name, "job", "cancel", "已取消")
            else:
                self._fail(jid, name, self.store.design(jid, name).get("stage") or "prepare",
                           "内部错误：%s" % e)

    def _run_design_inner(self, jid, name, src, top, pdir, platform, output="graph") -> None:
        S = str(signoff_scripts())
        # prepare
        self._stage(jid, name, "prepare")
        params = (self.store.job(jid) or {}).get("spec", {}).get("params") or {}
        meta = proj.create(src, pdir, platform, top=top, name=name, params=params)
        if meta["profile_changed"]:
            self._ok(jid, name, "prepare", "使用自定义参数：%s" % "、".join(
                profile.FIELDS[k][0] if k in profile.FIELDS else k for k in meta["profile_changed"]))
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
            synth_log = self._synth_log_text(pdir, run)
            hits = frontend.match_synth_failure(synth_log,
                                                run / "results" / "1_2_yosys.v" if run else None, tried)
            if not hits:
                msg = "综合失败：%s" % (self._last_error(pdir) if run else "rc=%s" % rc)
                blocked = frontend.semantic_blockers(synth_log)
                if blocked:
                    msg += "；" + "；".join(b["reason"] for b in blocked)
                if meta["missing_data_files"]:
                    msg += "：缺少数据文件 %s" % "、".join(m["reference"] for m in meta["missing_data_files"])
                self._fail(jid, name, "synth", msg, {"log_tail": self._tail(pdir, "synth"),
                                                     "frontend_tried": tried,
                                                     "frontend_blocked": blocked})
                self._ingest(jid, name, pdir)
                return
            tried += [h["id"] for h in hits]
            for h in hits:
                if h["id"] == "sv_frontend":
                    try:
                        attempt = frontend.prepare_slang(pdir, meta)
                        self._ok(jid, name, "synth", "检测到源码解析失败，尝试 Slang 前端", {"recipe": h["id"]})
                        rc = self._sh(jid, name, "frontend", attempt["command"], pdir)
                        if rc != 0:
                            raise ValueError("Slang 转换失败（rc=%s），见 agent_logs/frontend.log" % rc)
                        meta = proj.activate_slang(pdir, attempt)
                    except (OSError, ValueError) as exc:
                        self._fail(jid, name, "synth", str(exc), {
                            "recipe": h["id"], "frontend_tried": tried,
                            "log_tail": self._tail(pdir, "frontend")})
                        self._ingest(jid, name, pdir)
                        return
                    fx_file = "frontend/slang.tcl"
                else:
                    meta = proj.add_synth_hooks(pdir, [h["id"]])
                    fx_file = "constraints/frontend_fix.tcl"
                fx = {"recipe": h["id"], "file": fx_file, "detail": h["label"]}
                meta["frontend_fixes"].append(fx)
                self._ok(jid, name, "synth", "前端修复：%s，重新综合" % h["label"], fx)
                (pdir / "metadata.json").write_text(json.dumps(meta, indent=2))
            self.store.set_design(jid, name, detail={"frontend_fixes": meta["frontend_fixes"]})
        # size
        self._stage(jid, name, "size")
        sizing = proj.apply_sizing(pdir, stat, run / "results" / "1_2_yosys.v")
        self.store.set_design(jid, name, detail={"sizing": sizing})
        self._ok(jid, name, "size", "%d 个单元，die %g µm（%s），时钟 %g ns（%s）"
                 % (sizing["cells"], sizing["die_side_um"],
                    {"area": "由面积决定", "pins": "由引脚数决定", "pdn_floor": "电源网格下限",
                     "fixed": "自定义固定值", "pins_ppl": "按引脚数修正"}[sizing["binding"]],
                    sizing["period_ns"], "按单元数分档" if sizing["period_source"] == "tiers" else "自定义固定值"),
                 sizing)
        if sizing["binding"] == "fixed" and sizing["rule_die_side_um"] > sizing["die_side_um"]:
            self._fail_soft(jid, name, "size", "固定 die %g µm 小于规则算出的 %g µm，可能放不下或拥塞"
                            % (sizing["die_side_um"], sizing["rule_die_side_um"]))
        cores = self._flow_cores(sizing["cells"])
        # flow
        self._stage(jid, name, "flow")
        # from synthesis again, under the final SDC: the first synthesis only measured
        # the design at the tiers' nominal period, exactly as the baseline's pilot did,
        # and the baseline then synthesised every design at its own period
        rc = self._sh(jid, name, "flow", ["bash", S + "/flow/run_orfs.sh", str(pdir), platform], pdir,
                      env={"NUM_CORES": str(cores), "OMP_NUM_THREADS": str(cores)})
        # place_pins refusing the IO count (PPL-0024): grow only the die, from the
        # design's own reported numbers, and redo from floorplan (at most twice)
        for _ in range(2):
            if rc == 0:
                break
            sz = proj.ppl_corrected_die(pdir, self._run_log_text(latest_run(pdir)))
            if not sz:
                break
            fx = {"recipe": "ppl_die_correction", "file": "constraints/config.mk",
                  "detail": "引脚放不下（PPL-0024：%d 个引脚，只有 %d 个位置），die %g → %g µm"
                            % (sz["ppl"]["pins"], sz["ppl"]["positions"], sz["ppl"]["die_before_um"], sz["die_side_um"])}
            meta = json.loads((pdir / "metadata.json").read_text())
            meta.setdefault("flow_fixes", []).append(fx)
            (pdir / "metadata.json").write_text(json.dumps(meta, indent=2))
            self.store.set_design(jid, name, detail={"sizing": sz, "flow_fixes": meta["flow_fixes"]})
            self._ok(jid, name, "flow", "自动修复：%s，从 floorplan 重跑" % fx["detail"], fx)
            rc = self._sh(jid, name, "flow", ["bash", S + "/flow/run_orfs.sh", str(pdir), platform], pdir,
                          env={"FROM_STAGE": "floorplan", "NUM_CORES": str(cores), "OMP_NUM_THREADS": str(cores)})
        if rc != 0 and self._repair_failed_route(jid, name, pdir, platform, cores):
            rc = 0
        if rc != 0:
            self._fail(jid, name, "flow", "布局布线失败（rc=%s）：%s" % (rc, self._last_error(pdir)),
                       {"log_tail": self._tail(pdir, "flow")})
            self._ingest(jid, name, pdir)
            return
        self._ok(jid, name, "flow", "布局布线完成")
        # signoff
        self._stage(jid, name, "signoff")
        self._sh(jid, name, "signoff", ["bash", str(tools_dir() / "run_signoff.sh"), str(pdir), platform], pdir,
                 env={"DRC_TIMEOUT": str(DRC_TIMEOUT_S), "LVS_TIMEOUT": str(DRC_TIMEOUT_S),
                      "NETGEN_TIMEOUT": str(DRC_TIMEOUT_S),
                      "R2G_POWERED_NETLIST_TIMEOUT": str(DRC_TIMEOUT_S)}, timeout=3 * DRC_TIMEOUT_S + 3600)
        v = signoff_verdict(pdir)
        self.store.set_design(jid, name, detail={"signoff": v})
        (self._ok if v["dual_clean"] else self._fail_soft)(
            jid, name, "signoff", "DRC %s（%s 处），LVS %s" % (v["drc"], v["drc_violations"] or 0, v["lvs"]), v)
        # repair
        if not v["dual_clean"]:
            self._stage(jid, name, "repair", "自动修复：诊断 → 修改 → 重跑 → 重新签核")
            self._sh(jid, name, "repair", ["bash", S + "/flow/fix_signoff.sh", str(pdir), platform,
                                           "--check", "both", "--max-iters", "3"], pdir,
                     env={"DRC_TIMEOUT": str(DRC_TIMEOUT_S), "LVS_TIMEOUT": str(DRC_TIMEOUT_S),
                          "NETGEN_TIMEOUT": str(DRC_TIMEOUT_S),
                          "R2G_POWERED_NETLIST_TIMEOUT": str(DRC_TIMEOUT_S)},
                     timeout=3 * (self.stage_timeout + 3 * DRC_TIMEOUT_S + 3600))
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
        self._capture_view(jid, name, 'graph')
        self._ok(jid, name, "verify", "图像数据集校验通过：%d/%d 项（%s 网格，%d 个特征通道 + %d 个标签通道）"
                 % (v["passed"], v["total"], "×".join(map(str, rep.get("shape") or [])),
                    len(rep.get("features") or []), len(rep.get("labels") or [])), rep)
        return v["passed"], v["total"]

    def _repair_failed_route(self, jid, name, pdir, platform, cores) -> bool:
        """One ordinary, promoted SKY130 area recipe, followed by normal signoff.

        A recipe's exit code alone never establishes a completed physical flow.
        No candidate means no physical retry. Failed attempts remain on disk.
        """
        if platform != "sky130hd":
            return False
        before = latest_run(pdir)
        if before is None:
            return False
        scripts = signoff_scripts()
        reports = pdir / "reports"
        reports.mkdir(exist_ok=True)
        rc = self._sh(jid, name, "flow", ["python3", str(scripts / "extract/extract_route.py"),
                      str(pdir), str(reports / "route.json")], pdir, timeout=60)
        route = read_json(reports / "route.json")
        if rc or route.get("backend_run") != before.name or route.get("status") not in ("fail", "timeout"):
            return False
        selection = reports / "route_repair_selection.tsv"
        rc = self._sh(jid, name, "flow", diagnosis.command(pdir, "route", "--next", selection),
                      pdir, timeout=300)
        selected = selection.read_text().strip().split("\t") if selection.is_file() else []
        if rc or selected != ["fixed_area_route_relief", "floorplan", "route"]:
            return False

        def inputs():
            paths = [pdir / "constraints/constraint.sdc"]
            paths += sorted(p for p in (pdir / "rtl").rglob("*") if p.is_file())
            return {str(p.relative_to(pdir)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

        original_inputs = inputs()
        (reports / "route_repair_before.json").write_text(json.dumps({
            "backend_run": before.name, "route": route, "input_sha256": original_inputs,
            "config": (pdir / "constraints/config.mk").read_text(),
            "selected": selected, "forced_selection": False}, indent=2))
        self._stage(jid, name, "repair", "布线失败：按已验证策略调整面积，重跑并重新签核")
        rc = self._sh(jid, name, "repair", ["bash", str(scripts / "flow/fix_signoff.sh"),
                      str(pdir), platform, "--check", "route", "--max-iters", "1"], pdir,
                      env={"NUM_CORES": str(cores), "OMP_NUM_THREADS": str(cores)},
                      timeout=self.stage_timeout + 600)
        after = latest_run(pdir)
        route = read_json(reports / "route.json")
        complete = (rc == 0 and after is not None and after != before
                    and read_json(after / "run-meta.json").get("make_status") == 0
                    and route.get("backend_run") == after.name
                    and route.get("status") == "clean" and route.get("completed") is True
                    and any(p.stat().st_size for p in (after / "final").glob("*.gds"))
                    and inputs() == original_inputs)
        (reports / "route_repair_result.json").write_text(json.dumps({
            "returncode": rc, "physical_flow_completed": complete,
            "before_run": before.name, "after_run": after.name if after else None,
            "route": route, "input_sha256": inputs(), "signoff_pending": complete}, indent=2))
        if not complete:
            return False
        meta = read_json(pdir / "metadata.json")
        fx = {"recipe": selected[0], "file": "constraints/config.mk",
              "detail": "布线恢复完成，等待 DRC/LVS 签核", "rerun_from": "floorplan"}
        meta.setdefault("flow_fixes", []).append(fx)
        (pdir / "metadata.json").write_text(json.dumps(meta, indent=2))
        self.store.set_design(jid, name, detail={"flow_fixes": meta["flow_fixes"]})
        self._ok(jid, name, "repair", fx["detail"], fx)
        return True

    def _fail_soft(self, jid, name, stage, msg, data=None):
        if stage == 'signoff':
            self._capture_view(jid, name, stage)
        self.store.event(jid, name, stage, "warn", msg, data)

    def _ingest(self, jid, name, pdir: Path) -> None:
        """Every flow -- clean, failed or partial -- goes into the knowledge store."""
        if not self.ingest:
            return
        ing = skills_dir() / "signoff-loop" / "knowledge" / "ingest_run.py"
        try:
            rc = self._sh(jid, name, "ingest", ["python3", str(ing), str(pdir), "--db",
                                                knowledge.env()["R2G_KNOWLEDGE_DB"]], pdir, timeout=600)
        except Cancelled:
            raise
        except Exception as e:                                    # noqa: BLE001
            rc, err = -1, str(e)
        else:
            err = self._tail(pdir, "ingest", 3)
        # never silent: a swallowed refusal here once left every agent run unrecorded
        if rc != 0:
            self._fail_soft(jid, name, "ingest", "未能写入知识库（rc=%s）：%s" % (rc, err[-300:]))
            return
        ok, msg = knowledge.learn()
        if not ok:
            self._fail_soft(jid, name, "ingest", "已写入知识库，但重新学习修复经验失败：%s" % msg[-300:])

    @staticmethod
    def _run_log_text(run: Path | None) -> str:
        parts = []
        for p in (sorted((run / "logs").rglob("*.log")) if run else []):
            try:
                parts.append(p.read_text(errors="replace"))
            except OSError:
                pass
        return "\n".join(parts)

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
            t = f.read_text(errors="replace")
            m = re.findall(r"\[ERROR [A-Z]+-\d+\][^\n]{0,120}", t)
            if m:
                return m[-1]
            # yosys: "<file>:<line>: ERROR: <what>" -- keep file name and line, drop the long path
            y = re.findall(r"([^\s/]+:\d+): ERROR: ([^\n]{0,120})", t) or re.findall(r"^ERROR: ([^\n]{0,140})", t, re.M)
            if y:
                return "%s：%s" % y[0] if isinstance(y[0], tuple) else y[0]
        return "见日志"
