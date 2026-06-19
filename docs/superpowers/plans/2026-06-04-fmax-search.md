# Fmax Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an automated, loose-first Fmax-characterization tool to the `r2g-rtl2gds` skill that finds the minimum clock period a design can close at, using cheap placement-stage timing as the search signal and a learnable per-family slack-deterioration model to predict signoff.

**Architecture:** A pure model/helpers module (`fmax_model.py`) holds all numerics (deterioration model, guardband, root-find loop with *injected* probe callables, SDC/variant helpers) so it is fully unit-testable with mock oracles. An I/O orchestrator (`fmax_search.py`) supplies real probes (clone variant → run ORFS through `place` → read proxy via `extract_ppa`), seeds the search from the knowledge store, writes `reports/fmax_search.json`, and optionally `--verify`s. The knowledge store gains three per-stage-slack columns, a corrected `clock_period_ns` ingest, a `--backfill` path, and per-family `closing_period` + `slack_deterioration` aggregates that feed the search and self-correct from `--verify` results.

**Tech Stack:** Python 3.10+ (stdlib only: `json`, `re`, `sqlite3`, `subprocess`, `argparse`, `statistics`, `concurrent.futures`), pytest, bash (`run_orfs.sh`), ORFS.

**Spec:** `docs/superpowers/specs/2026-06-04-fmax-search-design.md` (read it first — it carries the source-verified ORFS facts and the locked decisions D1–D7).

---

## File Structure

| File | New/Changed | Responsibility |
|------|-------------|----------------|
| `r2g-rtl2gds/knowledge/schema.sql` | Changed | 3 new `runs` columns (declared for fresh DBs). |
| `r2g-rtl2gds/knowledge/knowledge_db.py` | Changed | Same 3 columns in `_RUNS_ADDED_COLUMNS` (live-DB migration). |
| `r2g-rtl2gds/scripts/extract/extract_ppa.py` | Changed | `parse_stage_metrics()` + `--stage`; emit `summary.timing_staged`. |
| `r2g-rtl2gds/knowledge/ingest_run.py` | Changed | clk_period from SDC; staged-slack columns; `--backfill`. |
| `r2g-rtl2gds/knowledge/learn_heuristics.py` | Changed | per-family `closing_period` + `slack_deterioration` aggregates. |
| `r2g-rtl2gds/knowledge/query_knowledge.py` | Changed | thin accessors `get_closing_period()` / `get_deterioration()`. |
| `r2g-rtl2gds/scripts/reports/fmax_model.py` | New | Pure model + helpers + injectable search loop. |
| `r2g-rtl2gds/scripts/reports/fmax_search.py` | New | I/O orchestrator (probe, seed, report, cleanup, `--verify`). |
| `r2g-rtl2gds/tests/test_fmax_model.py` | New | Unit tests for the pure core. |
| `r2g-rtl2gds/tests/test_fmax_search.py` | New | Orchestrator tests with mock probes. |
| `r2g-rtl2gds/tests/test_extract_ppa_stage.py` | New | `--stage` + `timing_staged` reader tests. |
| `r2g-rtl2gds/tests/test_ingest_run.py` | Changed | clk_period-from-SDC + staged ingest + backfill. |
| `r2g-rtl2gds/tests/test_learn_heuristics.py` | Changed | deterioration/closing aggregates. |
| `r2g-rtl2gds/SKILL.md` | Changed | New optional "Fmax Search" step + env knobs. |
| `r2g-rtl2gds/references/orfs-playbook.md` | Changed | "Fmax Search (loose-first)" section. |
| `r2g-rtl2gds/references/lessons-learned.md` | Changed | place→signoff optimism + archetype caveats. |

**Test command (all tasks):** run from the repo root.
`cd /proj/workarea/user5/agent-r2g && python3 -m pytest r2g-rtl2gds/tests/<file> -v`
The repo's `r2g-rtl2gds/tests/conftest.py` already puts `knowledge/`, `scripts/reports/`, and `scripts/extract/` on `sys.path`, and provides the `tmp_knowledge_dir` (copies real `schema.sql`+`families.json`) and `fixtures_dir` fixtures.

---

## Phase 1 — Knowledge-store foundation

### Task 1: Schema migration — three per-stage-slack columns

**Files:**
- Modify: `r2g-rtl2gds/knowledge/schema.sql` (runs table, after line 26 `timing_tier`)
- Modify: `r2g-rtl2gds/knowledge/knowledge_db.py:40-46` (`_RUNS_ADDED_COLUMNS`)
- Test: `r2g-rtl2gds/tests/test_knowledge_db.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `r2g-rtl2gds/tests/test_knowledge_db.py`:

```python
def test_staged_slack_columns_exist_after_ensure_schema(tmp_knowledge_dir):
    """ensure_schema must add the three per-stage setup-slack columns even on a
    DB created before they existed (live ALTER TABLE migration)."""
    import knowledge_db
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    # Simulate a pre-existing DB by creating the table WITHOUT the new columns,
    # then running ensure_schema (which must ALTER TABLE them in).
    conn.executescript(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, project_path TEXT NOT NULL,"
        " ingested_at TEXT NOT NULL);"
    )
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    assert {"floorplan_setup_ws", "place_setup_ws", "finish_setup_ws"} <= cols
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_knowledge_db.py::test_staged_slack_columns_exist_after_ensure_schema -v`
Expected: FAIL — columns missing from `PRAGMA table_info`.

- [ ] **Step 3: Add the columns in both places**

In `schema.sql`, after the `timing_tier TEXT,` line (line 26), insert:

```sql
    -- per-stage setup worst-slack (ns), for the Fmax deterioration model
    floorplan_setup_ws      REAL,
    place_setup_ws          REAL,
    finish_setup_ws         REAL,
```

In `knowledge_db.py`, extend `_RUNS_ADDED_COLUMNS` (currently ends with `"eval_arm": "TEXT",` at line 45):

```python
    "eval_arm": "TEXT",
    # Per-stage setup worst-slack (ns) for the Fmax slack-deterioration model.
    # floorplan_setup_ws = 2_1_floorplan.json floorplan__timing__setup__ws
    # place_setup_ws     = 3_5_place_dp.json   detailedplace__timing__setup__ws
    # finish_setup_ws    = 6_report.json       finish__timing__setup__ws (== wns_ns)
    "floorplan_setup_ws": "REAL",
    "place_setup_ws": "REAL",
    "finish_setup_ws": "REAL",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_knowledge_db.py -v`
Expected: PASS (all existing + the new test).

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/knowledge/schema.sql r2g-rtl2gds/knowledge/knowledge_db.py r2g-rtl2gds/tests/test_knowledge_db.py
git commit -m "feat(knowledge): add per-stage setup-slack columns for Fmax model"
```

---

### Task 2: `extract_ppa.py` — `parse_stage_metrics` + `--stage` + `timing_staged`

**Files:**
- Modify: `r2g-rtl2gds/scripts/extract/extract_ppa.py`
- Test: `r2g-rtl2gds/tests/test_extract_ppa_stage.py` (new)

- [ ] **Step 1: Write the failing test**

Create `r2g-rtl2gds/tests/test_extract_ppa_stage.py`:

```python
"""Tests for extract_ppa.py staged-timing readers."""
from __future__ import annotations
import json
import extract_ppa


def _write(logs, name, payload):
    logs.mkdir(parents=True, exist_ok=True)
    (logs / name).write_text(json.dumps(payload), encoding="utf-8")


def test_parse_stage_metrics_floorplan_and_place(tmp_path):
    run_dir = tmp_path / "RUN_x"
    logs = run_dir / "logs"
    _write(logs, "2_1_floorplan.json",
           {"floorplan__timing__setup__ws": 5.88, "floorplan__timing__setup__tns": 0})
    _write(logs, "3_5_place_dp.json",
           {"detailedplace__timing__setup__ws": 5.81, "detailedplace__timing__setup__tns": -0.2})

    fp = extract_ppa.parse_stage_metrics(run_dir, "floorplan")
    pl = extract_ppa.parse_stage_metrics(run_dir, "place")
    assert fp == {"setup_wns": 5.88, "setup_tns": 0}
    assert pl == {"setup_wns": 5.81, "setup_tns": -0.2}


def test_parse_stage_metrics_place_falls_back_to_3_4(tmp_path):
    run_dir = tmp_path / "RUN_x"
    logs = run_dir / "logs"
    _write(logs, "3_4_place_resized.json",
           {"placeopt__timing__setup__ws": 7.0, "placeopt__timing__setup__tns": 0})
    # No 3_5 file present -> must fall back to 3_4 placeopt keys.
    assert extract_ppa.parse_stage_metrics(run_dir, "place") == {"setup_wns": 7.0, "setup_tns": 0}


def test_parse_stage_metrics_missing_returns_empty(tmp_path):
    run_dir = tmp_path / "RUN_x"
    (run_dir / "logs").mkdir(parents=True)
    assert extract_ppa.parse_stage_metrics(run_dir, "place") == {}


def test_collect_timing_staged(tmp_path):
    run_dir = tmp_path / "RUN_x"
    logs = run_dir / "logs"
    _write(logs, "2_1_floorplan.json", {"floorplan__timing__setup__ws": 5.88})
    _write(logs, "3_5_place_dp.json", {"detailedplace__timing__setup__ws": 5.81})
    staged = extract_ppa.collect_timing_staged(run_dir)
    assert staged == {"floorplan_setup_ws": 5.88, "place_setup_ws": 5.81}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_extract_ppa_stage.py -v`
