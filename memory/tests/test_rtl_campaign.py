"""Real RTL evaluation campaign (Phase 10, design doc 26 acceptance).

Runs the FULL loop on real Verilog designs + real iverilog/vvp:
    train capture -> crystallize + audit -> retrieve (held-out)
    -> activate (real guard-strengthen + real sim) -> new transition
    -> lifecycle shadow -> candidate -> A/B win -> strict promotion-gate hold

Skipped cleanly when iverilog/vvp is unavailable.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tehm.rtl.rtl_oracle import IcarusOracle

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_real_rtl_campaign_closed_loop(tmp_path, monkeypatch):
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("iverilog/vvp not available")
    import sys
    sys.path.insert(0, str(SCRIPTS.parent))
    from scripts.run_rtl_campaign import main as campaign_main

    monkeypatch.setenv("TEHM_DB", str(tmp_path / "tehm.sqlite"))
    monkeypatch.setenv("TEHM_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    rc = campaign_main(["--db", str(tmp_path / "tehm.sqlite"),
                        "--artifacts", str(tmp_path / "artifacts")])
    assert rc == 0

    from tehm import db as tehm_db
    conn = tehm_db.connect(tmp_path / "tehm.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0] >= 3
    assert conn.execute("SELECT COUNT(*) FROM tehm_rules").fetchone()[0] == 1
    # One direct activation plus three independently persisted A/B repeats.
    assert conn.execute("SELECT COUNT(*) FROM tehm_activations").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM tehm_trials").fetchone()[0] == 1
    rule = conn.execute(
        "SELECT rule_id, validity_status FROM tehm_rules").fetchone()
    assert rule["validity_status"] in ("PROVISIONAL_VALID", "VALIDATED")
    status = conn.execute(
        "SELECT status FROM tehm_rule_status WHERE rule_id=?",
        (rule["rule_id"],)).fetchone()
    assert status["status"] == "candidate"
    trial = conn.execute(
        "SELECT metrics_json FROM tehm_trials WHERE trial_uuid=?",
        ("rtl_campaign_external_v1",)).fetchone()
    gates = json.loads(trial["metrics_json"])["promotion_gates"]
    assert gates["eligible"] is False
    assert gates["checks"]["cross_lineage_te"] is False
    assert gates["checks"]["harmful_rate"] is False
    assert gates["checks"]["conformal_coverage"] is False
    conn.close()
