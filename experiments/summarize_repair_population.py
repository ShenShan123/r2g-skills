#!/usr/bin/env python3
"""Merge full repair populations without running models or modifying runtime data.

The parent candidate manifests define the denominator. Platform CSV files only
provide measurements; missing rows never disappear from the population.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone, timedelta
import fcntl
import io
import json
import os
from pathlib import Path
import re
import signal
import time

DEFAULT = Path(__file__).resolve().parents[1] / "design_cases/api_repair_cost_20261028/full_population"
MANIFESTS = (("nangate45", "nangate45_candidates.json"), ("sky130hd", "sky130_candidates.json"))


def atomic_text(path, value):
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(value)
    tmp.replace(path)


def case_key(row):
    match = re.search(r"C\d{5}(?!\d)", str(row.get("case_id") or row.get("id") or row.get("design") or ""))
    if not match or not row.get("platform"):
        raise ValueError("Case lacks platform or valid CID")
    return row["platform"], match.group()


def blocked_reason(case):
    reasons = list(case.get("input_blockers") or [])
    if case.get("local_208_ready") is False:
        reasons.append("original inputs not available on execution host 208")
    if case.get("input_ready") is False or case.get("source_inventory_complete") is False:
        reasons.append("original input inventory incomplete")
    if case.get("missing_source_files"):
        reasons.append("missing original source files")
    return "; ".join(str(r) for r in reasons)


def integer(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def collect(source, cohorts=None):
    population = {}
    for platform, filename in MANIFESTS:
        data = json.loads((source / filename).read_text())
        for case in data["candidates"]:
            key = case_key(case)
            if key[0] != platform or key in population:
                raise ValueError(f"Invalid or duplicate master population key: {key}")
            population[key] = case
    measured = {}
    measurement_sources = {}
    fields = []
    cohort_paths = [source / p for p in ("nangate45", "sky130hd", "sky130hd_remaining")]
    cohort_paths.extend(Path(p) if Path(p).is_absolute() else source / p for p in (cohorts or []))
    seen_paths = set()
    for cohort in cohort_paths:
        path = cohort if cohort.suffix == ".csv" else cohort / "costs.csv"
        if path.resolve() in seen_paths:
            continue
        seen_paths.add(path.resolve())
        if not path.exists():
            continue
        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "case_id" not in reader.fieldnames:
                raise ValueError(f"Incomplete CSV header: {path}")
            fields.extend(k for k in reader.fieldnames if k not in fields)
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError(f"Incomplete CSV row: {path}")
                key = case_key(row)
                if key not in population:
                    raise ValueError(f"Measurement outside master population: {key}")
                if key in measured and measured[key] != row:
                    raise ValueError(f"Conflicting duplicate measurement: {key}")
                measured[key] = row
                measurement_sources[key] = str(path.relative_to(source)) if path.is_relative_to(source) else str(path)
    rows = []
    for key, case in sorted(population.items()):
        row = dict(measured.get(key, {}))
        has_api = bool(row.get("session_id")) or (integer(row.get("model_calls")) or 0) > 0
        reason = blocked_reason(case)
        status = row.get("runner_status", "")
        if status in ("", "not_started", "not_submitted", "input_blocked") and not has_api:
            status = "input_blocked" if reason else "not_submitted"
        # A finished EDA job does not imply the API explanation/accounting ended.
        terminal = status == "complete" and has_api
        row.update(case_id=f"{key[0]}_{key[1]}", platform=key[0], cid=key[1],
                   population_status=status, terminal=str(terminal).lower(),
                   input_blocker=reason if status == "input_blocked" else "",
                   measurement_source=measurement_sources.get(key, ""),
                   historical_category=case.get("historical_category", ""),
                   semantic_review=json.dumps(case.get("semantic_guard") or case.get("semantic_review"), ensure_ascii=False))
        row["runner_status"] = status
        rows.append(row)
    first = ["case_id", "platform", "cid", "population_status", "terminal", "input_blocker", "measurement_source", "historical_category", "semantic_review"]
    fields = first + [k for k in fields if k not in first]
    if "runner_status" not in fields:
        fields.append("runner_status")
    return rows, fields


def render(source, cohorts=None):
    rows, fields = collect(source, cohorts)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    finished = sum(r["terminal"] == "true" for r in rows)
    known = [integer(r.get("tokens_known_reported")) for r in rows]
    token_sum = sum(v for v in known if v is not None) if any(v is not None for v in known) else None
    wrappers = [integer(r.get("orfs_wrapper_calls")) for r in rows]
    wrapper_sum = sum(v for v in wrappers if v is not None) if any(v is not None for v in wrappers) else None
    lines = ["# 全量 Agent API 修复成本实验", "", f"更新时间：{now}（北京时间）。", "",
             f"固定母清单共 {len(rows)} 例；按平台与 CID 去重。API 测量已结束 {finished} 例。",
             "执行子清单、尚未提交、输入受阻、失败均不改变分母。同一平台与设计的测量证据只计一次。", "",
             "| 平台 | 总数 | API 测量结束 | 尚未提交 | 输入受阻 | 其余运行/未完成 | 已观察双清 | 已报告 token | ORFS 调用 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    platform_usage = {}
    for platform, _ in MANIFESTS:
        subset = [r for r in rows if r["platform"] == platform]
        counts = Counter(r["population_status"] for r in subset)
        done = sum(r["terminal"] == "true" for r in subset)
        blocked = counts["input_blocked"]
        waiting = counts["not_submitted"]
        clean = sum(str(r.get("dual_clean", "")).lower() == "true" for r in subset)
        def observed_sum(field):
            values = [integer(r.get(field)) for r in subset]
            return sum(v for v in values if v is not None) if any(v is not None for v in values) else None
        platform_usage[platform] = {
            'cases': len(subset), 'measurement_finished': done,
            'model_calls': observed_sum('model_calls'),
            'tokens_known_reported': observed_sum('tokens_known_reported'),
            'dispatch_tokens_known': observed_sum('dispatch_tokens_known'),
            'explanation_tokens_known': observed_sum('explanation_tokens_known'),
            'orfs_wrapper_calls_observed': observed_sum('orfs_wrapper_calls'),
            **{key + '_known': observed_sum(key + '_known') for key in
               ('input_tokens', 'output_tokens', 'cache_read_input_tokens',
                'uncached_input_tokens', 'reasoning_tokens')},
        }
        tokens = platform_usage[platform]['tokens_known_reported']
        launches = platform_usage[platform]['orfs_wrapper_calls_observed']
        lines.append(f"| {platform} | {len(subset)} | {done} | {waiting} | {blocked} | {len(subset)-done-waiting-blocked} | {clean} | {tokens if tokens is not None else '未知'} | {launches if launches is not None else '未知'} |")
    lines += ["", f"已报告 token 累计：{token_sum if token_sum is not None else '未知'}；已观察 ORFS wrapper 调用累计：{wrapper_sum if wrapper_sum is not None else '未知'}。",
              "上述累计仅含已观察证据；未知、断流或尚未提交不能按 0 计，不代表最终完整费用。", "",
              "模型负责调度和解释，物理修复由系统策略流程执行。首轮直接双清不等于本轮修复成功；双清与数据集通过分别记录。",
              "ORFS 主计数是 run_orfs wrapper 调用，实际顶层 make 的 build/check 与 clean 以及各阶段分项保留在 CSV。", "",
              "- [全量逐项成本 CSV](costs.csv)",
              "- [N45 逐项状态](nangate45/STATUS.md)",
              "- [SKY130 逐项状态](sky130hd/STATUS.md)", "",
              "| 案例 | 状态 | 已报告 token | ORFS wrapper | 双清 | 输入阻塞 |",
              "|---|---|---:|---:|---|---|"]
    def show(v):
        return str(v if v not in (None, "") else "未知").replace("|", "\\|").replace("\n", " ")
    for row in rows:
        lines.append("| " + " | ".join(show(row.get(k)) for k in ("case_id", "population_status", "tokens_known_reported", "orfs_wrapper_calls", "dual_clean", "input_blocker")) + " |")
    atomic_text(source / "costs.csv", buf.getvalue())
    atomic_text(source / "STATUS.md", "\n".join(lines) + "\n")
    result = {"generated_at": now, "population": len(rows), "terminated": finished,
              "all_terminated": bool(rows) and finished == len(rows),
              "statuses": dict(Counter(r["population_status"] for r in rows)),
              "tokens_known_reported": token_sum, "orfs_wrapper_calls_observed": wrapper_sum,
              "platform_usage": platform_usage}
    atomic_text(source / "platform_token_totals.json", json.dumps(platform_usage, ensure_ascii=False, indent=2) + "\n")
    atomic_text(source / "population_snapshot.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def process_identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, IndexError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--cohort", type=Path, action="append", default=[],
                        help="Additional cohort directory or costs.csv; repeatable. Defaults always include nangate45, sky130hd, sky130hd_remaining.")
    parser.add_argument("--interval", type=float, default=20)
    parser.add_argument("--runner-pid", type=int, action="append", default=[],
                        help="Repeat for multiple runners; stop when all bound processes exit. Without this, watch until all cases terminate.")
    args = parser.parse_args()
    if not 1 <= args.interval <= 60:
        parser.error("interval must be between 1 and 60 seconds")
    args.source.mkdir(parents=True, exist_ok=True)
    with (args.source / ".population_summarizer.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("another population summarizer owns this output")
        pidfile = args.source / "population_summarizer.pid"
        identities = {pid: process_identity(pid) for pid in args.runner_pid}
        if args.watch:
            atomic_text(pidfile, str(os.getpid()) + "\n")
            atomic_text(args.source / "population_summarizer_watch.json", json.dumps({
                "pid": os.getpid(), "runner_start_ticks": identities, "interval_seconds": args.interval}, indent=2) + "\n")
        def stop(signum, frame):
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, stop)
        try:
            while True:
                result = render(args.source, args.cohort)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if not args.watch or result["all_terminated"]:
                    break
                if identities and not any(identity is not None and process_identity(pid) == identity for pid, identity in identities.items()):
                    print("All bound runners exited; unfinished/blocked cases remain explicitly unfinished.", flush=True)
                    break
                time.sleep(args.interval)
        finally:
            if args.watch and pidfile.exists() and pidfile.read_text().strip() == str(os.getpid()):
                pidfile.unlink()


if __name__ == "__main__":
    main()
