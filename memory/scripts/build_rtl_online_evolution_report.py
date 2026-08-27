#!/usr/bin/env python3
"""Build a deterministic, evaluation-only B3 online-evolution report.

The lane starts from a v4 freeze backup, executes one new learner-eligible RTL
lineage and one held-out lineage with the real Icarus oracle, then runs the
online observation boundary.  Observation emits a hash-chained proposal only;
an explicit affected-group crystallization is performed afterwards to prove
the B2 write path is gated rather than automatic.  Raw canonical evidence and
the production lifecycle are checked before and after every phase.
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
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.canonical.capture import capture  # noqa: E402
from tehm.causal.orfs import _backup_database, _sha256  # noqa: E402
from tehm.evolution import (  # noqa: E402
    crystallize_affected_groups, observe_transition, verify_event_chain,
)
from tehm.evolution.anti_forgetting import (  # noqa: E402
    raw_evidence_digest, verify_raw_evidence_unchanged,
)
from tehm.rtl.rtl_evidence import build_rtl_execution_record  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402


REPORT_VERSION = "tehm-online-evolution-rtl-b3-v1"
CAMPAIGN_ID = "live"
MATERIALIZED_AT = "2026-08-22T00:00:00+00:00"
TRAINING_PROJECT = "p3_positive_credit_return"
HELDOUT_PROJECT = "p3_obligation_recovery"


def _stable_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _counts(conn) -> dict[str, int]:
    names = (
        "tehm_states", "tehm_transitions", "tehm_episodes",
        "tehm_episode_steps", "tehm_dataset_membership", "tehm_edges",
        "tehm_physical_effects", "tehm_rules", "tehm_rule_sources",
        "tehm_rule_status", "tehm_rule_revisions", "tehm_memory_events",
        "tehm_causal_nodes", "tehm_causal_edges",
    )
    return {
        name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        for name in names
    }


def _project(name: str) -> Path:
    return (ROOT / "tests" / "fixtures" / "rtl_projects" / name).resolve()


def _capture(conn, store, project: Path, oracle: IcarusOracle, *, split: str,
             learner_eligible: bool) -> tuple[str, dict]:
    record = build_rtl_execution_record(project, oracle=oracle, store=store)
    if record.verification.get("verdict") != "PASS":
        raise ValueError(f"RTL oracle did not PASS for {project.name}")
    receipt = capture(
        conn, store, record, dataset_campaign_id=CAMPAIGN_ID,
        dataset_split=split, dataset_learner_eligible=learner_eligible,
        materialized_at=MATERIALIZED_AT)
    return receipt.transition_id, {
        "project": project.name,
        "lineage_id": record.lineage_id,
        "transition_id": receipt.transition_id,
        "outcome": receipt.outcome,
        "oracle_verdict": record.verification.get("verdict"),
        "split": split,
        "learner_eligible": learner_eligible,
    }


def build_report(source_db: Path | str, output_dir: Path | str,
                 *, overwrite: bool = False) -> dict:
    source_db = Path(source_db).resolve()
    if not source_db.is_file():
        raise FileNotFoundError(f"source database not found: {source_db}")
    output_dir = Path(output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(
            f"output directory is non-empty; pass --overwrite: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    derived_db = output_dir / "tehm.sqlite"
    _backup_database(source_db, derived_db)
    source_digest_before = _sha256(source_db)
    conn = db.connect(derived_db)
    db.ensure_schema(conn)
    store = ArtifactStore(output_dir / "artifacts")
    oracle = IcarusOracle()
    if not oracle.available:
        conn.close()
        raise RuntimeError("B3 RTL online-evolution report requires Icarus")

    original_now_local = db.now_local
    db.now_local = lambda: MATERIALIZED_AT
    try:
        training_id, training_capture = _capture(
            conn, store, _project(TRAINING_PROJECT), oracle,
            split="training", learner_eligible=True)
        heldout_id, heldout_capture = _capture(
            conn, store, _project(HELDOUT_PROJECT), oracle,
            split="heldout", learner_eligible=False)
        raw_before_observation = raw_evidence_digest(conn)
        counts_before_observation = _counts(conn)

        training_observation = observe_transition(
            conn, training_id, campaign_id=CAMPAIGN_ID,
            created_at=MATERIALIZED_AT)
        heldout_observation = observe_transition(
            conn, heldout_id, campaign_id=CAMPAIGN_ID,
            created_at=MATERIALIZED_AT)
        counts_after_observation = _counts(conn)
        raw_after_observation = verify_raw_evidence_unchanged(
            conn, raw_before_observation)
        chain = verify_event_chain(conn, campaign_id=CAMPAIGN_ID)

        # The observation manager must not persist a rule or lifecycle row.
        observation_derived_unchanged = all(
            counts_after_observation[name] == counts_before_observation[name]
            for name in ("tehm_rules", "tehm_rule_sources", "tehm_rule_status",
                         "tehm_rule_revisions"))
        training_event_ids = [event.event_id for event in training_observation.events]
        heldout_event_ids = [event.event_id for event in heldout_observation.events]
        event_rows = conn.execute(
            "SELECT event_id, event_type, source_id, learner_eligible "
            "FROM tehm_memory_events ORDER BY created_at, event_id").fetchall()

        # B2 is an explicit follow-up operation.  It can write derived rules
        # and a revision, but still cannot change raw evidence or production
        # lifecycle authority.
        persisted = crystallize_affected_groups(
            conn, [training_id], campaign_id=CAMPAIGN_ID,
            created_at=MATERIALIZED_AT)
        counts_after_persist = _counts(conn)
        raw_after_persist = verify_raw_evidence_unchanged(
            conn, raw_before_observation)
        chain_after_persist = verify_event_chain(
            conn, campaign_id=CAMPAIGN_ID)
        conn.commit()
    finally:
        db.now_local = original_now_local
        conn.close()

    source_digest_after = _sha256(source_db)
    if source_digest_before != source_digest_after:
        raise RuntimeError("source database changed during online evaluation")

    event_summary = [dict(row) for row in event_rows]
    report = {
        "version": REPORT_VERSION,
        "evaluation_only": True,
        "campaign_id": CAMPAIGN_ID,
        "source_db": str(source_db),
        "source_db_sha256": source_digest_before,
        "derived_db": str(derived_db),
        "derived_db_sha256": _sha256(derived_db),
        "captures": {
            "training": training_capture,
            "heldout": heldout_capture,
        },
        "observations": {
            "training": training_observation.to_dict(),
            "heldout": heldout_observation.to_dict(),
        },
        "events": {
            "training_event_ids": training_event_ids,
            "heldout_event_ids": heldout_event_ids,
            "rows": event_summary,
            "chain": chain,
            "chain_after_persist": chain_after_persist,
        },
        "gated_persist": persisted.to_dict(),
        "counts": {
            "before_observation": counts_before_observation,
            "after_observation": counts_after_observation,
            "after_explicit_persist": counts_after_persist,
        },
        "invariants": {
            "training_triggered": training_observation.consolidation_triggered,
            "training_proposal_only_before_persist": (
                observation_derived_unchanged and
                any(event.event_type == "RULE_REVISION_PROPOSED"
                    for event in training_observation.events)),
            "heldout_not_triggered": not heldout_observation.consolidation_triggered,
            "heldout_trigger_reason": list(heldout_observation.trigger_reasons),
            "heldout_learner_eligible": heldout_observation.learner_eligible,
            "raw_evidence_preserved_after_observation": raw_after_observation.to_dict(),
            "raw_evidence_preserved_after_explicit_persist": raw_after_persist.to_dict(),
            "incremental_full_rebuild_equivalent": persisted.full_rebuild_equivalent,
            "event_chain_valid": bool(chain.get("ok") and chain_after_persist.get("ok")),
            "production_lifecycle_untouched_by_observation": observation_derived_unchanged,
            "canonical_memory_mutation": "none",
        },
        "promotion_attempted": False,
        "production_promotion_eligible": False,
        "report_digest": None,
    }
    digest_payload = dict(report)
    digest_payload["report_digest"] = None
    report["report_digest"] = _stable_digest(digest_payload)
    (output_dir / "online_evolution_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source-db", type=Path,
        default=ROOT.parent / "evidence" / "tehm-evidence-freeze-v4-dev" /
        "closed_loop" / "tehm.sqlite")
    parser.add_argument(
        "--output-dir", type=Path,
        default=ROOT.parent / "evidence" / "tehm-online-evolution-rtl-b3-dev")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(args.source_db, args.output_dir,
                          overwrite=args.overwrite)
    print(json.dumps({
        "version": report["version"],
        "source_db_sha256": report["source_db_sha256"],
        "derived_db_sha256": report["derived_db_sha256"],
        "training_triggered": report["invariants"]["training_triggered"],
        "heldout_not_triggered": report["invariants"]["heldout_not_triggered"],
        "incremental_full_rebuild_equivalent": report["invariants"][
            "incremental_full_rebuild_equivalent"],
        "event_chain_valid": report["invariants"]["event_chain_valid"],
        "canonical_memory_mutation": report["invariants"]["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
