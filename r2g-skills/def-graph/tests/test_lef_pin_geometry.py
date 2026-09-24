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
  PIN Z
    DIRECTION OUTPUT ;
    PORT
      LAYER metal1 ;
        RECT 0.0 0.0 0.2 0.2 ;
        RECT 0.6 1.0 1.0 2.0 ;
    END
  END Z
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
    # OpenDB getAvgXY semantics: mean of each shape center, not the union bbox center.
    assert inv["pins"]["Z"] == pytest.approx((0.45, 0.8))


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


# A real gf180mcu 9t pin (addf_1 CI): an L-shaped POLYGON that OpenDB stores as
# two boxes, [11.91 1.77 12.17 2.115] and [3.89 2.115 12.55 2.345].
GF180_CI_POLYGON = ("3.89 2.115 7.975 2.115 11.91 2.115 11.91 1.77 12.11 1.77 12.17 1.77 "
                    "12.17 2.115 12.55 2.115 12.55 2.345 12.11 2.345 7.975 2.345 3.89 2.345")


def test_polygon_decomposes_like_opendb():
    nums = [float(v) for v in GF180_CI_POLYGON.split()]
    boxes = sorted(lef.polygon_rects(nums[0::2], nums[1::2]))
    assert boxes == [(3.89, 2.115, 12.55, 2.345), (11.91, 1.77, 12.17, 2.115)]


def test_polygon_pin_center_is_the_mean_of_its_opendb_boxes(tmp_path):
    """Regression (2026-09-23): b917894 averaged shape centers but kept a POLYGON as
    ONE shape (its bbox center). OpenDB's getAvgXY averages the boxes the polygon
    decomposes into, so every non-rectangular gf180 pin was off: 1,628 of 3,344
    pin positions, checked against getAvgXY on all gf180 9t masters."""
    p = tmp_path / "gf.lef"
    p.write_text("MACRO ADDF\n  SIZE 20 BY 5 ;\n  PIN CI\n    PORT\n      LAYER Metal1 ;\n"
                 f"        POLYGON {GF180_CI_POLYGON} ;\n    END\n  END CI\nEND ADDF\n")
    got = lef.macro_pin_geometry([str(p)])["ADDF"]["pins"]["CI"]
    # mean of (12.04, 1.9425) and (8.22, 2.23); the polygon bbox center is (8.22, 2.0575)
    assert got == pytest.approx((10.13, 2.08625))


_OPENROAD = __import__("shutil").which("openroad")

_TECH_LEF = """VERSION 5.8 ;
BUSBITCHARS "[]" ;
DIVIDERCHAR "/" ;
UNITS
  DATABASE MICRONS 1000 ;
END UNITS
LAYER met1
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 0.34 ;
  WIDTH 0.14 ;
END met1
END LIBRARY
"""

_CELL_LEF = f"""VERSION 5.8 ;
MACRO PROBE
  CLASS CORE ;
  ORIGIN 0 0 ;
  SIZE 20 BY 5 ;
  PIN A
    DIRECTION INPUT ;
    PORT
      LAYER met1 ;
        RECT 0 0 1 1 ;
        RECT 0 0 3 1 ;
    END
  END A
  PIN CI
    DIRECTION INPUT ;
    PORT
      LAYER met1 ;
        POLYGON {GF180_CI_POLYGON} ;
    END
  END CI
END PROBE
END LIBRARY
"""

_ODB_PROBE = """
import odb, sys
db = odb.dbDatabase.create()
odb.read_lef(db, sys.argv[1]); odb.read_lef(db, sys.argv[2])
block = odb.dbBlock.create(odb.dbChip.create(db), "t")
dbu = block.getDbUnitsPerMicron()
for orient in ("R0", "MX", "MY", "R180"):
    inst = odb.dbInst.create(block, db.findMaster("PROBE"), orient)
    inst.setOrient(orient)
    inst.setLocation(10000, 20000)
    lx, ly = inst.getLocation()
    for it in inst.getITerms():
        ok, x, y = it.getAvgXY()
        print(orient, it.getMTerm().getName(), lx / dbu, ly / dbu, x / dbu, y / dbu)
"""


@pytest.mark.skipif(_OPENROAD is None, reason="openroad (OpenDB) not on PATH")
def test_pin_centers_equal_opendb_getavgxy(tmp_path):
    """Ground truth, not code agreement: the extractor's pin position must equal
    OpenDB's dbITerm::getAvgXY in every orientation, for a multi-RECT pin and a
    POLYGON pin alike."""
    import subprocess

    (tmp_path / "t.lef").write_text(_TECH_LEF)
    (tmp_path / "c.lef").write_text(_CELL_LEF)
    (tmp_path / "probe.py").write_text(_ODB_PROBE)
    # The oss-cad python3 wrapper exports PYTHONHOME; inherited by `openroad -python`
    # it points the embedded interpreter at the wrong stdlib (same leak as 9ba9bc4).
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONHOME", "PYTHONEXECUTABLE", "PYTHONNOUSERSITE")}
    out = subprocess.run([_OPENROAD, "-python", "-exit", str(tmp_path / "probe.py"),
                          str(tmp_path / "t.lef"), str(tmp_path / "c.lef")],
                         capture_output=True, text=True, timeout=300, cwd=tmp_path, env=env)
    rows = [ln.split() for ln in out.stdout.splitlines()
            if ln.split()[:1] and ln.split()[0] in ("R0", "MX", "MY", "R180")]
    assert len(rows) == 8, out.stdout + out.stderr
    geom = lef.macro_pin_geometry([str(tmp_path / "c.lef")])
    def_orient = {"R0": "N", "MX": "FS", "MY": "FN", "R180": "S"}
    for orient, pin, lx, ly, x, y in rows:
        got = lef.pin_abs_pos_um(geom, float(lx), float(ly), def_orient[orient], "PROBE", pin)
        assert got == pytest.approx((float(x), float(y)), abs=0.0015), (orient, pin)
