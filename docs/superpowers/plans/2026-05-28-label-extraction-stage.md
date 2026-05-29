# Label-Extraction Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a post-flow "label extraction" stage to the `r2g-rtl2gds` skill that emits per-cell/per-net regression-target CSVs (congestion, wirelength, timing, IR drop) plus a per-design stats JSON, working across all ORFS platforms.

**Architecture:** One fail-soft orchestrator (`run_labels.sh`) mirrors the existing `run_rcx.sh` pattern: it locates the collected `6_final.{odb,def}`, resolves platform liberty/lef/voltage via a Make-eval helper (`resolve_platform_paths.sh`), pulls clock period from the design SDC, then runs four workers into `design_cases/<d>/labels/` and rolls up stats into `reports/labels_stats.json`.

**Tech Stack:** Bash (orchestration), Python 3 stdlib (DEF parsing + stats, no pandas/numpy), Tcl + OpenROAD (`read_db`/STA/PDNSim), pytest (unit tests), GNU Make (platform var resolution via ORFS Makefile).

---

## Source material

The four scripts already exist untracked under `extract_label/` and are migrated (some generalized) into the skill:

| Source (delete at end) | Destination | Change |
|------------------------|-------------|--------|
| `extract_label/congestion/generate_cell_congestion.py` | `r2g-rtl2gds/scripts/extract/labels/extract_congestion.py` | generalize layer parsing |
| `extract_label/wirelength/extract_wirelength.py` | `r2g-rtl2gds/scripts/extract/labels/extract_wirelength.py` | verbatim |
| `extract_label/timing/extract_timing.tcl` | `r2g-rtl2gds/scripts/extract/labels/extract_timing.tcl` | generalize liberty loading |
| `extract_label/irdrop/run_pdnsim.tcl` | `r2g-rtl2gds/scripts/extract/labels/extract_irdrop.tcl` | verbatim (env-driven voltage) |

## File Structure

**Create:**
- `r2g-rtl2gds/scripts/extract/labels/extract_congestion.py` — DEF + tech.lef → per-cell congestion CSV
- `r2g-rtl2gds/scripts/extract/labels/extract_wirelength.py` — DEF → per-net Manhattan WL CSV
- `r2g-rtl2gds/scripts/extract/labels/extract_timing.tcl` — ODB + liberty → per-cell slack/delay CSV
- `r2g-rtl2gds/scripts/extract/labels/extract_irdrop.tcl` — ODB → per-cell IR drop CSV (PDNSim)
- `r2g-rtl2gds/scripts/extract/labels/compute_label_stats.py` — 4 CSVs → `reports/labels_stats.json`
- `r2g-rtl2gds/scripts/flow/resolve_platform_paths.sh` — Make-eval platform resolver (KEY=VALUE on stdout)
- `r2g-rtl2gds/scripts/flow/run_labels.sh` — orchestrator (flow stage)
- `r2g-rtl2gds/tools/run_labels_batch.sh` — subset/full backfill driver
- `r2g-rtl2gds/references/label-extraction.md` — reference doc
- `r2g-rtl2gds/tests/test_extract_congestion.py`
- `r2g-rtl2gds/tests/test_extract_wirelength.py`
- `r2g-rtl2gds/tests/test_compute_label_stats.py`

**Modify:**
- `r2g-rtl2gds/tests/conftest.py` — add labels dir to `sys.path`
- `r2g-rtl2gds/scripts/project/init_project.py:6-7` — add `labels` to `TEMPLATE_DIRS`
- `r2g-rtl2gds/SKILL.md` — add step "13b — Label Extraction"
- `CLAUDE.md` — layout note for `scripts/extract/labels/`, `labels/` output, batch tool

---

## Task 1: Generalize + migrate congestion extractor

**Files:**
- Create: `r2g-rtl2gds/scripts/extract/labels/extract_congestion.py`
- Modify: `r2g-rtl2gds/tests/conftest.py`
- Test: `r2g-rtl2gds/tests/test_extract_congestion.py`

The only logic change vs. the source is `parse_tech_lef`: recognize **any** layer with `TYPE ROUTING` (not just names starting with `metal`), capturing PITCH (per-direction when two values given) and DIRECTION; nangate `DEFAULT_LAYER_INFO` becomes a logged last-resort fallback.

- [ ] **Step 1: Add labels dir to test sys.path**

In `r2g-rtl2gds/tests/conftest.py`, after the `KNOWLEDGE_DIR` block (around line 15), add:

```python
# Make scripts/extract/labels/ importable as plain modules for label-extractor tests.
LABELS_DIR = SKILL_ROOT / "scripts" / "extract" / "labels"
if str(LABELS_DIR) not in sys.path:
    sys.path.insert(0, str(LABELS_DIR))
```

- [ ] **Step 2: Write the failing test**

Create `r2g-rtl2gds/tests/test_extract_congestion.py`:

```python
"""Tests for extract_congestion.py — generic TYPE ROUTING layer parsing."""
from __future__ import annotations

import textwrap

import extract_congestion as ec


def _write(tmp_path, text):
    p = tmp_path / "tech.lef"
    p.write_text(textwrap.dedent(text))
    return str(p)


def test_parses_nangate_metal_layers(tmp_path):
    lef = _write(tmp_path, """
        LAYER metal1
            TYPE ROUTING ;
            DIRECTION HORIZONTAL ;
            PITCH 0.14 ;
        END metal1
        LAYER via1
            TYPE CUT ;
        END via1
        LAYER metal2
            TYPE ROUTING ;
            DIRECTION VERTICAL ;
            PITCH 0.19 ;
        END metal2
    """)
    info = ec.parse_tech_lef(lef)
    assert set(info) == {"metal1", "metal2"}
    assert info["metal1"]["direction"] == "HORIZONTAL"
    assert abs(info["metal1"]["pitch"] - 0.14) < 1e-9
    assert info["metal2"]["direction"] == "VERTICAL"


def test_parses_non_metal_named_routing_layers(tmp_path):
    # sky130-style names (met1/li1) must be recognized via TYPE ROUTING, not name prefix.
    lef = _write(tmp_path, """
        LAYER li1
            TYPE ROUTING ;
            DIRECTION VERTICAL ;
            PITCH 0.34 ;
        END li1
        LAYER mcon
            TYPE CUT ;
        END mcon
        LAYER met1
            TYPE ROUTING ;
            DIRECTION HORIZONTAL ;
            PITCH 0.34 ;
        END met1
    """)
    info = ec.parse_tech_lef(lef)
    assert set(info) == {"li1", "met1"}
    assert info["met1"]["direction"] == "HORIZONTAL"


def test_two_value_pitch_picks_perpendicular_axis(tmp_path):
    # "PITCH x y": HORIZONTAL layer uses y (index 1), VERTICAL uses x (index 0).
    lef = _write(tmp_path, """
        LAYER M1
            TYPE ROUTING ;
            DIRECTION HORIZONTAL ;
            PITCH 0.18 0.20 ;
        END M1
        LAYER M2
            TYPE ROUTING ;
            DIRECTION VERTICAL ;
            PITCH 0.18 0.20 ;
        END M2
    """)
    info = ec.parse_tech_lef(lef)
    assert abs(info["M1"]["pitch"] - 0.20) < 1e-9
    assert abs(info["M2"]["pitch"] - 0.18) < 1e-9


def test_missing_file_returns_default(tmp_path):
    info = ec.parse_tech_lef(str(tmp_path / "nope.lef"))
    assert info == ec.DEFAULT_LAYER_INFO


def test_no_routing_layers_falls_back_to_default(tmp_path):
    lef = _write(tmp_path, """
        LAYER poly
            TYPE MASTERSLICE ;
        END poly
    """)
    info = ec.parse_tech_lef(lef)
    assert info == ec.DEFAULT_LAYER_INFO
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd r2g-rtl2gds && python3 -m pytest tests/test_extract_congestion.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'extract_congestion'`

