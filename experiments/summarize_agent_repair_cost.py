#!/usr/bin/env python3
"""One-shot, read-only experiment inspection; writes STATUS.md and costs.csv.

Only sessions identified by per-case state.json are counted. Calibration and
unrelated runtime sessions are excluded. No model/API calls, no EDA execution.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import fcntl
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "design_cases/api_repair_cost_20261028"
_spec = importlib.util.spec_from_file_location("repair_cost_measurement", Path(__file__).with_name("measure_agent_repair_cost.py"))
measurement = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measurement)


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def records(path):
    if not path.is_file():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            result.append(json.loads(line))
        except ValueError:
            pass  # writer may be appending the final line
    return result


def connect(path):
    if not path.is_file():
        return None
    conn = sqlite3.connect("file:" + quote(str(path.resolve())) + "?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def decode(row):
    out = dict(row)
    for key in ("spec", "result", "detail", "data"):
        if isinstance(out.get(key), str):
            try:
                out[key] = json.loads(out[key])
            except ValueError:
                pass
    return out


def session_usage(conn, sid, folder):
    if conn is not None and sid:
        rows = conn.execute("SELECT call_id,created,payload FROM model_usage WHERE session_id=? ORDER BY created", (sid,))
        return {"calls": [{**json.loads(r["payload"]), "call_id": r["call_id"], "created": r["created"]} for r in rows]}
    return read(folder / "usage.json") or read(folder / "usage_partial.json") or read(folder / "usage_initial.json") or {"calls": []}


def phase_usage(raw, initial, state):
    """A missing dispatch snapshot does not turn submission tokens into explanation."""
    start = state.get('explanation_started')
    if not start:
        return measurement.usage_subset(raw), measurement.usage_subset({'calls': []})
    cutoff = datetime.datetime.fromisoformat(start).timestamp()
    initial_ids = {c['call_id'] for c in initial.get('calls', [])}
    dispatch, explanation = [], []
    for call in raw.get('calls', []):
        created = call.get('created')
        before = created < cutoff if isinstance(created, (float, int)) else call.get('call_id') in initial_ids
        (dispatch if before else explanation).append(call)
    return measurement.usage_subset({'calls': dispatch}), measurement.usage_subset({'calls': explanation})


def jobs_for_case(conn, sid, state, folder):
    jobs, events = [], []
    if conn is not None and sid:
        rows = conn.execute("SELECT * FROM jobs WHERE json_extract(spec,'$.session_id')=? ORDER BY created", (sid,))
        for row in rows:
            job = decode(row)
            job["designs"] = [decode(r) for r in conn.execute("SELECT * FROM designs WHERE job_id=?", (job["id"],))]
            events.extend(decode(r) for r in conn.execute("SELECT * FROM events WHERE job_id=? ORDER BY id", (job["id"],)))
            jobs.append(job)
    else:
        for jid in state.get("job_ids", []):
            job = read(folder / (jid + ".job.json"))
            if job:
                jobs.append(job)
            events.extend(read(folder / (jid + ".events.json"), []))
    return jobs, events


def repair_observation(design, events):
    detail = design.get("detail") or {}
    project = Path(design["project"]) if design.get("project") else None
    meta = read(project / "metadata.json", {}) if project else {}
    frontend = detail.get("frontend_fixes") or meta.get("frontend_fixes") or []
    flow = detail.get("flow_fixes") or meta.get("flow_fixes") or []
    fixes = records(project / "reports/fix_log.jsonl") if project else []
    fixes = fixes or detail.get("repair") or []
    applied = [f for f in fixes if f.get("strategy") not in (None, "none", "stop_clean")
               and f.get("verdict") not in ("stop_clean", "skipped")]
    strategy_names = sorted({str(f.get("recipe") or f.get("strategy")) for f in frontend + flow + applied
                             if f.get("recipe") or f.get("strategy")})
    own_events = [e for e in events if e.get("design") == design["name"]]
    signoffs = [e["data"] for e in own_events if e.get("stage") == "signoff"
                and isinstance(e.get("data"), dict) and "dual_clean" in e["data"]]
    first_clean = signoffs[0].get("dual_clean") if signoffs else None
    dual = (detail.get("signoff") or {}).get("dual_clean")
    physical = bool(flow or applied)
    if dual is True:
        if physical and frontend:
            kind = "前端及物理修复后双清"
        elif physical:
            kind = "物理修复后双清"
        elif frontend:
            kind = "前端处理后首轮签核双清"
        elif first_clean is True:
            kind = "首轮签核直接双清（未观察修复）"
        else:
            kind = "双清；修复过程证据未完整"
    elif design.get("stage") == "repair" and design.get("status") == "running":
        kind = "物理修复进行中"
    elif frontend or physical:
        kind = "已观察修复尝试，尚未双清"
    else:
        kind = "尚未观察修复或完成签核"
    verify = detail.get("verify") or {}
    return {"dual_clean": dual, "dataset_passed": verify.get("passed"), "dataset_total": verify.get("total"),
            "first_signoff_dual_clean": first_clean, "repair_classification": kind,
            "actual_strategies": strategy_names, "frontend_fix_count": len(frontend),
            "physical_fix_records": len(flow) + len(applied)}


def show(value):
    if value is None:
        return "未知"
    return str(value).replace("|", "\\|").replace("\n", " ")


def atomic_text(path, text):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text)
    temp.replace(path)


def summarize(args):
    candidates = read(args.source / "case_candidates.json", {}).get("candidates", [])
    protocol = read(args.source / "measurement_protocol.json", {})
    selected = set(protocol.get("cases") or [c["id"] for c in candidates])
    sessions = connect(args.runtime / "sessions.sqlite")
    jobs_conn = connect(args.runtime / "jobs.sqlite")
    rows, details = [], []
    try:
        # Each DB is observed in one read transaction, without touching runtime configuration.
        for conn in (sessions, jobs_conn):
            if conn:
                conn.execute("BEGIN")
        for case in candidates:
            if case["id"] not in selected:
                continue
            folder = args.source / "cases" / case["id"]
            state = read(folder / "state.json", {})
            sid = state.get("session_id")
            raw_usage = session_usage(sessions, sid, folder)
            usage = measurement.usage_subset(raw_usage)
            initial_raw = read(folder / "usage_initial.json", {})
            dispatch, explanation = phase_usage(raw_usage, initial_raw, state)
            jobs, events = jobs_for_case(jobs_conn, sid, state, folder)
            audit = measurement.read_audit(args.orfs_audit, jobs)
            stage_counts = {}
            for stage in ("synth", "flow", "signoff", "repair", "graph", "verify", "unknown"):
                es = [e for e in audit["matched_events"] if (e.get("stage") or "unknown") == stage]
                stage_counts[stage] = {
                    "wrapper": len({e["wrapper_id"] for e in es if e.get("event") == "wrapper_start" and e.get("wrapper_id")}),
                    **{kind: len({e["invocation_id"] for e in es if e.get("event") == "make_start"
                                   and e.get("invocation_id") and int(e.get("make_level") or 0) == 0
                                   and e.get("kind") == kind}) for kind in ("build_or_check", "clean")}}
            audit["by_stage"] = stage_counts if audit["matched_events"] else None
            observed = []
            for job in jobs:
                for d in job.get("designs", []):
                    observed.append({"job_id": job["id"], "name": d["name"], "status": d["status"],
                                     "stage": d["stage"], "reason": (d.get("detail") or {}).get("reason"),
                                     **repair_observation(d, events)})
            complete_tokens = usage["totals"]["total_tokens"]
            all_clean = (all(d["dual_clean"] is True for d in observed) if observed
                         and all(d["dual_clean"] is not None for d in observed) else None)
            verify_known = bool(observed) and all(d["dataset_total"] is not None for d in observed)
            token_scope = ("未调用" if not usage["model_calls"] else "完整响应计数"
                           if complete_tokens is not None else "存在缺失或未完成响应")
            if state.get("status") != "complete" and usage["model_calls"]:
                token_scope += "；会话未结案"
            row = {"case_id": case["id"], "platform": case["platform"], "session_id": sid,
                   "runner_status": state.get("status", "not_started"),
                   "job_ids": ";".join(j["id"] for j in jobs),
                   "job_status": ";".join(j["status"] for j in jobs),
                   "design_stage": ";".join(d["name"] + ":" + d["stage"] + "/" + d["status"] for d in observed),
                   "model_calls": usage["model_calls"], "complete_usage_calls": usage["complete_usage_calls"],
                   "tokens_complete_total": complete_tokens,
                   "tokens_known_reported": usage["known_reported_totals"]["total_tokens"], "token_completeness": token_scope,
                   "dispatch_tokens_complete": dispatch["totals"]["total_tokens"],
                   "dispatch_tokens_known": dispatch["known_reported_totals"]["total_tokens"],
                   "explanation_tokens_complete": explanation["totals"]["total_tokens"],
                   "explanation_tokens_known": explanation["known_reported_totals"]["total_tokens"],
                   "explanation_model_calls": explanation["model_calls"],
                   "orfs_wrapper_calls": audit["orfs_wrapper_calls"], "orfs_make_launches": audit["orfs_make_launches"],
                   "make_build_or_check": (audit["make_kind_counts"] or {}).get("build_or_check"),
                   "make_clean": (audit["make_kind_counts"] or {}).get("clean"), "audit_coverage": audit["coverage"],
                   "repair_stage_wrappers": stage_counts["repair"]["wrapper"] if audit["matched_events"] else None,
                   "dual_clean": all_clean,
                   "dataset_passed": sum(d["dataset_passed"] or 0 for d in observed) if verify_known else None,
                   "dataset_total": sum(d["dataset_total"] for d in observed) if verify_known else None,
                   "repair_classification": ";".join(d["repair_classification"] for d in observed),
                   "actual_strategies": ";".join(sorted({s for d in observed for s in d["actual_strategies"]})),
                   "historical_expected_evaluator_only": case.get("expected_generic_recipe"),
                   "error": "; ".join(str(e) for e in [state.get("error"), state.get("initial_transport_error"),
                                    state.get("explanation_transport_error"), *state.get("chat_errors", []),
                                    *(d.get("reason") for d in observed)] if e)}
            rows.append(row)
            for counter in measurement.COUNTERS:
                if counter != "total_tokens":
                    row[counter + "_complete"] = usage["totals"][counter]
                    row[counter + "_known"] = usage["known_reported_totals"][counter]
            for stage, counts in stage_counts.items():
                for key, value in counts.items():
                    row[stage + "_" + key] = value if audit["matched_events"] else None
            details.append({"case_id": case["id"], "session_id": sid, "usage": usage,
                            "dispatch_usage": dispatch, "explanation_usage": explanation,
                            "designs": observed, "audit": audit})
    finally:
        for conn in (sessions, jobs_conn):
            if conn:
                conn.close()
    args.output.mkdir(parents=True, exist_ok=True)
    csvbuf = io.StringIO()
    if rows:
        writer = csv.DictWriter(csvbuf, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    atomic_text(args.output / "costs.csv", csvbuf.getvalue())
    observed_token_sum = (sum(r["tokens_known_reported"] or 0 for r in rows)
                          if any(r["tokens_known_reported"] is not None for r in rows) else None)
    observed_calls = sum(r["model_calls"] for r in rows)
    timestamp = measurement.now()
    lines = ["# Agent 修复 API 成本快照", "", f"生成时间：{timestamp}（北京时间）。", "",
             f"范围：{len(rows)} 个原始 RTL 案例；只统计各案例 state.json 关联的 session，排除校准会话。",
             f"已观察模型调用 {observed_calls} 次；已报告 token 累计 {show(observed_token_sum)}。"
             "该累计仅为已观察计数；运行中、缺失用量或断流时不代表最终完整费用。", "",
             "当前系统默认实验参数重放；不声称完全复刻历史冻结参数。模型负责调度和解释，物理修复由系统策略流程执行。",
             "ORFS wrapper 是 run_orfs 调用次数；make 为实际启动的顶层进程，区分 build/check 和 clean，排除递归 make。"
             "未知不能按 0 计；正在运行会出现 start/end 未配对。", "",
             "| 案例 | 阶段/状态 | 调度 token | 解释 token | 已报告合计 | 完整性 | ORFS 调用（wrapper） | 其中修复阶段 | build/check make | clean make | 双清 | 数据集 | 本轮修复 |",
             "|---|---|---:|---|---:|---|---:|---:|---:|---:|---|---|---|"]
    for r in rows:
        dataset = (str(r["dataset_passed"]) + "/" + str(r["dataset_total"])) if r["dataset_total"] is not None else "未知"
        dual = "是" if r["dual_clean"] is True else "否" if r["dual_clean"] is False else "未知"
        explanation_text = (show(r["explanation_tokens_complete"]) if r["explanation_tokens_complete"] is not None
                            else str(r["explanation_tokens_known"]) + "+未知"
                            if r["explanation_tokens_known"] is not None else "未知" if r["explanation_model_calls"] else "未调用")
        values = [r["case_id"], r["design_stage"] or r["runner_status"], r["dispatch_tokens_complete"],
                  explanation_text, r["tokens_known_reported"],
                  r["token_completeness"], r["orfs_wrapper_calls"], r["repair_stage_wrappers"],
                  r["make_build_or_check"], r["make_clean"], dual,
                  dataset, (r["repair_classification"] or "未观察") + ("：" + r["actual_strategies"] if r["actual_strategies"] else "")]
        lines.append("| " + " | ".join(show(v) for v in values) + " |")
    lines += ["", "首轮签核直接双清的案例不算成本轮修复成功。前端处理与物理修复分别记录；仅有历史 recipe 分类不构成本轮执行证据。",
              "CSV 同时按 synth/flow/signoff/repair/graph/verify 分列调用数；synth 重试属于前端开销，不合并为后端物理修复次数。",
              "逐项可机读数据：costs.csv；完整性及 start/end 缺口：status_snapshot.json。", ""]
    for r in rows:
        if r["error"]:
            lines.append(f"- {r['case_id']}：{show(r['error'])}")
    atomic_text(args.output / "STATUS.md", "\n".join(lines))
    measurement.save(args.output / "status_snapshot.json", {"generated_at": timestamp, "cases": details})
    print(json.dumps({"cases": len(rows), "model_calls": observed_calls, "known_reported_tokens": observed_token_sum if observed_calls else None,
                      "status_path": str(args.output / "STATUS.md"), "csv_path": str(args.output / "costs.csv")}, ensure_ascii=False), flush=True)


def process_identity(pid):
    """Linux kernel start ticks distinguish PID reuse; zombies have finished."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, IndexError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--orfs-audit", type=Path, action="append")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--runner-pid", type=int)
    args = parser.parse_args()
    args.output = args.output or args.source
    args.runtime = args.runtime or args.source / "runtime"
    args.orfs_audit = args.orfs_audit or [args.source / "orfs_audit.jsonl"]
    if args.watch and (not args.runner_pid or not 1 <= args.interval <= 60):
        parser.error("--watch requires --runner-pid and interval between 1 and 60 seconds")
    args.output.mkdir(parents=True, exist_ok=True)
    lock = open(args.output / ".summarizer.lock", "a+")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("another summarizer owns this output")
    identity = process_identity(args.runner_pid) if args.watch else None
    if args.watch:
        measurement.save(args.output / "summarizer_watch.json", {
            "runner_pid": args.runner_pid, "runner_start_ticks": identity,
            "interval_seconds": args.interval, "started_at": measurement.now()})
    while True:
        try:
            summarize(args)
        except (sqlite3.OperationalError, OSError) as exc:
            if not args.watch:
                raise
            print(json.dumps({'report_retry': type(exc).__name__, 'message': str(exc)[:200]}), flush=True)
        if not args.watch or identity is None or process_identity(args.runner_pid) != identity:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
