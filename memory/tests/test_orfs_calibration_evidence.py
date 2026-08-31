from __future__ import annotations

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_orfs_calibration_evidence import (  # noqa: E402
    _authority_lineages, _bind_routing_preflight, _conformal_for_sample,
    _source_disjoint_overlap)


def test_authority_lineages_are_read_from_states(tmp_path):
    db = tmp_path / "authority.sqlite"
    conn = __import__("sqlite3").connect(db)
    conn.execute("CREATE TABLE tehm_states (lineage_id TEXT)")
    conn.executemany("INSERT INTO tehm_states VALUES (?)", [
        ("lineage:a",), ("lineage:a",), ("lineage:b",), (None,), ("",)])
    conn.commit()
    assert _authority_lineages(conn) == {"lineage:a", "lineage:b"}
    conn.close()


def test_source_disjoint_overlap_is_exact_and_sorted():
    assert _source_disjoint_overlap(
        ["lineage:b", "lineage:a"], ["lineage:c", "lineage:a", "lineage:a"]
    ) == ["lineage:a"]


def test_row_conformal_coverage_is_derived_from_finite_metrics():
    sample = {
        "case_id": "sky130hs:crc:0",
        "predicted": {"wns_ns": 0.0, "area_um2": 2.0, "congestion": None},
        "observed_deltas": {"wns_ns": 0.0, "area_um2": 2.1, "congestion": None},
    }
    assert _conformal_for_sample(
        sample, {"area_um2": 0.2, "wns_ns": 0.0, "congestion": 1.0}) == {
            "covered": 2, "total": 2, "coverage": 1.0}


def test_row_conformal_coverage_fails_closed_without_finite_metric():
    sample = {
        "case_id": "sky130hs:empty:0",
        "predicted": {"wns_ns": None},
        "observed_deltas": {"wns_ns": None},
    }
    with pytest.raises(ValueError, match="no finite metric"):
        _conformal_for_sample(sample, {"wns_ns": 0.0})


def test_calibration_builder_binds_effective_routing_receipt(tmp_path):
    hook = tmp_path / "flow" / "platforms" / "asap7" / "fastroute.tcl"
    hook.parent.mkdir(parents=True)
    hook.write_text(
        "set_global_routing_layer_adjustment $::env(MIN_ROUTING_LAYER)-"
        "$::env(MAX_ROUTING_LAYER) $::env(ROUTING_LAYER_ADJUSTMENT)\n")
    before = tmp_path / "before" / "constraints"
    before.mkdir(parents=True)
    (before / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = asap7\n")
    row = {"case_id": "effective-routing", "record": {
        "verification": {}}}
    item = {
        "platform": "asap7",
        "config_edits": {"ROUTING_LAYER_ADJUSTMENT": "0.05"},
        "before_project": str(before.parent),
    }
    receipt = _bind_routing_preflight(row, item, orfs_root=tmp_path)
    assert receipt["status"] == "EFFECTIVE"
    assert row["record"]["verification"]["execution_preflight"]["digest"]


def test_calibration_builder_rejects_noop_routing_hook(tmp_path):
    hook = tmp_path / "flow" / "platforms" / "sky130hs" / "fastroute.tcl"
    hook.parent.mkdir(parents=True)
    hook.write_text(
        "set_global_routing_layer_adjustment $::env(MIN_ROUTING_LAYER)-"
        "$::env(MAX_ROUTING_LAYER) 0.2\n")
    before = tmp_path / "before" / "constraints"
    before.mkdir(parents=True)
    (before / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = sky130hs\n")
    with pytest.raises(ValueError, match="NO_OP"):
        _bind_routing_preflight(
            {"case_id": "noop-routing", "record": {"verification": {}}},
            {"platform": "sky130hs",
             "config_edits": {"ROUTING_LAYER_ADJUSTMENT": "0.05"},
             "before_project": str(before.parent)},
            orfs_root=tmp_path)