- [ ] **Step 4: Create the generalized extractor**

Create `r2g-rtl2gds/scripts/extract/labels/extract_congestion.py`. Copy the source file `extract_label/congestion/generate_cell_congestion.py` verbatim, then **replace its `parse_tech_lef` function** (source lines 22-56) with this generalized version (everything else in the file is unchanged):

```python
def parse_tech_lef(tech_lef):
    """Parse routing-layer pitch/direction from a tech LEF.

    Recognizes any layer declared TYPE ROUTING (platform-agnostic — nangate
    metal*, sky130 met*/li1, asap7 M*). Falls back to the nangate
    DEFAULT_LAYER_INFO (with a warning) when the LEF is absent or declares no
    routing layers.
    """
    if not tech_lef or not os.path.exists(tech_lef):
        print(f"WARNING: tech LEF not found ({tech_lef}); using nangate45 DEFAULT_LAYER_INFO")
        return DEFAULT_LAYER_INFO

    layers = {}
    current = None
    block = {}

    def _finalize():
        if block.get("type") == "ROUTING" and block.get("pitch_vals") and block.get("direction"):
            pv = block["pitch_vals"]
            direction = block["direction"]
            if len(pv) >= 2:
                pitch = pv[1] if direction == "HORIZONTAL" else pv[0]
            else:
                pitch = pv[0]
            if pitch > 0:
                layers[current] = {"pitch": pitch, "direction": direction}

    with open(tech_lef, "r") as f:
        for raw_line in f:
            parts = raw_line.replace(";", " ").split()
            if not parts:
                continue
            if parts[0] == "LAYER" and len(parts) >= 2:
                current = parts[1]
                block = {"pitch_vals": [], "direction": None, "type": None}
                continue
            if current is None:
                continue
            if parts[0] == "END":
                _finalize()
                current = None
                block = {}
                continue
            if parts[0] == "TYPE" and len(parts) >= 2:
                block["type"] = parts[1].upper()
            elif parts[0] == "PITCH":
                for tok in parts[1:]:
                    try:
                        block["pitch_vals"].append(float(tok))
                    except ValueError:
                        pass
            elif parts[0] == "DIRECTION" and len(parts) >= 2:
                block["direction"] = parts[1].upper()

    if not layers:
        print("WARNING: no TYPE ROUTING layers parsed; using nangate45 DEFAULT_LAYER_INFO")
        return DEFAULT_LAYER_INFO
    return layers
```

Then make it executable: `chmod +x r2g-rtl2gds/scripts/extract/labels/extract_congestion.py`

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd r2g-rtl2gds && python3 -m pytest tests/test_extract_congestion.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add r2g-rtl2gds/scripts/extract/labels/extract_congestion.py r2g-rtl2gds/tests/test_extract_congestion.py r2g-rtl2gds/tests/conftest.py
git commit -m "feat(skill): add platform-agnostic congestion label extractor"
```

---

## Task 2: Migrate wirelength extractor (verbatim) + test

**Files:**
- Create: `r2g-rtl2gds/scripts/extract/labels/extract_wirelength.py`
- Test: `r2g-rtl2gds/tests/test_extract_wirelength.py`

No logic change — the script is pure DEF Manhattan parsing and already platform-agnostic. Add a regression test that locks behavior (including `*`-relative routing points and `mask_wl`).

- [ ] **Step 1: Write the failing test**

Create `r2g-rtl2gds/tests/test_extract_wirelength.py`:

```python
"""Tests for extract_wirelength.py — DEF Manhattan wirelength + signal mask."""
from __future__ import annotations

import csv
import math
import textwrap

import extract_wirelength as ewl


DEF = """
    DESIGN tiny ;
    UNITS DISTANCE MICRONS 1000 ;
    COMPONENTS 0 ;
    END COMPONENTS
    NETS 2 ;
    - sig_a ( i1 A ) ( i2 Z )
      + ROUTED metal1 ( 0 0 ) ( 1000 0 ) ( 1000 2000 ) ;
    - clk_b ( i3 A )
      + USE CLOCK
      + ROUTED metal1 ( 0 0 ) ( 3000 * ) ;
    END NETS
    END DESIGN
"""


def _run(tmp_path):
    defp = tmp_path / "t.def"
    defp.write_text(textwrap.dedent(DEF))
    out = tmp_path / "wl.csv"
    wl_map, net_types, name = ewl.parse_def_wirelength(str(defp))
    return wl_map, net_types, name


def test_manhattan_length_with_relative_points(tmp_path):
    wl_map, net_types, name = _run(tmp_path)
    assert name == "tiny"
    # sig_a: (0,0)->(1000,0)=1.0um, (1000,0)->(1000,2000)=2.0um => 3.0um
    assert abs(wl_map["sig_a"] - 3.0) < 1e-6
    # clk_b: (0,0)->(3000,*) keeps y=0 => 3.0um
    assert abs(wl_map["clk_b"] - 3.0) < 1e-6


def test_net_types_and_mask(tmp_path):
    wl_map, net_types, name = _run(tmp_path)
    assert net_types["sig_a"] == "SIGNAL"
    assert net_types["clk_b"] == "CLOCK"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd r2g-rtl2gds && python3 -m pytest tests/test_extract_wirelength.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'extract_wirelength'`

- [ ] **Step 3: Create the extractor (copy source verbatim)**

```bash
cp extract_label/wirelength/extract_wirelength.py r2g-rtl2gds/scripts/extract/labels/extract_wirelength.py
chmod +x r2g-rtl2gds/scripts/extract/labels/extract_wirelength.py
```

(No edits — the file's `parse_def_wirelength` and `main` are used as-is.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd r2g-rtl2gds && python3 -m pytest tests/test_extract_wirelength.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/scripts/extract/labels/extract_wirelength.py r2g-rtl2gds/tests/test_extract_wirelength.py
git commit -m "feat(skill): add wirelength label extractor + regression test"
```

---

## Task 3: Stats roller `compute_label_stats.py` + test

**Files:**
- Create: `r2g-rtl2gds/scripts/extract/labels/compute_label_stats.py`
- Test: `r2g-rtl2gds/tests/test_compute_label_stats.py`

- [ ] **Step 1: Write the failing test**

Create `r2g-rtl2gds/tests/test_compute_label_stats.py`:

