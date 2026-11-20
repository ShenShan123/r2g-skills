#!/usr/bin/env python3
"""Check image-campaign isolation and the four real model/tool delivery paths."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "exp4_harness"))
from campaign import ARMS, config_for
from runner_native import run_native
from run_batch import run_container

TASK = """This is an infrastructure smoke check only. Use your shell tool to:
1. Create /work/converter/run.sh containing a bash script that prints
   EXP4_IMAGE_SMOKE_OK and writes that same text to /work/smoke_executed.txt.
2. Execute bash /work/converter/run.sh and verify that the text file exists.
3. Reply OK and finish.
The dummy converter only verifies the harness delivery path. This task requires
no design inspection, dataset conversion, or further development tests.
"""

PROBE = """import os,socket,json,errno
from pathlib import Path
import numpy,pandas,torch
assert not Path('/r2g/def-graph').exists()
assert not Path('/agent/r2g_agent').exists()
assert socket.if_nameindex()==[(1,'lo')],socket.if_nameindex()
assert not list(Path('/dev').glob('nvidia*'))
try:
 Path('/etc/exp4-image-write-test').write_text('x')
except OSError as e: assert e.errno in (errno.EROFS,errno.EACCES)
else: raise AssertionError('root filesystem was writable')
Path('/tmp/write-test').write_text('ok')
caps=next(x for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('CapEff:'))
assert int(caps.split()[1],16)==0,caps
print(json.dumps({'numpy':numpy.__version__,'pandas':pandas.__version__,
 'torch':torch.__version__,'cpu_affinity':sorted(os.sched_getaffinity(0)),
 'network':socket.if_nameindex(),'gpu_exposed':False,'private_runtime_exposed':False,
 'read_only_root':True}))
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--name", default="api_v1")
    ap.add_argument("--cpus", default="2,6,7,8,49,51,52,56")
    args = ap.parse_args()
    root = args.root.resolve()
    image_root = root / "image/rootfs"
    cpus = [int(cpu) for cpu in args.cpus.split(",")]
    if len(cpus) != 8 or len(set(cpus)) != 8:
        raise ValueError("Smoke uses eight distinct CPUs, two per arm")
    smoke = root / "smoke" / args.name
    smoke.mkdir(parents=True)
    (root / "data/dev").mkdir(parents=True, exist_ok=True)
    image_id = json.loads((image_root.parent / "image.json").read_text())["Id"]
    rc = run_container([], ["python3", "-c", PROBE], smoke / "isolation.log", image_id,
                       image_root=image_root, cpuset=",".join(map(str, cpus[:2])), cpus=2)
    if rc:
        raise RuntimeError((smoke / "isolation.log").read_text())
    task_file = smoke / "task.txt"
    task_file.write_text(TASK)

    def check(item):
        index, arm = item
        cpuset = ",".join(map(str, cpus[index * 2:index * 2 + 2]))
        config = config_for(root, {"arm": arm, "repeat": 0, "cpuset": cpuset}, image_id,
                            task_kind="image")
        config.update(image_root=str(image_root), task_file=str(task_file), out=str(smoke / arm))
        config["caps"].update(model_calls=8, wall_seconds=180, cpu_count=2)
        result = run_native(config)
        row = {key: result.get(key) for key in (
            "arm", "task_kind", "exit_code", "stop_reason", "model_calls", "successful_model_calls",
            "usage_complete", "converter_present", "converter_sha256", "cpu_affinity",
            "harness_version", "wall_seconds", "provider_usage")}
        executed = smoke / arm / "work/smoke_executed.txt"
        row["dummy_executed"] = executed.is_file() and executed.read_text().strip() == "EXP4_IMAGE_SMOKE_OK"
        row["passed"] = (row["exit_code"] == 0 and row["successful_model_calls"] > 0 and
                         row["usage_complete"] and row["converter_present"] and row["dummy_executed"])
        print(json.dumps(row), flush=True)
        return row

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(check, enumerate(ARMS)))
    summary = {"finished_at_utc": datetime.now(timezone.utc).isoformat(), "task_kind": "image",
               "root": str(root), "isolation_passed": rc == 0,
               "passed": all(row["passed"] for row in rows), "arms": rows}
    (smoke / "summary.json").write_text(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
