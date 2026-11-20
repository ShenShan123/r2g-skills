#!/usr/bin/env python3
"""Apply a frozen converter once per design; independently score every attempt."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from container_runtime import bubblewrap_command
from archive_worker import wait_for_archive

HERE = Path(__file__).resolve().parent


def wait_capacity():
    while os.getloadavg()[0] >= 110:
        time.sleep(15)


def run_container(mounts, command, log, image, *, cpus=8, cpuset=None, image_root=None):
    name = "exp4h-" + uuid.uuid4().hex[:12]
    cmd = ["podman", "run", "--rm", "--name", name, "--network=none", "--read-only", "--cap-drop=all",
           "--tmpfs", "/run:rw,size=64m",
           "-e", f"OMP_NUM_THREADS={cpus}", "-e", "OPENBLAS_NUM_THREADS=1",
           "-e", "MKL_NUM_THREADS=1", "-e", "PYTHONDONTWRITEBYTECODE=1"]
    for source, target, mode in mounts:
        cmd += ["-v", f"{Path(source).resolve()}:{target}:{mode}"]
    log.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tmp-", dir=log.parent) as scratch, log.open("w") as f:
        cmd += ["-v", f"{Path(scratch).resolve()}:/tmp:rw", image, *command]
        if image_root:
            cmd = bubblewrap_command(image_root, [*mounts, (scratch, "/tmp", "rw")],
                {"OMP_NUM_THREADS": str(cpus), "OPENBLAS_NUM_THREADS": "1",
                 "MKL_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1"}, command)
        # Affinity is inherited by evaluator and production descendants, even
        # on this host's rootless cgroups-v1 setup.
        if cpuset:
            cmd = ["taskset", "-c", cpuset, *cmd]
        p = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    return p.returncode


def produce(config, root, split, row, destination):
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "out").mkdir(exist_ok=True)
    runfile = destination / "run.json"
    if runfile.exists():
        return
    dev_result = json.loads((Path(config["out"]) / "result.json").read_text())
    if not dev_result.get("converter_present"):
        runfile.write_text(json.dumps({"exit_code": 2, "stop_reason": "no_converter", "wall_seconds": 0,
                                      "cpu_seconds": 0, "peak_process_tree_rss_bytes": 0}))
        return
    archive = Path(config["out"]) / "converter.tar"
    if hashlib.sha256(archive.read_bytes()).hexdigest() != dev_result["converter_sha256"]:
        raise RuntimeError("Frozen converter archive changed")
    mounts = [(dev_result["converter_root"], "/converter", "ro"),
              (root / "data" / split / row["design"], "/design", "ro"),
              (root / "data/platforms" / row["platform"], "/platform", "ro"),
              (destination, "/production", "rw"), (HERE / "measure.py", "/measure.py", "ro")]
    if config["harness"] == "r2g":
        mounts.append((config["r2g_root"], "/r2g", "ro"))
    wait_capacity()
    start = time.monotonic()
    command = ["python3", "/measure.py", "--report", "/production/run.json", "--seconds", "1800"]
    if config.get("cpuset"):
        command += ["--cpuset", config["cpuset"]]
    command += ["--", "bash", "/converter/run.sh", "/design", "/platform", "/production/out"]
    rc = run_container(mounts, command, destination / "production.log", config["image"],
                       cpuset=config.get("cpuset"), image_root=config.get("image_root"))
    # A container startup failure must still occupy the design's denominator.
    if not runfile.exists():
        runfile.write_text(json.dumps({"exit_code": rc or 1, "stop_reason": "container_error",
                                      "wall_seconds": time.monotonic() - start}))


def evaluate(config, root, split, row, destination, *, truth_only=False):
    score = destination / "score"
    score.mkdir(parents=True, exist_ok=True)
    if (score / "score.json").exists() and not truth_only:
        return
    reference = root / "reference" / row["design"]
    reference.mkdir(parents=True, exist_ok=True)
    mounts = [(root / "data" / split / row["design"], "/design", "ro"),
              (root / "data/platforms" / row["platform"], "/platform", "ro"),
              (reference, "/reference", "rw"), (destination, "/production", "ro"), (score, "/score", "rw"),
              (HERE, "/private/exp4_harness", "ro"),
              (HERE.parent / "exp4_validation", "/private/exp4_validation", "ro")]
    command = ["python3", "/private/exp4_harness/evaluate.py", "--platform", row["platform"], "--design", row["design"]]
    if config.get("task_kind") == "image":
        mounts = mounts[:-2] + [(HERE.parent, "/private", "ro")]
        command[1] = "/private/exp4_image_harness/image_evaluate.py"
        presentation = HERE.parent / "exp4_image_harness/presentation.json"
        if (presentation.exists() and config.get("repeat") == 1 and
                json.loads(presentation.read_text())["design"] == row["design"]):
            command.append("--save-preview")
    if truth_only:
        command.append("--truth-only")
    wait_capacity()
    rc = run_container(mounts, command, score / "evaluate.log", config["image"], cpus=2,
                       cpuset=config.get("cpuset"), image_root=config.get("image_root"))
    if rc:
        raise RuntimeError(f"Evaluator failed for {row['design']}; see {score / 'evaluate.log'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--split", choices=("dev", "test"), default="test")
    ap.add_argument("--design")
    a = ap.parse_args()
    config = json.loads(a.config.read_text())
    rows = json.loads((a.root / "cohort.json").read_text())["splits"][a.split]
    for row in rows:
        if a.design and row["design"] != a.design:
            continue
        destination = Path(config["out"]) / ("production" if a.split == "test" else "preflight") / row["design"]
        produce(config, a.root, a.split, row, destination)
        evaluate(config, a.root, a.split, row, destination)
        print(json.dumps({"design": row["design"], "status": "scored"}), flush=True)
        if config.get("archive_payloads"):
            wait_for_archive(destination, "production", ["out"])


if __name__ == "__main__":
    main()
