"""Synthetic corner-case platform + design fixture for end-to-end RTL->Graph
dataset verification (2026-07-06 nangate45 verification round).

This is a *hand-computable* miniature of a nangate45-style flow that deliberately
packs the extraction pipeline's known-tricky paths into one tiny design, so the
whole feature -> label -> graph chain can be driven and asserted against
independently hand-derived ground truth. It complements
``tools/verify_graph_dataset.py`` (which cross-checks a REAL built dataset against
the raw liberty/LEF/DEF) by exercising corner cases the real nangate45 designs do
not contain, and it complements the per-module unit tests by proving the pieces
compose correctly through the real worker entry points.

Corner cases encoded (see test_corner_case_pipeline.py for the assertions):

  cell_type_id     deterministic sorted std-cell ids + a shared MACRO id for the
                   per-design macro lib (SRAM_8x4), UNKNOWN reserved between them.
  driver/sink      chip INPUT port drives / OUTPUT port sinks; a real gate output
                   drives; a 2-driver net (mux Z + sram rd_out[0]); bus-pin
                   direction resolution (addr_in[3] input, rd_out[0] output).
  connects_macro   set only on nets touching the macro-lib master.
  num_layer        a net routed over metal1+metal2+metal3 -> 3; single-layer -> 1.
  pin_type_id      clock (CK), select-on-INPUT (mux S -> 10) vs select-on-OUTPUT
                   (fa S -> 4), generic input name buckets A/B/C/other, output.
  net_type_id      clock net -> 3, reset net -> 4, signal -> 0.
  wirelength       Manhattan centerline sum with a RECT patch that must be stripped
                   (n_i2), and *-relative coordinate chains.
  graph topology   clock + reset nets AND their pins excluded (clock tree not in
                   the graph); FILL/TAP cells excluded; undirected symmetric edges;
                   variant node/edge counts.

IMPORTANT: liberty is one-attribute-per-line (the parser uses anchored re.match,
mirroring real Synopsys/OpenROAD liberty). Cramming attributes onto one line
silently drops direction/clock/capacitance — do not "compact" these strings.

Pure data + a subprocess runner; no torch/pandas needed to import this module.
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
import textwrap

# --------------------------------------------------------------------------- #
# Standard-cell liberty                                                        #
# --------------------------------------------------------------------------- #
STD_LIB = textwrap.dedent("""
library (cornerstd) {
  capacitive_load_unit (1, ff);
  nom_voltage : 1.10;
  cell (INV_X1) {
    area : 0.532;
    cell_leakage_power : 1.5;
    pin (A) {
      direction : input;
      capacitance : 1.0;
    }
    pin (ZN) {
      direction : output;
      max_capacitance : 12.0;
      function : "!A";
    }
  }
  cell (NAND2_X1) {
    area : 0.798;
    cell_leakage_power : 2.0;
    pin (A1) {
      direction : input;
      capacitance : 1.1;
    }
    pin (A2) {
      direction : input;
      capacitance : 1.2;
    }
    pin (ZN) {
      direction : output;
      max_capacitance : 15.0;
    }
  }
  cell (DFF_X1) {
    area : 4.523;
    cell_leakage_power : 5.0;
    ff (IQ, IQN) {
      clocked_on : CK;
      next_state : D;
    }
    pin (D) {
      direction : input;
      capacitance : 1.5;
    }
    pin (CK) {
      direction : input;
      clock : true;
      capacitance : 2.0;
    }
    pin (Q) {
      direction : output;
      max_capacitance : 8.0;
    }
  }
  cell (FA_X1) {
    area : 4.891;
    cell_leakage_power : 6.0;
    pin (A) {
      direction : input;
      capacitance : 1.3;
    }
    pin (B) {
      direction : input;
      capacitance : 1.3;
    }
    pin (CI) {
      direction : input;
      capacitance : 1.4;
    }
    pin (S) {
      direction : output;
      max_capacitance : 10.0;
    }
    pin (CO) {
      direction : output;
      max_capacitance : 10.0;
    }
  }
  cell (MUX2_X1) {
    area : 1.064;
    cell_leakage_power : 3.0;
    pin (A) {
      direction : input;
      capacitance : 1.0;
    }
    pin (B) {
      direction : input;
      capacitance : 1.0;
    }
    pin (S) {
      direction : input;
      capacitance : 1.0;
    }
    pin (Z) {
      direction : output;
      max_capacitance : 12.0;
    }
  }
  cell (TAPCELL_X1) {
    area : 0.190;
    pin (WELL) {
      direction : input;
    }
  }
  cell (FILLCELL_X1) {
    area : 0.190;
  }
}
""")

# --------------------------------------------------------------------------- #
# Per-design macro liberty (bus pins; SRAM_8x4 resolves to the MACRO id)       #
# --------------------------------------------------------------------------- #
MACRO_LIB = textwrap.dedent("""
library (cornermacro) {
  capacitive_load_unit (1, ff);
  cell (SRAM_8x4) {
    area : 250.0;
    cell_leakage_power : 40.0;
    pin (clk) {
      direction : input;
      capacitance : 3.0;
      clock : true;
    }
    pin (we_in) {
      direction : input;
      capacitance : 2.0;
    }
    bus (addr_in) {
      direction : input;
      capacitance : 2.5;
    }
    bus (rd_out) {
      direction : output;
      capacitance : 1.0;
    }
  }
}
""")

# --------------------------------------------------------------------------- #
# Tech LEF: metal1 H / metal2 V / metal3 H, with CUT layers that must NOT be   #
# counted as routing layers.                                                   #
# --------------------------------------------------------------------------- #
TECH_LEF = textwrap.dedent("""
LAYER metal1
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 0.20 0.20 ;
END metal1
LAYER via1
  TYPE CUT ;
