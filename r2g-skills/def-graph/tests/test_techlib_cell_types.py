"""Tests for techlib.cell_types — the consolidated cell-type id mapping.

Behavioral equivalence to the original ``features/cell_type_map.py`` was proven during
the migration (Task 4) and is held by the byte-for-byte CSV gate
(tests/test_techlib_crossplatform.py). That oracle module was deleted in Task 9, so
these tests pin ``techlib.cell_types`` against KNOWN values:

  * Curated map RETIRED 2026-07-06 (22-master drift) — kept only as an import shim.
  * ``cell_type_id`` — lookup mechanics (strip/upper/None) against the shim dict.
  * ``build_runtime_map`` determinism — two calls equal, UNKNOWN=N, MACRO=N+1 shared
    by macro-lib cells, garbage->UNKNOWN.
  * ``resolve_cell_type_map`` — runtime map on EVERY platform incl. nangate45; the
    drifted masters (SDFF*/CLKGATE*/TLAT/AOI222_X1/...) heal to real ids.
  * sky130 real masters resolve via the runtime map — differentiated ids (quote-bug fixed).

The no-file tests run unconditionally. Liberty-backed tests SKIP (never fail) when the
ORFS platforms directory is absent, so the suite runs cleanly on a bare checkout.

sky130 quote-bug — FIXED on this branch:
  sky130 liberty quotes cell names (``cell ("sky130_fd_sc_hd__...")``). Previously
  ``techlib.liberty._strip_name_token`` did not strip the surrounding ``"`` chars, so
  ``lib_db['cells']`` keys retained them and never matched the unquoted ``master.upper()``
  lookup — collapsing cell_area/power/cell_type_id to 0/UNKNOWN for every sky130 cell (a
  pre-existing bug, not introduced by the techlib migration). ``_strip_name_token`` now
  strips the quotes, so sky130 masters resolve to real, differentiated ids and non-zero
  area/power (asap7/gf180/ihp/nangate are unquoted, so the strip is a no-op there).
"""
from __future__ import annotations

import os

import pytest

from techlib import cell_types


# ---------------------------------------------------------------------------
# Path resolution helpers — ORFS root first, machine-local fallback.
# ---------------------------------------------------------------------------

def _platforms_dir() -> str | None:
    candidates: list[str] = []
    orfs_root = os.environ.get("ORFS_ROOT")
    if orfs_root:
        candidates.append(os.path.join(orfs_root, "flow", "platforms"))
    # Machine-local fallback; absent elsewhere -> tests SKIP, not fail.
    candidates.append("/proj/workarea/user5/OpenROAD-flow-scripts/flow/platforms")
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def _sky130hd_lib() -> str | None:
    pdir = _platforms_dir()
    if not pdir:
        return None
    path = os.path.join(pdir, "sky130hd", "lib", "sky130_fd_sc_hd__tt_025C_1v80.lib")
    return path if os.path.isfile(path) else None


def _sky130hd_lib_or_skip() -> str:
    p = _sky130hd_lib()
    if not p:
        pytest.skip("sky130hd liberty absent (machine-local ORFS platforms)")
    return p


# ---------------------------------------------------------------------------
# 1. Curated map preserved (no files needed)
# ---------------------------------------------------------------------------

def test_retired_curated_map_still_importable_shim():
    """The curated map is retired (2026-07-06) but kept as an import-compat shim.

    Nothing may RESOLVE through it (see test_resolve_cell_type_map_nangate45_is_runtime)
    — but old imports must not break, and the alias identity is preserved.
    """
    m = cell_types.NANGATE45_CELL_TYPE_MAPPING
    assert m["UNKNOWN"] == 95  # shim contents frozen as documentation of the old space
    assert cell_types.COMPLETE_CELL_TYPE_MAPPING is m


# ---------------------------------------------------------------------------
# 2. cell_type_id equivalence (no files needed)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("master,expected", [
    # Curated hits
    ("INV_X1", 0),
    ("DFF_X2", 72),
    ("FAKERAM45_512X64", 113),
    ("UNKNOWN", 95),
    # Lowercase — must resolve after .strip().upper()
    ("inv_x1", 0),
    ("dff_x2", 72),
    ("fakeram45_512x64", 113),
    # Whitespace padding — must resolve after .strip().upper()
    ("  INV_X1  ", 0),
    # Non-existent master -> UNKNOWN = 95
    ("TOTALLY_NONEXISTENT_CELL", 95),
    # Empty / None -> UNKNOWN = 95
    ("", 95),
    (None, 95),
])
def test_cell_type_id_pinned(master, expected):
    """cell_type_id resolves to the KNOWN expected id (strip+upper normalization)."""
    m_new = cell_types.cell_type_id(master, cell_types.NANGATE45_CELL_TYPE_MAPPING)
    assert m_new == expected, \
        f"cell_type_id({master!r}): expected {expected}, got {m_new}"


