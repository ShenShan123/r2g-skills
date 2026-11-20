#!/usr/bin/env python3
"""One design through the native-harness arm: the model inside its vendor's own agent harness.

Same starting state, budgets, EDA actions and grading as Experiment 3's model-only arm
(experiments/bare_agent, imported from the frozen runtime given by --repo); only the agent
loop differs. The harness (DeepSeek Harness, `dsh --profile headless`) keeps its own system
prompt, file and shell tools, sandbox, subagents and context compaction. It gets:

  * the task (same content as the model-only arm's task, with the tool names adapted);
  * the project directory as its workspace (its own sandbox: workspace-write);
  * run_flow / run_signoff as MCP tools (eda_mcp.py, outside the sandbox, budgeted);
  * the model through a per-attempt accounting proxy (acct_proxy.py) and the relay, so the
    provider key never enters the sandbox and every billed call is counted.

An outer bubblewrap hides the R2G trees, the other experiments and every credential from the
harness (read-only toolchain and ORFS stay visible, as for the model-only arm).

Grading is the model-only arm's verdict(), plus an integrity rule: a report counts only if the
digests recorded by the MCP server when it produced it still match; otherwise the harness runs
signoff itself on the final layout. A final layout that differs from what run_flow produced
makes the attempt `tampered`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
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

CAPS = {"model_calls": 100, "cost_cny": 5.0, "run_flow": 10, "run_signoff": 16, "wall_seconds": 24 * 3600}

TASK = """设计：顶层模块 {top}，平台 {platform}。
工程目录（你的工作区）：{project}
- rtl/：原始 RTL；rtl/design.json 给出顶层、源文件清单和 include 目录（相对 rtl/）
- constraints/config.mk、constraints/constraint.sdc：基线配置（与历史基线完全相同的起点）
- ORFS（只读，可查阅）：{orfs}/flow/scripts、{orfs}/flow/platforms/{platform}

