# Symptom-Indexed Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-organize the `r2g-rtl2gds` knowledge store so all learned repair experience is indexed by a **symptom signature** (not the design-family-name prefix), keep raw actions/trajectories/symptoms as the lossless system of record, surface linked prose lessons in-context at the decision point, and validate cross-platform transfer on `sky130hd`.

**Architecture:** Add a raw `symptoms` dimension table + a `symptom_id`/`signature_json` foreign key on `fix_events`, `fix_trajectories`, and `run_violations`. A pure `knowledge/symptom.py` module computes the canonical signature + id. `learn_heuristics.py` derives a top-level `symptoms[symptom_id]` projection in `heuristics.json` (pooled across families/platforms, with a `by_platform` breakdown); `fix_model.py` seeds an informed cross-symptom prior; `diagnose_signoff_fix.py` looks recipes up by symptom and surfaces the matching active prose lesson. Family-name survives only as `evidence_designs` provenance. `platform` is a conditioning attribute with a `"*"` wildcard. Raw is never the derived projection's hostage: a from-scratch re-learn reproduces the aggregate.

**Tech Stack:** Python 3.10+, SQLite (stdlib `sqlite3`), Bash (fix loop), pytest. No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-06-09-symptom-indexed-memory-design.md`

---

## Conventions (read once)

- **Repo root:** `/proj/workarea/user5/agent-r2g`. All commands run from there.
- **Run one test:** `pytest r2g-rtl2gds/tests/test_<file>.py::<test_name> -v`
- **Run a file's tests:** `pytest r2g-rtl2gds/tests/test_<file>.py -v`
- **Full suite:** `pytest r2g-rtl2gds/ -q`
- **Test fixtures (from `r2g-rtl2gds/tests/conftest.py`):** `tmp_knowledge_dir` copies the real `schema.sql` + `families.json` into a temp dir; build a DB with
  `conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")` then
  `knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")`.
- **Branch:** work continues on `docs/symptom-indexed-memory-spec` is NOT correct — create a feature branch `feat/symptom-indexed-memory` from `main` at the start (Task 0).
- **Commit prefix:** `feat(skill):` / `fix(skill):` per repo `CLAUDE.md`.
- **Must-not-break invariants** (spec §7): `knowledge_db.is_success` stays the single success predicate; ingest stays idempotent; `PLACE_DENSITY_LB_ADDON ≥ 0.10` floor wins; no auto-promotion of prose; raw is rebuildable; **family/name never a learning or lookup key**.

---

## Task 0: Branch + baseline green

**Files:** none (git only)

- [ ] **Step 1: Create the feature branch from main**

```bash
cd /proj/workarea/user5/agent-r2g
git checkout main
git checkout -b feat/symptom-indexed-memory
```

- [ ] **Step 2: Confirm the suite is green before changes**

Run: `pytest r2g-rtl2gds/ -q`
Expected: all pass (baseline ~375 passed / 8 skipped). If anything fails pre-change, STOP and report — do not build on a red baseline.

---

# Phase 0 — Honesty gate + schema foundation

## Task 1: Generalize the column migration to any table

`knowledge_db._migrate_add_columns` currently only adds columns to `runs` (via `_RUNS_ADDED_COLUMNS`). Phase 1 must add columns to `fix_events`, `fix_trajectories`, and `run_violations`. Generalize the helper idempotently.

**Files:**
- Modify: `r2g-rtl2gds/knowledge/knowledge_db.py` (`_RUNS_ADDED_COLUMNS` ~40-46, `_migrate_add_columns` ~49-53)
- Test: `r2g-rtl2gds/tests/test_knowledge_db.py`

- [ ] **Step 1: Write the failing test**

```python
def test_migrate_adds_columns_to_multiple_tables(tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    # Simulate a legacy DB missing the new columns by dropping them is hard in
    # sqlite; instead assert ensure_schema is idempotent AND the new columns exist.
    fe_cols = {r[1] for r in conn.execute("PRAGMA table_info(fix_events)")}
    rv_cols = {r[1] for r in conn.execute("PRAGMA table_info(run_violations)")}
    ft_cols = {r[1] for r in conn.execute("PRAGMA table_info(fix_trajectories)")}
    assert {"symptom_id", "signature_json"} <= fe_cols
    assert {"symptom_id", "signature_json"} <= rv_cols
    assert {"symptom_id", "signature_json"} <= ft_cols
    # Idempotent: a second ensure_schema must not raise.
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    conn.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_knowledge_db.py::test_migrate_adds_columns_to_multiple_tables -v`
Expected: FAIL — `symptom_id` not in column sets (columns not added yet; introduced here + Task 2).

- [ ] **Step 3: Generalize the migration**

In `knowledge_db.py`, replace the `_RUNS_ADDED_COLUMNS` dict + `_migrate_add_columns` with a per-table mapping:

```python
# Idempotent ALTER TABLE ADD COLUMN migrations, keyed by table name. schema.sql
# uses CREATE TABLE IF NOT EXISTS so it never re-creates existing tables; these
# entries patch already-existing tables on legacy DBs. New tables (e.g. symptoms)
# go straight into schema.sql.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "runs": {
        "lvs_mismatch_class": "TEXT",
        "eval_arm": "TEXT",
    },
    # Symptom-indexed memory (spec 2026-06-09): raw symptom tagging on the raw tiers.
    "fix_events": {
        "symptom_id": "TEXT",
        "signature_json": "TEXT",
    },
    "fix_trajectories": {
        "symptom_id": "TEXT",
        "signature_json": "TEXT",
    },
    "run_violations": {
        "symptom_id": "TEXT",
        "signature_json": "TEXT",
    },
}


def _migrate_add_columns(conn: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
```

