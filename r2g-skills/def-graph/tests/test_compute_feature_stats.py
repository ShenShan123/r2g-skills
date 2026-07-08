"""Tests for compute_feature_stats.py — per-design feature statistics."""
from __future__ import annotations

import json

import compute_feature_stats as cfs


def test_numeric_summary_percentiles():
    s = cfs.numeric_summary([float(i) for i in range(1, 101)])  # 1..100
    assert s["min"] == 1.0
    assert s["max"] == 100.0
    assert abs(s["mean"] - 50.5) < 1e-9
    assert abs(s["p90"] - 90.1) < 1e-6


def test_numeric_summary_empty_is_none():
    assert cfs.numeric_summary([]) is None


def test_summarize_nodes_gate(tmp_path):
    (tmp_path / "nodes_gate.csv").write_text(
        "graph_id,inst_name,master,cell_type_id,cell_area,cell_power,x_um,y_um,orientation,orientation_id,placement_status,placement_status_id\n"
        "d,i1,INV_X1,0,0.5,1.0,0,0,N,0,PLACED,0\n"
        "d,i2,BUF_X1,6,1.5,3.0,0,0,N,0,PLACED,0\n"
    )
    res = cfs.summarize(str(tmp_path), "nodes_gate")
    assert res["status"] == "ok"
    assert res["rows"] == 2
    assert res["cell_area"]["min"] == 0.5
    assert res["cell_area"]["max"] == 1.5


def test_summarize_metadata_surfaces_scalars(tmp_path):
    (tmp_path / "metadata.csv").write_text(
        "graph_id,num_cells,num_nets,num_ios,avg_fanout,die_width,die_height,core_area,dbu_unit,PLACE_DENSITY,CORE_UTILIZATION,ABC_AREA,C_total,tracks_per_layer,V_nom,freq_Hz\n"
        "d,100,90,12,1.8,10,10,100,2000,Default,40,0,1234.5,metal1:10,1.10,100000000\n"
    )
    res = cfs.summarize(str(tmp_path), "metadata")
    assert res["status"] == "ok"
    assert res["num_cells"] == 100.0
    assert res["num_ios"] == 12.0
    assert abs(res["C_total"] - 1234.5) < 1e-6


def test_summarize_missing_csv_is_skipped(tmp_path):
    res = cfs.summarize(str(tmp_path), "nodes_net")
    assert res["status"] == "skipped"
    assert res["reason"] == "csv missing"


def test_build_report_writes_json_with_spef_flag(tmp_path):
    (tmp_path / "nodes_net.csv").write_text(
        "graph_id,net_name,net_type_id,fanout,pin_count,num_drivers,num_sinks,connects_macro_flag,num_layer,hpwl_um\n"
        "d,n1,0,2,3,1,2,0,3,5.0\n"
    )
    out = tmp_path / "features_stats.json"
    cfs.build_report(str(tmp_path), str(out), design="d", platform="nangate45", spef_present=False)
    data = json.loads(out.read_text())
    assert data["design"] == "d"
    assert data["platform"] == "nangate45"
    assert data["spef_present"] is False
    assert data["features"]["nodes_net"]["status"] == "ok"
    assert data["features"]["nodes_net"]["fanout"]["max"] == 2.0
    assert data["features"]["metadata"]["status"] == "skipped"