END via1
LAYER metal2
  TYPE ROUTING ;
  DIRECTION VERTICAL ;
  PITCH 0.20 0.20 ;
END metal2
LAYER via2
  TYPE CUT ;
END via2
LAYER metal3
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 0.20 0.20 ;
END metal3
""")

SDC = textwrap.dedent("""
set clk_period 2.0
create_clock -period $clk_period [get_ports clk_i]
""")

# DBU 1000. Clean connectivity — every gate pin appears on exactly one net.
DEF = textwrap.dedent("""
VERSION 5.8 ;
DESIGN corner_top ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 20000 20000 ) ;
GCELLGRID X 0 DO 11 STEP 2000 ;
GCELLGRID Y 0 DO 11 STEP 2000 ;
COMPONENTS 8 ;
- i_inv INV_X1 + PLACED ( 1000 1000 ) N ;
- i_nand NAND2_X1 + PLACED ( 3000 1000 ) S ;
- i_dff DFF_X1 + PLACED ( 5000 1000 ) FN ;
- i_fa FA_X1 + PLACED ( 1000 5000 ) FS ;
- i_mux MUX2_X1 + PLACED ( 3000 5000 ) N ;
- i_tap TAPCELL_X1 + FIXED ( 500 500 ) N ;
- i_fill FILLCELL_X1 + PLACED ( 9000 9000 ) N ;
- i_sram SRAM_8x4 + PLACED ( 12000 12000 ) N ;
END COMPONENTS
PINS 4 ;
- clk_i + NET clk_net + DIRECTION INPUT + USE SIGNAL
  + LAYER metal1 ( -35 -35 ) ( 35 35 )
  + PLACED ( 0 10000 ) N ;
- din_i + NET n_din + DIRECTION INPUT + USE SIGNAL
  + LAYER metal1 ( -35 -35 ) ( 35 35 )
  + PLACED ( 0 5000 ) N ;
- rstn_i + NET rstn_net + DIRECTION INPUT + USE SIGNAL
  + LAYER metal1 ( -35 -35 ) ( 35 35 )
  + PLACED ( 0 15000 ) N ;
- dout_o + NET n_dout + DIRECTION OUTPUT + USE SIGNAL
  + LAYER metal1 ( -35 -35 ) ( 35 35 )
  + PLACED ( 20000 5000 ) N ;
END PINS
NETS 9 ;
- clk_net ( PIN clk_i ) ( i_dff CK ) ( i_sram clk ) + USE SIGNAL
  + ROUTED metal1 ( 0 10000 ) ( 5000 10000 ) ;
- n_din ( PIN din_i ) ( i_inv A ) ( i_fa CI ) + USE SIGNAL
  + ROUTED metal1 ( 0 5000 ) ( 1000 5000 ) ;
- n_i1 ( i_inv ZN ) ( i_nand A1 ) ( i_mux A ) + USE SIGNAL
  + ROUTED metal1 ( 1000 1000 ) ( 3000 1000 )
    NEW metal2 ( 3000 1000 ) ( 3000 5000 )
    NEW metal3 ( 1000 5000 ) ( 3000 5000 ) ;
- n_i2 ( i_nand ZN ) ( i_fa A ) ( i_dff D ) + USE SIGNAL
  + ROUTED metal1 ( 3000 1000 ) ( 1000 1000 )
    RECT ( -50 -50 50 50 )
    NEW metal1 ( 1000 1000 ) ( 1000 5000 ) ;
- rstn_net ( PIN rstn_i ) ( i_mux S ) + USE SIGNAL
  + ROUTED metal1 ( 0 15000 ) ( 3000 15000 ) ;
- n_q ( i_dff Q ) ( i_fa B ) ( i_mux B ) + USE SIGNAL
  + ROUTED metal1 ( 5000 1000 ) ( 3000 5000 ) ;
- n_nand2 ( i_fa CO ) ( i_nand A2 ) + USE SIGNAL
  + ROUTED metal1 ( 1000 5000 ) ( 3000 1000 ) ;
- n_mac ( i_fa S ) ( i_sram addr_in[3] ) + USE SIGNAL
  + ROUTED metal2 ( 1000 5000 ) ( 12000 12000 ) ;
