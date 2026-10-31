#!/usr/bin/env python3
"""One design through the bare-agent arm: same model, same EDA tools, no R2G orchestration.

Starting state is the historical frozen-spec baseline the R2G campaign began
from: the original RTL (hash-checked against the population manifest) plus the
baseline config.mk and constraint.sdc, file paths remapped into the project.
The model then works through bare_tools.py until it calls finish, stops
calling tools, or a budget runs out. The harness, not the model, decides the
outcome: full-deck DRC clean and LVS clean bound to one archived layout,
platform decks and SDC unchanged; any RTL edit routes the result to a
semantics review instead of an automatic pass.

Writes <out>/result.json, usage.json (per model call, provider counters),
transcript.json and the ORFS audit journal, with the same counters the R2G
measurement uses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bare_tools import SPECS, BareTools, ToolError  # noqa: E402

COUNTERS = ("input_tokens", "output_tokens", "total_tokens", "cache_read_input_tokens",
            "uncached_input_tokens", "cache_creation_input_tokens", "reasoning_tokens")
# Budget by work and money, not by raw tokens: a total-token cap counted the same
# re-sent context over and over (94% cache hits) and stopped harness v1 after one
# flow run. Tokens stay a reported metric. Cost uses deepseek-flash PEAK prices
# (CNY per 1M tokens: uncached input 2, cache-hit input 0.04, output 8).
DEFAULT_CAPS = {"model_calls": 100, "cost_cny": 5.0, "run_flow": 10, "run_signoff": 16,
                "wall_seconds": 24 * 3600}
PEAK_CNY = {"uncached": 2.0, "cached": 0.04, "output": 8.0}
# Context management: when the full-length tool results in the conversation exceed
# this many characters, all but the newest KEEP_RECENT are replaced at once by a
# short stub. Compacting in blocks (not every call) keeps the prompt prefix, and so
# the provider's cache, stable between compactions.
COMPACT_AT_CHARS = 120_000
KEEP_RECENT = 4
MAX_TOOL_RESULT_CHARS = 12000          # same clip as the R2G agent loop
INVOCATION_WAIT = 1800                 # seconds of backoff before an outage ends the attempt
MAX_NUDGES = 3                         # consecutive truncated/empty replies tolerated

SYSTEM = """你是一名数字后端工程师，使用 OpenROAD-flow-scripts（ORFS）把一个 RTL 设计做到签核干净。
你只能通过提供的工具操作：读文件、在工程的 constraints/、rtl/、hooks/ 下写文件、从某一阶段运行 ORFS 流程、运行签核（全套 DRC + LVS，平台官方规则）、读日志、结束任务。
ORFS 的行为由工程 constraints/config.mk 里的变量控制；ORFS 的脚本和平台配置可以在 flow/scripts、flow/platforms/<平台> 下读到，需要时自行查阅。
每次运行流程和签核都很耗时（几分钟到几小时），请先读日志和报告弄清原因再改。"""

TASK = """设计：顶层模块 {top}，平台 {platform}。
工程目录：{project}
- rtl/：原始 RTL；rtl/design.json 给出顶层、源文件清单和 include 目录（相对 rtl/）
- constraints/config.mk、constraints/constraint.sdc：基线配置（与历史基线完全相同的起点）

目标：最终版图全套 DRC 干净且 LVS 一致。
必须遵守：
- 保持原设计功能：不得删除或简化逻辑、缩小存储器、把输出接常量、替换为空模块；只允许不改变行为的源码修正（如文件清单、include/路径问题）。
- 不得修改时钟周期和 constraint.sdc，不得修改 PDK、平台文件或签核规则（工具也不允许）。
- 可以修改 config.mk 中的 ORFS 变量（面积/利用率/布局布线选项等）、添加由 config.mk 引用的 Tcl hook。
预算：最多 {run_flow} 次 run_flow、{run_signoff} 次 run_signoff、{model_calls} 次模型调用。较早的工具输出会被压缩成摘要，需要时请重新读取。
先运行流程和签核了解现状；完成或判断无法修复时调用 finish 说明结果。"""


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        h.update(str(p.relative_to(root)).encode())
        h.update(sha(p).encode())
    return h.hexdigest()


def source_root(paths: str, rtl: Path) -> str | None:
    """The baseline directory that corresponds to the project's rtl/ (from any file that maps)."""
    for raw in paths.split():
        parts = Path(raw).parts
        for i in range(1, len(parts)):
            if (rtl.joinpath(*parts[i:])).is_file():
                return str(Path(*parts[:i]))
    return None


