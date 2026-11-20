#!/usr/bin/env python3
"""One design through the native-harness arm for GPT: gpt-6.1-sol inside OpenAI's own agent harness, Codex CLI.

Same starting state, budgets, EDA actions (run_flow / run_signoff over MCP, eda_mcp.py), task text and grading as the
DeepSeek Harness arm (run_native.py); only the harness differs. Codex keeps its own agent loop, prompt, shell and
file tools, sandbox and context management. Differences forced by the harness, all recorded in result.json:

  * Credentials: Codex is signed in with a ChatGPT plan (device login into a private CODEX_HOME). Its login file is
    unreadable to the agent: the permission profile in codex_home/config.toml denies that directory to the agent's
    commands (the Codex process itself still reads it), and the outer bubblewrap hides /data/yangao and /home/yangao.
  * Model traffic goes from Codex straight to the ChatGPT backend through the local proxy tunnel; there is no accounting
    proxy. Per-call usage comes from Codex's own session log (rollout-*.jsonl, one token_count event per model
    request); budgets (model calls, cost at the same price table as the model-only arm, wall) are enforced by polling
    that log and stopping Codex. The plan's rate-limit window (used_percent) is recorded at start and end.
  * Cost is NOTIONAL: a subscription has no per-call bill, so cost uses the Experiment 3 price table.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import acct_proxy  # noqa: E402
import eda_mcp     # noqa: E402
from run_native import CAPS, TASK, bwrap_cmd  # noqa: E402


def rollout_calls(path: Path):
    """Per-call usage records and the plan rate-limit snapshots from a Codex rollout file."""
    calls, rates, last_total = [], [], -1
    if not path or not path.is_file():
        return calls, rates
    for line in path.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        p = e.get("payload") or {}
        if p.get("type") != "token_count":
            continue
        if p.get("rate_limits"):
            rates.append(p["rate_limits"])
        info = p.get("info") or {}
        tot = (info.get("total_token_usage") or {}).get("total_tokens")
        last = info.get("last_token_usage")
        if not last or tot is None or tot == last_total:
            continue
        last_total = tot
        cached = last.get("cached_input_tokens") or 0
        inp = last.get("input_tokens") or 0
        calls.append({"input_tokens": inp, "cache_read_input_tokens": cached,
                      "uncached_input_tokens": max(inp - cached, 0), "output_tokens": last.get("output_tokens") or 0,
                      "reasoning_tokens": last.get("reasoning_output_tokens") or 0,
                      "total_tokens": last.get("total_tokens") or (inp + (last.get("output_tokens") or 0)),
                      "usage_reported": True, "stream_complete": True, "call_index": len(calls) + 1,
                      "at": e.get("timestamp")})
    return calls, rates


def find_rollout(codex_home: Path, thread_id: str | None):
    if not thread_id:
        return None
    hits = glob.glob(str(codex_home / "sessions" / "**" / ("rollout-*-%s.jsonl" % thread_id)), recursive=True)
    return Path(hits[0]) if hits else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True, help="frozen runtime of the model-only arm")
    ap.add_argument("--node-dir", type=Path, required=True)
    ap.add_argument("--codex", type=Path, required=True, help="codex executable (node_modules/.bin/codex)")
    ap.add_argument("--codex-home", type=Path, required=True)
    ap.add_argument("--proxy", default="http://127.0.0.1:12080", help="HTTPS proxy to reach the ChatGPT backend")
    ap.add_argument("--model", default="gpt-6.1-sol")
    ap.add_argument("--keep-ro", default="")
    ap.add_argument("--hide", default="/data/yangao:/home/yangao:/data2")
    ap.add_argument("--caps", default="")
    a = ap.parse_args()
    caps = {**CAPS, **(json.loads(a.caps) if a.caps else {})}
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "result.json").exists():
        print("already done:", out)
        return 0
    sys.path.insert(0, str(a.repo / "experiments/bare_agent"))
    import run_bare as RB
    from bare_tools import BareTools

    case = json.loads(a.case.read_text())
    platform = case["platform"]
    name = case["id"] + "_native"
    project = out / name
    receipt = RB.prepare(case, a.baseline, project)
    orfs = Path(os.environ.get("ORFS_ROOT", "/home/yangao/r2g_toolchain/OpenROAD-flow-scripts"))
    platform_dir = orfs / "flow/platforms" / platform
    deck_hash = RB.tree_hash(platform_dir)
    job = "native-codex-" + case["id"] + "-" + uuid.uuid4().hex[:6]
    journal = out / "orfs_audit.jsonl"
    tools = BareTools(project, platform, a.repo, orfs, RB.audit_env(job, name, project, journal, a.repo))
    marker = out / RB.MEMORY_MARKER
    service = eda_mcp.EdaService(tools, caps, out / "mcp_state.json", marker.is_file)
    mcp = eda_mcp.make_server(service)
    threading.Thread(target=mcp.serve_forever, daemon=True).start()
    mcp_url = "http://127.0.0.1:%d/mcp" % mcp.server_address[1]

    task = TASK.format(top=case["top"], platform=platform, project=project, orfs=orfs, **caps)
    (out / "task.txt").write_text(task)
    env = {"PATH": "%s:/usr/bin:/bin" % (a.node_dir / "bin"), "HOME": str(a.codex_home), "CODEX_HOME": str(a.codex_home),
           "HTTPS_PROXY": a.proxy, "HTTP_PROXY": a.proxy, "NO_PROXY": "localhost,127.0.0.1,::1", "LANG": "C.UTF-8",
           "NO_COLOR": "1"}
    keep = [str(a.node_dir), str(a.codex.absolute().parents[2])] + [p for p in a.keep_ro.split(":") if p]
    cmd = bwrap_cmd(project, a.codex_home, keep, [h for h in a.hide.split(":") if h]) + [
        str(a.codex), "exec", "--json", "--skip-git-repo-check", "-m", a.model, "-C", str(project),
        "-c", 'approval_policy="never"',
        "-c", 'mcp_servers.eda.url="%s"' % mcp_url,
        "-c", "mcp_servers.eda.tool_timeout_sec=%d" % (caps["wall_seconds"] + 3600),
        "-c", "mcp_servers.eda.startup_timeout_sec=60",
        # the only MCP server is our own EDA server; without this, approval_policy=never refuses its tools
        "-c", 'mcp_servers.eda.default_tools_approval_mode="approve"', "-"]
    (out / "launch.json").write_text(json.dumps({"cmd": cmd, "env_keys": sorted(env), "mcp": mcp_url, "caps": caps,
                                                 "model": a.model}, indent=1))
    started = time.time()
    thread_id, rollout, stop = None, None, None
    with open(out / "run.jsonl", "wb") as so, open(out / "codex.stderr", "wb") as se:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=so, stderr=se, env=env, start_new_session=True)
        p.stdin.write(task.encode())
        p.stdin.close()
        while p.poll() is None:
            time.sleep(5)
            if thread_id is None:
                for line in (out / "run.jsonl").read_text(errors="replace").splitlines()[:5]:
                    if '"thread.started"' in line:
                        thread_id = json.loads(line).get("thread_id")
            if rollout is None:
                rollout = find_rollout(a.codex_home, thread_id)
            calls, _ = rollout_calls(rollout)
            if marker.is_file():
                stop = "memory_bound"
            elif len(calls) >= caps["model_calls"]:
                stop = "cap_model_calls"
            elif sum(acct_proxy.call_cost_cny(c) for c in calls) >= caps["cost_cny"]:
                stop = "cap_cost"
            elif time.time() - started >= caps["wall_seconds"]:
                stop = "cap_wall"
            if stop:
                for sig, wait in ((signal.SIGTERM, 30), (signal.SIGKILL, 10)):
                    try:
                        os.killpg(p.pid, sig)
                    except ProcessLookupError:
                        break
                    try:
                        p.wait(timeout=wait)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                break
        rc = p.wait()
    agent_wall = time.time() - started
    events = []
    for line in (out / "run.jsonl").read_text(errors="replace").splitlines():
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    if thread_id is None:
        thread_id = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
    rollout = rollout or find_rollout(a.codex_home, thread_id)
    calls, rates = rollout_calls(rollout)
    if rollout and rollout.is_file():
        (out / "rollout.jsonl").write_bytes(rollout.read_bytes())
    failed = [e for e in events if e.get("type") in ("turn.failed", "error")]
    if stop is None:
        stop = "finished" if rc == 0 and not failed else ("harness_error" if failed else "harness_exit_%d" % rc)
    with service.lock:
        agent_counts, agent_eda = dict(tools.counts), tools.eda_seconds

    run = tools._latest_run()
    gdsf = service._gds(run) if run else None
    gds_now = eda_mcp.sha(gdsf) if gdsf else None
    produced = service.flow_gds.get(run.name) if run else None
    tampered = bool(run and (run.name not in service.flow_gds or gds_now != produced))
    rep = project / "reports"
    ls = service.last_signoff or {}
    trusted = bool(run and ls.get("run") == run.name and ls.get("gds") == gds_now and gds_now
                   and ls.get("drc") == eda_mcp.sha(rep / "drc.json") and ls.get("lvs") == eda_mcp.sha(rep / "lvs.json"))
    harness_signoff = False
    if stop != "memory_bound" and not tampered and run and gds_now and not trusted:
        tools.audit_env = {**tools.audit_env, "R2G_ORFS_AUDIT_JOB": job + "-verify"}
        tools.run_signoff()
        harness_signoff = True
    v = RB.verdict(tools, project, receipt, platform_dir, deck_hash)
    if stop == "memory_bound":
        v["outcome"] = "memory_bound"
        v["memory_bound"] = json.loads(marker.read_text() or "{}")
    elif tampered:
        v["outcome"] = "tampered"
    complete = bool(calls)
    totals = {k: (sum(c.get(k) or 0 for c in calls) if complete else None)
              for k in ("input_tokens", "uncached_input_tokens", "cache_read_input_tokens", "output_tokens",
                        "reasoning_tokens", "total_tokens")}
    version = subprocess.run([str(a.codex), "--version"], capture_output=True, text=True, env=env).stdout.strip()
    result = {"case_id": case["id"], "platform": platform, "arm": "native_codex", "harness": "codex-cli",
              "harness_version": version, "model": a.model, "job": job, "stop_reason": stop, "harness_exit": rc,
              "thread_id": thread_id, "final_failed": bool(failed), **v, "caps": caps,
              "model_calls": len(calls), "usage_complete": complete, "provider_usage": totals,
              "cost_cny_peak": round(sum(acct_proxy.call_cost_cny(c) for c in calls), 5),
              "cost_note": "notional: ChatGPT plan, priced with the Experiment 3 table",
              "plan_used_percent_start": (rates[0].get("primary") or {}).get("used_percent") if rates else None,
              "plan_used_percent_end": (rates[-1].get("primary") or {}).get("used_percent") if rates else None,
              "plan_window_minutes": (rates[-1].get("primary") or {}).get("window_minutes") if rates else None,
              "tool_counts": agent_counts, "agent_wall_seconds": round(agent_wall, 1),
              "agent_eda_seconds": round(agent_eda, 1), "harness_signoff": harness_signoff,
              "reports_trusted": trusted, "tampered": tampered,
              "harness_items": sum(1 for e in events if e.get("type") == "item.completed"),
              "orfs_audit": RB.audit_counts(journal, job), "prepare": receipt, "runtime": str(a.repo)}
    (out / "usage.json").write_text(json.dumps({"calls": calls, "rate_limits_first_last": [rates[:1], rates[-1:]]}, indent=1))
    (out / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str))
    mcp.shutdown()
    print(json.dumps({k: result[k] for k in ("case_id", "outcome", "stop_reason", "model_calls", "plan_used_percent_start",
                                             "plan_used_percent_end")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
