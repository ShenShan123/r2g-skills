"""Once-only C2 dispatcher for exactly five never-started EDA episodes."""
import json
import subprocess
import time

from bridge import ROOT, stable
from continuation_c2 import ROSTER, require_host, unstarted, validate
from dispatch_continuation import save
from phase_gate import require_clear


def terminal_status(work, rc):
    primary = work / "episode.json"
    if primary.exists():
        raw = json.loads(primary.read_text())
        if rc and raw["status"] in {"ACCEPTED", "NOT_ACCEPTED"}:
            return "TECHNICAL_STOP_WITH_RECEIPT"
        return raw["status"]
    scalar = work / "terminal-accounting.json"
    if scalar.exists():
        raw = json.loads(scalar.read_text())
        return "ACCOUNTING_AND_RECORDER_FAILURE" if raw["status"] == "ACCOUNTING_FAILURE" else "RECORDER_FAILURE"
    ledger = work / "provider/requests.jsonl"
    if ledger.exists() and any(json.loads(line).get("event") == "request_started"
                               for line in ledger.read_text().splitlines()):
        return "EXECUTED_TECHNICAL_STOP_NO_RECEIPT"
    return "SETUP_FAILURE"


def dispatch():
    validate()
    require_clear()
    path = ROOT / "reports/continuation-state-c2.json"
    if path.exists():
        raise ValueError("C2 already started; never restart or overwrite")
    for row in ROSTER:
        unstarted(*row)
    state = {"schema": "table4-continuation-state-c2", "paper_data": False,
             "status": "RUNNING", "started": time.time(), "done": [], "software_withheld": 4}
    save(path, state)
    for host, arm, task in ROSTER:
        try:
            require_host(host)
            require_clear()
            unstarted(host, arm, task)
        except (RuntimeError, ValueError) as exc:
            state.update(status="PAUSED", reason=str(exc), ended=time.time())
            save(path, state)
            return
        runtime, script = ("rtlfixer", "run_fixer.py") if host == "rtlfixer" else ("modern", "run_scout.py")
        logpath = ROOT / "reports" / (host + "-" + task + "-" + arm + "-c2.log")
        with logpath.open("x") as log:
            process = subprocess.Popen([str(ROOT / "envs" / runtime / "bin/python"), "-u",
                str(ROOT / "adapters" / script), "--arm", arm, "--task", task, "--continuation-c2"],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            state["running"] = {"host": host, "arm": arm, "task": task, "pid": process.pid}
            save(path, state)
            rc = process.wait()
        work = ROOT / "hosts" / host / arm / "episodes" / (task + "__c2")
        status = terminal_status(work, rc)
        state.pop("running", None)
        state["done"].append({"host": host, "arm": arm, "task": task,
                              "rc": rc, "status": status, "log": str(logpath.relative_to(ROOT))})
        if rc or status not in {"ACCEPTED", "NOT_ACCEPTED"}:
            state.update(status="PAUSED", reason="technical/accounting stop", ended=time.time())
            save(path, state)
            return
        save(path, state)
    state.update(status="EDA_COMPLETED_SOFTWARE_WITHHELD", ended=time.time())
    save(path, state)


if __name__ == "__main__":
    dispatch()
