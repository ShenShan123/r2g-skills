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


# --------------------------------------------------------------------------- #
# Demand-grid keying (2026-07-05 vertical-transposition regression, #7).       #
# --------------------------------------------------------------------------- #

import extract_congestion as ec


def test_demand_keys_are_x_y_for_both_directions():
    """All demand keys must be (x_gcell, y_gcell). A vertical wire at one x
    spanning several y gcells fills a COLUMN; keying it (y, x) — the 2026-07-05
    transposition — turned it into a row read by every diagonal-mirror cell."""
    demand_h, demand_v = {}, {}
    # vertical wire at x=100 (gcell 0), y 0..4000; grid 1000x1000 DBU, dbu=1000
    ec.add_route_segment(demand_h, demand_v, 100, 0, 100, 4000, 1000, 1000, 1000.0)
    assert demand_h == {}
    assert set(demand_v) == {(0, 0), (0, 1), (0, 2), (0, 3)}
    assert all(abs(v - 1.0) < 1e-9 for v in demand_v.values())
    # horizontal wire at y=2500 (gcell 2), x 0..3000 -> a row at y_gcell 2
    ec.add_route_segment(demand_h, demand_v, 0, 2500, 3000, 2500, 1000, 1000, 1000.0)
    assert set(demand_h) == {(0, 2), (1, 2), (2, 2)}


def test_cell_on_vertical_wire_sees_congestion_not_its_mirror():
    demand_h, demand_v = {}, {}
    ec.add_route_segment(demand_h, demand_v, 100, 0, 100, 4000, 1000, 1000, 1000.0)
    grid_util = ec.build_grid_utilization(demand_h, demand_v, cap_h=10.0, cap_v=10.0)
    # Densify onto a small grid: the vertical wire fills column x_gcell=0.
    util = ec.densify_util(grid_util, gridxcnt=4, gridycnt=4)
    on_wire = util[0][2]   # (x=0, y=2) — physically on the wire
    mirror = util[2][0]    # (x=2, y=0) — the diagonal mirror
    assert on_wire > 0.0, "cell physically on the wire must see its congestion"
    assert mirror == 0.0, "diagonal-mirror gcell must NOT see phantom congestion"


# --------------------------------------------------------------------------- #
# Ported Congestion_Parse method: pure-python Gaussian == scipy.gaussian_filter #
# (sigma=1.0, mode='reflect', truncate=4.0). Golden values captured from scipy  #
# 1.17.1 so the test stays self-contained (the pytest env carries no scipy).    #
# --------------------------------------------------------------------------- #
def test_gaussian_filter_2d_matches_scipy_golden():
    # scipy.ndimage.gaussian_filter(delta, sigma=1.0) where delta is 1 at center
    # of a 5x5 grid — the discrete Gaussian point-spread function.
    grid = [[0.0] * 5 for _ in range(5)]
    grid[2][2] = 1.0
    out = ec.gaussian_filter_2d(grid, 5, 5, sigma=1.0)
    golden = [
        [0.003413245648, 0.014144513903, 0.023307469938, 0.014144513903, 0.003413245648],
        [0.014144513903, 0.058614964803, 0.096586318869, 0.058614964803, 0.014144513903],
        [0.023307469938, 0.096586318869, 0.159155891742, 0.096586318869, 0.023307469938],
        [0.014144513903, 0.058614964803, 0.096586318869, 0.058614964803, 0.014144513903],
        [0.003413245648, 0.014144513903, 0.023307469938, 0.014144513903, 0.003413245648],
    ]
    worst = max(abs(out[i][j] - golden[i][j]) for i in range(5) for j in range(5))
    assert worst < 1e-9, f"gaussian_filter_2d diverged from scipy golden by {worst:.2e}"
    # reflect-mode Gaussian conserves total mass for a normalized kernel.
    assert abs(sum(out[i][j] for i in range(5) for j in range(5)) - 1.0) < 1e-9


