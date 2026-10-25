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

from . import frontend, housekeeping, knowledge, profile
from . import project as proj
from .jobs import CANCELLED, CANCELLING, RECOVERY_REQUIRED, FAILED, PASSED, RUNNING, JobStore
from .processes import boot_id, proc_start as _proc_start, stop_orphan
from .paths import defgraph_scripts, r2g_home, repo_root, signoff_scripts, skills_dir, tools_dir

STAGES = ["prepare", "synth", "size", "flow", "signoff", "repair", "graph", "verify", "preview"]
# the "graph" stage builds whichever dataset the job asked for (graph or image)
OUTPUTS = ("graph", "image")
STAGE_LABEL = {"prepare": "准备工程", "synth": "综合", "size": "定尺寸与时钟", "flow": "按最终时钟综合并布局布线",
               "signoff": "签核 DRC/LVS", "repair": "自动修复", "graph": "生成数据集", "verify": "数据集校验",
               "preview": "版图预览"}


class Cancelled(Exception):
    pass


MAX_RESTARTS = 2


BOOT_ID = boot_id()
OWNER = "%d:%s:%s" % (os.getpid(), _proc_start(os.getpid()), BOOT_ID)


class RecoveryRequired(RuntimeError):
    pass


class LostOwnership(RuntimeError):
    pass


