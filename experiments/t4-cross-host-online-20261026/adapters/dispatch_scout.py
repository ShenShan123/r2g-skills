"""Two paired waves; sequential online history within each isolated arm."""
import json
from pathlib import Path
import subprocess
import time

from bridge import ROOT, stable
from freeze import validate


def dispatch():
    validate()
    path = ROOT / "reports/scout-dispatch-r2.json"
    if path.exists(): raise ValueError("dispatcher already started; no implicit restart")
    state = {"status": "RUNNING", "host": "rtlscout", "episode_count": 4,
             "paper_data": False, "started": time.time(), "done": []}
    for task in ["simple_adder", "fifo_sync4"]:
        jobs = []
        for arm in ["native", "tehm"]:
            log = (ROOT / "reports" / ("scout-" + task + "-" + arm + "-r2.log")).open("a")
            p = subprocess.Popen([str(ROOT / "envs/modern/bin/python"), "-u",
                str(ROOT / "adapters/run_scout.py"), "--arm", arm, "--task", task],
                stdout=log, stderr=subprocess.STDOUT)
            jobs.append((arm, p, log))
        state["running"] = [{"arm": arm, "pid": p.pid, "task": task} for arm, p, log in jobs]
        path.write_text(stable(state) + "\n")
        failed = []
        for arm, p, log in jobs:
            rc = p.wait(); log.close()
            episode = ROOT / "hosts/rtlscout" / arm / "episodes" / (task + "__r2/episode.json")
            if rc or not episode.exists(): failed.append(arm)
            state["done"].append({"task": task, "arm": arm, "rc": rc})
        state.pop("running", None)
        if failed:
            state.update(status="PAUSED", reason="technical/accounting stop: " + ",".join(failed),
                         ended=time.time()); path.write_text(stable(state) + "\n"); return
    state.update(status="COMPLETED", ended=time.time()); path.write_text(stable(state) + "\n")


if __name__ == "__main__": dispatch()