def test_gaussian_filter_2d_preserves_uniform_field():
    # A constant field is a Gaussian eigenfunction (eigenvalue 1) under reflect
    # boundaries — every cell must come back unchanged.
    grid = [[3.5] * 6 for _ in range(4)]
    out = ec.gaussian_filter_2d(grid, 4, 6, sigma=1.0)
    assert max(abs(out[i][j] - 3.5) for i in range(4) for j in range(6)) < 1e-12


def test_gaussian_weights_radius_and_normalization():
    w, radius = ec._gaussian_weights(sigma=1.0)
    assert radius == 4                      # int(4.0*1.0 + 0.5)
    assert len(w) == 2 * radius + 1         # 9-tap kernel
    assert abs(sum(w) - 1.0) < 1e-15
    assert w[radius - 1] == w[radius + 1]   # symmetric


# --------------------------------------------------------------------------- #
# Cell -> bounding-box GCell mapping (needs macro SIZE from the cell LEF).      #
# --------------------------------------------------------------------------- #
from techlib import lef as _lef


def _write_cell_lef(tmp_path):
    p = tmp_path / "cells.lef"
    p.write_text(textwrap.dedent("""
        MACRO SMALL
            SIZE 0.4 BY 1.4 ;
        END SMALL
        MACRO WIDE
            SIZE 12.0 BY 1.4 ;
        END WIDE
    """))
    return str(p)


def test_macro_sizes_parses_size(tmp_path):
    sizes = _lef.macro_sizes(_write_cell_lef(tmp_path))
    assert sizes["SMALL"] == (0.4, 1.4)
    assert sizes["WIDE"] == (12.0, 1.4)


def test_macro_sizes_missing_file_is_empty(tmp_path):
    assert _lef.macro_sizes(str(tmp_path / "nope.lef")) == {}


def test_cell_bbox_orientation_swaps_wh():
    sizes_um = {"C": (4.0, 1.0)}  # w=4um, h=1um
    # unit=1000 dbu/um -> w=4000, h=1000 dbu
    north = ec.cell_bbox_dbu(0, 0, "C", "N", sizes_um, 1000)
    east = ec.cell_bbox_dbu(0, 0, "C", "E", sizes_um, 1000)
    assert north == (0, 0, 4000, 1000)          # N/S keep (w,h)
    assert east == (0, 0, 1000, 4000)           # E/W rotate 90 -> (h,w)
    # unknown master -> None (caller falls back to origin gcell)
    assert ec.cell_bbox_dbu(0, 0, "MISSING", "N", sizes_um, 1000) is None


def test_bbox_mapping_averages_all_overlapped_gcells():
    # A cell whose footprint straddles two GCells must average both, not just
    # the origin GCell (the core Congestion_Parse mapping change).
    gxc = gyc = 3
    util = [[0.0] * gyc for _ in range(gxc)]
    util[0][0] = 0.0
    util[1][0] = 1.0            # neighbor gcell carries all the congestion
    gauss = util                # bypass smoothing for a clean arithmetic check
    # cell at origin (0,0), 8um wide, 1um tall, grid step 5um -> spans gx 0..1
    sizes_um = {"C": (8.0, 1.0)}
    bbox = ec.cell_bbox_dbu(0, 0, "C", "N", sizes_um, 1000)   # (0,0,8000,1000)
    cong, label, label_raw = ec.cell_congestion_over_bbox(
        bbox, 0, 0, util, gauss, grid_step_x=5000, grid_step_y=5000,
        gridxcnt=gxc, gridycnt=gyc)
    # gx spans 0 and 1 (8000//5000=1), gy=0 only -> mean of util[0][0]=0 and util[1][0]=1
    assert abs(cong - 0.5) < 1e-12
    assert abs(label_raw - 0.5) < 1e-12         # mean(sqrt(0), sqrt(1)) = 0.5
    # origin-only fallback (bbox=None) sees just util[0][0]=0
    cong0, _l0, raw0 = ec.cell_congestion_over_bbox(
        None, 0, 0, util, gauss, 5000, 5000, gxc, gyc)
    assert cong0 == 0.0 and raw0 == 0.0
