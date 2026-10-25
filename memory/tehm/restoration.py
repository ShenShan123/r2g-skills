"""Item-by-item rollback-status restoration review (P1c, user decision D-R1, 2026-10-09).

A legacy fix-log row records no rollback marker. Its status is restored to "not rolled back" ONLY by a review of
that item which links (1) the original action, its verification result and the execution it rests on, (2) a
writer code-version range whose behaviour is verified, (3) the raw evidence (file, line, digest) and why it shows
"not rolled back", and (4) the exception checks. Restorations are supplementary evidence: the original label is
never overwritten (the unit keeps ``rolled_back`` as recorded) and never upgraded to a new acceptance checklist.

Two scopes are kept apart: "kept after verification AT THIS STEP" (what the marker means) and "accidentally
reverted by a LATER step" (the stale-backup hazard of fix_signoff 7ff9523a..e57acd03), which is reviewed and
recorded separately: it never invalidates the step's own verification, but such a result is never a final,
retained fix.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

REVIEW_VERSION = "restore-v1"
# fix_signoff versions in which a later memory step without a rerun could restore a stale config backup
STALE_BACKUP_WINDOW = ("2026-10-06T22:41:00-07:00", "2026-10-08T22:59:59-07:00")

_LOG_CALL = re.compile(r"(^|[;&|]\s*|\s)_log_iter\s+\"")
_ROLLBACK = re.compile(r"(^|[;&|]\s*|\s)(_memory_rollback\b(?!\s*\(\))|_restore_accepted_bundle\b(?!\s*\(\))|"
                       r"cp -f \"\$cfg_backup\" \"\$PROJECT_DIR/constraints/config\.mk\")")
_ITER_END = re.compile(r"(^\s*|;\s*|&&\s*|\|\|\s*)(continue|return|done)\b")


def audit_writer_version(text: str) -> dict:
    """Ordering audit of one fix_signoff.sh version.

    ``log_after_rollback``: every rollback statement that shares an iteration branch with a ``_log_iter`` comes
    BEFORE it (scan: after each ``_log_iter`` call, no rollback statement until the iteration ends at
    continue/return/done -- conservative: a rollback in another branch textually after it is also flagged).
    ``cumulative_at_log_time``: _log_iter reads ``cumulative_config`` from config.mk when it logs.
    """
    lines = text.splitlines()
    calls = [i for i, ln in enumerate(lines, 1) if _LOG_CALL.search(ln) and not ln.lstrip().startswith("#")]
    rollbacks = [i for i, ln in enumerate(lines, 1) if _ROLLBACK.search(ln) and not ln.lstrip().startswith("#")]
    violations = []
    for c in calls:
        if _ITER_END.search(lines[c - 1]):
            continue
        for j in range(c + 1, len(lines) + 1):
            ln = lines[j - 1]
            if ln.lstrip().startswith("#"):
                continue
            if _ROLLBACK.search(ln):
                violations.append({"log_line": c, "rollback_line": j})
                break
            if _ITER_END.search(ln):
                break
    cum = ('cfgp=os.path.join(proj,"constraints","config.mk")' in text and
           "cumulative_config=json.dumps(cum" in text)
    return {"log_calls": calls, "rollback_lines": rollbacks, "violations": violations,
            "log_after_rollback": not violations, "cumulative_at_log_time": cum}


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def _run_time(run_name: str, tzinfo) -> datetime | None:
    m = re.match(r"RUN_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})", run_name)
    if not m:
        return None
    return datetime.fromisoformat("%sT%s:%s:%s" % m.groups()).replace(tzinfo=tzinfo)


def _cfg(text: str) -> tuple[dict, dict]:
    """(base assignments outside the fix_signoff auto block, auto-block assignments) of a config.mk."""
    base, auto, inblk = {}, {}, False
    for ln in text.splitlines():
        s = ln.strip()
        if s == "# >>> r2g signoff-fix (auto) >>>":
            inblk = True
            continue
        if s == "# <<< r2g signoff-fix (auto) <<<":
            inblk = False
            continue
        m = re.match(r"export\s+([A-Za-z_][A-Za-z0-9_]*)\s*[?:]?=\s*(.*?)\s*$", s)
        if m:
            (auto if inblk else base)[m.group(1)] = m.group(2)
    return base, auto


def _sha(path: Path) -> str | None:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def review_fix_signoff_unit(unit: dict, *, project_rows: list[tuple[int, dict]], project_dir: Path,
                            source_ref: dict, writer_audits: list[dict]) -> dict:
    """Restoration review of one verified PASS/PARTIAL unit written by fix_signoff's _log_iter.

    ``project_rows``: every (line, row) of that project's fix_log.jsonl; ``writer_audits``: audit_writer_version
    results with "commit" and "date" (ISO), oldest first, for every fix_signoff.sh version that writes this row
    format. Returns the review record (``restored`` true/false with every check and reference)."""
    from tehm.adapters.r2g_evidence import normalize_knob_edits, _parse_json_field
    row = unit["raw_row"]
    ts = _ts(row.get("ts"))
    checks, refs = {}, {"row": source_ref}
    edits = normalize_knob_edits(row.get("config_delta"), row.get("config_before"))
    # 1. the attempt: action, verification, execution
    checks["action_present"] = bool(edits)
    checks["verified_positive"] = unit["outcome"]["verified"] and unit["outcome"]["canonical"] in ("PASS", "PARTIAL")
    checks["writer_signature"] = all(k in row for k in ("env_flags", "predicates", "cumulative_config", "ts"))
    prior = [(ln, r) for ln, r in project_rows if _ts(r.get("ts")) and ts and _ts(r.get("ts")) < ts]
    prev_ts = max((_ts(r.get("ts")) for _, r in prior), default=None)
    run = None
    if ts is not None and row.get("from_stage"):
        cands = []
        for d in sorted(Path(project_dir).glob("backend/RUN_*")):
            t = _run_time(d.name, ts.tzinfo)
            if t is not None and t <= ts and (prev_ts is None or t >= prev_ts):
                cands.append((t, d))
        if cands:
            run = max(cands)[1]
    checks["execution_located"] = run is not None
    if run is not None:
        sl = run / "stage_log.jsonl"
        stages = [json.loads(x).get("stage") for x in sl.read_text().splitlines() if x.strip()] if sl.is_file() else []
        refs["execution"] = {"run": str(run), "stage_log_sha256": _sha(sl), "stages": stages}
        checks["execution_reran_from_stage"] = bool(stages) and stages[0] == row.get("from_stage")
    # 2. writer code-version range: every version that writes this row format and was committed by the row's ts
    in_range = [a for a in writer_audits if ts is not None and _ts(a["date"]) and _ts(a["date"]) <= ts]
    refs["writer_versions"] = [a["commit"] for a in in_range]
    checks["writer_range_nonempty"] = bool(in_range)
    checks["writer_logs_after_rollback"] = bool(in_range) and all(a["log_after_rollback"] for a in in_range)
    checks["writer_cumulative_at_log_time"] = bool(in_range) and all(a["cumulative_at_log_time"] for a in in_range)
    # 3. evidence: the row's own cumulative_config (config.mk auto block at log time) holds the whole edit
    cum = _parse_json_field(row.get("cumulative_config")) or {}
    checks["edit_complete_in_logged_config"] = bool(edits) and all(
        (str(cum.get(k)) == str(v["after"])) if v["after"] is not None else k not in cum for k, v in edits.items())
    # 4a. exception: the target value already held before the action (a no-op edit proves nothing)
    cfg_path = Path(project_dir) / "constraints" / "config.mk"
    base, _ = _cfg(cfg_path.read_text(errors="ignore")) if cfg_path.is_file() else ({}, {})
    prev_row = max(prior, key=lambda x: _ts(x[1].get("ts")))[1] if prior else None
    prev_cum = (_parse_json_field(prev_row.get("cumulative_config")) or {}) if prev_row else {}
    pre, pre_basis = {}, {}
    for k, v in edits.items():
        if isinstance(row.get("config_before"), dict) and k in row["config_before"]:
            pre[k], pre_basis[k] = row["config_before"][k], "row.config_before"
        elif k in prev_cum:
            pre[k], pre_basis[k] = prev_cum[k], "previous_row.cumulative_config"
        elif k in base:
            pre[k], pre_basis[k] = base[k], "project config.mk outside the auto block (current file)"
        else:
            pre[k], pre_basis[k] = None, "unset (absent from the auto block and the base config)"
    refs["pre_action_values"] = {"values": pre, "basis": pre_basis,
                                 "config_mk": {"path": str(cfg_path), "sha256": _sha(cfg_path)}}
    # a multi-knob action may restate a knob at its current value: such knobs carry no information either way, so
    # the effective edit is the CHANGED knobs (recorded); an action that changes nothing is never restored
    noop = sorted(k for k, v in edits.items() if str(pre[k]) == str(v["after"]))
    refs["noop_knobs"] = noop
    checks["edit_changes_a_value"] = bool(edits) and len(noop) < len(edits)
    required = ("action_present", "verified_positive", "writer_signature", "execution_located",
                "execution_reran_from_stage", "writer_range_nonempty", "writer_logs_after_rollback",
                "writer_cumulative_at_log_time", "edit_complete_in_logged_config", "edit_changes_a_value")
    restored = all(checks.get(c) for c in required)
    # 4b. separately: a later step reverting the edit (stale-backup window)
    # only later rows of the SAME writer: another writer's cumulative_config has another meaning (engineer_loop
    # records dump the whole config, not the fix_signoff auto block)
    later = [r for _, r in project_rows if _ts(r.get("ts")) and ts and _ts(r.get("ts")) > ts
             and all(k in r for k in ("env_flags", "predicates", "cumulative_config"))]
    in_window = ts is not None and _ts(STALE_BACKUP_WINDOW[0]) <= ts <= _ts(STALE_BACKUP_WINDOW[1])
    changed = {k: v for k, v in edits.items() if k not in noop}
    reverted = [r.get("ts") for r in later if any(
        k not in (normalize_knob_edits(r.get("config_delta")) or {}) and
        str((_parse_json_field(r.get("cumulative_config")) or {}).get(k)) != str(v["after"])
        for k, v in changed.items())]
    later_review = {"in_stale_backup_window": in_window, "later_rows": len(later),
                    "later_rows_without_the_edit": reverted[:5],
                    "status": ("reverted_by_a_later_step" if reverted else
                               ("no_later_reversal_observed" if later else "no_later_rows"))}
    return {"review": REVIEW_VERSION, "unit_id": unit["unit_id"], "kind": "fix_signoff_log_after_rollback",
            "restored": restored, "restored_status": "false" if restored else "UNKNOWN", "checks": checks,
            "required": list(required), "refs": refs,
            "scope": "this step was not rolled back after its own verification (rollback marker semantics); "
                     "not a claim that the edit was retained to the end of the task",
            "later_step_review": later_review,
            # a stale-backup restore happens inside a later memory step, which then logs its own row with the
            # post-restore cumulative_config -- so later same-writer rows that all keep the edit show it retained
            "final_retained_fix": (None if not restored else
                                   (False if reverted else ("unknown" if not later else True)))}


def review_trial_unit(unit: dict, *, trial_project: Path | None, harness_audit: dict, source_ref: dict) -> dict:
    """Restoration review of one component trial: the result must come from the trial project AFTER the action
    (its config holds the trial value, and a backend run of that project carries the stage outcome), and the
    trial harness at its code version must have no rollback step."""
    row = unit["raw_row"]
    checks, refs = {}, {"row": source_ref, "harness": harness_audit}
    checks["verified_positive"] = unit["outcome"]["verified"] and unit["outcome"]["canonical"] in ("PASS", "PARTIAL")
    checks["trial_project_found"] = trial_project is not None and Path(trial_project).is_dir()
    edits = row.get("config_delta") or {}
    if checks["trial_project_found"]:
        cfg = Path(trial_project) / "constraints" / "config.mk"
        base, auto = _cfg(cfg.read_text(errors="ignore")) if cfg.is_file() else ({}, {})
        eff = {**base, **auto}
        refs["trial_config"] = {"path": str(cfg), "sha256": _sha(cfg),
                                "values": {k: eff.get(k) for k in edits}}
        checks["trial_config_holds_the_edit"] = bool(edits) and all(str(eff.get(k)) == str(v) for k, v in edits.items())
        runs = sorted(Path(trial_project).glob("backend/RUN_*"))
        refs["runs"] = [r.name for r in runs]
        checks["trial_run_after_action_found"] = bool(runs)
        if runs:
            sl = runs[-1] / "stage_log.jsonl"
            st = [json.loads(x) for x in sl.read_text().splitlines() if x.strip()] if sl.is_file() else []
            refs["run_stage_log"] = {"run": runs[-1].name, "sha256": _sha(sl),
                                     "stages": [(s.get("stage"), s.get("status")) for s in st]}
            ok = bool(st) and all(str(s.get("status")) in ("0", "pass") for s in st)
            checks["run_outcome_matches_row"] = ok == bool(row.get("stage_ok"))
    checks["harness_has_no_rollback"] = bool(harness_audit.get("no_rollback"))
    required = ("verified_positive", "trial_project_found", "trial_config_holds_the_edit",
                "trial_run_after_action_found", "run_outcome_matches_row", "harness_has_no_rollback")
    restored = all(checks.get(c) for c in required)
    return {"review": REVIEW_VERSION, "unit_id": unit["unit_id"], "kind": "component_trial_isolated",
            "restored": restored, "restored_status": "false" if restored else "UNKNOWN", "checks": checks,
            "required": list(required), "refs": refs,
            "scope": "the trial's edit was evaluated and kept in its own trial project (no rollback mechanism)",
            "final_retained_fix": None}


# -- engineer_loop recovery records ----------------------------------------------------------------------------

_RECORD_CALL = re.compile(r"_record_\w+_fix\(")
_RESTORE_ANY = re.compile(r"_restore_\w+\(|rollback|revert|cfg_backup|_memory_rollback")


def audit_engineer_loop_version(text: str) -> dict:
    """Every recovery that writes a fix-log row (``_record_*_fix``) snapshots the post-edit config
    (``_config_effect``) BEFORE the retry (``process_one``) and runs no restore/rollback/revert between the edit
    and the record. (``_restore_structured_delta_before`` re-materialises A/B arm copies only and never appears in
    a recovery block.)"""
    lines = text.splitlines()
    blocks, bad = [], []
    for i, ln in enumerate(lines):
        if not _RECORD_CALL.search(ln) or ln.lstrip().startswith(("def ", "#")):
            continue
        lo = max(0, i - 40)
        window = lines[lo:i + 1]
        eff = max((j for j, w in enumerate(window) if "_config_effect(" in w or "_r2g_config_effect" in w), default=None)
        run = max((j for j, w in enumerate(window) if "process_one(" in w), default=None)
        restore = [lo + j + 1 for j, w in enumerate(window) if _RESTORE_ANY.search(w) and not w.lstrip().startswith("#")]
        ok = eff is not None and run is not None and eff < run and not restore
        blocks.append({"record_line": i + 1, "effect_before_retry": eff is not None and run is not None and eff < run,
                       "restore_lines": restore})
        if not ok:
            bad.append(i + 1)
    return {"recovery_blocks": blocks, "violations": bad, "ok": bool(blocks) and not bad}


def review_engineer_loop_unit(unit: dict, *, project_rows: list[tuple[int, dict]], project_dir: Path,
                              source_ref: dict, executor_audits: list[dict]) -> dict:
    """An engineer_loop recovery row: the edit, the retry it was verified by, and an executor that never restores
    the edit. The row's cumulative_config is the post-edit snapshot taken BEFORE the retry, so it shows the edit
    was in place when verified; "not rolled back" then rests on the audited executor."""
    from tehm.adapters.r2g_evidence import _parse_json_field
    row = unit["raw_row"]
    ts = _ts(row.get("ts"))
    checks, refs = {}, {"row": source_ref}
    delta = _parse_json_field(row.get("config_delta")) or {}
    checks["verified_positive"] = unit["outcome"]["verified"] and unit["outcome"]["canonical"] in ("PASS", "PARTIAL")
    checks["writer_signature"] = all(k in row for k in ("before_status", "effect_fingerprint", "cumulative_config", "ts"))
    checks["edit_changes_values"] = bool(delta) and all(isinstance(v, dict) and v.get("before") != v.get("after")
                                                        for v in delta.values())
    snap = _parse_json_field(row.get("cumulative_config")) or {}
    checks["edit_in_post_edit_snapshot"] = bool(delta) and all(
        snap.get(k) == (v or {}).get("after") for k, v in delta.items())
    # bound by the previous row of the SAME writer: the retry (process_one) runs fix_signoff itself, whose rows are
    # logged BEFORE this recovery row, so "the previous row" would exclude the retry's own run
    prior = [r for _, r in project_rows if _ts(r.get("ts")) and ts and _ts(r.get("ts")) < ts
             and all(k in r for k in ("before_status", "effect_fingerprint"))]
    prev_ts = max((_ts(r.get("ts")) for r in prior), default=None)
    cands = []
    if ts is not None:
        for d in sorted(Path(project_dir).glob("backend/RUN_*")):
            t = _run_time(d.name, ts.tzinfo)
            if t is not None and t <= ts and (prev_ts is None or t >= prev_ts):
                cands.append((t, d))
    run = max(cands)[1] if cands else None
    checks["execution_located"] = run is not None
    if run is not None:
        sl = run / "stage_log.jsonl"
        st = [json.loads(x) for x in sl.read_text().splitlines() if x.strip()] if sl.is_file() else []
        refs["execution"] = {"run": str(run), "stage_log_sha256": _sha(sl),
                             "stages": [(s.get("stage"), s.get("status")) for s in st]}
        checks["execution_clean_matches_row"] = bool(st) and all(str(s.get("status")) in ("0", "pass") for s in st)
    in_range = [a for a in executor_audits if ts is not None and _ts(a["date"]) and _ts(a["date"]) <= ts]
    refs["executor_versions"] = [a["commit"] for a in in_range]
    checks["executor_range_nonempty"] = bool(in_range)
    checks["executor_never_restores_the_edit"] = bool(in_range) and all(a["ok"] for a in in_range)
    required = ("verified_positive", "writer_signature", "edit_changes_values", "edit_in_post_edit_snapshot",
                "execution_located", "execution_clean_matches_row", "executor_range_nonempty",
                "executor_never_restores_the_edit")
    restored = all(checks.get(c) for c in required)
    later = [r for _, r in project_rows if _ts(r.get("ts")) and ts and _ts(r.get("ts")) > ts
             and all(k in r for k in ("before_status", "effect_fingerprint", "cumulative_config"))]
    back = [r.get("ts") for r in later if any(
        k not in (_parse_json_field(r.get("config_delta")) or {}) and
        (_parse_json_field(r.get("cumulative_config")) or {}).get(k) == (v or {}).get("before")
        for k, v in delta.items())]
    return {"review": REVIEW_VERSION, "unit_id": unit["unit_id"], "kind": "engineer_loop_recovery",
            "restored": restored, "restored_status": "false" if restored else "UNKNOWN", "checks": checks,
            "required": list(required), "refs": refs,
            "scope": "the recovery edit was in place when the retry verified it and the executor never restores it",
            "later_step_review": {"later_rows": len(later), "later_rows_back_at_pre_value": back[:5],
                                  "status": "reverted_by_a_later_step" if back else
                                  ("no_later_reversal_observed" if later else "no_later_rows")},
            "final_retained_fix": (None if not restored else (False if back else ("unknown" if not later else True)))}


# -- LLM rows (live llm_assist, and seed copies of the original LLM-arm records) ---------------------------------

def audit_llm_harness_version(text: str) -> dict:
    """The LLM loops (``llm_assist``; ``run_llm``, the rounds-1/2 L arm) apply an edit, run and record; none
    restores it."""
    out = {}
    for fn in ("llm_assist", "run_llm", "apply_llm"):
        m = re.search(r"\ndef %s\(.*?(?=\ndef )" % fn, text, re.S)
        body = m.group(0) if m else ""
        out[fn] = {"present": bool(body),
                   "restore_lines": [ln.strip() for ln in body.splitlines()
                                     if _RESTORE_ANY.search(ln) and not ln.lstrip().startswith("#")]}
    loops = [out[f] for f in ("llm_assist", "run_llm") if out[f]["present"]]
    out["ok"] = bool(loops) and not any(x["restore_lines"] for x in loops)
    return out


def effective_config(text: str) -> dict:
    """Last assignment wins (the fix_signoff auto block overrides the base value)."""
    out = {}
    for ln in (text or "").splitlines():
        m = re.match(r"\s*export\s+([A-Za-z_][A-Za-z0-9_]*)\s*[?:]?=\s*(.*?)\s*$", ln)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def review_llm_unit(unit: dict, *, matches: list[dict], harness_audits: list[dict], source_ref: dict) -> dict:
    """``matches``: the run records whose LLM iteration has exactly this row's edit and closure result
    (each {"record": path, "iteration": dict, "final_config": text, "judged_run": path|None, "date": iso|None}).
    Restored only from a UNIQUE match whose judged final config still holds the edit."""
    row = unit["raw_row"]
    checks, refs = {}, {"row": source_ref, "record_matches": [m["record"] for m in matches]}
    edits = {k: v for k, v in (row.get("config_delta") or {}).items()}
    checks["verified_positive"] = unit["outcome"]["verified"] and unit["outcome"]["canonical"] in ("PASS", "PARTIAL")
    checks["unique_run_record"] = len(matches) == 1
    m = matches[0] if len(matches) == 1 else None
    if m is not None:
        final = effective_config(m["final_config"])
        refs["final_config"] = {"record": m["record"], "values": {k: final.get(k) for k in edits}}
        checks["final_config_holds_the_edit"] = bool(edits) and all(
            (final.get(k) == str(v)) if v is not None else k not in final for k, v in edits.items())
        before = row.get("config_before") or {}
        noop = sorted(k for k, v in edits.items() if k in before and str(before[k]) == str(v))
        refs["noop_knobs"] = noop
        checks["edit_changes_a_value"] = bool(edits) and len(noop) < len(edits)
        checks["execution_located"] = bool(m.get("judged_run")) and Path(m["judged_run"]).is_dir()
        refs["execution"] = m.get("judged_run")
        ts = m.get("date")
        in_range = [a for a in harness_audits if not ts or (_ts(a["date"]) and _ts(ts) and _ts(a["date"]) <= _ts(ts))]
        refs["harness_versions"] = [a["commit"] for a in in_range]
        checks["harness_never_restores_the_edit"] = bool(in_range) and all(a["ok"] for a in in_range)
    required = ("verified_positive", "unique_run_record", "final_config_holds_the_edit", "edit_changes_a_value",
                "execution_located", "harness_never_restores_the_edit")
    restored = all(checks.get(c) for c in required)
    return {"review": REVIEW_VERSION, "unit_id": unit["unit_id"], "kind": "llm_run_record",
            "restored": restored, "restored_status": "false" if restored else "UNKNOWN", "checks": checks,
            "required": list(required), "refs": refs,
            "scope": "the LLM edit is still in the run's judged final config (kept to the end of the task)",
            "final_retained_fix": True if restored else None}
