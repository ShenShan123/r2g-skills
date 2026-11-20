#!/usr/bin/env python3
"""Control-host handoff: verified inputs -> development preflight -> four arms.

Runs in its own tmux session on the SSH control host. This never changes the
already-running graph campaign. Experiment code/public materials must have
been staged in the image root before starting this handoff.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess
import time

SOURCE = "/proj/workarea/yangao/exp4_image_inputs_20261115"
ROOT = "/home/yangao/exp4_image_harness_20261115_203"
ARCHIVE = "/proj/workarea/yangao/exp4_image_harness_20261115_203_archive"
CONTROL = Path("/home/yangao/exp4_image_control_20261115")
CPU_IDS = [2, 6, 7, 8, 49, 51, 52, 56, 10, 32, 34, 37, 57, 60, 63, 81,
           38, 39, 40, 42, 82, 83, 85, 87, 43, 45, 46, 47, 89, 90, 92, 93]


def ssh(host, argv, **kwargs):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", host, shlex.join(argv)],
                          check=True, **kwargs)


def state(phase, **details):
    record = {"phase": phase, "updated_at_utc": datetime.now(timezone.utc).isoformat(), **details}
    (CONTROL / "state.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record), flush=True)


def read_source_ready():
    code = ("from pathlib import Path; import json; r=Path(" + repr(SOURCE) + "); "
            "p=r/'INPUTS_READY.json'; "
            "print(p.read_text() if p.exists() else json.dumps({'dev':len(list((r/'data/dev').glob('*/STAGED.json'))),"
            "'test':len(list((r/'data/test').glob('*/STAGED.json')))}))")
    return json.loads(ssh("memlab216", ["python3", "-c", code], capture_output=True, text=True).stdout)


def main():
    CONTROL.mkdir(parents=True, exist_ok=True)
    smoke_check = ("from pathlib import Path; import json; r=Path(" + repr(ROOT) + "); "
        "assert json.loads((r/'smoke/api_v1/summary.json').read_text())['passed']; "
        "assert all(x['strict_full_pass'] for x in json.loads((r/'smoke/small_v1/summary.json').read_text()))")
    ssh("memlab203", ["python3", "-c", smoke_check])
    while True:
        ready = read_source_ready()
        if "passed" in ready:
            if not ready["passed"] or ready["prepared"] != 78:
                raise RuntimeError("Input preparation failed: " + json.dumps(ready))
            break
        state("waiting_for_inputs", **ready)
        time.sleep(30)
    state("transferring_inputs")
    paths = ["data/dev", "data/test", "evidence", "preparation.json", "INPUTS_READY.json",
             "DEV_READY.json", "TEST_READY.json"]
    source = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "memlab216",
        shlex.join(["tar", "-cf", "-", "-C", SOURCE, *paths])], stdout=subprocess.PIPE)
    try:
        ssh("memlab203", ["tar", "-xf", "-", "-C", ROOT], stdin=source.stdout)
    finally:
        source.stdout.close()
    if source.wait():
        raise RuntimeError("Source input transfer failed")
    image_code = ROOT + "/snapshot/experiments/exp4_image_harness"
    common_code = ROOT + "/snapshot/experiments/exp4_harness"
    state("auditing_inputs")
    ssh("memlab203", ["python3", image_code + "/audit_inputs.py", "--root", ROOT])
    state("development_preflight")
    ssh("memlab203", ["python3", image_code + "/preflight.py", "--root", ROOT,
        "--name", "all_dev_final", "--all-dev", "--cpu-ids", ",".join(map(str, CPU_IDS[:8]))])
    probe = ("from pathlib import Path; import json,shutil; "
             "m=dict((s.split(':')[0],s.split(':')[1].strip()) for s in Path('/proc/meminfo').read_text().splitlines()); "
             "print(json.dumps({'available_memory_gib':int(m['MemAvailable'].split()[0])/1048576,"
             "'disk_free_gib':shutil.disk_usage(" + repr(ROOT) + ").free/2**30}))")
    while True:
        resources = json.loads(ssh("memlab203", ["python3", "-c", probe], capture_output=True, text=True).stdout)
        if resources["available_memory_gib"] >= 160 and resources["disk_free_gib"] >= 40:
            break
        state("waiting_for_capacity", **resources)
        time.sleep(30)
    state("freezing", **resources)
    campaign = ["python3", common_code + "/campaign.py", "--root", ROOT, "--task-kind", "image",
        "--cpu-ids", ",".join(map(str, CPU_IDS)), "--image-root", ROOT + "/image/rootfs", "--archive-payloads"]
    ssh("memlab203", campaign)
    ssh("memlab216", ["mkdir", "-p", ARCHIVE])
    links = ("from pathlib import Path; r=Path(" + repr(ARCHIVE) + "); "
        "p=r/'data'; p.symlink_to(" + repr(SOURCE + '/data') + ",target_is_directory=True) if not p.exists() else None; "
        "p=r/'native_bins'; p.symlink_to('/proj/workarea/yangao/exp4_harness_20261115/native_bins',target_is_directory=True) if not p.exists() else None")
    ssh("memlab216", ["python3", "-c", links])
    archive_command = ["/usr/bin/python3", str(CONTROL / "archive_worker.py"),
        "--source-host", "memlab203", "--source-root", ROOT,
        "--archive-host", "memlab216", "--archive-root", ARCHIVE,
        "--staging", str(CONTROL / "metadata")]
    subprocess.run(["tmux", "new-session", "-d", "-s", "exp4_image_archive_20261115",
        shlex.join(archive_command) + " > " + shlex.quote(str(CONTROL / "archive.log")) + " 2>&1"], check=True)
    ssh("memlab203", ["tmux", "new-session", "-d", "-s", "exp4_image_harness_20261115_203",
        shlex.join([*campaign, "--run"]) + " > " + shlex.quote(ROOT + "/controller.log") + " 2>&1"])
    # Require evidence from every arm before describing the formal launch as active.
    for _ in range(24):
        check = ("from pathlib import Path; import json; r=Path(" + repr(ROOT) + "); "
                 "print(json.dumps([{**json.loads(p.read_text()), 'successful_api_calls': "
                 "sum(x.get('status')==200 and x.get('delivered',False) for x in "
                 "json.loads((p.parent/'usage.json').read_text())) if (p.parent/'usage.json').exists() else 0} "
                 "for p in (r/'runs').glob('*/s1/controller.json')]))")
        jobs = json.loads(ssh("memlab203", ["python3", "-c", check], capture_output=True, text=True).stdout)
        if len(jobs) == 4:
            if not all(j.get("status") == "running" for j in jobs):
                raise RuntimeError("A formal arm did not start: " + json.dumps(jobs))
            if all(j["successful_api_calls"] > 0 for j in jobs):
                state("formal_started", compute_root=ROOT, archive_root=ARCHIVE, jobs=jobs)
                return
        time.sleep(5)
    state("formal_dispatched", compute_root=ROOT, jobs=jobs,
          note="Waiting for all four arms' first successful API response; inspect controller.log")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        state("stopped_on_error", error=f"{type(exc).__name__}: {exc}")
        raise
