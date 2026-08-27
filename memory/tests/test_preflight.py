"""Crystallizability preflight (design doc 6.3, 26 Phase 4, test list 27.1).

Metrics: singleton rate, CC_raw, CC_lineage, key precision/recall. The 5 output
files (groups.json / group_report.md / group_size.csv / lineage_support.csv /
manual_audit_sample.json) are written; the verdict is honest (an instance-
dominated corpus is NOT declared crystallizable).
"""
from __future__ import annotations

import json
from pathlib import Path

from tehm.adapters.r2g_evidence import capture_r2g_project
from tehm.canonical.capture import ExecutionRecord, capture
from tehm.crystallization.preflight import run_preflight

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ANTENNA_PROJ = FIXTURES / "project_antenna_fix"


def _repeat_record(base: dict, i: int) -> dict:
    """A variant of the sample antenna fix sharing the effect (family + outcome)
    but from a distinct lineage/design, differing only in instance-specific
    knob values and divergence point."""
    rec = json.loads(json.dumps(base))
    rec["record_id"] = f"repeat_{i}"
    rec["lineage_id"] = f"lineage_{i}"
    rec["design_id"] = f"design_{i}"
    rec["episode"] = {"episode_id": f"ep_repeat_{i}", "lineage_id": f"lineage_{i}",
                      "step_index": 0, "terminal_status": "VERIFIED_REPAIR"}
    rec["before"]["config"]["PLACE_DENSITY_LB_ADDON"] = f"0.1{i}"
    rec["after"]["config"]["PLACE_DENSITY_LB_ADDON"] = f"0.1{i}"
    rec["observation_delta"]["first_divergence"]["before"] = 10 + i
    return rec


def _capture_repeats(tmp_tehm, sample_record_dict, n: int = 4):
    conn, store, _ = tmp_tehm
    for i in range(n):
        capture(conn, store, ExecutionRecord.from_dict(_repeat_record(sample_record_dict, i)))


# -- metrics ------------------------------------------------------------------

def test_empty_store_verdict(tmp_tehm):
    conn, _, _ = tmp_tehm
    report = run_preflight(conn)
    assert report.verdict == "empty"
    assert report.total_transitions == 0


def test_instance_dominated_corpus(tmp_tehm):
    """The 3-strategy antenna fixture is all singletons -> honest negative verdict."""
    conn, store, _ = tmp_tehm
    capture_r2g_project(conn, store, ANTENNA_PROJ)
    report = run_preflight(conn)
    assert report.total_transitions == 3
    assert report.singleton_rate == 1.0
    assert report.cc_raw == 0.0
    assert report.verdict == "instance_dominated"


def test_repeat_corpus_crystallizable(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    _capture_repeats((conn, store, _), sample_record_dict, n=4)
    report = run_preflight(conn)
    assert report.total_transitions == 4
    assert report.num_groups == 1
    assert report.singleton_rate == 0.0
    assert report.cc_raw == 1.0
    assert report.cc_lineage == 1.0
    assert report.key_recall == 1.0
    assert report.verdict == "crystallizable"


def test_mixed_corpus_detects_only_repeat_group(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    capture_r2g_project(conn, store, ANTENNA_PROJ)          # 3 singletons
    _capture_repeats((conn, store, _), sample_record_dict, n=4)  # 1 group of 4
    report = run_preflight(conn)
    assert report.total_transitions == 7
    assert report.num_groups == 4
    sizes = sorted(g["size"] for g in report.groups.values())
    assert sizes == [1, 1, 1, 4]
    assert 0 < report.cc_raw < 1
    assert report.cc_lineage > 0


def test_key_precision_is_outcome_homogeneity(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    _capture_repeats((conn, store, _), sample_record_dict, n=4)
    report = run_preflight(conn)
    # All 4 members share outcome PASS -> precision 1.0
    assert report.key_precision == 1.0


def test_group_profile_fields(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    _capture_repeats((conn, store, _), sample_record_dict, n=4)
    report = run_preflight(conn)
    group = next(iter(report.groups.values()))
    assert group["size"] == 4
    assert group["unique_lineages"] == 4
    assert group["dominant_outcome"] == "PASS"
    assert len(group["transition_ids"]) == 4


# -- outputs ------------------------------------------------------------------

def test_outputs_written(tmp_tehm, sample_record_dict, tmp_path):
    conn, store, _ = tmp_tehm
    capture_r2g_project(conn, store, ANTENNA_PROJ)
    _capture_repeats((conn, store, _), sample_record_dict, n=4)
    out = tmp_path / "preflight"
    run_preflight(conn, out_dir=out)
    for name in ("groups.json", "group_report.md", "group_size.csv",
                 "lineage_support.csv", "manual_audit_sample.json"):
        assert (out / name).exists(), name
    groups = json.loads((out / "groups.json").read_text())
    assert groups["num_groups"] == 4
    audit = json.loads((out / "manual_audit_sample.json").read_text())
    assert audit["verdict"] in ("crystallizable", "crystallizable_raw_only")
    report_md = (out / "group_report.md").read_text()
    assert "## Verdict:" in report_md
    csv_text = (out / "group_size.csv").read_text()
    assert "effect_key,size" in csv_text


def test_preflight_deterministic(tmp_tehm, sample_record_dict):
    conn, store, _ = tmp_tehm
    _capture_repeats((conn, store, _), sample_record_dict, n=4)
    r1 = run_preflight(conn)
    r2 = run_preflight(conn)
    assert r1.to_dict() == r2.to_dict()
