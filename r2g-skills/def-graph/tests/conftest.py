"""Shared pytest fixtures for the def-graph (dataset-conversion) tests.

def-graph is self-contained: the graph extractors (techlib / labels / features / graph)
import each other via `scripts/extract/` as the common package root, exactly as the
runtime shell runners set it up. This conftest reproduces that sys.path so the tests
import the workers as plain modules.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]

# scripts/extract/ first so `import techlib.def_parse` (the consolidated DEF/SDC parser
# package) resolves; then the per-stage worker dirs so `import compute_feature_stats`,
# `import extract_congestion`, `import build_graphs`, `import graph_lib` etc. resolve.
for _sub in (
    SKILL_ROOT / "scripts" / "extract",
    SKILL_ROOT / "scripts" / "extract" / "labels",
    SKILL_ROOT / "scripts" / "extract" / "features",
    SKILL_ROOT / "scripts" / "extract" / "graph",
):
    if str(_sub) not in sys.path:
        sys.path.insert(0, str(_sub))


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


def write_csv(path, header, rows):
    """Tiny CSV writer shared by the mini-design fixture and its consumers."""
    import csv
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture()
def mini_csvs(tmp_path):
    """A minimal but complete feature+label CSV pair for the graph stage.

    Lives here (not in one test module) because more than one suite builds real
    graphs from it — test_graph_stage.py for tensor/view semantics and
    test_graph_generation_identity.py for publication atomicity. Same one-copy
    rule the techlib parser follows.

    Deliberate gaps the consumers assert on: g2 has no congestion label, f1 is a
    FILLCELL (filtered), nclk is a clock net (filtered), ir_drop has a duplicate
    Cell row (max-reduced).
    """
    import graph_lib as gl

    feat = tmp_path / "features"
    lab = tmp_path / "labels"
    feat.mkdir()
    lab.mkdir()
    g = "mini"

    write_csv(feat / "nodes_gate.csv",
              ["graph_id", "inst_name", "master", *gl.GATE_SCHEMA],
              [[g, "g1", "INV_X1", 0, 1.0, 2.0, 10.0, 20.0, 0, 0],
               [g, "g2", "INV_X2", 1, 1.5, 2.5, 30.0, 40.0, 0, 0],
               [g, "f1", "FILLCELL_X1", 86, 1.0, 0.0, 50.0, 60.0, 0, 0]])
    write_csv(feat / "nodes_net.csv",
              ["graph_id", "net_name", *gl.NET_SCHEMA],
              [[g, "n1", 0, 2, 3, 1, 2, 0, 2, 12.5],
               [g, "nclk", 3, 5, 6, 1, 5, 0, 3, 99.0]])
    write_csv(feat / "nodes_iopin.csv",
              ["graph_id", "iopin_name", "net_name", "net_type_id", *gl.IOPIN_SCHEMA],
              [[g, "in_port", "n1", 0, 0.0, 5.0, 1.0, 0],
               [g, "clk", "nclk", 3, 0.0, 9.0, 1.0, 0]])
    write_csv(feat / "nodes_pin.csv",
              ["graph_id", "inst_name", "pin_name", *gl.PIN_SCHEMA],
              [[g, "g1", "A", 0, 1.5],
               [g, "g1", "ZN", 4, 1.5],
               [g, "g2", "A", 0, 1.5],
               [g, "g2", "CK", 5, 0.5]])
    write_csv(feat / "edges_gate_pin.csv",
              ["graph_id", "inst_name", "pin_name"],
              [[g, "g1", "A"], [g, "g1", "ZN"], [g, "g2", "A"], [g, "g2", "CK"]])
    write_csv(feat / "edges_pin_net.csv",
              ["graph_id", "inst_name", "pin_name", "net_name", "net_type_id"],
              [[g, "g1", "ZN", "n1", 0], [g, "g2", "A", "n1", 0],
               [g, "g1", "A", "n1", 0],
               [g, "g2", "CK", "nclk", 3]])
    write_csv(feat / "edges_iopin_net.csv",
              ["graph_id", "iopin_name", "net_name", "net_type_id"],
              [[g, "in_port", "n1", 0], [g, "clk", "nclk", 3]])
    write_csv(feat / "metadata.csv",
              ["graph_id", *gl.METADATA_SCHEMA],
              [[g, 3, 2, 2, 1.5, 100.0, 100.0, 10000.0, 1000, 0.55, 40, 0, 5.0,
                "met1:100", 1.8, 100000000]])

    write_csv(lab / "cell_congestion.csv",
              ["Design", "Cell", "cell_type", "cell_congestion", "label"],
              [[g, "g1", "INV_X1", 0.04, 0.2]])  # g2 missing -> NaN
    write_csv(lab / "ir_drop.csv",
              ["Design", "Cell", "X", "Y", "Voltage_V", "IR_Drop_mV", "P95_mV",
               "label", "has_irdrop"],
              [[g, "g1", 1, 2, 1.79, 10.0, 9.0, 0.7, "true"],
               [g, "g1", 1, 2, 1.78, 20.0, 9.0, 0.9, "true"]])  # dup Cell -> max
    write_csv(lab / "timing_features.csv",
              ["Design", "Cell", "Cell_Slack_ns", "Path_Delay_ns", "label", "in_sta_path"],
              [[g, "g1", 5.0, 5.0, 1.79, "true"], [g, "g2", 8.0, 2.0, 1.10, "true"]])
    write_csv(lab / "wirelength.csv",
              ["Design", "Net", "NetType", "WireLength_um", "label", "mask_wl"],
              [[g, "n1", "SIGNAL", 12.5, 2.6, "true"]])
    return str(feat), str(lab)


# Machine pin isolation (2026-09-23). Every campaign worktree carries
# references/env.local.sh pins, and _env.sh sources them; a test that builds its
# own environment (a fake ORFS, a staged PDK, NUM_CORES) then silently ran against
# the machine's toolchain instead -- 13 tests failed only in pinned worktrees.
# Skip the skill pin file and drop the variables such pins (or a login shell that
# sourced them) export. $R2G_ENV_FILE-based tests still set their own file.
PINNED_ENV_VARS = (
    "R2G_ENV_FILE", "ORFS_ROOT", "FLOW_DIR", "OPENROAD_EXE", "YOSYS_EXE",
    "IVERILOG_EXE", "VVP_EXE", "VERILATOR_EXE", "KLAYOUT_CMD", "STA_EXE",
    "MAGIC_EXE", "NETGEN_EXE", "PDK_ROOT", "SKY130A_DIR", "R2G_GRAPH_PYTHON",
    "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
)


# Applied at conftest import, before any test module is imported, so module-level
# probes (e.g. a skipif computed from the resolved toolchain) see the same
# isolated environment as the tests.
os.environ["R2G_IGNORE_ENV_LOCAL"] = "1"
for _name in PINNED_ENV_VARS:
    os.environ.pop(_name, None)
