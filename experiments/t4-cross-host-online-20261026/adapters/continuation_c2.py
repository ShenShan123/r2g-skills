"""Exactly five EDA episodes, all never started. No software or old attempts."""
import hashlib
import json
from pathlib import Path
import time

from bridge import ROOT
from qualify_continuation import read_suite

PATH = ROOT / "inputs/continuation-runtime-c2.json"
ROSTER = [("rtlfixer", "tehm", "2012_q2b"),
          ("rtlfixer", "native", "2012_q2fsm"),
          ("rtlfixer", "tehm", "2012_q2fsm"),
          ("rtlscout", "native", "fifo_sync4"),
          ("rtlscout", "tehm", "fifo_sync4")]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unstarted(host, arm, task, root=ROOT):
    if (host, arm, task) not in ROSTER:
        raise ValueError("outside the five-episode EDA continuation")
    directory = Path(root) / "hosts" / host / arm / "episodes"
    if any(p.is_dir() and p.name.startswith(task + "__") for p in directory.glob("*")):
        raise ValueError("episode already allocated; never rerun")


def verify_files(root, files):
    root = Path(root).resolve()
    for name, expected in files.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or sha(path) != expected:
            raise ValueError("C2 frozen runtime changed: " + name)


def validate():
    data = json.loads(PATH.read_text())
    expected = [dict(zip(["host", "arm", "task"], item)) for item in ROSTER]
    if data["roster"] != expected:
        raise ValueError("C2 roster changed")
    verify_files(ROOT, data["files"])
    return data


def require_host(host):
    data = validate()
    if host not in {"rtlfixer", "rtlscout"}:
        raise ValueError("C2 does not launch software")
    return data


def prepare():
    if PATH.exists():
        raise ValueError("never overwrite an executed or prepared freeze")
    from phase_gate import require_clear
    require_clear()
    for episode in ROSTER:
        unstarted(*episode)
    modern = read_suite("modern-c2-tests.xml", [
        "test_c2_roster_is_exactly_five_and_excludes_every_started_or_software_task",
        "test_c2_authorization_preserves_unknown_and_does_not_waive_the_next_one",
        "test_scout_retains_exact_provider_reasoning_by_tool_call_id",
        "test_native_local_execution_and_verified_rollback_without_llm[False]"])
    fixer = read_suite("fixer-c2-tests.xml", [
        "test_legacy_langchain_round_trip_preserves_reasoning_without_tool_argument_pollution",
        "test_real_native_rag_agent_compiler_and_simulator_with_offline_transport",
        "test_scalar_terminal_survives_unknown_rich_trajectory_type"])
    files = {p for base in [ROOT / "adapters", ROOT / "code/memory", ROOT / "sources/rtlscout",
                           ROOT / "sources/rtlfixer/src", ROOT / "sources/verilog-eval/verilog_eval"]
             for p in base.rglob("*.py") if "__pycache__" not in p.parts}
    files.update(p for p in (ROOT / "sources/rtlscout/benchmarks/fifo_sync4").rglob("*") if p.is_file())
    files.update(ROOT / name for name in ["inputs/first-batch.json", "inputs/empty.env",
        "inputs/rtlfixer-first2.json", "inputs/continuation-authorization-20261026.json",
        "inputs/continuation-authorization-c2.json", "reports/modern-c2-tests.xml", "reports/fixer-c2-tests.xml",
        "sources/rtlfixer/src/benchmarks/verilogeval-syntax.jsonl"])
    data = {"schema": "table4-continuation-c2", "time": time.time(), "paper_data": False,
            "roster": [dict(zip(["host", "arm", "task"], item)) for item in ROSTER],
            "tests": {"modern": modern, "rtlfixer": fixer},
            "software_withheld": 4, "new_unknown_requires_decision": True,
            "files": {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}}
    PATH.write_text(json.dumps(data, indent=2, sort_keys=True)+"\n")
    return data


if __name__ == "__main__":
    print(json.dumps({"files": len(prepare()["files"])}))
