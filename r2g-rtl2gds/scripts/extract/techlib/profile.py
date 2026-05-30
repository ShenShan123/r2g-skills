"""techlib.profile — the single per-platform constant store (ORFS-only).

Historically the per-platform constants that the extract stage needs were scattered
across three unrelated places:

  * **supply voltage** — the ``case "$PLATFORM"`` voltage map in
    ``scripts/flow/resolve_platform_paths.sh`` (the fallback used when
    ``PWR_NETS_VOLTAGES`` doesn't yield a numeric value).
  * **well-tap / endcap master substrings** — ``_PLATFORM_TAP_EXTRA`` + the base
    ``["TAP"]`` in ``techlib.liberty`` (identical to ``features/lib_db.py``).
  * **cell-type strategy** (curated nangate map vs. a runtime-built map) — the
    ``(platform or "nangate45").lower() == "nangate45"`` test in
    ``techlib.cell_types.resolve_cell_type_map``.
  * **congestion's nangate routing-layer fallback** — ``techlib.lef.DEFAULT_LAYER_INFO``
    (the per-layer pitch/direction table used when a tech LEF yields no routing layers).

This module GATHERS those constants in one place as a ``TechProfile`` per ORFS platform.
Every value here is **copied verbatim** from the cited scattered source (see the
per-field comments below). This is a behavior-neutral consolidation: NOTHING is rewired
yet. The consumers (``techlib.lef`` / ``techlib.liberty`` / ``techlib.cell_types`` and the
shell) keep their own copies and are re-pointed to read this profile in Tasks 7/8. Until
then, ``tests/test_techlib_profile.py`` asserts each gathered value still EQUALS its
scattered source, so the byte-for-byte CSV gate stays green.

ORFS-only by design: there is no generic/foundry abstraction and no auto-detection —
just one concrete frozen ``TechProfile`` for each of the six platforms this checkout
ships, plus a documented degrade-don't-raise default for unknown names (mirroring the
current scattered fallbacks).

Pure stdlib (``dataclasses``) + ``techlib.lef`` (for ``DEFAULT_LAYER_INFO``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Literal, Tuple

from techlib import lef


def _copy_layer_info(layer_info):
    """Per-profile copy of a routing-layer table (outer + inner dicts).

    ``dict(layer_info)`` only copies the OUTER dict — the inner ``{pitch,direction}``
    sub-dicts would still alias ``lef.DEFAULT_LAYER_INFO`` (and each other across the
    cached profiles). ``frozen=True`` only blocks REBINDING the field, not nested
    mutation, so we copy each sub-dict to give every profile its own private table.
    """
    return {k: dict(v) for k, v in layer_info.items()}


@dataclass(frozen=True)
class TechProfile:
    """Immutable per-platform constant bundle (ORFS-only).

    Fields are copied verbatim from the scattered sources cited on each profile below:

      * ``name`` — the ORFS platform name (lower-case).
      * ``supply_voltage`` — nominal/supply voltage as a ``float`` (for arithmetic
        consumers); from the voltage case-map in ``scripts/flow/resolve_platform_paths.sh``.
      * ``supply_voltage_str`` — the VERBATIM shell token for that voltage (e.g. asap7's
        ``"0.70"``, gf180's ``"5.0"``). ``float`` stringifies lossily (``str(0.70) ==
        "0.7"``) but the shell emits ``SUPPLY_VOLTAGE=0.70``, so Task 6's KEY=VALUE shim
        MUST emit ``supply_voltage_str`` (not ``str(supply_voltage)``) to stay
        byte-for-byte identical to the shell. ``float(supply_voltage_str) ==
        supply_voltage`` always holds.
      * ``tap_patterns`` — well-tap/endcap master-name substrings; base ``"TAP"`` plus
        ``_PLATFORM_TAP_EXTRA`` from ``techlib.liberty`` / ``features/lib_db.py``.
        (The ``R2G_TAP_PATTERNS`` env override is intentionally NOT baked in — it is a
        runtime override, not a per-platform constant.)
      * ``cell_type_strategy`` — ``"curated"`` for nangate45 (uses the curated
        ``NANGATE45_CELL_TYPE_MAPPING``), ``"runtime"`` for every other platform (a map
        built at runtime from the resolved liberty). Mirrors
        ``techlib.cell_types.resolve_cell_type_map``.
      * ``fallback_routing_layers`` — congestion's nangate45 ``DEFAULT_LAYER_INFO``
        (per-layer pitch/direction), used when a tech LEF yields no routing layers.
    """

    name: str
    supply_voltage: float
    supply_voltage_str: str
    tap_patterns: Tuple[str, ...]
    cell_type_strategy: Literal["curated", "runtime"]
    fallback_routing_layers: Dict[str, dict] = field(
        default_factory=lambda: _copy_layer_info(lef.DEFAULT_LAYER_INFO)
    )


# --- supply voltages ---------------------------------------------------------
# The VERBATIM token each platform's case-branch assigns in
# scripts/flow/resolve_platform_paths.sh (the `case "$PLATFORM"` voltage fallback,
# lines ~74-82). Stored as the exact shell string so Task 6's KEY=VALUE shim can emit
# `SUPPLY_VOLTAGE=<token>` byte-for-byte (str(0.70)=="0.7" would NOT match the shell's
# "0.70"). supply_voltage (the float) is derived from this token; float arithmetic makes
# 5.0==5.00 and 0.70==0.7, so only the *string* is format-sensitive.
#   nangate45 -> 1.1 ; sky130hd|sky130hs -> 1.8 ; asap7 -> 0.70 ; gf180 -> 5.0 ;
#   ihp-sg13g2 -> 1.2 ; *) -> 1.0
_SUPPLY_VOLTAGE_STR = {
    "nangate45": "1.1",
    "sky130hd": "1.8",
    "sky130hs": "1.8",
    "asap7": "0.70",
    "gf180": "5.0",
    "ihp-sg13g2": "1.2",
}
_DEFAULT_SUPPLY_VOLTAGE_STR = "1.0"  # shell's `*) SUPPLY_VOLTAGE=1.0`

# --- tap-pattern extras ------------------------------------------------------
# Verbatim from techlib/liberty.py (== features/lib_db.py) `_PLATFORM_TAP_EXTRA`; the
# base pattern is the `["TAP"]` in `_tap_patterns`. Only gf180 has extras (its well-tap/
# endcap masters use __filltie / __endcap, which don't contain "TAP").
_PLATFORM_TAP_EXTRA = {
    "gf180": ["FILLTIE", "ENDCAP"],
}
_BASE_TAP_PATTERN = "TAP"  # the base `pats = ["TAP"]` in liberty._tap_patterns


def _tap_patterns_for(platform: str) -> Tuple[str, ...]:
    """Base "TAP" + per-platform extras (no R2G_TAP_PATTERNS env override — runtime-only)."""
    return tuple([_BASE_TAP_PATTERN] + list(_PLATFORM_TAP_EXTRA.get(platform, [])))


# The six ORFS platforms this checkout ships. nangate45 uses the curated cell-type map
# (cell_type_strategy="curated"), mirroring resolve_cell_type_map's
# `(platform or "nangate45").lower() == "nangate45"` test; everyone else is "runtime".
_ORFS_PLATFORMS = ("nangate45", "sky130hd", "sky130hs", "asap7", "gf180", "ihp-sg13g2")


def _build_profile(platform: str) -> TechProfile:
    sv_str = _SUPPLY_VOLTAGE_STR.get(platform, _DEFAULT_SUPPLY_VOLTAGE_STR)
    return TechProfile(
        name=platform,
        supply_voltage=float(sv_str),
        supply_voltage_str=sv_str,
        tap_patterns=_tap_patterns_for(platform),
        cell_type_strategy="curated" if platform == "nangate45" else "runtime",
        # Per-profile copy of techlib.lef.DEFAULT_LAYER_INFO (congestion's nangate
        # fallback) — equal value, no shared inner-dict aliasing.
        fallback_routing_layers=_copy_layer_info(lef.DEFAULT_LAYER_INFO),
    )


_PROFILES = {p: _build_profile(p) for p in _ORFS_PLATFORMS}


def get_profile(name: str) -> TechProfile:
    """Return the ``TechProfile`` for an ORFS platform (case-insensitive on ``name``).

    For an UNKNOWN platform name this DEGRADES (does not raise), reproducing the current
    scattered fallbacks so a future Task-6/7/8 rewire stays behavior-neutral:

      * ``supply_voltage = 1.0`` / ``supply_voltage_str = "1.0"`` — shell's ``*)``.
      * ``tap_patterns = ("TAP",)``      — lib_db's base ``["TAP"]`` (no per-platform extras).
      * ``cell_type_strategy = "runtime"`` — resolve_cell_type_map's non-nangate path.
      * ``fallback_routing_layers``      — the same nangate ``DEFAULT_LAYER_INFO``.

    The returned profile's ``name`` is the lower-cased input so callers can tell which
    platform (or unknown name) they asked for.
    """
    key = (name or "").lower()
    prof = _PROFILES.get(key)
    if prof is not None:
        return prof
    # Unknown platform: degrade exactly like the scattered sources do (don't raise).
    return _build_profile(key)
