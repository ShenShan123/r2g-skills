#!/usr/bin/env python3
"""Learning curve LC-v1: a dedicated, frozen package with a real closed loop and verified writes.

User plan (2026-10-18): 20 LEARN + 8 fixed PROBE on sky130hd, checkpoints after 0, 5, 10 and 20
LEARN episodes, each PROBE twice per checkpoint (84 episodes). One learning worker. No ablation, no
new cumulative cost cap; per episode at most three model repair generations, the UNKNOWN-usage pause
and the safety watchdogs; every cost recorded. A mechanism change means a NEW version; results of
different versions are never merged into one curve.

Closed loop per LEARN task:
  task-start snapshot of the growth head (store + TEHM-v2 evidence index, one version)
  -> repair episode (the guarded T2a worker, TEHM arm, the head's TEHM-v2 policy)
  -> independent verification: ann-v2 labels + the independent verifier must PASS
  -> LEARN write: the units with their shared labels (one success definition) into a NEW version of
     the store AND of the evidence index, together; sealed; head published atomically
  -> the next task reads the new head.
PROBE episodes bind the head read-only at their checkpoint and never write.

Success definition of a written unit (the shared-history rule): ann-v2 admission, AND the post run's
area within the task caps (a missing area is never a pass), AND no failure in a check that was not
measured before the action (the trial rule's generalisation: a cleared stage abort followed by a
failing signoff check is `cleared_new_failure`).

  python3 t2_lc.py prepare --root R
  python3 t2_lc.py run --root R          # the single-worker dispatcher (needs the authorisation)
  python3 t2_lc.py step --root R --index N   # one plan step (used by the dispatcher)
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EVAL = Path("/data1/zhangdy/RTL/RTL_testbench/_r2g_eval")
PILOT = EVAL / "learning-certified-sl1-20261015/pilot20"
ROSTER = PILOT / "replacement-r2/roster-r2.json"
ENVELOPES = PILOT / "review-r1/envelopes"
REPLACEMENT = PILOT / "qualification-q2/projects/lc20q__C02784/task-envelope.json"
INACTIVE = {"C00829__ltc2308_tape__basicmasterjr_mister"}           # tool-error LEARN, replaced
OLD_HOOK = EVAL / "t2a-mechanism-dev-r4-20261015/core/code/r2g-skills/signoff-loop/scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl"
HISTORY = EVAL / "t2-shared-history-package-v4-20261018/t2"
LABELS = [EVAL / "t2-shared-history-labels-v3-20261018" / n / "ann_v2_dev.jsonl"
          for n in ("source-v1", "dev-candidate", "collection-r2")] + \
         [EVAL / "t2-grt-component-trials-20261018/candidate-history-dev7/ann/ann_v2_dev.jsonl"]
M0_STORE = EVAL / "stores-v4/TEHM-shared-t2-v4-20261018"
SOURCE_V1 = EVAL / "p1c-export/SOURCE-v1"
T2_LOCK = EVAL / "t2-candidate-inventory-20261017/t2-source-lock.json"
SPLIT_CONTRACT = Path(__file__).resolve().parents[1] / "evaluation/t2_history_collection_contract.json"
CHECKPOINTS = (0, 5, 10, 20)
DISK_FLOOR_GIB = 50          # safety pause between steps (a disk-full run corrupts results); not a cost cap
PROBE_REPEATS = 2
ARM = "TM0"


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load(p):
    return json.loads(Path(p).read_text())


def save(p, value):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, sort_keys=True, default=str) + "\n")
    tmp.replace(p)


def design_id(task: str) -> str:
    return task.split("__")[0]


# ------------------------------------------------------------------------------------- plan

def plan_steps(learn: list, probes: list) -> list:
    """Checkpoint c's PROBE episodes run when exactly c LEARN tasks are committed."""
    steps, done = [], 0
    for c in CHECKPOINTS:
        while done < c:
            t = learn[done]
            done += 1
            steps.append({"role": "LEARN", "task": t, "order": done, "arm": ARM, "repeat": 1,
                          "name": f"{t.replace('/', '__')}__{ARM}__L{done:02d}"})
        for p in probes:
            for r in range(1, PROBE_REPEATS + 1):
                steps.append({"role": "PROBE", "task": p, "checkpoint": c, "arm": ARM, "repeat": r,
                              "name": f"{p.replace('/', '__')}__{ARM}__c{c:02d}r{r}"})
    return steps


# ------------------------------------------------------------------------------- growth state

def _lock(root: Path):
    stream = (root / "growth/growth.lock").open("a")
    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return stream