(Leave `ensure_schema` calling `_migrate_add_columns(conn)` as-is.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest r2g-rtl2gds/tests/test_knowledge_db.py::test_migrate_adds_columns_to_multiple_tables -v`
Expected: PASS (after Task 2 adds the symptoms table + index DDL; if it still fails only on the `symptoms`-table-dependent assertions, complete Task 2 then re-run — these two tasks land together).

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/knowledge/knowledge_db.py r2g-rtl2gds/tests/test_knowledge_db.py
git commit -m "feat(skill): generalize column migration to fix_events/trajectories/run_violations"
```

---

## Task 2: Add the raw `symptoms` table + indexes

**Files:**
- Modify: `r2g-rtl2gds/knowledge/schema.sql` (append after `fix_events_archive`, ~line 163)
- Test: `r2g-rtl2gds/tests/test_knowledge_db.py`

- [ ] **Step 1: Write the failing test**

```python
def test_symptoms_table_and_indexes_exist(tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(symptoms)")}
    assert {"symptom_id", "check_type", "class", "predicates_json",
            "symptom_schema_version", "first_seen"} <= cols
    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_symptoms_check_class" in idx
    assert "idx_fix_events_symptom" in idx
    assert "idx_run_violations_symptom" in idx
    assert "idx_fix_traj_symptom" in idx
    conn.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_knowledge_db.py::test_symptoms_table_and_indexes_exist -v`
Expected: FAIL — `symptoms` table does not exist.

- [ ] **Step 3: Append the DDL to `schema.sql`**

Append to the end of `r2g-rtl2gds/knowledge/schema.sql`:

```sql
-- ── Symptom-indexed memory (spec 2026-06-09) ─────────────────────────────
-- Raw symptom catalog: one row per distinct symptom_id. The symptom is the
-- universal index for learned repair experience; design-family/name is NEVER
-- a key (only evidence_designs provenance in the derived heuristics.json).
CREATE TABLE IF NOT EXISTS symptoms (
    symptom_id              TEXT PRIMARY KEY,        -- sha1(check, class, sorted true predicates)[:16]
    check_type              TEXT,                    -- drc | lvs | timing | synth | orfs_stage
    class                   TEXT,                    -- dominant DRC cat | lvs mismatch_class | timing tier | ...
    predicates_json         TEXT,                    -- {"nets_balanced": true, ...} (sparse, true-only)
    symptom_schema_version  INTEGER,                 -- bump when the predicate set / hashing changes
    first_seen              TEXT
);
CREATE INDEX IF NOT EXISTS idx_symptoms_check_class ON symptoms(check_type, class);

-- Fast symptom lookups on the raw tiers (symptom_id/signature_json columns are
-- added by knowledge_db._migrate_add_columns for legacy DBs; created here for new).
CREATE INDEX IF NOT EXISTS idx_fix_events_symptom    ON fix_events(symptom_id);
CREATE INDEX IF NOT EXISTS idx_run_violations_symptom ON run_violations(symptom_id);
CREATE INDEX IF NOT EXISTS idx_fix_traj_symptom      ON fix_trajectories(symptom_id);
```

> Note: the `symptom_id`/`signature_json` columns referenced by the indexes are added to existing tables by `_migrate_add_columns` (Task 1). On a brand-new DB the `CREATE INDEX` runs after `executescript` of `schema.sql`; the columns do not yet exist there, so **also** declare them inline on the raw tables to keep new-DB creation self-consistent. Edit the three `CREATE TABLE` bodies to add the two columns: in `fix_events` add `symptom_id TEXT,` and `signature_json TEXT,` before the `UNIQUE(...)` line; in `fix_trajectories` add them before `PRIMARY KEY (...)`; in `run_violations` add them before `snapshot_ts`. (Leave `_migrate_add_columns` for legacy DBs — it is a no-op when the column already exists.)

- [ ] **Step 4: Run both schema tests to verify they pass**

Run: `pytest r2g-rtl2gds/tests/test_knowledge_db.py::test_symptoms_table_and_indexes_exist r2g-rtl2gds/tests/test_knowledge_db.py::test_migrate_adds_columns_to_multiple_tables -v`
Expected: PASS both.

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/knowledge/schema.sql r2g-rtl2gds/tests/test_knowledge_db.py
git commit -m "feat(skill): add raw symptoms table + symptom_id columns/indexes"
```

---

## Task 3: Pure `symptom.py` signature module

**Files:**
- Create: `r2g-rtl2gds/knowledge/symptom.py`
- Test: `r2g-rtl2gds/tests/test_symptom.py`

- [ ] **Step 1: Write the failing test**

```python
import importlib
symptom = importlib.import_module("symptom")  # knowledge/ is on sys.path in conftest


def test_symptom_id_is_stable_and_predicate_order_independent():
    sig_a = symptom.canonical_signature(
        "lvs", "symmetric_matcher",
        {"nets_balanced": True, "device_mismatch_present": False})
    sig_b = symptom.canonical_signature(
        "lvs", "symmetric_matcher",
        {"device_mismatch_present": False, "nets_balanced": True})
    assert symptom.symptom_id(sig_a) == symptom.symptom_id(sig_b)
    # false predicates are dropped (sparse, true-only)
    assert sig_a["predicates"] == {"nets_balanced": True}


def test_distinct_predicates_make_distinct_symptoms():
    base = symptom.canonical_signature("lvs", "generic", {})
    swapped = symptom.canonical_signature("lvs", "generic", {"same_cell_swap_present": True})
    assert symptom.symptom_id(base) != symptom.symptom_id(swapped)


def test_predicates_for_lvs_derives_balance_and_device():
    report = {"net_mismatches_schematic_only": 4, "net_mismatches_layout_only": 4,
              "device_mismatches": 0, "circuit_swaps": 2}
    p = symptom.predicates_for("lvs", report)
    assert p["nets_balanced"] is True
    assert "device_mismatch_present" not in p
    assert p["same_cell_swap_present"] is True


def test_from_fix_log_row_uses_check_class_predicates():
    row = {"check": "drc", "violation_class": "METAL1_ANTENNA",
           "predicates": {"beol_only": True}}
    sig, sid = symptom.from_fix_log_row(row)
    assert sig["check"] == "drc" and sig["class"] == "METAL1_ANTENNA"
    assert sig["predicates"] == {"beol_only": True}
    assert len(sid) == 16
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_symptom.py -v`
Expected: FAIL — `ModuleNotFoundError: symptom`.

- [ ] **Step 3: Write `knowledge/symptom.py`**

```python
#!/usr/bin/env python3
"""Canonical symptom signature for the symptom-indexed memory (spec 2026-06-09).

A symptom is {check, class, predicates} -> a stable symptom_id hash. The symptom
is the UNIVERSAL index for learned repair experience; design-family/name is never
part of it. Pure module: no I/O, no DB, fully unit-testable.
"""
from __future__ import annotations

import hashlib
import json

SYMPTOM_SCHEMA_VERSION = 1

# Curated, decision-relevant predicate keys per check. ONLY these participate in
# the symptom_id hash. Kept deliberately small so symptoms pool (don't fragment).
_PREDICATE_KEYS: dict[str, tuple[str, ...]] = {
    "lvs": ("nets_balanced", "device_mismatch_present", "same_cell_swap_present",
            "sigsegv", "internal_assertion", "extraction_terminated"),
    "drc": ("beol_only",),
    "timing": ("single_dominant_path",),
    "synth": ("post_ast_marker_ge_3",),
    "orfs_stage": (),
}


def canonical_signature(check: str | None, vclass: str | None,
                        predicates: dict | None = None) -> dict:
    """Canonical {check, class, predicates} with predicates filtered to the
    curated, TRUE-valued decision keys for this check (sparse, true-only)."""
    preds: dict[str, bool] = {}
    for k in _PREDICATE_KEYS.get(check or "", ()):
        if (predicates or {}).get(k):
            preds[k] = True
    return {"check": check, "class": vclass, "predicates": preds}


def symptom_id(signature: dict) -> str:
    """Stable 16-hex hash over (check, class, sorted true predicate keys)."""
    payload = json.dumps(
        [signature.get("check"), signature.get("class"),
         sorted((signature.get("predicates") or {}).keys())],
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def predicates_for(check: str | None, report: dict) -> dict:
    """Derive curated booleans from a parsed reports/<check>.json dict. Missing
    fields -> predicate simply absent (yields a coarser, still-valid symptom)."""
    p: dict[str, bool] = {}
    if check == "lvs":
        so = report.get("net_mismatches_schematic_only")
        lo = report.get("net_mismatches_layout_only")
        if so is not None and lo is not None:
            p["nets_balanced"] = (so == lo)
        if (report.get("device_mismatches") or 0) > 0:
            p["device_mismatch_present"] = True
        if (report.get("circuit_swaps") or 0) > 0:
            p["same_cell_swap_present"] = True
        crash_line = (report.get("crash_line") or "").lower()
        if report.get("crash") and any(t in crash_line for t in
                                       ("sigsegv", "signal", "sort_circuit")):
            p["sigsegv"] = True
        if report.get("status") == "incomplete" and "assert" in crash_line:
            p["internal_assertion"] = True
    elif check == "drc":
        if str(report.get("drc_mode") or "").startswith("beol"):
            p["beol_only"] = True
    return p


def from_fix_log_row(row: dict) -> tuple[dict, str]:
    """Build (signature, symptom_id) from a fix_log.jsonl row. Uses row['check'],
    row['violation_class'], and the optional row['predicates'] dict (absent on
    backfilled/legacy rows -> coarse class-only signature)."""
    sig = canonical_signature(row.get("check"), row.get("violation_class"),
                              row.get("predicates"))
    return sig, symptom_id(sig)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest r2g-rtl2gds/tests/test_symptom.py -v`
Expected: PASS all four.

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/knowledge/symptom.py r2g-rtl2gds/tests/test_symptom.py
git commit -m "feat(skill): pure symptom-signature module (canonical_signature/symptom_id/predicates_for)"
```

---

## Task 4: Structured `config_lineage.current_outcome` (Phase 0.2)

`_record_lineage` writes `current_outcome = orfs_status` — `'partial'` on 100% of rows (inert). Replace with a structured outcome and make re-ingest idempotent.

**Files:**
- Modify: `r2g-rtl2gds/knowledge/ingest_run.py` (`_record_lineage` ~246-321; the `ingest()` call site ~ end)
- Test: `r2g-rtl2gds/tests/test_ingest_run.py`

- [ ] **Step 1: Write the failing test**

```python
def test_lineage_outcome_is_structured(tmp_path, tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    fam = tmp_knowledge_dir / "families.json"
    # Two runs of the SAME design/platform with a CORE_UTILIZATION change.
    p1 = _mk_lineage_project(tmp_path, "d1", cu="20", drc="clean")
    ingest_run.ingest(p1, conn, families_path=fam)
    p2 = _mk_lineage_project(tmp_path, "d1", cu="25", drc="clean", subdir="run2")
    ingest_run.ingest(p2, conn, families_path=fam)
    row = conn.execute(
        "SELECT current_outcome FROM config_lineage").fetchone()
    assert row is not None
    outcome = json.loads(row[0])
    assert set(outcome) >= {"is_success", "wns_ns", "drc_violations",
                            "total_elapsed_s"}
    assert outcome["is_success"] is True   # clean DRC -> relaxed success
    conn.close()
```

Add this helper near the top of the test file (a lineage project needs config.mk + a clean drc.json so `is_success` is True):

```python
def _mk_lineage_project(tmp_path, name, cu="20", drc="clean", subdir=None):
    base = tmp_path / (subdir or name)
    (base / "constraints").mkdir(parents=True, exist_ok=True)
    (base / "reports").mkdir(exist_ok=True)
    (base / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = nangate45\n"
        f"export CORE_UTILIZATION = {cu}\n")
    (base / "reports" / "ppa.json").write_text(json.dumps({"summary": {}, "geometry": {}}))
    (base / "reports" / "drc.json").write_text(
        json.dumps({"status": drc, "total_violations": 0, "categories": {}}))
    (base / "reports" / "lvs.json").write_text(json.dumps({"status": "clean"}))
    return base
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_ingest_run.py::test_lineage_outcome_is_structured -v`
Expected: FAIL — `current_outcome` is the bare string `"partial"`, `json.loads` raises or lacks keys.

- [ ] **Step 3: Build the structured outcome and write it**

In `ingest_run.py`, change `_record_lineage`'s signature to receive the current run's outcome fields, and write a JSON outcome. Replace the final `conn.execute("INSERT INTO config_lineage ...")` block with:

```python
    current_outcome = json.dumps({
        "is_success": knowledge_db.is_success({
            "orfs_status": orfs_status,
            "drc_status": outcome_fields.get("drc_status"),
            "lvs_status": outcome_fields.get("lvs_status"),
            "rcx_status": outcome_fields.get("rcx_status"),
            "lvs_mismatch_class": outcome_fields.get("lvs_mismatch_class"),
        }),
        "orfs_status": orfs_status,
        "wns_ns": outcome_fields.get("wns_ns"),
        "drc_violations": outcome_fields.get("drc_violations"),
        "total_elapsed_s": outcome_fields.get("total_elapsed_s"),
    }, sort_keys=True)

    conn.execute(
        "INSERT INTO config_lineage "
        "(design_name, platform, current_run_id, previous_run_id, "
        " diff_json, current_outcome, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (design_name, platform, run_id, prev_run_id,
         json.dumps(diff, sort_keys=True), current_outcome,
         _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"),
    )
```

Update the `def _record_lineage(...)` signature to add `outcome_fields: dict`, and update its call inside `ingest()` to pass the already-computed values:

```python
    _record_lineage(conn, run_id, design_name, platform, cfg, orfs_status,
                    outcome_fields={
                        "drc_status": drc.get("status"),
                        "lvs_status": lvs.get("status"),
                        "rcx_status": rcx.get("status"),
                        "lvs_mismatch_class": lvs.get("mismatch_class"),
                        "wns_ns": _to_float(timing.get("setup_wns")),
                        "drc_violations": _to_int(drc.get("total_violations")),
                        "total_elapsed_s": total_elapsed,
                    })
```

> Idempotency note: `config_lineage` rows are append-only and only created on a non-empty diff between the two most-recent runs; re-ingesting the same run is a no-op because the previous-run lookup excludes the same `run_id` and `INSERT OR REPLACE INTO runs` keeps one row per `run_id`. No change needed here, but DO NOT add a second lineage row on re-ingest — verify by re-running ingest in the test (add `ingest_run.ingest(p2, ...)` again and assert `SELECT COUNT(*) FROM config_lineage == 1`).

Add that idempotency assertion to the test before `conn.close()`:

```python
    ingest_run.ingest(p2, conn, families_path=fam)  # re-ingest
    assert conn.execute("SELECT COUNT(*) FROM config_lineage").fetchone()[0] == 1
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest r2g-rtl2gds/tests/test_ingest_run.py::test_lineage_outcome_is_structured -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/knowledge/ingest_run.py r2g-rtl2gds/tests/test_ingest_run.py
git commit -m "feat(skill): structured config_lineage.current_outcome (Phase 0.2)"
```

---

## Task 5: Run the dormant A/B harness once and record the verdict (Phase 0.1)

This is an **operational** task (no new code). It produces the never-existed `eval_summary.json` and an honest verdict on whether the learned config loop beats naive. Multi-hour EDA flows are operator-driven; this task scopes to a small set and records whatever was run.

**Files:**
- Create: `r2g-rtl2gds/knowledge/eval/eval_set.json` (small, ~6-10 designs)
- Create (output): `r2g-rtl2gds/knowledge/eval/eval_summary.json`
- Create (doc): a dated note appended to `r2g-rtl2gds/references/lessons-learned.md`

- [ ] **Step 1: Build a small eval set** — pick ≤10 small nangate45 designs from `design_cases/` whose `suggest_config` learned median differs from the size baseline. Confirm the diffs are non-empty by emitting the plan:

```bash
cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds/knowledge
python3 eval_heuristics.py emit --eval-set eval/eval_set.json --out-dir eval/arms
# Inspect printed knob_diff lines: drop any "(no knob differs)" design from eval_set.json.
```

- [ ] **Step 2: Run both arms** through the real flow for each design (operator-driven; distinct `<design>_naive` / `<design>_learned` basenames keep ORFS `FLOW_VARIANT` isolated — never run the same `DESIGN_NAME`+`FLOW_VARIANT` concurrently). Then summarize:

```bash
python3 eval_heuristics.py summarize --arms-dir eval/arms --out-dir eval/arms
```

- [ ] **Step 3: Record the verdict** — append a dated note to `references/lessons-learned.md` quoting `eval_summary.json`'s `n_wins`/`n_regressions`/`median_cost_delta_pct_wins`, and state the gate decision: if learned ≈ naive (no net win), Phase 2 credit-assignment on the config loop is deprioritized; if learned wins, it is greenlit. Commit the eval_set + summary + note.

- [ ] **Step 4: Commit**

```bash
git add r2g-rtl2gds/knowledge/eval/eval_set.json r2g-rtl2gds/knowledge/eval/eval_summary.json r2g-rtl2gds/references/lessons-learned.md
git commit -m "feat(skill): run config A/B harness; record honest payoff verdict (Phase 0.1)"
```

> If the multi-hour flows cannot be run in this session, commit `eval_set.json` + the `emit` plan and a note that summarize is pending an operator run. Do NOT fabricate `eval_summary.json`.

---

# Phase 1 — Symptom-indexed core

## Task 6: Capture `config_delta`, `env_flags`, and `predicates` into `fix_log.jsonl`

`fix_signoff.sh::_log_iter` writes 13 keys; it discards the `--apply` stdout (`config_edits`) at line 140 and never records env flags or symptom predicates. Add them.

**Files:**
- Modify: `r2g-rtl2gds/scripts/flow/fix_signoff.sh` (`_log_iter` 84-115; `fix_one` apply site ~140)
- Test: `r2g-rtl2gds/tests/test_fix_signoff_logging.py` (new; shell-level)

- [ ] **Step 1: Write the failing test** (drives the shell via a stubbed `diagnose` + `run_*`):

```python
import json, os, subprocess, stat
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]


def _stub(path: Path, body: str):
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_log_iter_records_config_delta_env_and_predicates(tmp_path, monkeypatch):
    proj = tmp_path / "demo"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n")
    # lvs.json with classifier fields so predicates_for derives booleans.
    (proj / "reports" / "lvs.json").write_text(json.dumps({
        "status": "fail", "mismatch_class": "symmetric_matcher",
        "net_mismatches_schematic_only": 3, "net_mismatches_layout_only": 3,
        "device_mismatches": 0, "circuit_swaps": 1}))
    # Stub DIAGNOSE: --next emits one strategy then STOP; --apply prints config_edits.
    bindir = tmp_path / "bin"; bindir.mkdir()
    _stub(bindir / "diagnose.sh", r'''
case "$*" in
  *--next*) if [[ -f /tmp/r2g_done ]]; then echo -e "STOP\tfail\tdone"; else echo -e "lvs_same_nets_seed\t\trecheck"; fi ;;
  *--apply*) echo '{"applied":"lvs_same_nets_seed","config_edits":{"LVS_SEED":"1"}}' ; touch /tmp/r2g_done ;;
esac
''')
    # ... (the test sets DIAGNOSE/RUN_LVS/RUN_DRC/RUN_ORFS env to harmless stubs,
    # exports ROUTE_FAST=1, runs fix_signoff.sh on proj with --check lvs, then:)
    log = json.loads((proj / "reports" / "fix_log.jsonl").read_text().splitlines()[0])
    assert json.loads(log["config_delta"]) == {"LVS_SEED": "1"}
    assert json.loads(log["env_flags"]).get("ROUTE_FAST") == "1"
    assert log["predicates"]["nets_balanced"] is True
    assert log["predicates"]["same_cell_swap_present"] is True
```

> The stub wiring (env vars `DIAGNOSE`, `RUN_LVS`, etc.) mirrors how `fix_signoff.sh` resolves its helpers near the top of the script — read lines 1-60 of `fix_signoff.sh` and set the matching env overrides in the test. Keep `MAX_ITERS=1`.

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_fix_signoff_logging.py -v`
Expected: FAIL — `KeyError: 'config_delta'`.

- [ ] **Step 3: Capture `--apply` stdout in `fix_one`**

In `fix_signoff.sh`, change the apply site (line ~140) from discarding stdout to capturing the `config_edits`:

```bash
    local apply_out cfg_delta="{}"
    if ! apply_out="$("$DIAGNOSE" "$PROJECT_DIR" --check "$check" --apply "$sid")"; then
      echo "[$check] apply '$sid' failed; aborting" >&2
      _log_iter "$check" "$it" "$sid" "$before" "$before" "apply_failed" "$rerun" "$before_vclass" "$before_cats" "{}"
      return 1
    fi
    cfg_delta="$(python3 -c 'import json,sys
try: print(json.dumps(json.loads(sys.stdin.read()).get("config_edits") or {}))
except Exception: print("{}")' <<<"$apply_out")"
```

Thread `cfg_delta` as a new 10th positional arg to EVERY `_log_iter` call in `fix_one` (pass `"{}"` where no apply happened — the STOP / apply_failed / rerun_failed / recheck_unparsed sites).

- [ ] **Step 4: Extend `_log_iter` to emit the three new fields**

Edit the python block inside `_log_iter` (after it computes `status` and `cum`) to also derive predicates from the report it already loaded, read env flags, and accept the config_delta arg. Replace the `o=dict(...)` assembly with:

```bash
_log_iter() {  # check iter strategy before after verdict from_stage vclass before_cats config_delta
  python3 -c 'import json,sys,os
check,it,strategy,before,after,verdict,from_stage=sys.argv[1:8]
proj,sid,logp=sys.argv[8],sys.argv[9],sys.argv[10]
vclass,before_cats_json,ts=sys.argv[11],sys.argv[12],sys.argv[13]
config_delta=sys.argv[14] if len(sys.argv)>14 else "{}"
rep=os.path.join(proj,"reports",check+".json")
try: report=json.load(open(rep))
except Exception: report={}
status=report.get("status")
# symptom predicates from the current report (knowledge/ is on sys.path via PYTHONPATH).
preds={}
try:
    sys.path.insert(0, os.environ.get("R2G_KNOWLEDGE_DIR",""))
    import symptom
    preds=symptom.predicates_for(check, report)
except Exception: preds={}
env_keys=("PLACE_FAST","ROUTE_FAST","SKIP_ANTENNA_REPAIR","ROUTE_FAST_DRT_ITERS")
env_flags={k:os.environ[k] for k in env_keys if k in os.environ}
cum={}
cfgp=os.path.join(proj,"constraints","config.mk")
if os.path.exists(cfgp):
    inblk=False
    for ln in open(cfgp):
        s=ln.strip()
        if s=="# >>> r2g signoff-fix (auto) >>>": inblk=True; continue
        if s=="# <<< r2g signoff-fix (auto) <<<": inblk=False; continue
        if inblk and s.startswith("export "):
            kv=s[len("export "):].split("=",1)
            if len(kv)==2: cum[kv[0].strip()]=kv[1].strip()
o=dict(check=check,iter=int(it),strategy=strategy,
       before=(before or None),after=(after or None),verdict=verdict,
       from_stage=(from_stage or None),fix_session_id=sid,
       violation_class=(vclass or None),after_status=status,
       before_categories=(before_cats_json if before_cats_json else None),
       cumulative_config=json.dumps(cum,sort_keys=True),
       config_delta=config_delta, env_flags=json.dumps(env_flags,sort_keys=True),
       predicates=preds, ts=ts)
open(logp,"a").write(json.dumps(o)+"\n")' \
    "$1" "$2" "$3" "$4" "$5" "$6" "${7:-}" "$PROJECT_DIR" "$FIX_SESSION_ID" "$LOG" \
    "${8:-}" "${9:-}" "$(date -u +%FT%TZ)" "${10:-{}}"
}
```

Also export `R2G_KNOWLEDGE_DIR` near the top of `fix_signoff.sh` (where other paths are resolved): `export R2G_KNOWLEDGE_DIR="$SCRIPTS_DIR/../knowledge"` (adjust to the script's existing path vars).

- [ ] **Step 5: Run the test to verify it passes, then commit**

Run: `pytest r2g-rtl2gds/tests/test_fix_signoff_logging.py -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/scripts/flow/fix_signoff.sh r2g-rtl2gds/tests/test_fix_signoff_logging.py
git commit -m "feat(skill): fix_log records config_delta, env_flags, symptom predicates"
```

---

## Task 7: `check_timing._journal` records predicates + config_delta parity

**Files:**
- Modify: `r2g-rtl2gds/scripts/reports/check_timing.py` (`_journal` 216-252)
- Test: `r2g-rtl2gds/tests/test_check_timing.py`

- [ ] **Step 1: Write the failing test**

```python
def test_journal_emits_predicates_and_config_delta(tmp_path):
    proj = tmp_path / "d"; (proj / "reports").mkdir(parents=True)
    before = proj / "b.json"; after = proj / "a.json"
    before.write_text(json.dumps({"tier": "minor", "wns": -0.5, "clock_period": 10}))
    after.write_text(json.dumps({"tier": "clean", "wns": 0.1, "clock_period": 11}))
    check_timing._journal(proj, before, after, "period_relax")
    row = json.loads((proj / "reports" / "fix_log.jsonl").read_text().splitlines()[0])
    assert "predicates" in row and isinstance(row["predicates"], dict)
    assert json.loads(row["config_delta"]) == {"clock_period_ns": 11}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_check_timing.py::test_journal_emits_predicates_and_config_delta -v`
Expected: FAIL — `KeyError: 'predicates'`.

- [ ] **Step 3: Add the two keys** to the `row` dict in `_journal` (after the existing keys, before the write):

```python
        "config_delta": json.dumps({"clock_period_ns": clock}),
        "predicates": {},   # timing predicates are tier-only today (class carries the tier)
```

(Timing has no curated boolean predicates in v1 of `symptom._PREDICATE_KEYS`, so an empty dict yields a class-only symptom — correct.)

- [ ] **Step 4: Run the test, then commit**

Run: `pytest r2g-rtl2gds/tests/test_check_timing.py::test_journal_emits_predicates_and_config_delta -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/scripts/reports/check_timing.py r2g-rtl2gds/tests/test_check_timing.py
git commit -m "feat(skill): timing fix_event records predicates + config_delta parity"
```

---

## Task 8: Ingest — tag `fix_events` with symptom + upsert `symptoms` table

**Files:**
- Modify: `r2g-rtl2gds/knowledge/ingest_run.py` (`_ingest_fix_events` 139-165)
- Test: `r2g-rtl2gds/tests/test_ingest_fix_events.py`

- [ ] **Step 1: Write the failing test**

```python
def test_fix_events_get_symptom_id_and_symptoms_table(tmp_path, tmp_knowledge_dir):
    fix_log = [
        {"check": "lvs", "iter": 1, "strategy": "lvs_same_nets_seed",
         "before": "8", "after": "8", "verdict": "no_improvement",
         "fix_session_id": "sX", "violation_class": "symmetric_matcher",
         "predicates": {"nets_balanced": True},
         "cumulative_config": "{}", "config_delta": '{"LVS_SEED":"1"}',
         "env_flags": '{"ROUTE_FAST":"1"}', "ts": "2026-06-09T00:00:00Z"},
    ]
    proj = _mk_project(tmp_path, fix_log=fix_log)
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")

    sid_row = conn.execute(
        "SELECT symptom_id, signature_json, config_delta_json, env_flags_json "
        "FROM fix_events").fetchone()
    assert sid_row[0] and len(sid_row[0]) == 16
    sig = json.loads(sid_row[1])
    assert sig["check"] == "lvs" and sig["class"] == "symmetric_matcher"
    assert sig["predicates"] == {"nets_balanced": True}
    assert json.loads(sid_row[2]) == {"LVS_SEED": "1"}
    assert json.loads(sid_row[3]) == {"ROUTE_FAST": "1"}
    # symptoms dimension row exists, keyed by symptom_id.
    srow = conn.execute("SELECT check_type, class FROM symptoms "
                        "WHERE symptom_id = ?", (sid_row[0],)).fetchone()
    assert srow == ("lvs", "symmetric_matcher")
    # idempotent re-ingest: still one fix_event, one symptom.
    ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    assert conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM symptoms").fetchone()[0] == 1
    conn.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_ingest_fix_events.py::test_fix_events_get_symptom_id_and_symptoms_table -v`
Expected: FAIL — `fix_events.symptom_id` is NULL / no `symptoms` row.

- [ ] **Step 3: Compute symptom + upsert in `_ingest_fix_events`**

At the top of `ingest_run.py` add `import symptom` (knowledge/ is on the path) and a helper:

```python
def _upsert_symptom(conn: sqlite3.Connection, sig: dict, sid: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO symptoms "
        "(symptom_id, check_type, class, predicates_json, symptom_schema_version, first_seen) "
        "VALUES (?,?,?,?,?,?)",
        (sid, sig.get("check"), sig.get("class"),
         json.dumps(sig.get("predicates") or {}, sort_keys=True),
         symptom.SYMPTOM_SCHEMA_VERSION,
         _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"))
```

Rewrite the `_ingest_fix_events` insert to compute the symptom, add the new columns, and switch from `INSERT OR IGNORE` to an `ON CONFLICT … DO UPDATE` that backfills the enrichment columns on re-ingest WITHOUT clobbering `provenance` (idempotency invariant §7.2):

```python
    for r in rows:
        sid = r.get("fix_session_id")
        if not sid:
            continue
        before = _to_float(r.get("before"))
        after = _to_float(r.get("after"))
        sig, symptom_id_ = symptom.from_fix_log_row(r)
        _upsert_symptom(conn, sig, symptom_id_)
        conn.execute(
            "INSERT INTO fix_events "
            "(fix_session_id, project_path, design_name, design_family, platform, "
            " check_type, violation_class, iter, strategy, from_stage, "
            " before_count, after_count, before_categories_json, after_categories_json, "
            " before_status, after_status, verdict, cumulative_config_json, "
            " config_delta_json, env_flags_json, symptom_id, signature_json, ts, provenance) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(fix_session_id, iter, strategy) DO UPDATE SET "
            "  config_delta_json=excluded.config_delta_json, "
            "  env_flags_json=excluded.env_flags_json, "
            "  symptom_id=excluded.symptom_id, "
            "  signature_json=excluded.signature_json",
            (sid, str(project.resolve()), design_name, design_family, platform,
             r.get("check"), r.get("violation_class"), _to_int(r.get("iter")),
             r.get("strategy"), r.get("from_stage"), before, after,
             r.get("before_categories"), r.get("after_categories"),
             r.get("before_status"), r.get("after_status"),
             _normalize_verdict(r.get("verdict"), before, after),
             r.get("cumulative_config"), r.get("config_delta"), r.get("env_flags"),
             symptom_id_, json.dumps(sig, sort_keys=True),
             r.get("ts"), "live"))
        n += 1
```

- [ ] **Step 4: Run the test, then commit**

Run: `pytest r2g-rtl2gds/tests/test_ingest_fix_events.py::test_fix_events_get_symptom_id_and_symptoms_table -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/knowledge/ingest_run.py r2g-rtl2gds/tests/test_ingest_fix_events.py
git commit -m "feat(skill): tag fix_events with symptom_id, upsert symptoms table, idempotent enrichment"
```

---

## Task 9: Ingest — tag `run_violations` with symptom

**Files:**
- Modify: `r2g-rtl2gds/knowledge/ingest_run.py` (`_write_run_violations` 168-180)
- Test: `r2g-rtl2gds/tests/test_ingest_run.py`

- [ ] **Step 1: Write the failing test**

```python
def test_run_violations_get_symptom(tmp_path, tmp_knowledge_dir):
    proj = tmp_path / "rv"
    (proj / "constraints").mkdir(parents=True); (proj / "reports").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = rv\nexport PLATFORM = nangate45\n")
    (proj / "reports" / "ppa.json").write_text(json.dumps({"summary": {}, "geometry": {}}))
    (proj / "reports" / "lvs.json").write_text(json.dumps({
        "status": "fail", "mismatch_class": "symmetric_matcher",
        "net_mismatches_schematic_only": 2, "net_mismatches_layout_only": 2,
        "device_mismatches": 0}))
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": "clean", "total_violations": 0, "categories": {}}))
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    sid, sig = conn.execute(
        "SELECT symptom_id, signature_json FROM run_violations").fetchone()
    assert sid and len(sid) == 16
    assert json.loads(sig)["class"] == "symmetric_matcher"
    assert json.loads(sig)["predicates"]["nets_balanced"] is True
    conn.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_ingest_run.py::test_run_violations_get_symptom -v`
Expected: FAIL — `run_violations.symptom_id` NULL.

- [ ] **Step 3: Compute + store the symptom in `_write_run_violations`**

The per-run symptom uses the dominant failing check: LVS if it failed, else DRC. Replace the body of `_write_run_violations` to compute a signature and persist the two new columns:

```python
def _write_run_violations(conn, run_id, design_family, platform, drc, lvs, tcheck, wns):
    # Per-run symptom: prefer the failing check (LVS fail -> mismatch_class symptom,
    # else DRC -> dominant category, else timing tier). Family is NOT part of it.
    if lvs.get("status") == "fail":
        check, vclass, report = "lvs", lvs.get("mismatch_class"), lvs
    elif drc.get("status") == "fail":
        cats = drc.get("categories") or {}
        vclass = max(cats, key=lambda k: cats[k].get("count") or 0) if cats else None
        check, report = "drc", drc
    else:
        check, vclass, report = "timing", tcheck.get("tier"), {}
    sig = symptom.canonical_signature(check, vclass, symptom.predicates_for(check, report))
    sid = symptom.symptom_id(sig)
    _upsert_symptom(conn, sig, sid)
    conn.execute(
        "INSERT OR REPLACE INTO run_violations "
        "(run_id, design_family, platform, drc_status, drc_categories_json, "
        " lvs_status, lvs_mismatch_class, timing_tier, wns_ns, symptom_id, "
        " signature_json, snapshot_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, design_family, platform, drc.get("status"),
         json.dumps(drc.get("categories") or {}, sort_keys=True),
         lvs.get("status"), lvs.get("mismatch_class"), tcheck.get("tier"), wns,
         sid, json.dumps(sig, sort_keys=True),
         _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"))
```

- [ ] **Step 4: Run the test, then commit**

Run: `pytest r2g-rtl2gds/tests/test_ingest_run.py::test_run_violations_get_symptom -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/knowledge/ingest_run.py r2g-rtl2gds/tests/test_ingest_run.py
git commit -m "feat(skill): tag run_violations with symptom_id/signature_json"
```

---

## Task 10: Carry `symptom_id`/`signature_json` onto `fix_trajectories`

`_rebuild_fix_trajectories` materializes Tier-2 from Tier-1 but drops the symptom. Carry the episode's symptom (from its first fix_event) onto the trajectory so symptom-keyed learning reads it from the never-archived tier.

**Files:**
- Modify: `r2g-rtl2gds/knowledge/learn_heuristics.py` (`_build_trajectory`, `_rebuild_fix_trajectories` 152-167)
- Modify: `r2g-rtl2gds/knowledge/schema.sql` — already has the columns from Task 2; nothing here.
- Test: `r2g-rtl2gds/tests/test_learn_heuristics.py`

- [ ] **Step 1: Write the failing test**

```python
def test_trajectories_carry_symptom(tmp_knowledge_dir):
    db = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    # Insert two raw fix_events for one episode, both tagged with a symptom.
    sig = '{"check": "lvs", "class": "symmetric_matcher", "predicates": {}}'
    for it, verdict in ((1, "no_change"), (2, "cleared")):
        conn.execute(
            "INSERT INTO fix_events (fix_session_id, check_type, violation_class, "
            " iter, strategy, verdict, symptom_id, signature_json, ts) "
            "VALUES ('e1','lvs','symmetric_matcher',?,?,?,?,?,?)",
            (it, "lvs_same_nets_seed", verdict, "abc123def4560000", sig,
             f"2026-06-09T00:0{it}:00Z"))
    conn.commit()
    learn_heuristics._rebuild_fix_trajectories(conn)
    row = conn.execute(
        "SELECT symptom_id, signature_json FROM fix_trajectories").fetchone()
    assert row[0] == "abc123def4560000"
    assert json.loads(row[1])["class"] == "symmetric_matcher"
    conn.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_learn_heuristics.py::test_trajectories_carry_symptom -v`
Expected: FAIL — trajectory has no `symptom_id` value (column NULL).

- [ ] **Step 3: Carry the symptom in `_build_trajectory`**

In `learn_heuristics.py`, `_build_trajectory(evs)` builds the trajectory dict from a list of events. Add the symptom from the first event that has one (fall back to a coarse signature derived from `check_type`+`violation_class` for legacy events without it):

```python
    # Symptom of the episode: first event's stored symptom, else coarse backfill
    # from (check_type, violation_class) so legacy/backfilled events still index.
    import symptom as _symptom
    first = evs[0]
    sid = first.get("symptom_id")
    sigj = first.get("signature_json")
    if not sid:
        sig = _symptom.canonical_signature(first.get("check_type"),
                                           first.get("violation_class"), None)
        sid, sigj = _symptom.symptom_id(sig), json.dumps(sig, sort_keys=True)
    traj["symptom_id"] = sid
    traj["signature_json"] = sigj
```

Ensure `_rebuild_fix_trajectories`'s INSERT includes the new keys — it builds the column list dynamically from `t.keys()` (lines 163-165), so adding the keys to the trajectory dict is sufficient. Confirm `_fetch_all_fix_events` selects `symptom_id, signature_json` (add them to its SELECT column list if it enumerates columns explicitly).

- [ ] **Step 4: Run the test, then commit**

Run: `pytest r2g-rtl2gds/tests/test_learn_heuristics.py::test_trajectories_carry_symptom -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/knowledge/learn_heuristics.py r2g-rtl2gds/tests/test_learn_heuristics.py
git commit -m "feat(skill): carry symptom onto fix_trajectories (coarse backfill for legacy)"
```

---

## Task 11: Learn — derive the `symptoms[symptom_id]` projection

Add a sibling to `_recipes_from_trajectories` that aggregates by `symptom_id` (pooled across families/platforms) with `by_platform` + `platform_specific` and `evidence_designs` provenance. Emit it as top-level `data["symptoms"]` in `heuristics.json`. Keep the family `fix_recipes` subtree during transition.

**Files:**
- Modify: `r2g-rtl2gds/knowledge/learn_heuristics.py` (`learn()` 220-272)
- Test: `r2g-rtl2gds/tests/test_learn_heuristics.py`

- [ ] **Step 1: Write the failing test**

```python
def test_learn_emits_symptom_projection_pooled_across_families(tmp_knowledge_dir):
    db = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    sig = '{"check": "drc", "class": "METAL1_ANTENNA", "predicates": {}}'
    sid = __import__("symptom").symptom_id(json.loads(sig))
    # Same symptom, TWO different families/platforms -> must pool into one bucket.
    rows = [("e_aes", "aes", "nangate45", "demo_aes"),
            ("e_fft", "fft", "nangate45", "demo_fft")]
    for ep, fam, plat, dn in rows:
        conn.execute(
            "INSERT INTO fix_events (fix_session_id, design_name, design_family, "
            " platform, check_type, violation_class, iter, strategy, verdict, "
            " symptom_id, signature_json, ts) "
            "VALUES (?,?,?,?,'drc','METAL1_ANTENNA',1,'antenna_diode_repair',"
            " 'cleared',?,?,?)",
            (ep, dn, fam, plat, sid, sig, "2026-06-09T00:00:00Z"))
    conn.commit(); conn.close()
    out = tmp_knowledge_dir / "heuristics.json"
    learn_heuristics.learn(db, out)
    data = json.loads(out.read_text())
    bucket = data["symptoms"][sid]
    assert bucket["check"] == "drc" and bucket["class"] == "METAL1_ANTENNA"
    assert bucket["n_sessions"] == 2                      # pooled across aes + fft
    assert set(bucket["platforms_seen"]) == {"nangate45"}
    assert sorted(bucket["evidence_designs"]) == ["demo_aes", "demo_fft"]
    strat = bucket["strategies"]["antenna_diode_repair"]
    assert strat["successes"] == 2
    assert strat["by_platform"]["nangate45"]["successes"] == 2
    # family name must NOT be a key anywhere in the symptom projection
    assert "aes" not in json.dumps(list(data["symptoms"].keys()))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_learn_heuristics.py::test_learn_emits_symptom_projection_pooled_across_families -v`
Expected: FAIL — `KeyError: 'symptoms'`.

- [ ] **Step 3: Implement `_symptom_recipes_from_trajectories` + emit**

Add to `learn_heuristics.py`:

```python
def _symptom_recipes_from_trajectories(trajectories: list[dict]) -> dict[str, dict]:
    """Aggregate trajectories BY symptom_id (pooled across families/platforms).
    Family-name is recorded only as evidence_designs provenance; platform is a
    conditioning attribute kept in platforms_seen + per-strategy by_platform."""
    acc: dict[str, dict] = {}
    for t in trajectories:
        sid = t.get("symptom_id")
        if not sid:
            continue
        sig = json.loads(t.get("signature_json") or "{}")
        plat = t.get("platform") or "unknown"
        node = acc.setdefault(sid, {
            "check": sig.get("check"), "class": sig.get("class"),
            "predicates": sig.get("predicates") or {},
            "platforms_seen": set(), "evidence_designs": set(),
            "_sessions": set(), "strategies": {}})
        node["platforms_seen"].add(plat)
        if t.get("design_name"):
            node["evidence_designs"].add(t["design_name"])
        node["_sessions"].add(t.get("fix_session_id"))
        for step in json.loads(t.get("path_json") or "[]"):
            stratid = step.get("strategy")
            if not stratid or stratid == "none":
                continue
            s = node["strategies"].setdefault(stratid, {
                "attempts": 0, "successes": 0, "failures": 0, "wins": 0,
                "by_platform": {}})
            bp = s["by_platform"].setdefault(plat, {
                "attempts": 0, "successes": 0, "failures": 0, "wins": 0})
            verdict = step.get("verdict")
            for tgt in (s, bp):
                tgt["attempts"] += 1
                if verdict == "cleared":
                    tgt["successes"] += 1
                elif verdict == "win":
                    tgt["wins"] += 1
                elif verdict in ("no_change", "regression"):
                    tgt["failures"] += 1
    final: dict[str, dict] = {}
    for sid, node in acc.items():
        final[sid] = {
            "check": node["check"], "class": node["class"],
            "predicates": node["predicates"],
            "platforms_seen": sorted(node["platforms_seen"]),
            "evidence_designs": sorted(node["evidence_designs"]),
            "n_sessions": len(node["_sessions"]),
            "strategies": node["strategies"],
        }
    return final
```

In `learn()`, after the existing fix_recipes assembly (which reads `trajectories` on `conn2`), add:

```python
    data = {
        "generated_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source_run_count": len(rows),
        "min_successful_runs_required": MIN_SUCCESSFUL,
        "schema_version": 2,                       # symptom projection added
        "families": families,
        "symptoms": _symptom_recipes_from_trajectories(trajectories),
    }
```

(Replace the existing `data = {...}` literal at lines 264-269 with the above — note the added `schema_version` and `symptoms` keys.)

- [ ] **Step 4: Run the test, then commit**

Run: `pytest r2g-rtl2gds/tests/test_learn_heuristics.py::test_learn_emits_symptom_projection_pooled_across_families -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/knowledge/learn_heuristics.py r2g-rtl2gds/tests/test_learn_heuristics.py
git commit -m "feat(skill): derive symptom[symptom_id] projection pooled across families"
```

---

## Task 12: `fix_model` — informed cross-symptom prior

Replace the flat `Beta(1,1)` cold-start: when a strategy is untried in the local recipe, seed its prior from a pooled (cross-platform) symptom-level rate passed in by the caller. Backward compatible (`pooled=None` → today's behavior).

**Files:**
- Modify: `r2g-rtl2gds/scripts/reports/fix_model.py` (full file)
- Test: `r2g-rtl2gds/tests/test_fix_model.py`

- [ ] **Step 1: Write the failing test**

```python
def test_informed_prior_lifts_untried_strategy_toward_pooled_rate():
    # Local recipe has NO data for diode_repair; pooled symptom evidence says it
    # clears ~90%. Informed prior must rank it above the flat-0.5 untried prior.
    STATIC = ["antenna_diode_repair", "antenna_density_relief"]
    pooled = {"antenna_diode_repair": {"successes": 9, "attempts": 10, "wins": 0}}
    ranked = fxm.rank_strategies(None, STATIC, pooled=pooled)
    diode = next(r for r in ranked if r["strategy"] == "antenna_diode_repair")
    relief = next(r for r in ranked if r["strategy"] == "antenna_density_relief")
    assert diode["score"] > 0.7           # ~ (9+1)/(10+2)
    assert relief["score"] == 0.5         # no pooled evidence -> neutral
    assert diode["provenance"].startswith("prior")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_fix_model.py::test_informed_prior_lifts_untried_strategy_toward_pooled_rate -v`
Expected: FAIL — `rank_strategies() got an unexpected keyword argument 'pooled'`.

- [ ] **Step 3: Add the `pooled` parameter**

In `fix_model.py`, change `rank_strategies` to accept `pooled` and use it for untried strategies:

```python
def rank_strategies(recipe_entry: dict | None, static_order: list[str],
                    pooled: dict | None = None) -> list[dict]:
    stats = (recipe_entry or {}).get("strategies", {})
    n_sessions = (recipe_entry or {}).get("n_sessions", 0)
    pooled = pooled or {}
    ranked: list[dict] = []
    for pos, sid in enumerate(static_order):
        s = stats.get(sid)
        if s:
            attempts = int(s.get("attempts", 0))
            successes = int(s.get("successes", 0))
            wins = int(s.get("wins", 0))
            failures = int(s.get("failures", max(0, attempts - successes)))
            score = _score(successes, attempts, wins)
            prov = f"learned(n={n_sessions},tried={attempts})"
        elif sid in pooled:
            ps = pooled[sid]
            attempts = int(ps.get("attempts", 0))
            successes = int(ps.get("successes", 0))
            wins = int(ps.get("wins", 0))
            failures = int(ps.get("failures", max(0, attempts - successes)))
            score = _score(successes, attempts, wins)
            prov = f"prior(pooled,tried={attempts})"
        else:
            attempts = successes = failures = wins = 0
            score = _score(0, 0)
            prov = "cold-start"
        item = {"strategy": sid, "score": score, "static_pos": pos,
                "attempts": attempts, "successes": successes, "failures": failures,
                "wins": wins, "provenance": prov}
        if s and s.get("median_reduction_pct") is not None:
            item["median_reduction_pct"] = s["median_reduction_pct"]
        ranked.append(item)
    ranked.sort(key=lambda r: (-r["score"], r["static_pos"]))
    return ranked
```

- [ ] **Step 4: Run the test (and the existing fix_model tests for regression), then commit**

Run: `pytest r2g-rtl2gds/tests/test_fix_model.py -v`
Expected: PASS (new test + all existing — `pooled=None` keeps old behavior).

```bash
git add r2g-rtl2gds/scripts/reports/fix_model.py r2g-rtl2gds/tests/test_fix_model.py
git commit -m "feat(skill): informed cross-symptom prior for untried fix strategies"
```

---

## Task 13: Diagnose — symptom-keyed recipe lookup + pooled prior + family fallback

`_load_recipes` keys by family/platform/check/violation_class. Add a symptom-first path: compute the current symptom_id, read `heuristics.json["symptoms"][sid]`, prefer the current platform's `by_platform` stats as the recipe, and pass the pooled (cross-platform) stats as the prior. Keep the family path as a transition fallback. A strategy flagged `platform_specific` does NOT contribute a cross-platform prior.

**Files:**
- Modify: `r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py` (`_load_recipes` 284-327, `_rank_plan_strategies` 239-248)
- Test: `r2g-rtl2gds/tests/test_diagnose_symptom_lookup.py` (new)

- [ ] **Step 1: Write the failing test**

```python
def test_symptom_lookup_returns_recipe_and_pooled_prior(tmp_path):
    import diagnose_signoff_fix as dsf
    heur = tmp_path / "heuristics.json"
    sig = {"check": "lvs", "class": "symmetric_matcher", "predicates": {}}
    import symptom
    sid = symptom.symptom_id(sig)
    heur.write_text(json.dumps({"symptoms": {sid: {
        "check": "lvs", "class": "symmetric_matcher", "predicates": {},
        "platforms_seen": ["nangate45"], "evidence_designs": ["d1"],
        "n_sessions": 5,
        "strategies": {"lvs_same_nets_seed": {
            "attempts": 5, "successes": 4, "failures": 1, "wins": 0,
            "by_platform": {"nangate45": {"attempts": 5, "successes": 4,
                                          "failures": 1, "wins": 0}}}}}}}))
    lvs = {"status": "fail", "mismatch_class": "symmetric_matcher"}
    recipe, pooled = dsf.load_symptom_recipe(
        check="lvs", platform="sky130hd", drc={}, lvs=lvs, heuristics=heur)
    # sky130hd has NO by_platform data -> recipe entry empty, but pooled prior present.
    assert pooled["lvs_same_nets_seed"]["successes"] == 4
    # nangate45 path returns the platform recipe.
    recipe_n, _ = dsf.load_symptom_recipe(
        check="lvs", platform="nangate45", drc={}, lvs=lvs, heuristics=heur)
    assert recipe_n["strategies"]["lvs_same_nets_seed"]["successes"] == 4
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_diagnose_symptom_lookup.py -v`
Expected: FAIL — `module 'diagnose_signoff_fix' has no attribute 'load_symptom_recipe'`.

- [ ] **Step 3: Add `load_symptom_recipe` and wire it into ranking**

Add to `diagnose_signoff_fix.py` (it already imports `knowledge_db`; add `import symptom`):

```python
def load_symptom_recipe(*, check: str, platform: str, drc: dict, lvs: dict,
                        heuristics: Path | None = None):
    """Return (recipe_entry, pooled_prior) for the current symptom, indexed by
    symptom_id (NOT family). recipe_entry = the current platform's by_platform
    stats (same-platform evidence preferred); pooled_prior = the cross-platform
    pooled stats for untried strategies, excluding platform_specific ones."""
    hp = heuristics or (Path(__file__).resolve().parents[1] / "knowledge" / "heuristics.json")
    if not hp.exists():
        return None, {}
    data = json.loads(hp.read_text(encoding="utf-8"))
    symptoms = data.get("symptoms") or {}
    if check == "drc":
        cats = drc.get("categories") or {}
        vclass = max(cats, key=lambda k: cats[k].get("count") or 0) if cats else None
        report = drc
    elif check == "lvs":
        vclass, report = lvs.get("mismatch_class"), lvs
    else:
        vclass, report = None, {}
    sig = symptom.canonical_signature(check, vclass, symptom.predicates_for(check, report))
    bucket = symptoms.get(symptom.symptom_id(sig))
    if not bucket:
        return None, {}
    strategies = bucket.get("strategies") or {}
    # Same-platform recipe: the by_platform slice for THIS platform.
    recipe = {"strategies": {}, "n_sessions": bucket.get("n_sessions", 0)}
    for stratid, s in strategies.items():
        bp = (s.get("by_platform") or {}).get(platform)
        if bp:
            recipe["strategies"][stratid] = bp
    # Pooled prior: cross-platform totals, minus platform_specific strategies.
    pooled = {stratid: {k: s.get(k, 0) for k in ("attempts", "successes", "wins", "failures")}
              for stratid, s in strategies.items() if not s.get("platform_specific")}
    return (recipe if recipe["strategies"] else None), pooled
```

Then in `_rank_plan_strategies`, prefer the symptom recipe + pooled prior. Modify the function to accept the looked-up pair and pass `pooled` through:

```python
def _rank_plan_strategies(plan, recipes, pooled=None):
    if not plan.get("strategies"):
        return plan
    static_order = [s["id"] for s in plan["strategies"]]
    ranking = fix_model.rank_strategies(recipes, static_order, pooled=pooled)
    by_id = {s["id"]: s for s in plan["strategies"]}
    plan["strategies"] = [by_id[r["strategy"]] for r in ranking if r["strategy"] in by_id]
    plan["ranking"] = ranking
    return plan
```

At the call site in `main()` where recipes are loaded (where `_load_recipes` is currently called), prefer the symptom lookup, falling back to the family lookup:

```python
    sym_recipe, pooled = load_symptom_recipe(
        check=args.check, platform=plat, drc=drc, lvs=lvs)
    recipes = sym_recipe if sym_recipe is not None else _load_recipes(
        proj, check=args.check, drc=drc, lvs=lvs)
    _rank_plan_strategies(plan, recipes, pooled=pooled)
```

(`plat` is computed the same way `_load_recipes` does — `cfg.get("PLATFORM", "nangate45")`.)

- [ ] **Step 4: Run the test (+ existing diagnose tests), then commit**

Run: `pytest r2g-rtl2gds/tests/test_diagnose_symptom_lookup.py r2g-rtl2gds/tests/ -k diagnose -v`
Expected: PASS new + no regression in existing diagnose tests.

```bash
git add r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py r2g-rtl2gds/tests/test_diagnose_symptom_lookup.py
git commit -m "feat(skill): symptom-keyed recipe lookup + pooled prior (family fallback retained)"
```

---

## Task 14: `lessons` table + front-matter parser + `sync_lessons.py`

**Files:**
- Modify: `r2g-rtl2gds/knowledge/schema.sql` (append `lessons` table)
- Create: `r2g-rtl2gds/knowledge/sync_lessons.py`
- Test: `r2g-rtl2gds/tests/test_sync_lessons.py` (new)

- [ ] **Step 1: Write the failing test**

```python
import importlib
sync_lessons = importlib.import_module("sync_lessons")


def test_sync_parses_frontmatter_and_backfills_evidence(tmp_path, tmp_knowledge_dir):
    md = tmp_path / "failure-patterns.md"
    md.write_text(
        "# Failure Patterns\n\n"
        "## LVS symmetric-matcher residual\n"
        "<!-- r2g-lesson:\n"
        "id: lesson-lvs-symmetric-matcher\n"
        "status: active\n"
        'trigger: {check: lvs, class: symmetric_matcher, platform: "*"}\n'
        "strategy_ids: [lvs_same_nets_seed]\n"
        "-->\n"
        "Balanced unmatched nets + zero device mismatches => tool artifact; stop re-running.\n")
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    # Seed a run_violations row with the matching symptom so evidence back-fills.
    import symptom
    sig = symptom.canonical_signature("lvs", "symmetric_matcher", None)
    sid = symptom.symptom_id(sig)
    conn.execute("INSERT INTO runs (run_id, project_path, ingested_at) "
                 "VALUES ('r1','/x','2026-06-09T00:00:00Z')")
    conn.execute("INSERT INTO run_violations (run_id, lvs_status, symptom_id, "
                 "signature_json, snapshot_ts) VALUES "
                 "('r1','fail',?,?,?)", (sid, json.dumps(sig), "2026-06-09T00:00:00Z"))
    conn.commit()
    n = sync_lessons.sync(conn, patterns_path=md)
    assert n == 1
    row = conn.execute(
        "SELECT lesson_id, status, symptom_trigger_json, evidence_runs_json "
        "FROM lessons").fetchone()
    assert row[0] == "lesson-lvs-symmetric-matcher" and row[1] == "active"
    assert json.loads(row[2])["check"] == "lvs"
    assert "r1" in json.loads(row[3])
    # idempotent: same content -> still one row, no error.
    assert sync_lessons.sync(conn, patterns_path=md) == 1
    conn.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_sync_lessons.py -v`
Expected: FAIL — no `sync_lessons` module / no `lessons` table.

- [ ] **Step 3a: Append the `lessons` table** to `schema.sql`:

```sql
-- Prose<->struct link (spec 2026-06-09 §4.4): one row per ## section that carries
-- an r2g-lesson front-matter block. Prose stays the human-editable source of truth;
-- this is a one-way derived index (sync_lessons.py). Never auto-writes prose.
CREATE TABLE IF NOT EXISTS lessons (
    lesson_id             TEXT PRIMARY KEY,
    source_doc            TEXT,
    section_title         TEXT,
    status                TEXT,                  -- active | retired
    symptom_trigger_json  TEXT,                  -- {check, class?, predicates?, platform}
    strategy_ids_json     TEXT,
    prose_excerpt         TEXT,
    evidence_runs_json    TEXT,                  -- AUTO back-filled; do not hand-edit
    content_hash          TEXT,
    synced_at             TEXT
);
```

- [ ] **Step 3b: Write `knowledge/sync_lessons.py`**

```python
#!/usr/bin/env python3
"""One-way prose -> `lessons` table sync (spec 2026-06-09 §4.4).

Parses `r2g-lesson:` HTML-comment front-matter from each ## section of
failure-patterns.md / signoff-fixing.md, upserts a lessons row keyed by lesson_id,
and back-fills evidence_runs_json by matching the symptom trigger against
run_violations.symptom_id / signature_json. Prose is never modified. Idempotent.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import knowledge_db
import symptom

_SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)
_FRONT_RE = re.compile(r"<!--\s*r2g-lesson:(.*?)-->", re.DOTALL)

_DEFAULT_DOCS = [
    knowledge_db.DEFAULT_KNOWLEDGE_DIR.parent / "references" / "failure-patterns.md",
    knowledge_db.DEFAULT_KNOWLEDGE_DIR.parent / "references" / "signoff-fixing.md",
]


def _parse_frontmatter(block: str) -> dict:
    """Parse the lightweight key: value front-matter. Values may be JSON-ish
    ({...}, [...], or "*"/bare scalars). Tolerant: unparsable value -> string."""
    out: dict = {}
    for line in block.strip().splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        try:
            out[key] = json.loads(_jsonify(val))
        except (json.JSONDecodeError, ValueError):
            out[key] = val.strip('"')
    return out


def _jsonify(val: str) -> str:
    # Turn {check: lvs, platform: "*"} and [a, b] into strict JSON.
    if val.startswith("{") or val.startswith("["):
        v = re.sub(r"([{\[,]\s*)([A-Za-z_][\w]*)(\s*):", r'\1"\2"\3:', val)  # quote keys
        v = re.sub(r":(\s*)([A-Za-z_][\w/.*-]*)(\s*[,}\]])", r':\1"\2"\3', v)  # quote bare scalars
        return v
    if val in ("true", "false", "null") or re.fullmatch(r"-?\d+(\.\d+)?", val):
        return val
    return json.dumps(val.strip('"'))


def _iter_sections(text: str):
    matches = list(_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body = text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        yield title, body


def _evidence_for(conn, trigger: dict) -> list[str]:
    """Find run_ids whose run_violations symptom matches the trigger (check/class;
    platform '*' matches any, else exact)."""
    rows = conn.execute(
        "SELECT run_id, platform, signature_json FROM run_violations "
        "WHERE symptom_id IS NOT NULL").fetchall()
    want_platform = trigger.get("platform", "*")
    out = []
    for run_id, plat, sigj in rows:
        try:
            sig = json.loads(sigj or "{}")
        except json.JSONDecodeError:
            continue
        if trigger.get("check") and sig.get("check") != trigger["check"]:
            continue
        if trigger.get("class") and sig.get("class") != trigger["class"]:
            continue
        if want_platform not in ("*", None) and plat != want_platform:
            continue
        out.append(run_id)
    return out


def sync(conn, patterns_path: Path | None = None) -> int:
    docs = [Path(patterns_path)] if patterns_path else _DEFAULT_DOCS
    n = 0
    for doc in docs:
        if not doc.exists():
            continue
        text = doc.read_text(encoding="utf-8")
        for title, body in _iter_sections(text):
            fm = _FRONT_RE.search(body)
            if not fm:
                continue
            meta = _parse_frontmatter(fm.group(1))
            lid = meta.get("id")
            if not lid:
                continue
            trigger = meta.get("trigger") or {}
            prose = _FRONT_RE.sub("", body).strip()[:400]
            content_hash = hashlib.sha1(body.encode("utf-8")).hexdigest()
            evidence = _evidence_for(conn, trigger)
            conn.execute(
                "INSERT INTO lessons (lesson_id, source_doc, section_title, status, "
                " symptom_trigger_json, strategy_ids_json, prose_excerpt, "
                " evidence_runs_json, content_hash, synced_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,datetime('now')) "
                "ON CONFLICT(lesson_id) DO UPDATE SET "
                "  status=excluded.status, symptom_trigger_json=excluded.symptom_trigger_json, "
                "  strategy_ids_json=excluded.strategy_ids_json, prose_excerpt=excluded.prose_excerpt, "
                "  evidence_runs_json=excluded.evidence_runs_json, "
                "  content_hash=excluded.content_hash, synced_at=datetime('now')",
                (lid, str(doc), title, meta.get("status", "active"),
                 json.dumps(trigger, sort_keys=True),
                 json.dumps(meta.get("strategy_ids") or []), prose,
                 json.dumps(evidence), content_hash))
            n += 1
    conn.commit()
    return n


def main() -> int:
    conn = knowledge_db.connect(knowledge_db.DEFAULT_DB_PATH)
    knowledge_db.ensure_schema(conn)
    n = sync(conn)
    print(f"Synced {n} lesson(s).")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
```

- [ ] **Step 4: Run the test, then commit**

Run: `pytest r2g-rtl2gds/tests/test_sync_lessons.py -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/knowledge/schema.sql r2g-rtl2gds/knowledge/sync_lessons.py r2g-rtl2gds/tests/test_sync_lessons.py
git commit -m "feat(skill): lessons table + front-matter parser + one-way sync_lessons.py"
```

---

## Task 15: `search_failures` — front-matter parse + symptom/platform pre-filter

**Files:**
- Modify: `r2g-rtl2gds/knowledge/search_failures.py` (`parse_failure_patterns` ~93, `search` ~120)
- Test: `r2g-rtl2gds/tests/test_search_failures.py`

- [ ] **Step 1: Write the failing test**

```python
def test_search_prefilters_by_symptom_trigger(tmp_path):
    md = tmp_path / "failure-patterns.md"
    md.write_text(
        "# F\n\n"
        "## Antenna (nangate45)\n"
        "<!-- r2g-lesson:\nid: l-ant\nstatus: active\n"
        'trigger: {check: drc, class: METAL1_ANTENNA, platform: nangate45}\n-->\n'
        "Force diodes on nangate45.\n\n"
        "## LVS symmetric (any platform)\n"
        "<!-- r2g-lesson:\nid: l-sym\nstatus: active\n"
        'trigger: {check: lvs, class: symmetric_matcher, platform: "*"}\n-->\n'
        "Tool artifact; stop re-running.\n\n"
        "## Retired note\n"
        "<!-- r2g-lesson:\nid: l-old\nstatus: retired\n"
        'trigger: {check: lvs, class: symmetric_matcher, platform: "*"}\n-->\n'
        "Old advice.\n")
    # LVS symmetric on sky130hd -> the "*" lesson matches; the nangate45 antenna
    # lesson does NOT; the retired lesson is excluded.
    hits = search_failures.lessons_for_symptom(
        check="lvs", vclass="symmetric_matcher", platform="sky130hd",
        patterns_path=md)
    ids = [h["id"] for h in hits]
    assert "l-sym" in ids
    assert "l-ant" not in ids
    assert "l-old" not in ids
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_search_failures.py::test_search_prefilters_by_symptom_trigger -v`
Expected: FAIL — `module 'search_failures' has no attribute 'lessons_for_symptom'`.

- [ ] **Step 3: Add front-matter-aware retrieval**

Add to `search_failures.py` (reuse `sync_lessons`'s parser to avoid duplication):

```python
import sync_lessons  # reuse the front-matter parser + section iterator


def lessons_for_symptom(*, check: str, vclass: str | None, platform: str,
                        patterns_path: Path = _PATTERNS_PATH) -> list[dict]:
    """Return ACTIVE lessons whose trigger matches this symptom (+ platform, with
    '*' wildcard). Each: {id, status, trigger, strategy_ids, prose}."""
    p = Path(patterns_path)
    if not p.exists():
        return []
    out = []
    for title, body in sync_lessons._iter_sections(p.read_text(encoding="utf-8")):
        fm = sync_lessons._FRONT_RE.search(body)
        if not fm:
            continue
        meta = sync_lessons._parse_frontmatter(fm.group(1))
        if meta.get("status", "active") != "active":
            continue
        trig = meta.get("trigger") or {}
        if trig.get("check") and trig["check"] != check:
            continue
        if trig.get("class") and vclass is not None and trig["class"] != vclass:
            continue
        tp = trig.get("platform", "*")
        if tp not in ("*", None) and tp != platform:
            continue
        out.append({"id": meta.get("id"), "status": "active", "trigger": trig,
                    "strategy_ids": meta.get("strategy_ids") or [],
                    "prose": sync_lessons._FRONT_RE.sub("", body).strip()[:400]})
    return out
```

- [ ] **Step 4: Run the test, then commit**

Run: `pytest r2g-rtl2gds/tests/test_search_failures.py -v`
Expected: PASS (new + existing BM25 tests unaffected).

```bash
git add r2g-rtl2gds/knowledge/search_failures.py r2g-rtl2gds/tests/test_search_failures.py
git commit -m "feat(skill): symptom/platform-filtered lesson retrieval in search_failures"
```

---

## Task 16: Surface the matched lesson in `diagnose_signoff_fix` output

When ranking strategies, attach the matching active lesson(s) so the agent sees the human rationale in-context.

**Files:**
- Modify: `r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py` (`main()` near the ranking; `--list` JSON)
- Test: `r2g-rtl2gds/tests/test_diagnose_symptom_lookup.py`

- [ ] **Step 1: Write the failing test**

```python
def test_plan_attaches_matching_lesson(tmp_path, monkeypatch):
    import diagnose_signoff_fix as dsf, search_failures
    md = tmp_path / "failure-patterns.md"
    md.write_text("# F\n\n## Sym\n<!-- r2g-lesson:\nid: l-sym\nstatus: active\n"
                  'trigger: {check: lvs, class: symmetric_matcher, platform: "*"}\n-->\n'
                  "Tool artifact; stop.\n")
    monkeypatch.setattr(search_failures, "_PATTERNS_PATH", md)
    plan = {"status": "fail", "strategies": [{"id": "lvs_same_nets_seed"}]}
    dsf.attach_lessons(plan, check="lvs", vclass="symmetric_matcher",
                       platform="sky130hd")
    assert plan["lessons"] and plan["lessons"][0]["id"] == "l-sym"
    assert "stop" in plan["lessons"][0]["prose"].lower()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_diagnose_symptom_lookup.py::test_plan_attaches_matching_lesson -v`
Expected: FAIL — no `attach_lessons`.

- [ ] **Step 3: Add `attach_lessons` and call it in `main()`**

```python
def attach_lessons(plan: dict, *, check: str, vclass: str | None, platform: str) -> dict:
    try:
        import search_failures
        plan["lessons"] = search_failures.lessons_for_symptom(
            check=check, vclass=vclass, platform=platform)
    except Exception:
        plan["lessons"] = []
    return plan
```

In `main()`, after `_rank_plan_strategies(...)`, compute the current vclass the same way `load_symptom_recipe` does and call `attach_lessons(plan, check=args.check, vclass=vclass, platform=plat)`. Ensure `--list` prints `plan` (which now includes `lessons`) — it already serializes the plan dict.

- [ ] **Step 4: Run the test, then commit**

Run: `pytest r2g-rtl2gds/tests/test_diagnose_symptom_lookup.py -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py r2g-rtl2gds/tests/test_diagnose_symptom_lookup.py
git commit -m "feat(skill): surface matched prose lesson in signoff diagnosis output"
```

---

## Task 17: Author front-matter on two real sections + wire sync into the post-ingest hook

**Files:**
- Modify: `r2g-rtl2gds/references/failure-patterns.md` (add front-matter to the LVS symmetric-matcher section and the nangate45 antenna section)
- Modify: `r2g-rtl2gds/knowledge/fix_log_manager.py` (`manage()` 115-130)
- Test: `r2g-rtl2gds/tests/test_fix_log_manager.py`

- [ ] **Step 1: Add front-matter to the two sections** (find them by heading; do not alter prose). Example for the LVS symmetric-matcher section — insert the comment block right under the `## ` heading:

```html
<!-- r2g-lesson:
id: lesson-lvs-symmetric-matcher
status: active
trigger: {check: lvs, class: symmetric_matcher, platform: "*"}
strategy_ids: [lvs_same_nets_seed]
-->
```

And for the nangate45 antenna repair section (platform-gated):

```html
<!-- r2g-lesson:
id: lesson-nangate45-antenna-diode
status: active
trigger: {check: drc, class: METAL1_ANTENNA, platform: nangate45}
strategy_ids: [antenna_diode_repair]
-->
```

- [ ] **Step 2: Write the failing test** (manage triggers a lesson sync):

```python
def test_manage_runs_lesson_sync(tmp_path, tmp_knowledge_dir, monkeypatch):
    db = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    conn.commit(); conn.close()
    md = tmp_path / "failure-patterns.md"
    md.write_text("# F\n\n## Sym\n<!-- r2g-lesson:\nid: l-sym\nstatus: active\n"
                  'trigger: {check: lvs, class: symmetric_matcher, platform: "*"}\n-->\nx\n')
    import sync_lessons
    monkeypatch.setattr(sync_lessons, "_DEFAULT_DOCS", [md])
    fix_log_manager.manage(db, out_path=tmp_knowledge_dir / "heuristics.json")
    conn = knowledge_db.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] == 1
    conn.close()
```

- [ ] **Step 3: Run it to verify it fails**

Run: `pytest r2g-rtl2gds/tests/test_fix_log_manager.py::test_manage_runs_lesson_sync -v`
Expected: FAIL — lessons table empty (manage doesn't sync).

- [ ] **Step 4: Call sync in `manage()`** — after the `learn_heuristics.learn(...)` line, before `archive_old_raw`:

```python
    if autolearn:
        import learn_heuristics
        learn_heuristics.learn(db_path, out_path)
        try:
            import sync_lessons
            conn = knowledge_db.connect(db_path)
            sync_lessons.sync(conn)
            conn.close()
        except Exception as e:               # sync must never break ingest
            import sys
            print(f"[fix_log_manager] lesson sync skipped: {e}", file=sys.stderr)
```

- [ ] **Step 5: Run the test, then commit**

Run: `pytest r2g-rtl2gds/tests/test_fix_log_manager.py -v`
Expected: PASS.

```bash
git add r2g-rtl2gds/references/failure-patterns.md r2g-rtl2gds/knowledge/fix_log_manager.py r2g-rtl2gds/tests/test_fix_log_manager.py
git commit -m "feat(skill): author lesson front-matter + sync lessons in post-ingest hook"
```

---

## Task 18: Resolve dead-module status (Phase 0.3) — wire/document, don't delete

`search_failures` is now wired (Tasks 15-16). `monitor_health.py` and `analyze_execution.py` have tests + README references + `analyze_execution.rank_proposals` is used in-process — do NOT delete. Make them non-"unqueried" by documenting their role and surfacing `monitor_health` in the dashboard health panel reference.

**Files:**
- Modify: `r2g-rtl2gds/SKILL.md` (the knowledge-store section)
- Modify: `r2g-rtl2gds/knowledge/README.md` (note search_failures is now a decision-path consumer)

- [ ] **Step 1: Document** — in `SKILL.md`, add one line under the knowledge-store bullet:
  "Symptom retrieval: `diagnose_signoff_fix.py` surfaces matching active prose lessons (via `search_failures.lessons_for_symptom`) at the fix-decision point. `monitor_health.py` (degradation alerts) and `analyze_execution.py` (fix-proposal triage) are operator-invoked CLIs over the same store."
- [ ] **Step 2: Commit** (docs only, no test):

```bash
git add r2g-rtl2gds/SKILL.md r2g-rtl2gds/knowledge/README.md
git commit -m "docs(skill): document symptom retrieval + clarify monitor_health/analyze_execution roles"
```

---

## Task 19: Full-suite regression + re-learn the live store

**Files:** none (validation)

- [ ] **Step 1: Run the full suite**

Run: `pytest r2g-rtl2gds/ -q`
Expected: all pass (baseline + new tests). Fix any regression before proceeding.

- [ ] **Step 2: Re-learn the real store** so the symptom projection materializes over the existing 417 nangate45 trajectories (coarse backfill from `violation_class`):

```bash
cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds/knowledge
python3 learn_heuristics.py --db runs.sqlite --out heuristics.json
python3 -c "import json; d=json.load(open('heuristics.json')); print('symptoms:', len(d.get('symptoms',{})))"
python3 sync_lessons.py
```

Expected: `symptoms: N` (N>0); `lessons` synced. Inspect a couple of `symptoms[...]` buckets to confirm `evidence_designs` carries design names and keys are hashes (no family names).

- [ ] **Step 3: Commit the regenerated store**

```bash
git add r2g-rtl2gds/knowledge/heuristics.json r2g-rtl2gds/knowledge/runs.sqlite
git commit -m "chore(knowledge): regenerate store with symptom projection + lessons"
```

---

# Validation on sky130hd

## Task 20: Cross-platform symptom transfer (deterministic fixture test)

Proves a symptom learned on nangate45 is retrieved for a sky130hd run with the same symptom — no real EDA flow needed.

**Files:**
- Test: `r2g-rtl2gds/tests/test_sky130_transfer.py` (new)

- [ ] **Step 1: Write the failing test**

```python
import importlib, json
knowledge_db = importlib.import_module("knowledge_db")
symptom = importlib.import_module("symptom")
dsf = importlib.import_module("diagnose_signoff_fix")


def test_nangate45_symptom_transfers_to_sky130hd(tmp_path):
    sig = {"check": "lvs", "class": "symmetric_matcher", "predicates": {}}
    sid = symptom.symptom_id(sig)
    heur = tmp_path / "heuristics.json"
    heur.write_text(json.dumps({"symptoms": {sid: {
        "check": "lvs", "class": "symmetric_matcher", "predicates": {},
        "platforms_seen": ["nangate45"], "evidence_designs": ["d_nan"],
        "n_sessions": 6,
        "strategies": {"lvs_same_nets_seed": {
            "attempts": 6, "successes": 5, "failures": 1, "wins": 0,
            "platform_specific": False,
            "by_platform": {"nangate45": {"attempts": 6, "successes": 5,
                                          "failures": 1, "wins": 0}}}}}}}))
    lvs = {"status": "fail", "mismatch_class": "symmetric_matcher"}
    # sky130hd run, no sky130 evidence: pooled prior carries nangate45 experience.
    recipe, pooled = dsf.load_symptom_recipe(
        check="lvs", platform="sky130hd", drc={}, lvs=lvs, heuristics=heur)
    ranked = __import__("fix_model").rank_strategies(
        recipe, ["lvs_same_nets_seed", "lvs_resolve_unknown"], pooled=pooled)
    top = ranked[0]
    assert top["strategy"] == "lvs_same_nets_seed"
    assert top["score"] > 0.7
    assert top["provenance"].startswith("prior")   # transferred, not local
```

- [ ] **Step 2: Run it to verify it fails, then (it should pass once Tasks 12-13 are in) confirm green**

Run: `pytest r2g-rtl2gds/tests/test_sky130_transfer.py -v`
Expected: PASS (this is an integration check over Tasks 12+13; if it fails, the prior/lookup wiring is wrong — fix there).

- [ ] **Step 3: Commit**

```bash
git add r2g-rtl2gds/tests/test_sky130_transfer.py
git commit -m "test(skill): sky130hd inherits nangate45 symptom via pooled prior"
```

---

## Task 21: Platform-specific gating (deterministic fixture test)

Proves a nangate45-deck-specific fix does NOT transfer to sky130hd.

**Files:**
- Test: `r2g-rtl2gds/tests/test_sky130_transfer.py` (add)

- [ ] **Step 1: Write the failing test**

```python
def test_platform_specific_strategy_not_transferred(tmp_path):
    sig = {"check": "drc", "class": "METAL1_ANTENNA", "predicates": {}}
    sid = symptom.symptom_id(sig)
    heur = tmp_path / "heuristics.json"
    heur.write_text(json.dumps({"symptoms": {sid: {
        "check": "drc", "class": "METAL1_ANTENNA", "predicates": {},
        "platforms_seen": ["nangate45"], "evidence_designs": ["d_nan"],
        "n_sessions": 8,
        "strategies": {"antenna_diode_repair": {
            "attempts": 8, "successes": 8, "failures": 0, "wins": 0,
            "platform_specific": True,        # nangate45 deck only
            "by_platform": {"nangate45": {"attempts": 8, "successes": 8,
                                          "failures": 0, "wins": 0}}}}}}}))
    drc = {"categories": {"METAL1_ANTENNA": {"count": 5}}}
    recipe, pooled = dsf.load_symptom_recipe(
        check="drc", platform="sky130hd", drc=drc, lvs={}, heuristics=heur)
    # platform_specific -> excluded from the cross-platform pooled prior.
    assert "antenna_diode_repair" not in pooled
```

- [ ] **Step 2: Run it to verify it passes (gating from Task 13), then commit**

Run: `pytest r2g-rtl2gds/tests/test_sky130_transfer.py -v`
Expected: PASS both transfer + gating tests.

```bash
git add r2g-rtl2gds/tests/test_sky130_transfer.py
git commit -m "test(skill): platform_specific antenna fix not transferred to sky130hd"
```

---

## Task 22: Real sky130hd end-to-end validation (operator-driven)

Confirms the deterministic transfer behavior on a real flow + the extraction regression guard. Multi-hour EDA flow — run by the operator.

**Files:** none (operational); records results in `references/lessons-learned.md`

- [ ] **Step 1: Set up 3 small sky130hd designs** under `design_cases/` (e.g. a small ALU, a small FIFO, and one likely to hit antennas), each with `export PLATFORM = sky130hd` in `constraints/config.mk`. Run the full flow per `SKILL.md` (synth → ORFS → DRC → LVS → RCX).

- [ ] **Step 2: Extraction regression guard** — after each run:

```bash
python3 -c "import json; d=json.load(open('design_cases/<d>/reports/ppa.json')); \
g=d.get('geometry',{}); print('cells', g.get('instance_count'), 'area', g.get('die_area_um2'))"
```

Expected: non-zero cell count + area (guards the historical sky130 quote-bug, fixed in `363a8b2`). If zero/UNKNOWN, STOP and re-open the quote-bug.

- [ ] **Step 3: Ingest + re-learn + assert transfer**

```bash
cd r2g-rtl2gds/knowledge
python3 ingest_run.py ../../design_cases/<d>
python3 learn_heuristics.py --db runs.sqlite --out heuristics.json
# For a sky130hd run that hit an LVS symmetric-matcher symptom, confirm the
# matching nangate45-learned lesson + recipe is retrieved:
python3 ../scripts/reports/diagnose_signoff_fix.py ../../design_cases/<d> --check lvs --list \
  | python3 -c "import json,sys; p=json.load(sys.stdin); print('lessons', [l['id'] for l in p.get('lessons',[])]); print('ranking', [(r['strategy'],r['provenance']) for r in p.get('ranking',[])])"
```

Expected: the `*`-platform LVS lesson appears; the top strategy's provenance is `prior(...)` (transferred from nangate45). After ingest, `symptoms[<sid>].platforms_seen` includes `sky130hd` and `evidence_designs` includes the sky130 design.

- [ ] **Step 4: Record the result** in `references/lessons-learned.md` (dated note: which symptoms transferred, gating held, extraction clean) and commit the regenerated store + note.

```bash
git add r2g-rtl2gds/knowledge/runs.sqlite r2g-rtl2gds/knowledge/heuristics.json r2g-rtl2gds/references/lessons-learned.md
git commit -m "feat(skill): validate symptom transfer + gating on sky130hd (real flow)"
```

> If real sky130hd flows cannot run in this session, Tasks 20-21 (deterministic fixtures) already prove the transfer/gating logic; mark Task 22 pending an operator run and do not fabricate results.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- §4.1 symptom signature + raw capture → Tasks 2, 3, 8, 9, 10. ✔
- §4.2 re-keyed symptom recipes (pooled, by_platform, evidence_designs) → Task 11. ✔
- §4.3 informed priors → Task 12 + lookup Task 13. ✔
- §4.4 lessons table + front-matter + sync + in-context retrieval → Tasks 14, 15, 16, 17. ✔
- §4.5 config_delta + env_flags capture → Tasks 6, 7, 8. ✔
- §4.6 shape-indexed config → **Phase 2, out of scope** (spec defers it). Noted, no task. ✔
- §5 Phase 0 honesty gate: A/B run → Task 5; lineage outcome → Task 4; dead modules → Tasks 15/16/18. ✔
- §6 sky130hd validation → Tasks 20, 21, 22. ✔
- §7 invariants: idempotent ingest (Tasks 4, 8 ON CONFLICT), is_success single source (reused in Task 4), no prose auto-promotion (Task 14 one-way sync), raw rebuildable (Task 11 derived projection + Task 19 re-learn), family never a key (Task 11 test asserts it). ✔
- §8 schema additions + migration + symptoms table → Tasks 1, 2. ✔

**Placeholder scan:** No TBD/“implement later”. Shell stub wiring in Task 6 references reading `fix_signoff.sh:1-60` for env-var names (a real, bounded lookup, not a placeholder). Task 5 and Task 22 are explicitly operator-driven with anti-fabrication notes.

**Type/name consistency:** `symptom.canonical_signature`/`symptom_id`/`predicates_for`/`from_fix_log_row`, `load_symptom_recipe(check,platform,drc,lvs,heuristics)`, `rank_strategies(recipe, static_order, pooled=)`, `lessons_for_symptom(check,vclass,platform,patterns_path)`, `sync_lessons.sync(conn, patterns_path=)` — names are used identically across Tasks 3, 8-16, 20-21. `symptoms[symptom_id]` bucket shape (check/class/predicates/platforms_seen/evidence_designs/n_sessions/strategies{by_platform,platform_specific}) is consistent between Task 11 (writer) and Tasks 13/20/21 (readers).
