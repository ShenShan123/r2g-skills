"""Support-cohort audit consumes only replay-verified L4 transfer evidence."""
from __future__ import annotations

import sqlite3
import sys
from types import SimpleNamespace
from pathlib import Path
import json
import hashlib

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import audit_orfs_support_cohort as support_audit


def _ledger(training, heldout):
    transfer = {
        "training_lineages": sorted(training),
        "transfer_lineages": sorted(heldout),
    }
    return SimpleNamespace(
        path_id="path:test",
        eligible=True,
        transfer_receipt=transfer,
        to_dict=lambda: {"transfer_receipt_id": "receipt:test"},
    )


def _ledger_db(tmp_path):
    path = tmp_path / "transfer.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tehm_causal_paths "
                 "(path_id TEXT, mechanism_family TEXT)")
    conn.execute("INSERT INTO tehm_causal_paths VALUES (?, ?)",
                 ("path:test", "ROUTING_CAPACITY_RECOVERY"))
    conn.commit()
    conn.close()
    return path


def _campaign_root(tmp_path, *, split="training", learner_eligible=True,
                   manifest_split=None, manifest_learner=None,
                   malformed_stored_learner=False,
                   include_source_freeze=True,
                   tamper_source_freeze=False):
    """Build one minimal full-oracle campaign for support-firewall tests."""
    root = tmp_path / f"campaign-{split}-{learner_eligible}"
    staging = root / "staging"
    staging.mkdir(parents=True)
    transition_id = "transition:test"
    manifest_split = split if manifest_split is None else manifest_split
    manifest_learner = (learner_eligible if manifest_learner is None
                        else manifest_learner)
    manifest = {
        "captured": [{
            "case_id": "case:test",
            "dataset_split": manifest_split,
            "family": "ROUTING_CAPACITY_RECOVERY",
            "learner_eligible": manifest_learner,
            "lineage_id": "lineage:test",
            "transition_id": transition_id,
        }],
        "orfs_root": str(root / "orfs"),
    }
    if include_source_freeze:
        freeze = {
            "version": "orfs-add-designs-source-freeze-v1",
            "request": {"orfs_root": str(root / "orfs"),
                        "toolchain_manifest": None},
            "source_code": [],
            "inputs": [],
            "source_tree_digest": "tree-digest",
            "input_digest": "input-digest",
        }
        freeze["freeze_digest"] = hashlib.sha256(
            json.dumps(freeze, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        freeze_path = root / "source_freeze.json"
        freeze_path.write_text(json.dumps(freeze, sort_keys=True))
        manifest["source_freeze"] = str(freeze_path)
        manifest["source_freeze_sha256"] = hashlib.sha256(
            freeze_path.read_bytes()).hexdigest()
        manifest["source_freeze_digest"] = freeze["freeze_digest"]
        if tamper_source_freeze:
            freeze["input_digest"] = "tampered"
            freeze_path.write_text(json.dumps(freeze, sort_keys=True))
    (root / "campaign_manifest.json").write_text(json.dumps(manifest))
    checks = {
        name: True for name in (
            "synthesis", "equivalence", "route", "finish", "timing",
            "drc", "lvs", "strict_signoff", "ppa", "graph",
            "artifact_digest", "input_binding", "timing_contract",
            "toolchain_binding",
        )
    }
    verifier = {
        "oracle_complete": True,
        "full_oracle": {
            "before": {"complete": True, "checks": checks},
            "after": {"complete": True, "checks": checks},
        },
    }
    conn = sqlite3.connect(staging / "tehm.sqlite")
    conn.executescript("""
        CREATE TABLE tehm_transitions (
            transition_id TEXT PRIMARY KEY,
            observation_delta_json TEXT,
            verifier_json TEXT,
            provenance_json TEXT,
            action_json TEXT
        );
        CREATE TABLE tehm_physical_effects (
            transition_id TEXT PRIMARY KEY,
            deltas_json TEXT
        );
        CREATE TABLE tehm_dataset_membership (
            transition_id TEXT,
            campaign_id TEXT,
            split TEXT,
            learner_eligible,
            PRIMARY KEY (transition_id, campaign_id)
        );
    """)
    conn.execute(
        "INSERT INTO tehm_transitions VALUES (?, ?, ?, ?, ?)",
        (transition_id, json.dumps({"utility_verdict": "NEUTRAL"}),
         json.dumps(verifier), json.dumps({"run_id": "run:test"}),
         json.dumps({"transformation_family": "ROUTING_CAPACITY_RECOVERY"})),
    )
    conn.execute(
        "INSERT INTO tehm_physical_effects VALUES (?, ?)",
        (transition_id, json.dumps({"wns_ns": 0.0})),
    )
    stored = "false" if malformed_stored_learner else int(learner_eligible)
    conn.execute(
        "INSERT INTO tehm_dataset_membership VALUES (?, ?, ?, ?)",
        (transition_id, "campaign:test", split, stored),
    )
    conn.commit()
    conn.close()
    return root


def test_l4_projector_requires_cohort_binding(monkeypatch, tmp_path):
    ledger_db = _ledger_db(tmp_path)
    ledger = _ledger({"train:a", "train:b"}, {"heldout:c"})
    monkeypatch.setattr(support_audit, "load_causal_transfer_receipt",
                        lambda conn, receipt_id: ledger)
    monkeypatch.setattr(
        support_audit, "verify_causal_transfer",
        lambda conn, receipt: {
            "verified": True, "eligible": True,
            "evidence_level": "L4_TRANSFER_SUPPORTED_MECHANISM",
            "path_id": "path:test", "reasons": [],
        })

    result = support_audit._audit_transfer_witness(
        ledger_db, ["receipt:test"],
        selected_lineages={"train:a", "train:b"},
        selected_families={"ROUTING_CAPACITY_RECOVERY"})
    assert result["gate_status"] == "PASS"
    assert result["training_lineages"] == ["train:a", "train:b"]
    assert result["transfer_lineages"] == ["heldout:c"]

    mismatch = support_audit._audit_transfer_witness(
        ledger_db, ["receipt:test"], selected_lineages={"unrelated"},
        selected_families={"ROUTING_CAPACITY_RECOVERY"})
    assert mismatch["gate_status"] == "FAIL"
    assert "transfer_training_lineage_cohort_mismatch" in mismatch["errors"][0]


def test_l4_projector_rejects_weak_lineage_vector(monkeypatch, tmp_path):
    ledger_db = _ledger_db(tmp_path)
    ledger = _ledger({"train:a", "train:b"}, {"heldout:c"})
    ledger.transfer_receipt["training_lineages"] = ["train:a", True]
    monkeypatch.setattr(support_audit, "load_causal_transfer_receipt",
                        lambda conn, receipt_id: ledger)
    monkeypatch.setattr(
        support_audit, "verify_causal_transfer",
        lambda conn, receipt: {
            "verified": True, "eligible": True,
            "evidence_level": "L4_TRANSFER_SUPPORTED_MECHANISM",
            "path_id": "path:test", "reasons": [],
        })
    result = support_audit._audit_transfer_witness(
        ledger_db, ["receipt:test"],
        selected_lineages={"train:a", "train:b"},
        selected_families={"ROUTING_CAPACITY_RECOVERY"})
    assert result["gate_status"] == "FAIL"
    assert any("training_lineages_malformed" in error
               for error in result["errors"])


def test_support_audit_replays_membership_and_rejects_heldout(tmp_path):
    root = _campaign_root(tmp_path, split="heldout", learner_eligible=False)
    report = support_audit.audit([root], negative_roots=[])

    assert report["support_observation_count"] == 0
    assert report["support_firewall_status"] == "FAIL"
    assert report["decision"] == "DENY_CANONICAL_IMPORT"
    reasons = report["support_firewall_errors"][0]["errors"][0]["reasons"]
    assert "membership_not_training_learner" in reasons


def test_support_audit_rejects_manifest_membership_mismatch(tmp_path):
    root = _campaign_root(
        tmp_path, split="training", learner_eligible=True,
        manifest_split="heldout", manifest_learner=False)
    report = support_audit.audit([root], negative_roots=[])

    assert report["support_observation_count"] == 0
    reasons = report["support_firewall_errors"][0]["errors"][0]["reasons"]
    assert "manifest_membership_split_mismatch" in reasons
    assert "manifest_membership_learner_flag_mismatch" in reasons


def test_support_audit_rejects_malformed_stored_learner_flag(tmp_path):
    root = _campaign_root(
        tmp_path, split="training", learner_eligible=True,
        malformed_stored_learner=True)
    report = support_audit.audit([root], negative_roots=[])

    assert report["support_observation_count"] == 0
    reasons = report["support_firewall_errors"][0]["errors"][0]["reasons"]
    assert any(reason.startswith("membership_invalid:") for reason in reasons)


def test_support_audit_accepts_only_replayed_training_membership(tmp_path):
    root = _campaign_root(tmp_path, split="training", learner_eligible=True)
    report = support_audit.audit([root], negative_roots=[])

    assert report["support_observation_count"] == 1
    assert report["support_firewall_status"] == "PASS"
    assert report["gate_status"]["obligation_coverage"] == "PASS"
    assert report["gate_status"]["harmful_rate"] == "PASS"
    assert report["all_gates_established"] is False


def test_support_audit_rejects_missing_source_freeze(tmp_path):
    root = _campaign_root(tmp_path, include_source_freeze=False)
    report = support_audit.audit([root], negative_roots=[])

    assert report["support_observation_count"] == 0
    assert report["campaigns"][0]["source_freeze_status"] == "FAIL"
    errors = report["campaigns"][0]["source_freeze_errors"]
    assert "source_freeze_missing" in errors
    assert report["decision"] == "DENY_CANONICAL_IMPORT"


def test_support_audit_rejects_tampered_source_freeze(tmp_path):
    root = _campaign_root(tmp_path, tamper_source_freeze=True)
    report = support_audit.audit([root], negative_roots=[])

    assert report["support_observation_count"] == 0
    errors = report["campaigns"][0]["source_freeze_errors"]
    assert "source_freeze_file_digest_mismatch" in errors
    assert "source_freeze_digest_mismatch" in errors
