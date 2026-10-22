"""ann-v2 typed measurements of ONE bound run (spec §2, §3).

Raw values with units only (no bands). Every field is a record
{value, status: KNOWN|UNKNOWN|NOT_APPLICABLE, unknown_reason, source[{path, locator, sha256}],
method}. UNKNOWN never holds a value; KNOWN always names a hashed source. A check whose
evidence is absent is `measured: False` with `present: None` -- never "no failure".
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ANNOTATOR_VERSION = "ann-v2-dev.7"
_KNOWLEDGE = Path(__file__).resolve().parents[2] / "r2g-skills" / "signoff-loop" / "knowledge"
if str(_KNOWLEDGE) not in sys.path:
    sys.path.insert(0, str(_KNOWLEDGE))
import situation as _SIT  # noqa: E402  (same error-code / timeout rules as sit-v1/v2)

UNKNOWN_REASONS = ("no_explicit_link", "ambiguous_binding", "contradicted_binding", "predecessor_unbound",
                   "predecessor_rollback_unknown", "anchor_unbound", "run_missing_on_disk",
                   "row_missing_on_disk", "export_only", "metric_absent", "conflicting_sources",
                   "copy_conflict", "binary_flag_only", "not_measured", "not_recorded")
CHECKS = ("stage", "route_residual", "drc", "lvs", "setup", "hold", "drv")
STAGE_ORDER = ("synth", "floorplan", "place", "cts", "route", "finish")
UNCONSTRAINED_WS = 1e30   # ORFS reports 1e+39 slack when no path is constrained (check_timing tier
#                            'unconstrained'); never a measured "met" (user decision 2026-10-18)
_GRT_TOTAL = re.compile(r"^Total\s+\d+\s+\d+\s+[\d.]+%\s+\d+\s*/\s*\d+\s*/\s*(\d+)\s*$")  # = severity.py
_GRT_ERROR = re.compile(r"\[ERROR GRT-\d+\]")
_ROUTE_MARKER = re.compile(r"violation type", re.IGNORECASE)
_SHA_CACHE: dict = {}


def sha256_of(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _SHA_CACHE:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _SHA_CACHE[key] = h.hexdigest()
    return _SHA_CACHE[key]


def src(path: Path, locator: str) -> dict:
    return {"path": str(path), "locator": locator, "sha256": sha256_of(Path(path))}


def known(value, sources: list, method: str) -> dict:
    return {"value": value, "status": "KNOWN", "unknown_reason": None, "source": sources,
            "method": f"{method}@{ANNOTATOR_VERSION}"}


def unknown(reason: str, method: str, sources: list | None = None) -> dict:
    assert reason in UNKNOWN_REASONS, reason
    return {"value": None, "status": "UNKNOWN", "unknown_reason": reason, "source": sources or [],
            "method": f"{method}@{ANNOTATOR_VERSION}"}


def na() -> dict:
    return {"value": None, "status": "NOT_APPLICABLE", "unknown_reason": None, "source": [], "method": None}


def _json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _jsonl(path: Path) -> list:
    try:
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def _metric(report: dict | None, path: Path, key: str, method: str) -> dict:
    if isinstance(report, dict) and isinstance(report.get(key), (int, float)):
        return known(report[key], [src(path, key)], method)
    return unknown("metric_absent", method)


def _time_unit(run: Path) -> dict:
    for f in sorted((run / "logs").glob("*.json")):
        d = _json(f)
        if isinstance(d, dict) and d.get("run__flow__platform__time_units"):
            return known(d["run__flow__platform__time_units"], [src(f, "run__flow__platform__time_units")],
                         "orfs_metrics")
    return unknown("metric_absent", "orfs_metrics")


def _global_route(run: Path) -> dict:
    """GRT overflow (the router's Total congestion line) and whether global routing ended
    clean (no GRT error and zero overflow). Labels v2, ann-v2-dev.6."""
    log = run / "logs" / "5_1_grt.log"
    try:
        lines = log.read_text(errors="ignore").splitlines()
    except OSError:
        return {"grt_overflow": unknown("metric_absent", "grt_log"),
                "global_route_clean": unknown("metric_absent", "grt_log")}
    tot = [(i, m.group(1)) for i, ln in enumerate(lines, 1) for m in [_GRT_TOTAL.match(ln.strip())] if m]
    err = [i for i, ln in enumerate(lines, 1) if _GRT_ERROR.search(ln)]
    ov = known(int(tot[-1][1]), [src(log, f"line {tot[-1][0]}: Total congestion")], "grt_log") if tot \
        else unknown("metric_absent", "grt_log")
    if err:
        clean = known(False, [src(log, f"line {err[0]}: GRT error")], "grt_log")
    elif tot:
        clean = known(int(tot[-1][1]) == 0, [src(log, f"line {tot[-1][0]}: Total congestion")], "grt_log")
    else:
        clean = unknown("metric_absent", "grt_log")
    return {"grt_overflow": ov, "global_route_clean": clean}


def _stage(run: Path) -> dict:
    log = run / "stage_log.jsonl"
    rows = _jsonl(log)
    if not rows:
        return {"measured": False, "present": None, "type_status": None, "fields": {}}
    last = rows[-1]
    present = last.get("status") not in (0, "0", "pass")
    loc = f"line {len(rows)}"
    reached = known([r.get("stage") for r in rows], [src(log, "all lines: stage")], "stage_log")
    passed = known([r.get("stage") for r in rows if r.get("status") in (0, "0", "pass")],
                   [src(log, "all lines: stage,status")], "stage_log")
    if not present:
        return {"measured": True, "present": False, "type_status": None,
                "fields": {"stage": na(), "exit": na(), "error_code": na(), "timeout": na(),
                           "reached": reached, "passed": passed, "grt_overflow": na(),
                           "global_route_clean": na()}}
    code = unknown("metric_absent", "flow_log")
    flow = run / "flow.log"
    try:
        tail = flow.read_text(errors="ignore").splitlines()[-500:]
    except OSError:
        tail = []
    for i, ln in enumerate(tail):
        m = _SIT.ORFS_ERRCODE_RE.search(ln)
        if m:
            code = known(m.group(1), [src(flow, f"tail-500 line {i + 1}")], "flow_log")
            break
    try:
        timeout = known(int(last.get("status")) in _SIT.TIMEOUT_RCS, [src(log, loc + ": status")], "stage_log")
    except (TypeError, ValueError):
        timeout = unknown("metric_absent", "stage_log")
    gr = _global_route(run) if last.get("stage") == "route" else {"grt_overflow": na(), "global_route_clean": na()}
    return {"measured": True, "present": True, "type_status": "KNOWN",
            "fields": {"stage": known(last.get("stage"), [src(log, loc + ": stage")], "stage_log"),
                       "exit": known(last.get("status"), [src(log, loc + ": status")], "stage_log"),
                       "error_code": code, "timeout": timeout, "reached": reached, "passed": passed, **gr}}


def _route_residual(run: Path) -> dict:
    rows = _jsonl(run / "stage_log.jsonl")
    rpt = run / "reports_orfs" / "5_route_drc.rpt"
    routed = any(r.get("stage") == "route" and r.get("status") in (0, "0") for r in rows)
    if not routed or not rpt.exists():
        return {"measured": False, "present": None, "type_status": None, "fields": {}}
    text = rpt.read_text(errors="ignore")
    count = len(_ROUTE_MARKER.findall(text)) if text.strip() else 0
    if text.strip() and count == 0:                       # content without a known marker
        count = sum(1 for ln in text.splitlines() if ln.strip())
    return {"measured": True, "present": count > 0, "type_status": "KNOWN" if count > 0 else None,
            "fields": {"count": known(count, [src(rpt, "violation type markers")], "route_drc_rpt")}}


def _drc(run: Path) -> dict:
    path = run / "drc" / "drc_result.json"
    d = _json(path)
    try:
        count = int(str(d.get("violations"))) if isinstance(d, dict) else None
    except ValueError:
        count = None
    if count is None:
        return {"measured": False, "present": None, "type_status": None, "fields": {}}
    checker = (known(d["checker"], [src(path, "checker")], "drc_result") if d.get("checker")
               else unknown("metric_absent", "drc_result"))
    return {"measured": True, "present": count > 0,
            "type_status": ("KNOWN" if checker["status"] == "KNOWN" else "UNKNOWN") if count > 0 else None,
            "fields": {"count": known(count, [src(path, "violations")], "drc_result"), "checker": checker}}


def _lvs(run: Path) -> dict:
    path = run / "lvs" / "netgen_lvs_result.json"
    d = _json(path)
    status = d.get("status") if isinstance(d, dict) else None
    if status in (None, "", "unknown", "error"):
        return {"measured": False, "present": None, "type_status": None, "fields": {}}
    present = status != "clean"
    mc = d.get("mismatch_class")
    return {"measured": True, "present": present, "type_status": "KNOWN" if present else None,
            "fields": {"status": known(status, [src(path, "status")], "netgen_lvs_result"),
                       "checker": (known(d["tool"], [src(path, "tool")], "netgen_lvs_result") if d.get("tool")
                                   else unknown("metric_absent", "netgen_lvs_result")),
                       "mismatch_class": (known(mc, [src(path, "mismatch_class")], "netgen_lvs_result") if mc
                                          else (na() if not present else unknown("metric_absent",
                                                                                 "netgen_lvs_result")))}}


def _slack(run: Path, kind: str, unit: dict) -> dict:
    path = run / "logs" / "6_report.json"
    rep = _json(path)
    ws = _metric(rep, path, f"finish__timing__{kind}__ws", "orfs_metrics")
    if ws["status"] != "KNOWN":
        return {"measured": False, "present": None, "type_status": None, "fields": {}}
    if abs(ws["value"]) >= UNCONSTRAINED_WS:    # no constrained path: not a measurement
        return {"measured": False, "present": None, "type_status": None, "fields": {}, "unconstrained": True}
    present = ws["value"] < 0
    fields = {"metric": known(f"{kind}_ws", [src(path, f"finish__timing__{kind}__ws")], "orfs_metrics"),
              "unit": unit, "value": ws,
              "stage": known("finish", [src(path, "finish__ prefix")], "orfs_metrics"),
              "tns": _metric(rep, path, f"finish__timing__{kind}__tns", "orfs_metrics"),
              "violators": _metric(rep, path, f"finish__timing__drv__{kind}_violation_count", "orfs_metrics")}
    return {"measured": True, "present": present,
            "type_status": ("KNOWN" if unit["status"] == "KNOWN" else "UNKNOWN") if present else None,
            "fields": fields}


def _drv(run: Path) -> dict:
    path = run / "logs" / "6_report.json"
    rep = _json(path)
    subs = {k: _metric(rep, path, f"finish__timing__drv__{k}", "orfs_metrics")
            for k in ("max_slew", "max_cap", "max_fanout")}
    if all(f["status"] != "KNOWN" for f in subs.values()):
        return {"measured": False, "present": None, "type_status": None, "fields": {}}
    present = any(f["status"] == "KNOWN" and f["value"] > 0 for f in subs.values())
    return {"measured": True, "present": present, "type_status": "KNOWN" if present else None,
            "fields": {**subs, "stage": known("finish", [src(path, "finish__ prefix")], "orfs_metrics")}}


def finished_here(run: Path) -> bool:
    """True only when THIS run's own stage log shows the finish stage passing. ann-v2-dev.7
    (failure-patterns #61): a rerun that aborted early carried the previous flow's
    logs/6_report.json (copied with fresh mtimes), so the finish report is evidence of this run
    only when this run finished."""
    return any(r.get("stage") == "finish" and r.get("status") in (0, "0", "pass")
               for r in _jsonl(run / "stage_log.jsonl"))


_NOT_THIS_RUN = {"measured": False, "present": None, "type_status": None, "fields": {},
                 "finish_report": "not_produced_by_this_run"}


def measure(run_dir) -> dict:
    """Typed measurements of one run: {check: {measured, present, type_status, fields}}."""
    run = Path(run_dir)
    unit = _time_unit(run)
    fin = finished_here(run)
    out = {"stage": _stage(run), "route_residual": _route_residual(run), "drc": _drc(run), "lvs": _lvs(run),
           "setup": _slack(run, "setup", unit) if fin else dict(_NOT_THIS_RUN),
           "hold": _slack(run, "hold", unit) if fin else dict(_NOT_THIS_RUN),
           "drv": _drv(run) if fin else dict(_NOT_THIS_RUN)}
    unc = [out[k].get("unconstrained") for k in ("setup", "hold")]
    out["constraint"] = {"timing_unconstrained": True if any(unc) else
                         (False if out["setup"]["measured"] or out["hold"]["measured"] else None)}
    return out
