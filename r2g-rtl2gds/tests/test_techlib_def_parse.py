"""Tests for techlib.def_parse — the consolidated DEF/SDC parser (Task 1).

The load-bearing part is the ``route_segments`` iterator: it is the dedup target
for the ``*``-relative coordinate-chain walk that the wirelength extractor
(``parse_def_wirelength``) and the congestion extractor (``extract_grid_demand``)
currently re-implement independently. These tests:

  1. Pin the synthetic ``*``-relative / trailing-token / single-point / leading-``*``
     edge cases directly.
  2. Prove byte-for-result CORRESPONDENCE on two REAL DEFs (aes_core nangate45 +
     cordic sky130hd): per-net Manhattan wirelength recomputed via
     ``route_segments`` must equal ``parse_def_wirelength`` exactly, and the
     per-route-line segment sequence must equal what congestion's regex+walk
     produces (copied inline here — the extractor itself is NOT modified).

The real-DEF tests skip cleanly when design_cases/ is absent (it is gitignored /
machine-local), so the suite still runs on a bare checkout.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from techlib import def_parse

# Untouched wirelength extractor (imported via the LABELS_DIR sys.path entry in
# conftest) — the correspondence oracle for route_segments.
import extract_wirelength as ewl


REPO_ROOT = Path(__file__).resolve().parents[2]
AES_DEF = REPO_ROOT / "design_cases" / "aes_core" / "backend" / "RUN_2026-04-12_18-04-55" / "results" / "6_final.def"
CORDIC_DEF = REPO_ROOT / "design_cases" / "cordic" / "backend" / "RUN_2026-05-17_05-58-40" / "results" / "6_final.def"


# --------------------------------------------------------------------------- #
# Synthetic *-relative edge cases.                                            #
# --------------------------------------------------------------------------- #
def test_simple_two_point_chain():
    segs = list(def_parse.route_segments("+ ROUTED met1 ( 100 200 ) ( 300 200 )"))
    assert segs == [(100, 200, 300, 200)]


def test_star_carries_previous_coordinate():
    # ( * 400 ) keeps x=100; ( 500 * ) keeps y=400.
    segs = list(def_parse.route_segments("+ ROUTED met1 ( 100 200 ) ( * 400 ) ( 500 * )"))
    assert segs == [
        (100, 200, 100, 400),
        (100, 400, 500, 400),
    ]


def test_multi_segment_chain():
    line = "NEW met2 ( 0 0 ) ( 0 1000 ) ( 2000 1000 ) ( 2000 3000 )"
    segs = list(def_parse.route_segments(line))
    assert segs == [
        (0, 0, 0, 1000),
        (0, 1000, 2000, 1000),
        (2000, 1000, 2000, 3000),
    ]


def test_trailing_via_or_layer_token_ignored():
    # The 3rd token inside ( ... ) (a via name) must be ignored; only x,y used.
    line = "+ ROUTED met1 ( 100 200 ) ( 300 200 via12_0 ) ( * 600 M2_M1_via )"
    segs = list(def_parse.route_segments(line))
    assert segs == [
        (100, 200, 300, 200),
        (300, 200, 300, 600),
    ]


def test_single_point_yields_nothing():
    assert list(def_parse.route_segments("+ ROUTED met1 ( 100 200 )")) == []


def test_no_points_yields_nothing():
    assert list(def_parse.route_segments("+ ROUTED met1")) == []


def test_leading_star_chain_skipped():
    # First point is ( * 400 ): wirelength's int('*') raises -> whole line skipped.
    assert list(def_parse.route_segments("+ ROUTED met1 ( * 400 ) ( 500 600 )")) == []
    assert list(def_parse.route_segments("+ ROUTED met1 ( 100 * ) ( 500 600 )")) == []


def test_iter_route_segments_flattens():
    lines = [
        "+ ROUTED met1 ( 0 0 ) ( 0 100 )",
        "NEW met1 ( 50 50 ) ( 50 200 )",
    ]
    assert list(def_parse.iter_route_segments(lines)) == [
        (0, 0, 0, 100),
        (50, 50, 50, 200),
    ]


# --------------------------------------------------------------------------- #
# Correspondence helpers — recompute the two consumers from route_segments    #
# and (for congestion) from an inline copy of its regex+walk.                 #
# --------------------------------------------------------------------------- #
def _wirelength_via_route_segments(def_file):
    """Per-net Manhattan DBU length using ONLY techlib.route_segments.

    Mirrors parse_def_wirelength's NETS-section scan + dbu division so the result
    is directly comparable, but the per-line coordinate walk comes from the
    consolidated iterator.
    """
    db_units = 2000.0
    with open(def_file, "r") as f:
        lines = f.readlines()

    for line in lines:
        if line.startswith("UNITS DISTANCE MICRONS"):
            parts = line.split()
            if len(parts) >= 4:
                try:
                    db_units = float(parts[3])
                except ValueError:
                    pass
        elif line.startswith("COMPONENTS"):
            break

    net_start = re.compile(r"^\s*-\s+(\S+)")
    wl = {}
    current = None
    in_nets = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("NETS") and not line.startswith("END NETS") and not line.startswith("SPECIALNETS"):
            in_nets = True
            continue
        if line.startswith("END NETS"):
            in_nets = False
            continue
        if not in_nets:
            continue
        if line.startswith(";"):
            continue
        m = net_start.match(line)
        if m:
            current = m.group(1)
            wl[current] = 0.0
        if current and ("ROUTED" in line or "NEW" in line):
            for x1, y1, x2, y2 in def_parse.route_segments(line):
                wl[current] += abs(x2 - x1) + abs(y2 - y1)
    for net in wl:
        wl[net] = wl[net] / db_units
    return wl


# Inline copy of congestion's point regex + *-chain walk (extract_grid_demand),
# emitting the (x1,y1,x2,y2) sequence per route line. Copied verbatim from
# scripts/extract/labels/extract_congestion.py so we compare against the real
# behavior without importing/modifying that module.
_CONG_POINT_RE = re.compile(r"\(\s*([^\s\)]+)\s+([^\s\)]+)(?:\s+[^\)]*)?\s*\)")


def _congestion_segments_for_line(line):
    points = _CONG_POINT_RE.findall(line)
    if len(points) < 2:
        return []
    out = []
    curr_x = None
    curr_y = None
    for x_str, y_str in points:
        if curr_x is None or curr_y is None:
            if x_str == "*" or y_str == "*":
                continue
            try:
                curr_x = int(x_str)
                curr_y = int(y_str)
            except ValueError:
                curr_x = None
                curr_y = None
            continue
        next_x = curr_x
        next_y = curr_y
        if x_str != "*":
            try:
                next_x = int(x_str)
            except ValueError:
                continue
        if y_str != "*":
            try:
                next_y = int(y_str)
            except ValueError:
                continue
        out.append((curr_x, curr_y, next_x, next_y))
        curr_x = next_x
        curr_y = next_y
    return out


def _iter_def_route_lines(def_file):
    """Yield every NETS-section route line the congestion walker would process.

    Matches extract_grid_demand's line gate exactly:
    inside NETS, line has 'ROUTED'/'NEW' or starts with '+'.
    """
    with open(def_file, "r") as f:
        in_nets = False
        for raw in f:
            line = raw.strip()
            if line.startswith("NETS") and not line.startswith("END NETS") and not line.startswith("SPECIALNETS"):
                in_nets = True
                continue
            if line.startswith("END NETS"):
                in_nets = False
                continue
            if not in_nets:
                continue
            if "ROUTED" not in line and "NEW" not in line and not line.startswith("+"):
                continue
            yield line


# --------------------------------------------------------------------------- #
# Correspondence on REAL DEFs.                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "def_path",
    [
        pytest.param(AES_DEF, id="aes_core-nangate45"),
        pytest.param(CORDIC_DEF, id="cordic-sky130hd"),
    ],
)
def test_route_segments_matches_wirelength_per_net(def_path):
    """route_segments reproduces parse_def_wirelength's per-net length, exactly."""
    if not def_path.is_file():
        pytest.skip(f"DEF absent (machine-local): {def_path}")

    expected_wl, _net_types, _name = ewl.parse_def_wirelength(str(def_path))
    actual_wl = _wirelength_via_route_segments(str(def_path))

    assert set(actual_wl) == set(expected_wl), (
        f"net set differs ({def_path.name}): "
        f"missing={sorted(set(expected_wl) - set(actual_wl))[:5]} "
        f"extra={sorted(set(actual_wl) - set(expected_wl))[:5]}"
    )
    mismatches = [
        (net, expected_wl[net], actual_wl[net])
        for net in expected_wl
        if actual_wl[net] != expected_wl[net]
    ]
    assert not mismatches, (
        f"{def_path.name}: {len(mismatches)} net(s) differ from "
        f"parse_def_wirelength, e.g. {mismatches[:5]}"
    )
    # Guard against a vacuous pass on an empty/unrouted DEF.
    assert sum(1 for v in expected_wl.values() if v > 0) > 0, "no routed nets in DEF"


@pytest.mark.parametrize(
    "def_path",
    [
        pytest.param(AES_DEF, id="aes_core-nangate45"),
        pytest.param(CORDIC_DEF, id="cordic-sky130hd"),
    ],
)
def test_route_segments_matches_congestion_segment_sequence(def_path):
    """Per-route-line segment sequence equals congestion's regex+walk output."""
    if not def_path.is_file():
        pytest.skip(f"DEF absent (machine-local): {def_path}")

    lines_seen = 0
    seg_lines = 0
    for line in _iter_def_route_lines(def_path):
        lines_seen += 1
        expected = _congestion_segments_for_line(line)
        actual = list(def_parse.route_segments(line))
        assert actual == expected, (
            f"{def_path.name}: route-segment sequence differs on line:\n"
            f"  line     = {line!r}\n"
            f"  expected = {expected}\n"
            f"  actual   = {actual}"
        )
        if actual:
            seg_lines += 1

    assert lines_seen > 0, "no NETS route lines scanned (DEF gate / path wrong?)"
    assert seg_lines > 0, "no route line produced any segment (vacuous pass?)"
