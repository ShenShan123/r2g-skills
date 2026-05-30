"""Tests for techlib.profile — the per-platform constant store (Task 5).

``techlib.profile`` GATHERS the per-platform constants that previously lived scattered
across three places. This refactor is behavior-neutral: NOTHING is rewired yet (consumers
get re-pointed in Tasks 7/8), so these tests prove the gathered values still EQUAL their
scattered sources by importing the REAL sources (non-circular) and comparing:

  * ``supply_voltage``        vs. the voltage case-map in resolve_platform_paths.sh.
  * ``tap_patterns``          vs. liberty._PLATFORM_TAP_EXTRA + the base ["TAP"].
  * ``fallback_routing_layers`` vs. lef.DEFAULT_LAYER_INFO == extract_congestion.DEFAULT_LAYER_INFO.
  * ``cell_type_strategy``    vs. cell_types.resolve_cell_type_map's nangate test.

These tests need NO external/platform files, so none should skip. Modules are imported as
plain top-level modules via the conftest sys.path entries (EXTRACT_DIR for ``techlib.*``,
LABELS_DIR for ``extract_congestion``, FEATURES_DIR for ``lib_db``).
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from techlib import profile, lef, liberty, cell_types

# The label extractor (congestion) keeps its own copy of DEFAULT_LAYER_INFO — the oracle
# the fallback table must still equal. Imported as a top-level module via LABELS_DIR.
import extract_congestion

# lib_db is the original (untouched) home of _PLATFORM_TAP_EXTRA; techlib.liberty is the
# verbatim copy. We cross-check the profile against BOTH.
import lib_db

ORFS_PLATFORMS = ("nangate45", "sky130hd", "sky130hs", "asap7", "gf180", "ihp-sg13g2")

EXPECTED_VOLTAGE = {
    "nangate45": 1.1,
    "sky130hd": 1.8,
    "sky130hs": 1.8,
    "asap7": 0.70,
    "gf180": 5.0,
    "ihp-sg13g2": 1.2,
}

# The VERBATIM shell tokens (string compare territory — str(0.70) == "0.7" != "0.70").
EXPECTED_VOLTAGE_STR = {
    "nangate45": "1.1",
    "sky130hd": "1.8",
    "sky130hs": "1.8",
    "asap7": "0.70",
    "gf180": "5.0",
    "ihp-sg13g2": "1.2",
}

RESOLVE_SH = (
    Path(__file__).resolve().parents[1]
    / "scripts" / "flow" / "resolve_platform_paths.sh"
)


# --------------------------------------------------------------------------- #
# supply_voltage
# --------------------------------------------------------------------------- #
def test_supply_voltage_literals():
    for p in ORFS_PLATFORMS:
        assert profile.get_profile(p).supply_voltage == EXPECTED_VOLTAGE[p], p


def test_supply_voltage_unknown_is_one():
    assert profile.get_profile("totally-not-a-platform").supply_voltage == 1.0


def test_supply_voltage_str_literals():
    for p in ORFS_PLATFORMS:
        prof = profile.get_profile(p)
        assert prof.supply_voltage_str == EXPECTED_VOLTAGE_STR[p], p
        # The float field must agree numerically with the verbatim string.
        assert float(prof.supply_voltage_str) == prof.supply_voltage, p


def test_supply_voltage_str_unknown_is_one_dot_zero():
    prof = profile.get_profile("totally-not-a-platform")
    assert prof.supply_voltage_str == "1.0"
    assert float(prof.supply_voltage_str) == prof.supply_voltage == 1.0


def _parse_shell_voltage_map(sh_text: str) -> dict:
    """Parse the `case "$PLATFORM"` voltage block in resolve_platform_paths.sh.

    Returns {platform: verbatim_token_str} for every concrete branch plus {"*": ...} for
    the default — the VERBATIM RHS token (e.g. "0.70", "5.0"), NOT a float, so the
    profile's supply_voltage_str can be string-compared against it (Task 6's byte-identity
    contract). Handles the `sky130hd|sky130hs)` alternation form. We deliberately parse
    only the inner platform-case that assigns SUPPLY_VOLTAGE (not the outer
    SUPPLY_VOLTAGE-validity case).
    """
    # Isolate the inner `case "$PLATFORM" in ... esac` that sets SUPPLY_VOLTAGE.
    m = re.search(r'case\s+"\$PLATFORM"\s+in(.*?)esac', sh_text, re.S)
    assert m, "could not find the `case \"$PLATFORM\"` block in the shell script"
    block = m.group(1)
    out = {}
    # Each branch looks like:  pat1|pat2)  SUPPLY_VOLTAGE=1.8 ;;
    for line in block.splitlines():
        bm = re.match(
            r"\s*([^)]+?)\)\s*SUPPLY_VOLTAGE=([0-9.]+)\s*;;", line.strip()
        )
        if not bm:
            continue
        token = bm.group(2)  # verbatim string token, e.g. "0.70"
        for pat in bm.group(1).split("|"):
            out[pat.strip()] = token
    return out


def test_supply_voltage_matches_shell_case_map():
    """Cross-check every profile voltage against what the shell case-map assigns.

    Compares BOTH the verbatim string token (Task 6 byte-identity) and the derived float.
    Catches a future shell-map edit that diverges from the profile (until Task 7/8
    collapse them into one source).
    """
    if not RESOLVE_SH.is_file():
        pytest.skip(f"resolve_platform_paths.sh not found at {RESOLVE_SH}")
    sh = _parse_shell_voltage_map(RESOLVE_SH.read_text())
    # Sanity: the parser actually found the branches we expect.
    for p in ORFS_PLATFORMS:
        assert p in sh, f"shell case-map missing platform {p}; parser/script drift"
    assert "*" in sh and sh["*"] == "1.0", "shell default branch should assign 1.0"

    for p in ORFS_PLATFORMS:
        prof = profile.get_profile(p)
        # Verbatim token equality (string) — this is what Task 6's shim must emit.
        assert prof.supply_voltage_str == sh[p], (
            f"{p}: profile_str={prof.supply_voltage_str!r} shell={sh[p]!r}"
        )
        # And the derived float still matches the shell token parsed as a float.
        assert prof.supply_voltage == float(sh[p]), p
    # Unknown-platform default ties to the shell's `*)` branch.
    assert profile.get_profile("nope").supply_voltage_str == sh["*"]
    assert profile.get_profile("nope").supply_voltage == float(sh["*"])


# --------------------------------------------------------------------------- #
# tap_patterns
# --------------------------------------------------------------------------- #
def test_tap_patterns_match_source_extras():
    # techlib.liberty and the original lib_db must agree on the extras dict.
    assert liberty._PLATFORM_TAP_EXTRA == lib_db._PLATFORM_TAP_EXTRA

    for p in ORFS_PLATFORMS:
        expected = {"TAP"} | set(liberty._PLATFORM_TAP_EXTRA.get(p, []))
        assert set(profile.get_profile(p).tap_patterns) == expected, p


def test_tap_patterns_gf180_has_filltie_endcap():
    pats = set(profile.get_profile("gf180").tap_patterns)
    assert pats == {"TAP", "FILLTIE", "ENDCAP"}


def test_tap_patterns_others_are_just_tap():
    for p in ("nangate45", "sky130hd", "sky130hs", "asap7", "ihp-sg13g2"):
        assert set(profile.get_profile(p).tap_patterns) == {"TAP"}, p


def test_tap_patterns_unknown_is_just_tap():
    # Unknown platform gets the base lib_db pattern only (no extras).
    assert set(profile.get_profile("mystery").tap_patterns) == {"TAP"}


# --------------------------------------------------------------------------- #
# fallback_routing_layers
# --------------------------------------------------------------------------- #
def test_fallback_routing_layers_equals_lef_and_congestion():
    # The two scattered copies must agree (lef.py ported it verbatim from congestion).
    assert lef.DEFAULT_LAYER_INFO == extract_congestion.DEFAULT_LAYER_INFO
    for p in ORFS_PLATFORMS:
        flayers = profile.get_profile(p).fallback_routing_layers
        assert flayers == lef.DEFAULT_LAYER_INFO
        assert flayers == extract_congestion.DEFAULT_LAYER_INFO


def test_fallback_routing_layers_unknown_also_nangate_table():
    assert profile.get_profile("xyz").fallback_routing_layers == lef.DEFAULT_LAYER_INFO


def test_fallback_routing_layers_copy_independence():
    """Mutating a profile's nested layer table must NOT corrupt the source or siblings.

    `dict(lef.DEFAULT_LAYER_INFO)` would alias the inner {pitch,direction} sub-dicts;
    frozen=True only blocks rebinding the field, not nested mutation. We copy each
    sub-dict, so this mutation stays local.
    """
    src_before = lef.DEFAULT_LAYER_INFO["metal1"]["pitch"]
    prof = profile.get_profile("nangate45")
    try:
        prof.fallback_routing_layers["metal1"]["pitch"] = 99.9

        # Source untouched.
        assert lef.DEFAULT_LAYER_INFO["metal1"]["pitch"] == src_before
        assert lef.DEFAULT_LAYER_INFO["metal1"]["pitch"] != 99.9
        # A different profile's table is also untouched (no cross-profile aliasing).
        assert (
            profile.get_profile("sky130hd").fallback_routing_layers["metal1"]["pitch"]
            == src_before
        )
    finally:
        # get_profile returns the CACHED profile; restore so test ordering can't leak.
        prof.fallback_routing_layers["metal1"]["pitch"] = src_before


# --------------------------------------------------------------------------- #
# cell_type_strategy
# --------------------------------------------------------------------------- #
def test_cell_type_strategy_nangate_curated_others_runtime():
    assert profile.get_profile("nangate45").cell_type_strategy == "curated"
    for p in ("sky130hd", "sky130hs", "asap7", "gf180", "ihp-sg13g2"):
        assert profile.get_profile(p).cell_type_strategy == "runtime", p


def test_cell_type_strategy_unknown_is_runtime():
    assert profile.get_profile("whatever").cell_type_strategy == "runtime"


def test_cell_type_strategy_ties_to_resolve_cell_type_map():
    # "curated" for nangate45 must mean resolve_cell_type_map returns the curated map
    # (identity with NANGATE45_CELL_TYPE_MAPPING) — independent of any liberty.
    assert profile.get_profile("nangate45").cell_type_strategy == "curated"
    assert (
        cell_types.resolve_cell_type_map("nangate45", {})
        is cell_types.NANGATE45_CELL_TYPE_MAPPING
    )

    # "runtime" for a non-nangate platform must mean a runtime map is BUILT (not the
    # curated identity). Feed a tiny synthetic lib_db so build_runtime_map has cells.
    fake_db = {"cells": {"FOO": {"source_lib": "x"}, "BAR": {"source_lib": "x"}}}
    runtime_map = cell_types.resolve_cell_type_map("sky130hd", fake_db)
    assert runtime_map is not cell_types.NANGATE45_CELL_TYPE_MAPPING
    assert runtime_map == {"BAR": 0, "FOO": 1, "UNKNOWN": 2}
    assert profile.get_profile("sky130hd").cell_type_strategy == "runtime"


# --------------------------------------------------------------------------- #
# get_profile basics + frozen dataclass
# --------------------------------------------------------------------------- #
def test_get_profile_case_insensitive():
    for p in ORFS_PLATFORMS:
        lower = profile.get_profile(p)
        upper = profile.get_profile(p.upper())
        assert upper.name == p
        assert upper == lower


def test_get_profile_known_names_set_name():
    for p in ORFS_PLATFORMS:
        prof = profile.get_profile(p)
        assert isinstance(prof, profile.TechProfile)
        assert prof.name == p


def test_get_profile_unknown_returns_documented_default():
    prof = profile.get_profile("Made-Up-Platform")
    assert isinstance(prof, profile.TechProfile)
    assert prof.name == "made-up-platform"  # lower-cased input
    assert prof.supply_voltage == 1.0
    assert set(prof.tap_patterns) == {"TAP"}
    assert prof.cell_type_strategy == "runtime"
    assert prof.fallback_routing_layers == lef.DEFAULT_LAYER_INFO


def test_get_profile_none_name_degrades():
    # Defensive: a None name must not crash (treated as unknown -> default).
    prof = profile.get_profile(None)  # type: ignore[arg-type]
    assert prof.supply_voltage == 1.0
    assert prof.cell_type_strategy == "runtime"


def test_techprofile_is_frozen():
    prof = profile.get_profile("nangate45")
    with pytest.raises(dataclasses.FrozenInstanceError):
        prof.supply_voltage = 9.9  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        prof.name = "x"  # type: ignore[misc]
