"""R2G real-evidence capture adapter (design doc 21.2, 20.4).

Reads a real R2G project dir the way the legacy loop leaves it and emits one
canonical ``ExecutionRecord`` per fix-log iteration:

    project/
      constraints/config.mk            -> before/after config (cumulative snapshots)
      constraints/constraint.sdc
      reports/{drc,lvs,rcx,timing_check,route,ppa}.json  -> state reports
      reports/fix_log.jsonl            -> one row per repair iteration (transition)
      backend/RUN_*/stage_log.jsonl    -> run/stage metadata

Each ``fix_log.jsonl`` row (the exact schema ``fix_signoff.sh _log_iter``
writes) becomes one Verified State Transition: before = pre-fix snapshot,
action = strategy + config_delta, after = post-fix snapshot, observation delta
and verification derived from the row's own verdict/counts — never fabricated.

Honesty notes:
  * No per-iteration report files are kept on disk, so non-target reports reuse
    the final on-disk snapshot; the target check's before/after counts come from
    the fix_log row itself (the authoritative per-iteration evidence).
  * ``original_failure`` / outcome are derived only from observed verdicts and
    counts; anything unknown stays UNKNOWN (H3).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from tehm.canonical.capture import CaptureReceipt, ExecutionRecord, capture

SIGNOFF_CHECKS = ("drc", "lvs", "rcx", "timing_check", "route")
CLEAN_VERDICTS = frozenset({"cleared", "win"})
CLEAN_STATUSES = frozenset({"clean", "clean_beol", "complete", "skipped"})

# check name -> oracle type (design doc 12 tiers; signoff checks = regression scope)
_ORACLE_TYPE = {
    "drc": "REGRESSION",
    "lvs": "REGRESSION",
    "rcx": "REGRESSION",
    "timing": "TARGET_TEST",
    "timing_check": "TARGET_TEST",
    "route": "TARGET_TEST",
    "orfs_stage": "TARGET_TEST",
}


def capture_r2g_project(conn, store, project: Path,
                        *, materialized_at: str | None = None) -> list[CaptureReceipt]:
    """Capture every repair transition from a real R2G project dir.

    Returns one ``CaptureReceipt`` per captured transition (empty when the
    project has no ``reports/fix_log.jsonl`` — a clean run produces no
    transition, and nothing is fabricated).
    """
    evidence = collect_execution_evidence(project)
    records = build_execution_records(evidence)
    receipts = []
    for record in records:
        receipts.append(capture(conn, store, record, materialized_at=materialized_at))
    return receipts


# ---------------------------------------------------------------------------
# Evidence collection
# ---------------------------------------------------------------------------

def collect_execution_evidence(project: Path) -> dict:
    """Normalize the on-disk evidence of one project dir."""
    project = Path(project)
    return {
        "project": project,
        "design_name": None,
        "platform": None,
        "config": parse_config_mk(_maybe_read(project / "constraints" / "config.mk")),
        "reports": read_reports(project),
        "stage_log": read_stage_log(project),
        "fix_log": read_fix_log(project),
    }


def parse_config_mk(text: str) -> dict:
    """Parse ``export KEY = value`` / ``export KEY=value`` lines (skip comments)."""
    cfg: dict = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.startswith("export "):
            continue
        kv = stripped[len("export "):]
        if "=" not in kv:
            continue
        key, _, value = kv.partition("=")
        cfg[key.strip()] = value.strip()
    return cfg


def read_reports(project: Path) -> dict:
    reports: dict = {}
    reports_dir = project / "reports"
    if not reports_dir.is_dir():
        return reports
    for name in SIGNOFF_CHECKS + ("ppa", "diagnosis"):
        path = reports_dir / f"{name}.json"
        if path.exists():
            data = _load_json(path)
            if isinstance(data, dict):
                reports[name] = data
    return reports


def read_stage_log(project: Path) -> list[dict]:
    lines = _read_jsonl(project / "backend" / "stage_log.jsonl")
    # stage_log.jsonl may live under backend/RUN_*/; try the newest run dir.
    if not lines:
        backend = project / "backend"
        if backend.is_dir():
            runs = sorted(backend.glob("RUN_*"))
            for run in reversed(runs):
                lines = _read_jsonl(run / "stage_log.jsonl")
                if lines:
                    break
    return lines


def read_fix_log(project: Path) -> list[dict]:
    return _read_jsonl(project / "reports" / "fix_log.jsonl")


# ---------------------------------------------------------------------------
# ExecutionRecord construction
# ---------------------------------------------------------------------------

def build_execution_records(evidence: dict) -> list[ExecutionRecord]:
    """One ExecutionRecord per fix-log iteration, grouped by fix session."""
    config = evidence["config"]
    reports = evidence["reports"]
    fix_log = evidence["fix_log"]
    project = evidence["project"]
    design_name = config.get("DESIGN_NAME")
    platform = config.get("PLATFORM")

    records: list[ExecutionRecord] = []
    if not fix_log:
        return records

    sessions: dict[str, list[dict]] = {}
    for row in fix_log:
        sid = str(row.get("fix_session_id") or "unknown_session")
        sessions.setdefault(sid, []).append(row)

    for sid, rows in sessions.items():
        rows.sort(key=lambda r: int(r.get("iter", 0) or 0))
        prev_cumulative: dict | None = None
        for i, row in enumerate(rows):
            record = _iter_record(
                project=project, config=config, reports=reports, row=row,
                prev_cumulative=prev_cumulative, design_name=design_name,
                platform=platform, session_id=sid, step_index=i,
                is_terminal=(i == len(rows) - 1), all_verdicts=[r.get("verdict") for r in rows])
            records.append(record)
            cumulative = _parse_json_field(row.get("cumulative_config")) or \
                {**config, **(_parse_json_field(row.get("config_delta")) or {})}
            prev_cumulative = cumulative
    return records


def _iter_record(*, project, config, reports, row, prev_cumulative, design_name,
                 platform, session_id, step_index, is_terminal, all_verdicts) -> ExecutionRecord:
    check = str(row.get("check") or "unknown")
    strategy = str(row.get("strategy") or "unknown")
    before_count = row.get("before")
    after_count = row.get("after")
    verdict = str(row.get("verdict") or "unknown")
    after_status = str(row.get("after_status") or "") or _status_from_count(after_count)
    config_delta = _parse_json_field(row.get("config_delta")) or {}
    cumulative = _parse_json_field(row.get("cumulative_config")) or {}
    global_regressions = _parse_json_field(row.get("global_regressions")) or []

    after_config = {**config, **cumulative} if cumulative else {**config, **config_delta}
    before_config = dict(prev_cumulative) if prev_cumulative else dict(config)

    before_categories = _parse_json_field(row.get("before_categories")) or {}
    before_reports = _state_reports(
        reports, check, _status_from_count(before_count), before_count,
        before_categories if isinstance(before_categories, dict) else {})
    after_reports = _state_reports(reports, check, after_status, after_count, {})

    original_failure = _original_failure(verdict, after_count)
    delta = {
        "original_failure": original_failure,
        "first_divergence": {"before": before_count, "after": after_count},
        "failing_tests": {
            "before": 1 if before_count else 0,
            "after": 1 if after_count else 0,
        },
        "created_regressions": list(global_regressions),
        "newly_observed_failures": [],
        "experiment_kind": ("REPAIR" if original_failure in {"REMOVED", "PRESENT"}
                            else "OBSERVATION"),
        "utility_verdict": "UNKNOWN",
    }

    oracle_type = _ORACLE_TYPE.get(check, "UNKNOWN")
    verification = {
        "verdict": _verifier_verdict(verdict, after_status),
        "oracle_type": oracle_type,
        "scope": f"signoff:{check}",
        "confidence_tier": {"REGRESSION": "R", "TARGET_TEST": "T"}.get(oracle_type, "H"),
        "obligation_coverage": _obligation_coverage(after_reports),
        "evidence_refs": [c for c in SIGNOFF_CHECKS if c in after_reports],
    }

    rerun_from = row.get("from_stage") or None
    action = {
        "domain": _action_domain(config_delta, rerun_from),
        "transformation_family": _normalize_family(strategy),
        "payload": {
            "strategy": strategy,
            "config_edits": config_delta,
            "rerun_from": rerun_from,
            "recheck": check,
            "dependency_cone_changed": bool(rerun_from),
            "register_boundary_changed": False,
        },
    }

    terminal_status = _terminal_status(is_terminal, all_verdicts, original_failure)
    before_content = {
        "repository_ref": None,
        "config": before_config,
        "reports": before_reports,
        "artifacts": {},
        "failure_signature": {
            "check": check,
            "class": row.get("violation_class"),
            "predicates": _parse_json_field(row.get("predicates")) or {},
        },
    }
    after_content = {
        "repository_ref": None,
        "config": after_config,
        "reports": after_reports,
        "artifacts": {},
    }

    return ExecutionRecord(
        record_id=f"r2g:{session_id}:iter{step_index}:{_normalize_family(strategy).lower()}",
        domain="flow.signoff",
        project_id=design_name,
        design_id=design_name,
        lineage_id=design_name,
        repository_ref=None,
        before=before_content,
        action=action,
        after=after_content,
        observation_delta=delta,
        verification=verification,
        episode={
            "episode_id": session_id,
            "mechanism_family": _normalize_family(strategy),
            "lineage_id": design_name,
            "step_index": step_index,
            "terminal_status": terminal_status,
        },
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _state_reports(base_reports: dict, check: str, status: str, count,
                   categories: dict) -> dict:
    reports = {k: dict(v) for k, v in (base_reports or {}).items()}
    report = {"status": status or "unknown"}
    if count is not None:
        report["total_violations"] = count
    if categories:
        report["categories"] = categories
    reports[check] = report
    return reports


def _status_from_count(count) -> str:
    if count is None:
        return "unknown"
    return "clean" if int(count) == 0 else "violations"


def _original_failure(verdict: str, after_count) -> str:
    if verdict in CLEAN_VERDICTS:
        return "REMOVED"
    if after_count is not None and int(after_count) > 0:
        return "PRESENT"
    return "UNKNOWN"


def _verifier_verdict(verdict: str, after_status: str) -> str:
    """Map a fix-log verdict to the transition verifier verdict.

    Only true clears / true failures / regressions claim PASS/FAIL; a partial
    improvement (``applied`` / ``no_improvement`` with residual violations) is
    honestly UNKNOWN — it is neither a pass nor a failure (H3).
    """
    if verdict in CLEAN_VERDICTS or after_status in CLEAN_STATUSES:
        return "PASS"
    if verdict == "regression":
        return "FAIL"
    if verdict in ("apply_failed", "rerun_failed") or after_status in (
            "stuck", "timeout", "crash", "failed"):
        return "FAIL"
    return "UNKNOWN"


def _action_domain(config_delta: dict, rerun_from) -> str:
    if config_delta and rerun_from:
        return "signoff.REPAIR_ACTION"
    if config_delta:
        return "flow.CONFIG_DELTA"
    if rerun_from:
        return "flow.STAGE_RERUN"
    return "unknown"


def _normalize_family(strategy: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", strategy).strip("_")
    return s.upper()


def _obligation_coverage(after_reports: dict) -> float | None:
    present = [c for c in SIGNOFF_CHECKS if c in after_reports]
    if not present:
        return None
    return len(present) / len(SIGNOFF_CHECKS)


def _terminal_status(is_terminal: bool, all_verdicts: list,
                     original_failure: str = "UNKNOWN") -> str:
    if not is_terminal:
        return "OPEN"
    if any(v in CLEAN_VERDICTS for v in all_verdicts):
        return ("VERIFIED_REPAIR" if original_failure in {"REMOVED", "PRESENT"}
                else "VERIFIED_OBSERVATION")
    if any(v == "no_improvement" for v in all_verdicts):
        return "ABANDONED"
    return "PARTIAL"


def _parse_json_field(text) -> dict | list:
    if text is None:
        return {}
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {}


def _maybe_read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
