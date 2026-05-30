"""Tests for techlib.lef — the unified tech-LEF routing-layer parser (Task 2).

``techlib.lef`` consolidates the two tech-LEF parsers that previously lived apart:

  * NAMES view — ``routing_layers`` / ``routing_layer_regex`` ported from the feature
    workers' ``def_parse.py`` (``parse_routing_layers`` / ``routing_layer_regex``).
  * PITCH/DIRECTION view — ``routing_layer_info`` ported from congestion's
    ``parse_tech_lef`` (with an injectable fallback).

These tests prove EQUIVALENCE to the untouched originals on the REAL tech LEFs of all
six ORFS platforms. The originals are imported as plain top-level modules via the
sys.path entries conftest installs (FEATURES_DIR / LABELS_DIR / EXTRACT_DIR):

  * ``from def_parse import parse_routing_layers, routing_layer_regex as orig_rlr``
    resolves the FEATURES module (top-level ``def_parse``), exactly as Task 1's
    test_techlib_def_parse.py relies on.
  * ``import extract_congestion`` resolves the LABELS module.
  * ``from techlib import lef`` resolves the new consolidated package module.

A platform is SKIPPED when its tech LEF is absent, so the suite runs on a bare
checkout (ORFS platforms are machine-local).
"""
from __future__ import annotations

import glob
import os
import re

import pytest

from techlib import lef

# Untouched originals — the equivalence oracles. Imported via conftest sys.path:
#   FEATURES_DIR -> top-level `def_parse` (the FEATURE workers' module)
#   LABELS_DIR   -> top-level `extract_congestion`
from def_parse import parse_routing_layers, routing_layer_regex as orig_rlr
import extract_congestion


# --------------------------------------------------------------------------- #
# Tech-LEF path resolution — robust + skippable.                              #
# --------------------------------------------------------------------------- #
def _platforms_dir():
    """Directory holding the ORFS platforms, or None if not resolvable.

    Prefers $ORFS_ROOT/flow/platforms (the authoritative flow location); falls back
    to the known checkout under the user workarea so the suite runs without env setup.
    """
    candidates = []
    orfs_root = os.environ.get("ORFS_ROOT")
    if orfs_root:
        candidates.append(os.path.join(orfs_root, "flow", "platforms"))
    candidates.append(
        "/proj/workarea/user5/OpenROAD-flow-scripts/flow/platforms"
    )
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return None


def _tech_lef(platform):
    """Resolve the routing tech LEF for a platform, or None if absent.

    Uses the literal ORFS platform LEF names; gf180 ships many corner-specific
    tech LEFs, so glob ``*_tech.lef`` and pick the sorted-first deterministically
    (any real gf180 routing tech LEF is valid for the equivalence assertion).
    """
    pdir = _platforms_dir()
    if not pdir:
        return None
    literal = {
        "nangate45": "nangate45/lef/NangateOpenCellLibrary.tech.lef",
        "sky130hd": "sky130hd/lef/sky130_fd_sc_hd.tlef",
        "sky130hs": "sky130hs/lef/sky130_fd_sc_hs.tlef",
        "asap7": "asap7/lef/asap7_tech_1x_201209.lef",
        "ihp-sg13g2": "ihp-sg13g2/lef/sg13g2_tech.lef",
    }
    if platform in literal:
        path = os.path.join(pdir, literal[platform])
        return path if os.path.isfile(path) else None
    if platform == "gf180":
        matches = sorted(glob.glob(os.path.join(pdir, "gf180", "lef", "*_tech.lef")))
        return matches[0] if matches else None
    return None


ALL_PLATFORMS = ["nangate45", "sky130hd", "sky130hs", "asap7", "gf180", "ihp-sg13g2"]


def _lef_or_skip(platform):
    path = _tech_lef(platform)
    if not path:
        pytest.skip(f"tech LEF absent for {platform} (machine-local ORFS platforms)")
    return path


# --------------------------------------------------------------------------- #
# Per-platform equivalence to the untouched originals.                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("platform", ALL_PLATFORMS)
def test_routing_layers_equiv(platform):
    """techlib.lef.routing_layers == features def_parse.parse_routing_layers (exact)."""
    path = _lef_or_skip(platform)
    expected = parse_routing_layers(path)
    actual = lef.routing_layers(path)
    assert actual == expected, f"{platform}: layer-name list differs"
    # Guard against a vacuous pass (every real routing tech LEF has >=1 routing layer).
    assert len(actual) > 0, f"{platform}: no routing layers parsed (path/parse wrong?)"