```python
"""Tests for compute_label_stats.py — per-design label statistics."""
from __future__ import annotations

import json

import compute_label_stats as cls


def test_numeric_summary_percentiles():
    s = cls.numeric_summary([float(i) for i in range(1, 101)])  # 1..100
    assert s["min"] == 1.0
    assert s["max"] == 100.0
    assert abs(s["mean"] - 50.5) < 1e-9
    assert abs(s["p50"] - 50.5) < 1e-6   # linear interp midpoint of 1..100
    assert abs(s["p90"] - 90.1) < 1e-6


def test_numeric_summary_empty_is_none():
    assert cls.numeric_summary([]) is None


def test_summarize_wirelength_counts_mask(tmp_path):
    (tmp_path / "wirelength.csv").write_text(
        "Design,Net,NetType,WireLength_um,label,mask_wl\n"
        "d,n1,SIGNAL,3.0,1.386,true\n"
        "d,n2,CLOCK,5.0,1.792,false\n"
    )
    res = cls.summarize(str(tmp_path), "wirelength", cls.SPECS["wirelength"])
    assert res["status"] == "ok"
    assert res["rows"] == 2
    assert res["signal_nets"] == 1
    assert res["masked_nets"] == 1
    assert res["label"]["max"] > res["label"]["min"]


def test_summarize_missing_csv_is_skipped(tmp_path):
    res = cls.summarize(str(tmp_path), "timing", cls.SPECS["timing"])
    assert res["status"] == "skipped"


def test_build_report_writes_json(tmp_path):
    (tmp_path / "irdrop.csv").write_text(
        "Design,Cell,X,Y,Voltage_V,IR_Drop_mV,P95_mV,label,has_irdrop\n"
        "d,c1,0,0,1.09,10.0,12.0,0.69,true\n"
    )
    out = tmp_path / "labels_stats.json"
    cls.build_report(str(tmp_path), str(out), design="d", platform="nangate45")
    data = json.loads(out.read_text())
    assert data["design"] == "d"
    assert data["platform"] == "nangate45"
    assert data["labels"]["irdrop"]["status"] == "ok"
    assert data["labels"]["irdrop"]["has_irdrop"] is True
    assert data["labels"]["congestion"]["status"] == "skipped"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd r2g-rtl2gds && python3 -m pytest tests/test_compute_label_stats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'compute_label_stats'`

- [ ] **Step 3: Create the stats roller**

Create `r2g-rtl2gds/scripts/extract/labels/compute_label_stats.py`:

```python
#!/usr/bin/env python3
"""Roll per-cell/per-net label CSVs into a compact per-design statistics JSON.

Pure stdlib (csv + statistics + math). Reads the four label CSVs from a labels
directory and writes reports/labels_stats.json. A label whose CSV is missing or
empty is recorded with status "skipped".
"""
import csv
import json
import math
import os
import statistics
import sys

# label name -> (csv filename, label column, raw-metric column)
SPECS = {
    "congestion": {"file": "congestion.csv", "label": "label", "metric": "cell_congestion"},
    "wirelength": {"file": "wirelength.csv", "label": "label", "metric": "WireLength_um"},
    "timing": {"file": "timing.csv", "label": "label", "metric": "Path_Delay_ns"},
    "irdrop": {"file": "irdrop.csv", "label": "label", "metric": "IR_Drop_mV"},
}


def _percentile(sorted_vals, q):
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    idx = q * (n - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - idx) + sorted_vals[hi] * (idx - lo)


def numeric_summary(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    return {
        "min": vals[0],
        "max": vals[-1],
        "mean": statistics.fmean(vals),
        "p50": _percentile(vals, 0.50),
        "p90": _percentile(vals, 0.90),
        "p95": _percentile(vals, 0.95),
        "p99": _percentile(vals, 0.99),
    }


def _col_floats(rows, col):
    out = []
    for row in rows:
        try:
            out.append(float(row[col]))
        except (ValueError, KeyError, TypeError):
            pass
    return out


def _is_true(val):
    return str(val).strip().lower() == "true"


def summarize(labels_dir, name, spec):
    path = os.path.join(labels_dir, spec["file"])
    if not os.path.exists(path):
        return {"status": "skipped", "reason": "csv missing"}
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {"status": "skipped", "reason": "csv empty"}

    res = {
        "status": "ok",
        "rows": len(rows),
        "label": numeric_summary(_col_floats(rows, spec["label"])),
        spec["metric"]: numeric_summary(_col_floats(rows, spec["metric"])),
    }
    if name == "wirelength":
        sig = sum(1 for r in rows if _is_true(r.get("mask_wl", "")))
        res["signal_nets"] = sig
        res["masked_nets"] = len(rows) - sig
    elif name == "timing":
        inp = sum(1 for r in rows if _is_true(r.get("in_sta_path", "")))
        res["in_path"] = inp
        res["not_in_path"] = len(rows) - inp
    elif name == "irdrop":
        res["has_irdrop"] = any(_is_true(r.get("has_irdrop", "")) for r in rows)
        p95s = _col_floats(rows, "P95_mV")
        res["p95_mV"] = p95s[0] if p95s else None
    return res


def build_report(labels_dir, out_path, design="unknown", platform="unknown"):
    report = {"design": design, "platform": platform, "labels": {}}
    for name, spec in SPECS.items():
        report["labels"][name] = summarize(labels_dir, name, spec)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    return report


def main():
    if len(sys.argv) < 3:
        print("usage: compute_label_stats.py <labels_dir> <out_json> [design] [platform]")
        sys.exit(1)
    labels_dir = sys.argv[1]
    out_path = sys.argv[2]
    design = sys.argv[3] if len(sys.argv) > 3 else "unknown"
    platform = sys.argv[4] if len(sys.argv) > 4 else "unknown"
    report = build_report(labels_dir, out_path, design, platform)
    ok = sum(1 for v in report["labels"].values() if v["status"] == "ok")
    print(f"Wrote {out_path}: {ok}/{len(report['labels'])} label sets present")


if __name__ == "__main__":
    main()
```

Then: `chmod +x r2g-rtl2gds/scripts/extract/labels/compute_label_stats.py`

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd r2g-rtl2gds && python3 -m pytest tests/test_compute_label_stats.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/scripts/extract/labels/compute_label_stats.py r2g-rtl2gds/tests/test_compute_label_stats.py
git commit -m "feat(skill): add per-design label statistics roller"
```

---

## Task 4: Generalize + migrate timing extractor (Tcl)

**Files:**
- Create: `r2g-rtl2gds/scripts/extract/labels/extract_timing.tcl`

Liberty loading must use a resolved list (`R2G_LIB_FILES`, space-separated) instead of a single hardcoded Nangate lib. No unit test (needs OpenROAD) — validated in Task 9.

- [ ] **Step 1: Create the file (copy source, then edit liberty loading)**

```bash
cp extract_label/timing/extract_timing.tcl r2g-rtl2gds/scripts/extract/labels/extract_timing.tcl
```

- [ ] **Step 2: Replace the liberty-file resolution + read blocks**

In `r2g-rtl2gds/scripts/extract/labels/extract_timing.tcl`, **replace** the single-lib definition (source line 24, `set lib_file [file join $project_root "NangateOpenCellLibrary_typical.lib"]`) with a resolved list built from the env:

```tcl
# Resolved liberty list (space-separated absolute paths) from the orchestrator.
# Falls back to the nangate typical lib next to the script for standalone use.
set lib_files {}
if {[info exists ::env(R2G_LIB_FILES)] && [string trim $::env(R2G_LIB_FILES)] != ""} {
    foreach lib $::env(R2G_LIB_FILES) {
        if {[file exists $lib]} { lappend lib_files $lib }
    }
}
if {[llength $lib_files] == 0} {
    set fallback_lib [file join $project_root "NangateOpenCellLibrary_typical.lib"]
    if {[file exists $fallback_lib]} { lappend lib_files $fallback_lib }
}
```

- [ ] **Step 3: Update the ODB read branch** (source lines 67-70)

Replace `read_liberty $lib_file` (in the `if {$odb_file != "" ...}` branch) with:

```tcl
    read_db $odb_file
    foreach lib $lib_files { read_liberty $lib }
