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
    record_causal_transfer,
    verify_causal_transfer,
)
from tehm.lifecycle import (
    build_causal_transfer_evidence, record_rule_authority,
    verify_rule_authority)
from tehm.lifecycle.rule_authority import _derive_gate_inputs
from tehm.lifecycle.trial_adapter import record_external_trial
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


def test_l4_transfer_rejects_non_string_transition_ids(tmp_tehm, tmp_path):
    report = _training_replication(tmp_tehm, tmp_path)
    conn = db.connect(report["derived_db"])
    try:
        receipt = evaluate_transfer_supported_mechanism(
            conn, report["path"]["path_id"], [123],
            training_campaign_id="l4-training")
    finally:
        conn.close()
    assert receipt.eligible is False
    assert receipt.reason == "malformed_transfer_transitions"


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


def test_causal_transfer_ledger_replays_and_binds_path(tmp_tehm, tmp_path):
    """A transfer receipt is durable shadow evidence, not path authority."""
    report = _training_replication(tmp_tehm, tmp_path)
    conn = db.connect(report["derived_db"])
    db.ensure_schema(conn)
    store = ArtifactStore(tmp_path / "transfer-ledger-artifacts")
    before = _completed_orfs_project(
        tmp_path, "heldout_ledger_before", 50, route_status="fail", make_status=1)
    after = _completed_orfs_project(tmp_path, "heldout_ledger_after", 40)
    transfer = build_orfs_pair_record(
        before, after, lineage_id="orfs-l4:heldout-ledger",
        config_edits={"CORE_UTILIZATION": "40"})
    transition_id = capture(
        conn, store, transfer, dataset_campaign_id="l4-heldout-ledger",
        dataset_split="heldout", dataset_learner_eligible=False).transition_id
    path_id = report["path"]["path_id"]
    before_path_digest = conn.execute(
        "SELECT path_digest FROM tehm_causal_paths WHERE path_id=?", (path_id,)
    ).fetchone()[0]

    ledger = record_causal_transfer(
        conn, path_id=path_id, transfer_transition_ids=[transition_id],
        training_campaign_id="l4-training",
        transfer_campaign_id="l4-heldout-ledger")
    checked = verify_causal_transfer(conn, ledger)
    assert checked["verified"] is True
    assert checked["eligible"] is True
    assert ledger.transfer_receipt_id.startswith("causal_transfer_")
    assert conn.execute(
        "SELECT path_digest FROM tehm_causal_paths WHERE path_id=?", (path_id,)
    ).fetchone()[0] == before_path_digest
    assert conn.execute(
        "SELECT COUNT(*) FROM tehm_causal_transfer_receipts"
    ).fetchone()[0] == 1

    tampered = ledger.to_dict()
    tampered["payload"] = dict(tampered["payload"])
    tampered["payload"]["eligible"] = False
    assert verify_causal_transfer(conn, tampered)["verified"] is False

    # The wrapper's convenience projection is also signed data.  Mutating it
    # must not be accepted merely because the nested payload remains intact.
    for field_name, value in (("eligible", False),
                              ("evidence_level", "L3_REPLICATED_EFFECT"),
                              ("reason", "tampered")):
        projection_tampered = ledger.to_dict()
        projection_tampered[field_name] = value
        checked_projection = verify_causal_transfer(conn, projection_tampered)
        assert checked_projection["verified"] is False
        assert f"transfer_{field_name}_mismatch" in checked_projection["reasons"]

    nested_tampered = ledger.to_dict()
    nested_tampered["transfer_receipt"] = dict(nested_tampered["transfer_receipt"])
    nested_tampered["transfer_receipt"]["reason"] = "tampered"
    checked_nested = verify_causal_transfer(conn, nested_tampered)
    assert checked_nested["verified"] is False
    assert "transfer_receipt_projection_mismatch" in checked_nested["reasons"]

    # SQLite's dynamic typing still permits a copied/altered ledger to store
    # truthy text in an INTEGER boolean column when checks are bypassed.  The
    # reader must reject that row instead of returning ``eligible=True``.
    conn.execute("PRAGMA ignore_check_constraints=ON")
    conn.execute(
        "UPDATE tehm_causal_transfer_receipts SET eligible='false' "
        "WHERE transfer_receipt_id=?", (ledger.transfer_receipt_id,))
    conn.commit()
    weak_storage = verify_causal_transfer(conn, ledger.to_dict())
    assert weak_storage["verified"] is False
    assert any(reason.startswith("transfer_receipt_row_malformed:")
               for reason in weak_storage["reasons"])

    # Transition witness vectors are typed replay data too.  A copied ledger
    # that replaces an ID string with an integer must fail before the receipt
    # can be loaded as a valid transfer witness.
    conn.execute(
        "UPDATE tehm_causal_transfer_receipts "
        "SET eligible=1, training_transition_ids_json='[1]' "
        "WHERE transfer_receipt_id=?", (ledger.transfer_receipt_id,))
    conn.commit()
    weak_vector = verify_causal_transfer(conn, ledger.to_dict())
    assert weak_vector["verified"] is False
    assert any(reason.startswith("transfer_receipt_row_malformed:")
               for reason in weak_vector["reasons"])
    conn.close()


