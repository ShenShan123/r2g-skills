"""End-to-end C4/C5 shadow execution on independent RTL lineages."""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

from tehm.rtl.rtl_oracle import IcarusOracle

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from build_rtl_asset_gap_shadow import build_rtl_asset_gap_shadow  # noqa: E402


PROJECTS = Path(__file__).resolve().parent / "fixtures" / "rtl_projects"


def test_real_rtl_gap_to_candidate_stays_non_production(tmp_tehm):
    if not IcarusOracle().available:
        pytest.skip("Icarus unavailable")
    conn, _, root = tmp_tehm
    source = root / "source.sqlite"
    destination = sqlite3.connect(source)
    conn.backup(destination)
    destination.close()
    conn.close()
    before_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = build_rtl_asset_gap_shadow(
        source,
        output_dir=root / "asset-gap-shadow",
        training_projects=[PROJECTS / "req_ack_bug",
                           PROJECTS / "req_ack_bug2"],
        heldout_projects=[PROJECTS / "req_ack_bug3"],
        non_target_projects=[PROJECTS / "valid_ready_bug"],
    )
    assert report["selected_gap"]["missing_asset_types"] == [
        "RTL_REWRITE_TEMPLATE"]
    assert report["candidate_status"]["status"] == "candidate"
    assert report["shadow_execution"] == {
        "training_pass": True, "heldout_pass": True,
        "no_regression": True, "independent_oracle": "icarus/vvp",
    }
    assert report["asset_authority_receipt"]["eligible"] is True
    assert report["asset_promotion_eligible"] is True
    assert report["promotion_attempted"] is False
    assert report["production_promotion_eligible"] is False
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_digest
