"""Master cell name -> integer ``cell_type_id`` (a categorical node feature).

Moved from ``features/cell_type_map.py`` and promoted to the consolidated
``techlib`` package as the single source of truth for cell-type ID assignment.
Feature workers and label extractors will import from here once the switchover
(Task 7) is complete; ``features/cell_type_map.py`` is retained unchanged until
that task to keep the golden-regression gate green. No executable line was
changed — only this docstring was updated.

``cell_type_id`` is categorical, so the IDs only need to be deterministic + distinct
*within a platform's dataset*, not aligned across platforms. Accordingly:

  * **nangate45** uses the curated ``NANGATE45_CELL_TYPE_MAPPING`` below (function family +
    drive strength, ``UNKNOWN`` = 95) — kept verbatim from the original so its IDs are
    preserved byte-for-byte. The eight ``FAKERAM45_*`` keys are upper-cased here so they
    actually match the ``master.upper()`` lookup (the original left them lowercase, so
    macro cells silently fell through to ``UNKNOWN``).
  * **any other platform** gets a deterministic map built at runtime from the resolved
    liberty's cell names (sorted, then assigned 0..N-1, with ``UNKNOWN`` = N).

Pure stdlib.
"""
import sys

NANGATE45_CELL_TYPE_MAPPING = {
    "INV_X1": 0,
    "INV_X2": 1,
    "INV_X4": 2,
    "INV_X8": 3,
    "INV_X16": 4,
    "INV_X32": 5,
    "BUF_X1": 6,
    "BUF_X2": 7,
    "BUF_X4": 8,
    "BUF_X8": 9,
    "BUF_X16": 10,
    "BUF_X32": 11,
    "CLKBUF_X1": 12,
    "CLKBUF_X2": 13,
    "CLKBUF_X3": 14,
    "NAND2_X1": 15,
    "NAND2_X2": 16,
    "NAND2_X4": 17,
    "NAND3_X1": 18,
    "NAND3_X2": 19,
    "NAND3_X4": 20,
    "NAND4_X1": 21,
    "NAND4_X2": 22,
    "NAND4_X4": 23,
    "NOR2_X1": 24,
    "NOR2_X2": 25,
    "NOR2_X4": 26,
    "NOR3_X1": 27,
    "NOR3_X2": 28,
    "NOR3_X4": 29,
    "NOR4_X1": 30,
    "NOR4_X2": 31,
    "NOR4_X4": 32,
    "AND2_X1": 33,
    "AND2_X2": 34,
    "AND2_X4": 35,
    "AND3_X1": 36,
    "AND3_X2": 37,
    "AND3_X4": 38,
    "AND4_X1": 39,
    "AND4_X2": 40,
    "AND4_X4": 41,
    "OR2_X1": 42,
    "OR2_X2": 43,
    "OR2_X4": 44,
    "OR3_X1": 45,
    "OR3_X2": 46,
    "OR3_X4": 47,
    "OR4_X1": 48,
    "OR4_X2": 49,
    "OR4_X4": 50,
    "XOR2_X1": 51,
    "XOR2_X2": 52,
    "XNOR2_X1": 53,
    "XNOR2_X2": 54,
    "AOI21_X1": 55,
    "AOI21_X2": 56,
    "AOI21_X4": 57,
    "AOI22_X1": 58,
    "AOI22_X2": 59,
    "AOI22_X4": 60,
    "OAI21_X1": 61,
    "OAI21_X2": 62,
    "OAI21_X4": 63,
    "OAI22_X1": 64,
    "OAI22_X2": 65,
    "OAI22_X4": 66,
    "MUX2_X1": 67,
    "MUX2_X2": 68,
    "FA_X1": 69,
    "HA_X1": 70,
    "DFF_X1": 71,
    "DFF_X2": 72,
    "DFFR_X1": 73,
    "DFFR_X2": 74,
    "DFFS_X1": 75,
    "DFFS_X2": 76,
    "DFFSR_X1": 77,
    "DFFSR_X2": 78,
    "TBUF_X1": 79,
    "TBUF_X2": 80,
    "TBUF_X4": 81,
    "TBUF_X8": 82,
    "TBUF_X16": 83,
    "TINV_X1": 84,
    "TINV_X2": 85,
    "FILLCELL_X1": 86,
    "FILLCELL_X2": 87,
    "FILLCELL_X4": 88,
    "FILLCELL_X8": 89,
    "FILLCELL_X16": 90,
    "FILLCELL_X32": 91,
    "ANTENNA_X1": 92,
    "LOGIC0_X1": 93,
    "LOGIC1_X1": 94,
    "AOI211_X2": 96,
    "AOI211_X4": 97,
    "AOI221_X1": 98,
    "AOI221_X2": 99,
    "AOI221_X4": 100,
    "AOI222_X2": 101,
    "AOI222_X4": 102,
    "OAI211_X2": 103,
    "OAI211_X4": 104,
    "OAI221_X1": 105,
    "OAI221_X2": 106,
    "OAI221_X4": 107,
    "OAI222_X2": 108,
    "OAI33_X1": 109,
    "DLL_X1": 110,
    "DLL_X2": 111,
    "TAPCELL_X1": 112,
    "FAKERAM45_512X64": 113,
    "FAKERAM45_64X96": 114,
    "FAKERAM45_256X32": 115,
    "FAKERAM45_32X64": 116,
    "FAKERAM45_64X32": 117,
    "FAKERAM45_256X96": 118,
    "FAKERAM45_64X15": 119,
    "FAKERAM45_64X7": 120,
    "DFFRS_X1": 121,
    "DFFRS_X2": 122,
    "DLH_X1": 123,
    "DLH_X2": 124,
    "DLHR_X1": 125,
    "DLHR_X2": 126,
    "DLHS_X1": 127,
    "DLHS_X2": 128,
    "UNKNOWN": 95,
}