# ---------------------------------------------------------------------------
# 3. build_runtime_map determinism + equivalence (needs sky130hd liberty)
# ---------------------------------------------------------------------------

def test_build_runtime_map_sc_none_deterministic():
    """build_runtime_map(db, sc=None) is deterministic; UNKNOWN == num cells."""
    lib = _sky130hd_lib_or_skip()
    from techlib import liberty
    db = liberty.load_liberty_db([lib])

    map_new_1 = cell_types.build_runtime_map(db, sc_lib_paths=None)
    map_new_2 = cell_types.build_runtime_map(db, sc_lib_paths=None)

    assert map_new_1 == map_new_2, "build_runtime_map is non-deterministic (sc=None)"

    # UNKNOWN must equal len(all cell names)
    cells = db.get("cells", {})
    n = len(cells)
    assert map_new_1["UNKNOWN"] == n, \
        f"UNKNOWN should be {n} (num cells), got {map_new_1['UNKNOWN']}"

    # Guard: non-empty lib => real entries
    assert n > 10, f"Suspiciously few cells ({n}) in sky130hd liberty"


def test_build_runtime_map_sc_set_deterministic():
    """build_runtime_map(db, sc_lib_paths=[lib]) is deterministic; UNKNOWN == sc count."""
    lib = _sky130hd_lib_or_skip()
    from techlib import liberty
    db = liberty.load_liberty_db([lib])

    map_new_1 = cell_types.build_runtime_map(db, sc_lib_paths=[lib])
    map_new_2 = cell_types.build_runtime_map(db, sc_lib_paths=[lib])

    assert map_new_1 == map_new_2, "build_runtime_map is non-deterministic (sc=[lib])"

    # UNKNOWN must equal len(sc-filtered names)
    cells = db.get("cells", {})
    sc_names = sorted(k for k, v in cells.items() if v.get("source_lib") == lib)
    n = len(sc_names)
    assert map_new_1["UNKNOWN"] == n, \
        f"UNKNOWN should be {n} (sc-filtered cells), got {map_new_1['UNKNOWN']}"

    # A macro/garbage name must resolve to UNKNOWN
    garbage_id = cell_types.cell_type_id("TOTALLY_MADE_UP_MACRO_XY", map_new_1)
    assert garbage_id == n, \
        f"Garbage master should map to UNKNOWN={n}, got {garbage_id}"


# ---------------------------------------------------------------------------
# 4. resolve_cell_type_map strategy (needs sky130hd liberty)
# ---------------------------------------------------------------------------

def test_resolve_cell_type_map_nangate45_is_runtime():
    """resolve_cell_type_map('nangate45', ...) builds a runtime map (curated retired).

    The frozen curated map had drifted 22 masters behind the deployed liberty
    (SDFF*/CLKGATE*/TLAT/AOI222_X1/... -> UNKNOWN=95), so nangate45 now self-heals
    from liberty like every other platform (2026-07-06).
    """
    fake_db = {"cells": {"INV_X1": {"source_lib": "x"}, "SDFF_X1": {"source_lib": "x"}}}
    result = cell_types.resolve_cell_type_map("nangate45", fake_db)
    assert result is not cell_types.NANGATE45_CELL_TYPE_MAPPING
    assert result == cell_types.build_runtime_map(fake_db)
    # The drifted master that the curated map silently aliased onto UNKNOWN now
    # resolves to a real id.
    assert cell_types.cell_type_id("SDFF_X1", result) != result["UNKNOWN"]


def test_nangate45_runtime_map_heals_drifted_masters():
    """Real nangate45 liberty: the 22 curated-map absentees resolve to real ids."""
    pdir = _platforms_dir()
    lib = os.path.join(pdir or "", "nangate45", "lib", "NangateOpenCellLibrary_typical.lib")
    if not pdir or not os.path.isfile(lib):
        pytest.skip("nangate45 liberty absent (machine-local ORFS platforms)")
    from techlib import liberty
    db = liberty.load_liberty_db([lib])
    mp = cell_types.resolve_cell_type_map("nangate45", db, sc_lib_paths=[lib])
    unknown = mp["UNKNOWN"]
    for master in ("SDFF_X1", "SDFFRS_X2", "CLKGATE_X1", "CLKGATETST_X8",
                   "TLAT_X1", "AOI222_X1", "OAI222_X1"):
        assert cell_types.cell_type_id(master, mp) != unknown, \
            f"{master} still collapses to UNKNOWN — curated-map drift regressed"


