"""Path / context resolution for the feature-extraction workers.

Each worker is driven by ``run_features.sh`` with an explicit positional contract:

    python3 <worker>.py <DEF> <out_csv> <graph_id> [extra ...]

plus optional environment overrides:

    R2G_SDC        design SDC (clock-port detection)            [optional]
    R2G_SPEF       6_final.spef (cap features)                  [optional]
    R2G_CONFIG     config.mk (PLACE_DENSITY/CORE_UTILIZATION..) [optional]
    R2G_LIB_FILES  space/colon-separated liberty paths          [optional]
    R2G_TECH_LEF   tech LEF (routing-layer names)               [optional]
    R2G_PLATFORM   ORFS platform name (cell-type-map selection) [default nangate45]

This replaces the original ``feature_test_v2/input/<case>/`` layout: in the skill the
inputs live under ``design_cases/<design>/`` and are resolved by ``run_features.sh``,
which passes them in here. Workers stay independently runnable + unit-testable because
every input is an explicit argument or env var.
"""
import os
import sys

# Runtime sys.path bootstrap: workers are launched by run_features.sh as
# ``python3 <features>/<worker>.py ...``, so sys.path[0] is this features/ dir
# and the consolidated ``techlib`` package (one level up, under scripts/extract/)
# would not import. Every worker imports case_paths before techlib, so inserting
# scripts/extract/ here makes ``import techlib.*`` resolve in production runs too
# (pytest already adds it via conftest.py). Guarded against duplicate insert.
_EXTRACT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _EXTRACT_DIR not in sys.path:
    sys.path.insert(0, _EXTRACT_DIR)


def _env(name, default=""):
    val = os.environ.get(name)
    return val if val is not None else default


def split_lib_files(raw):
    """Split a space/colon-separated liberty list into individual path tokens."""
    if not raw:
        return []
    return [t for t in raw.replace(":", " ").split() if t]


def resolve_case_paths(script_file, default_output_name):
    """Build the worker context dict from argv + environment.

    Returns the same keys the original feature_test_v2 workers consumed
    (``def_path``, ``out_csv``, ``graph_id``, ``sdc_path``, ``spef_path``,
    ``config_path``), plus ``lib_files`` / ``tech_lef`` / ``platform`` used by the
    platform-aware refactor.
    """
    argv = sys.argv
    script_name = os.path.basename(script_file)

    def_path = ""
    if len(argv) > 1 and not argv[1].startswith("-"):
        def_path = argv[1]
    else:
        def_path = _env("R2G_DEF", _env("DEF_FILE"))
    if not def_path:
        print(f"usage: python {script_name} <DEF> <out_csv> <graph_id>  "
              f"(or set R2G_DEF / DEF_FILE)")
        sys.exit(1)

    out_csv = argv[2] if len(argv) > 2 else _env("R2G_OUT_CSV", default_output_name)
    graph_id = argv[3] if len(argv) > 3 else _env("R2G_GRAPH_ID", "design")

    out_dir = os.path.dirname(os.path.abspath(out_csv))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    return {
        "script_dir": os.path.dirname(os.path.abspath(script_file)),
        "graph_id": graph_id,
        "def_path": def_path,
        "out_csv": out_csv,
        "sdc_path": _env("R2G_SDC"),
        "spef_path": _env("R2G_SPEF"),
        "config_path": _env("R2G_CONFIG"),
        "lib_files": split_lib_files(_env("R2G_LIB_FILES")),
        # Standard-cell liberty only (defaults to the full list) — the cell-type map is
        # built from this so per-design macro libs don't reshuffle std-cell ids.
        "sc_lib_files": split_lib_files(_env("R2G_SC_LIB_FILES", _env("R2G_LIB_FILES"))),
        "tech_lef": _env("R2G_TECH_LEF"),
        "platform": _env("R2G_PLATFORM", "asap7"),
        # Extra positional overrides (place_density, core_util, ...) start at argv[4];
        # metadata.py falls back to config.mk for each when they are not supplied.
        "extra_arg_start": 4,
    }
