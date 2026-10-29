"""Prepared RTLFixer integration launcher. UNKNOWN gate precedes credentials."""
import argparse
import json
import os
import sys
import tempfile
import time
import traceback

from bridge import ROOT, FullMemory, stable
from fixer_runtime import FixerEvidence, FixerTransport, execute_native, first_two, make_agent
from meter import MeteredProvider
from phase_gate import require_clear
from provider_clients import flash_key


def run(arm, task, *, continuation=False, continuation_c2=False):
    require_clear()
    if continuation_c2:
        from continuation_c2 import require_host, unstarted
        require_host("rtlfixer")
        unstarted("rtlfixer", arm, task)
    elif continuation:
        from continuation import require_host, unstarted
        require_host("rtlfixer")
        unstarted("rtlfixer", arm, task)
    else:
        from freeze_fixer import validate
        validate()
    policy = json.loads((ROOT / "inputs/first-batch.json").read_text())
    if not policy["paid_launch_authorized"]:
        raise ValueError("paid launch not authorized")
    roster = json.loads((ROOT / "inputs/rtlfixer-first2.json").read_text())
    matches = [row for row in first_two() if row["task_id"] == task]
    if len(matches) != 1 or task not in roster["task_order"]:
        raise ValueError("task not registered")
    item = matches[0]["item"]
    key = flash_key()
    memory = FullMemory("rtlfixer", arm=arm)
    revision = "c2" if continuation_c2 else "c1" if continuation else "r1"
    work = memory.arm_root / "episodes" / (task + "__" + revision)
    work.mkdir(exist_ok=False)
    workspace = work / "workspace"
    workspace.mkdir()
    scratch = workspace / ".tmp"
    scratch.mkdir()
    (workspace / "error_log").mkdir()
    tempfile.tempdir = str(scratch)
    os.environ["TMPDIR"] = str(scratch)
    os.environ["PATH"] = str(ROOT / "bin") + ":/usr/bin:/bin"
    os.chdir(workspace)
    from executors.vg_executor import VerilogExecutor
    provider = MeteredProvider(work / "provider", key)
    evidence = FixerEvidence(memory, item)
    executor = evidence.bind(VerilogExecutor())
    record = {"host": "rtlfixer", "arm": arm, "task": task, "paper_data": False,
              "dataset": "native VerilogEval syntax subset, NOT RTLRepair",
              "variant": "native RAG and agent, symmetric Quartus-to-Icarus tool port",
              "native_max_iterations": 10, "started": time.time()}
    record["runtime_revision"] = "continuation-" + revision if continuation or continuation_c2 else "prepared-r1"
    started, cpu = time.monotonic(), time.process_time()
    try:
        agent = make_agent(executor, FixerTransport(provider, memory, evidence), item)
        result = execute_native(agent, executor, item, evidence=evidence)
        record.update(native_result=result,
            status="ACCEPTED" if result["functional_verification"]["passed"] else "NOT_ACCEPTED")
    except Exception as exc:
        record.update(status="TECHNICAL_STOP", error=type(exc).__name__, traceback=traceback.format_exc())
    finally:
        if provider.blocked:
            record["status"] = "ACCOUNTING_FAILURE"
        rows = [json.loads(line) for line in provider.journal.path.read_text().splitlines()] if provider.journal.path.exists() else []
        ended = [e for e in rows if e["event"] == "request_ended"]
        record["cost"] = {"online_requests": sum(e["event"] == "request_started" for e in rows),
            "known_tokens": sum(e["usage"]["total_tokens"] for e in ended if isinstance(e.get("usage"), dict)),
            "unknown_requests": sum(e.get("usage") == "UNKNOWN" for e in ended),
            "native_evaluations": evidence.counter, "wall_s": time.monotonic()-started,
            "cpu_process_s": time.process_time()-cpu, "billing_cost": None}
        record["ended"] = time.time()
        from native_serialization import write_episode
        write_episode(work, record)
        memory.close()
    return 0 if record["status"] in {"ACCEPTED", "NOT_ACCEPTED"} else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=["native", "tehm"], required=True)
    parser.add_argument("--task", required=True)
    versions = parser.add_mutually_exclusive_group()
    versions.add_argument("--continuation", action="store_true")
    versions.add_argument("--continuation-c2", action="store_true")
    args = parser.parse_args()
    sys.exit(run(args.arm, args.task, continuation=args.continuation, continuation_c2=args.continuation_c2))