目标：最终版图全套 DRC 干净且 LVS 一致。
工具：用你自己的文件与 shell 工具读写文件；运行流程和签核只能通过 MCP 服务器 eda 的两个工具：
- run_flow(from_stage)：从某一阶段（synth/floorplan/place/cts/route/finish）运行 ORFS 到 finish，日志在 backend/<run>/
- run_signoff()：对最新版图运行全套 DRC + LVS（平台官方规则），报告在 reports/drc.json、reports/lvs.json，日志在 drc/、lvs/
必须遵守：
- 保持原设计功能：不得删除或简化逻辑、缩小存储器、把输出接常量、替换为空模块；只允许不改变行为的源码修正（如文件清单、include/路径问题）。
- 不得修改时钟周期和 constraint.sdc，不得修改 PDK、平台文件或签核规则，不得改写 backend/、reports/ 下工具生成的结果。
- 可以修改 config.mk 中的 ORFS 变量（面积/利用率/布局布线选项等）、添加由 config.mk 引用的 Tcl hook（放在 hooks/ 下）。
预算：最多 {run_flow} 次 run_flow、{run_signoff} 次 run_signoff、{model_calls} 次模型调用。
先运行流程和签核了解现状；完成或判断无法修复时，停止调用工具并说明结果与最终 DRC/LVS 状态。"""


def bwrap_cmd(project: Path, home: Path, keep_ro: list[str], hide: list[str]) -> list[str]:
    """Outer sandbox: read-only host, hidden R2G/experiment trees, writable project + harness home."""
    cmd = ["bwrap", "--die-with-parent", "--unshare-pid", "--ro-bind", "/", "/", "--dev", "/dev",
           "--proc", "/proc", "--tmpfs", "/tmp"]
    for h in hide:
        if Path(h).exists():
            cmd += ["--tmpfs", h]
    for p in keep_ro:
        if Path(p).exists():
            cmd += ["--ro-bind", p, p]
    cmd += ["--bind", str(project), str(project), "--bind", str(home), str(home), "--chdir", str(project)]
    return cmd


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True, help="frozen runtime of the model-only arm")
    ap.add_argument("--relay", default="http://127.0.0.1:18776", help="Messages relay (key stays on the relay host)")
    ap.add_argument("--node-dir", type=Path, required=True)
    ap.add_argument("--dsh", type=Path, required=True, help="dsh executable (node_modules/.bin/dsh)")
    ap.add_argument("--keep-ro", default="", help="colon-separated paths visible read-only inside the sandbox")
    ap.add_argument("--hide", default="/data/yangao:/home/yangao:/data2", help="colon-separated paths hidden")
    ap.add_argument("--caps", default="")
    a = ap.parse_args()
    caps = {**CAPS, **(json.loads(a.caps) if a.caps else {})}
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "result.json").exists():
        print("already done:", out)
        return 0
    sys.path.insert(0, str(a.repo / "experiments/bare_agent"))
    import run_bare as RB                     # the model-only arm's own prepare/verdict/audit code
    from bare_tools import BareTools

    case = json.loads(a.case.read_text())
    platform = case["platform"]
    name = case["id"] + "_native"
    project = out / name
    receipt = RB.prepare(case, a.baseline, project)
    orfs = Path(os.environ.get("ORFS_ROOT", "/home/yangao/r2g_toolchain/OpenROAD-flow-scripts"))
    platform_dir = orfs / "flow/platforms" / platform
    deck_hash = RB.tree_hash(platform_dir)
    job = "native-dsh-" + case["id"] + "-" + uuid.uuid4().hex[:6]
    journal = out / "orfs_audit.jsonl"
    tools = BareTools(project, platform, a.repo, orfs, RB.audit_env(job, name, project, journal, a.repo))
    marker = out / RB.MEMORY_MARKER
    service = eda_mcp.EdaService(tools, caps, out / "mcp_state.json", marker.is_file)
    mcp = eda_mcp.make_server(service)
    ledger = acct_proxy.Ledger(caps)
    proxy = acct_proxy.make_proxy(a.relay, ledger, "anthropic")
    for srv in (mcp, proxy):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    mcp_url = "http://127.0.0.1:%d/mcp" % mcp.server_address[1]
    proxy_url = "http://127.0.0.1:%d" % proxy.server_address[1]

    home = out / "dsh_home"
    (home / "profiles/headless").mkdir(parents=True, exist_ok=True)
    # Only addition to the shipped headless profile: the eda MCP server.
    (home / "profiles/headless/cordis.patch.yml").write_text(
        "- insert:\n"
        "    - id: mcp-eda\n"
        "      name: '@deepseek-ai/dsh-mcp-client'\n"
        "      config:\n"
        "        serverName: eda\n"
        "        transport: streamable-http\n"
        "        url: %s\n"
        "        toolCallTimeoutMs: %d\n"
        "        failOnStartupError: true\n" % (mcp_url, (caps["wall_seconds"] + 3600) * 1000))
    task = TASK.format(top=case["top"], platform=platform, project=project, orfs=orfs, **caps)
    (out / "task.txt").write_text(task)
    env = {"PATH": "%s:/usr/bin:/bin" % (a.node_dir / "bin"), "HOME": str(home), "DSH_HOME": str(home),
           "DEEPSEEK_API_KEY": "relay-dummy-not-a-provider-key", "DEEPSEEK_BASE_URL": proxy_url,
           "LANG": "C.UTF-8", "NO_COLOR": "1"}
    # the whole harness install (node_modules/.bin/dsh is a symlink into it), not just the target
    keep = [str(a.node_dir), str(a.dsh.absolute().parents[2])] + [p for p in a.keep_ro.split(":") if p]
    cmd = bwrap_cmd(project, home, keep, [h for h in a.hide.split(":") if h]) + [
        str(a.dsh), "headless", "--permission-mode", "workspace-write", "--approval-policy", "never", "--json", "-"]
    (out / "launch.json").write_text(json.dumps({"cmd": cmd, "env_keys": sorted(env), "mcp": mcp_url,
                                                 "proxy": proxy_url, "relay": a.relay, "caps": caps}, indent=1))
    started = time.time()
    with open(out / "run.jsonl", "wb") as so, open(out / "dsh.stderr", "wb") as se:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=so, stderr=se, env=env, start_new_session=True)
        p.stdin.write(task.encode())
        p.stdin.close()
        stop = None
        while p.poll() is None:
            time.sleep(5)
            if marker.is_file():
                stop = "memory_bound"
            elif ledger.capped():
                stop = ledger.capped()
            elif time.time() - started >= caps["wall_seconds"]:
                stop = "cap_wall"
            if stop:
                ledger.stopped = stop
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
    final = None
    for line in (out / "run.jsonl").read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "final":
            final = e
    if stop is None:
        kind = ((final or {}).get("reason") or {}).get("kind")
        stop = "finished" if rc == 0 and kind == "completed" else "harness_%s" % (kind or "exit_%d" % rc)
    # an EDA call may still be running if the harness was stopped mid-call: wait for it to end
    with service.lock:
        agent_counts, agent_eda = dict(tools.counts), tools.eda_seconds

    # ---- integrity + independent verification
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
    calls = ledger.calls
    complete = bool(calls) and all(c.get("usage_reported") and c.get("stream_complete") for c in calls)
    totals = {k: (sum(c.get(k) or 0 for c in calls) if complete else None)
              for k in ("input_tokens", "uncached_input_tokens", "cache_read_input_tokens", "output_tokens", "total_tokens")}
    events = []
    for l in (out / "run.jsonl").read_text(errors="replace").splitlines():
        if l.startswith("{"):
            try:
                events.append(json.loads(l))
            except ValueError:          # the harness was stopped (budget cap) in the middle of writing a line
                pass
    result = {"case_id": case["id"], "platform": platform, "arm": "native_dsh", "harness": "deepseek-harness",
              "harness_version": subprocess.run([str(a.dsh), "--version"], capture_output=True, text=True,
                                                env=env).stdout.strip(),
              "model": "deepseek-flash", "job": job, "stop_reason": stop, "harness_exit": rc,
              "final_reason": (final or {}).get("reason"), **v, "caps": caps,
              "model_calls": len(calls), "usage_complete": complete, "provider_usage": totals,
              "failed_invocations": len(ledger.failed),
              "cost_cny_peak": round(sum(acct_proxy.call_cost_cny(c) for c in calls), 5),
              "tool_counts": agent_counts, "agent_wall_seconds": round(agent_wall, 1),
              "agent_eda_seconds": round(agent_eda, 1), "harness_signoff": harness_signoff,
              "reports_trusted": trusted, "tampered": tampered,
              "harness_tool_calls": sum(1 for e in events if e.get("type") == "tool_call"),
              "harness_steps": sum(1 for e in events if e.get("type") == "status" and e.get("phase") == "step_end"),
              "orfs_audit": RB.audit_counts(journal, job), "prepare": receipt, "runtime": str(a.repo)}
    (out / "usage.json").write_text(json.dumps({"calls": calls, "failed_invocations": ledger.failed}, indent=1))
    (out / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str))
    mcp.shutdown()
    proxy.shutdown()
    print(json.dumps({k: result[k] for k in ("case_id", "outcome", "stop_reason", "model_calls")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
