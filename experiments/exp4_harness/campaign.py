#!/usr/bin/env python3
"""Freeze and run the twelve Exp4 attempts in four disjoint eight-CPU slots.

Default: prepare immutable configs/schedule/source hashes, without starting jobs.
Pass --run explicitly to execute or resume; completed development is never rerun.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
from archive_worker import wait_for_archive

ARMS = ("deepseek_native", "deepseek_r2g", "gpt_native", "gpt_r2g")
SEED = 20261115


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def source_hashes(root):
    sources = {"code": root / "snapshot/experiments/exp4_harness",
               "validation": root / "snapshot/experiments/exp4_validation",
               "agent": root / "snapshot/agent", "runtime": root / "runtime",
               "public": root / "public", "native_bins": root / "native_bins"}
    image_code = root / "snapshot/experiments/exp4_image_harness"
    if image_code.is_dir():
        sources["image_code"] = image_code
    result = {}
    for label, base in sources.items():
        if not base.is_dir():
            raise FileNotFoundError(base)
        files = {}
        for path in sorted(base.rglob("*")):
            if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
                continue
            if path.is_file():
                files[str(path.relative_to(base))] = digest(path)
        if not files:
            raise ValueError(f"Empty required source tree: {base}")
        result[label] = {"root": str(base), "files": files}
    for relative in ("cohort.json", "evidence/staged_inputs.json", "evidence/staged_inputs_audit.json"):
        result[relative] = digest(root / relative)
    for relative in ("image/image.json", "image/provenance.json"):
        if (root / relative).exists():
            result[relative] = digest(root / relative)
    if (root / "evidence/input_audit_203.json").exists():
        result["evidence/input_audit_203.json"] = digest(root / "evidence/input_audit_203.json")
    return result


def make_schedule(cpuset, seed):
    if len(cpuset) != 32 or len(set(cpuset)) != 32:
        raise ValueError("Exactly thirty-two distinct allowed CPU IDs are required")
    rng = random.Random(seed)
    jobs = []
    # One arm per slot: all four arms start together, then each independently
    # advances through its three repeats without waiting for slower arms.
    arms = list(ARMS)
    rng.shuffle(arms)
    for repeat in (1, 2, 3):
        for slot, arm in enumerate(arms):
            jobs.append({"arm": arm, "repeat": repeat, "slot": slot,
                         "cpuset": ",".join(map(str, cpuset[slot * 8:(slot + 1) * 8]))})
    return jobs


def config_for(root, job, image, task_kind="graph"):
    deepseek = job["arm"].startswith("deepseek")
    r2g = job["arm"].endswith("r2g")
    return {
        "arm": job["arm"], "repeat": job["repeat"], "task_kind": task_kind,
        "harness": "r2g" if r2g else "dsh" if deepseek else "codex",
        "harness_version": "frozen_R2G_source" if r2g else "0.2.2-rc.1" if deepseek else "0.169.0",
        "model": "deepseek-flash" if deepseek else "gpt-6.1-sol",
        "protocol": "anthropic" if deepseek else "openai-responses",
        "out": str(root / "runs" / job["arm"] / f"s{job['repeat']}"),
        "dev_root": str(root / "data/dev"), "public_root": str(root / "public"),
        "platform_root": str(root / "data/platforms"),
        "task_file": str(root / "public" / ("task_r2g.txt" if r2g else "task_native.txt")),
        "native_bins": str(root / "native_bins"), "agent_root": str(root / "snapshot/agent"),
        "r2g_root": str(root / "runtime"), "image": image, "cpuset": job["cpuset"],
        "upstream_url": f"http://127.0.0.1:{18776 if deepseek else 18775}/v1",
        "policy": {"thinking": True, "reasoning_effort": "high" if deepseek else "medium", "max_tokens": 32768},
        "caps": {"model_calls": 200, "wall_seconds": 14400, "cpu_count": 8, "memory_gib": 32},
    }


def prepare(root, cpus, seed, image, image_root=None, archive_payloads=False, task_kind="graph"):
    freeze_path = root / "campaign_freeze.json"
    if image_root:
        image_root = str(Path(image_root).resolve())
        image_id = json.loads((Path(image_root).parent / "image.json").read_text())["Id"]
    else:
        inspected = subprocess.run(["podman", "image", "inspect", "--format", "{{.Id}}", image],
                                   capture_output=True, text=True, check=True)
        image_id = inspected.stdout.strip()
    if not image_id:
        raise RuntimeError("Image inspection returned no immutable ID")
    if freeze_path.exists():
        frozen = json.loads(freeze_path.read_text())
        if frozen.get("task_kind", "graph") != task_kind:
            raise RuntimeError("Campaign task differs from freeze")
        if frozen.get("image_root") != image_root or frozen.get("archive_payloads", False) != archive_payloads:
            raise RuntimeError("Campaign runtime/archive configuration differs from freeze")
        if frozen["seed"] != seed or frozen["image"] != image or frozen["image_id"] != image_id:
            raise RuntimeError("Campaign seed/image differs from freeze")
        if cpus is not None and frozen["cpu_ids"] != cpus:
            raise RuntimeError("Campaign CPU assignment differs from freeze")
        if source_hashes(root) != frozen["source_hashes"]:
            raise RuntimeError("Frozen campaign sources changed; refuse mixed-version resume")
        for job in frozen["jobs"]:
            if digest(job["config"]) != job["config_sha256"]:
                raise RuntimeError("Frozen attempt config changed: " + job["config"])
        return frozen
    cpus = cpus if cpus is not None else sorted(os.sched_getaffinity(0))[:32]
    if not set(cpus) <= set(os.sched_getaffinity(0)):
        raise ValueError("Requested CPUs are not in controller affinity")
    jobs = make_schedule(cpus, seed)
    for job in jobs:
        config = config_for(root, job, image_id, task_kind)
        config.update(image_root=image_root, archive_payloads=archive_payloads)
        path = root / "configs" / job["arm"] / f"s{job['repeat']}.json"
        if path.exists() and json.loads(path.read_text()) != config:
            raise RuntimeError("Refusing to overwrite differing config: " + str(path))
        if not Path(config["task_file"]).is_file():
            raise FileNotFoundError(config["task_file"])
        save(path, config)
        job.update(config=str(path), config_sha256=digest(path))
    frozen = {"schema": 1, "task_kind": task_kind, "frozen_at_utc": now(), "seed": seed, "image": image,
              "image_id": image_id, "max_workers": 4, "cpu_ids": cpus,
              "jobs": jobs, "source_hashes": source_hashes(root),
              "image_root": image_root, "archive_payloads": archive_payloads,
              "compute_host": os.uname().nodename}
    if image_root:
        frozen["image_provenance"] = json.loads((Path(image_root).parent / "provenance.json").read_text())
        frozen["container_runtime"] = subprocess.check_output(["bwrap", "--version"], text=True).strip()
    save(freeze_path, frozen)
    return frozen


def run_job(root, job):
    config = json.loads(Path(job["config"]).read_text())
    out = Path(config["out"]); out.mkdir(parents=True, exist_ok=True)
    state_path = out / "controller.json"
    state = {"arm": job["arm"], "repeat": job["repeat"], "slot": job["slot"],
             "cpuset": job["cpuset"], "controller_started_at_utc": now(), "status": "running"}
    code = root / "snapshot/experiments/exp4_harness"
    try:
        if not (out / "result.json").is_file():
            if any((out / name).exists() for name in ("work", "home", "api")):
                raise RuntimeError("Interrupted development has no final result; preserve it for explicit recovery")
            state["phase"] = "development"; save(state_path, state)
            with (out / "controller_development.log").open("a") as log:
                run = subprocess.run([sys.executable, str(code / "runner_native.py"), "--config", job["config"]],
                                     stdout=log, stderr=subprocess.STDOUT)
            state["development_cli_exit_code"] = run.returncode
            if not (out / "result.json").is_file():
                raise RuntimeError(f"Development runner did not produce result.json (CLI exit {run.returncode})")
        result = json.loads((out / "result.json").read_text())
        if result.get("version_only"):
            raise RuntimeError("Version-only smoke result cannot be used as an experimental attempt")
        if config.get("archive_payloads"):
            state["phase"] = "archiving_development"; save(state_path, state)
            wait_for_archive(out, "development", ["work", "home", "tmp"])
        state.update(phase="production_and_scoring", development_stop_reason=result.get("stop_reason"),
                     development_exit_code=result.get("exit_code"))
        save(state_path, state)
        with (out / "controller_batch.log").open("a") as log:
            batch = subprocess.run([sys.executable, str(code / "run_batch.py"), "--root", str(root),
                                    "--config", job["config"]], stdout=log, stderr=subprocess.STDOUT)
        state["batch_cli_exit_code"] = batch.returncode
        expected = json.loads((root / "cohort.json").read_text())["splits"]["test"]
        state["scored_designs"] = sum((out / "production" / r["design"] / "score/score.json").is_file() for r in expected)
        state["expected_designs"] = len(expected)
        state["status"] = "complete" if batch.returncode == 0 and state["scored_designs"] == len(expected) else "incomplete"
    except Exception as exc:
        state.update(status="incomplete", error=f"{type(exc).__name__}: {exc}")
    state["controller_finished_at_utc"] = now()
    save(state_path, state)
    print(json.dumps({k: state[k] for k in ("arm", "repeat", "status")}), flush=True)
    return state


def run(root, frozen):
    def worker(slot):
        return [run_job(root, job) for job in frozen["jobs"] if job["slot"] == slot]
    with ThreadPoolExecutor(max_workers=frozen["max_workers"]) as executor:
        futures = [executor.submit(worker, slot) for slot in range(frozen["max_workers"])]
        states = [state for future in futures for state in future.result()]
    save(root / "campaign_status.json", {"updated_at_utc": now(), "jobs": states,
         "status": "complete" if all(s["status"] == "complete" for s in states) else "incomplete"})
    subprocess.run([sys.executable, str(root / "snapshot/experiments/exp4_harness/summarize.py"),
                    "--root", str(root)], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--cpu-ids", help="Thirty-two comma-separated CPU IDs; eight per slot")
    ap.add_argument("--image", default="localhost/exp4c-conv:1")
    ap.add_argument("--image-root", type=Path, help="Exported OCI filesystem for Bubblewrap")
    ap.add_argument("--archive-payloads", action="store_true")
    ap.add_argument("--task-kind", choices=("graph", "image"), default="graph")
    a = ap.parse_args(); root = a.root.resolve(); root.mkdir(parents=True, exist_ok=True)
    with (root / ".campaign.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        frozen = prepare(root, list(map(int, a.cpu_ids.split(","))) if a.cpu_ids else None,
                         a.seed, a.image, a.image_root, a.archive_payloads, a.task_kind)
        print(json.dumps({"status": "frozen", "jobs": len(frozen["jobs"]), "max_workers": frozen["max_workers"],
                          "frozen_at_utc": frozen["frozen_at_utc"]}), flush=True)
        if a.run:
            run(root, frozen)


if __name__ == "__main__":
    main()