def remap(paths: str, rtl: Path, root: str | None = None) -> tuple[list[str], list[str]]:
    """Map baseline absolute paths onto the project's rtl/ copy.

    Under the baseline source root: replace the prefix (so the root itself maps
    to rtl/); otherwise fall back to the longest existing suffix."""
    mapped, missing = [], []
    for raw in paths.split():
        hit = None
        if root and (raw == root or raw.startswith(root + "/")):
            cand = rtl / raw[len(root):].lstrip("/")
            hit = cand if cand.exists() else None
        if hit is None:
            parts = Path(raw).parts
            hit = next((rtl.joinpath(*parts[i:]) for i in range(1, len(parts))
                        if rtl.joinpath(*parts[i:]).exists()), None)
        if hit is None and Path(raw).suffix:
            # baseline sources staged under a container path (/work/_src/d0/x.v):
            # the unique file of that name in the project, if there is exactly one
            same = [q for q in rtl.rglob(Path(raw).name) if q.is_file()]
            hit = same[0] if len(same) == 1 else None
        (mapped if hit else missing).append(str(hit) if hit else raw)
    return mapped, missing


def prepare(case: dict, baseline: Path, project: Path) -> dict:
    """Original inputs + the baseline config/SDC, remapped; returns the receipt."""
    if project.exists():
        raise SystemExit("project exists; refusing to overwrite: %s" % project)
    rtl = project / "rtl"
    src = Path(case["input_root"])
    for f in src.rglob("*"):
        if f.is_file() and f.name != "design.json" and ".git" not in f.parts:
            d = rtl / f.relative_to(src)
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, d)
    for rel, expected in case.get("source_sha256", {}).items():
        if sha(rtl / rel) != expected:
            raise SystemExit("input differs from frozen manifest: %s" % rel)
    # The same neutral design description the R2G arm receives (top, sources,
    # include dirs) -- never the historical category or recipe.
    (rtl / "design.json").write_text(json.dumps(
        {"top": case["top"], "files": case.get("source_files", []),
         "include_dirs": case.get("include_dirs", []), "defines": case.get("defines") or []},
        indent=2, ensure_ascii=False) + "\n")
    cons = project / "constraints"
    cons.mkdir(parents=True)
    shutil.copyfile(baseline / "constraint.sdc", cons / "constraint.sdc")
    out, unmapped = [], []
    cfg_text = (baseline / "config.mk").read_text()
    files = re.search(r"^\s*export\s+VERILOG_FILES\s*=\s*(.*)$", cfg_text, re.M)
    root = source_root(files.group(1), rtl) if files else None
    for line in cfg_text.splitlines():
        m = re.match(r"\s*export\s+(VERILOG_FILES|VERILOG_INCLUDE_DIRS|SDC_FILE)\s*=\s*(.*)$", line)
        if m and m.group(1) == "SDC_FILE":
            line = "export SDC_FILE = %s" % (cons / "constraint.sdc")
        elif m:
            mapped, missing = remap(m.group(2), rtl, root)
            unmapped += missing
            line = "export %s = %s" % (m.group(1), " ".join(mapped + missing))
        out.append(line)
    (cons / "config.mk").write_text("\n".join(out) + "\n")
    (project / "hooks").mkdir()
    return {"input_root": str(src), "baseline": str(baseline), "unmapped_baseline_paths": unmapped,
            "rtl_sha256": {str(p.relative_to(rtl)): sha(p) for p in rtl.rglob("*") if p.is_file()},
            "sdc_sha256": sha(cons / "constraint.sdc"),
            "config_sha256": sha(cons / "config.mk")}


