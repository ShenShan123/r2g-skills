#!/usr/bin/env python3
"""Run the Revision3 non-P12 CAPABILITY_GAP challenge cohort.

Two source-disjoint RTL fixtures are executed by the real Icarus/VVP oracle
and captured into a disposable derived SQLite database.  The detector then
produces a typed gap, the reason-specific admission consumes it without a P12
paired counterfactual, and a proposal-only ADD object is emitted.  No source
canonical database, production authority, or ``memory/docs`` file is touched.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.assets import detect_capability_gaps  # noqa: E402
from tehm.causal.orfs import _sha256  # noqa: E402
from tehm.causal.rtl import capture_rtl_causal_fragment  # noqa: E402
from tehm.evolution.admission import admit_evolution_reason  # noqa: E402
from tehm.evolution.capability_gap import (  # noqa: E402
    propose_capability_gap_expansion,
)
from tehm.evolution.reason_derivation import (  # noqa: E402
    derive_capability_gap_reason,
)
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402
from contracts import MemoryRoutingDecision  # noqa: E402


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = (
        "tehm_transitions", "tehm_knowledge", "tehm_relations",
        "tehm_memory_events", "tehm_asset_status", "tehm_rule_status",
    )
    result = {}
    for name in names:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        if exists:
            result[name] = db.count_rows(conn, name)
    return result


def _no_match_route(gap) -> MemoryRoutingDecision:
    """Materialize the typed no-memory route for an aggregated gap case."""
    return MemoryRoutingDecision(
        decision="NO_SKILL", resolved_state_id="gap-state:" + gap.gap_id,
        selected_rule_ids=(), selected_path_ids=(), selected_asset_ids=(),
        applicability={}, causal_support={}, risk={}, abstain_reasons=(),
        no_memory_budget=1, memory_budget=0, no_skill_reason="NO_MATCH")


def run_challenge(
        *, output_dir: Path | str,
        training_projects: tuple[Path | str, ...],
        campaign_id: str = "tehm-r3-capability-gap-20260902",
) -> dict:
    if len(training_projects) < 2:
        raise ValueError("CAPABILITY_GAP challenge requires at least two lineages")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_db = output / "source_canonical.sqlite"
    derived_db = output / "derived_gap_shadow.sqlite"
    source_conn = db.connect(source_db)
    db.ensure_schema(source_conn)
    # Freeze the empty source snapshot before any derived shadow is created.
    # A plain close leaves WAL/SHM sidecars and is not a replayable evidence
    # boundary.
    db.checkpoint_and_close(source_conn)
    source_digest = _sha256(source_db)
    # The source was just checkpointed and is an empty frozen snapshot.  A
    # byte copy avoids opening it through SQLite in ``mode=ro``; that opening
    # can recreate a ``-shm`` sidecar and invalidate the evidence boundary.
    shutil.copy2(source_db, derived_db)
    conn = db.connect(derived_db)
    db.ensure_schema(conn)
    before_counts = _counts(conn)
    store = ArtifactStore(output / "artifacts")
    oracle = IcarusOracle()
    if not oracle.available:
        conn.close()
        raise RuntimeError("Icarus/VVP is required for the real challenge cohort")

    projects = tuple(Path(item).resolve() for item in training_projects)
    lineages = tuple(path.name for path in projects)
    captures = []
    for project in projects:
        receipt = capture_rtl_causal_fragment(
            conn, store, project, oracle=oracle, campaign_id=campaign_id,
            dataset_split="training", dataset_learner_eligible=True)
        captures.append(receipt.to_dict())
    gaps = detect_capability_gaps(
        conn, campaign_id=campaign_id, min_lineages=2, min_failures=2)
    gap = next((item for item in gaps
                if item.mechanism_family == "HANDSHAKE_COMPLETION" and
                "RTL_REWRITE_TEMPLATE" in item.missing_asset_types), None)
    if gap is None:
        conn.close()
        raise AssertionError("real cohort did not produce the expected RTL capability gap")
    route = _no_match_route(gap)
    derivation = derive_capability_gap_reason(
        gap, campaign_id=campaign_id, case_id="capability-gap-handshake",
        min_lineages=2, min_failures=2,
        failure_transition_ids=gap.evidence_transitions, routing=route)
    if derivation is None:
        conn.close()
        raise AssertionError("typed capability-gap derivation was not applicable")
    admission = admit_evolution_reason(
        derivation, campaign_id=campaign_id, learner_eligible=True,
        capability_gap=gap, failure_transition_ids=gap.evidence_transitions,
        routing=route)
    if not admission.admitted:
        conn.close()
        raise AssertionError(f"capability-gap admission blocked: {admission.blocked_reason}")
    proposal = propose_capability_gap_expansion(
        gap, derivation, admission, proposal_kind="ASSET_OR_KNOWLEDGE",
        failure_transition_ids=gap.evidence_transitions, routing=route)
    after_counts = _counts(conn)
    # Successful challenge reports must point at sidecar-free immutable
    # snapshots.  The source remains untouched; only the disposable derived
    # projection is checkpointed here.
    db.checkpoint_and_close(conn)
    if _sha256(source_db) != source_digest:
        raise AssertionError("source canonical database changed during shadow challenge")
    report = {
        "version": "r3-capability-gap-challenge-v1",
        "campaign_id": campaign_id,
        "source_db": str(source_db),
        "source_db_sha256": source_digest,
        "derived_db": str(derived_db),
        "derived_db_sha256": _sha256(derived_db),
        "training_projects": [str(item) for item in projects],
        "training_lineages": list(lineages),
        "learner_eligible_training_only": True,
        "real_oracle": "icarus/vvp",
        "training_capture": captures,
        "gap_receipts": [item.to_dict() for item in gaps],
        "selected_gap": gap.to_dict(),
        "evolution_reason_derivation": {
            **derivation.to_dict(),
            "receipt_id": derivation.receipt_id,
            "receipt_digest": derivation.receipt_digest,
        },
        "evolution_admission": {
            **admission.to_dict(),
            "receipt_id": admission.receipt_id,
            "receipt_digest": admission.receipt_digest,
        },
        "capability_gap_proposal": {
            **proposal.to_dict(),
            "proposal_id": proposal.proposal_id,
            "proposal_digest": proposal.proposal_digest,
        },
        "counts_before": before_counts,
        "counts_after": after_counts,
        "route": {
            **route.to_dict(),
            "routing_receipt_id": route.routing_receipt_id,
            "decision_digest": route.decision_digest,
            "semantic_name": "NO_SKILL_NO_MATCH",
            "paired_counterfactual_required": False,
        },
        "canonical_memory_mutation": "none",
        "production_runtime": {
            "promotion_attempted": False,
            "production_promotion_eligible": False,
            "runtime_authority_changed": False,
        },
        "evidence_boundary": {
            "failure_semantics": (
                "failure_evidence counts real source failures represented by "
                "original_failure=REMOVED on independently verified repairs; "
                "post-action PASS is not mislabeled as unresolved FAIL."
            ),
            "heldout_or_calibration_consumed": False,
            "proposal_is_executable": False,
        },
    }
    (output / "capability_gap_challenge_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_projects = (
        ROOT / "tests/fixtures/rtl_projects/req_ack_bug",
        ROOT / "tests/fixtures/rtl_projects/req_ack_bug2",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--training-project", type=Path, action="append")
    parser.add_argument("--campaign-id", default="tehm-r3-capability-gap-20260902")
    args = parser.parse_args(argv)
    projects = tuple(args.training_project or default_projects)
    report = run_challenge(output_dir=args.output, training_projects=projects,
                           campaign_id=args.campaign_id)
    print(json.dumps({
        "gap_id": report["selected_gap"]["gap_id"],
        "reason": report["evolution_reason_derivation"]["reason"],
        "admitted": report["evolution_admission"]["admitted"],
        "proposal_kind": report["capability_gap_proposal"]["proposal_kind"],
        "paired_counterfactual_required": report["route"][
            "paired_counterfactual_required"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
        "production_promotion_eligible": report["production_runtime"][
            "production_promotion_eligible"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
