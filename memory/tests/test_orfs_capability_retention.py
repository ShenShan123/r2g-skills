"""Frozen candidate policy retention stays read-only and fail-closed."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from tehm import db
from tehm.capability import (
    create_policy_snapshot, record_policy_load, register_capability,
    verify_capability_retention,
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_orfs_capability_retention import build_orfs_capability_retention  # noqa: E402
from build_orfs_capability_retention_batch import (  # noqa: E402
    build_orfs_capability_retention_batch,
)


def _project(root: Path, name: str, *, failed: bool) -> Path:
    project = root / name
    (project / "constraints").mkdir(parents=True)
    (project / "reports").mkdir()
    run = project / "backend" / f"RUN_{name}"
    run.mkdir(parents=True)
    (project / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = sky130hs\n"
        f"export CORE_UTILIZATION = {'95' if failed else '40'}\n")
    (run / "run-meta.json").write_text(json.dumps({
        "run_tag": f"RUN_{name}", "make_status": 2 if failed else 0,
        "config_mk": str(project / "constraints" / "config.mk")}))
    (run / "stage_log.jsonl").write_text(
        json.dumps({"stage": "route", "status": 2 if failed else 0}) + "\n")
    for report, payload in {
        "route": {"status": "fail" if failed else "clean"},
        "drc": {"status": "clean"}, "lvs": {"status": "clean"},
        "timing_check": {"tier": "clean"},
        "ppa": {"summary": {"timing": {"setup_wns": 1.0},
                              "area": {"design_area_um2": 100.0}}},
    }.items():
        (project / "reports" / f"{report}.json").write_text(json.dumps(payload))
    return project


def test_real_orfs_retention_replay_is_read_only(tmp_tehm, tmp_path):
    conn, _, _ = tmp_tehm
    source = tmp_path / "source.sqlite"
    destination = sqlite3.connect(source)
    conn.backup(destination)
    destination.close()
    conn.close()

    candidate_db = tmp_path / "candidate.sqlite"
    candidate = db.connect(candidate_db)
    db.ensure_schema(candidate)
    policy = create_policy_snapshot(
        candidate, memory_snapshot_id="candidate-memory",
        promoted_rules=[], retrieval_config={"evaluation_only": True})
    record_policy_load(candidate, policy_snapshot_id=policy.policy_snapshot_id,
                       runtime_id="runtime", loaded=True)
    candidate.close()
    report_path = tmp_path / "attribution.json"
    report_path.write_text(json.dumps({
        "derived_db": str(candidate_db),
        "candidate_policy": policy.to_dict(),
        "capability": {"capability_id": "capability-test"},
        "firewall": {"training_lineages": ["train:a"],
                      "heldout_lineages": ["held:b"]},
    }))
    before = _project(tmp_path, "retention_before", failed=True)
    after = _project(tmp_path, "retention_after", failed=False)
    digest_before = hashlib.sha256(candidate_db.read_bytes()).hexdigest()
    result = build_orfs_capability_retention(
        report_path, output=tmp_path / "retention.json",
        before_project=before, after_project=after,
        lineage_id="retention:c", config_edits={"CORE_UTILIZATION": "40"})
    assert result["retention"]["retained"] is True
    assert result["firewall"]["disjoint"] is True
    assert hashlib.sha256(candidate_db.read_bytes()).hexdigest() == digest_before


def test_real_orfs_retention_can_write_isolated_verified_ledger(tmp_tehm, tmp_path):
    """The explicit ledger lane binds a real replay without mutating source DB."""
    conn, _, _ = tmp_tehm
    source = tmp_path / "source.sqlite"
    destination = sqlite3.connect(source)
    conn.backup(destination)
    destination.close()
    conn.close()

    candidate_db = tmp_path / "candidate.sqlite"
    candidate = db.connect(candidate_db)
    db.ensure_schema(candidate)
    capability = register_capability(
        candidate, mechanism_family="ORFS_RETENTION", applicability={"target": "route"})
    policy = create_policy_snapshot(
        candidate, memory_snapshot_id="candidate-memory",
        promoted_rules=[], retrieval_config={"evaluation_only": True})
    load = record_policy_load(
        candidate, policy_snapshot_id=policy.policy_snapshot_id,
        runtime_id="runtime", loaded=True)
    candidate.close()
    report_path = tmp_path / "attribution.json"
    report_path.write_text(json.dumps({
        "derived_db": str(candidate_db),
        "candidate_policy": policy.to_dict(),
        "capability": {"capability_id": capability.capability_id},
        "firewall": {"training_lineages": ["train:a"],
                      "heldout_lineages": ["held:b"]},
    }))
    before = _project(tmp_path, "ledger_before", failed=True)
    after = _project(tmp_path, "ledger_after", failed=False)
    source_digest = hashlib.sha256(candidate_db.read_bytes()).hexdigest()
    ledger_db = tmp_path / "retention-ledger.sqlite"
    result = build_orfs_capability_retention(
        report_path, output=tmp_path / "retention.json",
        before_project=before, after_project=after,
        lineage_id="retention:c", config_edits={"CORE_UTILIZATION": "40"},
        retention_ledger_db=ledger_db)
    assert result["retention"]["retained"] is True
    assert result["retention_ledger"]["authority_eligible"] is True
    receipt = result["retention_ledger"]["receipt"]
    ledger = db.connect_read_only(ledger_db)
    try:
        checked = verify_capability_retention(ledger, capability.capability_id, receipt)
        assert checked["eligible"] is True
        assert checked["reasons"] == []
    finally:
        ledger.close()
    assert hashlib.sha256(candidate_db.read_bytes()).hexdigest() == source_digest


def _retention_attribution(tmp_path, *, training=(), heldout=()):
    candidate_db = tmp_path / "batch-candidate.sqlite"
    candidate = db.connect(candidate_db)
    db.ensure_schema(candidate)
    capability = register_capability(
        candidate, mechanism_family="ORFS_RETENTION_BATCH",
        applicability={"target": "route"})
    policy = create_policy_snapshot(
        candidate, memory_snapshot_id="candidate-memory",
        promoted_rules=[], retrieval_config={"evaluation_only": True})
    record_policy_load(candidate, policy_snapshot_id=policy.policy_snapshot_id,
                       runtime_id="runtime", loaded=True)
    candidate.close()
    report = tmp_path / "batch-attribution.json"
    report.write_text(json.dumps({
        "derived_db": str(candidate_db),
        "candidate_policy": policy.to_dict(),
        "capability": {"capability_id": capability.capability_id},
        "firewall": {"training_lineages": list(training),
                      "heldout_lineages": list(heldout)},
    }))
    return report, candidate_db, capability


def test_retention_batch_requires_independent_lineage_quota(tmp_tehm, tmp_path):
    report, candidate_db, _ = _retention_attribution(tmp_path)
    source_digest = hashlib.sha256(candidate_db.read_bytes()).hexdigest()
    before = _project(tmp_path, "batch_one_before", failed=True)
    after = _project(tmp_path, "batch_one_after", failed=False)
    manifest = tmp_path / "retention-manifest.json"
    manifest.write_text(json.dumps({
        "version": "orfs-capability-retention-batch-v1",
        "cases": [{"case_id": "one", "lineage_id": "retention:one",
                   "before_project": str(before), "after_project": str(after),
                   "config_edits": {"CORE_UTILIZATION": "40"}}],
    }))
    result = build_orfs_capability_retention_batch(
        manifest, attribution_report=report,
        output=tmp_path / "batch-report.json")
    assert result["summary"]["batch_status"] == "NOT_ESTABLISHED"
    assert result["summary"]["reasons"] == ["independent_lineage_quota_not_met"]
    assert result["summary"]["retained_count"] == 1
    assert hashlib.sha256(candidate_db.read_bytes()).hexdigest() == source_digest


def test_retention_batch_binds_two_lineages_to_isolated_ledger(tmp_tehm, tmp_path):
    report, candidate_db, capability = _retention_attribution(tmp_path)
    pairs = []
    for suffix in ("a", "b"):
        pairs.append({
            "case_id": suffix,
            "lineage_id": f"retention:{suffix}",
            "before_project": str(_project(tmp_path, f"batch_{suffix}_before", failed=True)),
            "after_project": str(_project(tmp_path, f"batch_{suffix}_after", failed=False)),
            "config_edits": {"CORE_UTILIZATION": "40"},
        })
    manifest = tmp_path / "retention-manifest-two.json"
    manifest.write_text(json.dumps({
        "version": "orfs-capability-retention-batch-v1", "cases": pairs,
    }))
    source_digest = hashlib.sha256(candidate_db.read_bytes()).hexdigest()
    ledger_db = tmp_path / "batch-retention-ledger.sqlite"
    result = build_orfs_capability_retention_batch(
        manifest, attribution_report=report,
        output=tmp_path / "batch-report-two.json",
        retention_ledger_db=ledger_db)
    assert result["summary"]["batch_status"] == "PASS"
    assert result["summary"]["retained_count"] == 2
    assert result["summary"]["ledger_authority_eligible_count"] == 2
    assert result["firewall"]["entered_learner_support"] is False
    assert hashlib.sha256(candidate_db.read_bytes()).hexdigest() == source_digest
    ledger = db.connect_read_only(ledger_db)
    try:
        count = ledger.execute(
            "SELECT COUNT(*) FROM tehm_capability_retention_receipts "
            "WHERE capability_id=?", (capability.capability_id,)).fetchone()[0]
        assert count == 2
    finally:
        ledger.close()
