"""Batch causal-transfer evaluation stays isolated and complete-denominator."""
from __future__ import annotations

import json

import pytest

from scripts.evaluate_causal_transfer_batch import evaluate_batch, validate_manifest
from tehm import db
from tehm.artifact_store import ArtifactStore
from tehm.adapters.orfs_pair import build_orfs_pair_record
from tehm.canonical.capture import capture

from .test_causal_transfer import _completed_orfs_project, _training_replication


def _prepare_batch(tmp_tehm, tmp_path, *, count=2):
    report = _training_replication(tmp_tehm, tmp_path)
    conn = db.connect(report["derived_db"])
    db.ensure_schema(conn)
    store = ArtifactStore(tmp_path / "batch-artifacts")
    cases = []
    for index in range(count):
        name = f"heldout-{index}"
        before = _completed_orfs_project(
            tmp_path, name + "-before", 50, route_status="fail", make_status=1)
        after = _completed_orfs_project(tmp_path, name + "-after", 40)
        record = build_orfs_pair_record(
            before, after, lineage_id=f"batch:{name}",
            config_edits={"CORE_UTILIZATION": "40"})
        transition_id = capture(
            conn, store, record, dataset_campaign_id="batch-heldout",
            dataset_split="heldout", dataset_learner_eligible=False).transition_id
        cases.append({
            "case_id": name,
            "lineage_id": f"batch:{name}",
            "transition_ids": [transition_id],
        })
    conn.close()
    manifest = tmp_path / "transfer_manifest.json"
    manifest.write_text(json.dumps({"version": "causal-transfer-batch-v1",
                                    "cases": cases}))
    return report, manifest, cases


def test_transfer_batch_isolated_ledger_and_source_read_only(tmp_tehm, tmp_path):
    report, manifest, cases = _prepare_batch(tmp_tehm, tmp_path)
    output = tmp_path / "batch-report.json"
    ledger = tmp_path / "ledger.sqlite"
    result = evaluate_batch(
        report["derived_db"], path_id=report["path"]["path_id"],
        training_campaign_id="l4-training", transfer_campaign_id="batch-heldout",
        manifest=manifest, output=output, require_full_oracle=False,
        min_transfer_lineages=2, ledger_db=ledger)
    assert result["summary"]["batch_status"] == "PASS"
    assert result["summary"]["eligible_count"] == 2
    assert len(result["ledger_receipt_ids"]) == 2
    assert result["promotion_attempted"] is False
    source = db.connect_read_only(report["derived_db"])
    assert source.execute(
        "SELECT COUNT(*) FROM tehm_causal_transfer_receipts").fetchone()[0] == 0
    source.close()
    isolated = db.connect_read_only(ledger)
    assert isolated.execute(
        "SELECT COUNT(*) FROM tehm_causal_transfer_receipts").fetchone()[0] == 2
    isolated.close()
    assert json.loads(output.read_text())["database_unchanged"] is True


def test_transfer_batch_keeps_failed_full_oracle_cases_in_denominator(
        tmp_tehm, tmp_path):
    report, manifest, _ = _prepare_batch(tmp_tehm, tmp_path, count=1)
    output = tmp_path / "batch-negative-report.json"
    ledger = tmp_path / "ledger-negative.sqlite"
    result = evaluate_batch(
        report["derived_db"], path_id=report["path"]["path_id"],
        training_campaign_id="l4-training", transfer_campaign_id="batch-heldout",
        manifest=manifest, output=output, require_full_oracle=True,
        min_transfer_lineages=1, ledger_db=ledger)
    assert result["summary"]["batch_status"] == "FAIL"
    assert result["summary"]["case_count"] == 1
    assert result["summary"]["eligible_count"] == 0
    assert result["summary"]["failed_count"] == 1
    isolated = db.connect_read_only(ledger)
    row = isolated.execute(
        "SELECT eligible FROM tehm_causal_transfer_receipts").fetchone()
    assert row[0] == 0
    isolated.close()


def test_transfer_batch_rejects_duplicate_lineage_before_evaluation():
    with pytest.raises(ValueError, match="duplicate transfer lineage_id"):
        validate_manifest({
            "version": "causal-transfer-batch-v1",
            "cases": [
                {"case_id": "a", "lineage_id": "same",
                 "transition_ids": ["t1"]},
                {"case_id": "b", "lineage_id": "same",
                 "transition_ids": ["t2"]},
            ],
        }, min_transfer_lineages=1)
