#!/usr/bin/env python3
"""Run a real Icarus-backed Revision3 REPEATED_FAILURE challenge.

Each fixture receives the same concrete but intentionally ineffective guard
rewrite.  The modified RTL is executed with target and frozen-regression
testbenches; the complete FAIL receipts are then captured in an external
shadow SQLite and aggregated only across distinct lineages.  This is a
negative capability signal, not a fabricated model result or a canonical
memory update.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.causal.orfs import _backup_database, _sha256  # noqa: E402
from tehm.canonical.capture import ExecutionRecord, capture  # noqa: E402
from tehm.evolution import (  # noqa: E402
    admit_evolution_reason, derive_repeated_failure_reason,
    detect_repeated_failures,
)
from tehm.evolution.repeated_failure import RepeatedFailureReceipt  # noqa: E402
from tehm.rtl.rtl_actions import apply_rtl_action  # noqa: E402
from tehm.rtl.rtl_evidence import (  # noqa: E402
    build_rtl_execution_record, _store_source,
)
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    result = {}
    for name in ("tehm_transitions", "tehm_memory_events", "tehm_rule_status"):
        if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,)).fetchone():
            result[name] = db.count_rows(conn, name)
    return result


def _failed_record(project: Path, *, oracle: IcarusOracle,
                   store: ArtifactStore) -> ExecutionRecord:
    """Build a transition for a real failed, parser-backed action trial."""
    base = build_rtl_execution_record(project, oracle=oracle, store=store)
    rtl_files = sorted((project / "rtl").glob("*.v"))
    if not rtl_files:
        raise ValueError(f"project has no RTL source: {project}")
    manifest = json.loads((project / "manifest.json").read_text())
    wrong_action = copy.deepcopy(base.action)
    payload = dict(wrong_action.get("payload") or {})
    payload["domain"] = wrong_action["domain"]
    # A constant-false guard is a concrete action and is parser-backed, but it
    # cannot complete the transfer; Icarus supplies the authoritative FAIL.
    payload["add_condition"] = "1'b0"
    wrong_action["payload"] = payload
    buggy_source = rtl_files[0].read_text()
    wrong_source, edit = apply_rtl_action(buggy_source, payload)
    if not edit.get("rewritten"):
        raise AssertionError(f"ineffective action was not applied: {project}")
    verification_cfg = manifest.get("verification") or {}
    target_tb = project / verification_cfg.get("target_test", "tb/tb_handshake.v")
    regression_tb = project / verification_cfg.get("frozen_regression", "tb/tb_basic.v")
    with tempfile.TemporaryDirectory(prefix="tehm_r3_failed_") as td:
        trial = Path(td) / rtl_files[0].name
        trial.write_text(wrong_source)
        verification = oracle.verify(
            [trial], target_tb=target_tb if target_tb.exists() else None,
            regression_tb=regression_tb if regression_tb.exists() else None)
    if verification.get("verdict") != "FAIL" or verification.get(
            "oracle_complete") is not True:
        raise AssertionError(
            f"ineffective action did not yield complete FAIL oracle: {project}")
    after = copy.deepcopy(base.before)
    after["artifacts"] = {"rtl_slice": _store_source(
        store, "rtl", wrong_source)}
    after["reports"] = {"rtl": {"status": "violations", "total_violations": 1}}
    after["structural_graph"] = copy.deepcopy(base.before["structural_graph"])
    return ExecutionRecord(
        record_id="rtl:repeated-failure:" + str(manifest["design"]),
        domain=base.domain, project_id=base.project_id,
        design_id=base.design_id, lineage_id=base.lineage_id,
        before=base.before, action=wrong_action, after=after,
        observation_delta={
            "original_failure": "PRESENT",
            "first_divergence": {"before": 1, "after": 1},
            "failing_tests": {"before": 1, "after": 1},
            "created_regressions": [],
            "newly_observed_failures": ["RTL_TARGET_TEST_PASS"],
            "experiment_kind": "REPAIR", "utility_verdict": "UNKNOWN",
        }, verification=verification,
        episode={
            "episode_id": "rtl_ep:repeated-failure:" + str(manifest["design"]),
            "mechanism_family": manifest.get("mechanism_family", "RTL_REPAIR"),
            "lineage_id": str(manifest["design"]), "step_index": 0,
            "terminal_status": "FAILED_REPAIR",
        })


def run_challenge(*, output_dir: Path | str,
                  training_projects: tuple[Path | str, ...],
                  campaign_id: str = "tehm-r3-repeated-failure-20260902") -> dict:
    if len(training_projects) < 2:
        raise ValueError("REPEATED_FAILURE challenge requires at least two projects")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_db = output / "source_canonical.sqlite"
    derived_db = output / "derived_repeated_failure_shadow.sqlite"
    source_conn = db.connect(source_db)
    db.ensure_schema(source_conn)
    source_conn.close()
    source_digest = _sha256(source_db)
    _backup_database(source_db, derived_db)
    conn = db.connect(derived_db)
    db.ensure_schema(conn)
    before_counts = _counts(conn)
    oracle = IcarusOracle()
    if not oracle.available:
        conn.close()
        raise RuntimeError("Icarus/VVP is required for repeated-failure challenge")
    store = ArtifactStore(output / "artifacts")
    projects = tuple(Path(item).resolve() for item in training_projects)
    captures = []
    for project in projects:
        record = _failed_record(project, oracle=oracle, store=store)
        receipt = capture(
            conn, store, record, dataset_campaign_id=campaign_id,
            dataset_split="training", dataset_learner_eligible=True)
        captures.append(receipt.to_dict())
    repeated = detect_repeated_failures(
        conn, campaign_id=campaign_id, mechanism_family="HANDSHAKE_COMPLETION",
        min_independent_observations=2)
    if len(repeated) != 1:
        conn.close()
        raise AssertionError(f"expected one repeated-failure receipt, got {len(repeated)}")
    typed = repeated[0]
    derivation = derive_repeated_failure_reason(
        typed, campaign_id=campaign_id, case_id="repeated-failure-handshake")
    if derivation is None:
        conn.close()
        raise AssertionError("repeated-failure derivation was not applicable")
    admission = admit_evolution_reason(
        derivation, campaign_id=campaign_id, learner_eligible=True,
        repeated_failure=typed)
    if not admission.admitted:
        conn.close()
        raise AssertionError(f"repeated-failure admission blocked: {admission.blocked_reason}")
    after_counts = _counts(conn)
    conn.close()
    if _sha256(source_db) != source_digest:
        raise AssertionError("source canonical database changed during repeated-failure challenge")

    def _with_ids(item):
        return {**item.to_dict(), "receipt_id": item.receipt_id,
                "receipt_digest": item.receipt_digest}

    report = {
        "version": "r3-repeated-failure-challenge-v1",
        "campaign_id": campaign_id,
        "source_db": str(source_db), "source_db_sha256": source_digest,
        "derived_db": str(derived_db), "derived_db_sha256": _sha256(derived_db),
        "real_oracle": "icarus/vvp",
        "training_projects": [str(item) for item in projects],
        "training_capture": captures,
        "repeated_failure_receipt": _with_ids(typed),
        "evolution_reason_derivation": _with_ids(derivation),
        "evolution_admission": _with_ids(admission),
        "counts_before": before_counts, "counts_after": after_counts,
        "canonical_memory_mutation": "none",
        "production_runtime": {
            "promotion_attempted": False,
            "production_promotion_eligible": False,
            "runtime_authority_changed": False,
        },
        "independence": {
            "lineages": list(typed.evidence_lineages),
            "resolutions": list(typed.resolution_ids),
            "independent_observation_count": typed.independent_observation_count,
            "same_failure_family": typed.failure_family,
            "all_oracles_complete": all(typed.oracle_complete),
        },
    }
    (output / "repeated_failure_challenge_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--training-project", type=Path, action="append")
    parser.add_argument("--campaign-id", default="tehm-r3-repeated-failure-20260902")
    args = parser.parse_args(argv)
    projects = tuple(args.training_project or (
        ROOT / "tests/fixtures/rtl_projects/req_ack_bug",
        ROOT / "tests/fixtures/rtl_projects/req_ack_bug2",
    ))
    report = run_challenge(output_dir=args.output, training_projects=projects,
                           campaign_id=args.campaign_id)
    print(json.dumps({
        "reason": report["evolution_reason_derivation"]["reason"],
        "admitted": report["evolution_admission"]["admitted"],
        "independent_observation_count": report["independence"][
            "independent_observation_count"],
        "all_oracles_complete": report["independence"]["all_oracles_complete"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
