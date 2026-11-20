"""Regression tests for the four-stage lineage / validator deltas D15 and D16 (2026-11-07).

D15: a Liberty-proven input-pin swap (resizer/swap_pins: NOR2 A1<->A2) must not make the two
     swapped canonical nets "ambiguous" -- before the fix both nets lost their route labels.
D16: a purely combinational design (all timing endpoints are output ports) has no valid
     internal-pin timing label; the validator must accept it, but still reject a design with
     internal endpoints and no pin labels, or no labels at all.
Found on pilot500 nangate45 dev designs (C03005 counter: 13 nets masked by swaps; C00702
wallace: 471 of 3805 nets; C02241/C00702 four-stage builds aborted by the validator).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

R2G2_DIR = Path(__file__).resolve().parents[1] / "scripts" / "r2g2"


def _load(filename, name):
    spec = importlib.util.spec_from_file_location(name, R2G2_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def stage02():
    pytest.importorskip("torch")
    return _load("02_extract_features.py", "t_d15_features")


def _cell(function, inputs=("A1", "A2"), out="ZN"):
    pins = {p: {"direction": "INPUT", "function": ""} for p in inputs}
    pins[out] = {"direction": "OUTPUT", "function": function}
    return {"pins": pins}


CELLS = {"NOR2_X1": _cell("!(A1 | A2)"), "ANDN_X1": _cell("(A1 & !A2)"), "INV_X1": _cell("!A", ("A",))}


def _case(gate_master="NOR2_X1"):
    # base (synthesis): d1 drives n1, d2 drives n2; g.A1 <- n1, g.A2 <- n2
    base = SimpleNamespace(
        net_names=["n1", "n2", "y"], gate_names=["d1", "d2", "g"],
        connection_net_names=["n1", "n1", "n2", "n2", "y"],
        connection_inst_names=["d1", "g", "d2", "g", "g"],
        connection_pin_names=["ZN", "A1", "ZN", "A2", "ZN"],
        io_pin_names=[], io_pin_net_names=[])
    # stage: the two inputs of g were swapped (nets keep their names)
    snapshot = {
        "nets": {"n1": {"connections": [("d1", "ZN"), ("g", "A2")]},
                 "n2": {"connections": [("d2", "ZN"), ("g", "A1")]},
                 "y": {"connections": [("g", "ZN")]}},
        "components": {"d1": {"master": "INV_X1"}, "d2": {"master": "INV_X1"}, "g": {"master": gate_master}},
        "iopins": {},
    }
    return base, snapshot


def test_swap_without_liberty_is_ambiguous_old_behaviour(stage02):
    base, snap = _case()
    rec = stage02.build_base_to_stage_lineage(base, snap)["records"]
    assert rec["n1"]["lineage_valid"] == 0 and rec["n2"]["lineage_valid"] == 0


def test_liberty_proven_swap_keeps_both_nets_valid(stage02):
    base, snap = _case()
    lin = stage02.build_base_to_stage_lineage(base, snap, CELLS)
    rec = lin["records"]
    assert rec["n1"]["lineage_valid"] == 1 and rec["n1"]["stage_nets"] == ["n1"]
    assert rec["n2"]["lineage_valid"] == 1 and rec["n2"]["stage_nets"] == ["n2"]
    assert lin["summary"]["legal_pin_permutation_endpoints"] == 2


def test_non_symmetric_swap_is_not_accepted(stage02):
    base, snap = _case("ANDN_X1")          # A1 & !A2 is NOT invariant under A1<->A2
    rec = stage02.build_base_to_stage_lineage(base, snap, CELLS)["records"]
    assert rec["n1"]["lineage_valid"] == 0 and rec["n2"]["lineage_valid"] == 0


def test_liberty_function_parser(stage02):
    pins, f = stage02._liberty_function("((A1 & A2) | B)'")
    assert pins == ["A1", "A2", "B"]
    assert f({"A1": 1, "A2": 1, "B": 0}) is False and f({"A1": 0, "A2": 1, "B": 0}) is True
    _, g = stage02._liberty_function("A ^ B")
    assert g({"A": 1, "B": 0}) and not g({"A": 1, "B": 1})
    _, h = stage02._liberty_function("!(A1 B1)")          # juxtaposition = AND
    assert h({"A1": 1, "B1": 0}) and not h({"A1": 1, "B1": 1})


@pytest.fixture(scope="module")
def validator():
    pytest.importorskip("torch_geometric")
    return _load("checks/validate_four_stage.py", "t_d16_validator")


def _graph(pin_valid, io_valid, endpoint_flag):
    import torch
    from torch_geometric.data import HeteroData
    g = HeteroData()
    g["pin"].x = torch.tensor([[endpoint_flag], [0.0]])
    g["pin"].x_schema = ["is_timing_endpoint"]
    g["pin"].y_valid_mask = torch.tensor([[pin_valid, False], [False, False]])
    g["io_pin"].x = torch.zeros(1, 1)
    g["io_pin"].y_valid_mask = torch.tensor([[io_valid, False]])
    return g


def test_combinational_design_with_io_endpoint_labels_passes(validator):
    assert validator.timing_label_presence_error(_graph(False, True, 0.0)) == ""


def test_internal_endpoints_without_pin_labels_still_fail(validator):
    assert validator.timing_label_presence_error(_graph(False, True, 1.0))


def test_no_timing_labels_at_all_fails(validator):
    assert validator.timing_label_presence_error(_graph(False, False, 0.0))


# ---- D18: structural endpoints that STA never timed (unclocked registers) -----------------
_RPT_PORTS_ONLY = """Startpoint: S1END[2] (input port clocked by virtual_clk)
Endpoint: W6BEG[9] (output port clocked by virtual_clk)
"""
_RPT_WITH_FLOP = _RPT_PORTS_ONLY + """Startpoint: a (input port clocked by core_clk)
Endpoint: _470_ (rising edge-triggered flip-flop clocked by core_clk)
"""


def test_report_internal_endpoint_scan(validator, tmp_path):
    p, q = tmp_path / "ports.rpt", tmp_path / "flop.rpt"
    p.write_text(_RPT_PORTS_ONLY); q.write_text(_RPT_WITH_FLOP)
    assert validator.report_has_internal_endpoint([p]) is False
    assert validator.report_has_internal_endpoint([p, q]) is True
    assert validator.report_has_internal_endpoint([tmp_path / "missing.rpt"]) is None


def test_untimed_internal_endpoints_pass_when_sta_timed_none(validator):
    """pilot500 C03894 RAM_IO (FABulous tile): a virtual clock times only port-to-port paths and
    the 372 register D pins are unconstrained (slack INF). Zero valid pin labels is then the
    truth, but the D16 guard aborted the whole build (failure-patterns #71)."""
    g = _graph(False, True, 1.0)
    assert validator.timing_label_presence_error(g, report_internal_endpoints=False) == ""
    # the guard keeps its power whenever the report DID time an internal endpoint, or is unknown
    assert validator.timing_label_presence_error(g, report_internal_endpoints=True)
    assert validator.timing_label_presence_error(g, report_internal_endpoints=None)
    assert validator.timing_label_presence_error(g)
