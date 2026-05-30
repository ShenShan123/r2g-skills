"""Tests for techlib.cell_types — the consolidated cell-type id mapping.

Behavioral equivalence to the original ``features/cell_type_map.py`` was proven during
the migration (Task 4) and is held by the byte-for-byte CSV gate
(tests/test_techlib_crossplatform.py). That oracle module was deleted in Task 9, so
these tests pin ``techlib.cell_types`` against KNOWN values:

  * Curated map preserved — UNKNOWN=95, INV_X1=0, DFF_X2=72, FAKERAM45_* keys upper-cased.
  * ``cell_type_id`` — curated hits, lowercase inputs, non-existent master -> UNKNOWN.
  * ``build_runtime_map`` determinism — two calls equal, UNKNOWN=N, macro/garbage->UNKNOWN.
  * ``resolve_cell_type_map`` strategy — nangate45 returns the curated dict; sky130hd builds runtime.
  * cordic-style "all masters UNKNOWN" baseline — observed value pinned; root-cause documented.

The no-file tests run unconditionally. Liberty-backed tests SKIP (never fail) when the
ORFS platforms directory is absent, so the suite runs cleanly on a bare checkout.

CONCERN (do not fix here — out of scope, tracked for Task 13):
  The sky130hd runtime map built by ``build_runtime_map(db, sc_lib_paths=[lib])`` has keys
  that include surrounding double-quote characters in the cell name token, e.g.
  ``'"SKY130_FD_SC_HD__A211OI_1"'`` (the liberty parser preserves the ``"..."`` token
  verbatim around the cell name and ``_norm_key`` just upper-cases without stripping quotes).
  Because a worker calls ``cell_type_id(master, mapping)`` with ``master.upper()`` =
  ``"SKY130_FD_SC_HD__A211OI_1"`` (no surrounding quotes), no key matches and every real
  sky130 master resolves to UNKNOWN. This is a pre-existing bug (it existed in the original
  ``cell_type_map`` too — it was NOT introduced by the techlib migration), and it means the
  sky130hd feature dataset has ``cell_type_id == UNKNOWN`` for every
  standard cell. Task 13 correctness validation should address the quote-stripping in
  ``techlib.liberty._norm_key`` so that ``lib_db['cells']`` keys are quote-free.
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

def test_curated_map_pinned_anchors():
    """The curated nangate45 map carries its known anchor ids.

    These are the durable id contract the feature dataset depends on (a reshuffle
    would silently relabel every nangate45 cell across the corpus).
    """
    m = cell_types.NANGATE45_CELL_TYPE_MAPPING
    assert m["INV_X1"] == 0
    assert m["DFF_X2"] == 72
    assert m["FAKERAM45_512X64"] == 113
    assert m["UNKNOWN"] == 95
    # A real curated map is large (>90 std-cell entries + macros).
    assert len(m) > 90, f"curated map shrank unexpectedly ({len(m)} entries)"


def test_unknown_is_95():
    """UNKNOWN = 95 in the curated map."""
    assert cell_types.NANGATE45_CELL_TYPE_MAPPING["UNKNOWN"] == 95


def test_fakeram45_keys_upper_cased():
    """FAKERAM45_* keys are upper-cased and present in the curated map."""
    expected_keys = [
        "FAKERAM45_512X64",
        "FAKERAM45_64X96",
        "FAKERAM45_256X32",
        "FAKERAM45_32X64",
        "FAKERAM45_64X32",
        "FAKERAM45_256X96",
        "FAKERAM45_64X15",
        "FAKERAM45_64X7",
    ]
    for key in expected_keys:
        assert key in cell_types.NANGATE45_CELL_TYPE_MAPPING, \
            f"FAKERAM45 key {key!r} missing from techlib.cell_types curated map"
        # Must be upper-cased (no lowercase variant present)
        assert key == key.upper(), f"Key {key!r} is not fully upper-cased"


def test_complete_cell_type_mapping_alias():
    """COMPLETE_CELL_TYPE_MAPPING is the same object as NANGATE45_CELL_TYPE_MAPPING."""
    assert cell_types.COMPLETE_CELL_TYPE_MAPPING is cell_types.NANGATE45_CELL_TYPE_MAPPING


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

def test_resolve_cell_type_map_nangate45_returns_curated():
    """resolve_cell_type_map('nangate45', ...) returns the curated dict.

    This runs unconditionally (no ORFS liberty needed): the nangate45 branch
    short-circuits to the curated map and ignores the ``lib_db`` argument, so an
    empty-dict placeholder exercises the same code path.
    """
    result_new = cell_types.resolve_cell_type_map("nangate45", {})

    assert result_new is cell_types.NANGATE45_CELL_TYPE_MAPPING, \
        "resolve_cell_type_map('nangate45') should return the curated dict"


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
# 5. cordic-style "all masters -> UNKNOWN" baseline (needs sky130hd liberty)
#
# PINS THE CURRENT BEHAVIOR — do NOT change this test to assert non-UNKNOWN.
# The root cause is documented in this module's docstring (CONCERN section).
# ---------------------------------------------------------------------------

_REAL_SKY130_MASTERS = [
    "sky130_fd_sc_hd__a211oi_1",
    "sky130_fd_sc_hd__fill_8",
    "sky130_fd_sc_hd__buf_1",
    "sky130_fd_sc_hd__inv_1",
    "sky130_fd_sc_hd__dfxtp_1",
]


def test_cordic_masters_resolve_to_unknown_equivalence():
    """Real sky130 masters -> UNKNOWN in both modules (pins current behavior).

    OBSERVED VALUE: every real sky130 master resolves to UNKNOWN (== len(sc_names)).
    This is because the liberty parser retains surrounding double-quote characters in
    the cell-name token used as the dict key (e.g. '"SKY130_FD_SC_HD__A211OI_1"'),
    so master.upper() (without quotes) never matches any key in the runtime map.
    See the module CONCERN docstring for the full analysis.
    """
    lib = _sky130hd_lib_or_skip()
    from techlib import liberty
    db = liberty.load_liberty_db([lib])

    sc_map_new = cell_types.resolve_cell_type_map("sky130hd", db, sc_lib_paths=[lib])

    expected_unknown = sc_map_new["UNKNOWN"]

    for master in _REAL_SKY130_MASTERS:
        result_new = cell_types.cell_type_id(master, sc_map_new)

        # Pin the observed value: every real sky130 master is UNKNOWN
        # NOTE: this is the pre-existing bug documented in the CONCERN docstring.
        # Do not change this assertion — it documents the baseline, not the desired state.
        assert result_new == expected_unknown, (
            f"cell_type_id({master!r}) = {result_new}, expected UNKNOWN={expected_unknown} "
            f"(observed baseline). If this assertion fails, the liberty key format changed."
        )


def test_cordic_masters_unknown_root_cause_evidence():
    """Documents evidence of the quoted-key root cause (no assertion on fix needed).

    Verifies that the runtime map keys contain surrounding quote characters, which
    causes master.upper() lookups to miss. This is purely diagnostic — it pins the
    symptom that Task 13 correctness validation should resolve.
    """
    lib = _sky130hd_lib_or_skip()
    from techlib import liberty
    db = liberty.load_liberty_db([lib])

    cells = db.get("cells", {})
    # Sample keys from the cells dict
    sample_keys = list(cells.keys())[:10]

    # At least one key must start with a quote character — if this assertion fails,
    # the liberty parser was fixed upstream and the cordic-UNKNOWN bug may be resolved.
    quoted_keys = [k for k in sample_keys if k.startswith('"')]
    assert len(quoted_keys) > 0, (
        "No quoted keys found in sky130hd db['cells'] — liberty parser may have "
        "been fixed; re-evaluate the cordic-UNKNOWN root cause and update Task 13 plan."
    )

    # The quoted key does NOT match the unquoted master.upper() form
    first_quoted = quoted_keys[0]
    unquoted_form = first_quoted.strip('"')
    sc_map = cell_types.build_runtime_map(db, sc_lib_paths=[lib])
    # The quoted key IS in the map
    assert first_quoted in sc_map, \
        f"Quoted key {first_quoted!r} should be in the runtime map"
    # The unquoted form is NOT in the map (unless UNKNOWN)
    assert unquoted_form not in sc_map or unquoted_form == "UNKNOWN", \
        f"Unquoted form {unquoted_form!r} unexpectedly found in runtime map — root cause changed"
