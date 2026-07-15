"""Unit tests for techlib.lef pin-center geometry (ported from RTL2Graph
lib_db._parse_lef_macros/_apply_orient, feature_test_v4).

Covers: MACRO SIZE + PIN RECT/POLYGON center extraction, MASK-prefixed RECT,
the 8-orientation transform, and the instance-origin fallback when geometry is
absent/unknown.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "extract"))

from techlib import lef  # noqa: E402


LEF_TEXT = """\
MACRO INV_X1
  SIZE 1.0 BY 2.0 ;
  PIN A
    DIRECTION INPUT ;
    PORT
      LAYER metal1 ;
        RECT 0.1 0.2 0.3 0.4 ;
    END
  END A
  PIN Y
    DIRECTION OUTPUT ;
    PORT
      LAYER metal1 ;
        RECT MASK 1 0.7 0.8 0.9 1.0 ;
    END
  END Y
END INV_X1

MACRO POLY_CELL
  SIZE 4.0 BY 4.0 ;
  PIN P
    PORT
      LAYER metal2 ;
        POLYGON 1.0 1.0 3.0 1.0 3.0 3.0 1.0 3.0 ;
    END
  END P
END POLY_CELL
"""


@pytest.fixture
def geom(tmp_path):
    p = tmp_path / "cells.lef"
    p.write_text(LEF_TEXT)
    return lef.macro_pin_geometry([str(p)])


def test_size_and_pin_centers(geom):
    inv = geom["INV_X1"]
    assert (inv["width"], inv["height"]) == (1.0, 2.0)
    # bbox center of RECT 0.1 0.2 0.3 0.4
    assert inv["pins"]["A"] == pytest.approx((0.2, 0.3))
    # MASK 1 prefix must be ignored -> coords are the last 4 floats
    assert inv["pins"]["Y"] == pytest.approx((0.8, 0.9))


def test_polygon_center(geom):
    # square polygon (1,1)-(3,3) -> center (2,2)
    assert geom["POLY_CELL"]["pins"]["P"] == pytest.approx((2.0, 2.0))


def test_apply_orient_all_eight():
    # cell 1.0 x 2.0, pin at (0.2, 0.3). Expected values are the OpenDB transforms
    # (validated on cordic sky130hs placed pins: FS=MX matched 2488/2488). FN=MY
    # (reflect X), FS=MX (reflect Y) — do NOT swap these back (the RTL2Graph
    # original transposed them; see failure-patterns.md).
    px, py, w, h = 0.2, 0.3, 1.0, 2.0
    assert lef.apply_orient(px, py, "N", w, h) == pytest.approx((0.2, 0.3))   # R0
    assert lef.apply_orient(px, py, "S", w, h) == pytest.approx((0.8, 1.7))   # R180
    assert lef.apply_orient(px, py, "W", w, h) == pytest.approx((1.7, 0.2))   # R90
    assert lef.apply_orient(px, py, "E", w, h) == pytest.approx((0.3, 0.8))   # R270
    assert lef.apply_orient(px, py, "FN", w, h) == pytest.approx((0.8, 0.3))  # MY (reflect X)
    assert lef.apply_orient(px, py, "FS", w, h) == pytest.approx((0.2, 1.7))  # MX (reflect Y)
    assert lef.apply_orient(px, py, "FW", w, h) == pytest.approx((0.3, 0.2))  # MYR90
    assert lef.apply_orient(px, py, "FE", w, h) == pytest.approx((1.7, 0.8))  # MXR90


def test_pin_abs_pos_with_geometry(geom):
    # instance origin (10, 20), orient N -> origin + pin offset
    assert lef.pin_abs_pos_um(geom, 10.0, 20.0, "N", "INV_X1", "A") == pytest.approx((10.2, 20.3))
    # orient S mirrors within the cell footprint
    assert lef.pin_abs_pos_um(geom, 10.0, 20.0, "S", "INV_X1", "A") == pytest.approx((10.8, 21.7))
    # case-insensitive master/pin keys
    assert lef.pin_abs_pos_um(geom, 0.0, 0.0, "N", "inv_x1", "a") == pytest.approx((0.2, 0.3))


def test_fallback_to_instance_origin(geom):
    # no geometry at all
    assert lef.pin_abs_pos_um({}, 5.0, 6.0, "N", "INV_X1", "A") == (5.0, 6.0)
    # unknown master
    assert lef.pin_abs_pos_um(geom, 5.0, 6.0, "N", "NOPE", "A") == (5.0, 6.0)
    # unknown pin
    assert lef.pin_abs_pos_um(geom, 5.0, 6.0, "N", "INV_X1", "ZZ") == (5.0, 6.0)


def test_missing_lef_is_empty():
    assert lef.macro_pin_geometry(["/nonexistent/path.lef"]) == {}
    assert lef.macro_pin_geometry([]) == {}


def test_cell_lef_paths_from_env(monkeypatch, tmp_path):
    a = tmp_path / "sc.lef"
    b = tmp_path / "macro.lef"
    a.write_text("")
    b.write_text("")
    monkeypatch.setenv("SC_LEF", str(a))
    monkeypatch.setenv("ADDITIONAL_LEFS", str(b))
    monkeypatch.delenv("CELL_LEFS", raising=False)
    assert lef.cell_lef_paths() == [str(a), str(b)]
