"""A real Icarus receipt must produce intervention-level causal evidence."""
from __future__ import annotations

from pathlib import Path

import pytest

from tehm.causal.rtl import capture_rtl_causal_fragment
from tehm.rtl.rtl_oracle import IcarusOracle


def test_real_icarus_rtl_receipt_is_l1_and_oracle_traceable(tmp_tehm):
    conn, store, _ = tmp_tehm
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus unavailable")
    receipt = capture_rtl_causal_fragment(
        conn, store,
        Path(__file__).resolve().parent / "fixtures" / "rtl_projects" / "req_ack_bug",
        oracle=oracle)
    assert receipt.verifier["verdict"] == "PASS"
    assert receipt.fragment.evidence_level == "L1_EXECUTED_INTERVENTION"
    assert any(ref.startswith("oracle:") for ref in receipt.fragment.edges[0].evidence_refs)
    assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] == 1
