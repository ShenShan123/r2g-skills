#!/usr/bin/env python3
"""Freeze and dispatch eight TEHM-only corrections; previous arms stay intact."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import resource
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "memory"), str(REPO / "memory/scripts")]
import t2_tehm_agent_memory as M

IDS = ("C00876", "C02786", "C00237", "C03579", "C02966", "C01295", "C02881", "C00568")
SLOTS = [list(range(n, n+8)) + list(range(n+96, n+104)) for n in (0, 16, 32, 48)]
load, sha = M.load, M.sha


def save(p, value):
    p = Path(p)
    temp = p.with_suffix(p.suffix + ".tmp")
    M.save(temp, value)
    temp.replace(p)


def files(root):
    return {str(p): sha(p) for p in sorted(Path(root).rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".pyc")}


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Runtime delta does not match exactly once: " + old[:100])
    return text.replace(old, new, 1)


def integrate(rel, original):
    """Apply only this change, keeping the frozen NB/followup-specific fixes."""
    if rel == "memory/scripts/t2a_batch.py":
        text = replace_once(original,
            "'R2G_TEHM_PRIORITY_FILE','R2G_ENGINEER_BRANCH','R2G_TEHM_V2_POLICY'):",
            "'R2G_TEHM_PRIORITY_FILE','R2G_ENGINEER_BRANCH','R2G_TEHM_V2_POLICY',\n"
            "                'R2G_TEHM_AGENT_CONSUMER'):")
        seam = "        memory=A.PromptMemory(arm,root/'controls') if arm in PROMPT_ARMS else None\n"
        addition = (REPO / rel).read_text().split(seam, 1)[1].split("        T.event(H,project,'memory_runtime_initialization'", 1)[0]
        if "AgentMemory" not in addition:
            raise ValueError("Consumer initialization delta missing")
        text = replace_once(text, seam, seam + addition)
        seam = "        record['initial']=initial\n"
        addition = (REPO / rel).read_text().split(seam, 1)[1].split("        if not initial['accepted']:", 1)[0]
        if "initial_route" not in addition:
            raise ValueError("Initial route delta missing")
        return replace_once(text, seam, seam + addition)
    if rel == "memory/scripts/t2a_runtime.py":
        current = (REPO / rel).read_text()
        old = "    if (arm in ('RET','EXPEL','AWM','AMEM')) != (memory is not None):"
        addition = current.split("    tehm_agent = ", 1)[1].split("\n        raise ValueError('Arm/representation mismatch')", 1)[0]
        text = replace_once(original, old, "    tehm_agent = " + addition)
        seam = "        content,usage=H.llm_call(key,base_messages,project/('repair-turn-%d'%turn),ledger=project,role='repair')\n"
        addition = "        agent_receipt = " + current.split("        agent_receipt = ", 1)[1].split("        charge_usage(budget,usage)", 1)[0]
        text = replace_once(text, seam, addition)
        seam = "             'situation':sit,'proposal':content,'flow_launched':False,'rolled_back':False}\n"
        addition = current.split(seam, 1)[1].split("        if stage is None:", 1)[0]
        return replace_once(text, seam, seam + addition)
    if rel == "memory/tehm/causal/mechanism.py":
        current = (REPO / rel).read_text()
        addition = "    # ORFS episodes" + current.split("    # ORFS episodes", 1)[1].split("    return TransitionFacts(", 1)[0]
        text = replace_once(original, "    return TransitionFacts(", addition + "    return TransitionFacts(")
        addition = "def orfs_signature(" + current.split("def orfs_signature(", 1)[1].split("def _graph_from_manifest(", 1)[0]
        return replace_once(text, "def _graph_from_manifest(", addition + "def _graph_from_manifest(")
    raise ValueError("Unregistered code delta")


def prepare(old, root, tests):
    prep_started, prep_cpu = time.time(), time.process_time()
    child_start = resource.getrusage(resource.RUSAGE_CHILDREN)
    import t2a_batch as B
    import t2a_envelope as E
    old, root = Path(old).resolve(), Path(root).resolve()
    if root.exists():
        raise FileExistsError("Never overwrite an earlier package")
    if not load(old / "table2-results.json")["complete"]:
        raise ValueError("Reference Table 2 is incomplete")
    import xml.etree.ElementTree as ET
    suite = ET.parse(tests).getroot()
    children = [suite] if suite.tag == "testsuite" else list(suite)
    if sum(int(t.attrib.get("tests", 0)) for t in children) == 0 or any(
            int(t.attrib.get("failures", 0)) or int(t.attrib.get("errors", 0)) for t in children):
        raise ValueError("Offline verification has not passed")
    root.mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "phase").mkdir()
    (root / "phase/resource_ledger.jsonl").write_bytes(b"")
    save(root / "budget.json", B.formal_phase_budget() | {"budget_id": root.name})
    save(root / "authorization.json", {
        "user_text": "修复当前的问题，以完整记忆机制重跑8个design；不能自动升级因果机制，不要什么都塞进提示词",
        "scope": "8 TM0 episodes; reuse prior NB and six-arm reference, no new external-method builds",
        "history": "same per-target NB history, self/same-IP excluded before construction",
        "repair_generations": 3, "cumulative_caps": None, "input_bytes_ceiling": 96000,
        "output_tokens_ceiling": 2000, "implicit_retries": 0, "UNKNOWN": "pause",
        "provider": "official api.deepseek.com, deepseek-flash",
        "persistent_write_back": False, "learning_curve": "unchanged"})
    shutil.copy2(tests, root / "offline-tests.xml")
    references = {str(old / "table2-results.json"): sha(old / "table2-results.json")}
    plans, deltas, construction = [], {}, {}
    for hid in IDS:
        source, fold = old / "folds" / hid, root / "folds" / hid
        prior = load(source / "registration.json")
        B.preflight(source, "TEST")
        fold.mkdir(parents=True)
        shutil.copytree(source / "code", fold / "code", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        changes = {}
        for rel in ("memory/scripts/t2a_batch.py", "memory/scripts/t2a_runtime.py", "memory/tehm/causal/mechanism.py"):
            p = fold / "code" / rel
            before = sha(p)
            p.write_text(integrate(rel, p.read_text()))
            changes[rel] = {"base": before, "new": sha(p)}
        for rel in ("memory/scripts/t2_tehm_agent_memory.py", "memory/scripts/t2_tehm_agent_rerun.py"):
            p = fold / "code" / rel
            shutil.copy2(REPO / rel, p)
            changes[rel] = {"base": None, "new": sha(p)}
        # Construct using exactly the new frozen modules, in a clean interpreter.
        cmd = [sys.executable, str(fold / "code/memory/scripts/t2_tehm_agent_rerun.py"),
               "build-store", "--old", str(source), "--root", str(fold)]
        with (root / "logs" / (hid + "-build.log")).open("x") as log:
            subprocess.run(cmd, check=True, stdout=log, stderr=subprocess.STDOUT,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        receipt = load(fold / "construction.json")
        construction[hid] = {k: receipt[k] for k in (
            "wall_s", "cpu_s", "model_requests", "tokens", "eda_flows", "sealed_db_sha256")}
        construction[hid]["shadow_claims"] = sum(u["construction"] == "SHADOW_KNOWLEDGE" for u in receipt["units"])
        # Templates contain unrepaired inputs only; reuse the identical numeric area references.
        task = prior["tasks"][0]["task"]
        src = Path(prior["tasks"][0]["template"])
        dst = fold / "tasks" / src.name
        shutil.copytree(src, dst)
        config = dst / "constraints/config.mk"
        old_text = config.read_text()
        config.write_text(old_text.replace(str(src) + "/", str(dst) + "/"))
        manifest = load(dst / "manifest.json")
        manifest["source_project"] = str(dst)
        manifest["initial_config"] = E.effective_config(config.read_text())
        manifest["initial_config_sha256"] = sha(config)
        save(dst / "manifest.json", manifest)
        # Repoint only the policy's evidence-index path; no ranking changes.
        shutil.copy2(source / "evidence-index.json", fold / "evidence-index.json")
        policy = load(source / "tehm-v2-policy.json")
        policy["index"] = str(fold / "evidence-index.json")
        save(fold / "tehm-v2-policy.json", policy)
        shutil.copy2(source / "episode-budget.json", fold / "episode-budget.json")
        shutil.copy2(source / "online-input-authorization.json", fold / "online-input-authorization.json")
        frozen = files(fold)
        # Bind the retained history and all original external tool/PDK hashes.
        for p, h in prior["files"].items():
            if not Path(p).is_relative_to(source):
                frozen[p] = h
        for sub in ("shared", "source", "tehm-import", "ann"):
            frozen.update(files(source / sub))
        for name in ("exclusions.json", "verification.json", "overrides.json"):
            frozen[str(source / name)] = sha(source / name)
        for name in ("authorization.json", "budget.json", "offline-tests.xml"):
            frozen[str(root / name)] = sha(root / name)
        new = {**prior, "schema": "tehm-agent8-correction-v1", "paper_data": False,
               "tasks": [{"task": task, "template": str(dst), "handoff_id": hid}],
               "arms": ["TM0"], "plan": [{"task": task, "arm": "TM0", "repeat": 1}],
               "files": frozen, "tehm_agent_consumer": {
                   "version": M.VERSION, "construction": str(fold / "construction.json"),
                   "construction_sha256": sha(fold / "construction.json")},
               "phase_binding": {"budget_file": str(root / "budget.json"),
                   "budget_sha256": sha(root / "budget.json"), "ledger_path": str(root / "phase/resource_ledger.jsonl")},
               "version_boundary": "same observed designs; corrected Agent consumer; not independent confirmation",
               "mechanism_policy": prior.get("mechanism_policy"),
               "runtime_delta": changes}
        save(fold / "registration.json", new)
        B.preflight(fold, "TEST")
        # Read-only native router checks after construction, on every known typed
        # history signature. NOT a replacement for a real online consumption record.
        probe_cmd = [sys.executable, str(fold / "code/memory/scripts/t2_tehm_agent_rerun.py"),
                     "probe", "--root", str(fold)]
        with (root / "logs" / (hid + "-probe.log")).open("x") as log:
            subprocess.run(probe_cmd, check=True, stdout=log, stderr=subprocess.STDOUT,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        references[str(source / "registration.json")] = sha(source / "registration.json")
        references[str(source / "store/tehm.sqlite")] = sha(source / "store/tehm.sqlite")
        for p in (source / "episodes").glob("*TM0*/episode.json"):
            references[str(p)] = sha(p)
        plans.append({"root": str(fold), "task": task, "arm": "TM0", "repeat": 1, "design": hid})
        deltas[hid] = changes
        print(json.dumps({"prepared": hid, "shadow_claims": construction[hid]["shadow_claims"]}), flush=True)
    save(root / "registration.json", {"status": "FROZEN_READY", "version": M.VERSION,
        "prior_package": str(old), "plan": plans, "slots": SLOTS, "construction": construction,
        "preparation_cost": {"wall_s": time.time() - prep_started,
            "parent_cpu_s": time.process_time() - prep_cpu,
            "child_cpu_s": (resource.getrusage(resource.RUSAGE_CHILDREN).ru_utime +
                           resource.getrusage(resource.RUSAGE_CHILDREN).ru_stime -
                           child_start.ru_utime - child_start.ru_stime),
            "includes_construction_and_probes": True, "model_requests": 0, "eda_flows": 0},
        "references": references, "deltas": deltas, "no_implicit_retry": True,
        "scope": "8 TM0 corrected episodes, historical comparison only",
        "files": {str(root / n): sha(root / n) for n in ("budget.json", "authorization.json", "offline-tests.xml")}
                 | {str(Path(p["root"]) / "registration.json"): sha(Path(p["root"]) / "registration.json") for p in plans}})
    save(root / "dispatch-state.json", {"status": "FROZEN_READY", "done": [], "running": [], "pending": 8})


def probe(fold):
    from tehm.snapshot import bind_snapshot
    import tempfile
    fold = Path(fold)
    # Explicit temporary data only; sealed source never changes.
    with tempfile.TemporaryDirectory(prefix="tehm-agent-probe-") as directory:
        project = Path(directory)
        bind_snapshot(fold / "store", project / ".tehm-store")
        memory = M.AgentMemory(project, fold / "construction.json")
        cases = [
            {"check": "drc", "violation_class": "m3.2", "platform": "sky130hd", "count_band": "6-20"},
            {"check": "route", "error_code": "GRT-0116", "platform": "sky130hd"},
            {"check": "physical", "violation_class": "timing_or_drc", "platform": "sky130hd"}]
        results = []
        for situation in cases:
            context, ids = memory.context("offline interface check", situation)
            results.append({**memory.last_receipt, "context": context})
        save(fold / "offline-route-probe.json", {"offline_only": True, "results": results})
        print(json.dumps({"decisions": [r["decision"]["decision"] for r in results]}))


def collect(root):
    reg = load(root / "registration.json")
    results = []
    for row in reg["plan"]:
        p = Path(row["root"]) / "episodes" / (row["task"].replace("/", "__") + "__TM0__r1")
        e = load(p / "episode.json") if (p / "episode.json").exists() else {}
        events = [json.loads(l) for l in (p / "reports/resource_ledger.jsonl").read_text().splitlines()
                  if l] if (p / "reports/resource_ledger.jsonl").exists() else []
        routes = [r for r in events if r.get("type") in ("tehm_agent_route", "tehm_agent_request_planned")]
        consumed = [r for r in events if r.get("type") == "tehm_agent_consumption"]
        results.append({**row, "episode": str(p / "episode.json"), "status": e.get("status", "NOT_STARTED"),
            "accounting": e.get("accounting"), "resources": e.get("resources"), "tokens": e.get("token_usage"),
            "wall_s": e.get("total_worker_wall_s"), "routes": routes, "consumption": consumed})
    prior = load(Path(reg["prior_package"]) / "table2-results.json")
    nb = {r["design"]: r for r in prior["episodes"] if r["arm"] == "NB"}
    measured = [(r, nb[r["design"]]) for r in results
                if r["status"] in ("ACCEPTED", "NOT_ACCEPTED")
                and nb[r["design"]]["status"] in ("ACCEPTED", "NOT_ACCEPTED")]
    totals = {"registered": 8, "measured": len(measured),
              "accepted": sum(r["status"] == "ACCEPTED" for r in results),
              "G": sum(r["status"] == "ACCEPTED" and n["status"] == "NOT_ACCEPTED" for r, n in measured),
              "H": sum(r["status"] == "NOT_ACCEPTED" and n["status"] == "ACCEPTED" for r, n in measured)}
    save(root / "results.json", {"version": M.VERSION, "complete": all(
        r["status"] in ("ACCEPTED", "NOT_ACCEPTED") for r in results),
        "registered": 8, "construction": reg["construction"], "episodes": results,
        "preparation_cost": reg.get("preparation_cost"),
        "paired_summary": totals, "prior_package": reg["prior_package"], "historical_results_unchanged": True})
    import csv
    with (root / "matrix.csv").open("w") as stream:
        writer = csv.writer(stream)
        writer.writerow(["design", "task", "NB_reference", "TM0_agent", "worker_wall_s", "episode"])
        writer.writerows([r["design"], r["task"], nb[r["design"]]["status"],
                         r["status"], r["wall_s"], r["episode"]] for r in results)


def run(root):
    import t2a_batch as B
    root = Path(root).resolve()
    reg = load(root / "registration.json")
    if (root / "launch.json").exists():
        raise FileExistsError("No automatic restart or rerun")
    lock = (root / "supervisor.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for p, h in (reg["files"] | reg["references"]).items():
        if sha(p) != h:
            raise ValueError("Registered input changed: " + p)
    for row in reg["plan"]:
        B.preflight(Path(row["root"]), "TEST")
    if not set(c for slot in reg["slots"] for c in slot) <= os.sched_getaffinity(0):
        raise ValueError("CPU slots unavailable")
    if shutil.disk_usage(root).free < 80 * 1024**3:
        raise RuntimeError("Insufficient free space for preserved EDA evidence (80 GiB guard)")
    save(root / "launch.json", {"started": time.time(), "registration_sha256": sha(root / "registration.json"),
                               "pid": os.getpid(), "python": sys.executable})
    pending, live, done, reason = list(reg["plan"]), {}, [], None
    while pending or live:
        if shutil.disk_usage(root).free < 40 * 1024**3:
            reason = "Disk-space safety pause"
        for i, slot in enumerate(reg["slots"]):
            if reason or i in live or not pending:
                continue
            row = pending.pop(0)
            fold = Path(row["root"])
            name = row["task"].replace("/", "__") + "__TM0__r1"
            if (fold / "episodes" / name).exists():
                reason = "Existing episode; no overwrite"
                pending.insert(0, row)
                break
            log = (root / "logs" / (row["design"] + "-episode.log")).open("x")
            cmd = ["taskset", "-c", ",".join(map(str, slot)), sys.executable,
                   str(fold / "code/memory/scripts/t2a_batch.py"), "episode", "--root", str(fold),
                   "--task", row["task"], "--arm", "TM0", "--repeat", "1", "--split", "TEST"]
            p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "CUDA_VISIBLE_DEVICES": ""})
            live[i] = (p, log, row, name)
        for i, (p, log, row, name) in list(live.items()):
            if p.poll() is None:
                continue
            log.close()
            file = Path(row["root"]) / "episodes" / name / "episode.json"
            e = load(file) if file.exists() else {}
            if p.returncode or e.get("status") not in ("ACCEPTED", "NOT_ACCEPTED") or not e.get("accounting", {}).get("passed"):
                reason = "Technical/accounting stop: " + row["design"]
            elif not consumption_integrity(file):
                reason = "TEHM consumption receipt missing/mismatched: " + row["design"]
            done.append({**row, "status": e.get("status"), "rc": p.returncode})
            del live[i]
        if shutil.disk_usage(root).free < 40 * 1024**3:
            reason = "Disk-space safety pause"
        save(root / "dispatch-state.json", {"status": "PAUSED_DRAINING" if reason and live else
             "PAUSED" if reason else "RUNNING" if pending or live else "COMPLETED",
             "done": done, "running": [{"pid": p.pid, **row} for p, log, row, name in live.values()],
             "pending": len(pending), "reason": reason, "updated": time.time()})
        collect(root)
        if reason and not live:
            break
        if pending or live:
            time.sleep(30)


def consumption_integrity(episode_file):
    project = Path(episode_file).parent
    events = [json.loads(l) for l in (project / "reports/resource_ledger.jsonl").read_text().splitlines() if l]
    routes = [e for e in events if e.get("type") == "tehm_agent_route"]
    planned = [e for e in events if e.get("type") == "tehm_agent_request_planned"]
    consumed = [e for e in events if e.get("type") == "tehm_agent_consumption"]
    if len(routes) != 1 or len(planned) != len(consumed):
        return False
    if {e["turn"] for e in planned} != {e["turn"] for e in consumed}:
        return False
    covered = set()
    for receipt in consumed:
        checked = M.verify_request(project, receipt)
        if checked["request_files"] != receipt["request_files"]:
            return False
        covered.update(r["path"] for r in receipt["request_files"])
    return covered == {p.name for p in project.glob("repair-turn-*.try*.request.json")}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("prepare", "build-store", "probe", "run", "collect"))
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--old", type=Path)
    p.add_argument("--tests", type=Path)
    a = p.parse_args()
    if a.mode == "prepare":
        prepare(a.old, a.root, a.tests)
    elif a.mode == "build-store":
        save(a.root / "construction.json", M.enrich_store(a.old, a.root / "store"))
    elif a.mode == "probe":
        probe(a.root)
    elif a.mode == "run":
        run(a.root)
    else:
        collect(a.root)


if __name__ == "__main__":
    main()
