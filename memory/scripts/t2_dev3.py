#!/usr/bin/env python3
"""Small DEV on the shared history: NB, repaired RECIPE and TEHM (v2), whole-episode comparison.

Prepare freezes a DEV package (runtime copy, TEHM store, RECIPE library, TEHM-v2 policy and
evidence index, task manifests, budgets, plan, CPU slots) and runs NOTHING: no flow, no model
request. Launching is a separate, authorised step (`run`). The package reuses the guarded T2a
worker (t2a_batch.episode) unchanged except that the TEHM arm receives the registered TEHM-v2
policy file.

Differences from the seven-arm DEV package (t2a_dev.prepare):
  * history = a shared package (hash-bound), not SOURCE-v1; the store, recipe library and policy
    index must all carry that package's units hash;
  * independent phase ledger with explicit caps (requests, combined tokens), halting on UNKNOWN
    usage; the historical construction bank is not touched;
  * arms are a subset of NB / RECIPE / TM0, repeats are per task.

  python3 t2_dev3.py prepare --root R --package P --store S --recipe F --policy POL \
      --tasks chacha_core/u72=2 riscv_core/u19=1 --arms NB RECIPE TM0 --requests 64 --tokens 1200000
  python3 t2_dev3.py run --root R
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
import t2a_adapters as A  # noqa: E402
import t2a_batch as B  # noqa: E402
import t2a_dev as D  # noqa: E402
import t2a_envelope as E  # noqa: E402
import t2a_qualify as Q  # noqa: E402
import t2a_runtime as T  # noqa: E402

ARMS = ("NB", "RECIPE", "TM0")
TOOLBASE = Path("/data1/zhangdy/Tools/tehm-toolchain")
ORFS = Path("/data1/zhangdy/Tools/OpenROAD-flow-scripts-clean/flow")


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def pick_slots(n: int, width: int = 8, ht_offset: int = 96) -> list:
    """n disjoint CPU sets (width cores + their hyper-thread siblings), the least loaded right now."""
    import collections
    import subprocess
    load = collections.defaultdict(float)
    for line in subprocess.run(["ps", "-eo", "psr,pcpu"], capture_output=True, text=True).stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2:
            load[int(parts[0])] += float(parts[1])
    avail = sorted(os.sched_getaffinity(0))
    cands = []
    for lo in range(0, ht_offset - width + 1):
        cpus = list(range(lo, lo + width)) + list(range(lo + ht_offset, lo + ht_offset + width))
        if set(cpus) <= set(avail):
            cands.append((sum(load[c] for c in cpus), lo, cpus))
    chosen, used = [], set()
    for score, lo, cpus in sorted(cands):
        if not used & set(cpus):
            chosen.append(cpus)
            used |= set(cpus)
        if len(chosen) == n:
            return chosen
    raise RuntimeError("not enough free CPU sets")


def check_history(package: Path, store: Path, recipe: Path, policy: Path) -> dict:
    """Fail closed: store, recipe library and policy index are all built from THIS package."""
    man = json.loads((package / "manifest.json").read_text())
    if man.get("view") != "dev_verification":
        raise ValueError("the DEV needs the dev_verification view of the shared history")
    units = man["units_sha256"]
    if sha(package / "shared_units.jsonl") != units:
        raise ValueError("package units changed")
    imp = json.loads((package / "tehm-import/source_manifest.json").read_text())["export_sha256"]
    receipt = json.loads((store / "store_receipt.json").read_text())
    if receipt.get("source_export_sha256") != imp or receipt.get("d1") is not True or receipt.get("caliber") != "main":
        raise ValueError("TEHM store was not built from this package's import view")
    lib = json.loads(recipe.read_text())
    if lib.get("units_sha256") != units or lib.get("status") != "FROZEN":
        raise ValueError("RECIPE library was not built from this package")
    pol = json.loads(policy.read_text())
    index = Path(pol["index"])
    if sha(index) != pol["index_sha256"] or json.loads(index.read_text()).get("package", {}).get("units_sha256") != units:
        raise ValueError("TEHM-v2 evidence index was not built from this package")
    if pol.get("schema") != "tehm-v2-policy-v1":
        raise ValueError("policy schema")
    return {"package": str(package), "units_sha256": units, "admitted": man["admitted"], "units": man["units"],
            "tehm_rules": receipt.get("rules"), "tehm_admissible": len(receipt.get("admitted_trial_main") or []),
            "recipes": len(lib.get("recipes") or []), "mechanisms": pol["mechanisms"],
            "policy_sha256": sha(policy), "index_sha256": pol["index_sha256"]}


def merged_inventory(root: Path, tasks: list) -> Path:
    rows, hashes = [], {}
    for task in tasks:
        inv = json.loads(D.inputs(root.with_name(root.name + "-inputs-" + task.replace("/", "_")), task).read_text())
        rows += inv["tasks"]
        hashes.update(inv["input_hashes"])
    out = root.with_name(root.name + "-inputs") / "inventory.json"
    out.parent.mkdir(parents=True, exist_ok=False)
    Q.save(out, {"schema": "t2-dev3-inventory-v1", "status": "FROZEN_DEV", "split": "DEV", "paper_data": False,
                 "tasks": rows, "input_hashes": hashes})
    return out


def prepare(root: Path, package: Path, store: Path, recipe: Path, policy: Path, tasks: dict, arms: tuple,
            requests: int, tokens: int, slots: list | None = None) -> dict:
    if root.exists():
        raise ValueError("no overwriting prior DEV registrations")
    if not set(arms) <= set(ARMS) or "NB" not in arms:
        raise ValueError("arms must be a subset of NB/RECIPE/TM0 containing NB")
    if not set(tasks) <= set(B.MECHANISM_DEV_TASKS):
        raise ValueError("tasks must be registered historical DEV tasks")
    history = check_history(package, store, recipe, policy)
    slots = slots or pick_slots(len(arms))
    B.validate_cpu_slots(slots, os.sched_getaffinity(0), "DEV")
    start, cpu = time.time(), time.process_time()
    inventory_path = merged_inventory(root, list(tasks))
    inventory = json.loads(inventory_path.read_text())
    root.mkdir(parents=True)
    for rel in ("r2g-skills/signoff-loop", "r2g-skills/def-graph", "memory"):
        shutil.copytree(Q.REPO / rel, root / "code" / rel, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".pytest_cache", "docs", "tests", "evaluation"))
    pin = Path("r2g-skills/signoff-loop/references/env.local.sh")
    shutil.copy2(Q.EVAL / "toolchain/frozen-r2g-cc6d103" / pin, root / "code" / pin)
    shutil.copytree(store, root / "store")
    (root / "controls").mkdir()
    shutil.copy2(recipe, root / "controls/RECIPE.json")
    v2 = root / "tehm-v2"
    v2.mkdir()
    pol = json.loads(policy.read_text())
    shutil.copy2(pol["index"], v2 / "evidence_index.json")
    pol["index"] = str(v2 / "evidence_index.json")
    Q.save(root / "tehm-v2-policy.json", pol)
    shutil.copy2(inventory_path, root / "inventory.json")
    Q.save(root / "dev.json", {"status": "NOT_EXECUTED", "split": "DEV", "paper_data": False})
    auth = Q.EVAL / "t2a-online-input-o1-20261012/authorization.json"
    grant = json.loads(auth.read_text())
    if (grant.get("schema") != "t2a-online-input-authorization-v1" or grant.get("user_approved") is not True
            or grant.get("online_policy") != T.online_policy()):
        raise ValueError("common online input authorization missing or changed")
    shutil.copy2(auth, root / "online-input-authorization.json")
    placed = []
    for row in inventory["tasks"]:
        p = Path(row["manifest"])
        if Q.digest(p) != row["manifest_sha256"]:
            raise ValueError("inventory envelope digest mismatch")
        man = json.loads(p.read_text())
        task = root / "tasks" / row["task"].replace("/", "__")
        project = Path(man["source_project"])
        shutil.copytree(project / "constraints", task / "constraints")
        shutil.copytree(project / "rtl", task / "rtl")
        cfg = task / "constraints/config.mk"
        cfg.write_text(cfg.read_text().replace(str(project) + "/", str(task) + "/"))
        old = man["initial_config"]
        man["initial_config"] = E.effective_config(cfg.read_text())
        if man["initial_config"] != {k: v.replace(str(project) + "/", str(task) + "/") for k, v in old.items()}:
            raise ValueError("task rebase changed more than owned paths")
        man["source_project"] = str(task)
        man["initial_config_sha256"] = Q.digest(cfg)
        Q.save(task / "manifest.json", man)
        placed.append({"task": row["task"], "template": str(task)})
    Q.save(root / "episode-budget.json", B.episode_budget())
    phase = {"budget_id": "t2-dev3-small", "caps": {"llm_requests": requests}, "combined_tokens_cap": tokens,
             "reserve": {}, "halt_on_unknown_usage": True}
    Q.save(root / "phase-budget.json", phase)
    (root / "phase").mkdir()
    ledger = root / "phase/resource_ledger.jsonl"
    ledger.write_bytes(b"")
    binding = {"budget_file": str(root / "phase-budget.json"), "budget_sha256": Q.digest(root / "phase-budget.json"),
               "ledger_path": str(ledger), "prior_ledger_bytes": 0, "prior_ledger_sha256": hashlib.sha256(b"").hexdigest()}
    plan = []
    for t in sorted(placed, key=lambda r: A.fingerprint([B.ORDER_SEED, 1, r["task"]])):
        for rep in range(1, tasks[t["task"]] + 1):
            order = sorted(arms, key=lambda arm: A.fingerprint([B.ORDER_SEED, rep, t["task"], arm]))
            plan += [{"task": t["task"], "arm": arm, "repeat": rep} for arm in order]
    frozen = [p for base in ("code", "store", "controls", "tasks", "tehm-v2") for p in (root / base).rglob("*") if p.is_file()]
    frozen += [root / n for n in ("inventory.json", "dev.json", "episode-budget.json", "phase-budget.json",
                                  "online-input-authorization.json", "tehm-v2-policy.json")]
    for t in placed:
        man = json.loads((Path(t["template"]) / "manifest.json").read_text())
        frozen += [Path(p) for p in man["immutable"]["files"] if Path(p).is_absolute()]
    files = {str(p): Q.digest(p) for p in frozen}
    native = [TOOLBASE / p for p in ("openroad-matched/bin/openroad", "yosys/bin/yosys",
              "yosys/share/yosys/plugins/slang.so", "sta/bin/sta", "klayout/bin/klayout.bin", "magic/bin/magic",
              "netgen/bin/netgen")]
    for launcher in ("magic", "netgen"):
        native.extend(p for p in (TOOLBASE / launcher / "lib").rglob("*") if p.is_file())
    native.extend(p for sub in ("scripts", "util", "platforms/sky130hd", "platforms/sky130hs")
                  for p in (ORFS / sub).rglob("*") if p.is_file())
    native.append(ORFS / "Makefile")
    native.extend(p for p in (TOOLBASE / "pdks/sky130A/libs.tech/netgen").rglob("*") if p.is_file())
    native.extend(p for p in (TOOLBASE / "pdks/sky130A/libs.tech/magic").rglob("*") if p.is_file())
    external = {str(p): Q.digest(p) for p in native}
    files.update(external)
    runtime = {str(Path(k).relative_to(root / "code")): v for k, v in files.items()
               if Path(k).is_relative_to(root / "code")}
    registration = {
        "schema": "t2-dev3-package-v1", "registered_at": time.time(), "status": "FROZEN_DEV_READY", "split": "DEV",
        "paper_data": False, "launch_blockers": [], "phase_binding": binding, "history": history,
        "tasks": placed, "plan": plan, "files": files, "arms": list(arms), "repeats": tasks,
        "runtime_hashes": runtime, "external_hashes": external, "control_hashes": {"RECIPE.json": Q.digest(root / "controls/RECIPE.json")},
        "scheduler": {"order_seed": B.ORDER_SEED, "cpu_slots": slots, "threads_per_episode": 8, "load_log_interval_s": 30,
                      "pair_barrier": False, "concurrency": len(slots)},
        "stopping_rule": "strict_acceptance_or_three_repair_generations", "per_episode_cost_caps": None,
        "online_policy": T.online_policy(), "mechanism_policy": None,
        "caps": {"llm_requests": requests, "combined_tokens": tokens, "halt_on_unknown_usage": True},
        "tehm_evidence_policy": {"caliber": "main", "require_verified_positive": True},
        "tehm_v2_policy": True, "new_eda_flows": 0, "new_llm_requests": 0, "no_implicit_retry": True,
        "preparation_wall_s": time.time() - start, "preparation_cpu_s": time.process_time() - cpu}
    Q.save(root / "registration.json", registration)
    B.preflight(root, "DEV")
    return registration


# ---------------------------------------------------------------------------------- dispatcher

def episode_dir(root: Path, row: dict) -> Path:
    return root / "episodes" / (row["task"].replace("/", "__") + "__" + row["arm"] + "__r" + str(row["repeat"]))


def flows_charged(root: Path, plan: list) -> float:
    """Flows charged in every episode ledger; a non-numeric amount raises (UNKNOWN never counts as 0)."""
    total = 0.0
    for row in plan:
        led = episode_dir(root, row) / "reports/resource_ledger.jsonl"
        if not led.is_file():
            continue
        for line in led.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "budget_charge" and e.get("resource") == "flows":
                if not isinstance(e.get("amount"), (int, float)):
                    raise RuntimeError("UNKNOWN flow amount in " + str(led))
                total += e["amount"]
    return total


def wave_gate(root: Path, rows: list) -> list:
    """Problems that must stop the dispatcher after a wave (empty list = proceed). Technical, never
    a judgement of the arms' results: an unfinished record, an accounting failure, a technical
    failure, or a broken chain link on a rerun attempt."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import t2_history_collection as HC
    problems = []
    for row in rows:
        d = episode_dir(root, row)
        name = d.name
        try:
            rec = json.loads((d / "episode.json").read_text())
        except (OSError, ValueError):
            problems.append(f"{name}: episode.json missing or unreadable")
            continue
        if rec.get("status") in ("RUNNING", "UNVERIFIED_OR_SAFETY_STOP", "ACCOUNTING_FAILURE"):
            problems.append(f"{name}: status {rec.get('status')} {rec.get('error') or ''}".strip())
        if rec.get("technical_failures"):
            problems.append(f"{name}: technical failures recorded")
        if (rec.get("accounting") or {}).get("passed") is not True:
            problems.append(f"{name}: accounting did not pass")
        usage = rec.get("token_usage") or {}
        if usage.get("llm_requests") and usage.get("measurement_complete") is not True:
            problems.append(f"{name}: token measurement incomplete")
        for msg in HC.chain_problems(d):
            if msg != "no attempt row in the episode":      # an episode accepted before any repair has none
                problems.append(f"{name}: chain: {msg}")
    return problems