# Back-compat alias for callers that imported the original symbol name.
COMPLETE_CELL_TYPE_MAPPING = NANGATE45_CELL_TYPE_MAPPING


def _normalize_paths(paths):
    if not paths:
        return None
    if isinstance(paths, str):
        paths = [t for t in paths.replace(":", " ").split() if t]
    return set(paths) or None


def build_runtime_map(lib_db, sc_lib_paths=None):
    """Deterministic ``{UPPER_CELL_NAME: id}`` from the resolved liberty cell list.

    Cell names are sorted then assigned ``0..N-1``; ``UNKNOWN`` maps to ``N``. Keys are
    already upper-cased (``lib_db['cells']`` is keyed on the normalized name), matching the
    ``master.upper()`` lookup the workers perform.

    When ``sc_lib_paths`` is given, only cells whose ``source_lib`` is in that set are
    enumerated — so the id space is the platform's STANDARD-CELL vocabulary, which is
    identical across every design of a platform. Per-design macro libs
    (``ADDITIONAL_LIBS``) are therefore excluded and resolve to ``UNKNOWN``, keeping the
    categorical ``cell_type_id`` stable across a multi-design dataset.
    """
    cells = lib_db.get("cells", {})
    sc = _normalize_paths(sc_lib_paths)
    if sc is not None:
        names = sorted(k for k, v in cells.items() if v.get("source_lib") in sc)
    else:
        names = sorted(cells.keys())
    if not names:
        sys.stderr.write(
            "WARN: cell-type map has no cells (empty/unresolved liberty) — every "
            "cell_type_id will be UNKNOWN\n"
        )
    mapping = {name: idx for idx, name in enumerate(names)}
    mapping["UNKNOWN"] = len(names)
    return mapping


def resolve_cell_type_map(platform, lib_db, sc_lib_paths=None):
    """Select the curated nangate45 map or a liberty-derived map for other platforms."""
    if (platform or "nangate45").lower() == "nangate45":
        return NANGATE45_CELL_TYPE_MAPPING
    return build_runtime_map(lib_db, sc_lib_paths)


def cell_type_id(master, mapping):
    """Look up a master's id, falling back to the map's ``UNKNOWN`` (95 if absent)."""
    return mapping.get((master or "").strip().upper(), mapping.get("UNKNOWN", 95))
