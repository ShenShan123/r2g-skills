"""Exact remaining roster and immutable runtime checks for continuation C1."""
import hashlib
import json
from pathlib import Path

from bridge import ROOT

PATH = ROOT / "inputs/continuation-runtime-c1.json"
ROSTER = [
    (host, arm, task) for host, tasks in [
        ("rtlfixer", ["2012_q2b", "2012_q2fsm"]),
        ("rtlscout", ["fifo_sync4"]),
        ("mini-swe-agent", ["astropy__astropy-12907", "astropy__astropy-13033"])]
    for task in tasks for arm in ["native", "tehm"]]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unstarted(host, arm, task, root=ROOT):
    if (host, arm, task) not in ROSTER:
        raise ValueError("not an authorised never-started episode")
    directory = Path(root) / "hosts" / host / arm / "episodes"
    if any(p.is_dir() and p.name.startswith(task + "__") for p in directory.glob("*")):
        raise ValueError("episode already allocated; no automatic rerun")


def validate():
    data = json.loads(PATH.read_text())
    for name, expected in data["files"].items():
        path = ROOT / name
        if not path.is_file() or sha(path) != expected:
            raise ValueError("continuation runtime changed: " + name)
    return data


def prepare():
    if PATH.exists():
        raise ValueError("continuation is already frozen")
    files = {p for base in [ROOT / "adapters", ROOT / "code/memory", ROOT / "sources/rtlscout",
                           ROOT / "sources/rtlfixer/src", ROOT / "sources/verilog-eval/verilog_eval"]
             for p in base.rglob("*.py") if "__pycache__" not in p.parts}
    for base in [ROOT / "sources/rtlscout/benchmarks/fifo_sync4"]:
        files.update(p for p in base.rglob("*") if p.is_file())
    files.update(ROOT / name for name in ["inputs/first-batch.json", "inputs/empty.env",
        "inputs/rtlfixer-first2.json", "inputs/continuation-authorization-20261026.json",
        "reports/continuation-qualification.json",
        "sources/rtlfixer/src/benchmarks/verilogeval-syntax.jsonl"])
    data = {"schema": "table4-continuation-c1", "paper_data": False,
        "roster": [dict(zip(["host", "arm", "task"], item)) for item in ROSTER],
        "files": {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}}
    PATH.write_text(json.dumps(data, indent=2, sort_keys=True)+"\n")
    return data


def require_host(host):
    data = validate()
    qualification = json.loads((ROOT / "reports/continuation-qualification.json").read_text())
    if qualification["hosts"][host]["launch_allowed"] is not True:
        raise ValueError("host is not qualified for live integration: " + host)
    return data


if __name__ == "__main__":
    print(json.dumps({"files": len(prepare()["files"])}))
