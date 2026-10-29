"""Pinned RTLScout native/TEHM integration episodes (not paper results)."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

from bridge import ROOT, FullMemory, stable
from hooks import HostObserver, attach_rtlscout
from meter import MeteredProvider
from native_evidence import ScoutEvidence
from provider_clients import ScoutClient, flash_key
from tool_sandbox import command as sandbox_command

sys.path.insert(0, str(ROOT / "sources/rtlscout"))


def sandbox_native_simulation():
    import rtlscout.correctness as C
    original = C._run
    def run(args, cwd, timeout=30):
        return original(sandbox_command(args[0], args[1:], cwd), cwd, timeout=timeout)
    C._run = run


def run(arm, task, *, fake=False, continuation=False, continuation_c2=False, continuation_c3=False):
    if not fake:
        if continuation_c3:
            from continuation_c3 import require_host, unstarted
            from phase_gate import require_clear
            require_host("rtlscout")
            require_clear()
            unstarted("rtlscout", arm, task)
        elif continuation_c2:
            from continuation_c2 import require_host, unstarted
            from phase_gate import require_clear
            require_host("rtlscout")
            require_clear()
            unstarted("rtlscout", arm, task)
        elif continuation:
            from continuation import require_host, unstarted
            from phase_gate import require_clear
            require_host("rtlscout")
            require_clear()
            unstarted("rtlscout", arm, task)
        else:
            from freeze import validate
            validate()
    policy = json.loads((ROOT / "inputs/first-batch.json").read_text())
    if task not in policy["rtlscout"]["tasks"]:
        raise ValueError("task not registered")
    if not fake and not policy["paid_launch_authorized"]:
        raise ValueError("paid launch not authorized")
    key = None if fake else flash_key()  # fail before allocating any episode
    # Host-local caches and native temp outputs never use another experiment.
    os.environ["RTLSCOUT_CACHE_DIR"] = str(ROOT / "hosts/rtlscout" / arm / "cache")
    os.environ["RTLSCOUT_ENV_FILE"] = str(ROOT / "inputs/empty.env")
    os.environ["PATH"] = str(ROOT / "bin") + ":/usr/bin:/bin"
    from rtlscout.agent import RTLAgent
    from rtlscout.benchmarks import load_benchmark
    from rtlscout.runner import provision_workspace
    sandbox_native_simulation()
    runtime_root = ROOT / "reports/native-smoke" if fake else ROOT
    memory = FullMemory("rtlscout", arm=arm, root=runtime_root)
    revision = "c3" if continuation_c3 else "c2" if continuation_c2 else "c1" if continuation else "r2"
    work = memory.arm_root / "episodes" / (task + "__" + revision)
    work.mkdir(exist_ok=False)
    bench = load_benchmark(ROOT / "sources/rtlscout/benchmarks" / task)
    workspace, reference = provision_workspace(bench, work, language="verilog", run_cec=True)
    scratch = workspace / ".tmp"
    scratch.mkdir()
    tempfile.tempdir = str(scratch)
    os.environ["TMPDIR"] = str(scratch)
    os.chdir(workspace)
    if fake:
        from rtlscout.fake_provider import build_fake_client
        client = build_fake_client("simple_adder_pass")
        provider = None
    else:
        provider = MeteredProvider(work / "provider", key)
        client = ScoutClient(provider)
    agent = RTLAgent(client, workdir=work, max_steps=3 if fake else 20,
                     language="verilog", run_cec=True, cec_reference=reference)
    agent.design_top_module = bench.module_name
    evidence = ScoutEvidence(memory, agent, task)
    observer = HostObserver(memory, evidence.state, evidence.verify)
    attach_rtlscout(agent, observer)
    from local_reuse import ScoutReuse
    observer.direct_driver = ScoutReuse(agent, observer, evidence)
    start, cpu = time.time(), time.process_time()
    record = {"host": "rtlscout", "arm": arm, "task": task, "paper_data": False,
              "phase": "offline_native_integration" if fake else "paid_native_integration",
              "seed": "empty_private_store_then_online_write_back", "max_steps": agent.max_steps,
              "success_definition": "native_correct_design_found; not an optimization-gain claim",
              "started": start}
    record["runtime_revision"] = "continuation-" + revision if continuation or continuation_c2 or continuation_c3 else "r2"
    try:
        result = agent.run(bench.description, task)
        record.update(status="ACCEPTED" if result.passed else "NOT_ACCEPTED",
                      native_result=result.to_dict())
    except Exception as exc:
        record.update(status="TECHNICAL_STOP", error=type(exc).__name__, traceback=traceback.format_exc())
    finally:
        if provider and provider.blocked:
            record["status"] = "ACCOUNTING_FAILURE"
        events = [] if fake else [json.loads(l) for l in provider.journal.path.read_text().splitlines()]
        ended = [e for e in events if e["event"] == "request_ended"]
        known = [e for e in ended if isinstance(e.get("usage"), dict)]
        record["cost"] = {"online_requests": len([e for e in events if e["event"] == "request_started"]),
            "known_tokens": sum(e["usage"]["total_tokens"] for e in known),
            "unknown_requests": sum(e.get("usage") == "UNKNOWN" for e in ended),
            "wall_s": time.time()-start, "cpu_process_s": time.process_time()-cpu}
        record["ended"] = time.time()
        (work / "episode.json").write_text(stable(record) + "\n")
        memory.close()
    print(stable({k: record[k] for k in ["host", "arm", "task", "status", "cost"]}))
    return 0 if record["status"] in {"ACCEPTED", "NOT_ACCEPTED"} else 2


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--arm", choices=["native", "tehm"], required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--fake", action="store_true")
    versions = p.add_mutually_exclusive_group()
    versions.add_argument("--continuation", action="store_true")
    versions.add_argument("--continuation-c2", action="store_true")
    versions.add_argument("--continuation-c3", action="store_true")
    a = p.parse_args()
    try:
        code = run(a.arm, a.task, fake=a.fake, continuation=a.continuation,
                   continuation_c2=a.continuation_c2, continuation_c3=a.continuation_c3)
    except Exception:
        failure = {"status": "SETUP_FAILURE", "host": "rtlscout", "arm": a.arm,
                   "task": a.task, "revision": "c3" if a.continuation_c3 else "c2" if a.continuation_c2 else "c1" if a.continuation else "r2", "paper_data": False,
                   "traceback": traceback.format_exc(), "time": time.time()}
        suffix = "c3" if a.continuation_c3 else "c2" if a.continuation_c2 else "c1" if a.continuation else "r2"
        (ROOT / "reports" / ("setup-" + a.task + "-" + a.arm + "-" + suffix + ".json")).write_text(stable(failure)+"\n")
        raise
    raise SystemExit(code)
