"""`r2g` command line.

  r2g config  [--provider P --model M --base-url U --key-env VAR --set-key]
  r2g check-model           one request with a tool: endpoint, key, tool calling
  r2g selftest              run the counter8 example end to end
  r2g run PATH|--example NAME ...   submit a job and follow it
  r2g run --batch DIR [--output graph|image]   every subfolder of DIR is a design
          [--period NS --util U --die UM --params FILE]   override r2g's measured defaults
  r2g params                 the default flow parameters and their ranges
  r2g status [JOB]
  r2g ui [--port N] [--host H]
  r2g chat                  terminal chat with the agent
  r2g mcp                   MCP server on stdio
  r2g clean [--older-than DAYS] [--yes]   list (or delete) finished jobs' workspaces
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import time

from . import __version__, config


def _runtime():
    from .server import STATE, init_runtime
    init_runtime()
    return STATE


def _follow(store, jid: str, quiet: bool = False) -> dict:
    """Print a job's events until it ends. quiet (batches): only each design's
    end and failures, plus a count line every minute."""
    after, last = 0, 0.0
    while True:
        for e in store.events(jid, after=after):
            after = e["id"]
            if quiet and not (e["kind"] == "fail" or e["stage"] in ("done", "job")):
                continue
            mark = {"ok": "✓", "fail": "✗", "warn": "!", "start": "·"}.get(e["kind"], " ")
            print("%s %s %-10s %s" % (time.strftime("%H:%M:%S", time.localtime(e["ts"])), mark,
                                      (e["design"] or "job")[:10], e["message"]), flush=True)
        j = store.job(jid)
        if quiet and time.time() - last > 60:
            c = {}
            for d in j["designs"]:
                c[d["status"]] = c.get(d["status"], 0) + 1
            print("%s   进度：通过 %d · 失败 %d · 运行 %d · 排队 %d / 共 %d" % (
                time.strftime("%H:%M:%S"), c.get("passed", 0), c.get("failed", 0), c.get("running", 0),
                c.get("pending", 0), len(j["designs"])), flush=True)
            last = time.time()
        if j["status"] not in ("running", "pending", "finalizing"):
            return j
        time.sleep(2)


def cmd_config(a) -> int:
    cfg = config.load()
    for k in ("provider", "model", "base_url"):
        v = getattr(a, k)
        if v is not None:
            setattr(cfg.model, k, v)
    if a.key_env is not None:
        cfg.model.api_key_env = a.key_env
    if a.workers:
        cfg.workers = a.workers
    if a.cores:
        cfg.cores_per_design = a.cores
    if a.no_ingest:
        cfg.ingest = False
    config.save(cfg)
    if a.set_key:
        config.store_api_key(cfg.model.provider, getpass.getpass("API key（不回显）: "))
    print(json.dumps(config.public_view(cfg)["model_resolved"], ensure_ascii=False, indent=1))
    print("配置文件：%s" % config.config_path())
    return 0


def cmd_check_model(a) -> int:
    _runtime()
    from .server import test_model
    r = test_model()
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r.get("ok") else 1


def cmd_run(a) -> int:
    st = _runtime()
    from . import tools
    params = {}
    if a.params:
        params.update(json.loads(open(a.params).read()))
    for k in ("period", "util", "die"):
        v = getattr(a, k)
        if v is not None:
            params[{"period": "period_ns", "util": "utilization", "die": "die_side_um"}[k]] = v
    if a.batch:
        r = tools.start_batch(a.batch, output=a.output, platform=a.platform, params=params or None)
        if "error" in r:
            print("错误：", r["error"])
            return 2
        print("批量任务 %s：提交 %d 个设计，跳过 %d 个" % (r["job_id"], r["submitted"], r["skipped"]))
        for d in r["skipped_designs"]:
            print("  跳过 %-30s %s" % (d["name"], d["problem"]))
        j = _follow(tools.RT.store, r["job_id"], quiet=True)
        print(json.dumps(j.get("result"), ensure_ascii=False))
        return 0 if (j.get("result") or {}).get("passed") else 1
    designs = [{"example": e} for e in (a.example or [])] + [{"path": p} for p in (a.paths or [])]
    r = tools.start_job(designs, platform=a.platform, output=a.output, params=params or None)
    if "error" in r:
        print("错误：", r["error"])
        return 2
    print("任务 %s 已提交" % r["job_id"])
    j = _follow(tools.RT.store, r["job_id"])
    print(json.dumps(j.get("result"), ensure_ascii=False))
    return 0 if (j.get("result") or {}).get("passed") else 1


def cmd_params(a) -> int:
    from . import profile
    d = profile.describe(a.platform)
    print("默认流程参数（%s）—— 来源：%s" % (a.platform, d["source"]))
    for k, v in d["defaults"].items():
        f = d["fields"].get(k)
        print("  %-24s %-28s %s" % (k, json.dumps(v), (f["label"] + "：" + f["help"]) if f else "时钟分档 [单元数上限, 周期ns]"))
    return 0


def cmd_selftest(a) -> int:
    print("自检：用内置示例 counter8 跑完整流程（综合 → 布局布线 → 签核 → 转图 → 校验），约 3–5 分钟")
    # Reuse run's parser so its complete defaults stay in sync. A selftest
    # Namespace only has platform, and cannot be passed directly to cmd_run.
    return main(["run", "--example", "counter8", "--platform", a.platform])


def cmd_status(a) -> int:
    _runtime()
    from . import tools
    if a.job:
        print(json.dumps(tools.job_status(a.job), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(tools.list_jobs(20), ensure_ascii=False, indent=1))
    return 0


def cmd_clean(a) -> int:
    """Finished jobs' workspaces only; never a running job, never the packaged dataset."""
    import shutil
    from . import housekeeping
    from .jobs import JobStore
    store = JobStore()
    rep = housekeeping.disk_report(store)
    rows = housekeeping.clean_candidates(store, a.older_than)
    gb = lambda b: b / 1e9
    print("工作目录合计 %.2f GB，数据集包 %.2f GB，磁盘剩余 %.1f GB"
          % (gb(rep["workspace_bytes"]), gb(rep["datasets_bytes"]), gb(rep["free_bytes"])))
    if not rows:
        print("没有超过 %g 天的已结束任务可清理" % a.older_than)
        return 0
    for r in rows:
        print("  %s  %-9s  %8.2f GB" % (r["job_id"], r["status"], gb(r["bytes"])))
    total = sum(r["bytes"] for r in rows)
    if not a.yes:
        print("以上 %d 个任务共 %.2f GB。加 --yes 才会删除（数据集包保留）。" % (len(rows), gb(total)))
        return 0
    for r in rows:
        shutil.rmtree(r["path"], ignore_errors=True)
        store.event(r["job_id"], None, "job", "info", "工作目录已清理（数据集包保留）")
    print("已删除 %d 个任务的工作目录，释放 %.2f GB" % (len(rows), gb(total)))
    return 0