- n_dout ( i_mux Z ) ( i_sram rd_out[0] ) ( PIN dout_o ) + USE SIGNAL
  + ROUTED metal1 ( 3000 5000 ) ( 20000 5000 ) ;
END NETS
END DESIGN
""")

DESIGN = "corner_top"

_SKILL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_EXTRACT = os.path.join(_SKILL, "scripts", "extract")
_FEAT = os.path.join(_EXTRACT, "features")
_LAB = os.path.join(_EXTRACT, "labels")
_GRAPH = os.path.join(_EXTRACT, "graph")

FEATURE_WORKERS = [
    "nodes_gate", "nodes_net", "nodes_pin", "nodes_iopin",
    "edges_gate_pin", "edges_pin_net", "edges_iopin_net", "metadata",
]


def _write_platform(workdir):
    paths = {}
    for name, txt in [("std.lib", STD_LIB), ("macro.lib", MACRO_LIB),
                      ("tech.lef", TECH_LEF), ("corner.sdc", SDC), ("corner.def", DEF)]:
        p = os.path.join(workdir, name)
        with open(p, "w") as f:
            f.write(txt)
        paths[name] = p
    return paths


def _env(paths):
    env = dict(os.environ)
    env["R2G_LIB_FILES"] = f"{paths['std.lib']} {paths['macro.lib']}"
    env["R2G_SC_LIB_FILES"] = paths["std.lib"]
    env["R2G_TECH_LEF"] = paths["tech.lef"]
    env["TECH_LEF"] = paths["tech.lef"]  # extract_congestion reads TECH_LEF, not R2G_TECH_LEF
    env["R2G_PLATFORM"] = "nangate45"
    env["R2G_SDC"] = paths["corner.sdc"]
    env["PYTHONPATH"] = _EXTRACT + os.pathsep + env.get("PYTHONPATH", "")
    for v in ("R2G_SPEF", "R2G_CONFIG", "R2G_DEF"):
        env.pop(v, None)
    return env


def _read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def build(workdir, with_graph=True, variants="bcdef"):
    """Write the fixture, run every real worker + label extractor (+ graph
    builder), and return parsed outputs.

    Runs workers as subprocesses of ``sys.executable`` — the exact production
    invocation contract (argv + env), which also validates the sys.path bootstrap
    and avoids in-process global-state leakage between workers.

    Returns a dict with keys: ``def_path``, ``features`` (dir), ``labels`` (dir),
    ``dataset`` (dir), one entry per feature/label CSV name -> list[dict rows],
    and (if ``with_graph``) ``manifest`` -> parsed graph_manifest.json.
    """
    paths = _write_platform(workdir)
    env = _env(paths)
    deff = paths["corner.def"]
    featdir = os.path.join(workdir, "features"); os.makedirs(featdir, exist_ok=True)
    labdir = os.path.join(workdir, "labels"); os.makedirs(labdir, exist_ok=True)
    out = {"def_path": deff, "features": featdir, "labels": labdir}

    def _run(script, args):
        r = subprocess.run([sys.executable, script] + args, env=env,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"{os.path.basename(script)} failed:\n{r.stdout}\n{r.stderr}")
        return r

    for w in FEATURE_WORKERS:
        csv_path = os.path.join(featdir, f"{w}.csv")
        _run(os.path.join(_FEAT, f"{w}.py"), [deff, csv_path, DESIGN])
        out[f"{w}.csv"] = _read_csv(csv_path)

    _run(os.path.join(_LAB, "extract_wirelength.py"),
         [deff, os.path.join(labdir, "wirelength.csv"), DESIGN])
    _run(os.path.join(_LAB, "extract_congestion.py"),
         [deff, os.path.join(labdir, "cell_congestion.csv"), DESIGN])
    out["wirelength.csv"] = _read_csv(os.path.join(labdir, "wirelength.csv"))
    out["cell_congestion.csv"] = _read_csv(os.path.join(labdir, "cell_congestion.csv"))
    # The graph builder joins four label files; stub the two we don't extract here
    # so it exercises the (correct) label-gap warning path without erroring.
    for fn, hdr in [("ir_drop.csv", "Design,Cell,label"),
                    ("timing_features.csv", "Design,Cell,Pin,label")]:
        with open(os.path.join(labdir, fn), "w") as f:
            f.write(hdr + "\n")

    if with_graph:
        import json
        dsdir = os.path.join(workdir, "dataset"); os.makedirs(dsdir, exist_ok=True)
        _run(os.path.join(_GRAPH, "build_graphs.py"),
             ["--features", featdir, "--labels", labdir, "--design", DESIGN,
              "--out-dir", dsdir, "--variants", variants,
              "--platform", "nangate45"])   # manifest provenance stamp (#30)
        out["dataset"] = dsdir
        with open(os.path.join(dsdir, "graph_manifest.json")) as f:
            out["manifest"] = json.load(f)
    return out


def rows_by(rows, key):
    """Index a list of csv dict rows by a single column value."""
    return {r[key]: r for r in rows}