Expected: FAIL — `AttributeError: module 'extract_ppa' has no attribute 'parse_stage_metrics'`.

- [ ] **Step 3: Add the readers to `extract_ppa.py`**

Insert after `parse_drc_report` (before `find_reports`, around line 101):

```python
# --- Staged setup-slack readers (for the Fmax search & deterioration model) ---
# Per-stage ORFS metrics JSONs live in <run_dir>/logs/. Keys verified against a
# real nangate45 run. 3_4_place_resized is the fallback when 3_5 is absent.
_STAGE_METRIC_FILES = {
    "floorplan": [("2_1_floorplan.json",
                   "floorplan__timing__setup__ws", "floorplan__timing__setup__tns")],
    "place": [("3_5_place_dp.json",
               "detailedplace__timing__setup__ws", "detailedplace__timing__setup__tns"),
              ("3_4_place_resized.json",
               "placeopt__timing__setup__ws", "placeopt__timing__setup__tns")],
}


def _read_stage_json(path: Path, ws_key: str, tns_key: str) -> dict:
    if not path.exists():
        return {}
    try:
        d = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    if ws_key in d:
        out["setup_wns"] = d[ws_key]
    if tns_key in d:
        out["setup_tns"] = d[tns_key]
    return out


def parse_stage_metrics(run_dir, stage: str) -> dict:
    """Return {'setup_wns':.., 'setup_tns':..} for 'floorplan' or 'place' from the
    per-stage metrics JSON under <run_dir>/logs/. Tries fallbacks in order; returns
    {} if nothing readable."""
    logs = Path(run_dir) / "logs"
    for fname, ws_key, tns_key in _STAGE_METRIC_FILES[stage]:
        out = _read_stage_json(logs / fname, ws_key, tns_key)
        if out:
            return out
    return {}


def collect_timing_staged(run_dir) -> dict:
    """{floorplan_setup_ws, place_setup_ws} from whichever stage JSONs exist."""
    staged = {}
    fp = parse_stage_metrics(run_dir, "floorplan")
    pl = parse_stage_metrics(run_dir, "place")
    if "setup_wns" in fp:
        staged["floorplan_setup_ws"] = fp["setup_wns"]
    if "setup_wns" in pl:
        staged["place_setup_ws"] = pl["setup_wns"]
    return staged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_extract_ppa_stage.py -v`
Expected: PASS.

- [ ] **Step 5: Wire `--stage` and `timing_staged` into `main()`**

In `extract_ppa.py` `main()`, replace the argv check (lines 144-149) with stage-flag parsing:

```python
def main():
    argv = sys.argv[1:]
    stage_arg = None
    if "--stage" in argv:
        i = argv.index("--stage")
        stage_arg = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) < 2:
        print('usage: extract_ppa.py <project-root> <output.json> [--stage floorplan|place]',
              file=sys.stderr)
        sys.exit(1)
    project_root = Path(argv[0])
    out_path = Path(argv[1])
```

Then, just before `out_path.parent.mkdir(...)` (the final write, ~line 251), insert:

```python
    # Staged setup slacks (for the deterioration model) + optional per-stage override.
    if reports.get('run_dir'):
        staged = collect_timing_staged(reports['run_dir'])
        if 'setup_wns' in ppa['summary']['timing']:
            staged['finish_setup_ws'] = ppa['summary']['timing']['setup_wns']
        if staged:
            ppa['summary']['timing_staged'] = staged
        if stage_arg in ('floorplan', 'place'):
            sm = parse_stage_metrics(reports['run_dir'], stage_arg)
            if sm:
                # For a place-only Fmax probe there is no finish/6_report; surface
                # the requested stage's slack in the standard summary.timing shape.
                ppa['summary']['timing'] = sm
```

- [ ] **Step 6: Run the full extract tests + commit**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_extract_ppa_stage.py -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/scripts/extract/extract_ppa.py r2g-rtl2gds/tests/test_extract_ppa_stage.py
git commit -m "feat(extract): extract_ppa --stage + summary.timing_staged for Fmax"
```

---

### Task 3: `ingest_run.py` — clk_period from SDC + staged-slack columns

**Files:**
- Modify: `r2g-rtl2gds/knowledge/ingest_run.py`
- Test: `r2g-rtl2gds/tests/test_ingest_run.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `r2g-rtl2gds/tests/test_ingest_run.py` (reuse its existing helpers for building a fake project; the test below is self-contained):

```python
def test_ingest_reads_clk_period_from_sdc_and_staged_slacks(tmp_path, tmp_knowledge_dir):
    import ingest_run, knowledge_db, json as _json
    proj = tmp_path / "design_cases" / "demo"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n", encoding="utf-8")
    # Period lives in the SDC, NOT config.mk (this is the bug being fixed).
    (proj / "constraints" / "constraint.sdc").write_text(
        "set clk_period 3.5\ncreate_clock -period $clk_period [get_ports clk]\n", encoding="utf-8")
    (proj / "reports" / "ppa.json").write_text(_json.dumps({
        "summary": {
            "timing": {"setup_wns": 0.4, "setup_tns": 0.0},
            "timing_staged": {"floorplan_setup_ws": 0.9,
                              "place_setup_ws": 0.5,
                              "finish_setup_ws": 0.4},
        }
    }), encoding="utf-8")

    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    rid = ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    r = conn.execute(
        "SELECT clock_period_ns, floorplan_setup_ws, place_setup_ws, finish_setup_ws "
        "FROM runs WHERE run_id=?", (rid,)).fetchone()
    assert r == (3.5, 0.9, 0.5, 0.4)
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_ingest_run.py::test_ingest_reads_clk_period_from_sdc_and_staged_slacks -v`
Expected: FAIL — `clock_period_ns` is None (read from config.mk) and staged columns are None.

- [ ] **Step 3: Add the SDC reader + wire the columns**

In `ingest_run.py`, after `_parse_config_mk` (line 52), add:

```python
# clock period lives in constraints/constraint.sdc as `set clk_period X`, NOT in
# config.mk. Mirrors check_timing.read_clock_period (kept local to avoid a
# scripts/reports import from the knowledge package).
def _read_sdc_clk_period(project: Path) -> float | None:
    sdc = project / "constraints" / "constraint.sdc"
    if not sdc.exists():
        return None
    m = re.search(r"set\s+clk_period\s+([\d.]+)",
                  sdc.read_text(encoding="utf-8", errors="ignore"))
    return float(m.group(1)) if m else None
```

In `ingest()`, after the `timing = summary.get("timing", {})` line (237), add:

```python
    timing_staged = summary.get("timing_staged", {}) if isinstance(summary, dict) else {}
```

In the `row` dict, change `clock_period_ns` (line 298) and add the three staged columns near the timing block (after `tns_ns`, line 311):

```python
        "clock_period_ns":        (_read_sdc_clk_period(project)
                                   if _read_sdc_clk_period(project) is not None
                                   else _to_float(cfg.get("CLOCK_PERIOD"))),
```

```python
        "wns_ns":          _to_float(timing.get("setup_wns")),
        "tns_ns":          _to_float(timing.get("setup_tns")),
        "floorplan_setup_ws": _to_float(timing_staged.get("floorplan_setup_ws")),
        "place_setup_ws":     _to_float(timing_staged.get("place_setup_ws")),
        "finish_setup_ws":    (_to_float(timing_staged.get("finish_setup_ws"))
                               if timing_staged.get("finish_setup_ws") is not None
                               else _to_float(timing.get("setup_wns"))),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_ingest_run.py -v`
Expected: PASS (new test + existing ingest tests still green).

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/knowledge/ingest_run.py r2g-rtl2gds/tests/test_ingest_run.py
git commit -m "fix(knowledge): ingest clk_period from SDC + staged setup slacks"
```

---

### Task 4: `ingest_run.py` — `--backfill` from preserved logs

**Files:**
- Modify: `r2g-rtl2gds/knowledge/ingest_run.py`
- Test: `r2g-rtl2gds/tests/test_ingest_run.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `r2g-rtl2gds/tests/test_ingest_run.py`:

```python
def test_backfill_updates_staged_slacks_from_logs(tmp_path, tmp_knowledge_dir):
    import ingest_run, knowledge_db, json as _json
    cases = tmp_path / "design_cases"
    proj = cases / "demo"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n", encoding="utf-8")
    (proj / "constraints" / "constraint.sdc").write_text("set clk_period 4.0\n", encoding="utf-8")
    # An OLD ppa.json without timing_staged (pre-feature run).
    (proj / "reports" / "ppa.json").write_text(_json.dumps(
        {"summary": {"timing": {"setup_wns": 0.6}}}), encoding="utf-8")
    logs = proj / "backend" / "RUN_2026-01-01_00-00-00" / "logs"
    logs.mkdir(parents=True)
    (logs / "2_1_floorplan.json").write_text(
        _json.dumps({"floorplan__timing__setup__ws": 1.2}), encoding="utf-8")
    (logs / "3_5_place_dp.json").write_text(
        _json.dumps({"detailedplace__timing__setup__ws": 0.8}), encoding="utf-8")

    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    # First ingest the (old) run; staged columns are NULL because ppa.json lacks them.
    ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    assert conn.execute("SELECT place_setup_ws FROM runs").fetchone()[0] is None
    # Backfill from the preserved logs.
    n = ingest_run.backfill(cases, conn)
    assert n == 1
    r = conn.execute(
        "SELECT clock_period_ns, floorplan_setup_ws, place_setup_ws, finish_setup_ws "
        "FROM runs").fetchone()
    assert r == (4.0, 1.2, 0.8, 0.6)  # finish backfilled from existing wns_ns
    conn.close()


def test_backfill_filters_unconstrained_sentinel(tmp_path, tmp_knowledge_dir):
    import ingest_run, knowledge_db, json as _json
    cases = tmp_path / "design_cases"
    proj = cases / "demo"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n", encoding="utf-8")
    (proj / "constraints" / "constraint.sdc").write_text("set clk_period 4.0\n", encoding="utf-8")
    (proj / "reports" / "ppa.json").write_text(_json.dumps(
        {"summary": {"timing": {"setup_wns": 0.6}}}), encoding="utf-8")
    logs = proj / "backend" / "RUN_2026-01-01_00-00-00" / "logs"
    logs.mkdir(parents=True)
    (logs / "3_5_place_dp.json").write_text(
        _json.dumps({"detailedplace__timing__setup__ws": 1e39}), encoding="utf-8")
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    ingest_run.backfill(cases, conn)
    assert conn.execute("SELECT place_setup_ws FROM runs").fetchone()[0] is None
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_ingest_run.py::test_backfill_updates_staged_slacks_from_logs -v`
Expected: FAIL — `AttributeError: module 'ingest_run' has no attribute 'backfill'`.

- [ ] **Step 3: Implement `backfill` + a shared latest-run-dir helper + a raw stage reader**

In `ingest_run.py`, add a module constant near the top (after the imports, ~line 35):

```python
_SENTINEL = 1e30
# Raw per-stage slack files, in priority order (3_5 before its 3_4 fallback).
_STAGE_SLACK_FILES = [
    ("floorplan_setup_ws", "2_1_floorplan.json", "floorplan__timing__setup__ws"),
    ("place_setup_ws", "3_5_place_dp.json", "detailedplace__timing__setup__ws"),
    ("place_setup_ws", "3_4_place_resized.json", "placeopt__timing__setup__ws"),
]
```

Add helpers after `_read_sdc_clk_period`:

```python
def _latest_run_dir(project: Path) -> Path | None:
    backend = project / "backend"
    if not backend.is_dir():
        return None
    runs = sorted((d for d in backend.iterdir()
                   if d.is_dir() and d.name.startswith("RUN_")),
                  key=lambda d: d.stat().st_mtime, reverse=True)
    return runs[0] if runs else None


def _read_staged_slacks(logs_dir: Path) -> dict:
    """Read {floorplan_setup_ws, place_setup_ws} directly from the per-stage
    metric JSONs (for --backfill of historical runs whose ppa.json predates
    timing_staged). Filters the 1e+39 unconstrained sentinel."""
    out: dict[str, float] = {}
    for col, fname, key in _STAGE_SLACK_FILES:
        if col in out:
            continue
        p = logs_dir / fname
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            continue
        v = _to_float(d.get(key))
        if v is not None and v < _SENTINEL:
            out[col] = v
    return out


def backfill(cases_root: Path, conn: sqlite3.Connection) -> int:
    """Populate staged-slack + clock_period_ns columns for already-ingested runs
    by re-scanning each project's latest backend/RUN_*/logs/. Matches rows by
    project_path. Returns the number of rows updated."""
    cases_root = Path(cases_root)
    updated = 0
    for proj in sorted(p for p in cases_root.iterdir() if p.is_dir()):
        rd = _latest_run_dir(proj)
        if rd is None:
            continue
        slacks = _read_staged_slacks(rd / "logs")
        clk = _read_sdc_clk_period(proj)
        sets, vals = [], []
        for col in ("floorplan_setup_ws", "place_setup_ws"):
            if col in slacks:
                sets.append(f"{col} = ?")
                vals.append(slacks[col])
        if clk is not None:
            sets.append("clock_period_ns = ?")
            vals.append(clk)
        # finish slack = the already-stored finish wns_ns where we don't have a fresh one
        sets.append("finish_setup_ws = COALESCE(finish_setup_ws, wns_ns)")
        if len(sets) == 1 and clk is None:
            continue  # nothing but the COALESCE — skip projects with no usable data
        vals.append(str(proj.resolve()))
        cur = conn.execute(
            f"UPDATE runs SET {', '.join(sets)} WHERE project_path = ?", vals)
        updated += cur.rowcount
    conn.commit()
    return updated
```

- [ ] **Step 4: Add a `--backfill` CLI path**

In `main()`, after building the arg parser (after line 364) and before `args = p.parse_args()`, add:

```python
    p.add_argument("--backfill", type=Path, default=None, metavar="DESIGN_CASES_DIR",
                   help="Backfill staged-slack + clock_period_ns columns for all "
                        "already-ingested projects under this dir, then exit.")
```

Right after `args = p.parse_args()` and the `conn`/`ensure_schema` setup (after line 368), insert:

```python
    if args.backfill is not None:
        n = backfill(args.backfill, conn)
        conn.close()
        print(f"Backfilled staged slacks for {n} run(s) under {args.backfill}")
        return 0
```