TASK_FALLBACK = """设计：顶层模块 {top}，平台 {platform}。
工程目录：{project}
这个工程是 R2G 自动修复流程的最终状态。R2G 没能把它做到签核干净（{reason}）。
- rtl/、constraints/、hooks/ 是 R2G 修复后的状态；r2g_context/ 里有 R2G 的诊断（diagnosis*.json）、修复日志（fix_log.jsonl）和它最后一次的签核报告（drc.json、lvs.json）。
R2G 诊断摘要：
{summary}

目标：最终版图全套 DRC 干净且 LVS 一致。
必须遵守：
- 保持原设计功能：不得删除或简化逻辑、缩小存储器、把输出接常量、替换为空模块；只允许不改变行为的源码修正。
- 不得修改时钟周期和 constraint.sdc，不得修改 PDK、平台文件或签核规则（工具也不允许）。
- 可以修改 config.mk 中的 ORFS 变量、添加由 config.mk 引用的 Tcl hook。
预算：最多 {run_flow} 次 run_flow、{run_signoff} 次 run_signoff、{model_calls} 次模型调用。较早的工具输出会被压缩成摘要，需要时请重新读取。
先读 r2g_context/ 弄清 R2G 做过什么、为什么没成功，再决定修复。上一次的 ORFS 工作区已清除，第一次 run_flow 必须从 synth 开始。完成或判断无法修复时调用 finish 说明结果。"""

FALLBACK_SKIP = {"backend", "reports", "agent_logs", "drc", "lvs", "rcx", "dataset", "features",
                 "labels", "logs", "objects", "results"}


def prepare_from_project(case: dict, src: Path, project: Path, context: Path | None) -> dict:
    """Start the fallback from R2G's final project (LLM + R2G arm).

    The project is copied without its layouts and reports (the harness grades
    only what the model produces); absolute paths that named R2G's project are
    rewritten to this one; R2G's last reports, fix log and diagnosis go to
    r2g_context/ for the model to read. The receipt hashes R2G's final RTL and
    SDC, so the SDC must stay as R2G left it and RTL edits are reviewed against
    R2G's RTL (r2g_rtl_snapshot/); the equivalence proof still compares with the
    original design."""
    if project.exists():
        raise SystemExit("project exists; refusing to overwrite: %s" % project)
    src = src.resolve()
    shutil.copytree(src, project, ignore=lambda d, names: [n for n in names if Path(d) == src and n in FALLBACK_SKIP])
    old, new = str(src), str(project)
    for f in project.rglob("*"):
        if f.is_file() and f.suffix in (".mk", ".tcl", ".sdc", ".cfg", ".json", ".v", ".sv", ".vh", ".svh"):
            t = f.read_text(errors="replace")
            if old in t:
                f.write_text(t.replace(old, new))
    (project / "reports").mkdir(exist_ok=True)
    (project / "hooks").mkdir(exist_ok=True)
    ctx = project / "r2g_context"
    ctx.mkdir(exist_ok=True)
    for name in ("drc.json", "lvs.json", "fix_log.jsonl", "route.json"):
        if (src / "reports" / name).is_file():
            shutil.copyfile(src / "reports" / name, ctx / name)
    if (src / "agent_logs" / "repair.log").is_file():
        shutil.copyfile(src / "agent_logs" / "repair.log", ctx / "repair.log")
    if context and context.is_dir():
        for f in context.iterdir():
            if f.is_file():
                shutil.copyfile(f, ctx / f.name)
    snap = project.parent / "r2g_rtl_snapshot"
    shutil.copytree(project / "rtl", snap)
    rtl = project / "rtl"
    return {"input_root": str(snap), "from_project": old,
            "rtl_sha256": {str(p.relative_to(rtl)): sha(p) for p in rtl.rglob("*") if p.is_file()},
            "sdc_sha256": sha(project / "constraints/constraint.sdc"),
            "config_sha256": sha(project / "constraints/config.mk"), "unmapped_baseline_paths": []}