def test_verified_l4_receipt_is_the_only_transfer_authority_projection(
        tmp_tehm, tmp_path):
    """Rule authority consumes replayed L4 lineage vectors, not a caller flag."""
    report = _training_replication(tmp_tehm, tmp_path)
    conn = db.connect(report["derived_db"])
    db.ensure_schema(conn)
    store = ArtifactStore(tmp_path / "transfer-authority-artifacts")
    before = _completed_orfs_project(
        tmp_path, "heldout_authority_before", 50, route_status="fail", make_status=1)
    after = _completed_orfs_project(tmp_path, "heldout_authority_after", 40)
    transfer = build_orfs_pair_record(
        before, after, lineage_id="orfs-l4:heldout-authority",
        config_edits={"CORE_UTILIZATION": "40"})
    transition_id = capture(
        conn, store, transfer, dataset_campaign_id="l4-heldout-authority",
        dataset_split="heldout", dataset_learner_eligible=False).transition_id
    ledger = record_causal_transfer(
        conn, path_id=report["path"]["path_id"],
        transfer_transition_ids=[transition_id],
        training_campaign_id="l4-training",
        transfer_campaign_id="l4-heldout-authority")
    entries = build_causal_transfer_evidence(
        conn, [ledger.transfer_receipt_id])
    inputs, details = _derive_gate_inputs(
        {"cross_lineage_te": entries}, (), rule_row=None, status=None,
        expected_status_version=None, rule_digest=None,
        min_obligation_coverage=1.0, min_cross_lineage_te=1.0,
        max_harmful_rate=0.0, min_conformal_coverage=0.8)
    assert inputs["cross_lineage_te"] == 1.0
    assert details["causal_transfer_count"] == 1
    assert len(details["causal_transfer_training_lineages"]) == 2
    # An L4 receipt is not universal authority for every candidate rule.  The
    # selected rule must expose the same mechanism/action domain and source
    # transition witnesses in the training campaign.
    source_ids = [row[0] for row in conn.execute(
        "SELECT transition_id FROM tehm_dataset_membership "
        "WHERE campaign_id='l4-training' AND split='training' "
        "AND learner_eligible=1 ORDER BY transition_id LIMIT 2")]
    path_family = report["path"]["mechanism_family"]
    rule_columns = (
        "rule_id, domain, before_pattern_json, after_pattern_json, "
        "hard_preconditions_json, context_profile_json, obligations_json, "
        "validity_status, validity_profile_json, confidence_json, utility_json, "
        "risk_profile_json, predicate_schema_version, role_schema_version, "
        "crystallizer_version, merge_trace_digest, created_at, updated_at")
    rule_values = (
        "rule_transfer_binding", "flow.signoff",
        json.dumps({"action_domain": "flow.CONFIG_DELTA",
                    "type": path_family}),
        json.dumps({"action_domain": "flow.CONFIG_DELTA",
                    "type": path_family}),
        "[]", "{}", "[]", "PROVISIONAL_VALID", "{}", "{}", "{}", "[]",
        "predicate-v0.1", "role-v0.1", "test", "test", "now", "now")
    conn.execute(
        f"INSERT INTO tehm_rules ({rule_columns}) VALUES ({','.join('?' for _ in rule_values)})",
        rule_values)
    conn.execute(
        "INSERT INTO tehm_rule_sources "
        "(rule_id, episode_id, source_substitution_json, evidence_profile_json, lineage_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("rule_transfer_binding", "episode-transfer-binding",
         json.dumps({source_id: {} for source_id in source_ids}),
         "{}", "training-lineage"))
    bound = build_causal_transfer_evidence(
        conn, [ledger.transfer_receipt_id], rule_id="rule_transfer_binding")
    assert bound[0]["payload"]["rule_binding"]["transfer_action_domains"] == [
        "flow.CONFIG_DELTA"]
    conn.execute(
        "INSERT INTO tehm_rule_status "
        "(rule_id, target_scope, status, status_version, provenance_json, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("rule_transfer_binding", "route", "candidate", 1, "{}", "now"))
    trial = record_external_trial(
        conn, rule_id="rule_transfer_binding", target_scope="route",
        verdict="win", metrics={"arms_differ": True,
                                 "obligation_coverage": 1.0,
                                 "created_regressions": []},
        status_version=1, trial_uuid="transfer-authority-trial",
        arm_a_run_id="a", arm_b_run_id="b")
    authority = record_rule_authority(
        conn, rule_id="rule_transfer_binding", target_scope="route",
        evidence={}, trial_id=trial["trial_id"], expected_status_version=1,
        causal_transfer_receipt_ids=[ledger.transfer_receipt_id])
    assert authority.gate_status["cross_lineage_te"] == "PASS"
    assert authority.eligible is False
    assert "cross_lineage_te" not in authority.not_established
    replayed = verify_rule_authority(conn, authority)
    assert replayed["eligible"] is False
    assert replayed["checks"]["cross_lineage_te"] is True
    conn.execute(
        "UPDATE tehm_rules SET before_pattern_json=?, after_pattern_json=? "
        "WHERE rule_id=?", (
            json.dumps({"action_domain": "flow.BASELINE_CONTROL",
                        "type": path_family}),
            json.dumps({"action_domain": "flow.BASELINE_CONTROL",
                        "type": path_family}),
            "rule_transfer_binding"))
    try:
        build_causal_transfer_evidence(
            conn, [ledger.transfer_receipt_id], rule_id="rule_transfer_binding")
    except ValueError as exc:
        assert "rule_binding_action_domain_mismatch" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unrelated rule accepted an L4 transfer receipt")
    try:
        build_causal_transfer_evidence(conn, ["missing-transfer-receipt"])
    except ValueError as exc:
        assert "transfer_receipt_missing" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing L4 receipt bypassed authority projection")
    conn.close()


def test_causal_transfer_ledger_keeps_ineligible_receipt_auditable(
        tmp_tehm, tmp_path):
    """Missing full ORFS proof is recorded and remains non-promotable."""
    report = _training_replication(tmp_tehm, tmp_path)
    conn = db.connect(report["derived_db"])
    db.ensure_schema(conn)
    store = ArtifactStore(tmp_path / "transfer-ledger-negative-artifacts")
    before = _completed_orfs_project(
        tmp_path, "heldout_ledger_bad_before", 50, route_status="fail", make_status=1)
    after = _completed_orfs_project(tmp_path, "heldout_ledger_bad_after", 40)
    transfer = build_orfs_pair_record(
        before, after, lineage_id="orfs-l4:heldout-ledger-bad",
        config_edits={"CORE_UTILIZATION": "40"})
    transition_id = capture(
        conn, store, transfer, dataset_campaign_id="l4-heldout-ledger-bad",
        dataset_split="heldout", dataset_learner_eligible=False).transition_id
    ledger = record_causal_transfer(
        conn, path_id=report["path"]["path_id"],
        transfer_transition_ids=[transition_id],
        training_campaign_id="l4-training",
        transfer_campaign_id="l4-heldout-ledger-bad",
        require_full_oracle=True)
    checked = verify_causal_transfer(conn, ledger.to_dict())
    assert ledger.eligible is False
    assert checked["verified"] is True
    assert checked["eligible"] is False
    assert ledger.transfer_receipt["promotion_eligible"] is False
    conn.close()


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
