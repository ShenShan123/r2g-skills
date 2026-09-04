#!/usr/bin/env python3
"""Run a real P2-R6 NOVELTY/CONFLICT adapter challenge.

The existing novelty and conflict detectors are exercised on two independent
RTL fixtures under a real Icarus/VVP oracle. Their outputs are converted into
content-addressed typed receipts, then reason derivation and admission are
replayed in an external shadow database. No canonical production store or
runtime authority is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.causal.orfs import _sha256  # noqa: E402
from tehm.causal.rtl import capture_rtl_causal_fragment  # noqa: E402
from tehm.evolution import (  # noqa: E402
    admit_evolution_reason, detect_conflicts, detect_novelty,
    derive_conflict_reason, derive_novelty_reason,
)
from tehm.evolution.conflict import ConflictReceipt  # noqa: E402
from tehm.evolution.novelty import NoveltyReceipt  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    result = {}
    for name in ("tehm_transitions", "tehm_memory_events", "tehm_rule_status",
                 "tehm_asset_status"):
        if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,)).fetchone():
            result[name] = db.count_rows(conn, name)
    return result


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False, default=str).encode()).hexdigest()


def run_challenge(*, output_dir: Path | str,
                  training_projects: tuple[Path | str, ...],
                  campaign_id: str = "tehm-r3-reason-adapters-20260902") -> dict:
    if len(training_projects) < 2:
        raise ValueError("P2-R6 challenge requires at least two projects")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_db = output / "source_canonical.sqlite"
    derived_db = output / "derived_reason_adapter_shadow.sqlite"
    source_conn = db.connect(source_db)
    db.ensure_schema(source_conn)
    # Freeze the empty source before copying it.  A plain close leaves WAL/SHM
    # sidecars and makes the challenge report unsuitable for immutable replay.
    db.checkpoint_and_close(source_conn)
    source_digest = _sha256(source_db)
    # Avoid opening the source through SQLite backup: a read-only backup can
    # recreate a shared-memory sidecar on the supposedly frozen source.
    shutil.copy2(source_db, derived_db)
    conn = db.connect(derived_db)
    db.ensure_schema(conn)
    before_counts = _counts(conn)
    oracle = IcarusOracle()
    if not oracle.available:
        conn.close()
        raise RuntimeError("Icarus/VVP is required for P2-R6 challenge")
    store = ArtifactStore(output / "artifacts")
    projects = tuple(Path(item).resolve() for item in training_projects)
    captured = []
    transition_ids = []
    for project in projects:
        receipt = capture_rtl_causal_fragment(
            conn, store, project, oracle=oracle, campaign_id=campaign_id,
            dataset_split="training", dataset_learner_eligible=True)
        captured.append(receipt.to_dict())
        transition_ids.append(receipt.capture.transition_id)

    novelty_raw = detect_novelty(conn, transition_ids[0], campaign_id=campaign_id)
    novelty = NoveltyReceipt.from_dict(novelty_raw)
    novelty_derivation = derive_novelty_reason(
        novelty, campaign_id=campaign_id, case_id="novelty-case")
    if novelty_derivation is None:
        raise AssertionError("real first fixture did not produce NOVEL_MECHANISM")
    novelty_admission = admit_evolution_reason(
        novelty_derivation, campaign_id=campaign_id, learner_eligible=True,
        novelty=novelty)
    if not novelty_admission.admitted:
        raise AssertionError(
            f"novelty admission blocked: {novelty_admission.blocked_reason}")

    conflict = detect_conflicts(conn, transition_ids[1], campaign_id=campaign_id)
    conflict_derivation = derive_conflict_reason(
        conflict, campaign_id=campaign_id, case_id="conflict-case")
    if conflict_derivation is None:
        raise AssertionError("real fixture pair did not produce a conflict")
    conflict_admission = admit_evolution_reason(
        conflict_derivation, campaign_id=campaign_id, learner_eligible=True,
        conflict=conflict)
    if not conflict_admission.admitted:
        raise AssertionError(
            f"conflict admission blocked: {conflict_admission.blocked_reason}")
    after_counts = _counts(conn)
    db.checkpoint_and_close(conn)
    if _sha256(source_db) != source_digest:
        raise AssertionError("source canonical database changed during P2-R6 challenge")

    def _receipt_payload(receipt, derivation, admission):
        return {
            "receipt": {**receipt.to_dict(), "receipt_id": receipt.receipt_id,
                        "receipt_digest": receipt.receipt_digest},
            "derivation": {**derivation.to_dict(),
                           "receipt_id": derivation.receipt_id,
                           "receipt_digest": derivation.receipt_digest},
            "admission": {**admission.to_dict(), "receipt_id": admission.receipt_id,
                          "receipt_digest": admission.receipt_digest},
        }

    report = {
        "version": "r3-reason-adapter-challenge-v1",
        "campaign_id": campaign_id,
        "source_db": str(source_db),
        "source_db_sha256": source_digest,
        "derived_db": str(derived_db),
        "derived_db_sha256": _sha256(derived_db),
        "real_oracle": "icarus/vvp",
        "training_capture": captured,
        "transition_ids": transition_ids,
        "novelty_path": _receipt_payload(novelty, novelty_derivation, novelty_admission),
        "conflict_path": _receipt_payload(conflict, conflict_derivation, conflict_admission),
        "counts_before": before_counts,
        "counts_after": after_counts,
        "canonical_memory_mutation": "none",
        "memory_docs_submitted": False,
        "production_runtime": {
            "promotion_attempted": False,
            "production_promotion_eligible": False,
            "runtime_authority_changed": False,
        },
        "typed_adapter_digest": "sha256:" + _digest({
            "novelty": novelty.receipt_digest,
            "conflict": conflict.receipt_digest,
        }),
    }
    (output / "reason_adapter_challenge_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--training-project", type=Path, action="append")
    parser.add_argument("--campaign-id", default="tehm-r3-reason-adapters-20260902")
    args = parser.parse_args(argv)
    projects = tuple(args.training_project or (
        ROOT / "tests/fixtures/rtl_projects/req_ack_bug",
        ROOT / "tests/fixtures/rtl_projects/req_ack_bug2",
    ))
    report = run_challenge(output_dir=args.output, training_projects=projects,
                           campaign_id=args.campaign_id)
    print(json.dumps({
        "novelty_admitted": report["novelty_path"]["admission"]["admitted"],
        "conflict_admitted": report["conflict_path"]["admission"]["admitted"],
        "novelty_reason": report["novelty_path"]["derivation"]["reason"],
        "conflict_reason": report["conflict_path"]["derivation"]["reason"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
