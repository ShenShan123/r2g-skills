"""Real RTL C1-C8 attribution stays evaluation-only."""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

from tehm.rtl.rtl_oracle import IcarusOracle

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from build_rtl_asset_gap_shadow import build_rtl_asset_gap_shadow  # noqa: E402
from build_rtl_capability_attribution import (  # noqa: E402
    build_rtl_capability_attribution,
)


PROJECTS = Path(__file__).resolve().parent / "fixtures" / "rtl_projects"


def test_real_rtl_capability_attribution_is_read_only(tmp_tehm):
    if not IcarusOracle().available:
        import pytest
        pytest.skip("Icarus unavailable")
    conn, _, root = tmp_tehm
    source = root / "source.sqlite"
    destination = sqlite3.connect(source)
    conn.backup(destination)
    destination.close()
    conn.close()
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    asset_report = build_rtl_asset_gap_shadow(
        source, output_dir=root / "asset-gap-shadow",
        training_projects=[PROJECTS / "req_ack_bug",
                           PROJECTS / "req_ack_bug2"],
        heldout_projects=[PROJECTS / "req_ack_bug3"],
        non_target_projects=[PROJECTS / "valid_ready_bug"],
    )
    report = build_rtl_capability_attribution(
        source, output_dir=root / "capability-attribution",
        asset_gap_report=(root / "asset-gap-shadow" /
                          "asset_gap_shadow_report.json"),
        training_projects=[PROJECTS / "req_ack_bug",
                           PROJECTS / "req_ack_bug2"],
        heldout_projects=[PROJECTS / "req_ack_bug3"],
        non_target_projects=[PROJECTS / "valid_ready_bug"],
    )

    assert report["attribution"]["attribution"]["gates"] == {
        "C1": True, "C2": True, "C3": True, "C4": True,
        "C5": True, "C6": True, "C7": True, "C8": True,
    }
    assert report["capability_authority_gates"]["eligible"] is True
    assert report["capability"]["status"] == "candidate"
    assert report["promotion_attempted"] is False
    assert report["production_promotion_eligible"] is False
    assert report["canonical_memory_mutation"] == "none"
    assert report["firewall"]["disjoint"] is True
    assert report["ablation"]["gain_without_memory"] is False
    assert report["ablation"]["gain_with_memory"] is True
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before

    candidate = report["runtime_behavior"]["candidate"]["decisions"]
    by_lineage = {item["lineage_id"]: item for item in candidate}
    assert by_lineage["req_ack_fsm"]["oracle_verdict"] == "PASS"
    assert by_lineage["req_ack_fsm3"]["oracle_verdict"] == "PASS"
    assert by_lineage["valid_ready_fsm"]["status"] == "INAPPLICABLE"
