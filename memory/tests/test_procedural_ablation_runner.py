"""Unit contracts for the P3 component-gate harness."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_procedural_ablation import _gate_evidence  # noqa: E402


def test_role_collision_is_unknown_to_role_ablated_arm_only():
    evidence = {"role_compatible": False, "predicate_status": "TRUE",
                "candidate_validity_status": "PROVISIONAL_VALID"}
    assert _gate_evidence(evidence, "M8")["status"] == "INAPPLICABLE"
    assert _gate_evidence(evidence, "M5")["passed"] is True


def test_unknown_predicate_never_defaults_to_false_or_pass():
    evidence = {"role_compatible": True, "predicate_status": "UNKNOWN",
                "candidate_validity_status": "PROVISIONAL_VALID"}
    gate = _gate_evidence(evidence, "M8")
    assert gate["status"] == "UNRESOLVED"
    assert gate["decisions"][0]["reason"] == "UNKNOWN_not_false"
    assert _gate_evidence(evidence, "M6")["passed"] is True


def test_degenerate_candidate_only_runs_when_validity_gate_is_ablated():
    evidence = {"role_compatible": True, "predicate_status": "TRUE",
                "candidate_validity_status": "REJECT_DEGENERATE"}
    assert _gate_evidence(evidence, "M8")["status"] == "REJECTED"
    assert _gate_evidence(evidence, "M4")["passed"] is True
