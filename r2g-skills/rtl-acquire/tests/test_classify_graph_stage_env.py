"""A graph-stage environment fault must never become a source-quality verdict.

Regression (wave-3 E9, 2026-09-22): the graph venv died on every design with
"No module named 'encodings'" (a PYTHONHOME leak) and had no torch either.
`classify_failed_candidates` then wrote all 69 cleanly synthesised designs into
failed_candidates_exclude.csv as `exclude / low_value_failure`, and discovery
never sees an excluded source again. A graph-stage row reached the classifier
only because it is not `success`; nothing in it says anything about the RTL.
"""
from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_SCRIPTS / "repair"))

from classify_failed_candidates import classify  # noqa: E402

# Verbatim tail of an E9 graph_failed row (acquire_e9/corpus/index.csv).
ENCODINGS_LEAK = (
    "    '/opt/OpenROAD/oss-cad-suite/lib/python3.10/lib-dynload',\n  ]\n"
    "Fatal Python error: init_fs_encoding: failed to get the Python codec of the "
    "filesystem encoding\nPython runtime state: core initialized\n"
    "ModuleNotFoundError: No module named 'encodings'\n"
)
NO_TORCH = (
    "Traceback (most recent call last):\n"
    '  File "netlist_graph.py", line 12, in <module>\n'
    "    import torch\nModuleNotFoundError: No module named 'torch'\n"
)
GRAPH_PYTHON_MISSING = (
    "HINT: R2G_GRAPH_PYTHON='/nope/python' is not a usable executable "
    "(toolchain_graph_python_missing) — provision the torch venv with eda-install "
    "or fix the path. The design is recorded as graph_skipped, NOT a design failure."
)


def test_graph_python_import_failures_are_deferred() -> None:
    for notes in (ENCODINGS_LEAK, NO_TORCH):
        assert classify("/c/rtl/top.v", notes, status="graph_failed") == (
            "defer", "graph_tool_unavailable")


def test_graph_skipped_is_deferred() -> None:
    assert classify("/c/rtl/top.v", GRAPH_PYTHON_MISSING, status="graph_skipped") == (
        "defer", "graph_tool_unavailable")


def test_unrecognised_graph_failure_is_still_not_an_exclusion() -> None:
    # The design synthesised; a graph-converter crash is our tool's problem.
    bucket, reason = classify("/c/rtl/top.v", "KeyError: 'sky130_fd_sc_hd__xyz'",
                              status="graph_failed")
    assert (bucket, reason) == ("defer", "graph_stage_failure")


def test_synth_failures_are_unchanged() -> None:
    # "No module named" in a SYNTH log is not a graph-stage fault: keep the old path.
    assert classify("/c/rtl/top.v", "syntax error, unexpected ';'",
                    status="synth_failed") == ("exclude", "low_value_failure")
    assert classify("/c/rtl/top.v", "syntax error, unexpected ';'") == (
        "exclude", "low_value_failure")


def test_main_routes_graph_rows_to_defer_not_exclude(tmp_path: Path) -> None:
    index = tmp_path / "index.csv"
    with index.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["design", "status", "source_path", "top", "notes"])
        w.writeheader()
        w.writerow({"design": "a", "status": "graph_failed", "source_path": "/c/a.v",
                    "top": "a", "notes": ENCODINGS_LEAK})
        w.writerow({"design": "b", "status": "synth_failed", "source_path": "/c/b.v",
                    "top": "b", "notes": "syntax error, unexpected ';'"})
    out = {k: tmp_path / f"{k}.csv" for k in ("retry", "exclude", "rc", "defer")}
    subprocess.run(
        [sys.executable, str(_SCRIPTS / "repair" / "classify_failed_candidates.py"),
         "--index", str(index), "--out-retry", str(out["retry"]),
         "--out-exclude", str(out["exclude"]), "--out-retry-candidates", str(out["rc"]),
         "--out-defer", str(out["defer"])],
        check=True, capture_output=True, text=True)
    excluded = [r["design"] for r in csv.DictReader(out["exclude"].open())]
    deferred = [(r["design"], r["reason"]) for r in csv.DictReader(out["defer"].open())]
    assert excluded == ["b"]
    assert deferred == [("a", "graph_tool_unavailable")]