```

- [ ] **Step 4: Update the DEF read branch** (source lines 80-83)

Replace the single `read_liberty $lib_file` (in the `else` branch, before the `read_lef` calls) with:

```tcl
    foreach lib $lib_files { read_liberty $lib }
```

Leave the `read_lef $tech_lef` / `$macro_lef` / `$macro_mod_lef` and fakeram glob lines intact (DEF branch is the standalone fallback; the orchestrator passes an ODB).

- [ ] **Step 5: Smoke-check syntax**

Run: `tclsh -c 'source r2g-rtl2gds/scripts/extract/labels/extract_timing.tcl' ; echo "syntax-checked (errors above if any non-OpenROAD command failed early)"`
Expected: It will error on the first OpenROAD-only command (`read_db`/`get_pins`) — that's fine; we only care there is no Tcl *parse* error (no "missing close-brace"/"extra characters"). If it reports a brace/syntax error, fix it. (Full functional check is Task 9.)

- [ ] **Step 6: Commit**

```bash
git add r2g-rtl2gds/scripts/extract/labels/extract_timing.tcl
git commit -m "feat(skill): add timing label extractor with platform-agnostic liberty loading"
```

---

## Task 5: Migrate IR-drop extractor (Tcl, verbatim)

**Files:**
- Create: `r2g-rtl2gds/scripts/extract/labels/extract_irdrop.tcl`

The source already reads supply voltage from `$::env(SUPPLY_VOLTAGE)` and the design from the ODB — no logic change needed. The orchestrator supplies `SUPPLY_VOLTAGE` and `ODB_FILE`.

- [ ] **Step 1: Copy the source verbatim**

```bash
cp extract_label/irdrop/run_pdnsim.tcl r2g-rtl2gds/scripts/extract/labels/extract_irdrop.tcl
```

- [ ] **Step 2: Smoke-check syntax**

Run: `tclsh -c 'source r2g-rtl2gds/scripts/extract/labels/extract_irdrop.tcl'`
Expected: errors on the first OpenROAD-only command, no Tcl parse error.

- [ ] **Step 3: Commit**

```bash
git add r2g-rtl2gds/scripts/extract/labels/extract_irdrop.tcl
git commit -m "feat(skill): add IR-drop label extractor (PDNSim)"
```

---

## Task 6: Platform path resolver `resolve_platform_paths.sh`

**Files:**
- Create: `r2g-rtl2gds/scripts/flow/resolve_platform_paths.sh`

Resolves `LIB_FILES`, `TECH_LEF`, `SC_LEF`, `ADDITIONAL_LIBS`, `ADDITIONAL_LEFS`, `SUPPLY_VOLTAGE` by asking the ORFS Makefile to expand them (so asap7/gf180 corner variables resolve correctly), with a glob fallback.

- [ ] **Step 1: Create the resolver**

Create `r2g-rtl2gds/scripts/flow/resolve_platform_paths.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# usage: resolve_platform_paths.sh <config.mk> <platform>
# Emits KEY=VALUE lines on stdout:
#   LIB_FILES TECH_LEF SC_LEF ADDITIONAL_LIBS ADDITIONAL_LEFS SUPPLY_VOLTAGE
# Primary source: ORFS Makefile variable expansion (handles corner-built vars on
# asap7/gf180). Fallback: glob the platform dir + a per-platform voltage map.

CONFIG_MK="${1:-}"
PLATFORM="${2:-nangate45}"

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

PLATFORM_DIR="$FLOW_DIR/platforms/$PLATFORM"

LIB_FILES=""; TECH_LEF=""; SC_LEF=""; ADDITIONAL_LIBS=""; ADDITIONAL_LEFS=""; PWR=""

# --- Primary: ask ORFS Make to expand the variables -------------------------
if [[ -n "$CONFIG_MK" && -f "$CONFIG_MK" && -f "$FLOW_DIR/Makefile" ]]; then
  # ORFS Makefile uses SCRIPTS_DIR internally; an inherited value breaks it.
  unset SCRIPTS_DIR || true
  DUMP="$(cd "$FLOW_DIR" && make -f Makefile \
      DESIGN_CONFIG="$CONFIG_MK" PLATFORM="$PLATFORM" \
      --eval='__r2g_dump: ; @printf "%s\n" "LIB_FILES=$(LIB_FILES)" "TECH_LEF=$(TECH_LEF)" "SC_LEF=$(SC_LEF)" "ADDITIONAL_LIBS=$(ADDITIONAL_LIBS)" "ADDITIONAL_LEFS=$(ADDITIONAL_LEFS)" "PWR_NETS_VOLTAGES=$(PWR_NETS_VOLTAGES)"' \
      __r2g_dump 2>/dev/null || true)"
  while IFS= read -r line; do
    case "$line" in
      LIB_FILES=*)        LIB_FILES="${line#LIB_FILES=}" ;;
      TECH_LEF=*)         TECH_LEF="${line#TECH_LEF=}" ;;
      SC_LEF=*)           SC_LEF="${line#SC_LEF=}" ;;
      ADDITIONAL_LIBS=*)  ADDITIONAL_LIBS="${line#ADDITIONAL_LIBS=}" ;;
      ADDITIONAL_LEFS=*)  ADDITIONAL_LEFS="${line#ADDITIONAL_LEFS=}" ;;
      PWR_NETS_VOLTAGES=*) PWR="${line#PWR_NETS_VOLTAGES=}" ;;
    esac
  done <<< "$DUMP"
fi

# Validate the primary LIB_FILES actually exist; else trigger the fallback.
_first_existing_lib=""
for l in $LIB_FILES; do [[ -f "$l" ]] && { _first_existing_lib="$l"; break; }; done

# --- Fallback: glob the platform dir ----------------------------------------
if [[ -z "$_first_existing_lib" ]]; then
  for pat in '*typical*.lib' '*__tt*.lib' '*_tt_*.lib' '*tt*.lib' '*.lib'; do
    found=$(ls -1 "$PLATFORM_DIR"/lib/$pat 2>/dev/null | grep -v 'fakeram' | head -1 || true)
    [[ -n "$found" ]] && { LIB_FILES="$found"; break; }
  done
fi
if [[ -z "$TECH_LEF" || ! -f "$TECH_LEF" ]]; then
  for pat in '*tech*.lef' '*.tlef' '*.tech.lef'; do
    found=$(ls -1 "$PLATFORM_DIR"/lef/$pat 2>/dev/null | head -1 || true)
    [[ -n "$found" ]] && { TECH_LEF="$found"; break; }
  done
fi

# --- Supply voltage ---------------------------------------------------------
# Parse "VDD <v> ..." from PWR_NETS_VOLTAGES; else per-platform default.
SUPPLY_VOLTAGE=""
if [[ -n "$PWR" ]]; then
  SUPPLY_VOLTAGE=$(echo "$PWR" | tr -d '"' | awk '{print $2}')
fi
case "$SUPPLY_VOLTAGE" in
  ''|*[!0-9.]*)
    case "$PLATFORM" in
      nangate45)   SUPPLY_VOLTAGE=1.1 ;;
      sky130hd|sky130hs) SUPPLY_VOLTAGE=1.8 ;;
      asap7)       SUPPLY_VOLTAGE=0.70 ;;
      gf180)       SUPPLY_VOLTAGE=5.0 ;;
      ihp-sg13g2)  SUPPLY_VOLTAGE=1.2 ;;
      *)           SUPPLY_VOLTAGE=1.0 ;;
    esac
    ;;