@pytest.mark.parametrize("platform", ALL_PLATFORMS)
def test_routing_layer_info_equiv(platform):
    """techlib.lef.routing_layer_info == congestion.parse_tech_lef (exact dict)."""
    path = _lef_or_skip(platform)
    expected = extract_congestion.parse_tech_lef(path)
    actual = lef.routing_layer_info(path)
    assert actual == expected, f"{platform}: pitch/direction dict differs"
    # Real LEF parses to actual layers, not the nangate fallback (except nangate itself,
    # whose real parse may legitimately equal the fallback table).
    assert len(actual) > 0, f"{platform}: empty layer-info dict"


@pytest.mark.parametrize("platform", ALL_PLATFORMS)
def test_routing_layer_regex_equiv(platform):
    """techlib.lef.routing_layer_regex matches features def_parse.routing_layer_regex.

    Compare the compiled pattern string and the from_lef bool (compiled regex objects
    are not directly equality-comparable).
    """
    path = _lef_or_skip(platform)
    exp_rx, exp_from_lef = orig_rlr(path)
    act_rx, act_from_lef = lef.routing_layer_regex(path)
    assert act_rx.pattern == exp_rx.pattern, f"{platform}: regex pattern differs"
    assert act_from_lef == exp_from_lef, f"{platform}: from_lef flag differs"
    # Real LEFs yield layers -> from_lef must be True.
    assert act_from_lef is True, f"{platform}: expected layers from real LEF"


# --------------------------------------------------------------------------- #
# Layer-family sanity (don't pin exact counts — just naming conventions).     #
# --------------------------------------------------------------------------- #
def test_family_nangate45():
    path = _lef_or_skip("nangate45")
    names = lef.routing_layers(path)
    assert all(re.fullmatch(r"metal\d+", n) for n in names), names
    assert "metal1" in names


def test_family_sky130hd():
    path = _lef_or_skip("sky130hd")
    names = lef.routing_layers(path)
    assert "li1" in names
    for m in ["met1", "met2", "met3", "met4", "met5"]:
        assert m in names, (m, names)


def test_family_sky130hs():
    path = _lef_or_skip("sky130hs")
    names = lef.routing_layers(path)
    assert "li1" in names
    for m in ["met1", "met2", "met3", "met4", "met5"]:
        assert m in names, (m, names)


def test_family_asap7():
    path = _lef_or_skip("asap7")
    names = lef.routing_layers(path)
    metal_like = [n for n in names if re.fullmatch(r"M\d+", n)]
    assert metal_like, names
    assert "M1" in names


def test_family_gf180():
    path = _lef_or_skip("gf180")
    names = lef.routing_layers(path)
    metal_like = [n for n in names if re.fullmatch(r"Metal\d+", n)]
    assert metal_like, names
    assert "Metal1" in names


def test_family_ihp():
    path = _lef_or_skip("ihp-sg13g2")
    names = lef.routing_layers(path)
    assert any(n.startswith("Metal") for n in names), names
    assert any(n.startswith("TopMetal") for n in names), names


# --------------------------------------------------------------------------- #
# No-LEF / missing-LEF behavior (runs anywhere — no platform dependency).     #
# --------------------------------------------------------------------------- #
def test_no_lef_routing_layer_info_is_default():
    """Missing LEF => DEFAULT_LAYER_INFO, equal to congestion's DEFAULT_LAYER_INFO."""
    info = lef.routing_layer_info("/nonexistent/path/tech.lef")
    assert info == lef.DEFAULT_LAYER_INFO
    assert info == extract_congestion.DEFAULT_LAYER_INFO


def test_no_lef_routing_layers_empty():
    assert lef.routing_layers("/nonexistent/path/tech.lef") == []
    assert lef.routing_layers("") == []
    assert lef.routing_layers(None) == []


def test_no_lef_routing_layer_regex_falls_back():
    rx, from_lef = lef.routing_layer_regex("/nonexistent/path/tech.lef")
    assert from_lef is False
    assert rx.pattern == re.compile(r"(metal\d+)", re.IGNORECASE).pattern
    # Matches the original's fallback too.
    orig_rx, orig_from = orig_rlr("/nonexistent/path/tech.lef")
    assert rx.pattern == orig_rx.pattern
    assert from_lef == orig_from


def test_default_layer_info_matches_congestion_constant():
    """The ported module-level constant is byte-equal to congestion's."""
    assert lef.DEFAULT_LAYER_INFO == extract_congestion.DEFAULT_LAYER_INFO


def test_injectable_fallback_used_when_no_layers():
    """fallback= overrides DEFAULT_LAYER_INFO on absent/empty-LEF paths (Task 5 hook)."""
    sentinel = {"customL": {"pitch": 0.5, "direction": "VERTICAL"}}
    assert lef.routing_layer_info("/nonexistent/path/tech.lef", fallback=sentinel) is sentinel
