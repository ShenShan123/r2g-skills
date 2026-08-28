"""Support-cohort audit consumes only replay-verified L4 transfer evidence."""
from __future__ import annotations

import sqlite3
import sys
from types import SimpleNamespace
from pathlib import Path

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
