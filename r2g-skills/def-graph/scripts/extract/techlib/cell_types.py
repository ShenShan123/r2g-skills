"""Master cell name -> integer ``cell_type_id`` (a categorical node feature).

Moved from ``features/cell_type_map.py`` and promoted to the consolidated
``techlib`` package as the single source of truth for cell-type ID assignment.
Feature workers and label extractors will import from here once the switchover
(Task 7) is complete; ``features/cell_type_map.py`` is retained unchanged until
that task to keep the golden-regression gate green. No executable line was
changed — only this docstring was updated.

``cell_type_id`` is categorical, so the IDs only need to be deterministic + distinct
*within a platform's dataset*, not aligned across platforms. **Every platform** gets a
deterministic map built at runtime from the resolved liberty's cell names (sorted, then
assigned 0..N-1, ``UNKNOWN`` = N, ``MACRO`` = N+1 shared by all per-design macro cells).

nangate45 previously used a curated ``NANGATE45_CELL_TYPE_MAPPING`` dict — retired
2026-07-06: the frozen map had drifted against the deployed liberty (22 real masters
missing → silently aliased onto UNKNOWN=95, incl. every SDFF*/CLKGATE*/TLAT cell; 4
stale keys — DFFSR/DLHR/DLHS/TINV_X2 — no longer in the liberty; 8 of 23 fakeram
sizes). The runtime map self-heals from the platform liberty like every other platform.
The dict lived on briefly as an import-compat shim and was deleted outright 2026-07-09
(house-cleaning; no in-repo consumer remained). Any nangate45 dataset built against it
must be regenerated. See failure-patterns.md ("Dataset-Extraction Silent-Value
Defects" #12).

Pure stdlib.
"""
import sys


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

    When ``sc_lib_paths`` is given, only cells whose ``source_lib`` is in that set get
    their own ids — so the id space is the platform's STANDARD-CELL vocabulary, which is
    identical across every design of a platform. Cells from per-design macro libs
    (``ADDITIONAL_LIBS``) all share the dedicated ``MACRO`` id (= N+1) instead of
    aliasing onto ``UNKNOWN`` (= N, unchanged so existing non-macro datasets keep their
    UNKNOWN id): a macro is a *known* kind of node, and folding it into UNKNOWN made
    "SRAM" indistinguishable from "unmapped master" (2026-07-06 nangate45 fakeram audit).
    """
    cells = lib_db.get("cells", {})
    sc = _normalize_paths(sc_lib_paths)
    if sc is not None:
        names = sorted(k for k, v in cells.items() if v.get("source_lib") in sc)
        macro_names = [k for k in cells.keys() if cells[k].get("source_lib") not in sc]
    else:
        names = sorted(cells.keys())
        macro_names = []
    if not names:
        sys.stderr.write(
            "WARN: cell-type map has no cells (empty/unresolved liberty) — every "
            "cell_type_id will be UNKNOWN\n"
        )
    mapping = {name: idx for idx, name in enumerate(names)}
    mapping["UNKNOWN"] = len(names)
    macro_id = len(names) + 1
    for name in macro_names:
        mapping[name] = macro_id
    mapping["MACRO"] = macro_id
    return mapping


def resolve_cell_type_map(platform, lib_db, sc_lib_paths=None):
    """Liberty-derived runtime map for EVERY platform.

    nangate45's curated-map special case was retired 2026-07-06 (frozen map had
    drifted: 22 live masters → UNKNOWN; see module docstring). ``platform`` is kept in
    the signature for call-site compatibility and future per-platform strategies.
    """
    del platform  # no per-platform strategy since the curated-map retirement
    return build_runtime_map(lib_db, sc_lib_paths)


def cell_type_id(master, mapping):
    """Look up a master's id, falling back to the map's ``UNKNOWN`` (95 if absent)."""
    return mapping.get((master or "").strip().upper(), mapping.get("UNKNOWN", 95))