def audit_env(job: str, design: str, project: Path, journal: Path, repo: Path) -> dict:
    """Same context the R2G pipeline gives its stage commands (pipeline._sh)."""
    audit_bin = str(repo / "r2g-skills/signoff-loop/scripts/flow/orfs_audit_bin")
    return {"R2G_ORFS_AUDIT_FILE": str(journal),
            "R2G_ORFS_REAL_MAKE": shutil.which("make") or "/usr/bin/make",
            "PATH": audit_bin + os.pathsep + os.environ.get("PATH", os.defpath),
            "R2G_ORFS_AUDIT_PYTHON": sys.executable, "R2G_ORFS_AUDIT_JOB": job,
            "R2G_ORFS_AUDIT_DESIGN": design, "R2G_ORFS_AUDIT_PROJECT": str(project)}


def audit_counts(journal: Path, job: str) -> dict:
    ev = []
    if journal.is_file():
        for line in journal.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if (e.get("job_id") or e.get("job")) == job:
                ev.append(e)
    starts = {e["invocation_id"]: e for e in ev if e.get("event") == "make_start"
              and e.get("invocation_id") and int(e.get("make_level") or 0) == 0}
    ends = {e["invocation_id"] for e in ev if e.get("event") == "make_end" and e.get("invocation_id")}
    wrappers = {e["wrapper_id"] for e in ev if e.get("event") == "wrapper_start" and e.get("wrapper_id")}
    wends = {e["wrapper_id"] for e in ev if e.get("event") == "wrapper_end" and e.get("wrapper_id")}
    return {"orfs_wrapper_calls": len(wrappers), "orfs_make_launches": len(starts),
            "make_kind_counts": {k: sum(s.get("kind") == k for s in starts.values()) for k in ("clean", "build_or_check")},
            "gaps": {"make_without_end": len(set(starts) - ends), "wrapper_without_end": len(wrappers - wends)}}


def call_cost(c: dict) -> float:
    cached = c.get("cache_read_input_tokens") or 0
    uncached = c.get("uncached_input_tokens")
    if uncached is None:
        uncached = (c.get("input_tokens") or 0) - cached
    return (uncached * PEAK_CNY["uncached"] + cached * PEAK_CNY["cached"]
            + (c.get("output_tokens") or 0) * PEAK_CNY["output"]) / 1e6


def compact(messages: list, transcript: list) -> bool:
    """Shrink old tool results in place once they pass COMPACT_AT_CHARS; True if it did."""
    full = [i for i, m in enumerate(messages) if m["role"] == "tool" and not m.get("_compacted")]
    if sum(len(messages[i]["content"]) for i in full) <= COMPACT_AT_CHARS or len(full) <= KEEP_RECENT:
        return False
    args = {tc["id"]: tc for m in messages if m["role"] == "assistant" for tc in m.get("tool_calls") or []}
    for i in full[:-KEEP_RECENT]:
        m = messages[i]
        a = json.dumps((args.get(m["tool_call_id"]) or {}).get("arguments", {}), ensure_ascii=False)[:160]
        head = m["content"][:240].replace("\n", " ")
        m["content"] = ("[较早的工具结果已压缩以控制上下文；需要细节请重新读取] %s %s → %s ..."
                        % (m.get("name"), a, head))
        m["_compacted"] = True
    transcript.append({"harness_compaction": len(full) - KEEP_RECENT})
    return True


