"""Tests for compute_label_stats.py — per-design label statistics."""
from __future__ import annotations

import json

import compute_label_stats as cls


def test_numeric_summary_percentiles():
    s = cls.numeric_summary([float(i) for i in range(1, 101)])  # 1..100
    assert s["min"] == 1.0
    assert s["max"] == 100.0
    assert abs(s["mean"] - 50.5) < 1e-9
    assert abs(s["p50"] - 50.5) < 1e-6   # linear interp midpoint of 1..100
    assert abs(s["p90"] - 90.1) < 1e-6


def test_numeric_summary_empty_is_none():
    assert cls.numeric_summary([]) is None


def test_summarize_wirelength_counts_mask(tmp_path):
    (tmp_path / "wirelength.csv").write_text(
        "Design,Net,NetType,WireLength_um,label,mask_wl\n"
        "d,n1,SIGNAL,3.0,1.386,true\n"
        "d,n2,CLOCK,5.0,1.792,false\n"
    )
    res = cls.summarize(str(tmp_path), "wirelength", cls.SPECS["wirelength"])
    assert res["status"] == "ok"
    assert res["rows"] == 2
    assert res["signal_nets"] == 1
    assert res["masked_nets"] == 1
    assert res["label"]["max"] > res["label"]["min"]


def test_summarize_missing_csv_is_skipped(tmp_path):
    res = cls.summarize(str(tmp_path), "timing", cls.SPECS["timing"])
    assert res["status"] == "skipped"


def test_build_report_writes_json(tmp_path):
    (tmp_path / "ir_drop.csv").write_text(
        "Design,Cell,X,Y,Voltage_V,IR_Drop_mV,P95_mV,label,has_irdrop\n"
        "d,c1,0,0,1.09,10.0,12.0,0.69,true\n"
    )
    out = tmp_path / "labels_stats.json"
    cls.build_report(str(tmp_path), str(out), design="d", platform="nangate45")
    data = json.loads(out.read_text())
    assert data["design"] == "d"
    assert data["platform"] == "nangate45"
    assert data["labels"]["irdrop"]["status"] == "ok"
    assert data["labels"]["irdrop"]["has_irdrop"] is True
    assert data["labels"]["congestion"]["status"] == "skipped"


def test_summarize_raw_unprocessed_csv_is_invalid(tmp_path):
    # A killed extractor can leave the raw tool dump at the canonical path
    # (2026-07-05 irdrop incident: PDNSim's voltage file, no Design/Cell/label
    # columns). That must NOT read as an ok label set.
    (tmp_path / "ir_drop.csv").write_text(
        "Instance,Terminal,Layer,X location,Y location,Voltage\n"
        "FILLER_1,VPWR,li1,1.0,2.0,1.8\n"
        "_123_,VPWR,li1,3.0,4.0,1.79\n"
    )
    res = cls.summarize(str(tmp_path), "irdrop", cls.SPECS["irdrop"])
    assert res["status"] == "invalid"
    assert "label" in res["reason"] and "missing" in res["reason"]
    assert res["rows"] == 2


def test_summarize_nonnumeric_label_is_invalid(tmp_path):
    (tmp_path / "wirelength.csv").write_text(
        "Design,Net,NetType,WireLength_um,label,mask_wl\n"
        "d,n1,SIGNAL,3.0,oops,true\n"
    )
    res = cls.summarize(str(tmp_path), "wirelength", cls.SPECS["wirelength"])
    assert res["status"] == "invalid"
    assert "no numeric values" in res["reason"]