def _maintenance(root: Path, value: dict) -> None:
    with (root / "growth/maintenance.jsonl").open("a") as f:
        f.write(json.dumps({**value, "recorded_at": time.time()}, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _write_policy(version: Path, index_path: Path) -> Path:
    from tehm_v2 import policy as POL
    pol = {"schema": "tehm-v2-policy-v1", "mechanisms": {m: True for m in POL.MECHANISMS},
           "index": str(index_path), "index_sha256": sha(index_path), "task_ip_family": None,
           "note": "LC-v1: all four mechanisms on; the index belongs to this growth version"}
    p = version / "tehm-v2-policy.json"
    save(p, pol)
    return p


def init_growth(root: Path, m0: Path, index: dict, learn: list, probes: list) -> dict:
    from tehm.snapshot import bind_snapshot, seal_store
    g = root / "growth"
    if g.exists():
        raise RuntimeError("existing growth state; no overwrite")
    g.mkdir(parents=True)
    t0, c0 = time.time(), time.process_time()
    v = g / "versions/v000"
    bind_snapshot(m0, v)
    save(v / "evidence_index.json", index)
    policy = _write_policy(v, v / "evidence_index.json")
    db_sha = seal_store(v)
    head = {"schema": "lc-growth-head-v1", "version": "v000", "store": str(v), "db_sha256": db_sha,
            "index": str(v / "evidence_index.json"), "index_sha256": sha(v / "evidence_index.json"),
            "policy": str(policy), "policy_sha256": sha(policy), "learn_order": list(learn), "probe_ids": list(probes),
            "committed_learn": [], "experienced_learn_designs": 0, "written_units": 0, "written_admitted": 0,
            "index_entries": len(index.get("entries") or []), "m0_store": str(m0), "m0_db_sha256": sha(m0 / "tehm.sqlite")}
    save(g / "head.json", head)
    _maintenance(root, {"operation": "initialize", "status": "COMMITTED", "version": "v000",
                        "wall_s": time.time() - t0, "cpu_s": time.process_time() - c0, "llm_requests": 0})
    return head


def bind(root: Path, task: str, role: str, dest: Path, expect_version: str | None = None) -> dict:
    """Task-start snapshot of the CURRENT head. Only the next LEARN may bind for LEARN; any PROBE may."""
    from tehm.snapshot import bind_snapshot, seal_store
    with _lock(root):
        head = load(root / "growth/head.json")
        if expect_version and head["version"] != expect_version:
            raise RuntimeError(f"head moved: expected {expect_version}, found {head['version']}")
        if role == "LEARN":
            n = len(head["committed_learn"])
            if n >= len(head["learn_order"]) or head["learn_order"][n] != task:
                raise ValueError("only the next LEARN task may bind the head")
        elif role != "PROBE" or task not in head["probe_ids"]:
            raise ValueError("task/role outside the registered scope")
        for key, path in (("db_sha256", Path(head["store"]) / "tehm.sqlite"), ("index_sha256", head["index"]),
                          ("policy_sha256", head["policy"])):
            if sha(path) != head[key]:
                raise RuntimeError("committed head drift: " + key)
        t0, c0 = time.time(), time.process_time()
        rec = bind_snapshot(Path(head["store"]), dest)
        rec.update(task=task, role=role, growth_version=head["version"], source_db_sha256=head["db_sha256"],
                   index_sha256=head["index_sha256"], policy_sha256=head["policy_sha256"],
                   experienced_learn_designs=head["experienced_learn_designs"])
        save(dest / "growth-binding.json", rec)
        seal_store(dest)
        _maintenance(root, {"operation": "bind", "task": task, "role": role, "version": head["version"],
                            "status": "COMMITTED", "wall_s": time.time() - t0, "cpu_s": time.process_time() - c0})
        return rec


# ----------------------------------------------------------------------------- verified write

def unit_overrides(recs: list, project: Path, manifest: dict) -> dict:
    """Success also needs the post run's area within the caps and no failure in a check that was not
    measured before the action. Overrides only remove success (never grant it)."""
    import t2a_envelope as E
    from ann_v2 import fields as FD
    out = {}
    for r in recs:
        if (r.get("admission") or {}).get("status") != "ADMITTED":
            continue
        post = ((r.get("binding") or {}).get("post") or {}).get("run")
        run = project / "backend" / str(post) if post else None
        if run is None or not run.is_dir():
            out[r["unit_id"]] = {"admitted": False, "reason": "post run not on disk"}
            continue
        area = E.check_area(manifest, E.area_metrics(run))
        if not area["ok"]:
            out[r["unit_id"]] = {"admitted": False, "reason": "area envelope " +
                                 ("FAIL" if area["violations"] else "UNVERIFIED"), "area": area}
            continue
        m = FD.measure(run)
        new = [k for k, v in (r.get("new_failures") or {}).items()
               if v == "NOT_MEASURED_BEFORE" and (m.get(k) or {}).get("measured") and (m.get(k) or {}).get("present")]
        if new:
            out[r["unit_id"]] = {"admitted": False, "outcome_class": "cleared_new_failure",
                                 "reason": "failure after the action in a check not measured before it: " + ",".join(new)}
    return out


def commit_verified(root: Path, step: dict, project: Path, manifest: dict, group: str) -> dict:
    """Independent verification, then one new version of store AND index (or a recorded no-write)."""
    from ann_v2 import annotate as AN, dev_export as DX, verify as VF
    from tehm import neutral_export as N
    from tehm.snapshot import bind_snapshot, seal_store
    from tehm_backend import TehmMemoryBackend
    from tehm_v2 import evidence_index as EI
    import t2_shared_history as SH
    t0, c0 = time.time(), time.process_time()
    task, did = step["task"], design_id(step["task"])
    with _lock(root):
        head = load(root / "growth/head.json")
        n = len(head["committed_learn"])
        if n >= len(head["learn_order"]) or head["learn_order"][n] != task:
            raise ValueError("only the next LEARN task may commit")
        binding = load(project / ".tehm-store/growth-binding.json")
        if binding.get("growth_version") != head["version"] or binding.get("source_db_sha256") != head["db_sha256"]:
            raise RuntimeError("the task-start snapshot is not the current head")
        work = root / "growth/writes" / step["name"]
        if work.exists():
            raise RuntimeError("a write for this step exists; no implicit retry")
        locked = {e.get("handoff_id") for e in load(T2_LOCK).get("entries", []) if e.get("handoff_id")}
        man = DX.build(project.parent, [project.name], SOURCE_V1, "", set(), locked, work / "source",
                       extra_families={did: "lc:" + group})
        if man["units"] == 0:
            new_head = {**head, "committed_learn": head["committed_learn"] + [task],
                        "experienced_learn_designs": n + 1}
            save(root / "growth/head.json", new_head)
            receipt = {"step": step["name"], "task": task, "status": "NO_WRITE", "reason": "no executed action row",
                       "version": head["version"], "experienced_learn_designs": n + 1,
                       "wall_s": time.time() - t0, "cpu_s": time.process_time() - c0}
            save(work / "receipt.json", receipt)
            _maintenance(root, {"operation": "commit", "task": task, "status": "NO_WRITE", "version": head["version"],
                                "wall_s": receipt["wall_s"], "cpu_s": receipt["cpu_s"]})
            return receipt
        AN.main(["--source", str(work / "source"), "--out", str(work / "ann")])
        rep = VF.verify(work / "ann/ann_v2_dev.jsonl", work / "source")
        save(work / "verification.json", rep)
        if rep.get("gate") != "PASS":
            _maintenance(root, {"operation": "commit", "task": task, "status": "VERIFIER_FAIL",
                                "critical": rep.get("critical"), "wall_s": time.time() - t0})
            raise RuntimeError(f"independent verifier gate {rep.get('gate')} ({rep.get('critical')} critical)")
        recs = [json.loads(l) for l in (work / "ann/ann_v2_dev.jsonl").read_text().splitlines() if l.strip()]
        ovs = unit_overrides(recs, project, manifest)
        save(work / "overrides.json", ovs)
        split = load(root / "split-contract.json")["split"]          # the package's copy (hash-bound)
        units, sman = SH.build([work / "source/source_export.jsonl"], [work / "ann/ann_v2_dev.jsonl"],
                               split, "t2", ovs)
        if any(u["identity"].get("platform") != "sky130hd" or design_id(u["identity"].get("design", "")) != did
               for u in units):
            raise RuntimeError("experience scope leaks another task or platform")
        version = root / "growth/versions" / ("v%03d" % (int(head["version"][1:]) + 1))
        if version.exists():
            raise RuntimeError("prior partial version exists; no implicit retry")
        bind_snapshot(Path(head["store"]), version)
        version.chmod(version.stat().st_mode | 0o200)
        (version / "tehm.sqlite").chmod((version / "tehm.sqlite").stat().st_mode | 0o200)
        backend = TehmMemoryBackend(db_path=version / "tehm.sqlite", artifact_root=version / "artifacts",
                                    read_only_eval=False)
        try:
            if os.environ.get("TEHM_EVIDENCE_CALIBER", "main") != "main" or \
                    os.environ.get("TEHM_D1_REQUIRE_VERIFIED_POSITIVE", "1") == "0":
                raise ValueError("growth requires the strict main evidence caliber and D1")
            conn, store = backend._open()
            view = SH.tehm_view(units)                     # rolled_back = the verified value only
            receipts = N.import_units(conn, store, view)
            backend.rebuild()
            if N.import_units(conn, store, view):
                raise RuntimeError("re-import was not idempotent")
            backend.rebuild()
            rules = conn.execute("SELECT count(*) FROM tehm_rules").fetchone()[0]
        finally:
            backend.close()
        prev = load(head["index"])
        added = EI.build_shared(units, [work / "ann/ann_v2_dev.jsonl"], "t2")
        index = {**prev, "entries": list(prev.get("entries") or []) + added["entries"],
                 "lc_versions": list(prev.get("lc_versions") or []) + [{"version": version.name, "task": task,
                                                                        "entries_added": len(added["entries"]),
                                                                        "skipped": added["skipped"]}]}
        save(version / "evidence_index.json", index)
        policy = _write_policy(version, version / "evidence_index.json")
        admitted = sum(1 for u in units if u["shared_label"]["admitted"])
        receipt = {"step": step["name"], "task": task, "status": "COMMITTED", "version": version.name,
                   "parent_version": head["version"], "experienced_learn_designs": n + 1,
                   "units": len(units), "admitted": admitted, "new_canonical_units": len(receipts),
                   "outcomes": sman["outcomes"], "overrides": len(ovs), "rules": rules,
                   "index_entries_added": len(added["entries"]), "verifier": {"gate": rep.get("gate"),
                                                                              "critical": rep.get("critical")}}
        save(version / "commit.json", receipt)
        db_sha = seal_store(version)
        new_head = {**head, "version": version.name, "store": str(version), "db_sha256": db_sha,
                    "index": str(version / "evidence_index.json"), "index_sha256": sha(version / "evidence_index.json"),
                    "policy": str(policy), "policy_sha256": sha(policy),
                    "committed_learn": head["committed_learn"] + [task], "experienced_learn_designs": n + 1,
                    "written_units": head["written_units"] + len(units), "written_admitted": head["written_admitted"] + admitted,
                    "index_entries": len(index["entries"])}
        save(root / "growth/head.json", new_head)
        receipt.update(wall_s=time.time() - t0, cpu_s=time.process_time() - c0)
        save(work / "receipt.json", receipt)
        _maintenance(root, {"operation": "commit", "task": task, "status": "COMMITTED", "version": version.name,
                            "units": len(units), "admitted": admitted, "wall_s": receipt["wall_s"],
                            "cpu_s": receipt["cpu_s"], "llm_requests": 0})
        return receipt


# ------------------------------------------------------------------------------------ prepare

def runtime_ignore(top: Path):
    """Copy filter for the runtime: drop caches, docs and tests everywhere, but drop `evaluation` ONLY
    at memory/evaluation (contracts and records). memory/tehm/evaluation is runtime code that the
    store rebuild imports (failure seen in the R4 packaging and again in this package's pre-launch check)."""
    base = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "docs", "tests")
    top = Path(top).resolve()

    def _ignore(dirpath, names):
        out = set(base(dirpath, names))
        if Path(dirpath).resolve() == top / "memory" and "evaluation" in names:
            out.add("evaluation")
        return out
    return _ignore


def lc_tasks() -> tuple:
    roster = load(ROSTER)
    learn = [r for r in roster["selected"]["LEARN"]]
    probes = [r for r in roster["selected"]["PROBE"]]
    if len(learn) != 20 or len(probes) != 8:
        raise ValueError("the approved scope is 20 LEARN + 8 PROBE")
    if roster.get("platform") != "sky130hd":
        raise ValueError("one process library: sky130hd")
    if {r["design"] for r in learn} & INACTIVE:
        raise ValueError("an inactive design is in the LEARN order")
    return learn, probes


def envelope_for(design: str, hook_new: Path) -> dict:
    """The reviewed envelope (the replacement's is reviewed the same way), with the allowlisted
    feedthrough hook moved to this package's runtime copy after a byte check."""
    p = ENVELOPES / (design + ".json")
    if p.is_file():
        m = load(p)
    else:
        if not design.startswith("C02784__"):
            raise ValueError("no reviewed envelope for " + design)
        m = load(REPLACEMENT)
        m["allowlist"]["POST_GLOBAL_PLACE_TCL"] = {"type": "enum", "values": [str(OLD_HOOK)]}
        m["immutable"]["files"][str(OLD_HOOK)] = "sha256:" + sha(OLD_HOOK)
        m["catalogue_permission_amendment"] = load(ENVELOPES / "C00053__program_counter__rv32i-soc.json")[
            "catalogue_permission_amendment"]
        m.update(qualification_status="INITIAL_PASS_CONTROL", formal_launch_allowed=False, repair_dispatch_allowed=False)
    if sha(OLD_HOOK) != sha(hook_new):
        raise ValueError("the runtime's feedthrough hook differs from the reviewed one")
    if m["allowlist"].get("POST_GLOBAL_PLACE_TCL", {}).get("values") != [str(OLD_HOOK)]:
        raise ValueError("unexpected hook allowlist in " + design)
    m["allowlist"]["POST_GLOBAL_PLACE_TCL"]["values"] = [str(hook_new)]
    m["immutable"]["files"].pop(str(OLD_HOOK))
    m["immutable"]["files"][str(hook_new)] = "sha256:" + sha(hook_new)
    m["lc_hook_rebase"] = {"from": str(OLD_HOOK), "to": str(hook_new), "sha256": sha(hook_new)}
    if m.get("platform") != "sky130hd":
        raise ValueError("platform must be sky130hd")
    return m


def isolation_checks(learn: list, probes: list) -> dict:
    """LEARN/PROBE disjoint (designs and lineage groups), no T2 lock conflict, none in the history."""
    import t2_source_lock as SL
    groups = [r["group_id"] for r in learn + probes]
    designs = [r["design"] for r in learn + probes]
    if len(set(groups)) != len(groups) or len(set(designs)) != len(designs):
        raise ValueError("LEARN/PROBE designs or lineage groups overlap")
    found = SL.conflicts(SL.load(T2_LOCK), ROSTER)
    if found:
        raise ValueError("T2 source-lock conflict: " + json.dumps(found)[:400])
    hist = set()
    for line in (HISTORY / "shared_units.jsonl").read_text().splitlines():
        if line.strip():
            hist.add(json.loads(line)["identity"].get("design"))
    clash = sorted({design_id(d) for d in designs} & {design_id(str(h)) for h in hist})
    if clash:
        raise ValueError("an LC design is in the M0 history: " + ",".join(clash))
    return {"designs": len(designs), "groups": len(set(groups)), "t2_lock_conflicts": 0, "history_overlap": 0}


def prepare(root: Path, slot: list) -> dict:
    import t2a_adapters as A
    import t2a_batch as B
    import t2a_envelope as E
    import t2a_qualify as Q
    import t2a_runtime as T
    import t2_dev3 as V
    from tehm_v2 import evidence_index as EI
    if root.exists():
        raise ValueError("no overwriting a registered package")
    learn, probes = lc_tasks()
    iso = isolation_checks(learn, probes)
    B.validate_cpu_slots([slot], os.sched_getaffinity(0), "DEV")
    t0, c0 = time.time(), time.process_time()
    root.mkdir(parents=True)
    for rel in ("r2g-skills/signoff-loop", "r2g-skills/def-graph", "memory"):
        shutil.copytree(Q.REPO / rel, root / "code" / rel, ignore=runtime_ignore(Q.REPO))
    if not (root / "code/memory/tehm/evaluation").is_dir():
        raise RuntimeError("runtime copy lacks memory/tehm/evaluation")
    shutil.copy2(SPLIT_CONTRACT, root / "split-contract.json")
    pin = Path("r2g-skills/signoff-loop/references/env.local.sh")
    shutil.copy2(Q.EVAL / "toolchain/frozen-r2g-cc6d103" / pin, root / "code" / pin)
    hook_new = root / "code/r2g-skills/signoff-loop/scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl"
    placed, tasks_meta = [], {}
    for role, rows in (("LEARN", learn), ("PROBE", probes)):
        for r in rows:
            m = envelope_for(r["design"], hook_new)
            project = Path(m["source_project"])
            task_dir = root / "tasks" / m["task"].replace("/", "__")
            shutil.copytree(project / "constraints", task_dir / "constraints")
            shutil.copytree(project / "rtl", task_dir / "rtl")
            cfg = task_dir / "constraints/config.mk"
            cfg.write_text(cfg.read_text().replace(str(project) + "/", str(task_dir) + "/"))
            old = m["initial_config"]
            m["initial_config"] = E.effective_config(cfg.read_text())
            if m["initial_config"] != {k: v.replace(str(project) + "/", str(task_dir) + "/") for k, v in old.items()}:
                raise ValueError("task rebase changed more than owned paths: " + r["design"])
            m["source_project"] = str(task_dir)
            m["initial_config_sha256"] = Q.digest(cfg)
            m["split"] = "LC_" + role
            Q.save(task_dir / "manifest.json", m)
            placed.append({"task": m["task"], "template": str(task_dir)})
            tasks_meta[m["task"]] = {"role": role, "design": r["design"], "group_id": r["group_id"],
                                     "stratum": m.get("stratum"), "qualification_status": m.get("qualification_status")}
    learn_ids = [envelope_for(r["design"], hook_new)["task"] for r in learn]
    probe_ids = [envelope_for(r["design"], hook_new)["task"] for r in probes]
    units = A.load_shared(HISTORY)
    index = EI.build_shared(units, LABELS, "t2")
    index["package"] = {"view": "t2", "units_sha256": load(HISTORY / "manifest.json")["units_sha256"]}
    from tehm.snapshot import seal_store
    shutil.copytree(M0_STORE, root / "m0-store")
    seal_store(root / "m0-store")                  # read-only single file: a reader never adds -wal/-shm
    head = init_growth(root, root / "m0-store", index, learn_ids, probe_ids)
    Q.save(root / "episode-budget.json", B.episode_budget())
    Q.save(root / "phase-budget.json", B.formal_phase_budget() | {"budget_id": "lc-v1-independent"})
    (root / "phase").mkdir()
    ledger = root / "phase/resource_ledger.jsonl"
    ledger.write_bytes(b"")
    binding = {"budget_file": str(root / "phase-budget.json"), "budget_sha256": Q.digest(root / "phase-budget.json"),
               "ledger_path": str(ledger), "prior_ledger_bytes": 0,
               "prior_ledger_sha256": hashlib.sha256(b"").hexdigest()}
    auth = Q.EVAL / "t2a-online-input-o1-20261012/authorization.json"
    grant = load(auth)
    if grant.get("user_approved") is not True or grant.get("online_policy") != T.online_policy():
        raise ValueError("common online input authorization missing or changed")
    shutil.copy2(auth, root / "online-input-authorization.json")
    Q.save(root / "dev.json", {"status": "NOT_EXECUTED", "split": "DEV", "paper_data": False})
    steps = plan_steps(learn_ids, probe_ids)
    plan = [{"task": s["task"], "arm": s["arm"], "repeat": s["repeat"]} for s in steps]
    frozen = [p for base in ("code", "tasks", "m0-store") for p in (root / base).rglob("*") if p.is_file()]
    frozen += [root / n for n in ("episode-budget.json", "phase-budget.json", "online-input-authorization.json",
                                  "dev.json", "split-contract.json")]
    frozen += [T2_LOCK, SOURCE_V1 / "source_export.jsonl", SOURCE_V1 / "source_manifest.json"]   # read by the write
    for t in placed:
        man = load(Path(t["template"]) / "manifest.json")
        frozen += [Path(p) for p in man["immutable"]["files"] if Path(p).is_absolute()]
    files = {str(p): Q.digest(p) for p in frozen}
    reg = {"schema": "lc-v1-package", "registered_at": time.time(), "status": "FROZEN_DEV_READY", "split": "DEV",
           "purpose": "learning_curve_LC_v1", "paper_data": False, "launch_blockers": [], "phase_binding": binding,
           "tasks": placed, "tasks_meta": tasks_meta, "plan": plan, "steps": steps, "files": files,
           "arms": [ARM], "checkpoints": list(CHECKPOINTS), "probe_repeats": PROBE_REPEATS,
           "learn_order": learn_ids, "probe_ids": probe_ids, "isolation": iso,
           "history": {"package": str(HISTORY), "units_sha256": index["package"]["units_sha256"],
                       "m0_store": str(M0_STORE), "m0_db_sha256": head["m0_db_sha256"],
                       "index_entries": len(index["entries"]), "index_skipped": index["skipped"]},
           "growth_head_v000": head,
           "scheduler": {"cpu_slots": [slot], "threads_per_episode": 8, "concurrency": 1},
           "online_policy": T.online_policy(), "mechanism_policy": None,
           "stopping_rule": "strict_acceptance_or_three_repair_generations",
           "cost_caps": {"cumulative": None, "halt_on_unknown_usage": True, "per_episode_repair_attempts": 3},
           "success_definition": "ann-v2 admission AND post-run area within caps AND no failure in a check not measured before",
           "baseline_note": "the supplied CSV is an external one-pass reference, not the three-generation NB; no token or repair saving is claimed against it",
           "new_eda_flows": 0, "new_llm_requests": 0, "no_implicit_retry": True,
           "preparation_wall_s": time.time() - t0, "preparation_cpu_s": time.process_time() - c0}
    Q.save(root / "registration.json", reg)
    B.preflight(root, "DEV")
    return reg


# -------------------------------------------------------------------------------- one step

class StepFailure(RuntimeError):
    pass


def run_step(root: Path, index: int) -> dict:
    """Executed from the package's code copy, inside the worker's CPU set."""
    import t2a_batch as B
    reg = B.preflight(root, "DEV")
    step = reg["steps"][index]
    out = root / "steps" / f"{index:03d}.json"
    if out.exists():
        raise RuntimeError("step already recorded; no implicit retry")
    head = load(root / "growth/head.json")
    meta = reg["tasks_meta"][step["task"]]
    record = {"index": index, **step, "growth_version_at_start": head["version"], "started": time.time()}
    version = head["version"]
    binder = lambda dest: bind(root, step["task"], step["role"], dest, expect_version=version)  # noqa: E731
    B.episode(root, step["task"], step["arm"], step["repeat"], "DEV", name=step["name"], store_binder=binder,
              tehm_v2_policy=head["policy"])
    project = root / "episodes" / step["name"]
    ep = load(project / "episode.json")
    record.update(episode_status=ep.get("status"), accepted=ep.get("accepted"),
                  accounting_passed=(ep.get("accounting") or {}).get("passed"))
    technical = ep.get("status") in ("ACCOUNTING_FAILURE", "UNVERIFIED_OR_SAFETY_STOP") or ep.get("technical_failures") \
        or (ep.get("accounting") or {}).get("passed") is not True
    if technical:
        record.update(status="TECHNICALLY_TERMINATED", write="NOT_ATTEMPTED")
    elif step["role"] == "LEARN":
        try:
            manifest = load(project / "manifest.json")
            record["write"] = commit_verified(root, step, project, manifest, meta["group_id"])
            record["status"] = "OK"
        except Exception as exc:                          # the write gate failed: pause, never skip
            record.update(status="WRITE_FAILED", write_error=f"{type(exc).__name__}: {exc}")
    else:
        record["status"] = "OK"
    record["ended"] = time.time()
    save(out, record)
    return record


# ------------------------------------------------------------------------------- dispatcher

def e2e_check(root: Path, step: dict, record: dict) -> dict:
    """After the first LEARN: the closed loop really closed (end-to-end validation)."""
    head = load(root / "growth/head.json")
    v0 = root / "growth/versions/v000"
    checks = {
        "episode_terminal_measured": record.get("episode_status") in ("ACCEPTED", "NOT_ACCEPTED"),
        "write_ok": record.get("status") == "OK",
        "head_advanced_or_no_write": (record.get("write") or {}).get("status") in ("COMMITTED", "NO_WRITE"),
        "committed_order": head["committed_learn"] == [step["task"]],
        "m0_unchanged": sha(root / "m0-store/tehm.sqlite") == head["m0_db_sha256"],
        "v000_sealed_unchanged": sha(v0 / "tehm.sqlite") == load(root / "registration.json")["growth_head_v000"]["db_sha256"],
        "head_files_match": all(sha(p) == head[k] for k, p in (("db_sha256", Path(head["store"]) / "tehm.sqlite"),
                                                               ("index_sha256", head["index"]),
                                                               ("policy_sha256", head["policy"]))),
        "task_start_snapshot_was_v000": record.get("growth_version_at_start") == "v000",
    }
    w = record.get("write") or {}
    if w.get("status") == "COMMITTED":
        checks["verifier_pass"] = (w.get("verifier") or {}).get("gate") == "PASS"
        checks["index_grew_with_store"] = head["version"] == w["version"] and head["index_entries"] >= \
            load(root / "registration.json")["history"]["index_entries"]
    return {"ok": all(checks.values()), "checks": checks}


def known_lsu_probe_timeout(root: Path, step: dict, rec: dict) -> bool:
    """Keep the registered LSU safety cell; only the independent queue may continue.

    No acceptance, repair-failure relabel, store write, new timeout or retry.
    Every other technical fault and every failed LEARN still pauses the queue.
    """
    if not (step.get("role") == rec.get("role") == "PROBE"
            and step.get("task") == rec.get("task") == "C02309__lsu__miaow/initial"
            and step.get("name") == rec.get("name")
            and rec.get("status") == "TECHNICALLY_TERMINATED"
            and rec.get("episode_status") == "UNVERIFIED_OR_SAFETY_STOP"
            and rec.get("accounting_passed") is True
            and rec.get("write") == "NOT_ATTEMPTED"):
        return False
    try:
        episode = load(root / "episodes" / step["name"] / "episode.json")
    except (OSError, ValueError):
        return False
    final = episode.get("final") or {}
    resources = episode.get("resources") or {}
    accounting = episode.get("accounting") or {}
    return (episode.get("accepted") is False
            and episode.get("status") == "UNVERIFIED_OR_SAFETY_STOP"
            and episode.get("terminal_reason") == "restoration_unverified_or_safety_stop"
            and final.get("execution_recovery") == "SAFETY_STOP"
            and final.get("recovery_rc") == 124
            and accounting.get("passed") is True and not accounting.get("errors")
            and resources.get("open_holds") == 0)


def phase_admission(root: Path, reg: dict) -> tuple[bool, str]:
    """Check the frozen phase ledger BEFORE paying for another episode's EDA.

    Read only: no request booking, settlement, occupancy or usage relabel.
    The existing per-request gate remains authoritative when a request is sent.
    """
    binding = reg.get("phase_binding")
    if not binding:                 # legacy packages without a shared phase ledger
        return True, "no phase ledger"
    import importlib.util
    try:
        source = root / "code/r2g-skills/signoff-loop/scripts/flow/resource_ledger.py"
        if sha(source) != reg["files"][str(source)]:
            raise ValueError("phase ledger runtime changed")
        if sha(binding["budget_file"]) != binding["budget_sha256"]:
            raise ValueError("phase budget changed")
        spec = importlib.util.spec_from_file_location("_lc_phase_ledger", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        budget = load(binding["budget_file"])
        budget.setdefault("caps", {})
        budget.setdefault("reserve", {})
        ledger = Path(binding["ledger_path"])
        with ledger.open() as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            events = [json.loads(line) for line in stream if line.strip()]
        held = {e["hold_id"] for e in events if e.get("type") == "budget_hold"}
        settled = {e["hold_id"] for e in events if e.get("type") == "budget_settle"}
        if held - settled:
            return False, "open phase request holds"
        return module.check(events, budget, {}, "repair")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return False, "phase accounting validation failed: " + str(exc)


def dispatch(root: Path, launcher=None, poll_s: float = 30) -> dict:
    import t2a_batch as B
    reg = B.preflight(root, "DEV")
    lock = (root / "dispatcher.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    slot = reg["scheduler"]["cpu_slots"][0]
    if launcher is None:
        def launcher(i):
            worker = root / "code/memory/scripts/t2_lc.py"
            if Q_digest(worker) != reg["files"][str(worker)]:
                raise RuntimeError("worker code changed")
            env = dict(os.environ)
            env.update(PYTHONDONTWRITEBYTECODE="1", CUDA_VISIBLE_DEVICES="")
            (root / "logs").mkdir(exist_ok=True)
            log = (root / "logs" / f"step-{i:03d}.log").open("a")
            return subprocess.Popen(["taskset", "-c", ",".join(map(str, slot)), sys.executable, str(worker), "step",
                                     "--root", str(root), "--index", str(i)], env=env, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
    state = {"started": time.time(), "steps": len(reg["steps"]), "status": "RUNNING"}
    save(root / "dispatch-state.json", state)
    first_learn = next((i for i, s in enumerate(reg["steps"]) if s["role"] == "LEARN"), None)
    for i, step in enumerate(reg["steps"]):
        out = root / "steps" / f"{i:03d}.json"
        if out.exists():
            continue
        if (root / "episodes" / step["name"]).exists():
            return _pause(root, state, i, "episode directory without a step record; no implicit retry")
        if (root / "dispatch-pause.json").exists():
            return _pause(root, state, i, "operator pause request")
        clear, why = phase_admission(root, reg)
        if not clear:
            return _pause(root, state, i, "phase ledger blocks new episode: " + why)
        free = shutil.disk_usage(root).free / 2 ** 30
        if free < DISK_FLOOR_GIB:
            return _pause(root, state, i, f"safety: {free:.0f} GiB free on the package's file system (< {DISK_FLOOR_GIB})")
        state["running"] = {"index": i, "name": step["name"], "role": step["role"], "started": time.time()}
        save(root / "dispatch-state.json", state)
        child = launcher(i)
        while child.poll() is None:
            time.sleep(poll_s)
        state.pop("running", None)
        if not out.exists():
            return _pause(root, state, i + 1, f"step {i} worker ended rc={child.returncode} without a record")
        rec = load(out)
        if i == first_learn:
            e2e = e2e_check(root, step, rec)
            save(root / "e2e-validation.json", e2e)
            if not e2e["ok"]:
                return _pause(root, state, i + 1, "end-to-end validation failed: " +
                              ",".join(k for k, v in e2e["checks"].items() if not v))
        if rec.get("status") != "OK":
            if known_lsu_probe_timeout(root, step, rec):
                # Costs and original technical outcome stay in the denominator.
                state.setdefault("technical_skips", []).append(i)
                save(root / "dispatch-state.json", state)
                save(root / "operator-notes" / f"step-{i:03d}-timeout-continuation.json",
                     {"index": i, "name": step["name"], "status": rec["status"],
                      "episode_status": rec["episode_status"], "write": rec["write"],
                      "action": "continue next independent step; no rerun or history write",
                      "step_sha256": sha(out), "recorded_at": time.time()})
                continue
            return _pause(root, state, i + 1, f"step {i} {rec.get('status')}: {rec.get('write_error', '')}")
        state.setdefault("done", []).append(i)
        save(root / "dispatch-state.json", state)
    state.update(status="COMPLETED", ended=time.time())
    save(root / "dispatch-state.json", state)
    return state


def Q_digest(p) -> str:
    return sha(p)


def _pause(root: Path, state: dict, nxt: int, reason: str) -> dict:
    state.update(status="PAUSED", reason=reason, next_step=nxt, ended=time.time())
    save(root / "dispatch-state.json", state)
    save(root / "batch-paused.json", {"paused_at": time.time(), "next_step": nxt, "reason": reason})
    return state


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--slot", required=True, help="comma-separated CPU list (8 cores + SMT siblings)")
    r = sub.add_parser("run")
    r.add_argument("--root", type=Path, required=True)
    s = sub.add_parser("step")
    s.add_argument("--root", type=Path, required=True)
    s.add_argument("--index", type=int, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "prepare":
        reg = prepare(a.root.resolve(), [int(x) for x in a.slot.split(",")])
        print(json.dumps({"root": str(a.root), "status": reg["status"], "steps": len(reg["steps"]),
                          "isolation": reg["isolation"], "history": reg["history"]}, indent=1))
    elif a.cmd == "run":
        print(json.dumps(dispatch(a.root.resolve()), default=str))
    else:
        print(json.dumps(run_step(a.root.resolve(), a.index), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