def clip(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= MAX_TOOL_RESULT_CHARS else s[:MAX_TOOL_RESULT_CHARS] + "...(truncated)"


def run_loop(adapter, tools: BareTools, task: str, caps: dict, transcript: list, calls: list,
             failed: list | None = None) -> str:
    failed = [] if failed is None else failed
    messages = [{"role": "user", "content": task}]
    t0 = time.monotonic()
    nudges = 0
    while True:
        if len(calls) >= caps["model_calls"]:
            return "cap_model_calls"
        if sum(call_cost(c) for c in calls) >= caps["cost_cny"]:
            return "cap_cost"
        compact(messages, transcript)
        if time.monotonic() - t0 >= caps["wall_seconds"]:
            return "cap_wall"
        text, tool_calls, usage, err, stop_reason = "", [], None, None, None
        wire = [{k: v for k, v in m.items() if k != "_compacted"} for m in messages]
        for ev in adapter.stream(wire, SPECS, SYSTEM):
            if ev["type"] == "text":
                text += ev["delta"]
            elif ev["type"] == "tool_call":
                tool_calls.append(ev)
            elif ev["type"] == "usage":
                usage = ev
            elif ev["type"] == "error":
                err = ev["message"]
            elif ev["type"] == "done":
                stop_reason = ev.get("stop_reason")
        if err:
            # An invocation failure (relay/provider down, stream cut) is not the
            # model's decision: re-send the same request with backoff, for up to
            # INVOCATION_WAIT seconds in all. Failed invocations are kept apart
            # from the model calls and reported (2026-10-29 relay outage ended 38
            # attempts on their first timeout).
            failed.append({**(usage or {"usage_reported": False, "stream_complete": False}),
                           "error": err, "partial_output": bool(text or tool_calls), "at": time.time()})
            transcript.append({"model_error": err})
            waited = sum(f.get("backoff", 0) for f in failed[:-1])
            if waited >= INVOCATION_WAIT:
                return "model_error"
            failed[-1]["backoff"] = min(30 * 2 ** (len(failed) - 1), 300, INVOCATION_WAIT - waited)
            time.sleep(failed[-1]["backoff"])
            continue
        calls.append({**(usage or {"usage_reported": False, "stream_complete": False}),
                      "call_index": len(calls) + 1, "at": time.time()})
        messages.append({"role": "assistant", "content": text,
                         "tool_calls": [{"id": c["id"], "name": c["name"], "arguments": c["arguments"]}
                                        for c in tool_calls]})
        transcript.append({"assistant": text, "tool_calls": [{"name": c["name"], "arguments": c["arguments"]}
                                                              for c in tool_calls]})
        if not tool_calls:
            # A reply cut at the response limit (often all reasoning) or empty is not
            # a decision to stop: say so and let the model continue, a few times.
            if (stop_reason == "length" or not text.strip()) and nudges < MAX_NUDGES:
                nudges += 1
                note = ("（上一条回复达到长度上限被截断，没有输出工具调用。请直接调用工具继续，"
                        "或调用 finish 结束并说明结果。）" if stop_reason == "length" else
                        "（上一条回复为空。请直接调用工具继续，或调用 finish 结束并说明结果。）")
                messages.append({"role": "user", "content": note})
                transcript.append({"harness_nudge": note})
                continue
            return "model_stopped"
        nudges = 0
        for c in tool_calls:
            name, args = c["name"], c["arguments"] if isinstance(c["arguments"], dict) else {}
            t_start = time.time()
            try:
                if name in ("run_flow", "run_signoff") and tools.counts[name] >= caps[name]:
                    result = {"error": "budget exhausted for %s" % name}
                elif name in {s["name"] for s in SPECS}:
                    result = getattr(tools, name)(**args)
                else:
                    result = {"error": "unknown tool %s" % name}
            except ToolError as e:
                result = {"error": str(e)}
            except TypeError as e:
                result = {"error": "bad arguments: %s" % e}
            except Exception as e:                      # noqa: BLE001
                # a harness defect must not end the attempt; the model sees the error
                result = {"error": "tool failed: %s: %s" % (type(e).__name__, e)}
            transcript.append({"tool": name, "arguments": args, "seconds": round(time.time() - t_start, 1),
                               "result": result})
            messages.append({"role": "tool", "tool_call_id": c["id"], "name": name, "content": clip(result)})
        if tools.finished:
            return "finished"


_LITERAL = re.compile(r'"[^"\n]*"')
_PATHLIKE = re.compile(r'/|\.[A-Za-z0-9]{1,6}$')


def _lines(p: Path) -> list[str]:
    lines = [l.rstrip() for l in p.read_text(errors="replace").replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()                    # a final newline added or dropped is not an edit
    return lines


def _path_literals_only(old: list[str], new: list[str]) -> bool:
    """Same lines except inside double-quoted literals that are paths on both
    sides (`include "x.vh"`, $readmemh("mem/x.hex")): a location fix, not logic."""
    if len(old) != len(new):
        return False
    for a, b in zip(old, new):
        if a == b:
            continue
        if _LITERAL.sub('""', a) != _LITERAL.sub('""', b):
            return False
        for la, lb in zip(_LITERAL.findall(a), _LITERAL.findall(b)):
            if la != lb and not (_PATHLIKE.search(la[1:-1]) and _PATHLIKE.search(lb[1:-1])):
                return False
    return True


def semantic_review(project: Path, receipt: dict) -> dict:
    """Automatic part of the semantics review of the model's RTL edits.

    Cleared without a human: rtl/design.json (a description, not read by the
    flow); line endings / trailing whitespace; an added file whose lines equal
    an original's (a copy or a move to a space-free path); a moved original
    present elsewhere line-for-line; edits confined to path string literals.
    Anything else goes to a human review (pending)."""
    rtl, src = project / "rtl", Path(receipt["input_root"])
    originals = {rel: src / rel for rel in receipt["rtl_sha256"] if rel != "design.json" and (src / rel).is_file()}
    final = {str(p.relative_to(rtl)): p for p in rtl.rglob("*") if p.is_file()}
    final.pop("design.json", None)
    orig_lines = {rel: _lines(p) for rel, p in originals.items()}
    cleared, pending = [], []
    for rel, p in sorted(final.items()):
        h = receipt["rtl_sha256"].get(rel)
        if h and sha(p) == h:
            continue
        new = _lines(p)
        if rel in orig_lines and new == orig_lines[rel]:
            cleared.append({"file": rel, "why": "whitespace_only"})
        elif rel in orig_lines and _path_literals_only(orig_lines[rel], new):
            cleared.append({"file": rel, "why": "path_literals_only"})
        elif rel not in orig_lines and any(new == ol for ol in orig_lines.values()):
            cleared.append({"file": rel, "why": "copy_of_original"})
        elif rel not in orig_lines and any(_path_literals_only(ol, new) for ol in orig_lines.values()):
            cleared.append({"file": rel, "why": "copy_with_path_literals_only"})
        else:
            pending.append(rel)
    finals = [_lines(p) for p in final.values()]
    for rel in sorted(set(orig_lines) - set(final)):
        if any(f == orig_lines[rel] or _path_literals_only(orig_lines[rel], f) for f in finals):
            cleared.append({"file": rel, "why": "moved"})
        else:
            pending.append(rel)
    return {"cleared": cleared, "pending": pending}


def verdict(tools: BareTools, project: Path, receipt: dict, platform_dir: Path, deck_hash: str) -> dict:
    s = tools.signoff_summary()
    drc = json.loads((project / "reports/drc.json").read_text()) if (project / "reports/drc.json").is_file() else {}
    lvs = json.loads((project / "reports/lvs.json").read_text()) if (project / "reports/lvs.json").is_file() else {}
    run = tools._latest_run()
    gds = None
    if run:
        for c in (run / "results/6_final.gds", run / "final/6_final.gds"):
            if c.is_file():
                gds = sha(c)
                break
    # Both reports must have graded THIS layout (GDS digest). GF180's DRC report
    # carries no run tag (run_drc.sh hands GF180 straight to gf180_drc.py), so a
    # missing DRC tag is accepted when its digest matches; LVS's tag must match.
    tag = run.name if run else None
    bound = bool(gds and drc.get("gds_sha256") == gds and lvs.get("gds_sha256") == gds
                 and lvs.get("run_tag") == tag and drc.get("run_tag") in (tag, None))
    dual = (drc.get("status") == "clean" and drc.get("total_violations") == 0
            and drc.get("drc_mode") == "full" and lvs.get("status") == "clean")
    rtl_changed = sorted(rel for rel, h in receipt["rtl_sha256"].items()
                         if not (project / "rtl" / rel).is_file() or sha(project / "rtl" / rel) != h)
    rtl_added = sorted(str(p.relative_to(project / "rtl")) for p in (project / "rtl").rglob("*")
                       if p.is_file() and str(p.relative_to(project / "rtl")) not in receipt["rtl_sha256"])
    sdc_ok = sha(project / "constraints/constraint.sdc") == receipt["sdc_sha256"]
    deck_ok = tree_hash(platform_dir) == deck_hash
    review = semantic_review(project, receipt) if (rtl_changed or rtl_added) else {"cleared": [], "pending": []}
    if dual and bound and sdc_ok and deck_ok:
        outcome = "pending_semantic_review" if review["pending"] else "dual_clean"
    else:
        outcome = "not_dual_clean"
    return {"outcome": outcome, "signoff": s, "bound_to_layout": bound, "run": run.name if run else None,
            "gds_sha256": gds, "sdc_unchanged": sdc_ok, "platform_unchanged": deck_ok,
            "rtl_changed": rtl_changed, "rtl_added": rtl_added, "semantic_review": review}


def regrade(out: Path, repo: Path) -> dict:
    """Re-run verdict() on a finished attempt (a grading-rule fix, never a new
    attempt): same reports, same layout, same receipt. The previous verdict is
    kept under regraded_from for the audit trail."""
    res = json.loads((out / "result.json").read_text())
    project = out / (res["case_id"] + "_bare")
    orfs = Path(os.environ.get("ORFS_ROOT", "/home/yangao/r2g_toolchain/OpenROAD-flow-scripts"))
    platform_dir = orfs / "flow/platforms" / res["platform"]
    tools = BareTools(project, res["platform"], repo, orfs, {})
    deck = tree_hash(platform_dir) if res.get("platform_unchanged") else "changed"
    v = verdict(tools, project, res["prepare"], platform_dir, deck)
    prev = {k: res.get(k) for k in ("outcome", "bound_to_layout")}
    if v["outcome"] != res["outcome"]:
        res.update(v)
        res.setdefault("regraded_from", []).append({**prev, "at": time.time(), "rule": "gf180 DRC run_tag"})
        (out / "result.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    return {"case_id": res["case_id"], "before": prev["outcome"], "after": res["outcome"]}


def main():
    if "--regrade" in sys.argv:
        i = sys.argv.index("--regrade")
        print(json.dumps(regrade(Path(sys.argv[i + 1]).resolve(), Path(sys.argv[i + 2]).resolve())))
        return 0
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", type=Path, required=True, help="candidate JSON (population manifest entry)")
    ap.add_argument("--baseline", type=Path, required=True, help="dir with the baseline config.mk + constraint.sdc")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True, help="frozen runtime source root")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--caps", default="", help="JSON overrides of the budget caps")
    # 4096 was used up by thinking alone on hard steps (empty replies, then
    # model_stopped): pilot v2. Both arms use the same value.
    ap.add_argument("--max-tokens", type=int, default=32768)
    ap.add_argument("--from-project", type=Path, help="LLM+R2G fallback: start from R2G's final project")
    ap.add_argument("--context", type=Path, help="dir of R2G diagnosis files for r2g_context/")
    ap.add_argument("--reason", default="", help="why R2G stopped (no_recipe / recipe_failed: ...)")
    a = ap.parse_args()
    case = json.loads(a.case.read_text())
    caps = {**DEFAULT_CAPS, **(json.loads(a.caps) if a.caps else {})}
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "result.json").exists():
        print("already done:", out)
        return 0
    sys.path.insert(0, str(a.repo / "agent"))
    from r2g_agent.config import ModelConfig
    from r2g_agent.llm.openai_compat import OpenAICompatAdapter

    platform = case["platform"]
    name = case["id"] + "_bare"
    project = out / name
    if a.from_project:
        receipt = prepare_from_project(case, a.from_project, project, a.context)
    else:
        receipt = prepare(case, a.baseline, project)
    orfs = Path(os.environ.get("ORFS_ROOT", "/home/yangao/r2g_toolchain/OpenROAD-flow-scripts"))
    platform_dir = orfs / "flow/platforms" / platform
    deck_hash = tree_hash(platform_dir)
    job = "bare-" + case["id"] + "-" + uuid.uuid4().hex[:6]
    journal = out / "orfs_audit.jsonl"
    tools = BareTools(project, platform, a.repo, orfs, audit_env(job, name, project, journal, a.repo))
    mc = ModelConfig(provider="deepseek", model=a.model, base_url=a.base_url,
                     api_key_env="R2G_LOCAL_RELAY_AUTH", thinking=True, max_tokens=a.max_tokens, temperature=0.2)
    os.environ.setdefault("R2G_LOCAL_RELAY_AUTH", "local-relay-only-not-a-provider-key")
    adapter = OpenAICompatAdapter(mc.resolved(), os.environ["R2G_LOCAL_RELAY_AUTH"])
    if a.from_project:
        summary = ""
        for f in sorted((project / "r2g_context").glob("diagnosis*.json")):
            summary += f.name + ": " + f.read_text(errors="replace")[:3000] + "\n"
        task = TASK_FALLBACK.format(top=case["top"], platform=platform, project=project, reason=a.reason or "see r2g_context",
                                    summary=summary or "(see r2g_context/)", **caps)
        tools.force_synth_first = True
    else:
        task = TASK.format(top=case["top"], platform=platform, project=project, **caps)
    transcript, calls, failed = [], [], []
    started = time.time()
    stop = run_loop(adapter, tools, task, caps, transcript, calls, failed)
    agent_wall = time.time() - started
    agent_counts = dict(tools.counts)
    agent_eda = tools.eda_seconds
    # Independent verification: grade the final layout unless the model's own
    # last signoff already graded it (then its reports are reused, not re-run).
    harness_signoff = False
    run = tools._latest_run()
    drc = project / "reports/drc.json"
    graded = drc.is_file() and run and json.loads(drc.read_text()).get("run_tag") == run.name
    if run and not graded and (run / "results/6_final.gds").is_file():
        # journaled under its own job id: verification is apparatus, not agent cost
        tools.audit_env = {**tools.audit_env, "R2G_ORFS_AUDIT_JOB": job + "-verify"}
        tools.run_signoff()
        harness_signoff = True
    v = verdict(tools, project, receipt, platform_dir, deck_hash)
    # a failed invocation that had started answering may have been billed with no
    # counters: the attempt's usage is then incomplete (voided by the launcher)
    complete_usage = (bool(calls) and all(c.get("usage_reported") and c.get("stream_complete") for c in calls)
                      and not any(f["partial_output"] and not f.get("usage_reported") for f in failed))
    usage_totals = {k: (sum(c.get(k) or 0 for c in calls) if complete_usage else None) for k in COUNTERS}
    audit = audit_counts(journal, job)
    result = {"case_id": case["id"], "platform": platform, "arm": "llm_after_r2g" if a.from_project else "bare",
              "model": a.model, "job": job, "r2g_stop_reason": a.reason or None,
              "max_tokens": a.max_tokens,
              "stop_reason": stop, **v, "caps": caps, "model_calls": len(calls),
              "usage_complete": complete_usage, "provider_usage": usage_totals,
              "failed_invocations": len(failed),
              "failed_invocations_with_partial_output": sum(1 for f in failed if f["partial_output"]),
              "transport_retries": sum(len(c.get("transport_retries") or []) for c in calls),
              "cost_cny_peak": round(sum(call_cost(c) for c in calls), 5),
              "compactions": sum(1 for e in transcript if "harness_compaction" in e),
              "tool_counts": agent_counts, "agent_wall_seconds": round(agent_wall, 1),
              "agent_eda_seconds": round(agent_eda, 1), "harness_signoff": harness_signoff,
              "orfs_audit": audit, "writes": tools.writes, "finish": tools.finished,
              "prepare": receipt, "runtime": str(a.repo)}
    (out / "usage.json").write_text(json.dumps({"calls": calls, "failed_invocations": failed},
                                               indent=1, ensure_ascii=False))
    (out / "transcript.json").write_text(json.dumps(transcript, indent=1, ensure_ascii=False, default=str))
    (out / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    print(json.dumps({k: result[k] for k in ("case_id", "outcome", "stop_reason", "model_calls")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
