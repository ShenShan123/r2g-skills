"""Tests for normalize_diode_spice.py: the sky130 antenna-diode X->D rewrite.

Pure text transform (no Magic/Netgen needed). Covers the fix for the 2026-06-11
"Top level cell failed pin matching" residual: Magic extracts the diode primitive
as an X subcircuit instance (black box, pins "1 2") while the PDK cell library
models it as a D device (anode/cathode, properties area/pj) — see
references/failure-patterns.md "sky130 LVS" cause 5.
"""
from __future__ import annotations

import normalize_diode_spice as n


DIODE_LINE = ("X0 VNB DIODE sky130_fd_pr__diode_pw2nd_05v5 "
              "perim=2.64e+06 area=4.347e+11")


def test_rewrites_diode_x_instance_to_d_device():
    out, count = n.normalize_lines([DIODE_LINE])
    assert count == 1
    assert out == ["D0 VNB DIODE sky130_fd_pr__diode_pw2nd_05v5 "
                   "pj=2.64e+06 area=4.347e+11"]


def test_preserves_instance_suffix_and_other_diode_models():
    out, count = n.normalize_lines(
        ["Xdio_3 a b sky130_fd_pr__diode_pd2nw_05v5_lvt area=1 perim=2"])
    assert count == 1
    assert out == ["Ddio_3 a b sky130_fd_pr__diode_pd2nw_05v5_lvt area=1 pj=2"]


def test_leaves_non_diode_lines_untouched():
    lines = [
        ".subckt sky130_fd_sc_hd__diode_2 VNB VPB VGND VPWR DIODE",
        "X1 VPWR A Y VPB sky130_fd_pr__pfet_01v8_hvt w=1 l=0.15",
        "Xsky130_fd_sc_hd__diode_2_0 VSS VDD VSS VDD n1 sky130_fd_sc_hd__diode_2",
        ".ends",
    ]
    out, count = n.normalize_lines(list(lines))
    assert count == 0
    assert out == lines


def test_joins_spice_continuation_lines_before_matching():
    out, count = n.normalize_lines(
        ["X0 VNB DIODE", "+ sky130_fd_pr__diode_pw2nd_05v5 perim=5 area=9"])
    assert count == 1
    assert out == ["D0 VNB DIODE sky130_fd_pr__diode_pw2nd_05v5 pj=5 area=9"]


def test_idempotent_on_already_normalized_netlist():
    once, c1 = n.normalize_lines([DIODE_LINE])
    twice, c2 = n.normalize_lines(list(once))
    assert c1 == 1 and c2 == 0
    assert twice == once
