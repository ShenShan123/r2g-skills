"""Regression coverage for the COMPREHENSIVE graph-dataset verification added to
`tools/verify_graph_dataset.py` (Groups A/B/C):

  A  topology of ALL five views b-f (symmetry, self-loops, block-positional node
     order incl. the pin block, fwd/rev [fwd0,rev0,...] interleaving, d/e
     edge_attr content) — not just variant b;
  B  feature statistics (placement_status/fanout re-derivation, num_layer &
     tap-distance bounds, categorical enum/vocab coverage, and the stats-gate
     honesty check that features_stats.json/labels_stats.json reflect the CSVs);
  C  labels <-> surviving sign-off reports (DRC/LVS clean gate, ppa.json
     geometry, timing<->SDC clock-period transform, C_total & equiv_res vs SPEF).

Every check is exercised both CLEAN (passes) and CORRUPTED (fails) — a verifier
check that cannot fail is the exact silent lie this suite exists to prevent
(verifier-silent-lies-audit-2026-07-07.md). The synthetic mini-design drives the
REAL graph builder + the REAL verifier functions, so this is the blind-spot layer
for code paths the two real design_cases never reach.

Skips cleanly without torch/pandas, exactly as run_graphs.sh / the verifier do.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import sys

import pytest

pytest.importorskip("pandas")
pytest.importorskip("torch")
import pandas as pd  # noqa: E402
import torch  # noqa: E402

# conftest puts scripts/extract/{graph,...} on sys.path.
import build_graphs as bg  # noqa: E402
import graph_lib as gl  # noqa: E402

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "tools")
_spec = importlib.util.spec_from_file_location(
    "verify_graph_dataset", os.path.join(_TOOLS, "verify_graph_dataset.py"))
vgd = importlib.util.module_from_spec(_spec)
sys.modules["verify_graph_dataset"] = vgd
_spec.loader.exec_module(vgd)

DZ = "tiny"


def _wr(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _run(fn, *args):
    """Run a verifier check-group with a clean RESULTS/SKIPPED slate; return the
    list of failed check names."""
    vgd.RESULTS.clear()
    vgd.SKIPPED.clear()
    import io
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        fn(*args)
    return [r["check"] for r in vgd.RESULTS if not r["ok"]]


# --------------------------------------------------------------------------- #
# Synthetic mini-design: 2 INV gates, 2 signal nets (n0,n1), 1 iopin, full RC.  #
# --------------------------------------------------------------------------- #
def _make_features_labels(tmp_path):
    feat = tmp_path / "features"
    lab = tmp_path / "labels"
    feat.mkdir(); lab.mkdir()
    _wr(feat / "nodes_gate.csv",
        ["graph_id", "inst_name", "master", "cell_type_id", "cell_area", "cell_power",
         "x_um", "y_um", "orientation_id", "placement_status_id"],
        [[DZ, "g0", "INV_X1", 1, 1.0, 0.10, 0.0, 0.0, 0, 0],
         [DZ, "g1", "INV_X1", 1, 2.0, 0.20, 5.0, 0.0, 0, 0]])
    _wr(feat / "nodes_net.csv",
        ["graph_id", "net_name", "net_type_id", "fanout", "pin_count", "num_drivers",
         "num_sinks", "connects_macro_flag", "num_layer", "hpwl_um"],
        [[DZ, "n0", 0, 2, 3, 1, 2, 0, 1, 5.0],
         [DZ, "n1", 0, 1, 2, 1, 1, 0, 1, 5.0]])
    _wr(feat / "nodes_iopin.csv",
        ["graph_id", "iopin_name", "net_name", "net_type_id", "pin_x_um", "pin_y_um",
         "nearest_tap_distance_um", "pin_direction_id"],
        [[DZ, "out0", "n0", 0, 10.0, 0.0, 1.0, 1]])
    _wr(feat / "nodes_pin.csv",
        ["graph_id", "inst_name", "pin_name", "pin_type_id", "sum_pin_cap_fF"],
        [[DZ, "g0", "ZN", 4, 0.0], [DZ, "g0", "A", 0, 1.0],
         [DZ, "g1", "ZN", 4, 0.0], [DZ, "g1", "A", 0, 1.0]])
    _wr(feat / "edges_gate_pin.csv", ["graph_id", "inst_name", "pin_name"],
        [[DZ, "g0", "ZN"], [DZ, "g0", "A"], [DZ, "g1", "ZN"], [DZ, "g1", "A"]])
    _wr(feat / "edges_pin_net.csv",
        ["graph_id", "inst_name", "pin_name", "net_name", "net_type_id"],
        [[DZ, "g0", "ZN", "n0", 0], [DZ, "g1", "A", "n0", 0],
         [DZ, "g1", "ZN", "n1", 0], [DZ, "g0", "A", "n1", 0]])
    _wr(feat / "edges_iopin_net.csv", ["graph_id", "iopin_name", "net_name", "net_type_id"],
        [[DZ, "out0", "n0", 0]])
    _wr(feat / "metadata.csv",
        ["graph_id", "num_cells", "num_nets", "num_ios", "avg_fanout", "die_width",
         "die_height", "core_area", "dbu_unit", "PLACE_DENSITY", "CORE_UTILIZATION",
         "ABC_AREA", "C_total", "tracks_per_layer", "V_nom", "freq_Hz", "tracks_detail"],
        # C_total = Σground(30) + 2*Σcoupling(5) = 40 (symmetric-SPEF convention)
        [[DZ, 2, 2, 1, 1.5, 10, 10, 100, 2000, 0.6, 40, 0, 40.0, 1, 1.1, 1000000, "m1:1"]])
    _wr(lab / "wirelength.csv", ["Design", "Net", "NetType", "WireLength_um", "label", "mask_wl"],
        [[DZ, "n0", "SIGNAL", 5.0, math.log1p(5.0), "true"],
         [DZ, "n1", "SIGNAL", 5.0, math.log1p(5.0), "true"]])
    _wr(lab / "cell_congestion.csv", ["Design", "Cell", "cell_congestion", "label", "label_raw"],
        [[DZ, "g0", 0.5, 0.5, 0.25], [DZ, "g1", 0.6, 0.6, 0.30]])
    # timing: Path_Delay == max(0, period - slack); period = 2.0 below.
    _wr(lab / "ir_drop.csv", ["Design", "Cell", "IR_Drop_mV", "label", "P95_mV", "has_irdrop"],
        [[DZ, "g0", 1.0, math.log1p(1.0 / 2.0), 2.0, "true"],
         [DZ, "g1", 2.0, math.log1p(2.0 / 2.0), 2.0, "true"]])
    _wr(lab / "timing_features.csv", ["Design", "Cell", "Cell_Slack_ns", "Path_Delay_ns", "label", "in_sta_path"],
        [[DZ, "g0", 0.5, 1.5, math.log1p(1.5), "true"],
         [DZ, "g1", "INF", 0.0, 0.0, "false"]])
    _wr(lab / "net_ground_cap.csv", ["Design", "Net", "ground_cap_fF", "label"],
        [[DZ, "n0", 10.0, math.log1p(10.0)], [DZ, "n1", 20.0, math.log1p(20.0)]])
    _wr(lab / "coupling_cap.csv", ["Design", "Net1", "Net2", "coupling_cap_fF", "label"],
        [[DZ, "n0", "n1", 5.0, math.log1p(5.0)]])
    _wr(lab / "equiv_res.csv",
        ["Design", "Net", "Inst1", "Pin1", "Inst2", "Pin2", "equiv_res_ohm", "label"],
        [[DZ, "n0", "g0", "ZN", "g1", "A", 100.0, math.log1p(100.0)],
         [DZ, "n0", "g0", "ZN", "PIN", "out0", 50.0, math.log1p(50.0)],
         [DZ, "n1", "g1", "ZN", "g0", "A", 200.0, math.log1p(200.0)]])
    _wr(lab / "net_driver.csv", ["Design", "Net", "DrvInst", "DrvPin"],
        [[DZ, "n0", "g0", "ZN"], [DZ, "n1", "g1", "ZN"]])
    return str(feat), str(lab)


def _build_tensors(feat, lab):
    views7 = gl.build_feature_views(feat, DZ)
    label_dfs = gl.load_label_cache(lab)
    rc = gl.load_rc_label_cache(lab)
    return {v: bg.BUILDERS[v](views7, label_dfs, DZ, DZ, 0, feat, rc=rc) for v in "bcdef"}


@pytest.fixture
def mini(tmp_path):
    feat, lab = _make_features_labels(tmp_path)
    views = vgd.build_views(feat, DZ)          # verifier's INDEPENDENT view builder
    tensors = _build_tensors(feat, lab)        # the REAL graph builder
    return {"feat": feat, "lab": lab, "views": views, "tensors": tensors,
            "tmp": str(tmp_path)}


# =========================================================================== #
# GROUP A — topology of all five views                                        #
# =========================================================================== #
def test_topology_clean_passes_all_views(mini):
    assert _run(vgd.topology_checks, mini["views"], mini["tensors"]) == []


def test_topology_flags_broken_node_order(mini):
    t = dict(mini["tensors"])
    d = t["d"]
    nm = list(d.node_name)
    nm[0], nm[1] = nm[1], nm[0]          # swap two node names -> misaligned labels
    d.node_name = nm
    fails = _run(vgd.topology_checks, mini["views"], t)
    assert "top.d node_name block-positional order" in fails


def test_topology_flags_broken_interleaving(mini):
    t = dict(mini["tensors"])
    c = t["c"]
    if c.edge_attr.shape[0] >= 4:
        ea = c.edge_attr.clone()
        ea[[0, 2]] = ea[[2, 0]]          # break [fwd0,rev0,...] pairing
        c.edge_attr = ea
        fails = _run(vgd.topology_checks, mini["views"], t)
        assert any("interleaved" in f and "top.c" in f for f in fails)


def test_topology_flags_self_loop(mini):
    t = dict(mini["tensors"])
    f = t["f"]
    if f.edge_index.shape[1] >= 1:
        ei = f.edge_index.clone()
        ei[1, 0] = ei[0, 0]              # inject a self-loop
        f.edge_index = ei
        fails = _run(vgd.topology_checks, mini["views"], t)
        assert "top.f no self-loops" in fails


def test_topology_flags_corrupt_d_net_edge_attr(mini):
    t = dict(mini["tensors"])
    d = t["d"]
    k = next((i for i in range(len(d.edge_type)) if int(d.edge_type[i]) == 1), None)
    if k is not None:
        ea = d.edge_attr.clone()
        ea[k] += 9.0
        d.edge_attr = ea
        fails = _run(vgd.topology_checks, mini["views"], t)
        assert any("edge_attr" in f and "top.d" in f for f in fails)


def test_topology_flags_stale_schema_without_crashing(mini):
    """A pre-RC dataset (edge_y width 5, no rc tensors) must FAIL loudly, not
    IndexError — the 2026-07-08 DMA stale-dataset incident."""
    t = dict(mini["tensors"])
    c = t["c"]
    c.edge_y = c.edge_y[:, :5]           # simulate the old width-5 schema
    fails = _run(vgd.topology_checks, mini["views"], t)   # must not raise
    assert any("edge_y width==6" in f and "top.c" in f for f in fails)


# =========================================================================== #
# GROUP B — feature statistics: pure helpers + tensor categorical checks       #
# =========================================================================== #
def test_percentile_matches_the_gate():
    """vgd._pctile must reproduce compute_label_stats._percentile exactly, else
    the stats-honesty check false-fails."""
    import compute_label_stats as cls
    vals = [0.0, 1.0, 2.0, 3.0, 4.0, 10.0, 11.0]
    for q in (0.5, 0.9, 0.95, 0.99):
        assert vgd._pctile(vals, q) == pytest.approx(cls._percentile(vals, q))


def test_numsummary_matches_gate_and_drops_nan():
    import compute_label_stats as cls
    vals = [3.0, 1.0, 2.0, float("nan"), 5.0]
    got = vgd._numsummary(vals)
    exp = cls.numeric_summary([v for v in vals if v == v])
    assert got["min"] == exp["min"] and got["max"] == exp["max"]
    assert got["mean"] == pytest.approx(exp["mean"])
    assert got["p90"] == pytest.approx(exp["p90"])


def test_summ_close_detects_drift():
    a = vgd._numsummary([1.0, 2.0, 3.0, 4.0])
    assert vgd._summ_close(a, dict(a))
    b = dict(a); b["mean"] = b["mean"] * 1.05
    assert not vgd._summ_close(a, b)


def test_reverse_and_paired_helpers():
    ei = torch.tensor([[0, 1, 2, 3], [1, 0, 3, 2]])   # [fwd0,rev0,fwd1,rev1]
    assert vgd._reverse_pairs_bad(ei) == 0
    bad = torch.tensor([[0, 9, 2, 3], [1, 0, 3, 2]])
    assert vgd._reverse_pairs_bad(bad) == 1
    attr = torch.tensor([[1.0], [1.0], [2.0], [2.0]])
    assert vgd._paired_rows_bad(attr) == 0
    assert vgd._paired_rows_bad(torch.tensor([[1.0], [7.0]])) == 1


def test_feature_categorical_clean_and_corrupt(mini, monkeypatch):
    # stub the platform resolver (shells out; irrelevant to the categorical/stats
    # checks and would otherwise write a CWD working dir on this synthetic case).
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda case: {})
    # clean: net_type all signal, ids in-range
    assert _run(vgd.feature_stat_checks, mini["tmp"], DZ, mini["feat"], mini["lab"],
                mini["tensors"]) == []
    # inject a clock net_type onto a net node -> signal-only filter violated
    t = dict(mini["tensors"])
    b = t["b"]
    nt = b.x[:, 0].long()
    idx = (nt == 1).nonzero()[0].item()
    b.x[idx, 2] = 3.0
    fails = _run(vgd.feature_stat_checks, mini["tmp"], DZ, mini["feat"], mini["lab"], t)
    assert "feat.net nodes all signal (net_type_id==0)" in fails


def test_feature_stats_json_honesty(mini, monkeypatch):
    """features_stats.json / labels_stats.json must reflect the CURRENT CSVs;
    a tampered summary is caught."""
    import compute_feature_stats as cfs
    import compute_label_stats as cls
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda case: {})
    reports = os.path.join(mini["tmp"], "reports")
    os.makedirs(reports, exist_ok=True)
    # generate matching stats JSONs via the REAL gates
    fj = cfs.build_report(mini["feat"], DZ, "sky130hd")
    lj = cls.build_report(mini["lab"], DZ, "sky130hd")
    json.dump(fj, open(os.path.join(reports, "features_stats.json"), "w"))
    json.dump(lj, open(os.path.join(reports, "labels_stats.json"), "w"))
    fails = _run(vgd.feature_stat_checks, mini["tmp"], DZ, mini["feat"], mini["lab"],
                 mini["tensors"])
    assert "feat.features_stats.json matches recomputed CSV distributions" not in fails
    assert "feat.labels_stats.json matches recomputed CSV distributions" not in fails
    # tamper a summary value -> must be caught
    fj["features"]["nodes_gate"]["cell_area"]["mean"] += 1.0
    json.dump(fj, open(os.path.join(reports, "features_stats.json"), "w"))
    fails = _run(vgd.feature_stat_checks, mini["tmp"], DZ, mini["feat"], mini["lab"],
                 mini["tensors"])
    assert "feat.features_stats.json matches recomputed CSV distributions" in fails


# =========================================================================== #
# GROUP C — labels <-> sign-off reports                                        #
# =========================================================================== #
def _write_signoff_stubs(case, period=2.0, drc="clean", lvs="clean",
                         io_count=1, macro_count=0, seq_count=0):
    os.makedirs(os.path.join(case, "reports"), exist_ok=True)
    run = os.path.join(case, "backend", "RUN_x", "results")
    os.makedirs(run, exist_ok=True)
    json.dump({"status": drc}, open(os.path.join(case, "reports", "drc.json"), "w"))
    json.dump({"status": lvs}, open(os.path.join(case, "reports", "lvs.json"), "w"))
    json.dump({"geometry": {"io_count": io_count, "macro_count": macro_count,
                            "sequential_count": seq_count}},
              open(os.path.join(case, "reports", "ppa.json"), "w"))
    with open(os.path.join(run, "6_final.sdc"), "w") as f:
        f.write(f"create_clock -name clk -period {period} [get_ports clk]\n")
    # minimal SPEF with an R_UNIT and a *RES section big enough to bound equiv_res
    with open(os.path.join(run, "6_final.spef"), "w") as f:
        f.write("*SPEF \"IEEE 1481-1999\"\n*R_UNIT 1 OHM\n")
        f.write("*D_NET n0 30\n*RES\n1 a b 500.0\n2 b c 400.0\n*END\n")
    # a tiny DEF so _find_final_def + geometry re-derivation work (no macros/seq)
    with open(os.path.join(run, "6_final.def"), "w") as f:
        f.write("UNITS DISTANCE MICRONS 2000 ;\nDIEAREA ( 0 0 20000 20000 ) ;\n")
        f.write("COMPONENTS 2 ;\n - g0 INV_X1 + PLACED ( 0 0 ) N ;\n"
                " - g1 INV_X1 + PLACED ( 5000 0 ) N ;\nEND COMPONENTS\n")


def test_signoff_clean_passes(mini, monkeypatch):
    _write_signoff_stubs(mini["tmp"], period=2.0, io_count=1)
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda case: {})
    fails = _run(vgd.signoff_report_checks, mini["tmp"], DZ, mini["feat"], mini["lab"],
                 mini["tensors"])
    # geometry macro/sequential need liberty/LEF (stubbed empty) so those may be
    # absent; the always-computable ones must PASS.
    assert "signoff.drc clean (dataset built on a signed-off design)" not in fails
    assert "signoff.lvs clean (dataset built on a signed-off design)" not in fails
    assert "signoff.geometry io_count == nodes_iopin rows" not in fails
    assert ("signoff.timing Path_Delay==clk_period-slack (6_final.sdc) & label==log1p"
            not in fails)
    assert "signoff.metadata C_total within [Σground+Σcoupling, Σground+2Σcoupling]" not in fails
    assert "signoff.equiv_res label bounded by SPEF *RES (0<r<=ΣR, scale sane)" not in fails


def test_signoff_drc_dirty_gate(mini, monkeypatch):
    _write_signoff_stubs(mini["tmp"], drc="violations")
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda case: {})
    fails = _run(vgd.signoff_report_checks, mini["tmp"], DZ, mini["feat"], mini["lab"],
                 mini["tensors"])
    assert "signoff.drc clean (dataset built on a signed-off design)" in fails


def test_signoff_geometry_io_mismatch(mini, monkeypatch):
    _write_signoff_stubs(mini["tmp"], io_count=99)
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda case: {})
    fails = _run(vgd.signoff_report_checks, mini["tmp"], DZ, mini["feat"], mini["lab"],
                 mini["tensors"])
    assert "signoff.geometry io_count == nodes_iopin rows" in fails


def test_signoff_timing_transform_violation(mini, monkeypatch, tmp_path):
    """If the SDC period disagrees with Path_Delay==period-slack, flag it."""
    _write_signoff_stubs(mini["tmp"], period=5.0)   # CSV was built with period 2.0
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda case: {})
    fails = _run(vgd.signoff_report_checks, mini["tmp"], DZ, mini["feat"], mini["lab"],
                 mini["tensors"])
    assert ("signoff.timing Path_Delay==clk_period-slack (6_final.sdc) & label==log1p"
            in fails)


def test_signoff_ctotal_bound_violation(mini, monkeypatch):
    _write_signoff_stubs(mini["tmp"])
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda case: {})
    # rewrite metadata C_total to a value outside [Σg+Σc, Σg+2Σc] = [35,40]
    p = os.path.join(mini["feat"], "metadata.csv")
    rows = list(csv.DictReader(open(p)))
    rows[0]["C_total"] = "100.0"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    fails = _run(vgd.signoff_report_checks, mini["tmp"], DZ, mini["feat"], mini["lab"],
                 mini["tensors"])
    assert "signoff.metadata C_total within [Σground+Σcoupling, Σground+2Σcoupling]" in fails


def test_signoff_equiv_res_unit_bug(mini, monkeypatch):
    """equiv_res inflated 1000x (an ohm/kohm unit bug) must be caught."""
    _write_signoff_stubs(mini["tmp"])
    monkeypatch.setattr(vgd, "resolve_platform_files", lambda case: {})
    p = os.path.join(mini["lab"], "equiv_res.csv")
    rows = list(csv.DictReader(open(p)))
    for r in rows:
        r["equiv_res_ohm"] = str(float(r["equiv_res_ohm"]) * 1000.0)
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    fails = _run(vgd.signoff_report_checks, mini["tmp"], DZ, mini["feat"], mini["lab"],
                 mini["tensors"])
    assert "signoff.equiv_res label bounded by SPEF *RES (0<r<=ΣR, scale sane)" in fails


def test_sdc_clock_period_and_spef_resistances(tmp_path):
    case = str(tmp_path)
    _write_signoff_stubs(case, period=3.14)
    assert vgd._sdc_clock_period(case) == pytest.approx(3.14)
    rv = vgd._spef_resistances(case)
    assert rv and sum(rv) == pytest.approx(900.0)   # 500 + 400, R_UNIT 1 OHM
