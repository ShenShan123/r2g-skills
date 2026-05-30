"""Tests for techlib.lef — the unified tech-LEF routing-layer parser (Task 2).

``techlib.lef`` consolidates the two tech-LEF parsers that previously lived apart:

  * NAMES view — ``routing_layers`` / ``routing_layer_regex`` ported from the feature
    workers' old ``def_parse.py`` (``parse_routing_layers`` / ``routing_layer_regex``).
  * PITCH/DIRECTION view — ``routing_layer_info`` ported from congestion's
    ``parse_tech_lef`` (with an injectable fallback).

Behavioral equivalence to those originals was proven during the migration (Task 2) and
is held by the byte-for-byte CSV gate (tests/test_techlib_crossplatform.py). The original
``features/def_parse.py`` was deleted in Task 9 and congestion's ``parse_tech_lef``
collapsed into a direct ``routing_layer_info`` call, so these tests now pin
``techlib.lef`` against KNOWN expectations on the REAL tech LEFs of all six ORFS
platforms (layer-name families per platform; regex pattern shape + from_lef flag;
no-LEF fallback == DEFAULT_LAYER_INFO).

A platform is SKIPPED when its tech LEF is absent, so the suite runs on a bare
checkout (ORFS platforms are machine-local).
"""
from __future__ import annotations

import glob
import os
import re

import pytest

from techlib import lef


# --------------------------------------------------------------------------- #
# Tech-LEF path resolution — robust + skippable.                              #
# --------------------------------------------------------------------------- #
def _platforms_dir():
    """Directory holding the ORFS platforms, or None if not resolvable.

    Prefers $ORFS_ROOT/flow/platforms (the authoritative flow location, set by the
    autodetected ORFS env) FIRST; only then falls back to the known checkout. When
    neither resolves, returns None so the equivalence tests SKIP (never fail) on a
    machine without an ORFS checkout.
    """
    candidates = []
    orfs_root = os.environ.get("ORFS_ROOT")
    if orfs_root:
        candidates.append(os.path.join(orfs_root, "flow", "platforms"))
    # Machine-local fallback for this dev box; absent elsewhere -> tests SKIP, not fail.
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
def test_routing_layers_real_lef(platform):
    """techlib.lef.routing_layers yields a non-empty, well-formed layer-name list.

    Every real routing tech LEF declares >=1 TYPE ROUTING layer; names are unique
    non-empty strings in declaration order. (Per-platform naming families are pinned
    in the test_family_* tests below.)
    """
    path = _lef_or_skip(platform)
    actual = lef.routing_layers(path)
    assert len(actual) > 0, f"{platform}: no routing layers parsed (path/parse wrong?)"
    assert all(isinstance(n, str) and n for n in actual), f"{platform}: bad layer names {actual!r}"
    assert len(set(actual)) == len(actual), f"{platform}: duplicate layer names {actual!r}"


@pytest.mark.parametrize("platform", ALL_PLATFORMS)
def test_routing_layer_info_real_lef(platform):
    """techlib.lef.routing_layer_info yields a populated pitch/direction dict.

    On a real LEF the parser must return layers with positive pitch and a valid
    preferred direction — not the nangate fallback table (except nangate45 itself,
    whose real parse may legitimately coincide with the fallback values).
    """
    path = _lef_or_skip(platform)
    actual = lef.routing_layer_info(path)
    assert len(actual) > 0, f"{platform}: empty layer-info dict"
    for name, info in actual.items():
        assert info["pitch"] > 0, f"{platform}/{name}: non-positive pitch {info['pitch']!r}"
        assert info["direction"] in {"HORIZONTAL", "VERTICAL"}, \
            f"{platform}/{name}: bad direction {info['direction']!r}"
    # A real parse must NOT silently degrade to the fallback (which would mean the LEF
    # path/parse broke). nangate45 is exempt: its real layers can equal the fallback.
    if platform != "nangate45":
        assert actual != lef.DEFAULT_LAYER_INFO, \
            f"{platform}: layer-info collapsed to the nangate45 fallback (parse failed?)"


@pytest.mark.parametrize("platform", ALL_PLATFORMS)
def test_routing_layer_regex_real_lef(platform):
    """techlib.lef.routing_layer_regex builds a from-LEF matcher over the real layers.

    from_lef must be True (real LEF yields layers) and the compiled pattern must
    full-token-match every parsed layer name while NOT matching a clearly-bogus token.
    """
    path = _lef_or_skip(platform)
    act_rx, act_from_lef = lef.routing_layer_regex(path)
    assert act_from_lef is True, f"{platform}: expected layers from real LEF"
    names = lef.routing_layers(path)
    for n in names:
        m = act_rx.search(f"+ ROUTED {n} ( 0 0 )")
        assert m and m.group(1) == n, f"{platform}: regex failed to match layer {n!r}"
    # The matcher is anchored to known layers, so a junk token must not match.
    assert act_rx.search("+ ROUTED zzznotalayer ( 0 0 )") is None, \
        f"{platform}: regex over-matched a non-layer token"


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
    """Missing LEF => the module-level DEFAULT_LAYER_INFO fallback (nangate45 table)."""
    info = lef.routing_layer_info("/nonexistent/path/tech.lef")
    assert info == lef.DEFAULT_LAYER_INFO


def test_no_lef_routing_layers_empty():
    assert lef.routing_layers("/nonexistent/path/tech.lef") == []
    assert lef.routing_layers("") == []
    assert lef.routing_layers(None) == []


def test_no_lef_routing_layer_regex_falls_back():
    rx, from_lef = lef.routing_layer_regex("/nonexistent/path/tech.lef")
    assert from_lef is False
    # The no-layer fallback is the platform-agnostic metal\d+ matcher.
    assert rx.pattern == re.compile(r"(metal\d+)", re.IGNORECASE).pattern
    # The fallback matcher still recognizes a generic metal layer.
    assert rx.search("ROUTED metal7 ( 0 0 )").group(1) == "metal7"


def test_default_layer_info_is_nangate45_table():
    """The module-level DEFAULT_LAYER_INFO is the known nangate45 metal1..metal10 table."""
    info = lef.DEFAULT_LAYER_INFO
    assert set(info) == {f"metal{i}" for i in range(1, 11)}
    assert info["metal1"] == {"pitch": 0.14, "direction": "HORIZONTAL"}
    assert info["metal10"] == {"pitch": 1.6, "direction": "VERTICAL"}


def test_injectable_fallback_used_when_no_layers():
    """fallback= overrides DEFAULT_LAYER_INFO on absent/empty-LEF paths (Task 5 hook)."""
    sentinel = {"customL": {"pitch": 0.5, "direction": "VERTICAL"}}
    assert lef.routing_layer_info("/nonexistent/path/tech.lef", fallback=sentinel) is sentinel
