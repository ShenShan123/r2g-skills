"""Read-only extraction and grouping of production ORFS/PPA evidence.

This adapter consumes preserved ``run-meta.json``/stage logs and ORFS report
files.  It never records a physical effect in TEHM.  A missing congestion or
DRC extractor is represented as ``None`` with an explicit reason; no metric is
silently fabricated from a flow exit code.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Mapping

from tehm.ids import stable_dumps
from tehm.physical.effects import PHYSICAL_METRICS, extract_deltas
from tehm.physical.memory import _action_signature


ORFS_PPA_VERSION = "orfs-ppa-extractor-v1"
_NUMBER = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"


def extract_orfs_ppa(run_meta: str | Path | Mapping, *, report: str | Path | Mapping | None = None,
                     route_report: str | Path | None = None,
                     drc_report: str | Path | None = None) -> dict:
    """Extract a single successful ORFS run into an auditable PPA record."""
    meta_path = None
    if isinstance(run_meta, Mapping):
        meta = dict(run_meta)
    else:
        meta_path = Path(run_meta).resolve()
        meta = json.loads(meta_path.read_text())
    status = meta.get("make_status")
    if status != 0:
        raise ValueError(f"ORFS run is not successful: make_status={status!r}")

    report_path, report_obj = _load_json_report(report, meta)
    finish = report_obj if isinstance(report_obj, Mapping) else {}
    ppa = {
        "wns_ns": _finite(finish.get("finish__timing__setup__ws")),
        "tns_ns": _finite(finish.get("finish__timing__setup__tns")),
        "area_um2": _finite(finish.get("finish__design__instance__area")),
        "power_w": _finite(finish.get("finish__power__total")),
        "congestion": None,
        "drc_violations": None,
    }
    provenance = {
        "version": ORFS_PPA_VERSION,
        "run_meta": str(meta_path) if meta_path else None,
        "report": str(report_path) if report_path else None,
        "platform": meta.get("platform"),
        "design_name": meta.get("design_name"),
        "make_status": status,
        "metric_sources": {
            "wns_ns": "ORFS 6_report.json finish__timing__setup__ws",
            "tns_ns": "ORFS 6_report.json finish__timing__setup__tns",
            "area_um2": "ORFS 6_report.json finish__design__instance__area",
            "power_w": "ORFS 6_report.json finish__power__total",
        },
        "missing_metrics": [],
        "evidence_refs": [],
    }
    if meta_path:
        provenance["evidence_refs"].append(_evidence_ref(meta_path))
    if report_path:
        provenance["evidence_refs"].append(_evidence_ref(report_path))

    route_path = _resolve_optional(route_report, meta, "route")
    if route_path and route_path.is_file():
        congestion = _extract_congestion(route_path.read_text(errors="replace"))
        if congestion is not None:
            ppa["congestion"] = congestion
            provenance["metric_sources"]["congestion"] = f"ORFS {route_path.name} overflow"
        provenance["evidence_refs"].append(_evidence_ref(route_path))
    drc_path = _resolve_optional(drc_report, meta, "drc")
    if drc_path and drc_path.is_file():
        violations = _extract_drc_violations(drc_path.read_text(errors="replace"))
        if violations is not None:
            ppa["drc_violations"] = violations
            provenance["metric_sources"]["drc_violations"] = f"ORFS {drc_path.name} violation count"
        provenance["evidence_refs"].append(_evidence_ref(drc_path))
    provenance["missing_metrics"] = [m for m in PHYSICAL_METRICS
                                      if ppa.get(m) is None]
    return {"version": ORFS_PPA_VERSION, "ppa": ppa,
            "provenance": provenance,
            "complete": not provenance["missing_metrics"]}


def build_orfs_pair(before: Mapping, after: Mapping, *, lineage_id: str,
                    platform: str, family: str, dataset_tier: str,
                    action: Mapping, predicted: Mapping | None = None) -> dict:
    """Build one external calibration observation from two PPA records."""
    if not lineage_id or not platform or not family or not dataset_tier:
        raise ValueError("lineage_id/platform/family/dataset_tier are required")
    signature = _action_signature(dict(action))
    if signature is None:
        raise ValueError("action must have a typed config_edits signature")
    before_ppa = dict(before.get("ppa") or {})
    after_ppa = dict(after.get("ppa") or {})
    deltas = extract_deltas(before_ppa, after_ppa)
    return {
        "lineage_id": str(lineage_id), "platform": str(platform),
        "family": str(family), "dataset_tier": str(dataset_tier),
        "action": dict(action), "action_signature": signature,
        "action_signature_digest": _digest(signature),
        "before_ppa": before_ppa, "after_ppa": after_ppa,
        "observed_deltas": deltas,
        "predicted": dict(predicted) if isinstance(predicted, Mapping) else None,
        "evidence": {"before": before.get("provenance"),
                     "after": after.get("provenance")},
        "complete": bool(before.get("complete") and after.get("complete")),
    }


def calibration_group_key(sample: Mapping) -> str:
    """Return the exact required calibration partition key."""
    fields = (sample.get("platform"), sample.get("family"),
              sample.get("dataset_tier"))
    signature = sample.get("action_signature")
    if signature is None:
        signature = _action_signature(sample.get("action"))
    if any(not isinstance(value, str) or not value for value in fields) or signature is None:
        raise ValueError("sample lacks platform/family/dataset_tier/action_signature")
    return "|".join((*fields, _digest(signature)))


def _load_json_report(report, meta: Mapping):
    if isinstance(report, Mapping):
        return None, dict(report)
    if report:
        path = Path(report).resolve()
        return path, json.loads(path.read_text())
    logs = Path(str(meta.get("orfs_logs") or ""))
    candidates = [logs / "6_report.json", logs / "reports" / "6_report.json"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve(), json.loads(candidate.read_text())
    raise FileNotFoundError("successful run has no preserved ORFS 6_report.json")


def _resolve_optional(value, meta: Mapping, kind: str):
    if value:
        return Path(value).resolve()
    logs = Path(str(meta.get("orfs_logs") or ""))
    roots = [logs]
    # ORFS keeps machine-readable stage JSON beside logs but often stores
    # human-readable route/DRC reports in the sibling ``reports`` tree.
    parts = list(logs.parts)
    if "logs" in parts:
        index = len(parts) - 1 - parts[::-1].index("logs")
        roots.append(Path(*parts[:index], "reports", *parts[index + 1:]))
    candidates = [root / name for root in roots
                  for name in (f"5_{kind}.rpt", f"5_global_{kind}.rpt",
                               f"5_{kind}_drc.rpt", f"{kind}.rpt")]
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


def _extract_congestion(text: str):
    patterns = (rf"(?:total\s+)?overflow\s*[:=]?\s*{_NUMBER}",
                rf"congestion(?:\s+overflow)?\s*[:=]\s*{_NUMBER}")
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _finite(match.group(1))
    return None


def _extract_drc_violations(text: str):
    patterns = (rf"(?:total\s+)?violations?\s*[:=]\s*{_NUMBER}",
                rf"number\s+of\s+violations\s*=\s*{_NUMBER}")
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _finite(match.group(1))
    return None


def _evidence_ref(path: Path) -> dict:
    data = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data)}


def _digest(value) -> str:
    return hashlib.sha256(stable_dumps(value).encode()).hexdigest()[:24]


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
