#!/usr/bin/env python3
"""Replay the proposal/abstain selector against frozen external observations.

The memory database is opened read-only.  This report measures whether the
current calibration is sharp enough to propose the typed utility action; it
does not record observations, mutate canonical memory, or grant promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.physical.utility_contracts import (  # noqa: E402
    contract_action,
    select_contract_proposal,
    timing_relief_budgeted_v1,
    utility_contract_digest,
)


def _ppa(metrics: dict) -> dict:
    return {"summary": {
        "timing": {"setup_wns": metrics.get("wns_ns"),
                   "setup_tns": metrics.get("tns_ns")},
        "area": {"design_area_um2": metrics.get("area_um2")},
        "power": {"total_power_w": metrics.get("power_w")},
    }}


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--observations", type=Path, required=True)
    ap.add_argument("--policy", type=Path, required=True)
    ap.add_argument("--staging-db", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    contract = timing_relief_budgeted_v1()
    action = contract_action(contract)
    policy = json.loads(args.policy.read_text())
    policy = policy.get("policy", policy)
    conn = sqlite3.connect(f"file:{args.staging_db.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        from tehm.physical.memory import PhysicalEffectMemory
        memory = PhysicalEffectMemory(conn)
        results = []
        for row in _rows(args.observations):
            before, after = row.get("before") or {}, row.get("after") or {}
            result = select_contract_proposal(
                memory, graph_context=before.get("graph") or {},
                baseline_ppa=_ppa(before.get("ppa_metrics") or {}),
                calibration_policy=policy,
                hard_checks=after.get("checks") or {},
                obligation_coverage=1.0, action=action, contract=contract)
            results.append({
                "case_id": row.get("case_id"),
                "lineage_id": row.get("lineage_id"),
                "status": result.get("status"),
                "abstain_reasons": result.get("abstain_reasons", []),
                "nearest_distance": (result.get("prediction") or {}).get(
                    "nearest_distance"),
                "support": (result.get("prediction") or {}).get("support"),
                "unique_graph_contexts": (result.get("prediction") or {}).get(
                    "unique_graph_contexts"),
            })
    finally:
        conn.close()
    proposed = sum(row["status"] == "PROPOSED" for row in results)
    abstained = sum(row["status"] == "ABSTAINED" for row in results)
    report = {
        "version": "timing-relief-budgeted-v1-selector-replay-v1",
        "status": "PROPOSAL_ONLY_REPLAY",
        "contract_id": contract["contract_id"],
        "contract_digest": utility_contract_digest(contract),
        "policy": str(args.policy.resolve()),
        "policy_sha256": hashlib.sha256(args.policy.read_bytes()).hexdigest(),
        "observations": str(args.observations.resolve()),
        "observations_sha256": hashlib.sha256(
            args.observations.read_bytes()).hexdigest(),
        "staging_db": str(args.staging_db.resolve()),
        "sample_count": len(results),
        "proposed_count": proposed,
        "abstained_count": abstained,
        "results": results,
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": report["status"], "sample_count": len(results),
                      "proposed_count": proposed, "abstained_count": abstained,
                      "promotion_eligible": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
