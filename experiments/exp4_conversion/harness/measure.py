#!/usr/bin/env python3
"""Run one command with a wall-clock limit; record exit code, wall/CPU seconds, peak RSS.

    measure.py <timeout_s> <result.json> <cmd> [args...]

Peak RSS is ru_maxrss over the waited-for process tree (the largest single process, KiB),
from getrusage(RUSAGE_CHILDREN). On timeout the whole process group is killed.
"""
import json
import os
import resource
import signal
import subprocess
import sys
import time


def main():
    timeout, res, cmd = float(sys.argv[1]), sys.argv[2], sys.argv[3:]
    t0 = time.time()
    p = subprocess.Popen(cmd, start_new_session=True)
    timed_out = False
    try:
        rc = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        rc = p.wait()
    wall = time.time() - t0
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    out = {"exit_code": rc, "timed_out": timed_out, "timeout_s": timeout, "wall_seconds": round(wall, 2),
           "user_seconds": round(ru.ru_utime, 2), "sys_seconds": round(ru.ru_stime, 2),
           "peak_rss_mb": round(ru.ru_maxrss / 1024.0, 1), "cmd": cmd}
    with open(res, "w") as f:
        json.dump(out, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
