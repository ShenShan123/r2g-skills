#!/usr/bin/env python3
"""Develop and freeze a dataset converter using a real, isolated model harness.

Usage: python3 runner_native.py --config attempt.json [--version-only]
The JSON config supplies every runtime path; this does not use personal CLI homes
or credentials. See native_config.example.json. No held-out inputs are mounted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tarfile
import threading
import time
import uuid
from pathlib import Path

from native_proxy import Ledger, make_proxy
from container_runtime import bubblewrap_command

HERE = Path(__file__).resolve().parent
TOKEN_KEYS = ("input_tokens", "uncached_input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
              "output_tokens", "reasoning_tokens", "total_tokens")


def freeze_converter(source: Path, out: Path) -> dict:
    if not (source / "run.sh").is_file():
        return {"converter_present": False, "freeze_error": "converter/run.sh is missing"}
    paths = [source, *sorted(source.rglob("*"))]
    if any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in paths):
        return {"converter_present": False, "freeze_error": "converter contains a symlink or special file"}
    destination = out / "converter"
    shutil.copytree(source, destination)
    archive = out / "converter.tar"
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as tf:
        for path in [destination, *sorted(destination.rglob("*"))]:
            info = tf.gettarinfo(str(path), arcname="converter" + ("/" + str(path.relative_to(destination)) if path != destination else ""))
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644
            if path.is_file():
                with path.open("rb") as handle:
                    tf.addfile(info, handle)
            else:
                tf.addfile(info)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out / "converter.tar.sha256").write_text(digest + "  converter.tar\n")
    for path in sorted(destination.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
    destination.chmod(0o555)
    return {"converter_present": True, "converter_sha256": digest, "converter_archive": str(archive),
            "converter_root": str(destination)}


def process_tree_rss(pid: int) -> int:
    """Host /proc RSS sum for the container subtree; includes native subagents."""
    total, pending, seen = 0, [pid], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            status = Path(f"/proc/{current}/status").read_text()
            total += next((int(line.split()[1]) * 1024 for line in status.splitlines() if line.startswith("VmRSS:")), 0)
            for task in Path(f"/proc/{current}/task").iterdir():
                pending.extend(int(x) for x in (task / "children").read_text().split())
        except (OSError, ProcessLookupError):
            pass
    return total


def build_container_command(config: dict, out: Path, name: str, version_only=False) -> list[str]:
    native = Path(config["native_bins"]).resolve()
    caps = config.get("caps", {})
    command = ["podman", "run", "--name", name, "--network=none", "--read-only", "--cap-drop=all",
               "--pids-limit=1024", "--memory=%sg" % caps.get("memory_gib", 32),
               "--cpus=" + str(caps.get("cpu_count", 8)),
               "--tmpfs", "/run:rw,size=64m", "--workdir", "/work"]
    mounts = [(Path(config["dev_root"]).resolve(), "/data/dev", "ro"),
              (Path(config["public_root"]).resolve(), "/data/spec", "ro"),
              (out / "work", "/work", "rw"), (out / "home", "/agent-home", "rw"),
              (out / "tmp", "/tmp", "rw"),
              (out / "api", "/experiment/api", "ro"),
              (out / "task.txt", "/experiment/task.txt", "ro"),
              (HERE / "native_bootstrap.py", "/experiment/native_bootstrap.py", "ro")]
    if config["harness"] == "r2g":
        mounts += [(Path(config["agent_root"]).resolve(), "/agent", "ro"),
                   (Path(config["r2g_root"]).resolve(), "/r2g", "ro"),
                   (HERE / "runner_r2g.py", "/experiment/runner_r2g.py", "ro"),
                   (out / "config.json", "/experiment/config.json", "ro")]
    else:
        mounts += [(native / "node", "/opt/native/node", "ro"),
                   (native / config["harness"], "/opt/native/" + config["harness"], "ro")]
    if config.get("platform_root"):
        mounts += [(Path(config["platform_root"]).resolve(), "/data/platform", "ro")]
    for source, destination, mode in mounts:
        command += ["-v", f"{source}:{destination}:{mode}"]
    environment = {"PATH": "/opt/native/node/bin:/opt/py/bin:/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin:/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin:/usr/local/bin:/usr/bin:/bin",
                       "OMP_NUM_THREADS": str(caps.get("cpu_count", 8)), "OPENBLAS_NUM_THREADS": "1",
                       "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1",
                       "PYTHONPATH": "/agent:/agent/deps" if config["harness"] == "r2g" else ""}
    for key, value in environment.items():
        command += ["-e", key + "=" + value]
    command += ["--entrypoint", "python3", config.get("image", "localhost/exp4c-conv:1"),
                "/experiment/native_bootstrap.py", "--harness", config["harness"], "--model", config["model"],
                "--protocol", config["protocol"],
                "--cpu-count", str(caps.get("cpu_count", 8))]
    if config.get("cpuset"):
        command += ["--cpuset", config["cpuset"]]
    if (config.get("policy") or {}).get("reasoning_effort"):
        command += ["--reasoning-effort", config["policy"]["reasoning_effort"]]
    if version_only:
        command += ["--version-only"]
    if config.get("image_root"):
        entry = command.index("/experiment/native_bootstrap.py", command.index("--entrypoint"))
        return bubblewrap_command(config["image_root"], mounts, environment,
                                  ["python3", *command[entry:]], "/work")
    return command


def run_native(config: dict, *, version_only=False) -> dict:
    required = ("harness", "model", "out", "dev_root", "public_root", "native_bins", "upstream_url", "protocol", "task_file")
    for key in required:
        if not config.get(key):
            raise ValueError(f"missing config field {key}")
    if config["harness"] not in ("dsh", "codex", "r2g"):
        raise ValueError("harness must be dsh, codex or r2g")
    if config.get("task_kind", "graph") not in ("graph", "image"):
        raise ValueError("task_kind must be graph or image")
    if config["harness"] == "r2g" and not all(config.get(k) for k in ("agent_root", "r2g_root")):
        raise ValueError("R2G requires explicit frozen agent_root and r2g_root")
    if config["harness"] != "r2g" and config["protocol"] != ("anthropic" if config["harness"] == "dsh" else "openai-responses"):
        raise ValueError("native CLI protocol mismatch")
    caps = {"model_calls": 200, "wall_seconds": 14400, "cpu_count": 8, "memory_gib": 32, **config.get("caps", {})}
    config = {**config, "caps": caps}
    out = Path(config["out"]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "result.json").exists() or any((out / sub).exists() for sub in ("work", "home", "api")):
        raise FileExistsError("attempt output already exists; independent repeats need fresh output directories")
    for sub in ("work", "home", "api", "tmp"):
        (out / sub).mkdir(mode=0o700)
    shutil.copyfile(config["task_file"], out / "task.txt")
    (out / "config.json").write_text(json.dumps(config, indent=2))
    ledger = Ledger(out / "usage.json", config["model"], int(caps["model_calls"]))
    proxy = make_proxy(out / "api/gateway.sock", config["upstream_url"], config["protocol"], ledger, config.get("policy"))
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    name = "exp4-native-" + uuid.uuid4().hex[:12]
    command = build_container_command(config, out, name, version_only)
    (out / "launch.json").write_text(json.dumps({"command": command, "container": name}, indent=2))
    start, stop, peak_rss, container_pid = time.monotonic(), None, 0, 0
    with (out / "run.jsonl").open("wb") as stdout, (out / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   start_new_session=bool(config.get("image_root")))
        try:
            while process.poll() is None:
                time.sleep(1)
                if config.get("image_root"):
                    container_pid = process.pid
                elif not container_pid:
                    inspection = subprocess.run(["podman", "inspect", "--format", "{{.State.Pid}}", name], capture_output=True, text=True)
                    if inspection.returncode == 0:
                        container_pid = int(inspection.stdout.strip() or "0")
                if container_pid:
                    peak_rss = max(peak_rss, process_tree_rss(container_pid))
                if time.monotonic() - start >= caps["wall_seconds"]:
                    stop = "cap_wall"
                elif peak_rss > caps["memory_gib"] * 1024 ** 3:
                    stop = "cap_memory"
                # The gateway enforces the model cap before every request. Allow
                # tools requested by the last admitted response to finish.
                if stop:
                    ledger.stopped = True
                    if config.get("image_root"):
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        subprocess.run(["podman", "stop", "--time", "10", name], capture_output=True)
                    break
            returncode = process.wait(timeout=30)
        finally:
            if process.poll() is None:
                if config.get("image_root"):
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    subprocess.run(["podman", "kill", name], capture_output=True)
                process.kill()
                process.wait()
            ledger.stopped = True
            proxy.shutdown()
            proxy.server_close()
    calls = ledger.snapshot()
    if stop is None and (ledger.refused_requests or returncode and len(calls) >= caps["model_calls"]):
        stop = "cap_model_calls"
    complete = bool(calls) and all(row.get("finished") and row.get("usage_reported") and row.get("stream_complete") for row in calls)
    totals = {key: sum(row.get(key) or 0 for row in calls) if complete else None for key in TOKEN_KEYS}
    (out / "usage.json").write_text(json.dumps(calls, indent=2))
    # The stopped container cannot exec. Version is captured separately by the
    # required --version-only infrastructure smoke before any campaign.
    runtime_file = out / "work/native_runtime.json"
    runtime = json.loads(runtime_file.read_text()) if runtime_file.is_file() else {}
    result = {"arm": config.get("arm", config["harness"] + "_native"), "repeat": config.get("repeat"),
              "task_kind": config.get("task_kind", "graph"),
              "harness": config["harness"], "model": config["model"], "protocol": config["protocol"],
              "harness_version": runtime.get("harness_version"), "cpu_affinity": runtime.get("cpu_affinity"),
              "inner_sandbox_mode": runtime.get("inner_sandbox_mode"), "isolation": runtime.get("isolation"),
              "caps": caps, "policy": config.get("policy"),
              "stop_reason": stop or ("finished" if returncode == 0 else "harness_error"), "exit_code": returncode,
              "model_calls": len(calls), "successful_model_calls": sum(c.get("status") == 200 for c in calls),
              "budget_refusals": ledger.refused_requests,
              "usage_complete": complete, "provider_usage": totals,
              "known_provider_usage": {key: sum(row.get(key) or 0 for row in calls) for key in TOKEN_KEYS},
              "wall_seconds": round(time.monotonic() - start, 3), "peak_process_tree_rss_bytes": peak_rss,
              "version_only": version_only,
              **({"converter_present": False} if version_only else freeze_converter(out / "work/converter", out))}
    (out / "result.json").write_text(json.dumps(result, indent=2))
    if not config.get("image_root"):
        subprocess.run(["podman", "rm", name], capture_output=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--version-only", action="store_true", help="no model request: check image, mounts, Node and native CLI")
    args = parser.parse_args()
    result = run_native(json.loads(args.config.read_text()), version_only=args.version_only)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
