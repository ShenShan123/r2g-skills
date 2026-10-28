"""Opt-in ORFS process accounting; no RTL, config contents, or environment dump.

wrapper_start counts run_orfs.sh invocations, including preflight failures.
make_start counts successfully spawned external make processes intercepted by the
PATH shim (clean targets included). Internal recursive make via GNU MAKE's absolute
path is not counted; this is process-launch accounting, not full-flow/rebuild counts.
Hard SIGKILL can leave a start without an end; consumers must retain that distinction.
"""
import datetime
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


SAFE_ASSIGNMENTS = {
    "DESIGN_CONFIG", "FLOW_VARIANT", "NUM_CORES", "GPL_TIMING_DRIVEN",
    "GPL_ROUTABILITY_DRIVEN", "SKIP_INCREMENTAL_REPAIR", "SKIP_ANTENNA_REPAIR",
    "DETAILED_ROUTE_END_ITERATION", "GLOBAL_ROUTE_ARGS",
    "GRT_INCREMENTAL_ALLOW_CONGESTION", "SKIP_DETAILED_ROUTE",
}
SAFE_TARGETS = {
    "all", "synth", "floorplan", "place", "cts", "grt", "route", "finish",
    "drc", "lvs", "gui", "clean", "clean_all", "clean_synth", "clean_floorplan",
    "clean_place", "clean_cts", "clean_grt", "clean_route", "clean_finish",
    "--version", "--no-print-directory", "-n", "--dry-run", "-j",
}


def safe_args(args):
    result = []
    for arg in args:
        if "=" in arg:
            key, value = arg.split("=", 1)
            result.append(key + "=" + (value if key in SAFE_ASSIGNMENTS else "<redacted>"))
        elif arg in SAFE_TARGETS or arg.isdecimal():
            result.append(arg)
        else:
            result.append("<argument>")
    return result


def emit(event, **fields):
    path = os.environ.get("R2G_ORFS_AUDIT_FILE")
    if not path:
        return
    row = {
        "schema_version": 1, "event": event,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "pid": os.getpid(), "ppid": os.getppid(), "cwd": os.getcwd(),
        "exit_code": None,
        **{key.lower(): os.environ.get("R2G_ORFS_AUDIT_" + key)
           for key in ("JOB", "DESIGN", "STAGE", "PROJECT", "EXECUTION")},
        "wrapper_id": os.environ.get("R2G_ORFS_AUDIT_WRAPPER_ID"),
        "run_id": os.environ.get("R2G_ORFS_AUDIT_RUN_ID"),
        **fields,
    }
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.write(json.dumps(row, ensure_ascii=True) + "\n")
            stream.flush()
            fcntl.flock(stream, fcntl.LOCK_UN)
    except OSError as exc:
        print("WARNING: ORFS audit write failed: " + str(exc), file=sys.stderr)


def make(args):
    executable = os.environ["R2G_ORFS_REAL_MAKE"]
    command = [executable, *args]
    if not os.environ.get("R2G_ORFS_AUDIT_FILE"):
        os.execv(executable, command)
    invocation = uuid.uuid4().hex
    start = time.monotonic()
    child = None
    pending_signal = None

    def forward(signum, _frame):
        nonlocal pending_signal
        pending_signal = signum
        if child is not None:
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, forward)
    details = {
        "invocation_id": invocation, "argv": [executable, *safe_args(args)],
        "make_level": os.environ.get("MAKELEVEL", "0"),
        "kind": "clean" if any(a == "clean" or a.startswith("clean_") for a in args)
                else "build_or_check",
    }
    try:
        child = subprocess.Popen(command)
    except OSError as exc:
        emit("make_spawn_failed", **details, error_type=type(exc).__name__, exit_code=127)
        return 127
    emit("make_start", **details, pid=child.pid, observer_pid=os.getpid())
    if pending_signal is not None:
        forward(pending_signal, None)
    code = child.wait()
    normalized = code if code >= 0 else 128 - code
    emit("make_end", **details, pid=child.pid, observer_pid=os.getpid(),
         exit_code=normalized, signal=-code if code < 0 else None,
         elapsed_s=round(time.monotonic() - start, 6))
    return normalized


if __name__ == "__main__":
    if sys.argv[1] == "make":
        sys.exit(make(sys.argv[2:]))
    if sys.argv[1] == "wrapper":
        emit(sys.argv[2], pid=int(sys.argv[3]),
             exit_code=int(sys.argv[4]) if sys.argv[2] == "wrapper_end" else None,
             argv=["run_orfs.sh", os.environ.get("R2G_ORFS_AUDIT_PROJECT", ""),
                   os.environ.get("R2G_ORFS_AUDIT_PLATFORM", "")])
