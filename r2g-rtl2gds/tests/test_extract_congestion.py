"""Tests for the congestion worker's tech-LEF routing-layer parsing.

The worker single-sources its tech-LEF parse + fallback from ``techlib.lef`` (Task 8
re-point; Task 9 removed the local ``parse_tech_lef`` / ``DEFAULT_LAYER_INFO`` compat
shims). These tests exercise the canonical parser the worker uses
(``techlib.lef.routing_layer_info`` + ``techlib.lef.DEFAULT_LAYER_INFO``), so they pin
exactly the behavior the worker's ``main()`` depends on.
"""
from __future__ import annotations

import textwrap

from techlib import lef


def parse_tech_lef(path):
    """The worker's tech-LEF parse: routing_layer_info with the nangate45 fallback."""
    return lef.routing_layer_info(path, fallback=lef.DEFAULT_LAYER_INFO)


DEFAULT_LAYER_INFO = lef.DEFAULT_LAYER_INFO


def _write(tmp_path, text):
    p = tmp_path / "tech.lef"
    p.write_text(textwrap.dedent(text))
    return str(p)


def test_parses_nangate_metal_layers(tmp_path):
    lef_path = _write(tmp_path, """
        LAYER metal1
            TYPE ROUTING ;
            DIRECTION HORIZONTAL ;
            PITCH 0.14 ;
        END metal1
        LAYER via1
            TYPE CUT ;
        END via1
        LAYER metal2
            TYPE ROUTING ;
            DIRECTION VERTICAL ;
            PITCH 0.19 ;
        END metal2
    """)
    info = parse_tech_lef(lef_path)
    assert set(info) == {"metal1", "metal2"}
    assert info["metal1"]["direction"] == "HORIZONTAL"
    assert abs(info["metal1"]["pitch"] - 0.14) < 1e-9
    assert info["metal2"]["direction"] == "VERTICAL"


def test_parses_non_metal_named_routing_layers(tmp_path):
    # sky130-style names (met1/li1) must be recognized via TYPE ROUTING, not name prefix.
    lef_path = _write(tmp_path, """
        LAYER li1
            TYPE ROUTING ;
            DIRECTION VERTICAL ;
            PITCH 0.34 ;
        END li1
        LAYER mcon
            TYPE CUT ;
        END mcon
        LAYER met1
            TYPE ROUTING ;
            DIRECTION HORIZONTAL ;
            PITCH 0.34 ;
        END met1
    """)
    info = parse_tech_lef(lef_path)
    assert set(info) == {"li1", "met1"}
    assert info["met1"]["direction"] == "HORIZONTAL"


def test_two_value_pitch_picks_perpendicular_axis(tmp_path):
    # "PITCH x y": HORIZONTAL layer uses y (index 1), VERTICAL uses x (index 0).
    lef_path = _write(tmp_path, """
        LAYER M1
            TYPE ROUTING ;
            DIRECTION HORIZONTAL ;
            PITCH 0.18 0.20 ;
        END M1
        LAYER M2
            TYPE ROUTING ;
            DIRECTION VERTICAL ;
            PITCH 0.18 0.20 ;
        END M2
    """)
    info = parse_tech_lef(lef_path)
    assert abs(info["M1"]["pitch"] - 0.20) < 1e-9
    assert abs(info["M2"]["pitch"] - 0.18) < 1e-9


def test_missing_file_returns_default(tmp_path):
    info = parse_tech_lef(str(tmp_path / "nope.lef"))
    assert info == DEFAULT_LAYER_INFO


def test_no_routing_layers_falls_back_to_default(tmp_path):
    lef_path = _write(tmp_path, """
        LAYER poly
            TYPE MASTERSLICE ;
        END poly
    """)
    info = parse_tech_lef(lef_path)
    assert info == DEFAULT_LAYER_INFO