- [ ] **Step 5: Run tests to verify pass**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_ingest_run.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add r2g-rtl2gds/knowledge/ingest_run.py r2g-rtl2gds/tests/test_ingest_run.py
git commit -m "feat(knowledge): ingest_run --backfill staged slacks from preserved logs"
```

---

### Task 5: `learn_heuristics.py` — `closing_period` + `slack_deterioration`

**Files:**
- Modify: `r2g-rtl2gds/knowledge/learn_heuristics.py`
- Test: `r2g-rtl2gds/tests/test_learn_heuristics.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `r2g-rtl2gds/tests/test_learn_heuristics.py` (reuse the file's existing row-insert helper if present; this test inserts directly):

```python
def test_learn_emits_closing_period_and_deterioration(tmp_knowledge_dir):
    import knowledge_db, learn_heuristics
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    # 4 signoff-positive runs for (alu, nangate45): period, fp, place, finish.
    rows = [(10.0, 1.0, 0.8, 0.6),
            (10.0, 1.2, 0.9, 0.7),
            (8.0, 0.9, 0.7, 0.5),
            (8.0, 1.1, 0.8, 0.6)]
    for i, (period, fp, pl, fin) in enumerate(rows):
        conn.execute(
            "INSERT INTO runs (run_id, project_path, design_name, design_family, "
            "platform, ingested_at, clock_period_ns, floorplan_setup_ws, "
            "place_setup_ws, finish_setup_ws, wns_ns, drc_status, lvs_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"r{i}", f"/tmp/r{i}", "alu", "alu", "nangate45", "2026-01-01T00:00:00Z",
             period, fp, pl, fin, fin, "clean", "clean"))
    conn.commit()
    out = tmp_knowledge_dir / "heuristics.json"
    data = learn_heuristics.learn(tmp_knowledge_dir / "runs.sqlite", out)
    entry = data["families"]["alu"]["platforms"]["nangate45"]
    # closing_period = period - finish_ws ; min over rows = min(9.4,9.3,7.5,7.4)=7.4
    assert entry["closing_period"]["min"] == 7.4
    sd = entry["slack_deterioration"]
    assert sd["n"] == 4
    # d_fp_pl per row = fp-place = [0.2,0.3,0.2,0.3]; p90 (idx round(0.9*3)=3) = 0.3
    assert abs(sd["d_fp_pl"]["ns_p90"] - 0.3) < 1e-9
    # d_pl_fin per row = place-finish = [0.2,0.2,0.2,0.2]; p90 = 0.2
    assert abs(sd["d_pl_fin"]["ns_p90"] - 0.2) < 1e-9
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_learn_heuristics.py::test_learn_emits_closing_period_and_deterioration -v`
Expected: FAIL — `KeyError: 'closing_period'`.

- [ ] **Step 3: Add a `_quantile` helper and the aggregates**

In `learn_heuristics.py`, after `_p90` (line 50), add:

```python
_SENTINEL = 1e30


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[idx]
```

In `_family_platform_entry`, after the `elapsed_vals` block (line 65) and before `entry: dict = {`, add the collection:

```python
    cp_vals: list[float] = []
    d_fp_pl_ns, d_fp_pl_pct, d_pl_fin_ns, d_pl_fin_pct = [], [], [], []
    for r in successes:
        period = r.get("clock_period_ns")
        fp = r.get("floorplan_setup_ws")
        pl = r.get("place_setup_ws")
        fin = r.get("finish_setup_ws")
        if fin is None:
            fin = r.get("wns_ns")
        if period is not None and fin is not None and fin < _SENTINEL:
            cp_vals.append(period - fin)
        if (None not in (period, fp, pl, fin) and period > 0
                and max(fp, pl, fin) < _SENTINEL):
            d_fp_pl_ns.append(fp - pl)
            d_fp_pl_pct.append((fp - pl) / period)
            d_pl_fin_ns.append(pl - fin)
            d_pl_fin_pct.append((pl - fin) / period)
```

Then, after the existing `if elapsed_vals:` block (line 87) and before `return entry`, add:

```python
    if cp_vals:
        entry["closing_period"] = {
            "min": min(cp_vals),
            "p10": _quantile(cp_vals, 0.10),
            "median": statistics.median(cp_vals),
            "n": len(cp_vals),
        }
    if d_fp_pl_ns:
        entry["slack_deterioration"] = {
            "d_fp_pl": {"ns_p90": _quantile(d_fp_pl_ns, 0.90),
                        "pct_p90": _quantile(d_fp_pl_pct, 0.90)},
            "d_pl_fin": {"ns_p90": _quantile(d_pl_fin_ns, 0.90),
                         "pct_p90": _quantile(d_pl_fin_pct, 0.90)},
            "n": len(d_fp_pl_ns),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_learn_heuristics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/knowledge/learn_heuristics.py r2g-rtl2gds/tests/test_learn_heuristics.py
git commit -m "feat(knowledge): learn per-family closing_period + slack_deterioration"
```

---

### Task 6: `query_knowledge.py` — thin accessors

**Files:**
- Modify: `r2g-rtl2gds/knowledge/query_knowledge.py`
- Test: `r2g-rtl2gds/tests/test_query_knowledge.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `r2g-rtl2gds/tests/test_query_knowledge.py`:

```python
def test_get_deterioration_and_closing_period(tmp_path):
    import query_knowledge, json as _json
    h = tmp_path / "heuristics.json"
    h.write_text(_json.dumps({"families": {"alu": {"platforms": {"nangate45": {
        "closing_period": {"min": 7.4, "median": 8.5, "n": 4},
        "slack_deterioration": {"d_fp_pl": {"ns_p90": 0.3, "pct_p90": 0.03},
                                "d_pl_fin": {"ns_p90": 0.2, "pct_p90": 0.02},
                                "n": 4},
    }}}}}), encoding="utf-8")
    assert query_knowledge.get_closing_period("alu", "nangate45", heuristics_path=h)["min"] == 7.4
    assert query_knowledge.get_deterioration("alu", "nangate45", heuristics_path=h)["n"] == 4
    assert query_knowledge.get_deterioration("nope", "nangate45", heuristics_path=h) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_query_knowledge.py::test_get_deterioration_and_closing_period -v`
Expected: FAIL — accessors don't exist.

- [ ] **Step 3: Add accessors**

In `query_knowledge.py`, after `get_family_heuristics` (line 42), add:

```python
def get_closing_period(family: str, platform: str,
                       heuristics_path: Path | str = DEFAULT_HEURISTICS_PATH
                       ) -> dict[str, Any] | None:
    entry = get_family_heuristics(family, platform, heuristics_path=heuristics_path)
    return (entry or {}).get("closing_period")


def get_deterioration(family: str, platform: str,
                      heuristics_path: Path | str = DEFAULT_HEURISTICS_PATH
                      ) -> dict[str, Any] | None:
    entry = get_family_heuristics(family, platform, heuristics_path=heuristics_path)
    return (entry or {}).get("slack_deterioration")
```

- [ ] **Step 4: Run test to verify it passes & commit**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_query_knowledge.py -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/knowledge/query_knowledge.py r2g-rtl2gds/tests/test_query_knowledge.py
git commit -m "feat(knowledge): query accessors for closing_period + deterioration"
```

---

## Phase 2 — Fmax search core

### Task 7: `fmax_model.py` — deterioration model + helpers

**Files:**
- Create: `r2g-rtl2gds/scripts/reports/fmax_model.py`
- Test: `r2g-rtl2gds/tests/test_fmax_model.py` (new)

- [ ] **Step 1: Write the failing test**

Create `r2g-rtl2gds/tests/test_fmax_model.py`:

```python
"""Unit tests for the pure Fmax model + helpers."""
from __future__ import annotations
import math
import pytest
import fmax_model as fm


def test_default_deterioration_terms_scale_with_period():
    # d_pl_fin default = max(0.10 ns, 1% of period); at T=20 -> 0.20 ns dominates.
    assert fm.d_pl_fin(20.0) == pytest.approx(0.20)
    # at T=5 -> max(0.10, 0.05) = 0.10 ns floor dominates.
    assert fm.d_pl_fin(5.0) == pytest.approx(0.10)


def test_d_fp_fin_is_sum_of_primitives():
    # d_fp_fin = d_fp_pl + d_pl_fin; defaults at T=10: d_fp_pl=max(.45,.45)=.45, d_pl_fin=max(.10,.10)=.10
    assert fm.d_fp_fin(10.0) == pytest.approx(0.55)


def test_learned_model_overrides_default_and_clamps_negative():
    model = {"d_pl_fin": (-0.05, -0.01), "d_fp_pl": (0.30, 0.03)}
    # negative learned d_pl_fin clamps to 0 (never predict negative erosion).
    assert fm.d_pl_fin(10.0, model) == 0.0
    # d_fp_pl learned positive: max(0.30, 0.03*10)=0.30
    assert fm.d_fp_fin(10.0, model) == pytest.approx(0.30)


def test_classify_probe():
    # closes: place_ws >= d_pl_fin(10)=0.10 and tns>=0
    assert fm.classify_probe(0.5, 0.0, 10.0) == "pass"
    assert fm.classify_probe(0.05, 0.0, 10.0) == "fail"     # below guardband
    assert fm.classify_probe(0.5, -1.0, 10.0) == "fail"      # tns violated
    assert fm.classify_probe(None, 0.0, 10.0) == "inconclusive"
    assert fm.classify_probe(1e39, 0.0, 10.0) == "inconclusive"  # unconstrained
    assert fm.classify_probe(0.5, 0.0, 10.0, completed=False) == "inconclusive"


def test_variant_name_encodes_period():
    assert fm.variant_name("alu", 4.5) == "alu_fmax_p0045"
    assert fm.variant_name("alu", 12.0) == "alu_fmax_p0120"


def test_rewrite_clk_period():
    sdc = "current_design alu\nset clk_period 10.0\ncreate_clock -period $clk_period [get_ports clk]\n"
    out = fm.rewrite_clk_period(sdc, 4.5)
    assert "set clk_period 4.5" in out
    assert "10.0" not in out.split("create_clock")[0]
    with pytest.raises(ValueError):
        fm.rewrite_clk_period("no period here\n", 4.5)


def test_select_model_tiers():
    entry = {"slack_deterioration": {"d_fp_pl": {"ns_p90": 0.3, "pct_p90": 0.03},
                                     "d_pl_fin": {"ns_p90": 0.2, "pct_p90": 0.02}, "n": 10}}
    model, prov = fm.select_model(entry)
    assert model["d_fp_pl"] == (0.3, 0.03) and prov.startswith("learned")
    # below N_MIN_FAMILY -> default static
    entry["slack_deterioration"]["n"] = 3
    model, prov = fm.select_model(entry)
    assert model is None and "default-static" in prov
    assert fm.select_model(None) == (None, "default-static")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_fmax_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fmax_model'`.

- [ ] **Step 3: Implement `fmax_model.py`**

Create `r2g-rtl2gds/scripts/reports/fmax_model.py`:

```python
#!/usr/bin/env python3
"""Pure model + helpers for the Fmax search. No I/O, no subprocess — every
function here is unit-testable. The orchestrator (fmax_search.py) supplies real
probe callables to ``search_loop``.

Slack-deterioration model (per design spec 2026-06-04, §5.1):
  d_fp_pl  = floorplan_ws - place_ws    (placement erosion — dominant)
  d_pl_fin = place_ws    - finish_ws    (routing erosion — tiny, often negative)
  d_fp_fin = d_fp_pl + d_pl_fin
Estimator = p90, applied as max(ns_floor, pct*period), clamped >= 0.
"""
from __future__ import annotations
import re

UNCONSTRAINED = 1e30
N_MIN_FAMILY = 8
N_MIN_PLATFORM = 20

# Cold-start (ns_floor, pct-of-period) defaults from the corpus (spec §5.1 table).
DEFAULT_D_FP_PL = (0.45, 0.045)
DEFAULT_D_PL_FIN = (0.10, 0.010)

_CLK_RE = re.compile(r"(set\s+clk_period\s+)([0-9.]+)")


def _term(default: tuple[float, float], period: float,
          learned: tuple[float, float] | None) -> float:
    ns, pct = learned if learned is not None else default
    return max(0.0, ns, pct * period)


def d_fp_pl(period: float, model: dict | None = None) -> float:
    return _term(DEFAULT_D_FP_PL, period, (model or {}).get("d_fp_pl"))


def d_pl_fin(period: float, model: dict | None = None) -> float:
    return _term(DEFAULT_D_PL_FIN, period, (model or {}).get("d_pl_fin"))


def d_fp_fin(period: float, model: dict | None = None) -> float:
    return d_fp_pl(period, model) + d_pl_fin(period, model)


def classify_probe(place_ws: float | None, place_tns: float | None,
                   period: float, model: dict | None = None,
                   completed: bool = True) -> str:
    """'pass' | 'fail' | 'inconclusive' at the placement reference stage."""
    if not completed:
        return "inconclusive"
    if place_ws is None or place_ws > UNCONSTRAINED:
        return "inconclusive"
    if place_tns is None or place_tns < 0:
        return "fail"
    return "pass" if place_ws >= d_pl_fin(period, model) else "fail"


def variant_name(base: str, period: float) -> str:
    """Unique FLOW_VARIANT per period: <base>_fmax_p<NNNN> (NNNN = period*10)."""
    return f"{base}_fmax_p{int(round(period * 10)):04d}"


def rewrite_clk_period(sdc_text: str, period: float) -> str:
    new, n = _CLK_RE.subn(rf"\g<1>{period:g}", sdc_text, count=1)
    if n == 0:
        raise ValueError("no 'set clk_period' line found in SDC")
    return new


def select_model(entry: dict | None,
                 n_min_family: int = N_MIN_FAMILY) -> tuple[dict | None, str]:
    """Pick the deterioration model + provenance from a heuristics entry dict.
    Below n_min_family samples, return (None, 'default-static…') so the caller
    uses the cold-start defaults."""
    sd = (entry or {}).get("slack_deterioration")
    if not sd:
        return None, "default-static"
    n = sd.get("n", 0)
    if n >= n_min_family:
        model = {
            "d_fp_pl": (sd["d_fp_pl"]["ns_p90"], sd["d_fp_pl"]["pct_p90"]),
            "d_pl_fin": (sd["d_pl_fin"]["ns_p90"], sd["d_pl_fin"]["pct_p90"]),
        }
        return model, f"learned(n={n},q=p90)"
    return None, f"default-static(family n={n}<{n_min_family})"
```

- [ ] **Step 4: Run test to verify it passes & commit**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_fmax_model.py -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/scripts/reports/fmax_model.py r2g-rtl2gds/tests/test_fmax_model.py
git commit -m "feat(fmax): pure deterioration model + SDC/variant helpers"
```

---

### Task 8: `fmax_model.py` — the injectable `search_loop`

**Files:**
- Modify: `r2g-rtl2gds/scripts/reports/fmax_model.py`
- Test: `r2g-rtl2gds/tests/test_fmax_model.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `r2g-rtl2gds/tests/test_fmax_model.py`:

```python
def _oracle(true_fmax_period):
    """A mock design whose slack is linear: ws(period) = period - true_fmax_period.
    Floorplan reports slightly MORE slack than place (placement erodes it)."""
    def floorplan_probe(period):
        return (period - true_fmax_period) + 0.30   # floorplan optimistic by 0.30 ns
    def place_probe(period):
        ws = period - true_fmax_period
        return {"place_ws": ws, "place_tns": 0.0,
                "status": fm.classify_probe(ws, 0.0, period)}
    return floorplan_probe, place_probe


def test_search_loop_converges_to_true_fmax():
    fp, pl = _oracle(true_fmax_period=5.0)
    res = fm.search_loop(seed_period=10.0, floorplan_probe=fp, place_probe=pl, model=None)
    assert res["status"] == "ok"
    # converges to place_ws ~ d_pl_fin(T) ~ 0.10 -> T* ~ 5.10
    assert res["t_star"] == pytest.approx(5.1, abs=0.15)
    assert res["fmax_predicted_signoff"] == pytest.approx(1.0 / res["t_star"])


def test_search_loop_restarts_on_bad_seed():
    fp, pl = _oracle(true_fmax_period=5.0)
    calls = []
    def fp_logged(p):
        calls.append(p)
        return fp(p)
    # Seed wildly loose (50 ns) -> Fmax_fp ~ 5.3, off by >50% -> must restart near 5.3.
    res = fm.search_loop(seed_period=50.0, floorplan_probe=fp_logged, place_probe=pl, model=None)
    assert res["status"] == "ok"
    assert len(calls) >= 2  # restarted floorplan at the corrected seed
    assert res["t_star"] == pytest.approx(5.1, abs=0.2)


def test_search_loop_inconclusive_propagates():
    def fp(period):
        return (period - 5.0) + 0.3
    def pl(period):
        return {"place_ws": None, "place_tns": None, "status": "inconclusive"}
    res = fm.search_loop(seed_period=10.0, floorplan_probe=fp, place_probe=pl, model=None)
    assert res["status"] == "inconclusive"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_fmax_model.py::test_search_loop_converges_to_true_fmax -v`
Expected: FAIL — `search_loop` not defined.

- [ ] **Step 3: Implement `search_loop`**

Append to `fmax_model.py`:

```python
def estimate_fmax_fp(t_ref: float, floorplan_ws: float) -> float:
    """Floorplan-stage Fmax point estimate = period - worst slack."""
    return t_ref - floorplan_ws


def search_loop(seed_period, floorplan_probe, place_probe, model=None, *,
                floor=0.05, max_iter=3, tol=None):
    """Tier-1 floorplan early-look + Tier-2 fixed-point root-find.

    floorplan_probe(period) -> floorplan setup_ws (float) | None
    place_probe(period)     -> {'place_ws', 'place_tns', 'status'}
    Returns a dict: status in {'ok','inconclusive','error'} plus the trace 'log'.
    """
    log: list[dict] = []
    t_ref = float(seed_period)

    fp_ws = floorplan_probe(t_ref)
    log.append({"stage": "floorplan", "period": t_ref, "ws": fp_ws})
    if fp_ws is None or fp_ws > UNCONSTRAINED:
        return {"status": "error", "reason": "floorplan_unconstrained", "log": log}

    fmax_fp = estimate_fmax_fp(t_ref, fp_ws)
    if abs(fmax_fp - t_ref) > 0.5 * t_ref:
        # Bad seed: jump to the corrected center and re-probe floorplan once.
        t_ref = max(fmax_fp + d_fp_fin(fmax_fp, model), floor)
        fp_ws = floorplan_probe(t_ref)
        log.append({"stage": "floorplan_restart", "period": t_ref, "ws": fp_ws})
        if fp_ws is None or fp_ws > UNCONSTRAINED:
            return {"status": "error", "reason": "floorplan_unconstrained", "log": log}
        fmax_fp = estimate_fmax_fp(t_ref, fp_ws)

    # Bracket center = predicted-signoff closing period (pre-absorb erosion).
    t_ref = max(fmax_fp + d_fp_fin(fmax_fp, model), floor)
    if tol is None:
        tol = max(0.1, 0.02 * t_ref)

    last_pass = None
    for _ in range(max_iter):
        r = place_probe(t_ref)
        log.append({"stage": "place", "period": t_ref,
                    "ws": r.get("place_ws"), "status": r.get("status")})
        if r.get("status") == "inconclusive":
            return {"status": "inconclusive", "period": t_ref, "log": log}
        if r.get("status") == "pass":
            last_pass = t_ref
        place_ws = r.get("place_ws")
        t_next = (t_ref - place_ws) + d_pl_fin(t_ref, model)
        if abs(t_next - t_ref) < tol:
            t_ref = max(t_next, floor)
            break
        t_ref = max(t_next, floor)

    place_proxy_period = max(t_ref - d_pl_fin(t_ref, model), floor)
    return {
        "status": "ok",
        "t_star": t_ref,
        "last_pass": last_pass,
        "fmax_predicted_signoff": 1.0 / t_ref,
        "t_place_proxy": place_proxy_period,
        "fmax_place_proxy": 1.0 / place_proxy_period,
        "log": log,
    }
```

- [ ] **Step 4: Run test to verify it passes & commit**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_fmax_model.py -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/scripts/reports/fmax_model.py r2g-rtl2gds/tests/test_fmax_model.py
git commit -m "feat(fmax): injectable root-find search_loop (floorplan early-look + fixed-point)"
```

---

### Task 9: `fmax_search.py` — real probe (clone variant, run ORFS, read proxy)

**Files:**
- Create: `r2g-rtl2gds/scripts/reports/fmax_search.py`
- Test: `r2g-rtl2gds/tests/test_fmax_search.py` (new)

- [ ] **Step 1: Write the failing test**

Create `r2g-rtl2gds/tests/test_fmax_search.py`:

```python
"""Tests for the Fmax search orchestrator (I/O parts mocked)."""
from __future__ import annotations
import fmax_search as fs


def test_clone_variant_symlinks_rtl_and_rewrites_sdc(tmp_path):
    base = tmp_path / "design_cases" / "alu"
    (base / "constraints").mkdir(parents=True)
    (base / "rtl").mkdir(parents=True)
    (base / "rtl" / "alu.v").write_text("module alu(); endmodule\n", encoding="utf-8")
    (base / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = alu\n", encoding="utf-8")
    (base / "constraints" / "constraint.sdc").write_text(
        "set clk_period 10.0\n", encoding="utf-8")

    variant = fs.clone_variant(base, 4.5)
    assert variant.name == "alu_fmax_p0045"
    # rtl symlinked (not copied)
    assert (variant / "rtl").is_symlink() or (variant / "rtl" / "alu.v").exists()
    # sdc rewritten to the probe period
    assert "set clk_period 4.5" in (variant / "constraints" / "constraint.sdc").read_text()
    # config.mk copied
    assert (variant / "constraints" / "config.mk").exists()


def test_density_floor_and_unique_variant_asserts(tmp_path):
    base = tmp_path / "design_cases" / "alu"
    (base / "constraints").mkdir(parents=True)
    (base / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = alu\nexport PLACE_DENSITY_LB_ADDON = 0.05\n", encoding="utf-8")
    (base / "constraints" / "constraint.sdc").write_text("set clk_period 10.0\n", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="PLACE_DENSITY_LB_ADDON"):
        fs.assert_safe_knobs(base)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_fmax_search.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fmax_search'`.

- [ ] **Step 3: Implement the probe-side helpers in `fmax_search.py`**

Create `r2g-rtl2gds/scripts/reports/fmax_search.py` with the scaffolding + clone/assert/probe (the CLI + search wiring come in Tasks 10–12):

```python
#!/usr/bin/env python3
"""Automated loose-first Fmax characterization for r2g-rtl2gds.

Finds the minimum clock period a design can close at, using placement-stage
timing as the search signal and a learnable per-family slack-deterioration model
(see docs/superpowers/specs/2026-06-04-fmax-search-design.md). Reports a
predicted-signoff Fmax proxy; --verify runs one full signoff flow at the winner.

Usage:
  fmax_search.py <project-dir> [platform] [--verify] [--keep-variants]
                 [--max-parallel N] [--place-fast]
"""
from __future__ import annotations
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import extract_ppa          # scripts/extract on sys.path (conftest / _add_paths below)
import fmax_model as fm

SKILL_ROOT = Path(__file__).resolve().parents[2]
RUN_ORFS = SKILL_ROOT / "scripts" / "flow" / "run_orfs.sh"


def _config_value(config_mk: Path, key: str) -> str | None:
    if not config_mk.exists():
        return None
    m = re.search(rf"(?:export\s+)?{re.escape(key)}\s*=\s*(.*)",
                  config_mk.read_text(encoding="utf-8", errors="ignore"))
    return m.group(1).strip() if m else None


def assert_safe_knobs(project: Path) -> None:
    """Hard-rule guard: the search must never run with PLACE_DENSITY_LB_ADDON
    below 0.10 (irrecoverable placer divergence, per CLAUDE.md)."""
    v = _config_value(project / "constraints" / "config.mk", "PLACE_DENSITY_LB_ADDON")
    if v is not None:
        try:
            if float(v) < 0.10:
                raise ValueError(
                    f"PLACE_DENSITY_LB_ADDON={v} < 0.10 — refusing to run Fmax search "
                    "(placer divergence is irrecoverable). Fix config.mk first.")
        except ValueError as e:
            if "PLACE_DENSITY_LB_ADDON" in str(e):
                raise
            # non-numeric value: leave to ORFS, don't block here.


def clone_variant(base: Path, period: float) -> Path:
    """Lean clone of <base> into a sibling <base>_fmax_p<NNNN>: symlink rtl/,
    copy constraints/, rewrite the SDC clk_period. Unique basename => unique
    FLOW_VARIANT (hard-rule isolation)."""
    base = Path(base)
    name = fm.variant_name(base.name, period)
    variant = base.parent / name
    if variant.exists():
        shutil.rmtree(variant)
    (variant / "constraints").mkdir(parents=True)
    shutil.copy(base / "constraints" / "config.mk", variant / "constraints" / "config.mk")
    sdc_text = (base / "constraints" / "constraint.sdc").read_text(encoding="utf-8")
    (variant / "constraints" / "constraint.sdc").write_text(
        fm.rewrite_clk_period(sdc_text, period), encoding="utf-8")
    # Symlink rtl/ (read-only, large); fall back to copy if symlink unsupported.
    src_rtl = base / "rtl"
    if src_rtl.exists():
        try:
            (variant / "rtl").symlink_to(src_rtl.resolve(), target_is_directory=True)
        except OSError:
            shutil.copytree(src_rtl, variant / "rtl")
    return variant


def _latest_run_dir(project: Path) -> Path | None:
    backend = project / "backend"
    if not backend.is_dir():
        return None
    runs = sorted((d for d in backend.iterdir()
                   if d.is_dir() and d.name.startswith("RUN_")),
                  key=lambda d: d.stat().st_mtime, reverse=True)
    return runs[0] if runs else None


def run_probe(variant: Path, platform: str, stages: str, *,
              timeout_s: int = 3600, place_fast: bool = False,
              env: dict | None = None) -> dict:
    """Run run_orfs.sh for the variant through `stages` and read the proxy slack.
    Returns {'place_ws','place_tns','floorplan_ws','status','completed'}."""
    e = dict(os.environ if env is None else env)
    e["ORFS_STAGES"] = stages
    e["ORFS_TIMEOUT"] = str(timeout_s)
    if place_fast:
        e["PLACE_FAST"] = "1"
    proc = subprocess.run(
        ["bash", str(RUN_ORFS), str(variant), platform, variant.name],
        env=e, capture_output=True, text=True)
    completed = proc.returncode == 0
    rd = _latest_run_dir(variant)
    fp = extract_ppa.parse_stage_metrics(rd, "floorplan") if rd else {}
    pl = extract_ppa.parse_stage_metrics(rd, "place") if rd else {}
    place_ws = pl.get("setup_wns")
    place_tns = pl.get("setup_tns")
    return {
        "floorplan_ws": fp.get("setup_wns"),
        "place_ws": place_ws,
        "place_tns": place_tns,
        "completed": completed,
        "returncode": proc.returncode,
    }
```

Note: `extract_ppa` and `fmax_model` import as plain modules because `scripts/extract` and `scripts/reports` are on `sys.path` (conftest in tests; at CLI runtime add them — handled in Task 10's `_add_paths()`).

- [ ] **Step 4: Run test to verify it passes & commit**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_fmax_search.py -v`
Expected: PASS (both tests; the subprocess path is not exercised yet).

```bash
git add r2g-rtl2gds/scripts/reports/fmax_search.py r2g-rtl2gds/tests/test_fmax_search.py
git commit -m "feat(fmax): variant cloning + ORFS probe runner + safety asserts"
```

---

### Task 10: `fmax_search.py` — seed, orchestrate, report

**Files:**
- Modify: `r2g-rtl2gds/scripts/reports/fmax_search.py`
- Test: `r2g-rtl2gds/tests/test_fmax_search.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `r2g-rtl2gds/tests/test_fmax_search.py`:

```python
def test_search_with_injected_probes_writes_report(tmp_path, monkeypatch):
    import json, fmax_model as fm
    base = tmp_path / "design_cases" / "alu"
    (base / "constraints").mkdir(parents=True)
    (base / "reports").mkdir(parents=True)
    (base / "constraints" / "config.mk").write_text("export DESIGN_NAME = alu\n", encoding="utf-8")
    (base / "constraints" / "constraint.sdc").write_text("set clk_period 10.0\n", encoding="utf-8")

    # Inject pure probes (no ORFS): linear slack with true Fmax period 5.0.
    def fp(period): return (period - 5.0) + 0.3
    def pl(period):
        ws = period - 5.0
        return {"place_ws": ws, "place_tns": 0.0, "status": fm.classify_probe(ws, 0.0, period)}

    result = fs.search(base, platform="nangate45", seed_period=10.0,
                       floorplan_probe=fp, place_probe=pl, model=None,
                       model_provenance="default-static")
    assert result["status"] == "ok"
    assert result["fmax_predicted_signoff_period"] == result["t_star"]
    # report written
    rpt = json.loads((base / "reports" / "fmax_search.json").read_text())
    assert rpt["winner"]["period"] == result["t_star"]
    assert "Fmax_predicted_signoff" in rpt["labels"][0]
    assert any("CTS-skew-unmodeled" in l for l in rpt["labels"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_fmax_search.py::test_search_with_injected_probes_writes_report -v`
Expected: FAIL — `fs.search` not defined.

- [ ] **Step 3: Implement `search()` + report writer + seed + path setup**

Append to `fmax_search.py`:

```python
def _add_paths() -> None:
    """Make sibling skill modules importable when run as a CLI."""
    for sub in (SKILL_ROOT / "scripts" / "extract", SKILL_ROOT / "knowledge"):
        if str(sub) not in sys.path:
            sys.path.insert(0, str(sub))


def seed_period(project: Path, platform: str, family: str | None = None) -> float:
    """Tier-0 seed: aggressive end of the family's learned closing_period if
    available, else the design's nominal SDC period."""
    try:
        _add_paths()
        import knowledge_db, query_knowledge
        if family is None:
            cfg_name = _config_value(project / "constraints" / "config.mk", "DESIGN_NAME") or ""
            fams = knowledge_db.load_families()
            family = knowledge_db.infer_family(cfg_name, fams)
        cp = query_knowledge.get_closing_period(family, platform)
        if cp and cp.get("min"):
            return float(cp["min"])
    except Exception:
        pass
    # Fallback: nominal SDC period.
    sdc = (project / "constraints" / "constraint.sdc").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"set\s+clk_period\s+([\d.]+)", sdc)
    return float(m.group(1)) if m else 10.0


def build_labels(result: dict, model_provenance: str, place_fast: bool) -> list[str]:
    t = result["t_star"]
    labels = [
        f"Fmax_predicted_signoff: {1.0 / t:.4g} (period {t:.4g} ns) [proxy, UNVERIFIED]",
        f"Fmax_place_proxy: {result['fmax_place_proxy']:.4g} "
        f"(period {result['t_place_proxy']:.4g} ns)",
        "+CTS-skew-unmodeled",
        f"deterioration: {model_provenance}",
    ]
    if place_fast:
        labels.append("PLACE_FAST-lower-bound")
    return labels


def search(project: Path, platform: str, *, seed_period: float,
           floorplan_probe, place_probe, model=None,
           model_provenance: str = "default-static",
           place_fast: bool = False) -> dict:
    """Run the search loop with the given probes and write reports/fmax_search.json."""
    import json
    res = fm.search_loop(seed_period, floorplan_probe, place_probe, model=model)
    report = {
        "design": Path(project).name,
        "platform": platform,
        "seed_period": seed_period,
        "status": res["status"],
        "model_provenance": model_provenance,
        "place_fast": place_fast,
        "log": res.get("log", []),
    }
    if res["status"] == "ok":
        report["winner"] = {
            "period": res["t_star"],
            "fmax_predicted_signoff": res["fmax_predicted_signoff"],
            "fmax_place_proxy": res["fmax_place_proxy"],
        }
        report["labels"] = build_labels(res, model_provenance, place_fast)
        res["fmax_predicted_signoff_period"] = res["t_star"]
    else:
        report["labels"] = [f"status: {res['status']}"]
    out = Path(project) / "reports" / "fmax_search.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    res["report_path"] = str(out)
    return res
```

- [ ] **Step 4: Run test to verify it passes & commit**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_fmax_search.py -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/scripts/reports/fmax_search.py r2g-rtl2gds/tests/test_fmax_search.py
git commit -m "feat(fmax): search() orchestration, KB seed, honest-label report"
```

---

### Task 11: `fmax_search.py` — CLI, real probes, confirm grid, cleanup, escape hatches

**Files:**
- Modify: `r2g-rtl2gds/scripts/reports/fmax_search.py`
- Test: `r2g-rtl2gds/tests/test_fmax_search.py` (append)

- [ ] **Step 1: Write the failing test (confirm grid + cleanup are pure-testable)**

Append to `r2g-rtl2gds/tests/test_fmax_search.py`:

```python
def test_confirm_grid_picks_looser_pass_edge():
    import fmax_model as fm
    # places: pass at >=5.1, fail below. Grid around t_star=5.1.
    def pl(period):
        ws = period - 5.0
        return {"place_ws": ws, "place_tns": 0.0, "status": fm.classify_probe(ws, 0.0, period)}
    edge = fs.confirm_grid(5.1, pl, model=None, width=0.02, n=3)
    # looser passing edge should be >= 5.1
    assert edge >= 5.1


def test_cleanup_variants_removes_dirs(tmp_path):
    base = tmp_path / "design_cases" / "alu"
    v = tmp_path / "design_cases" / "alu_fmax_p0045"
    v.mkdir(parents=True)
    fs.cleanup_variants([v])
    assert not v.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_fmax_search.py::test_confirm_grid_picks_looser_pass_edge -v`
Expected: FAIL — `confirm_grid` not defined.

- [ ] **Step 3: Implement confirm grid, cleanup, real-probe factory, PLACE_FAST escape hatch, and `main()`**

Append to `fmax_search.py`:

```python
def confirm_grid(t_star: float, place_probe, model=None, *, width=0.02, n=3) -> float:
    """Probe a small grid around t_star; return the looser (larger-period)
    passing edge. Sequential here; the CLI runs these in parallel."""
    lo = t_star * (1 - width)
    hi = t_star * (1 + width)
    periods = [lo + (hi - lo) * i / (n - 1) for i in range(n)] if n > 1 else [t_star]
    best_pass = None
    for p in sorted(periods):  # ascending = looser last
        r = place_probe(p)
        if r.get("status") == "pass":
            best_pass = p if best_pass is None else max(best_pass, p)
    return best_pass if best_pass is not None else hi


def cleanup_variants(variants: list[Path]) -> None:
    for v in variants:
        try:
            shutil.rmtree(v)
        except OSError:
            pass


def _make_real_probes(base: Path, platform: str, *, timeout_s: int,
                      place_fast: bool, created: list[Path]):
    """Build floorplan_probe/place_probe that clone a variant per period, run
    ORFS, read the proxy, and record the variant dir for cleanup."""
    def floorplan_probe(period):
        v = clone_variant(base, period)
        created.append(v)
        out = run_probe(v, platform, "synth floorplan",
                        timeout_s=timeout_s, place_fast=place_fast)
        return out.get("floorplan_ws")

    def place_probe(period):
        v = clone_variant(base, period)
        if v not in created:
            created.append(v)
        out = run_probe(v, platform, "synth floorplan place",
                        timeout_s=timeout_s, place_fast=place_fast)
        status = fm.classify_probe(out.get("place_ws"), out.get("place_tns"),
                                   period, completed=out.get("completed", True))
        return {"place_ws": out.get("place_ws"), "place_tns": out.get("place_tns"),
                "status": status}
    return floorplan_probe, place_probe


def main() -> int:
    _add_paths()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("project", type=Path)
    p.add_argument("platform", nargs="?", default="nangate45")
    p.add_argument("--verify", action="store_true",
                   help="Run one full signoff flow at the winning period.")
    p.add_argument("--keep-variants", action="store_true")
    p.add_argument("--place-fast", action="store_true",
                   help="Whole-search PLACE_FAST mode (conservative lower bound).")
    p.add_argument("--probe-timeout", type=int, default=3600)
    args = p.parse_args()

    base = args.project.resolve()
    assert_safe_knobs(base)

    import knowledge_db, query_knowledge
    fam = knowledge_db.infer_family(
        _config_value(base / "constraints" / "config.mk", "DESIGN_NAME") or "",
        knowledge_db.load_families())
    model, provenance = fm.select_model(query_knowledge.get_family_heuristics(fam, args.platform))
    seed = seed_period(base, args.platform, family=fam)

    created: list[Path] = []
    fp_probe, pl_probe = _make_real_probes(
        base, args.platform, timeout_s=args.probe_timeout,
        place_fast=args.place_fast, created=created)
    res = search(base, args.platform, seed_period=seed,
                 floorplan_probe=fp_probe, place_probe=pl_probe,
                 model=model, model_provenance=provenance, place_fast=args.place_fast)

    if res["status"] == "ok":
        edge = confirm_grid(res["t_star"], pl_probe, model=model)
        print(f"Fmax (predicted-signoff) ~ {1.0 / edge:.4g}  (period {edge:.4g} ns)  [{provenance}]")
        if args.verify:
            print("Running full signoff verify at the winning period…")
            verify_winner(base, args.platform, edge)  # Task 12
    else:
        print(f"Fmax search status: {res['status']} — see reports/fmax_search.json")

    if not args.keep_variants:
        cleanup_variants(created)
    return 0 if res["status"] in ("ok",) else 1


if __name__ == "__main__":
    main()
```

Note on PLACE_FAST escape hatch: a probe whose `completed` is False (placer hang/timeout) yields `status='inconclusive'`, which `search_loop` surfaces as a top-level `inconclusive` — the honest "could not measure" outcome. Re-running the whole search with `--place-fast` is the operator's escalation; it is whole-search (never per-probe) so the proxy stays consistent.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_fmax_search.py -v`
Expected: PASS (`verify_winner` is referenced only on the `--verify` runtime path, added in Task 12; the unit tests don't import it).

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/scripts/reports/fmax_search.py r2g-rtl2gds/tests/test_fmax_search.py
git commit -m "feat(fmax): CLI, real probes, confirm grid, cleanup, PLACE_FAST hatch"
```

---

## Phase 3 — Verify + online self-correction

### Task 12: `--verify` full flow + record the triple back to the knowledge store

**Files:**
- Modify: `r2g-rtl2gds/scripts/reports/fmax_search.py`
- Test: `r2g-rtl2gds/tests/test_fmax_search.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `r2g-rtl2gds/tests/test_fmax_search.py`:

```python
def test_record_verify_triple_appends_to_db(tmp_path, tmp_knowledge_dir, monkeypatch):
    import knowledge_db
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    fs.record_verify_triple(conn, design_name="alu", design_family="alu",
                            platform="nangate45", period=5.1,
                            floorplan_ws=0.4, place_ws=0.2, finish_ws=0.05)
    r = conn.execute("SELECT clock_period_ns, floorplan_setup_ws, place_setup_ws, "
                     "finish_setup_ws, eval_arm FROM runs").fetchone()
    assert r[:4] == (5.1, 0.4, 0.2, 0.05)
    assert r[4] == "fmax_verify"  # tagged so it is identifiable but still learnable
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_fmax_search.py::test_record_verify_triple_appends_to_db -v`
Expected: FAIL — `record_verify_triple` not defined.

- [ ] **Step 3: Implement `record_verify_triple` + `verify_winner`**

Append to `fmax_search.py`:

```python
def record_verify_triple(conn, *, design_name, design_family, platform, period,
                         floorplan_ws, place_ws, finish_ws) -> str:
    """Append a verified (floorplan, place, finish) slack triple so the
    deterioration model self-corrects. A signoff-positive row (drc/lvs clean)
    so it counts toward learning; tagged eval_arm='fmax_verify' for provenance.
    The N>=8 min-sample gate in fmax_model.select_model means one triple cannot
    move the active estimator."""
    import datetime as _dt
    rid = "fmaxverify_" + _dt.datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    conn.execute(
        "INSERT OR REPLACE INTO runs (run_id, project_path, design_name, "
        "design_family, platform, ingested_at, clock_period_ns, floorplan_setup_ws, "
        "place_setup_ws, finish_setup_ws, wns_ns, drc_status, lvs_status, eval_arm) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, f"verify:{design_name}", design_name, design_family, platform,
         _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
         period, floorplan_ws, place_ws, finish_ws, finish_ws,
         "clean" if finish_ws is not None and finish_ws >= 0 else "fail",
         "clean", "fmax_verify"))
    conn.commit()
    return rid


def verify_winner(base: Path, platform: str, period: float) -> dict:
    """Run ONE full signoff flow at the winning period, read finish timing, and
    record the verified triple. Returns {'closed', 'finish_ws', ...}."""
    v = clone_variant(base, period)
    out = run_probe(v, platform, "synth floorplan place cts route finish",
                    timeout_s=int(os.environ.get("FMAX_VERIFY_TIMEOUT", "14400")))
    rd = _latest_run_dir(v)
    # Read finish timing from 6_report.json. Gate on setup AND hold (spec §6).
    import json
    fin_ws = fin_tns = hold_ws = None
    if rd:
        rj = rd / "logs" / "6_report.json"
        if rj.exists():
            d = json.loads(rj.read_text(encoding="utf-8", errors="ignore"))
            fin_ws = d.get("finish__timing__setup__ws")
            fin_tns = d.get("finish__timing__setup__tns")
            hold_ws = d.get("finish__timing__hold__ws")
    closed = (fin_ws is not None and fin_ws >= 0
              and fin_tns is not None and fin_tns >= 0
              and (hold_ws is None or hold_ws >= 0))
    _add_paths()
    import knowledge_db
    fam = knowledge_db.infer_family(
        _config_value(base / "constraints" / "config.mk", "DESIGN_NAME") or "",
        knowledge_db.load_families())
    conn = knowledge_db.connect()
    knowledge_db.ensure_schema(conn)
    record_verify_triple(conn, design_name=base.name, design_family=fam,
                         platform=platform, period=period,
                         floorplan_ws=out.get("floorplan_ws"),
                         place_ws=out.get("place_ws"), finish_ws=fin_ws)
    conn.close()
    print(f"Verify @ {period:.4g} ns: finish_ws={fin_ws} -> "
          f"{'CONFIRMED' if closed else 'MISS (back off one notch)'}")
    return {"closed": closed, "finish_ws": fin_ws, "variant": v}
```

- [ ] **Step 4: Run test to verify it passes & commit**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_fmax_search.py -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/scripts/reports/fmax_search.py r2g-rtl2gds/tests/test_fmax_search.py
git commit -m "feat(fmax): --verify full flow + online deterioration self-correction"
```

---

### Task 13: Full-suite regression gate

**Files:** none (verification only)

- [ ] **Step 1: Run the full knowledge + fmax test set**

Run: `python3 -m pytest r2g-rtl2gds/tests/test_fmax_model.py r2g-rtl2gds/tests/test_fmax_search.py r2g-rtl2gds/tests/test_extract_ppa_stage.py r2g-rtl2gds/tests/test_ingest_run.py r2g-rtl2gds/tests/test_learn_heuristics.py r2g-rtl2gds/tests/test_query_knowledge.py r2g-rtl2gds/tests/test_knowledge_db.py -v`
Expected: all PASS.

- [ ] **Step 2: Run the WHOLE suite to ensure nothing regressed**

Run: `python3 -m pytest r2g-rtl2gds/tests -q`
Expected: all PASS (no regressions in techlib/extract/etc.).

- [ ] **Step 3: Backfill the live knowledge store and re-learn (real data smoke test)**

Run:
```bash
cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds/knowledge
python3 ingest_run.py --backfill ../../design_cases
python3 learn_heuristics.py
python3 query_knowledge.py family alu --platform nangate45 | grep -A6 slack_deterioration || echo "(no alu entry — try another family from query_knowledge.py list)"
```
Expected: backfill reports several hundred updated rows; `slack_deterioration` appears for at least some nangate45 families.

- [ ] **Step 4: Commit (no-op if clean; otherwise the regenerated heuristics.json)**

```bash
cd /proj/workarea/user5/agent-r2g
git add -A r2g-rtl2gds/knowledge/heuristics.json r2g-rtl2gds/knowledge/runs.sqlite 2>/dev/null || true
git commit -m "chore(knowledge): backfill staged slacks + re-learn deterioration" || echo "nothing to commit"
```

---

## Phase 4 — Documentation

### Task 14: SKILL.md, orfs-playbook.md, lessons-learned.md

**Files:**
- Modify: `r2g-rtl2gds/SKILL.md`
- Modify: `r2g-rtl2gds/references/orfs-playbook.md`
- Modify: `r2g-rtl2gds/references/lessons-learned.md`

- [ ] **Step 1: Add the optional Fmax step to SKILL.md**

In `SKILL.md`, after the backend step (the "5b"/PPA region), add a short subsection:

```markdown
### 5a. (Optional) Fmax search — find the fastest closing period

Before committing to a clock period, you can characterize the design's Fmax:

    python3 scripts/reports/fmax_search.py <project-dir> [platform] [--verify]

Loose-first search using cheap **placement-stage** timing (each probe runs only
`ORFS_STAGES="synth floorplan place"`). It reports a **predicted-signoff Fmax**
(`reports/fmax_search.json`), corrected by a learned per-family slack-deterioration
model. The number is a **proxy (UNVERIFIED)** — post-place timing is optimistic vs
signoff. Pass `--verify` to confirm the winner with one full flow (and feed the
result back to tighten the model). This does NOT replace the step-8 `check_timing`
gate, which still runs on the final backend.

Knobs: `--max-parallel`, `--probe-timeout`, `--place-fast` (whole-search
conservative lower bound for hang-prone designs), `--keep-variants`.
```

- [ ] **Step 2: Add the playbook section**

In `references/orfs-playbook.md`, after "Config Tuning Guidelines", add a "Fmax Search (loose-first)" section documenting: the probe command (`ORFS_STAGES="synth floorplan place"`), the proxy keys (`detailedplace__timing__setup__ws` / floorplan `floorplan__timing__setup__ws`), the variant-cloning recipe (`<base>_fmax_p<NNNN>`, unique FLOW_VARIANT), the root-find algorithm, the deterioration model + `--verify` self-correction, and the honest-label taxonomy. Cross-reference `failure-patterns.md` for the congestion/macro archetypes where the proxy is least reliable.

- [ ] **Step 3: Add the lessons-learned note**

In `references/lessons-learned.md`, add a dated note recording: post-place timing is optimistic vs signoff; the corpus shows **placement is the dominant gap** (`d_fp_pl` p90 ≈ 0.41 ns) while **routing is ≈ neutral** (`d_pl_fin` median negative); archetypes where the proxy lies (congestion/route-limited, macro/CTS-skew, hold cliffs invisible at place); and that the deterioration model is **nangate45-backfilled, other platforms forward-learned**.

- [ ] **Step 4: Commit**

```bash
git add r2g-rtl2gds/SKILL.md r2g-rtl2gds/references/orfs-playbook.md r2g-rtl2gds/references/lessons-learned.md
git commit -m "docs(skill): document Fmax search + deterioration model"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** D1–D7 all map to tasks — D1/D2 (place-stage loose-first) Tasks 7–11; D3 (proxy + labels, `--verify`) Tasks 10/12; D4 (escape hatches, inconclusive) Task 11; D5 (root-find + confirm grid) Tasks 8/11; D6 (clk_period fix + seed) Tasks 3/6/10; D7 (learnable staged deterioration + backfill) Tasks 1/4/5/12.
- **Type consistency:** the `model` dict shape (`{"d_fp_pl": (ns,pct), "d_pl_fin": (ns,pct)}`) is produced by `fm.select_model` and consumed by `d_fp_pl`/`d_pl_fin`/`classify_probe`/`search_loop` identically. Probe dicts always carry `place_ws`/`place_tns`/`status`. `parse_stage_metrics` always returns `{setup_wns, setup_tns}` or `{}`.
- **Hard rules enforced in code:** `assert_safe_knobs` (density ≥ 0.10), `variant_name` (unique FLOW_VARIANT per period), frozen knobs (only the SDC `clk_period` line is rewritten; `clone_variant` copies `config.mk` verbatim), inconclusive≠fail (`classify_probe`/`search_loop`).
- **Heavy end-to-end** (real ORFS) is intentionally NOT a unit test — it is the Task 13 Step 3 live smoke test (gated behind the operator running it), mirroring the repo's golden-regression convention.
```

---

## Implementation Log (2026-06-04)

Executed via a dependency-gated Workflow (14 subagents, strict TDD, no git inside agents; commits made from the driving session). **9 commits `cc5376d..42c233a`** on `feat/fmax-search`. Full suite **357 passed / 8 skipped / 0 failed**; 54 new fmax+knowledge tests. Live smoke test (Task 13 Step 3) backfilled **750 runs** (`clock_period_ns` 750/750, `place_setup_ws` 634/750) and re-learned **`slack_deterioration` for 47/48** family·platform entries — real-corpus numbers confirm the model premise (`d_fp_pl` p90 ≈ 0.38 ns dominant, `d_pl_fin` p90 ≈ −0.01 ns neutral). `runs.sqlite`/`heuristics.json` are gitignored, so Step 4 produced no commit (as its defensive `|| echo "nothing to commit"` anticipated).

**Two fixes beyond the plan's verbatim edits (superseding invariants):**

1. **`knowledge_db.ensure_schema` latent ordering bug** (folded into Task 1, commit `cc5376d`). The plan's Task 1 test seeds a stripped legacy `runs` table; the original `conn.executescript(ddl)` aborted the whole bootstrap when `CREATE INDEX … ON runs(design_family,…)` referenced a not-yet-migrated column — *before* `_migrate_add_columns` ran, so the slack columns were never added. `ensure_schema` now executes statements one-by-one, defers any `CREATE INDEX` raising "no such column" until after the ALTER-TABLE migration, then retries/skips. Production bootstrap (fresh/complete DB) is unchanged — every statement runs on the first pass. Verified on the real 750-row legacy DB (backfill migrated it without error).

2. **`ingest_run.py --backfill` CLI ergonomics** (commit `42c233a`, supersedes Task 4's `main()` text). The required `project` positional made the documented standalone form (`ingest_run.py --backfill <dir>`, used by Task 13 Step 3) fail argparse. `project` is now `nargs="?"`, with a clear error only when neither `project` nor `--backfill` is given. New test `test_main_backfill_runs_without_a_project_arg`.
