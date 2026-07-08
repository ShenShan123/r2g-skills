"""Tests for extract_wirelength.py — DEF Manhattan wirelength + signal mask."""
from __future__ import annotations

import textwrap

import extract_wirelength as ewl


DEF = """
    DESIGN tiny ;
    UNITS DISTANCE MICRONS 1000 ;
    COMPONENTS 0 ;
    END COMPONENTS
    NETS 2 ;
    - sig_a ( i1 A ) ( i2 Z )
      + ROUTED metal1 ( 0 0 ) ( 1000 0 ) ( 1000 2000 ) ;
    - clk_b ( i3 A )
      + USE CLOCK
      + ROUTED metal1 ( 0 0 ) ( 3000 * ) ;
    END NETS
    END DESIGN
"""


def _run(tmp_path):
    defp = tmp_path / "t.def"
    defp.write_text(textwrap.dedent(DEF))
    wl_map, net_types, name = ewl.parse_def_wirelength(str(defp))
    return wl_map, net_types, name


def test_manhattan_length_with_relative_points(tmp_path):
    wl_map, net_types, name = _run(tmp_path)
    assert name == "tiny"
    # sig_a: (0,0)->(1000,0)=1.0um, (1000,0)->(1000,2000)=2.0um => 3.0um
    assert abs(wl_map["sig_a"] - 3.0) < 1e-6
    # clk_b: (0,0)->(3000,*) keeps y=0 => 3.0um
    assert abs(wl_map["clk_b"] - 3.0) < 1e-6


def test_net_types_and_mask(tmp_path):
    wl_map, net_types, name = _run(tmp_path)
    assert net_types["sig_a"] == "SIGNAL"
    assert net_types["clk_b"] == "CLOCK"
