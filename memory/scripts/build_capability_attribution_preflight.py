#!/usr/bin/env python3
"""Build an evaluation-only C1-C8 attribution preflight from a v4 DB.

The preflight binds memory/policy/runtime receipts but intentionally does not
invent behavior, held-out, or ablation results.  Missing empirical evidence
therefore remains false in the resulting gate report, and no capability status
or production policy is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db  # noqa: E402
from tehm.causal.orfs import _backup_database, _sha256  # noqa: E402
from tehm.capability import (  # noqa: E402
    create_policy_snapshot, evaluate_capability_campaign,
    record_capability_evidence, record_policy_load, register_capability,
)


def _stable_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def build_capability_attribution_preflight(
    source_db: Path | str,
    *,
    output_dir: Path | str,
    mechanism_family: str,
    causal_path_id: str | None = None,
    capability_id_hint: str | None = None,
) -> dict:
    source = Path(source_db).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    derived_db = output / "tehm.sqlite"
    _backup_database(source, derived_db)
    conn = db.connect(derived_db)
    db.ensure_schema(conn)
    capability = register_capability(
        conn, mechanism_family=mechanism_family,
        applicability={"source": "causal_shadow_preflight"},
        required_rules=[], required_assets=[], status="candidate",
        provenance={"preflight": True, "capability_id_hint": capability_id_hint})
    if causal_path_id:
        record_capability_evidence(
            conn, capability_id=capability.capability_id,
            evidence_type="causal_path", evidence_id=causal_path_id,
            split="training", verdict="PASS")

    baseline_memory_digest = "sha256:" + _sha256(source)
    candidate_memory_digest = "sha256:" + _sha256(derived_db)
    baseline_policy = create_policy_snapshot(
        conn, memory_snapshot_id=baseline_memory_digest,
        promoted_rules=[], promoted_assets=[],
        retrieval_config={"causal_shadow": False, "evaluation_only": False})
    candidate_policy = create_policy_snapshot(
        conn, memory_snapshot_id=candidate_memory_digest,
        promoted_rules=[], promoted_assets=[],
        retrieval_config={"causal_shadow": True, "evaluation_only": True})
    runtime_id = "tehm-capability-preflight-evaluation"
    load = record_policy_load(
        conn, policy_snapshot_id=candidate_policy.policy_snapshot_id,
        runtime_id=runtime_id, loaded=True,
        receipt={"mode": "evaluation_only", "production_authority": False})
    controls = {
        "executor": "tehm-evaluation-lane-v1",
        "oracle": "not_run_preflight",
        "toolchain": "not_run_preflight",
        "dataset": "not_run_preflight",
        "seed_policy": "fixed-but-not-executed",
        "candidate_budget": 0,
    }
    attribution = evaluate_capability_campaign(
        conn, capability_id=capability.capability_id,
        baseline_memory_digest=baseline_memory_digest,
        candidate_memory_digest=candidate_memory_digest,
        baseline_policy_snapshot_id=baseline_policy.policy_snapshot_id,
        candidate_policy_snapshot_id=candidate_policy.policy_snapshot_id,
        runtime_id=runtime_id,
        baseline_behavior_digest=_stable_digest({"executed": False}),
        candidate_behavior_digest=_stable_digest({"executed": False}),
        target_gain=False, no_regression=False,
        heldout={"verdict": "UNKNOWN", "disjoint_lineage": False},
        ablation={"gain_without_memory": False, "gain_with_memory": False},
        baseline_controls=controls, candidate_controls=dict(controls),
        memory_delta={
            "version": "memory-delta-v1",
            "baseline_memory_digest": baseline_memory_digest,
            "candidate_memory_digest": candidate_memory_digest,
            "added_capability_ids": [capability.capability_id],
        }, strict_memory_delta=True)
    conn.close()
    report = {
        "version": "capability-attribution-preflight-v1",
        "source_db": str(source),
        "source_db_sha256": _sha256(source),
        "derived_db": str(derived_db),
        "derived_db_sha256": _sha256(derived_db),
        "memory_delta": {
            "version": "memory-delta-v1",
            "baseline_memory_digest": baseline_memory_digest,
            "candidate_memory_digest": candidate_memory_digest,
            "added_capability_ids": [capability.capability_id],
        },
        "canonical_memory_mutation": "none",
        "capability": capability.to_dict(),
        "baseline_policy": baseline_policy.to_dict(),
        "candidate_policy": candidate_policy.to_dict(),
        "policy_load": load.to_dict(),
        "attribution": attribution.to_dict(),
        "promotion_eligible": False,
        "interpretation": (
            "C1-C3 infrastructure receipts exist, but behavior, target gain, "
            "held-out transfer, no-regression, and ablation were not executed; "
            "this is not capability evidence"),
    }
    (output / "capability_attribution_preflight.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mechanism-family", required=True)
    parser.add_argument("--causal-path-id")
    parser.add_argument("--capability-id-hint")
    args = parser.parse_args(argv)
    report = build_capability_attribution_preflight(
        args.source_db, output_dir=args.output,
        mechanism_family=args.mechanism_family,
        causal_path_id=args.causal_path_id,
        capability_id_hint=args.capability_id_hint)
    print(json.dumps({
        "capability_id": report["capability"]["capability_id"],
        "gates": report["attribution"]["attribution"]["gates"],
        "missing_gates": report["attribution"]["attribution"]["missing_gates"],
        "promotion_eligible": report["promotion_eligible"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
