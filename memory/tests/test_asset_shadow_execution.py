"""End-to-end C4/C5 shadow execution on independent RTL lineages."""
from __future__ import annotations

import hashlib
import json
import shutil
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


@pytest.mark.parametrize("heldout_answer", ["deleted", "poisoned"])
def test_real_rtl_gap_to_candidate_stays_non_production(tmp_tehm, heldout_answer):
    if not IcarusOracle().available:
        pytest.skip("Icarus unavailable")
    conn, _, root = tmp_tehm
    source = root / "source.sqlite"
    destination = sqlite3.connect(source)
    conn.backup(destination)
    destination.close()
    conn.close()
    before_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    heldout = root / "req_ack_bug3"
    shutil.copytree(PROJECTS / "req_ack_bug3", heldout)
    manifest_path = heldout / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if heldout_answer == "deleted":
        manifest.pop("fix")
    else:
        manifest["fix"] = {"module": "wrong", "add_condition": "1'b0"}
    manifest_path.write_text(json.dumps(manifest))
    report = build_rtl_asset_gap_shadow(
        source,
        output_dir=root / "asset-gap-shadow",
        training_projects=[PROJECTS / "req_ack_bug",
                           PROJECTS / "req_ack_bug2"],
        heldout_projects=[heldout],
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
    assert report["claim_scope"] == "alpha_equivalent_fixture_transfer"
    assert report["l3_capability_expansion_established"] is False
    assert report["firewall"]["heldout_binding_reads_manifest"] is False
    bound = report["heldout_validation"][0]["asset"]
    assert bound["definition"]["action"]["payload"]["add_condition"] == "rd_ack"
    assert bound["provenance"]["binding_source"] == "rtl_source"
    assert all(item["oracle_executed_restored_source"] is True
               for item in report["rollback_receipt"]["entries"])
    assert report["non_target_compatibility"][0]["regression_preserved"] is True
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_digest
    assert not list(root.glob("source.sqlite-*"))
    with pytest.raises(ValueError, match="output directory must be empty"):
        build_rtl_asset_gap_shadow(
            source, output_dir=root,
            training_projects=[PROJECTS / "req_ack_bug", PROJECTS / "req_ack_bug2"],
            heldout_projects=[heldout], non_target_projects=[PROJECTS / "valid_ready_bug"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_digest


def test_empty_file_is_not_a_canonical_snapshot(tmp_path):
    source = tmp_path / "empty.sqlite"
    source.touch()
    with pytest.raises(RuntimeError, match="schema mismatch"):
        build_rtl_asset_gap_shadow(
            source, output_dir=tmp_path / "output",
            training_projects=[PROJECTS / "req_ack_bug", PROJECTS / "req_ack_bug2"],
            heldout_projects=[PROJECTS / "req_ack_bug3"],
            non_target_projects=[PROJECTS / "valid_ready_bug"])
    assert source.read_bytes() == b""
    assert not (tmp_path / "output").exists()
