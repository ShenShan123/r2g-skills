#!/usr/bin/env python3
"""Measure real chat API orchestration tokens and audited ORFS launches.

Each original design gets a new session and a neutral source-only input copy.
Polling uses HTTP GET, not model calls. A final chat turn asks only for an
explanation. This measures the installed Agent plus deterministic repair loop;
it does not claim the model personally chooses each physical recipe.
Run with --dry-run first; repeat the same command to resume recorded jobs.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "design_cases/api_repair_cost_20261028"
TERMINAL = {"done", "failed", "cancelled"}
COUNTERS = ("input_tokens", "output_tokens", "total_tokens",
            "cache_read_input_tokens", "uncached_input_tokens",
            "cache_creation_input_tokens", "reasoning_tokens")
PRINT_LOCK = threading.Lock()


def now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    temp.replace(path)


def load(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def progress(case, message):
    with PRINT_LOCK:
        print(f"{now()} {case}: {message}", flush=True)


def get(api, route):
    # Ignore ambient HTTP proxies for this explicitly selected local service.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(5):
        try:
            with opener.open(api.rstrip("/") + route, timeout=90) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == 4:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 4:
                raise
        time.sleep(min(2 ** attempt, 8))


def stream_chat(api, message, sid, raw_path, on_event, timeout):
    body = {"message": message}
    if sid:
        body["session_id"] = sid
    req = urllib.request.Request(api.rstrip("/") + "/api/chat",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # Do not retry POST: a failed connection can still have submitted a job.
    with opener.open(req, timeout=timeout) as response, open(raw_path, "wb") as raw:
        data_lines = []
        for line in response:
            raw.write(line)
            raw.flush()
            clean = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if clean.startswith("data:"):
                data_lines.append(clean[5:].lstrip())
            elif not clean and data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                if payload != "[DONE]":
                    on_event(json.loads(payload))
        if data_lines:
            payload = "\n".join(data_lines)
            if payload != "[DONE]":
                on_event(json.loads(payload))


def prepare_input(case, output):
    """Copy original source material, stripping historical strategy metadata."""
    target = output / "inputs" / case["id"]
    receipt_path = output / "cases" / case["id"] / "input_receipt.json"
    if target.exists():
        receipt = load(receipt_path)
        if not receipt:
            raise RuntimeError(f"existing input has no receipt: {target}")
        for rel, expected in receipt["sha256"].items():
            p = target / rel
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f"input changed since preparation: {p}")
        for rel, expected in case.get("source_sha256", {}).items():
            if receipt["sha256"].get(rel) != expected:
                raise RuntimeError(f"input receipt differs from frozen manifest: {target / rel}")
        return target
    source = Path(case["input_root"]).resolve()
    target.mkdir(parents=True)
    for f in source.rglob("*"):
        if not f.is_file() or f.name == "design.json" or ".git" in f.parts:
            continue
        dest = target / f.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dest)
    def relative(name):
        p = Path(name)
        return str(p.relative_to(source)) if p.is_absolute() else str(p)
    meta = {"top": case["top"], "files": [relative(f) for f in case["source_files"]],
            "include_dirs": [relative(f) for f in case.get("include_dirs", [])],
            "defines": case.get("defines") or []}
    # No origin, baseline category, recipe names, or repaired constraints.
    save(target / "design.json", meta)
    hashes = {str(f.relative_to(target)): hashlib.sha256(f.read_bytes()).hexdigest()
              for f in target.rglob("*") if f.is_file()}
    for rel, expected in case.get("source_sha256", {}).items():
        if hashes.get(rel) != expected:
            raise RuntimeError(f"copied input differs from frozen manifest: {target / rel}")
    save(receipt_path, {"source": str(source), "input": str(target), "sha256": hashes,
                        "historical_metadata_removed": True,
                        "physical_parameters": "current Agent measured platform defaults"})
    return target


def initial_prompt(case, source):
    return (f"请处理这个原始 RTL 设计：平台 {case['platform']}，目录 {source}，"
            f"顶层模块 {case['top']}。使用系统正常流程生成 graph 数据集，遇到问题尝试修复，"
            "目标是实际 DRC 干净且 LVS 一致。必须保持原设计功能，不得删逻辑、缩小存储器、"
            "常量化输出、替换为空模块或放宽签核条件。使用该平台默认实验参数。"
            "直接启动这个设计一次，返回任务 ID 即可，后台完成后我会再请求结果。")


def event_job_ids(event):
    result = event.get("result")
    if event.get("type") == "tool_result" and isinstance(result, dict) and result.get("job_id"):
        return [str(result["job_id"])]
    return []


def associated_jobs(api, sid):
    return [j["id"] for j in get(api, "/api/jobs")
            if (j.get("spec") or {}).get("session_id") == sid]


def usage_subset(usage, excluded=()):
    calls = [c for c in (usage or {}).get("calls", []) if c.get("call_id") not in excluded]
    complete = bool(calls) and all(c.get("usage_reported") and c.get("stream_complete") for c in calls)
    return {"model_calls": len(calls), "calls": calls,
            "totals": {k: sum(c[k] for c in calls)
                       if complete and all(isinstance(c.get(k), int) for c in calls) else None
                       for k in COUNTERS},
            "known_reported_totals": {k: sum(c[k] for c in calls if isinstance(c.get(k), int))
                                      if any(isinstance(c.get(k), int) for c in calls) else None
                                      for k in COUNTERS},
            "known_reported_totals_note": "observed provider counters only; incomplete streams/calls are not complete billed totals",
            "complete_usage_calls": sum(bool(c.get("usage_reported") and c.get("stream_complete")
                                             and c.get("total_tokens") is not None) for c in calls)}


def read_audit(paths, jobs):
    projects = {str(Path(d["project"]).resolve()) for j in jobs for d in j.get("designs", [])
                if d.get("project")}
    job_ids = {j["id"] for j in jobs}
    records, errors, available = [], [], False
    for path in paths:
        p = Path(path)
        if not p.is_file():
            errors.append(f"audit missing: {p}")
            continue
        available = True
        for line_no, line in enumerate(p.read_text().splitlines(), 1):
            try:
                e = json.loads(line)
            except ValueError:
                errors.append(f"invalid audit line: {p}:{line_no}")
                continue
            project = e.get("project") or e.get("project_path")
            explicit_job = e.get("job_id") or e.get("job")
            if ((explicit_job in job_ids) if explicit_job else
                    (project and str(Path(project).resolve()) in projects)):
                records.append(e)
    complete = available and bool(records) and not errors
    wrappers = {e["wrapper_id"] for e in records
                if e.get("event") == "wrapper_start" and e.get("wrapper_id")}
    starts = {e["invocation_id"]: e for e in records
              if e.get("event") == "make_start" and e.get("invocation_id")
              and int(e.get("make_level") or 0) == 0}
    breakdown = {kind: sum(e.get("kind") == kind for e in starts.values())
                 for kind in ("clean", "build_or_check")}
    ends = {e["invocation_id"] for e in records if e.get("event") == "make_end"
            and e.get("invocation_id") and int(e.get("make_level") or 0) == 0}
    wrapper_ends = {e["wrapper_id"] for e in records
                    if e.get("event") == "wrapper_end" and e.get("wrapper_id")}
    gaps = {"make_starts_without_end": sorted(set(starts) - ends),
            "make_ends_without_start": sorted(ends - set(starts)),
            "wrapper_starts_without_end": sorted(wrappers - wrapper_ends),
            "wrapper_ends_without_start": sorted(wrapper_ends - wrappers)}
    coverage = ("not_observed" if not records else "gaps_observed"
                if errors or any(gaps.values()) else "paired_observed_events")
    return {"available": available, "launches": len(starts) if complete else None,
            "coverage": coverage, "gaps": gaps,
            "orfs_wrapper_calls": len(wrappers) if complete else None,
            "orfs_make_launches": len(starts) if complete else None,
            "make_kind_counts": breakdown if complete else None,
            "matched_events": records, "errors": errors,
            "definition": "wrapper_start counts run_orfs calls; make_start counts successfully spawned top-level make processes, split clean/build_or_check; recursive make targets are excluded"}


def collect(api, state, folder, audit_paths):
    jobs = [get(api, "/api/jobs/" + jid) for jid in state.get("job_ids", [])]
    designs = []
    for job in jobs:
        save(folder / (job["id"] + ".job.json"), job)
        events, after = [], 0
        while True:
            part = get(api, f"/api/jobs/{job['id']}/events?after={after}")
            if not part:
                break
            events.extend(part)
            nxt = max(e["id"] for e in part)
            if nxt <= after:
                break
            after = nxt
        save(folder / (job["id"] + ".events.json"), events)
        for d in job.get("designs", []):
            detail = d.get("detail") or {}
            designs.append({"job_id": job["id"], "name": d["name"], "project": d.get("project"),
                            "status": d["status"], "stage": d["stage"],
                            "signoff": detail.get("signoff"), "frontend_fixes": detail.get("frontend_fixes"),
                            "repair_events": [e for e in events if e.get("design") == d["name"]
                                              and ("repair" in e.get("stage", "") or "repair" in e.get("kind", ""))]})
    audit = read_audit(audit_paths, jobs)
    save(folder / "orfs_audit.json", audit)
    return jobs, designs, audit


def reuse_case(case, args):
    """Follow an existing measured attempt without a second model request.

    The original runner remains the sole writer and owns its final explanation.
    Linking its case directory lets the full-population read-only report include
    the live receipt without copying a stale snapshot or overwriting evidence.
    """
    source = Path(case['reuse_case_directory']).resolve(strict=True)
    state_path = source / 'state.json'
    if not state_path.is_file():
        raise ValueError('reused case has no submission state: ' + str(source))
    state = load(state_path)
    if state.get('case_id') != case['id'] or not state.get('initial_started'):
        raise ValueError('reused case identity or submission receipt does not match')
    dest = args.output / 'cases' / case['id']
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        if dest.resolve() != source:
            raise ValueError('existing case directory conflicts with reuse receipt')
    else:
        dest.symlink_to(source, target_is_directory=True)
    if args.dry_run:
        return {'case_id': case['id'], 'status': 'dry_run', 'reused_case': str(source)}
    progress(case['id'], 'following existing API attempt; no new POST')
    deadline = time.monotonic() + args.job_timeout
    while True:
        state = load(state_path)
        if state.get('status') == 'complete':
            summary = load(source / 'summary.json')
            if summary:
                return summary
        if state.get('status') == 'incomplete' or time.monotonic() >= deadline:
            return {**state, 'status': 'incomplete', 'reused_case': str(source),
                    'reuse_note': 'Original attempt retained; no automatic duplicate submission'}
        time.sleep(args.poll_seconds)


def run_case(case, args):
    if case.get('reuse_case_directory'):
        return reuse_case(case, args)
    folder = args.output / "cases" / case["id"]
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "state.json"
    state = load(state_path, {"case_id": case["id"], "platform": case["platform"],
                             "session_id": None, "job_ids": [], "created": now()})
    if state.get("status") == "complete":
        return load(folder / "summary.json")
    try:
        source = prepare_input(case, args.output)
        prompt = initial_prompt(case, source)
        save(folder / "request.json", {"message": prompt})
        if args.dry_run:
            return {"case_id": case["id"], "status": "dry_run", "request": str(folder / "request.json")}
        def on_event(event):
            if event.get("type") == "session":
                state["session_id"] = event["session_id"]
            state["job_ids"] = sorted(set(state["job_ids"] + event_job_ids(event)))
            if event.get("type") == "error":
                state.setdefault("chat_errors", []).append(event.get("message"))
            save(state_path, state)
        if not state.get("initial_started"):
            state.update(initial_started=now(), status="submitting")
            save(state_path, state)
            progress(case["id"], "chat submission")
            try:
                stream_chat(args.api, prompt, None, folder / "initial.sse", on_event, args.chat_timeout)
                state["initial_finished"] = now()
            except Exception as exc:
                state["initial_transport_error"] = str(exc)
            save(state_path, state)
        if not state.get("session_id"):
            raise RuntimeError("No session receipt; initial POST outcome ambiguous, will not resubmit automatically")
        state["job_ids"] = sorted(set(state["job_ids"] + associated_jobs(args.api, state["session_id"])))
        save(state_path, state)
        if (not state['job_ids'] and getattr(args, 'recover_unsubmitted', False)
                and state.get('initial_finished') and not state.get('recovery_started')):
            # A completed/closed initial stream with no server-side session job
            # can be retried explicitly. Keep the same session/token ledger and
            # preserve the original stream. Never retry an ambiguous POST blindly.
            state['recovery_started'] = now()
            save(state_path, state)
            stream_chat(args.api, prompt + ' 上次请求未产生关联任务；这是一次恢复请求。已有该设计任务时仅报告其ID，避免重复启动。',
                        state['session_id'], folder / 'recovery.sse', on_event, args.chat_timeout)
            state['job_ids'] = sorted(set(state['job_ids'] + associated_jobs(args.api, state['session_id'])))
            state['recovery_finished'] = now()
            save(state_path, state)
        usage_initial = get(args.api, f"/api/sessions/{state['session_id']}/usage")
        if state.get('recovery_finished') and not state.get('recovery_usage_snapshotted'):
            old_usage = folder / 'usage_initial.json'
            if old_usage.exists():
                save(folder / 'usage_initial.before_recovery.json', load(old_usage))
            save(old_usage, usage_initial)
            state['recovery_usage_snapshotted'] = True
            save(state_path, state)
        if not (folder / "usage_initial.json").exists():
            save(folder / "usage_initial.json", usage_initial)
        if not state["job_ids"]:
            raise RuntimeError("Agent did not submit a job; see initial.sse (no direct-job fallback permitted)")
        deadline = time.time() + args.job_timeout
        if state.get('error'):
            state.setdefault('recovered_errors', []).append(state.pop('error'))
        state["status"] = "polling"
        save(state_path, state)
        progress(case["id"], "polling " + ",".join(state["job_ids"]))
        while True:
            jobs = [get(args.api, "/api/jobs/" + jid) for jid in state["job_ids"]]
            save(folder / "latest_jobs.json", jobs)
            if all(j["status"] in TERMINAL for j in jobs):
                break
            if time.time() >= deadline:
                raise TimeoutError("Polling deadline reached; jobs remain live and can be resumed")
            time.sleep(args.poll_seconds)
        if not state.get("explanation_started"):
            state["explanation_started"] = now()
            save(state_path, state)
            explanation = ("这些任务已结束，请仅调用只读工具查看结果并简要解释：" + ", ".join(state["job_ids"])
                           + "。报告实际 DRC/LVS 与数据集结果，以及能从记录确认的修复。不要启动、重跑、取消或删除任何任务，也不要修改任何数据。")
            save(folder / "explanation_request.json", {"session_id": state["session_id"], "message": explanation})
            try:
                stream_chat(args.api, explanation, state["session_id"], folder / "explanation.sse",
                            on_event, args.chat_timeout)
                state["explanation_finished"] = now()
            except Exception as exc:
                state["explanation_transport_error"] = str(exc)
        state["job_ids"] = sorted(set(state["job_ids"] + associated_jobs(args.api, state["session_id"])))
        usage = get(args.api, f"/api/sessions/{state['session_id']}/usage")
        save(folder / "usage.json", usage)
        initial = load(folder / "usage_initial.json", {})
        jobs, designs, audit = collect(args.api, state, folder, args.orfs_audit)
        summary = {"case_id": case["id"], "platform": case["platform"], "session_id": state["session_id"],
                   "job_ids": state["job_ids"], "status": "complete" if all(j["status"] in TERMINAL for j in jobs) else "unexpected_live_job",
                   "usage": usage_subset(usage), "dispatch_usage": usage_subset(initial),
                   "explanation_usage": usage_subset(usage, {c['call_id'] for c in initial.get('calls', [])}),
                   "designs": designs, "orfs_launches": audit["launches"],
                   "orfs_wrapper_calls": audit["orfs_wrapper_calls"],
                   "orfs_make_launches": audit["orfs_make_launches"],
                   "orfs_make_kind_counts": audit["make_kind_counts"], "orfs_audit_errors": audit["errors"],
                   "orfs_audit_coverage": audit["coverage"], "orfs_audit_gaps": audit["gaps"],
                   "measurement_boundary": "real model orchestration + deterministic pipeline/recipe repairs; normal current platform defaults, not frozen historical parameter replay",
                   "finished": now()}
        save(folder / "summary.json", summary)
        state["status"] = summary["status"]
        save(state_path, state)
        progress(case["id"], f"{summary['status']} tokens={summary['usage']['totals']['total_tokens']} ORFS={audit['launches']}")
        return summary
    except Exception as exc:
        state.update(status="incomplete", error=str(exc), updated=now())
        save(state_path, state)
        if state.get("session_id"):
            try:
                partial = get(args.api, f"/api/sessions/{state['session_id']}/usage")
                save(folder / "usage_partial.json", partial)
                state["usage"] = usage_subset(partial)
            except Exception:
                pass
        state.setdefault("orfs_make_launches", None)
        state.setdefault("orfs_wrapper_calls", None)
        save(state_path, state)
        progress(case["id"], "incomplete: " + str(exc))
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT / "case_candidates.json")
    parser.add_argument("--api", default="http://127.0.0.1:8772")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument('--recover-unsubmitted', action='store_true',
                        help='One recorded recovery turn in an existing session after a closed stream and confirmed absence of a job')
    parser.add_argument("--orfs-audit", type=Path, action="append", default=[])
    parser.add_argument("--poll-seconds", type=float, default=10)
    parser.add_argument("--job-timeout", type=float, default=28800)
    parser.add_argument("--chat-timeout", type=float, default=600)
    args = parser.parse_args()
    if args.workers < 1 or (args.limit is not None and args.limit < 1):
        parser.error("workers and limit must be positive")
    args.output = args.output.resolve()
    if not args.orfs_audit:
        args.orfs_audit = [args.output / "orfs_audit.jsonl"]
    candidates = load(args.manifest)["candidates"]
    candidates = sorted(candidates, key=lambda c: c.get("priority", 9))
    if args.limit:
        candidates = candidates[:args.limit]
    if not args.dry_run:
        get(args.api, "/api/platforms")
    args.output.mkdir(parents=True, exist_ok=True)
    lock = open(args.output / ".measurement_runner.lock", "a+")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("another measurement runner owns this output; refusing duplicate submissions")
    save(args.output / "measurement_protocol.json", {"created": now(), "api": args.api,
         "workers": args.workers, "cases": [c['id'] for c in candidates], "dry_run": args.dry_run,
         "token_measurement": "provider-reported per-call counters; missing/partial totals remain null",
         "polling": "deterministic HTTP GET, no model usage",
         "orfs_audit": [str(p) for p in args.orfs_audit],
         "scope": "current Agent source-to-signoff replay, not historical frozen-config replay"})
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_case, case, args) for case in candidates]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
            save(args.output / ("dry_run_summary.json" if args.dry_run else "measurement_summary.json"),
                 {"updated": now(), "expected": len(candidates), "reported": len(results), "cases": results})
    return 0 if all(r.get("status") in {"complete", "dry_run"} for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
