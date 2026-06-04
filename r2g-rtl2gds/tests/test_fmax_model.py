"""Unit tests for the pure Fmax model + helpers."""
from __future__ import annotations
import math
import pytest
import fmax_model as fm


def test_default_deterioration_terms_scale_with_period():
    # d_pl_fin default = max(0.10 ns, 1% of period); at T=20 -> 0.20 ns dominates.
    assert fm.d_pl_fin(20.0) == pytest.approx(0.20)
    # at T=5 -> max(0.10, 0.05) = 0.10 ns floor dominates.
    assert fm.d_pl_fin(5.0) == pytest.approx(0.10)


def test_d_fp_fin_is_sum_of_primitives():
    # d_fp_fin = d_fp_pl + d_pl_fin; defaults at T=10: d_fp_pl=max(.45,.45)=.45, d_pl_fin=max(.10,.10)=.10
    assert fm.d_fp_fin(10.0) == pytest.approx(0.55)


def test_learned_model_overrides_default_and_clamps_negative():
    model = {"d_pl_fin": (-0.05, -0.01), "d_fp_pl": (0.30, 0.03)}
    # negative learned d_pl_fin clamps to 0 (never predict negative erosion).
    assert fm.d_pl_fin(10.0, model) == 0.0
    # d_fp_pl learned positive: max(0.30, 0.03*10)=0.30
    assert fm.d_fp_fin(10.0, model) == pytest.approx(0.30)


def test_classify_probe():
    # closes: place_ws >= d_pl_fin(10)=0.10 and tns>=0
    assert fm.classify_probe(0.5, 0.0, 10.0) == "pass"
    assert fm.classify_probe(0.05, 0.0, 10.0) == "fail"     # below guardband
    assert fm.classify_probe(0.5, -1.0, 10.0) == "fail"      # tns violated
    assert fm.classify_probe(None, 0.0, 10.0) == "inconclusive"
    assert fm.classify_probe(1e39, 0.0, 10.0) == "inconclusive"  # unconstrained
    assert fm.classify_probe(0.5, 0.0, 10.0, completed=False) == "inconclusive"


def test_variant_name_encodes_period():
    assert fm.variant_name("alu", 4.5) == "alu_fmax_p0045"
    assert fm.variant_name("alu", 12.0) == "alu_fmax_p0120"


def test_rewrite_clk_period():
    sdc = "current_design alu\nset clk_period 10.0\ncreate_clock -period $clk_period [get_ports clk]\n"
    out = fm.rewrite_clk_period(sdc, 4.5)
    assert "set clk_period 4.5" in out
    assert "10.0" not in out.split("create_clock")[0]
    with pytest.raises(ValueError):
        fm.rewrite_clk_period("no period here\n", 4.5)


def test_select_model_tiers():
    entry = {"slack_deterioration": {"d_fp_pl": {"ns_p90": 0.3, "pct_p90": 0.03},
                                     "d_pl_fin": {"ns_p90": 0.2, "pct_p90": 0.02}, "n": 10}}
    model, prov = fm.select_model(entry)
    assert model["d_fp_pl"] == (0.3, 0.03) and prov.startswith("learned")
    # below N_MIN_FAMILY -> default static
    entry["slack_deterioration"]["n"] = 3
    model, prov = fm.select_model(entry)
    assert model is None and "default-static" in prov
    assert fm.select_model(None) == (None, "default-static")


def _oracle(true_fmax_period):
    """A mock design whose slack is linear: ws(period) = period - true_fmax_period.
    Floorplan reports slightly MORE slack than place (placement erodes it)."""
    def floorplan_probe(period):
        return (period - true_fmax_period) + 0.30   # floorplan optimistic by 0.30 ns
    def place_probe(period):
        ws = period - true_fmax_period
        return {"place_ws": ws, "place_tns": 0.0,
                "status": fm.classify_probe(ws, 0.0, period)}
    return floorplan_probe, place_probe


def test_search_loop_converges_to_true_fmax():
    fp, pl = _oracle(true_fmax_period=5.0)
    res = fm.search_loop(seed_period=10.0, floorplan_probe=fp, place_probe=pl, model=None)
    assert res["status"] == "ok"
    # converges to place_ws ~ d_pl_fin(T) ~ 0.10 -> T* ~ 5.10
    assert res["t_star"] == pytest.approx(5.1, abs=0.15)
    assert res["fmax_predicted_signoff"] == pytest.approx(1.0 / res["t_star"])


def test_search_loop_restarts_on_bad_seed():
    fp, pl = _oracle(true_fmax_period=5.0)
    calls = []
    def fp_logged(p):
        calls.append(p)
        return fp(p)
    # Seed wildly loose (50 ns) -> Fmax_fp ~ 5.3, off by >50% -> must restart near 5.3.
    res = fm.search_loop(seed_period=50.0, floorplan_probe=fp_logged, place_probe=pl, model=None)
    assert res["status"] == "ok"
    assert len(calls) >= 2  # restarted floorplan at the corrected seed
    assert res["t_star"] == pytest.approx(5.1, abs=0.2)


def test_search_loop_inconclusive_propagates():
    def fp(period):
        return (period - 5.0) + 0.3
    def pl(period):
        return {"place_ws": None, "place_tns": None, "status": "inconclusive"}
    res = fm.search_loop(seed_period=10.0, floorplan_probe=fp, place_probe=pl, model=None)
    assert res["status"] == "inconclusive"