def cmd_ui(a) -> int:
    import uvicorn
    cfg = config.load()
    host, port = a.host or cfg.host, a.port or cfg.port
    print("r2g 界面：http://%s:%d   （远程机器请用 ssh -L %d:127.0.0.1:%d 转发）" % (host, port, port, port))
    uvicorn.run("r2g_agent.server:app", host=host, port=port, log_level="warning")
    return 0


def cmd_chat(a) -> int:
    st = _runtime()
    from .agent_loop import run_turn
    from .llm import make_adapter
    adapter = make_adapter(st["cfg"].model)
    sid = st["sessions"].create()
    print("r2g 对话（Ctrl-D 退出）")
    while True:
        try:
            text = input("\n你> ").strip()
        except EOFError:
            return 0
        if not text:
            continue
        print("r2g> ", end="", flush=True)
        for ev in run_turn(adapter, st["sessions"], sid, text):
            if ev["type"] == "text":
                print(ev["delta"], end="", flush=True)
            elif ev["type"] == "tool_call":
                print("\n  [调用 %s %s]" % (ev["name"], json.dumps(ev["arguments"], ensure_ascii=False)[:120]), flush=True)
            elif ev["type"] == "error":
                print("\n  [错误] %s" % ev["message"])
        print()


def cmd_mcp(a) -> int:
    import anyio
    _runtime()
    from .mcp_server import serve
    anyio.run(serve)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="r2g", description="r2g agent %s" % __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("config", help="设置模型与算力")
    c.add_argument("--provider"); c.add_argument("--model"); c.add_argument("--base-url")
    c.add_argument("--key-env"); c.add_argument("--set-key", action="store_true")
    c.add_argument("--workers", type=int); c.add_argument("--cores", type=int)
    c.add_argument("--no-ingest", action="store_true", help="不把运行写入知识库（开发用）")
    c.set_defaults(fn=cmd_config)
    sub.add_parser("check-model", help="测试模型连通与工具调用").set_defaults(fn=cmd_check_model)
    s = sub.add_parser("selftest", help="用内置示例跑一遍完整流程")
    s.add_argument("--platform", default="nangate45"); s.set_defaults(fn=cmd_selftest)
    r = sub.add_parser("run", help="提交任务并跟踪")
    r.add_argument("paths", nargs="*"); r.add_argument("--example", action="append")
    r.add_argument("--platform", default="nangate45")
    r.add_argument("--batch", metavar="DIR", help="DIR 下每个子文件夹是一个设计，一次提交")
    r.add_argument("--output", choices=["graph", "image"], default="graph")
    r.add_argument("--period", type=float, help="固定时钟周期 ns（默认按单元数分档）")
    r.add_argument("--util", type=float, help="利用率，默认 0.50")
    r.add_argument("--die", type=float, help="固定 die 边长 µm（默认自动）")
    r.add_argument("--params", help="参数 JSON 文件（键见 r2g params）"); r.set_defaults(fn=cmd_run)
    pp = sub.add_parser("params", help="显示默认流程参数（经验值）及可改范围")
    pp.add_argument("--platform", default="nangate45"); pp.set_defaults(fn=cmd_params)
    t = sub.add_parser("status"); t.add_argument("job", nargs="?"); t.set_defaults(fn=cmd_status)
    u = sub.add_parser("ui", help="启动本地网页界面")
    u.add_argument("--port", type=int); u.add_argument("--host"); u.set_defaults(fn=cmd_ui)
    sub.add_parser("chat", help="终端对话").set_defaults(fn=cmd_chat)
    sub.add_parser("mcp", help="以 MCP 服务器方式运行（stdio）").set_defaults(fn=cmd_mcp)
    k = sub.add_parser("clean", help="清理已结束任务的工作目录（默认只列出）")
    k.add_argument("--older-than", type=float, default=7.0, help="天数，默认 7")
    k.add_argument("--yes", action="store_true", help="确认删除")
    k.set_defaults(fn=cmd_clean)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
