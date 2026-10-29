"""Continue only qualified, never-started episodes; sequential admission.

Active attempts finish under native rules. A new UNKNOWN pauses the next
admission. Neither old failed episode is ever a queue item.
"""
import json
import subprocess
import time

from bridge import ROOT, stable
from continuation import ROSTER, require_host, unstarted, validate
from phase_gate import require_clear


def save(path, state):
    path.write_text(stable(state)+"\n")
    from summarize_integration import collect
    (ROOT / "reports/integration-status.json").write_text(stable(collect())+"\n")


def dispatch():
    validate()
    path = ROOT / "reports/continuation-state-c1.json"
    if path.exists():
        raise ValueError("continuation already started; no implicit restart")
    state = {"schema": "table4-continuation-state-c1", "paper_data": False,
             "status": "RUNNING", "started": time.time(), "done": [], "withheld": []}
    save(path, state)
    for host, arm, task in ROSTER:
        try:
            validate()
        except ValueError as exc:
            state.update(status="PAUSED", reason=str(exc), ended=time.time())
            save(path, state)
            return
        qualification = json.loads((ROOT / "reports/continuation-qualification.json").read_text())
        if not qualification["hosts"][host]["launch_allowed"]:
            state["withheld"].append({"host": host, "arm": arm, "task": task,
                                      "reason": qualification["hosts"][host]["reason"]})
            continue
        require_host(host)
        try:
            require_clear()
            unstarted(host, arm, task)
        except (RuntimeError, ValueError) as exc:
            state.update(status="PAUSED", reason=str(exc), ended=time.time())
            save(path, state)
            return
        runtime, script = ("rtlfixer", "run_fixer.py") if host == "rtlfixer" else ("modern", "run_scout.py")
        logpath = ROOT / "reports" / (host + "-" + task + "-" + arm + "-c1.log")
        with logpath.open("x") as log:
            process = subprocess.Popen([str(ROOT / "envs" / runtime / "bin/python"), "-u",
                str(ROOT / "adapters" / script), "--arm", arm, "--task", task, "--continuation"],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            state["running"] = {"host": host, "arm": arm, "task": task, "pid": process.pid}
            save(path, state)
            rc = process.wait()
        receipt = ROOT / "hosts" / host / arm / "episodes" / (task + "__c1/episode.json")
        state.pop("running", None)
        status = json.loads(receipt.read_text())["status"] if receipt.exists() else "SETUP_FAILURE"
        state["done"].append({"host": host, "arm": arm, "task": task, "rc": rc,
                              "status": status, "log": str(logpath.relative_to(ROOT))})
        if rc or status not in {"ACCEPTED", "NOT_ACCEPTED"}:
            state.update(status="PAUSED", reason="technical/accounting stop", ended=time.time())
            save(path, state)
            return
        save(path, state)
    state.update(status="QUALIFIED_TASKS_COMPLETED_WAITING_FOR_HOST" if state["withheld"] else "COMPLETED",
                 ended=time.time())
    save(path, state)


if __name__ == "__main__":
    dispatch()
