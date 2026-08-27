#!/usr/bin/env python3
"""Build an auditable batch of frozen-policy ORFS retention replays.

The single-pair retention builder is deliberately small and useful for one
held-out lineage.  This wrapper is the next evidence-plane seam: it validates
the complete case manifest before any replay, rejects duplicate or training /
held-out lineages, and aggregates independent receipts without turning a
partial batch into capability authority.  Every pair remains evaluation-only;
the optional retention ledger is an isolated clone of the attribution DB.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_orfs_capability_retention import (  # noqa: E402
    _load_policy_binding,
    build_orfs_capability_retention,
)


BATCH_VERSION = "orfs-capability-retention-batch-v1"
DEFAULT_MIN_LINEAGES = 2


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read retention batch manifest: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("retention batch manifest must be a JSON object")
    return value


def _validate_manifest(manifest: dict, *, attribution_report: Path,
                       min_lineages: int | None) -> tuple[list[dict], int, dict]:
    if manifest.get("version") != BATCH_VERSION:
        raise ValueError("retention batch manifest version mismatch")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("retention batch manifest requires non-empty cases")
    requested_min = min_lineages if min_lineages is not None else manifest.get(
        "min_independent_lineages", DEFAULT_MIN_LINEAGES)
    try:
        requested_min = int(requested_min)
    except (TypeError, ValueError) as exc:
        raise ValueError("min_independent_lineages must be an integer") from exc
    if requested_min < 1:
        raise ValueError("min_independent_lineages must be positive")

    report = _load(attribution_report)
    _, _, _, _ = _load_policy_binding(report)
    firewall = report.get("firewall") or {}
    training = {str(value) for value in firewall.get("training_lineages") or ()}
    heldout = {str(value) for value in firewall.get("heldout_lineages") or ()}
    seen_cases: set[str] = set()
    seen_lineages: set[str] = set()
    normalised: list[dict] = []
    for ordinal, raw in enumerate(cases):
        if not isinstance(raw, dict):
            raise ValueError(f"retention case {ordinal} must be an object")
        case = dict(raw)
        case_id = str(case.get("case_id") or f"case-{ordinal}")
        lineage = str(case.get("lineage_id") or "")
        before_raw = case.get("before_project")
        after_raw = case.get("after_project")
        if not isinstance(before_raw, str) or not before_raw.strip():
            raise ValueError(f"retention case {case_id} lacks before_project")
        if not isinstance(after_raw, str) or not after_raw.strip():
            raise ValueError(f"retention case {case_id} lacks after_project")
        before = Path(before_raw).resolve()
        after = Path(after_raw).resolve()
        config = case.get("config_edits")
        if not case_id or case_id in {".", ".."} or "/" in case_id or "\\" in case_id:
            raise ValueError(f"retention case_id is not a safe basename: {case_id!r}")
        if case_id in seen_cases:
            raise ValueError(f"duplicate retention case_id: {case_id}")
        if not lineage:
            raise ValueError(f"retention case {case_id} lacks lineage_id")
        if lineage in seen_lineages:
            raise ValueError(f"duplicate retention lineage_id: {lineage}")
        if lineage in training or lineage in heldout:
            raise ValueError(
                f"retention lineage overlaps attribution firewall: {lineage}")
        if not before.is_dir() or not after.is_dir():
            raise ValueError(f"retention case {case_id} project directory missing")
        if not isinstance(config, dict) or not config:
            raise ValueError(f"retention case {case_id} requires config_edits")
        target_check = str(case.get("target_check") or "route")
        if not target_check:
            raise ValueError(f"retention case {case_id} target_check is empty")
        seen_cases.add(case_id)
        seen_lineages.add(lineage)
        normalised.append({
            "case_id": case_id,
            "before_project": before,
            "after_project": after,
            "lineage_id": lineage,
            "config_edits": dict(config),
            "target_check": target_check,
        })
    bindings = {
        "training_lineages": sorted(training),
        "heldout_lineages": sorted(heldout),
        "retention_lineages": sorted(seen_lineages),
        "disjoint": not bool(seen_lineages & (training | heldout)),
    }
    return normalised, requested_min, bindings


def build_orfs_capability_retention_batch(
        manifest_path: Path | str, *, attribution_report: Path | str,
        output: Path | str, retention_ledger_db: Path | str | None = None,
        min_lineages: int | None = None) -> dict:
    """Replay all manifest cases and write one aggregate evaluation report.

    Manifest validation happens before the first pair is executed.  A failed
    pair is retained as an audit result; it cannot be silently dropped from
    the denominator or make the batch eligible.  The function never opens the
    attribution DB for writing and never invokes lifecycle promotion.
    """
    manifest_path = Path(manifest_path).resolve()
    report_path = Path(attribution_report).resolve()
    cases, minimum, firewall = _validate_manifest(
        _load(manifest_path), attribution_report=report_path,
        min_lineages=min_lineages)
    ledger_path = Path(retention_ledger_db).resolve() if retention_ledger_db else None

    results: list[dict] = []
    # Pair reports are implementation details; the aggregate output is the
    # durable receipt.  A TemporaryDirectory also ensures a failed run cannot
    # leave misleading per-case reports beside the requested output.
    with tempfile.TemporaryDirectory(prefix="tehm-retention-batch-") as tmp:
        for case in cases:
            pair_output = Path(tmp) / f"{case['case_id']}.json"
            pair = build_orfs_capability_retention(
                report_path, output=pair_output,
                before_project=case["before_project"],
                after_project=case["after_project"],
                lineage_id=case["lineage_id"],
                config_edits=case["config_edits"],
                target_check=case["target_check"],
                retention_ledger_db=ledger_path)
            ledger = pair.get("retention_ledger") or {}
            results.append({
                "case_id": case["case_id"],
                "lineage_id": case["lineage_id"],
                "before_project": str(case["before_project"]),
                "after_project": str(case["after_project"]),
                "target_check": case["target_check"],
                "retained": bool((pair.get("retention") or {}).get("retained")),
                "replay_id": (pair.get("retention") or {}).get("replay_id"),
                "replay_verdict": (pair.get("retention") or {}).get("replay_verdict"),
                "retention_receipt_id": (ledger.get("receipt") or {}).get(
                    "retention_receipt_id"),
                "ledger_authority_eligible": bool(ledger.get("authority_eligible")),
                "evidence_id": (pair.get("replay") or {}).get("evidence_id"),
            })

    retained = sum(row["retained"] for row in results)
    ledger_eligible = sum(row["ledger_authority_eligible"] for row in results)
    if len(firewall["retention_lineages"]) < minimum:
        batch_status = "NOT_ESTABLISHED"
        reasons = ["independent_lineage_quota_not_met"]
    elif retained != len(results):
        batch_status = "FAIL"
        reasons = ["one_or_more_retention_replays_failed"]
    elif ledger_path is not None and ledger_eligible != len(results):
        batch_status = "FAIL"
        reasons = ["one_or_more_retention_ledger_receipts_ineligible"]
    else:
        batch_status = "PASS"
        reasons = []
    result = {
        "version": BATCH_VERSION,
        "manifest": str(manifest_path),
        "attribution_report": str(report_path),
        "retention_ledger_db": str(ledger_path) if ledger_path else None,
        "minimum_independent_lineages": minimum,
        "firewall": {**firewall, "entered_learner_support": False},
        "cases": results,
        "summary": {
            "case_count": len(results),
            "retained_count": retained,
            "failed_count": len(results) - retained,
            "ledger_authority_eligible_count": ledger_eligible,
            "batch_status": batch_status,
            "reasons": reasons,
        },
        "canonical_memory_mutation": "none",
        "promotion_attempted": False,
        "production_promotion_eligible": False,
    }
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--attribution-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--retention-ledger-db", type=Path, default=None)
    parser.add_argument("--min-lineages", type=int, default=None)
    args = parser.parse_args(argv)
    result = build_orfs_capability_retention_batch(
        args.manifest, attribution_report=args.attribution_report,
        output=args.output, retention_ledger_db=args.retention_ledger_db,
        min_lineages=args.min_lineages)
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