def default_launcher(root: Path, reg: dict):
    worker = root / "code/memory/scripts/t2a_batch.py"
    if Q.digest(worker) != reg["files"][str(worker)]:
        raise RuntimeError("Worker code changed")
    env = dict(os.environ)
    env.update(PYTHONDONTWRITEBYTECODE="1", CUDA_VISIBLE_DEVICES="")

    def launch(row: dict, slot: list, log) -> "subprocess.Popen":
        cmd = ["taskset", "-c", ",".join(map(str, slot)), sys.executable, str(worker), "episode", "--root", str(root),
               "--task", row["task"], "--arm", row["arm"], "--repeat", str(row["repeat"]), "--split", "DEV"]
        return subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return launch


def dispatch(root: Path, launcher=None, flow_cap: int = 160, gate_wave: int = 0, poll_s: float = 30) -> dict:
    """One dispatcher: waves of one (task, repeat) group, one worker per arm on its own CPU set.
    Never re-runs an episode, never retries. After wave `gate_wave` (default: the first) the technical
    gate must pass; the flow cap is checked at every wave boundary (an episode in flight is bounded
    by its own 8 h watchdog). Any stop writes batch-paused.json and returns; a later `run` continues
    from the first wave without an episode directory."""
    reg = B.preflight(root, "DEV")
    lock = (root / "dispatcher.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    slots = reg["scheduler"]["cpu_slots"]
    B.validate_cpu_slots(slots, os.sched_getaffinity(0), "DEV")
    launcher = launcher or default_launcher(root, reg)
    waves = B.plan_waves(reg["plan"], len(slots))
    state = {"started": time.time(), "waves": len(waves), "done": [], "status": "RUNNING"}
    Q.save(root / "dispatch-state.json", state)
    paused = root / "batch-paused.json"
    if paused.exists():
        paused.rename(root / ("batch-paused-" + str(int(time.time())) + ".json"))
    for index, wave in enumerate(waves):
        dirs = [episode_dir(root, r) for r in wave]
        if all(d.exists() for d in dirs):
            state["done"].append(index)
            continue
        if any(d.exists() for d in dirs):
            raise RuntimeError("partial wave on disk; no implicit retry: " + str([d.name for d in dirs if d.exists()]))
        if (root / "dispatch-pause.json").exists():
            return _pause(root, state, index, "operator pause request")
        spent = flows_charged(root, reg["plan"])
        if spent >= flow_cap:
            return _pause(root, state, index, f"flow cap {flow_cap} reached ({spent})")
        children, handles = [], []
        for slot, row in zip(slots, wave):
            handle = (root / (episode_dir(root, row).name + ".worker.log")).open("a")
            handles.append(handle)
            children.append(launcher(row, slot, handle))
        state["running"] = {"wave": index, "episodes": [d.name for d in dirs], "started": time.time()}
        Q.save(root / "dispatch-state.json", state)
        try:
            while any(c.poll() is None for c in children):
                time.sleep(poll_s)
        finally:
            for h in handles:
                h.close()
        state.pop("running", None)
        state["done"].append(index)
        Q.save(root / "dispatch-state.json", state)
        if any(c.returncode for c in children):
            return _pause(root, state, index + 1, "a worker aborted; not restarted")
        if index == gate_wave:
            problems = wave_gate(root, wave)
            Q.save(root / "wave-gate.json", {"wave": index, "problems": problems})
            if problems:
                return _pause(root, state, index + 1, "gate: " + "; ".join(problems))
    state["status"] = "COMPLETED"
    state["ended"] = time.time()
    state["flows_charged"] = flows_charged(root, reg["plan"])
    Q.save(root / "dispatch-state.json", state)
    return state


def _pause(root: Path, state: dict, next_wave: int, reason: str) -> dict:
    state.update(status="PAUSED", reason=reason, next_wave=next_wave, ended=time.time())
    Q.save(root / "dispatch-state.json", state)
    Q.save(root / "batch-paused.json", {"paused_at": time.time(), "next_wave": next_wave, "reason": reason})
    return state


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--package", type=Path, required=True)
    p.add_argument("--store", type=Path, required=True)
    p.add_argument("--recipe", type=Path, required=True)
    p.add_argument("--policy", type=Path, required=True)
    p.add_argument("--tasks", nargs="+", required=True, help="task=repeats ...")
    p.add_argument("--arms", nargs="+", default=list(ARMS))
    p.add_argument("--requests", type=int, required=True)
    p.add_argument("--tokens", type=int, required=True)
    r = sub.add_parser("run")
    r.add_argument("--root", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "prepare":
        tasks = {t.split("=")[0]: int(t.split("=")[1]) for t in a.tasks}
        reg = prepare(a.root.resolve(), a.package.resolve(), a.store.resolve(), a.recipe.resolve(), a.policy.resolve(),
                      tasks, tuple(a.arms), a.requests, a.tokens)
        print(json.dumps({"root": str(a.root), "status": reg["status"], "episodes": len(reg["plan"]),
                          "slots": reg["scheduler"]["cpu_slots"], "history": reg["history"], "caps": reg["caps"]}, indent=1))
    else:
        st = dispatch(a.root.resolve())
        print(json.dumps({k: st.get(k) for k in ("status", "reason", "next_wave", "flows_charged")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