def owner_alive(owner: str | None) -> bool:
    if not owner:
        return False
    parts = owner.split(":")
    if len(parts) not in (2, 3) or not parts[0].isdigit():
        return False
    if len(parts) == 3 and parts[2] != BOOT_ID:
        return False
    try:
        return _proc_start(int(parts[0])) == parts[1]
    except RuntimeError:
        return True  # uncertain liveness is not permission to adopt


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
    rcx = read_json(project / "reports" / "rcx.json")
    v = {
        "drc": drc.get("status") or "missing",
        "drc_violations": drc.get("total_violations"),
        "lvs": lvs.get("status") or "missing",
        "lvs_mismatches": lvs.get("mismatch_count"),
        "rcx": rcx.get("status") or "missing",
    }
    v["dual_clean"] = v["drc"] == "clean" and v["lvs"] == "clean"
    if lvs.get("reason") == "layout_changed_under_signoff":
        v["infrastructure_error"] = "LVS 检查期间版图被重建，签核证据已失效"
    elif v["lvs"] not in ("clean", "fail"):
        v["infrastructure_error"] = "LVS 未获得可用的匹配判定（%s），需要检查工具与输入" % (
            lvs.get("reason") or v["lvs"])
    elif v["drc"] not in ("clean", "fail", "violations", "stuck"):
        v["infrastructure_error"] = "DRC 未获得完整的规则检查结果（%s），需要检查工具与输入" % v["drc"]
    elif v["rcx"] != "complete":
        v["infrastructure_error"] = "RCX 未确认完成（%s），不能生成已验证数据集" % v["rcx"]
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
        self.ingest = ingest
        self.stage_timeout = stage_timeout
        self._procs: dict[tuple, subprocess.Popen] = {}
        # the user's own knowledge store (seeded from the shipped one, which the
        # skills refuse to write); every stage reads and writes this copy
        self.memory_env = knowledge.env()
        self.knowledge = knowledge.ensure() if ingest else {"action": "disabled"}
        self._shutdown = threading.Event()
        self.stop_grace = 10.0
        self._lock = threading.Lock()

    # -- public -------------------------------------------------------------
    def submit(self, sources: list[dict], platform: str = "nangate45", title: str = "",
               output: str = "graph", params: dict | None = None, *, submission: dict | None = None) -> str:
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
        if submission:
            from .submissions import source_snapshot
            from .turns import digest
            submission = {**submission, "source_snapshot": source_snapshot(sources)}
            submission["request_hash"] = digest({"spec": spec, "title": title,
                                                  "source_snapshot": submission["source_snapshot"],
                                                  "session_id": submission.get("session_id"),
                                                  "turn_id": submission.get("turn_id"),
                                                  "action_id": submission.get("action_id")})
            spec["source_snapshot"] = submission["source_snapshot"]
        jid, created = self.store.create_submission(
            title or "%d 个设计 · %s · %s" % (len(designs), platform, output), spec, designs, submission)
        if not created:
            return jid
        ws = r2g_home() / "workspace" / jid
        for d in designs:
            # FLOW_VARIANT comes from the project folder name, so it carries the
            # job id: two jobs on one design must never share ORFS result dirs
            pdir = ws / ("%s__%s" % (d["name"], jid))
            self.store.set_design(jid, d["name"], project=str(pdir), detail={"owner": OWNER, "execution_protocol": 1})
            self.pool.submit(self._run_design, jid, d["name"], Path(d["detail"]["source"]),
                             d["detail"].get("top"), pdir, platform, output)
        self.store.set_job(jid, RUNNING)
        threading.Thread(target=self._watch_job, args=(jid,), daemon=True).start()
        return jid

    def shutdown(self) -> None:
        """Server stopping: stop every running stage group (the designs restart on
        the next start, so nothing may keep writing to their FLOW_VARIANT)."""
        self._shutdown.set()
        with self._lock:
            procs = list(self._procs.values())
        for p in procs:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def cancel(self, jid: str) -> dict | None:
        # Any service instance can cancel; the worker polls the persisted intent.
        return self.store.request_cancel(jid)

    def _check_cancel(self, jid: str) -> None:
        if self.store.cancel_requested(jid):
            raise Cancelled()
        if self._shutdown.is_set():
            raise RecoveryRequired("服务正在停止，已保留执行记录等待恢复核对")

    def _update(self, jid: str, name: str, **kwargs) -> None:
        if not self.store.set_design(jid, name, expected_owner=OWNER, **kwargs):
            self._check_cancel(jid)
            raise LostOwnership("worker no longer owns a mutable design")

    def _blocked(self, jid, name, reason):
        changed = self.store.set_design(jid, name, status=RECOVERY_REQUIRED, expected_owner=OWNER,
                              detail={"reason": reason},
                              event=("job", "recovery_required", reason, {}))
        if changed:
            self.store.set_job(jid, RECOVERY_REQUIRED)

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
        if self.store.active_executions(jid):
            return False
        # the web server's watcher and a terminal `r2g status` can both get here;
        # only the one that claims the job packages it and writes "任务结束"
        if not self.store.claim_job(jid):
            return True
        n_ok = sts.count(PASSED)
        status = CANCELLED if self.store.cancel_requested(jid) else ("done" if n_ok else FAILED)
        result = {"passed": n_ok, "total": len(sts)}
        if n_ok:
            from .package import package_job
            try:
                result["package"] = str(package_job(self.store, jid))
            except Exception as e:                              # noqa: BLE001
                result["package_error"] = str(e)
        self.store.set_job(jid, status, result, event=("job", "done",
                         "任务结束：%d/%d 个设计通过并产出数据集" % (n_ok, len(sts)), result))
        return True

    def recover(self, resume: bool = False) -> list[str]:
        """Adopt dead owners only; uncertain executions block instead of replaying."""
        touched = []
        for j in self.store.jobs(5000):
            if j["status"] not in (RUNNING, "pending", "finalizing", CANCELLING, RECOVERY_REQUIRED):
                continue
            jid = j["id"]
            full = self.store.job(jid)
            act = [d for d in full["designs"] if d["status"] in (RUNNING, "pending", RECOVERY_REQUIRED)]
            if any(owner_alive((d.get("detail") or {}).get("owner")) for d in act):
                continue
            if j["status"] == "finalizing":
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
            queued, blocked = [], False
            for d in act:
                det = d.get("detail") or {}
                if not self.store.claim_design(jid, d["name"], det.get("owner"), OWNER):
                    continue
                run = self.store.execution(det["execution_id"]) if det.get("execution_id") else None
                safe = False
                if run:
                    if run["status"] in ("exited", "not_started", "interrupted"):
                        safe = True
                    elif run["pgid"] and run["boot_id"] == BOOT_ID:
                        safe = stop_orphan(run["pgid"], d["project"], self.stop_grace,
                                           expected_start=run["process_start"], execution_id=run["id"])
                        if safe:
                            self.store.update_execution(run["id"], OWNER, "interrupted")
                    # Missing handles or another boot are unresolved, not evidence of no spawn.
                elif det.get("execution_id"):
                    safe = False  # a broken reference is not proof of no spawn
                elif det.get("execution_protocol") == 1:
                    # This protocol always commits begin_execution before any Popen.
                    safe = True
                elif d["status"] == "pending" and d["stage"] == "queued":
                    safe = True  # the old runner changes stage before it can spawn
                # Legacy running records have no execution marker. Even an
                # empty original pgid cannot rule out a detached EDA child.
                if not safe:
                    if run:
                        self.store.update_execution(run["id"], OWNER, "recovery_required")
                    self._blocked(jid, d["name"], "无法确认旧执行已停止，需要恢复核对；未自动重跑")
                    blocked = True
                    continue
                if self.store.cancel_requested(jid):
                    self._update(jid, d["name"], status=CANCELLED,
                                 event=("job", "cancelled", "已确认旧执行停止，取消完成", {}))
                    continue
                if not det.get("source") or not d.get("project"):
                    self._update(jid, d["name"], status=FAILED,
                                 detail={"reason": "记录不完整，无法恢复"},
                                 event=("job", "fail", "记录不完整，无法恢复", {}))
                    continue
                restarts = int(det.get("restarts") or 0) + (d["status"] != "pending")
                if restarts > MAX_RESTARTS:
                    self._update(jid, d["name"], status=FAILED,
                                 detail={"reason": "运行中被中断过多，不再重试"},
                                 event=("job", "fail", "运行中被中断过多，不再重试", {}))
                    continue
                self._update(jid, d["name"], status="pending", stage="queued",
                             detail={"restarts": restarts, "execution_protocol": 1},
                             event=("prepare", "warn", "已核对旧执行，准备从头恢复", {}))
                queued.append(d)
            if not blocked:
                self.store.set_job(jid, RUNNING)
            for d in queued:
                det = d["detail"]
                self.pool.submit(self._run_design, jid, d["name"], Path(det["source"]), det.get("top"),
                                 Path(d["project"]), spec.get("platform", "nangate45"), spec.get("output", "graph"))
            if queued:
                threading.Thread(target=self._watch_job, args=(jid,), daemon=True).start()
            elif not blocked:
                self.finalize_if_done(jid)
            touched.append(jid)
        return touched

    def _sh(self, jid: str, name: str, stage: str, cmd: list[str], project: Path,
            env: dict | None = None, timeout: int | None = None) -> int:
        self._check_cancel(jid)
        from .input_snapshots import capture, reference, write_json
        bound = None
        if (project / "metadata.json").is_file():
            try:
                bound = reference(capture(project))
            except (OSError, ValueError) as exc:
                bound = {"unavailable": type(exc).__name__}
            bindings = (self.store.design(jid, name) or {}).get("detail", {}).get("input_bindings", {})
            bindings[stage] = {"before": bound, "unchanged": False}
            self._update(jid, name, detail={"input_bindings": bindings})
            write_json(project / "reports" / "input_bindings.json", bindings)
        invocation = self.store.begin_execution(jid, name, stage, OWNER, BOOT_ID, cmd)
        if invocation is None:
            self._check_cancel(jid)
            raise LostOwnership("execution admission refused")
        logdir = project / "agent_logs"
        e = {**os.environ, "NUM_CORES": str(self.cores), "OMP_NUM_THREADS": str(self.cores),
             "ORFS_TIMEOUT": str(self.stage_timeout), **self.memory_env, **(env or {}),
             "EDACODE_EXECUTION_ID": invocation}
        p = None
        confirmed = False
        start = None
        try:
            logdir.mkdir(parents=True, exist_ok=True)
            with open(logdir / (stage + ".log"), "ab") as log:
                log.write(("\n$ %s\n" % " ".join(cmd)).encode())
                log.flush()
                self._check_cancel(jid)
                p = subprocess.Popen(cmd, cwd=repo_root(), env=e, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
                start = _proc_start(p.pid)
                with self._lock:
                    self._procs[(jid, name)] = p
                if not self.store.update_execution(invocation, OWNER, "running", pgid=p.pid, process_start=start):
                    raise LostOwnership("execution owner changed after spawn")
                deadline = time.monotonic() + (timeout or self.stage_timeout)
                timed_out = False
                while True:
                    self._check_cancel(jid)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    try:
                        rc = p.wait(timeout=min(0.2, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        pass
                confirmed = stop_orphan(p.pid, str(project), self.stop_grace,
                                        expected_start=start, execution_id=invocation)
                if not confirmed:
                    raise RecoveryRequired("阶段结束但无法确认进程组已停止")
                p.wait(timeout=self.stop_grace + 1)
                if timed_out:
                    rc = 124
                if not self.store.update_execution(invocation, OWNER, "exited", exit_code=rc):
                    raise LostOwnership("execution owner changed before completion")
                if bound is not None:
                    try:
                        after = reference(capture(project))
                    except (OSError, ValueError) as exc:
                        after = {"unavailable": type(exc).__name__}
                    bindings[stage] = {"before": bound, "after": after,
                                       "unchanged": "sha256" in bound and after == bound,
                                       "execution_id": invocation, "exit_code": rc}
                    self._update(jid, name, detail={"input_bindings": bindings})
                    write_json(project / "reports" / "input_bindings.json", bindings)
                self._check_cancel(jid)
                return rc
        finally:
            if p is None:
                self.store.update_execution(invocation, OWNER, "not_started")
            else:
                if not confirmed:
                    confirmed = stop_orphan(p.pid, str(project), self.stop_grace,
                                            expected_start=start, execution_id=invocation)
                    if confirmed:
                        p.wait(timeout=self.stop_grace + 1)
                        self.store.update_execution(invocation, OWNER, "interrupted", exit_code=p.returncode)
                    else:
                        self.store.update_execution(invocation, OWNER, "recovery_required")
                with self._lock:
                    self._procs.pop((jid, name), None)
                if not confirmed:
                    raise RecoveryRequired("无法确认进程组已停止；保留工作目录等待恢复核对")

    def _stage(self, jid: str, name: str, stage: str, msg: str | None = None) -> None:
        self._check_cancel(jid)
        self._update(jid, name, stage=stage, status=RUNNING,
                     event=(stage, "start", msg or STAGE_LABEL[stage], {}))

    def _ok(self, jid, name, stage, msg, data=None):
        self.store.event(jid, name, stage, "ok", msg, data, expected_owner=OWNER)

    def _fail(self, jid, name, stage, msg, data=None):
        self._check_cancel(jid)
        self._update(jid, name, status=FAILED, detail={"failed_stage": stage, "reason": msg},
                     event=(stage, "fail", msg, data or {}))

    def _run_design(self, jid: str, name: str, src: Path, top: str | None, pdir: Path,
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
                try:
                    self._run_design_inner(jid, name, src, top, pdir, platform, output)
                    if not self.keep_intermediate and self.store.design(jid, name)["status"] == PASSED:
                        freed = housekeeping.slim_passed(pdir)
                        if freed:
                            self.store.event(jid, name, "job", "info", "已删除中间版图文件 %.1f MB（数据集与报告保留）"
                                             % (freed / 1e6))
                except (RecoveryRequired, LostOwnership):
                    raise
                else:
                    # Clean only after confirmed execution, never on an uncertain stop.
                    # the ORFS checkout's per-variant dirs duplicate backend/RUN_*
                    freed = housekeeping.drop_orfs_copies(pdir)
                    if freed:
                        self.store.event(jid, name, "job", "info",
                                         "已清理 ORFS 目录中的重复中间文件 %.1f MB" % (freed / 1e6))
        except Cancelled:
            self.store.set_design(jid, name, expected_owner=OWNER, status=CANCELLED,
                                  event=("job", "cancelled", "已确认停止，取消完成", {}))
        except RecoveryRequired as e:
            self._blocked(jid, name, str(e))
        except LostOwnership:
            return
        except Exception as e:                                   # noqa: BLE001
            # Cancellation or ownership may change while reporting an exception.
            # Do not let that secondary exception strand a design as running.
            if any(run["design"] == name for run in self.store.active_executions(jid)):
                self._blocked(jid, name, "执行异常且停止尚未确认：%s" % e)
            elif self.store.cancel_requested(jid):
                self.store.set_design(jid, name, expected_owner=OWNER, status=CANCELLED,
                                      event=("job", "cancelled", "已确认停止，取消完成", {}))
            else:
                self.store.set_design(jid, name, expected_owner=OWNER, status=FAILED,
                                      detail={"reason": "内部错误：%s" % e},
                                      event=("job", "fail", "内部错误：%s" % e, {}))

    def _capability_gate(self, jid, name, pdir, platform, output) -> bool:
        """Check tool/PDK prerequisites before spending synthesis/backend work.

        This observes availability, never awards a design or toolchain PASS.
        Unknown prerequisites stop alongside missing prerequisites; subsequent
        signoff and dataset checks remain mandatory on every admitted design.
        """
        from . import capabilities, task_skills
        from .input_snapshots import digest, write_json
        self._check_cancel(jid)
        observed = capabilities.inventory(platform)
        admission = capabilities.check_requirements(observed, output=output)
        contract = task_skills.load_skill("dataset_delivery")
        write_json(pdir / "reports" / "toolchain_capabilities.json", observed)
        write_json(pdir / "reports" / "task_contract.json", {
            "skill_id": contract["id"], "version": contract["version"],
            "contract_sha256": digest(contract), "contract": contract,
            "admission": admission, "scope": "prerequisite observation; per-design checks remain required"})
        self._check_cancel(jid)
        self._update(jid, name, detail={"capability_admission": admission,
                                       "task_contract_sha256": digest(contract)})
        if admission["verdict"] != "READY":
            reasons = admission["missing"] + admission["unsupported"] + admission["unverified"]
            self._fail(jid, name, "prepare", "工具/PDK 前置检查未通过：%s（%s）" % (
                admission["verdict"], "、".join(reasons[:8])),
                {"failure_class": "environment", "capability_admission": admission,
                 "backend_started": False})
            return False
        self._ok(jid, name, "prepare", "工具/PDK 前置条件已核对；仍需本设计的独立签核和数据集检查",
                 {"inventory_id": admission["inventory_id"], "combination_verification": "UNVERIFIED"})
        return True

    def _run_design_inner(self, jid, name, src, top, pdir, platform, output="graph") -> None:
        S = str(signoff_scripts())
        # prepare
        self._stage(jid, name, "prepare")
        params = (self.store.job(jid) or {}).get("spec", {}).get("params") or {}
        meta = proj.create(src, pdir, platform, top=top, name=name, params=params)
        from .repairs import prepare_child_receipt
        prepare_child_receipt(self.store, jid, name, pdir)
        if meta["profile_changed"]:
            self._ok(jid, name, "prepare", "使用自定义参数：%s" % "、".join(
                profile.FIELDS[k][0] if k in profile.FIELDS else k for k in meta["profile_changed"]))
        self._ok(jid, name, "prepare", "顶层模块 %s（%s），%d 个源文件"
                 % (meta["top"], meta.get("top_source") or "指定", len(meta["files"])),
                 {"top": meta["top"], "top_source": meta.get("top_source")})
        self._update(jid, name, detail={"top": meta["top"], "top_source": meta.get("top_source"),
                                                 "frontend_fixes": meta["frontend_fixes"]})
        for fx in meta["frontend_fixes"]:
            self._ok(jid, name, "prepare", "前端修复：%s（%s）" % (fx["detail"], fx["file"]), fx)
        for m in meta["missing_data_files"]:
            self._fail_soft(jid, name, "prepare", "缺少数据文件：%s 里的 $readmem 读取 %s，%s；请补传这个文件"
                            % (m["file"], m["reference"], m["reason"]), m)
        if not self._capability_gate(jid, name, pdir, platform, output):
            return
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
                msg = "综合失败：%s" % (self._last_error(pdir) if run else "rc=%s" % rc)
                if meta["missing_data_files"]:
                    msg += "：缺少数据文件 %s" % "、".join(m["reference"] for m in meta["missing_data_files"])
                self._ingest(jid, name, pdir)
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
            self._update(jid, name, detail={"frontend_fixes": meta["frontend_fixes"]})
        # size
        self._stage(jid, name, "size")
        sizing = proj.apply_sizing(pdir, stat, run / "results" / "1_2_yosys.v")
        self._update(jid, name, detail={"sizing": sizing})
        self._ok(jid, name, "size", "%d 个单元，die %g µm（%s），时钟 %g ns（%s）"
                 % (sizing["cells"], sizing["die_side_um"],
                    {"area": "由面积决定", "pins": "由引脚数决定", "pdn_floor": "电源网格下限",
                     "fixed": "自定义固定值", "pins_ppl": "按引脚数修正"}[sizing["binding"]],
                    sizing["period_ns"], "按单元数分档" if sizing["period_source"] == "tiers" else "自定义固定值"),
                 sizing)
        if sizing["binding"] == "fixed" and sizing["rule_die_side_um"] > sizing["die_side_um"]:
            self._fail_soft(jid, name, "size", "固定 die %g µm 小于规则算出的 %g µm，可能放不下或拥塞"
                            % (sizing["die_side_um"], sizing["rule_die_side_um"]))
        if sizing["cells"] < 5000:
            cores = min(self.cores, 4)    # small designs: more threads only thrash
        else:
            cores = self.cores
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
            self._update(jid, name, detail={"sizing": sz, "flow_fixes": meta["flow_fixes"]})
            self._ok(jid, name, "flow", "自动修复：%s，从 floorplan 重跑" % fx["detail"], fx)
            rc = self._sh(jid, name, "flow", ["bash", S + "/flow/run_orfs.sh", str(pdir), platform], pdir,
                          env={"FROM_STAGE": "floorplan", "NUM_CORES": str(cores), "OMP_NUM_THREADS": str(cores)})
        if rc != 0:
            self._ingest(jid, name, pdir)
            self._fail(jid, name, "flow", "布局布线失败（rc=%s）：%s" % (rc, self._last_error(pdir)),
                       {"log_tail": self._tail(pdir, "flow")})
            return
        self._ok(jid, name, "flow", "布局布线完成")
        # signoff
        self._stage(jid, name, "signoff")
        self._sh(jid, name, "signoff", ["bash", str(tools_dir() / "run_signoff.sh"), str(pdir), platform], pdir,
                 env={"R2G_LVS_FROZEN": "1"})
        v = signoff_verdict(pdir)
        self._update(jid, name, detail={"signoff": v})
        if v.get("infrastructure_error"):
            self._ingest(jid, name, pdir)
            self._fail(jid, name, "signoff", v["infrastructure_error"], v)
            return
        (self._ok if v["dual_clean"] else self._fail_soft)(
            jid, name, "signoff", "DRC %s（%s 处），LVS %s" % (v["drc"], v["drc_violations"] or 0, v["lvs"]), v)
        # repair
        if not v["dual_clean"]:
            self._stage(jid, name, "repair", "自动修复：诊断 → 修改 → 重跑 → 重新签核")
            self._sh(jid, name, "repair", ["bash", S + "/flow/fix_signoff.sh", str(pdir), platform,
                                           "--check", "both", "--max-iters", "3"], pdir,
                     env={"R2G_LVS_FROZEN": "1"})
            v = signoff_verdict(pdir)
            fixes = [json.loads(l) for l in (pdir / "reports" / "fix_log.jsonl").read_text().splitlines()
                     if l.strip()] if (pdir / "reports" / "fix_log.jsonl").is_file() else []
            self._update(jid, name, detail={"signoff": v, "repair": fixes[-5:]})
            if not v["dual_clean"] or v.get("infrastructure_error"):
                self._ingest(jid, name, pdir)
                self._fail(jid, name, "repair", v.get("infrastructure_error") or
                           "修复后仍未双清：DRC %s，LVS %s" % (v["drc"], v["lvs"]), v)
                return
            self._ok(jid, name, "repair", "修复成功，DRC/LVS 均已清零", v)
        self._ingest(jid, name, pdir)
        if output == "image":
            n_ok, n_all = self._image_dataset(jid, name, pdir, platform)
            if n_ok is None:
                return
            self._preview(jid, name, pdir)
            self._update(jid, name, status=PASSED, stage="done",
                         detail={"verify": {"passed": n_ok, "total": n_all}, "output": "image"},
                         event=("done", "ok", "完成：签核通过，图像数据集已校验", {}))
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
            self._update(jid, name, detail={"verify": {"passed": n_ok, "total": len(res),
                                                                "failed_checks": bad[:20]}})
            self._fail(jid, name, "verify", "数据集校验未通过：%d/%d，未通过项：%s"
                       % (n_ok, len(res), "；".join(bad[:5]) + ("……" if len(bad) > 5 else "")),
                       {"failed_checks": bad[:20]})
            return
        self._ok(jid, name, "verify", "数据集校验通过：%d/%d 项" % (n_ok, len(res)),
                 {"checks": len(res)})
        self._preview(jid, name, pdir)
        from .repairs import validate_child
        validate_child(self.store, jid, name, pdir)
        self._update(jid, name, status=PASSED, stage="done",
                     detail={"verify": {"passed": n_ok, "total": len(res)}},
                     event=("done", "ok", "完成：签核通过，数据集已校验", {}))

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
        self._update(jid, name, detail={"image": {"shape": rep.get("shape"), "features": rep.get("features"),
                                                           "labels": rep.get("labels")}})
        self._ok(jid, name, "verify", "图像数据集校验通过：%d/%d 项（%s 网格，%d 个特征通道 + %d 个标签通道）"
                 % (v["passed"], v["total"], "×".join(map(str, rep.get("shape") or [])),
                    len(rep.get("features") or []), len(rep.get("labels") or [])), rep)
        return v["passed"], v["total"]

    def _fail_soft(self, jid, name, stage, msg, data=None):
        self.store.event(jid, name, stage, "warn", msg, data, expected_owner=OWNER)

    def _ingest(self, jid, name, pdir: Path) -> None:
        """Every flow -- clean, failed or partial -- goes into the knowledge store."""
        if not self.ingest:
            return
        ing = skills_dir() / "signoff-loop" / "knowledge" / "ingest_run.py"
        try:
            cmd = ["python3", str(ing), str(pdir)]
            if self.memory_env["R2G_MEMORY_BACKEND"] == "legacy":
                cmd += ["--db", self.memory_env["R2G_KNOWLEDGE_DB"]]
            rc = self._sh(jid, name, "ingest", cmd, pdir, timeout=600)
        except (Cancelled, RecoveryRequired, LostOwnership):
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