def test_runtime_map_macro_cells_get_shared_macro_id():
    """Macro-lib cells share the dedicated MACRO id (= N+1), not UNKNOWN (= N)."""
    std, macro = "/p/std.lib", "/p/fakeram45_512x64.lib"
    db = {"cells": {
        "INV_X1": {"source_lib": std}, "NAND2_X1": {"source_lib": std},
        "FAKERAM45_512X64": {"source_lib": macro},
        "FAKERAM45_256X32": {"source_lib": macro},
    }}
    mp = cell_types.build_runtime_map(db, sc_lib_paths=[std])
    assert mp["UNKNOWN"] == 2
    assert mp["MACRO"] == 3
    # both macros share the MACRO id; std ids unaffected by macro presence
    assert cell_types.cell_type_id("fakeram45_512x64", mp) == 3
    assert cell_types.cell_type_id("fakeram45_256x32", mp) == 3
    assert cell_types.cell_type_id("INV_X1", mp) == 0
    # a garbage master still lands on UNKNOWN, distinguishable from macros
    assert cell_types.cell_type_id("NO_SUCH_CELL", mp) == 2


def test_resolve_cell_type_map_sky130hd_returns_runtime():
    """resolve_cell_type_map('sky130hd', db, sc) returns a runtime map (not the curated dict)."""
    lib = _sky130hd_lib_or_skip()
    from techlib import liberty
    db = liberty.load_liberty_db([lib])

    result_new = cell_types.resolve_cell_type_map("sky130hd", db, sc_lib_paths=[lib])

    # Must NOT be the curated nangate45 dict
    assert result_new is not cell_types.NANGATE45_CELL_TYPE_MAPPING, \
        "resolve_cell_type_map('sky130hd') must return a runtime map, not the curated dict"

    # The runtime map must equal build_runtime_map directly (runtime strategy).
    expected = cell_types.build_runtime_map(db, sc_lib_paths=[lib])
    assert result_new == expected


# ---------------------------------------------------------------------------
# 5. sky130 real masters resolve via the runtime map (quote-bug FIXED; needs sky130hd liberty)
# ---------------------------------------------------------------------------


def test_sky130_masters_resolve_via_runtime_map():
    """Real sky130 masters resolve to differentiated, non-UNKNOWN ids (quote-bug fixed).

    Masters are taken straight from the parsed liberty (now quote-free uppercase keys),
    so the test never guesses cell names that might be absent from a given corner lib.
    """
    lib = _sky130hd_lib_or_skip()
    from techlib import liberty
    db = liberty.load_liberty_db([lib])

    sc_map = cell_types.resolve_cell_type_map("sky130hd", db, sc_lib_paths=[lib])
    unknown = sc_map["UNKNOWN"]

    real = [k for k in db.get("cells", {}) if k.startswith("SKY130_FD_SC_HD__")][:8]
    assert real, "no sky130 standard cells parsed from the liberty"
    ids = {m: cell_types.cell_type_id(m, sc_map) for m in real}
    # Every real master must now resolve to a real id, not UNKNOWN (the quote-bug fix).
    assert all(cid != unknown for cid in ids.values()), \
        f"some real sky130 masters still resolve to UNKNOWN={unknown}: {ids}"
    # And the ids are differentiated (not all collapsed onto one bucket).
    assert len(set(ids.values())) > 1, f"expected differentiated cell_type_ids, got {ids}"


def test_sky130_runtime_map_keys_are_quote_free():
    """Quote-bug fix evidence: liberty cell keys carry no surrounding double-quotes."""
    lib = _sky130hd_lib_or_skip()
    from techlib import liberty
    db = liberty.load_liberty_db([lib])

    cells = db.get("cells", {})
    quoted = [k for k in cells if k.startswith('"') or k.endswith('"')]
    assert not quoted, f"liberty cell keys still carry surrounding quotes: {quoted[:5]}"
    # The unquoted, uppercased master form is now a real key in the runtime map.
    sc_map = cell_types.build_runtime_map(db, sc_lib_paths=[lib])
    assert "SKY130_FD_SC_HD__INV_1" in sc_map, \
        "unquoted sky130 master key missing from runtime map (quote-strip regression)"
