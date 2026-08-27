"""L4 held-out causal transfer remains an evaluation-only receipt."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tehm import db
from tehm.artifact_store import ArtifactStore
from tehm.canonical.capture import capture
from tehm.causal import (
    build_orfs_controlled_replication,
    build_transition_causal_fragment,
    consolidate_causal_path,
    evaluate_transfer_supported_mechanism,
)
from tehm.adapters.orfs_pair import build_orfs_pair_record
from tehm.rtl.rtl_evidence import build_rtl_execution_record


RTL_PROJECT = Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "req_ack_bug"


def _completed_orfs_project(root, name, utilization, *, route_status="clean",
                            make_status=0):
    project = root / name
    (project / "constraints").mkdir(parents=True)
    (project / "reports").mkdir()
    run = project / "backend" / f"RUN_{name}"
    run.mkdir(parents=True)
    (project / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = sky130hs\n"
        f"export CORE_UTILIZATION = {utilization}\n")
    (run / "run-meta.json").write_text(json.dumps({
        "run_tag": f"RUN_{name}", "make_status": make_status,
        "config_mk": str(project / "constraints" / "config.mk")}))
    (run / "stage_log.jsonl").write_text(
        json.dumps({"stage": "route", "status": 0}) + "\n")
    for report, payload in {
        "route": {"status": route_status}, "drc": {"status": "clean"},
        "lvs": {"status": "clean"}, "timing_check": {"tier": "clean"},
        "ppa": {"summary": {"timing": {"setup_wns": 1.0},
                              "area": {"design_area_um2": 100.0}}},
    }.items():
        (project / "reports" / f"{report}.json").write_text(json.dumps(payload))
    return project


def _training_replication(tmp_tehm, tmp_path):
    conn, _, _ = tmp_tehm
    source_db = tmp_path / "source.sqlite"
    destination = sqlite3.connect(source_db)
    conn.backup(destination)
    destination.close()
    conn.close()
    pairs = []
    for design in ("and32", "toggle32"):
        before = _completed_orfs_project(
            tmp_path, f"{design}_before", 50, route_status="fail", make_status=1)
        after = _completed_orfs_project(tmp_path, f"{design}_after", 40)
        pairs.append({
            "before_project": str(before), "after_project": str(after),
            "lineage_id": f"orfs-l4:{design}",
            "config_edits": {"CORE_UTILIZATION": "40"},
        })
    return build_orfs_controlled_replication(
        source_db, pairs=pairs, campaign_id="l4-training",
        output_dir=tmp_path / "controlled")


def test_l4_transfer_requires_disjoint_heldout_and_is_read_only(tmp_tehm, tmp_path):
    report = _training_replication(tmp_tehm, tmp_path)
    conn = db.connect(report["derived_db"])
    db.ensure_schema(conn)
    store = ArtifactStore(tmp_path / "transfer-artifacts")
    before = _completed_orfs_project(
        tmp_path, "heldout_before", 50, route_status="fail", make_status=1)
    after = _completed_orfs_project(tmp_path, "heldout_after", 40)
    transfer = build_orfs_pair_record(
        before, after, lineage_id="orfs-l4:heldout",
        config_edits={"CORE_UTILIZATION": "40"})
    transition_id = capture(
        conn, store, transfer, dataset_campaign_id="l4-training",
        dataset_split="heldout", dataset_learner_eligible=False).transition_id
    path_id = report["path"]["path_id"]
    before_counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("tehm_transitions", "tehm_causal_paths")
    }
    receipt = evaluate_transfer_supported_mechanism(
        conn, path_id, [transition_id], training_campaign_id="l4-training")
    after_counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before_counts
    }
    conn.close()

    assert receipt.eligible is True
    assert receipt.evidence_level == "L4_TRANSFER_SUPPORTED_MECHANISM"
    assert receipt.reason == "transfer_supported_mechanism"
    assert receipt.transfer_lineages == ("orfs-l4:heldout",)
    assert receipt.transfer_designs == ("orfs-l4:heldout",)
    assert receipt.promotion_eligible is False
    assert before_counts == after_counts


def test_l4_transfer_cannot_reuse_training_transition(tmp_tehm, tmp_path):
    report = _training_replication(tmp_tehm, tmp_path)
    conn = db.connect(report["derived_db"])
    db.ensure_schema(conn)
    source_id = conn.execute(
        "SELECT transition_id FROM tehm_dataset_membership "
        "WHERE campaign_id='l4-training' AND split='training' LIMIT 1"
    ).fetchone()[0]
    receipt = evaluate_transfer_supported_mechanism(
        conn, report["path"]["path_id"], [source_id],
        training_campaign_id="l4-training")
    conn.close()
    assert receipt.eligible is False
    assert receipt.reason == "transfer_reuses_training_transition"


def test_orfs_l4_requires_exact_two_arm_full_oracle(tmp_tehm, tmp_path):
    """A generic verifier PASS cannot masquerade as an ORFS full receipt."""
    report = _training_replication(tmp_tehm, tmp_path)
    conn = db.connect(report["derived_db"])
    db.ensure_schema(conn)
    store = ArtifactStore(tmp_path / "transfer-artifacts-full")
    before = _completed_orfs_project(
        tmp_path, "heldout_full_before", 50, route_status="fail", make_status=1)
    after = _completed_orfs_project(tmp_path, "heldout_full_after", 40)
    transfer = build_orfs_pair_record(
        before, after, lineage_id="orfs-l4:heldout-full",
        config_edits={"CORE_UTILIZATION": "40"})
    transition_id = capture(
        conn, store, transfer, dataset_campaign_id="l4-transfer-full",
        dataset_split="heldout", dataset_learner_eligible=False).transition_id
    receipt = evaluate_transfer_supported_mechanism(
        conn, report["path"]["path_id"], [transition_id],
        training_campaign_id="l4-training",
        transfer_campaign_id="l4-transfer-full", require_full_oracle=True)
    conn.close()

    assert receipt.eligible is False
    assert receipt.reason == "heldout_transfer_witness_failed"
    assert receipt.details[0]["full_oracle_required"] is True
    assert receipt.details[0]["full_oracle_complete"] is False


def test_causal_path_rejects_mixed_learner_campaigns(tmp_tehm):
    conn, store, _ = tmp_tehm
    first = capture(conn, store, build_rtl_execution_record(
        RTL_PROJECT, oracle=None, store=store)).transition_id
    second_record = build_rtl_execution_record(
        RTL_PROJECT, oracle=None, store=store)
    second_record.record_id = "rtl:req_ack_fsm:mixed-campaign"
    second = capture(
        conn, store, second_record, dataset_campaign_id="other-training",
        dataset_split="training", dataset_learner_eligible=True).transition_id
    first_fragment = build_transition_causal_fragment(
        conn, first, campaign_id="live")
    second_fragment = build_transition_causal_fragment(
        conn, second, campaign_id="other-training")
    try:
        consolidate_causal_path(conn, [first_fragment, second_fragment])
    except ValueError as exc:
        assert "one explicit learner campaign" in str(exc)
    else:  # pragma: no cover - assertion keeps the failure diagnostic explicit
        raise AssertionError("mixed learner campaigns were consolidated")


def test_causal_path_rejects_tampered_fragment_witness(tmp_tehm):
    conn, store, _ = tmp_tehm
    transition_id = capture(
        conn, store, build_rtl_execution_record(
            RTL_PROJECT, oracle=None, store=store)).transition_id
    fragment = build_transition_causal_fragment(conn, transition_id)
    conn.execute(
        "UPDATE tehm_causal_nodes SET payload_json='{}' WHERE causal_node_id=?",
        (fragment.node_ids[0],))
    conn.commit()
    try:
        consolidate_causal_path(conn, [fragment])
    except ValueError as exc:
        assert "node witness conflicts" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("tampered causal node was accepted")