esac

printf "LIB_FILES=%s\n" "$LIB_FILES"
printf "TECH_LEF=%s\n" "$TECH_LEF"
printf "SC_LEF=%s\n" "$SC_LEF"
printf "ADDITIONAL_LIBS=%s\n" "$ADDITIONAL_LIBS"
printf "ADDITIONAL_LEFS=%s\n" "$ADDITIONAL_LEFS"
printf "SUPPLY_VOLTAGE=%s\n" "$SUPPLY_VOLTAGE"
```

Then: `chmod +x r2g-rtl2gds/scripts/flow/resolve_platform_paths.sh`

- [ ] **Step 2: Smoke-test against a real design config**

Run:
```bash
cd /proj/workarea/user5/agent-r2g
bash r2g-rtl2gds/scripts/flow/resolve_platform_paths.sh design_cases/aes_core/constraints/config.mk nangate45
```
Expected: prints six `KEY=VALUE` lines; `LIB_FILES` points to an existing `NangateOpenCellLibrary_typical.lib`, `TECH_LEF` ends in `.tech.lef`, `SUPPLY_VOLTAGE=1.1`. Verify the lib path exists:
```bash
ls -l "$(bash r2g-rtl2gds/scripts/flow/resolve_platform_paths.sh design_cases/aes_core/constraints/config.mk nangate45 | sed -n 's/^LIB_FILES=//p' | awk '{print $1}')"
```
Expected: file exists. If the Make dump path produced nothing, confirm the glob fallback still yielded a valid lib.

- [ ] **Step 3: Commit**

```bash
git add r2g-rtl2gds/scripts/flow/resolve_platform_paths.sh
git commit -m "feat(skill): add ORFS Make-eval platform path/voltage resolver"
```

---

## Task 7: Orchestrator `run_labels.sh`

**Files:**
- Create: `r2g-rtl2gds/scripts/flow/run_labels.sh`

- [ ] **Step 1: Create the orchestrator**

Create `r2g-rtl2gds/scripts/flow/run_labels.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# usage: run_labels.sh <project-dir> [platform] [flow_variant]
# Extracts per-cell/per-net dataset labels (congestion, wirelength, timing,
# IR drop) from a completed ORFS backend run, plus a per-design stats JSON.
# Fail-soft: a missing input or per-label tool error is recorded, not fatal.
# Results: <project-dir>/labels/*.csv and <project-dir>/reports/labels_stats.json

PROJECT_DIR="${1:-}"
PLATFORM="${2:-}"
FLOW_VARIANT_ARG="${3:-}"

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_labels.sh <project-dir> [platform]" >&2
  exit 1
fi

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LABELS_SRC="$SKILL_DIR/scripts/extract/labels"
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

if [[ -z "${ORFS_ROOT:-}" || ! -d "$FLOW_DIR" ]]; then
  echo "ERROR: ORFS not found. Set ORFS_ROOT." >&2
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"
SDC_FILE="$PROJECT_DIR/constraints/constraint.sdc"
LABELS_DIR="$PROJECT_DIR/labels"
REPORTS_DIR="$PROJECT_DIR/reports"
mkdir -p "$LABELS_DIR" "$REPORTS_DIR"

DESIGN_NAME="$(basename "$PROJECT_DIR")"
if [[ -f "$CONFIG_MK" ]]; then
  _dn=$(grep -E '^\s*(export\s+)?DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
  [[ -n "$_dn" ]] && DESIGN_NAME="$_dn"
  if [[ -z "$PLATFORM" ]]; then
    _pl=$(grep -E '^\s*(export\s+)?PLATFORM\b' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
    PLATFORM="${_pl:-nangate45}"
  fi
fi
PLATFORM="${PLATFORM:-nangate45}"

# --- Locate the collected 6_final.{odb,def} --------------------------------
ODB=""; DEF=""
BACKEND_DIR="$PROJECT_DIR/backend"
if [[ -d "$BACKEND_DIR" ]]; then
  for run in $(ls -d "$BACKEND_DIR"/RUN_* 2>/dev/null | sort -r); do
    for sub in final results; do
      [[ -z "$ODB" && -f "$run/$sub/6_final.odb" ]] && ODB="$run/$sub/6_final.odb"
      [[ -z "$DEF" && -f "$run/$sub/6_final.def" ]] && DEF="$run/$sub/6_final.def"
    done
    [[ -n "$ODB" || -n "$DEF" ]] && break
  done
fi
# Fallback: live ORFS results dir
if [[ -z "$ODB" || -z "$DEF" ]]; then
  VARIANT="${FLOW_VARIANT_ARG:-$(basename "$PROJECT_DIR")}"
  for rd in "$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$VARIANT" "$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME"; do
    [[ -z "$ODB" && -f "$rd/6_final.odb" ]] && ODB="$rd/6_final.odb"
    [[ -z "$DEF" && -f "$rd/6_final.def" ]] && DEF="$rd/6_final.def"
  done
fi

if [[ -z "$ODB" && -z "$DEF" ]]; then
  echo "SKIP: no 6_final.odb/def found for $DESIGN_NAME — backend not completed/collected." >&2
  printf '{"design":"%s","platform":"%s","labels":{},"status":"skipped","reason":"no backend artifacts"}\n' \
    "$DESIGN_NAME" "$PLATFORM" > "$REPORTS_DIR/labels_stats.json"
  exit 0
fi

echo "Design: $DESIGN_NAME  Platform: $PLATFORM"
echo "ODB: ${ODB:-<none>}"
echo "DEF: ${DEF:-<none>}"

# --- Resolve platform liberty/lef/voltage ----------------------------------
RESOLVED="$(bash "$(dirname "${BASH_SOURCE[0]}")/resolve_platform_paths.sh" "$CONFIG_MK" "$PLATFORM" || true)"
LIB_FILES=$(echo "$RESOLVED" | sed -n 's/^LIB_FILES=//p')
TECH_LEF=$(echo "$RESOLVED" | sed -n 's/^TECH_LEF=//p')
ADDITIONAL_LIBS=$(echo "$RESOLVED" | sed -n 's/^ADDITIONAL_LIBS=//p')
SUPPLY_VOLTAGE=$(echo "$RESOLVED" | sed -n 's/^SUPPLY_VOLTAGE=//p')
SUPPLY_VOLTAGE="${SUPPLY_VOLTAGE:-1.1}"

# --- Clock period / port from the design SDC -------------------------------
CLOCK_PERIOD="10.0"; CLOCK_PORT=""
if [[ -f "$SDC_FILE" ]]; then
  _cp=$(grep -E '^\s*set\s+clk_period\b' "$SDC_FILE" | head -1 | sed -E 's/.*set\s+clk_period\s+//' | awk '{print $1}')
  [[ -n "$_cp" ]] && CLOCK_PERIOD="$_cp"
  _pn=$(grep -E '^\s*set\s+clk_port_name\b' "$SDC_FILE" | head -1 | sed -E 's/.*set\s+clk_port_name\s+//' | awk '{print $1}' | tr -d '"')
  [[ -n "$_pn" ]] && CLOCK_PORT="$_pn"
fi
echo "clk_period=$CLOCK_PERIOD clk_port=${CLOCK_PORT:-<auto>} supply=$SUPPLY_VOLTAGE"

OPENROAD="${OPENROAD_EXE:-openroad}"
LABEL_TIMEOUT="${LABEL_TIMEOUT:-2400}"

run_soft() {  # name + command...; never aborts the orchestrator
  local name="$1"; shift
  echo "--- $name ---"
  if timeout --signal=TERM --kill-after=30 "$LABEL_TIMEOUT" "$@" > "$LABELS_DIR/$name.log" 2>&1; then
    echo "  $name: ok"
  else
    echo "  $name: FAILED (see $LABELS_DIR/$name.log)" >&2
  fi
}

# --- Congestion (DEF + tech.lef) -------------------------------------------
if [[ -n "$DEF" ]]; then
  TECH_LEF="$TECH_LEF" run_soft congestion \
    python3 "$LABELS_SRC/extract_congestion.py" "$DEF" "$LABELS_DIR/congestion.csv" "$DESIGN_NAME"
fi

# --- Wirelength (DEF) ------------------------------------------------------
if [[ -n "$DEF" ]]; then
  run_soft wirelength \
    python3 "$LABELS_SRC/extract_wirelength.py" "$DEF" "$LABELS_DIR/wirelength.csv" "$DESIGN_NAME"
fi

# --- Timing (ODB preferred, DEF fallback) + liberty ------------------------
# Leading var-assignments before the run_soft FUNCTION call are exported into
# the openroad child (verified bash behavior). Do NOT wrap with `env` — env
# cannot exec a shell function. ${CLOCK_PORT:+...} omits the assignment entirely
# when empty so extract_timing.tcl falls back to clock auto-detection.
TIMING_LIBS="$LIB_FILES $ADDITIONAL_LIBS"
if [[ -n "$ODB" ]]; then
  ODB_FILE="$ODB" R2G_LIB_FILES="$TIMING_LIBS" OUTPUT_CSV="$LABELS_DIR/timing.csv" \
    CLOCK_PERIOD="$CLOCK_PERIOD" ${CLOCK_PORT:+CLOCK_PORT="$CLOCK_PORT"} DESIGN_NAME="$DESIGN_NAME" \
    run_soft timing "$OPENROAD" -no_splash -exit "$LABELS_SRC/extract_timing.tcl"
elif [[ -n "$DEF" ]]; then
  DEF_FILE="$DEF" R2G_LIB_FILES="$TIMING_LIBS" TECH_LEF="$TECH_LEF" OUTPUT_CSV="$LABELS_DIR/timing.csv" \
    CLOCK_PERIOD="$CLOCK_PERIOD" ${CLOCK_PORT:+CLOCK_PORT="$CLOCK_PORT"} DESIGN_NAME="$DESIGN_NAME" \
    run_soft timing "$OPENROAD" -no_splash -exit "$LABELS_SRC/extract_timing.tcl"
fi

# --- IR drop (ODB) ---------------------------------------------------------
if [[ -n "$ODB" ]]; then
  ODB_FILE="$ODB" OUTPUT_RPT="$LABELS_DIR/irdrop.csv" \
    SUPPLY_VOLTAGE="$SUPPLY_VOLTAGE" DESIGN_NAME="$DESIGN_NAME" \
    run_soft irdrop "$OPENROAD" -no_splash -exit "$LABELS_SRC/extract_irdrop.tcl"
fi

# --- Stats roll-up ---------------------------------------------------------
python3 "$LABELS_SRC/compute_label_stats.py" "$LABELS_DIR" "$REPORTS_DIR/labels_stats.json" "$DESIGN_NAME" "$PLATFORM"

echo "Labels: $LABELS_DIR"
echo "Stats:  $REPORTS_DIR/labels_stats.json"
```

Then: `chmod +x r2g-rtl2gds/scripts/flow/run_labels.sh`

(The leading-assignment form used for the timing/irdrop calls was verified: vars set
before a function invocation are exported into the commands the function runs. Step 2
confirms the Tcl scripts actually see `ODB_FILE`/`OUTPUT_CSV`.)

- [ ] **Step 2: Smoke-test on aes_core**

Run:
```bash
cd /proj/workarea/user5/agent-r2g
bash r2g-rtl2gds/scripts/flow/run_labels.sh design_cases/aes_core nangate45
```
Expected: prints Design/Platform/ODB/DEF, runs four stages, ends with Labels/Stats paths. Then:
```bash
ls -l design_cases/aes_core/labels/
head -3 design_cases/aes_core/labels/congestion.csv design_cases/aes_core/labels/wirelength.csv design_cases/aes_core/labels/timing.csv design_cases/aes_core/labels/irdrop.csv
cat design_cases/aes_core/reports/labels_stats.json
```
Expected: four non-empty CSVs with correct headers; `labels_stats.json` shows `status:"ok"` for all four with sane row counts. Fix any per-label failures (check `labels/<name>.log`) before committing.

- [ ] **Step 3: Commit**

```bash
git add r2g-rtl2gds/scripts/flow/run_labels.sh
git commit -m "feat(skill): add run_labels.sh label-extraction orchestrator"
```

---

## Task 8: Project layout + docs

**Files:**
- Modify: `r2g-rtl2gds/scripts/project/init_project.py:6-7`
- Modify: `r2g-rtl2gds/SKILL.md`
- Create: `r2g-rtl2gds/references/label-extraction.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add `labels` to the project template dirs**

In `r2g-rtl2gds/scripts/project/init_project.py`, change `TEMPLATE_DIRS` (lines 6-7) to include `labels`:

```python
TEMPLATE_DIRS = [
    "input", "rtl", "tb", "constraints", "lint", "sim", "synth", "backend", "drc", "lvs", "rcx", "reports", "labels"
]
```

- [ ] **Step 2: Verify init_project creates labels/**

Run:
```bash
cd /proj/workarea/user5/agent-r2g
python3 r2g-rtl2gds/scripts/project/init_project.py /tmp/r2g_initcheck && ls /tmp/r2g_initcheck | tr '\n' ' '; rm -rf /tmp/r2g_initcheck
```
Expected: the listing includes `labels`.

- [ ] **Step 3: Add the SKILL.md step**

In `r2g-rtl2gds/SKILL.md`, after the RCX extraction block in "Running a Full Flow" (step 13 — the `extract_rcx.py` line), add a new step:

```markdown
13b. Extract dataset labels (optional, for dataset building):
    - `scripts/flow/run_labels.sh <project-dir> [platform]`
    - Emits per-cell/per-net label CSVs to `<project-dir>/labels/` (congestion,
      wirelength, timing, IR drop) and a per-design `reports/labels_stats.json`.
    - Fail-soft: a missing input or per-label tool error is recorded, not fatal.
    - Platform-agnostic: liberty/lef/supply-voltage are resolved from the ORFS
      platform config. See `references/label-extraction.md`.
```

Also add a row to the "Resource Map" list:

```markdown
- Read `references/label-extraction.md` when building the physical-design dataset (per-cell/per-net labels + stats).
```

- [ ] **Step 4: Write the reference doc**

Create `r2g-rtl2gds/references/label-extraction.md`:

```markdown
# Label Extraction (dataset building)

`scripts/flow/run_labels.sh <project-dir> [platform]` runs after a completed ORFS
backend and emits per-cell/per-net **regression-target** tables plus a per-design
statistics JSON. It is fail-soft: each of the four label sets is independent, and a
missing input or tool error records a per-label status without aborting the others.

## Outputs

Written to `design_cases/<design>/labels/` and `design_cases/<design>/reports/`:

| File | Rows | Columns | Label transform |
|------|------|---------|-----------------|
| `labels/congestion.csv` | per cell | `Design,Cell,cell_type,cell_congestion,label` | `label = sqrt(cell_congestion)` |
| `labels/wirelength.csv` | per net | `Design,Net,NetType,WireLength_um,label,mask_wl` | `label = log1p(WireLength_um)`; `mask_wl = NetType==SIGNAL` |
| `labels/timing.csv` | per cell | `Design,Cell,Cell_Slack_ns,Path_Delay_ns,label,in_sta_path` | `label = log(1+Path_Delay_ns)`; `Path_Delay_ns = clk_period - worst_slack` (>=0) |
| `labels/irdrop.csv` | per cell | `Design,Cell,X,Y,Voltage_V,IR_Drop_mV,P95_mV,label,has_irdrop` | `label = log(1 + IR_Drop_mV/P95_mV)` |
| `reports/labels_stats.json` | — | per-label count + min/mean/p50/p90/p95/p99/max for `label` and the raw metric, plus mask/in_path/has_irdrop tallies | — |

`Design` + `Cell`/`Net` are the join keys across the four tables.

## Inputs & resolution

- **Design geometry:** the collected `backend/RUN_*/{final,results}/6_final.odb`
  (timing, IR drop) and `6_final.def` (congestion, wirelength). Falls back to the
  live ORFS results dir.
- **Platform liberty/lef/voltage:** `resolve_platform_paths.sh` asks the ORFS
  Makefile to expand `LIB_FILES`, `TECH_LEF`, `SC_LEF`, `ADDITIONAL_LIBS`,
  `PWR_NETS_VOLTAGES` for the design's `config.mk` (so asap7/gf180 corner-built
  variables resolve), with a platform-dir glob + per-platform voltage map as
  fallback. Works on all ORFS platforms.
- **Clock period / port:** parsed from `constraints/constraint.sdc`
  (`set clk_period`, `set clk_port_name`); defaults to 10.0 / clock-name auto-detect.
  A wrong clock period biases `Path_Delay_ns` — keep the SDC accurate.

## Env knobs (override resolution)

| Var | Effect |
|-----|--------|
| `R2G_LIB_FILES` | space-separated liberty paths for timing (overrides resolver) |
| `TECH_LEF` | tech LEF for congestion layer pitches |
| `SUPPLY_VOLTAGE` | nominal VDD for IR-drop delta |
| `CLOCK_PERIOD` / `CLOCK_PORT` | timing clock (overrides SDC) |
| `ODB_FILE` / `DEF_FILE` | explicit input design |
| `LABEL_TIMEOUT` | per-label timeout seconds (default 2400) |

## Batch backfill

`tools/run_labels_batch.sh [design ...]` runs `run_labels.sh` across many completed
designs with a concurrency cap (default 4 — OpenROAD STA/PDNSim are memory-light vs.
KLayout LVS). With no args it auto-discovers designs that have a collected
`6_final.odb`. Per-design logs and a `labels_backfill.jsonl` roll-up land under
`design_cases/_batch/logs_labels_<ts>/`.

## Scope notes

- Per-design only — corpus-wide aggregation, knowledge-store ingest, and dashboard
  surfacing are intentionally not wired here.
- Typical/primary corner only (no multi-corner labels).
- Designs that never reached `6_final` are skipped, not errored.
```

- [ ] **Step 5: Update CLAUDE.md layout note**

In `CLAUDE.md`, under the `r2g-rtl2gds/` project-layout block, change the `scripts/extract/` line to mention the labels subdir, and add a line for the labels output dir. Replace:

```
  scripts/extract/               # Parse tool output → JSON: extract_ppa, extract_drc, extract_lvs, …
```
with:
```
  scripts/extract/               # Parse tool output → JSON: extract_ppa, extract_drc, extract_lvs, …
    labels/                        # Dataset label extractors (congestion, wirelength, timing, irdrop) + stats
```

And under the `design_cases/` block, after the `<design-name>/` line add:
```
    <design-name>/labels/           # Per-cell/per-net dataset label CSVs (run_labels.sh)
```

- [ ] **Step 6: Run the full skill test suite (no regressions)**

Run: `cd r2g-rtl2gds && python3 -m pytest -q`
Expected: all tests pass, including the three new label tests.

- [ ] **Step 7: Commit**

```bash
git add r2g-rtl2gds/scripts/project/init_project.py r2g-rtl2gds/SKILL.md r2g-rtl2gds/references/label-extraction.md CLAUDE.md
git commit -m "feat(skill): wire label-extraction stage into layout + docs"
```

---

## Task 9: Validate on aes_core + picorv32_core

**Files:** none (validation + any hotfixes from Tasks 1-8).

- [ ] **Step 1: Run on both designs**

```bash
cd /proj/workarea/user5/agent-r2g
for d in aes_core picorv32_core; do
  echo "######## $d ########"
  bash r2g-rtl2gds/scripts/flow/run_labels.sh design_cases/$d nangate45 || true
done
```

- [ ] **Step 2: Inspect outputs**

```bash
for d in aes_core picorv32_core; do
  echo "== $d =="
  wc -l design_cases/$d/labels/*.csv 2>/dev/null
  python3 -c "import json;print(json.dumps(json.load(open('design_cases/$d/reports/labels_stats.json'))['labels'],indent=2))"
done
```
Expected per design: four CSVs with > a handful of rows; `timing` rows ≈ instance count with a nonzero `in_path`; `irdrop` `has_irdrop` boolean present; `congestion`/`wirelength` label summaries non-null. Investigate any `status:"skipped"` or empty CSV via `labels/<name>.log` and fix the offending script/orchestrator (then re-commit under the relevant task).

- [ ] **Step 3: Sanity-check label semantics**

Confirm `timing.csv` `Path_Delay_ns` ≈ `clk_period - slack` (read clk_period from `design_cases/aes_core/constraints/constraint.sdc`), and `wirelength.csv` `mask_wl` is `false` for clock/power nets. If timing labels look uniformly 0 or use clk_period=10 when the SDC says otherwise, fix the SDC parsing in `run_labels.sh`.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A && git commit -m "fix(skill): label-extraction fixes from aes_core/picorv32 validation"
```
(Skip if no fixes were needed.)

---

## Task 10: Backfill driver + subset run

**Files:**
- Create: `r2g-rtl2gds/tools/run_labels_batch.sh`

- [ ] **Step 1: Create the batch driver**

Create `r2g-rtl2gds/tools/run_labels_batch.sh`:

```bash
#!/usr/bin/env bash
set -uo pipefail

# usage: run_labels_batch.sh [N] [design ...]
#   N         max concurrent jobs (default 4)
#   design... explicit project dirs/names under design_cases/; if omitted,
#             auto-discovers designs with a collected 6_final.odb.
# Per-design logs + labels_backfill.jsonl under design_cases/_batch/logs_labels_<ts>/.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"        # r2g-rtl2gds/
ROOT="$(cd "$REPO/.." && pwd)"                                  # repo root
RUN_LABELS="$REPO/scripts/flow/run_labels.sh"
CASES="$ROOT/design_cases"

MAXJOBS=4
if [[ "${1:-}" =~ ^[0-9]+$ ]]; then MAXJOBS="$1"; shift; fi

# Timestamp passed in to keep logs deterministic across re-runs is optional;
# default to a fixed label dir name the caller can override via LOGTAG.
LOGTAG="${LOGTAG:-$(ls -d "$CASES"/_batch/logs_labels_* 2>/dev/null | wc -l)}"
LOGDIR="$CASES/_batch/logs_labels_${LOGTAG}"
mkdir -p "$LOGDIR"
JSONL="$LOGDIR/labels_backfill.jsonl"
: > "$JSONL"

# Build design list
designs=()
if [[ $# -gt 0 ]]; then
  for d in "$@"; do designs+=("$(basename "$d")"); done
else
  while IFS= read -r odb; do
    d=$(echo "$odb" | sed -E "s#^$CASES/([^/]+)/.*#\1#")
    designs+=("$d")
  done < <(find "$CASES" -maxdepth 5 -path '*/backend/RUN_*/final/6_final.odb' 2>/dev/null | sort -u)
  # de-dup
  mapfile -t designs < <(printf '%s\n' "${designs[@]}" | awk '!seen[$0]++')
fi

echo "Backfilling labels for ${#designs[@]} designs (max $MAXJOBS concurrent) -> $LOGDIR"

run_one() {
  local d="$1"
  local proj="$CASES/$d"
  local log="$LOGDIR/$d.log"
  [[ -d "$proj" ]] || { echo "{\"design\":\"$d\",\"status\":\"missing\"}" >> "$JSONL"; return; }
  bash "$RUN_LABELS" "$proj" > "$log" 2>&1
  local stats="$proj/reports/labels_stats.json"
  if [[ -f "$stats" ]]; then
    python3 - "$d" "$stats" >> "$JSONL" <<'PY'
import json,sys
d,stats=sys.argv[1],sys.argv[2]
try:
    j=json.load(open(stats)); L=j.get("labels",{})
    row={"design":d,"status":j.get("status","done"),
         "rows":{k:(v.get("rows") if isinstance(v,dict) else None) for k,v in L.items()},
         "label_status":{k:(v.get("status") if isinstance(v,dict) else None) for k,v in L.items()}}
except Exception as e:
    row={"design":d,"status":"error","error":str(e)}
print(json.dumps(row))
PY
  else
    echo "{\"design\":\"$d\",\"status\":\"no_stats\"}" >> "$JSONL"
  fi
  echo "  done: $d"
}

i=0
for d in "${designs[@]}"; do
  run_one "$d" &
  i=$((i+1))
  if (( i % MAXJOBS == 0 )); then wait; fi
done
wait

echo "Roll-up: $JSONL"
echo "OK:     $(grep -c '"status": *"done"\|"status":"done"' "$JSONL" 2>/dev/null || echo 0)"
wc -l "$JSONL"
```

Then: `chmod +x r2g-rtl2gds/tools/run_labels_batch.sh`

- [ ] **Step 2: Pick a ~30-design subset and dry-list it**

```bash
cd /proj/workarea/user5/agent-r2g
find design_cases -maxdepth 5 -path '*/backend/RUN_*/final/6_final.odb' 2>/dev/null \
  | sed -E 's#design_cases/([^/]+)/.*#\1#' | sort -u | head -30 > /tmp/label_subset.txt
cat /tmp/label_subset.txt
```

- [ ] **Step 3: Run the subset backfill**

```bash
cd /proj/workarea/user5/agent-r2g
bash r2g-rtl2gds/tools/run_labels_batch.sh 4 $(cat /tmp/label_subset.txt)
```
Expected: each design logs "done"; the roll-up JSONL has one row per design.

- [ ] **Step 4: Review the roll-up for failures**

```bash
LOGDIR=$(ls -dt design_cases/_batch/logs_labels_* | head -1)
echo "$LOGDIR"
python3 -c "
import json,glob,collections
rows=[json.loads(l) for l in open(glob.glob('$LOGDIR/labels_backfill.jsonl')[0])]
c=collections.Counter()
for r in rows:
    for k,v in (r.get('label_status') or {}).items(): c[(k,v)]+=1
print('designs:',len(rows))
for k in sorted(c): print(k,c[k])
"
```
Expected: most `(label, "ok")`. For recurring `skipped`/failures, open the offending `design_cases/<d>/labels/<name>.log`, fix the root cause in the relevant script, re-run that design, then re-run the subset.

- [ ] **Step 5: Commit the batch driver**

```bash
git add r2g-rtl2gds/tools/run_labels_batch.sh
git commit -m "feat(skill): add run_labels_batch.sh backfill driver"
```

(The generated `design_cases/**` label CSVs are gitignored — do not commit dataset artifacts.)

---

## Task 11: Cleanup migrated source

**Files:**
- Delete: `extract_label/` (untracked staging dir, now migrated into the skill)

- [ ] **Step 1: Confirm the migrated scripts exist and the originals are redundant**

```bash
ls r2g-rtl2gds/scripts/extract/labels/
diff <(sed '/^def parse_tech_lef/,/^def parse_def_header/!d' extract_label/congestion/generate_cell_congestion.py) /dev/null >/dev/null 2>&1; echo "compared"
```

- [ ] **Step 2: Remove the staging dir**

```bash
cd /proj/workarea/user5/agent-r2g
rm -rf extract_label
```
(`extract_label/` is untracked, so no `git rm` needed; this just clears the staging copy now that the skill owns the scripts.)

- [ ] **Step 3: Final full test run**

Run: `cd r2g-rtl2gds && python3 -m pytest -q`
Expected: all green.

---

## Self-Review

**Spec coverage:**
- Per-design CSVs (4 workers) → Tasks 1,2,4,5. ✔
- Per-design stats JSON → Task 3 + orchestrator step. ✔
- All-platform resolver (Make-eval + glob/voltage fallback) → Task 6. ✔
- congestion `TYPE ROUTING` generalization → Task 1. ✔
- timing liberty-list generalization → Task 4. ✔
- irdrop supply voltage from resolver → Task 5 (verbatim) + Task 7 (passes `SUPPLY_VOLTAGE`). ✔
- Collected-backend input resolution + SDC clock → Task 7. ✔
- Orchestrator fail-soft → Task 7. ✔
- init_project `labels/` + SKILL.md 13b + references + CLAUDE.md → Task 8. ✔
- Tests (congestion/wirelength/stats) → Tasks 1-3. ✔
- Validate aes_core/picorv32 → Task 9. ✔
- Subset backfill + driver → Task 10. ✔
- Cleanup staging dir → Task 11. ✔
- Out-of-scope (corpus aggregation / knowledge ingest / dashboard) → not implemented, per spec. ✔

**Placeholder scan:** No TBD/TODO; every code step has full content. The one `run_soft`+`env` interaction caveat in Task 7 is explicitly resolved with the corrected leading-assignment form.

**Type/name consistency:** Worker filenames (`extract_congestion.py`, `extract_wirelength.py`, `extract_timing.tcl`, `extract_irdrop.tcl`, `compute_label_stats.py`), env vars (`R2G_LIB_FILES`, `TECH_LEF`, `SUPPLY_VOLTAGE`, `CLOCK_PERIOD`, `CLOCK_PORT`, `ODB_FILE`, `DEF_FILE`, `OUTPUT_CSV`, `OUTPUT_RPT`), CSV column names, and `compute_label_stats` API (`numeric_summary`, `summarize`, `SPECS`, `build_report`) are consistent across the orchestrator, tests, and stats module.
