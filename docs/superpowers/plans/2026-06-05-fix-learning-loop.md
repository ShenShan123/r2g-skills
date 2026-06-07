# Fix-Learning Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `r2g-rtl2gds` skill learn from every violation-fixing iteration (timing/DRC/LVS), turning success / failure / failure→success episodes into a per-family/per-platform database that proposes evidence-ranked next moves — then re-run the whole RTL corpus to enrich it.

**Architecture:** Replicate the proven Fmax `observe → learn → persist → apply` pattern. Three re-derivable, lossless tiers in the SQLite knowledge store: **Tier-1 `fix_events`** (append-only raw per-iteration), **Tier-2 `fix_trajectories`** (per-episode path), **Tier-3 `fix_recipes`** (per-family aggregate in `heuristics.json`). A pure `fix_model.py` ranks strategies (negative learning + smoothing). The existing `fix_signoff.sh` loop and `diagnose_signoff_fix.py` are wired to record and consume it. Hard safety clamps stay absolute.

**Tech Stack:** Python 3.10+ (stdlib only: `sqlite3`, `json`, `statistics`, `argparse`), Bash, pytest. No new dependencies. All new Python lives under `r2g-rtl2gds/`; tests under `r2g-rtl2gds/tests/`.

**Design spec:** `docs/superpowers/specs/2026-06-05-fix-learning-loop-design.md`

**Branch:** create `feat/fix-learning-loop` off `main` before Task 1 (we are currently on `feat/fmax-search`).

---

## File Structure

**New files:**
- `r2g-rtl2gds/scripts/reports/fix_model.py` — pure strategy-ranking model (no I/O).
- `r2g-rtl2gds/knowledge/fix_log_manager.py` — autonomous manager: config-normalized merge key, detail-blob bounding, auto-run `manage()`, archive-on-threshold (Tasks 2B, 5B).
- `r2g-rtl2gds/knowledge/backfill_fix_events.py` — mine `design_cases/_batch/*.jsonl` → `fix_events`.
- `r2g-rtl2gds/knowledge/repair_run_status.py` — reconcile dead `orfs_status='partial'` rows from reports.
- `r2g-rtl2gds/tests/test_fix_model.py`, `test_fix_log_manager.py`, `test_ingest_fix_events.py`, `test_learn_fix.py`, `test_diagnose_ranking.py`, `test_backfill_fix_events.py`, `test_repair_run_status.py`, `test_fix_signoff_log.py`, `test_fix_signoff_adaptive.py`, `test_check_timing_journal.py`.

**Key correctness invariant (archival-safe learning):** Tier-3 `fix_recipes` is derived from **Tier-2 `fix_trajectories`** (whose `path_json` captures every iteration's strategy + verdict), **not** from raw `fix_events`. `fix_trajectories` is tiny and is **never archived**. So when old raw `fix_events` are archived to a separate file to bound DB size, **no learning signal is lost** — the trajectory IS the durable merged record. Raw `fix_events` carries the lossless forensic detail (categories, rule samples, configs) and is recoverable from the archive.

**Modified files:**
- `r2g-rtl2gds/knowledge/schema.sql` — add 3 tables (Task 1).
- `r2g-rtl2gds/scripts/flow/fix_signoff.sh` — enrich `fix_log.jsonl` + session id (Task 3).
- `r2g-rtl2gds/knowledge/ingest_run.py` — read `fix_log.jsonl` → `fix_events`; write `run_violations` (Task 4).
- `r2g-rtl2gds/knowledge/learn_heuristics.py` — derive `fix_trajectories` + `fix_recipes` (Task 5).
- `r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py` — rank via `fix_model`; `--list` (Task 6).
- `r2g-rtl2gds/scripts/reports/check_timing.py` — `--journal` subcommand (Task 7).
- `r2g-rtl2gds/knowledge/analyze_execution.py` — rank backend proposals by history (Task 8).
- `r2g-rtl2gds/scripts/reports/build_lineage_view.py` — fix-effectiveness projection (Task 9).
- `r2g-rtl2gds/knowledge/eval_heuristics.py` — empirical-vs-static arm (Task 10).
- `r2g-rtl2gds/knowledge/mine_rules.py` — evidence-backed fix candidates (Task 11).
- `r2g-rtl2gds/SKILL.md`, `references/signoff-fixing.md`, `references/orfs-playbook.md`, `knowledge/README.md` — docs (Task 14).

**Verdict vocabulary (canonical, used everywhere):** `cleared` | `win` | `no_change` | `regression` | `inconclusive`. The shell emits its legacy strings (`applied`/`no_improvement`/`stop_*`/`apply_failed`/`rerun_failed_rc*`); the **ingester normalizes** them to this vocabulary (Task 4) so the shell change stays minimal.

---

## Part A — The mechanism (TDD)

### Task 0: Branch

- [ ] **Step 1: Create the feature branch**

Run:
```bash
cd /proj/workarea/user5/agent-r2g
git checkout main && git checkout -b feat/fix-learning-loop
git add docs/superpowers/specs/2026-06-05-fix-learning-loop-design.md docs/superpowers/plans/2026-06-05-fix-learning-loop.md
git commit -m "docs(fix-learning): spec + implementation plan"
```
Expected: new branch `feat/fix-learning-loop`, spec + plan committed.

> Note: the spec/plan files are currently untracked on `feat/fmax-search`; `git checkout -b` carries untracked files to the new branch, so they commit cleanly here.

---

### Task 1: Schema — three new tables

**Files:**
- Modify: `r2g-rtl2gds/knowledge/schema.sql`
- Test: `r2g-rtl2gds/tests/test_ingest_fix_events.py` (schema-creation test added first)

`CREATE TABLE IF NOT EXISTS` reaches existing DBs through `ensure_schema` with no column migration needed (migrations are only for new *columns* on the existing `runs` table). These are whole new tables.

- [ ] **Step 1: Write the failing test**

Create `r2g-rtl2gds/tests/test_ingest_fix_events.py`:
```python
"""Tests for fix-event ingestion + the three new knowledge tables."""
from __future__ import annotations

import knowledge_db


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def test_schema_creates_fix_tables(tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    tables = _tables(conn)
    assert {"fix_events", "fix_trajectories", "run_violations", "fix_events_archive"} <= tables
    # fix_events has the lossless detail columns + the idempotency constraint
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fix_events)")}
    assert {"fix_session_id", "iter", "strategy", "violation_class", "verdict",
            "before_categories_json", "after_categories_json", "rule_details_json",
            "cumulative_config_json", "env_flags_json", "tool_versions_json",
            "stage_metrics_json", "provenance"} <= cols
    conn.close()


def test_fix_events_unique_constraint(tmp_knowledge_dir):
    import sqlite3
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    ins = ("INSERT OR IGNORE INTO fix_events "
           "(fix_session_id, iter, strategy) VALUES (?,?,?)")
    conn.execute(ins, ("sess1", 1, "antenna_diode_repair"))
    conn.execute(ins, ("sess1", 1, "antenna_diode_repair"))  # dup -> ignored
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0]
    assert n == 1
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_ingest_fix_events.py -v`
Expected: FAIL — `fix_events` not in tables.

- [ ] **Step 3: Add the tables to `schema.sql`**

Append to `r2g-rtl2gds/knowledge/schema.sql`:
```sql

-- ── Fix-Learning Loop (spec 2026-06-05) ──────────────────────────────────
-- Tier-1: append-only raw, one row per fix iteration (lossless system of record).
CREATE TABLE IF NOT EXISTS fix_events (
    fix_event_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fix_session_id          TEXT NOT NULL,           -- episode key (stable across re-ingest)
    project_path            TEXT,
    design_name             TEXT,
    design_family           TEXT,
    platform                TEXT,
    check_type              TEXT,                    -- timing | drc | lvs
    violation_class         TEXT,                    -- dominant DRC cat | lvs mismatch_class | timing tier
    iter                    INTEGER,
    strategy                TEXT,
    from_stage              TEXT,                    -- ORFS rerun-from stage
    before_count            REAL,
    after_count             REAL,
    before_categories_json  TEXT,                    -- full category vector (D9)
    after_categories_json   TEXT,
    rule_details_json       TEXT,                    -- rule/net/path specifics where emitted
    before_status           TEXT,
    after_status            TEXT,
    verdict                 TEXT,                    -- cleared|win|no_change|regression|inconclusive
    config_delta_json       TEXT,                    -- this iteration's config.mk edit
    cumulative_config_json  TEXT,                    -- full applied-fix block snapshot
    env_flags_json          TEXT,                    -- PLACE_FAST/ROUTE_FAST/SKIP_ANTENNA_REPAIR/...
    tool_versions_json      TEXT,                    -- openroad/klayout/yosys/orfs
    stage_metrics_json      TEXT,                    -- per-stage slacks/area/power/IR
    stacked                 INTEGER,                 -- 1 if prior edits still in effect
    elapsed_s               REAL,
    ts                      TEXT,
    provenance              TEXT,                    -- live | backfill:<source>
    UNIQUE(fix_session_id, iter, strategy)
);
CREATE INDEX IF NOT EXISTS idx_fix_events_session ON fix_events(fix_session_id);
CREATE INDEX IF NOT EXISTS idx_fix_events_fam
    ON fix_events(design_family, platform, check_type, violation_class);

-- Tier-2: per-episode trajectory (re-derivable from fix_events; materialized).
CREATE TABLE IF NOT EXISTS fix_trajectories (
    fix_session_id          TEXT PRIMARY KEY,
    project_path            TEXT,
    design_name             TEXT,
    design_family           TEXT,
    platform                TEXT,
    check_type              TEXT,
    violation_class         TEXT,
    path_json               TEXT,                    -- ordered [{iter,strategy,before,after,verdict}]
    n_iters                 INTEGER,
    outcome                 TEXT,                    -- resolved | abandoned
    winning_strategy        TEXT,
    winning_config_json     TEXT,
    failed_strategies_json  TEXT,
    initial_count           REAL,
    final_count             REAL,
    total_elapsed_s         REAL
);
CREATE INDEX IF NOT EXISTS idx_fix_traj_fam
    ON fix_trajectories(design_family, platform, check_type, violation_class);

-- Per-run violation snapshot (EVERY run, incl. clean) — the complete landscape (D9).
CREATE TABLE IF NOT EXISTS run_violations (
    run_id                  TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    design_family           TEXT,
    platform                TEXT,
    drc_status              TEXT,
    drc_categories_json     TEXT,
    lvs_status              TEXT,
    lvs_mismatch_class      TEXT,
    timing_tier             TEXT,
    wns_ns                  REAL,
    snapshot_ts             TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_violations_fam ON run_violations(design_family, platform);

-- Cold archive for raw fix_events evicted past the size threshold (D13). Same columns
-- as fix_events (no autoincrement PK / UNIQUE — it's a sink). ensure_schema creates it
-- in both the main DB and the separate knowledge/fix_events_archive.sqlite file.
CREATE TABLE IF NOT EXISTS fix_events_archive (
    fix_event_id INTEGER, fix_session_id TEXT, project_path TEXT, design_name TEXT,
    design_family TEXT, platform TEXT, check_type TEXT, violation_class TEXT, iter INTEGER,
    strategy TEXT, from_stage TEXT, before_count REAL, after_count REAL,
    before_categories_json TEXT, after_categories_json TEXT, rule_details_json TEXT,
    before_status TEXT, after_status TEXT, verdict TEXT, config_delta_json TEXT,
    cumulative_config_json TEXT, env_flags_json TEXT, tool_versions_json TEXT,
    stage_metrics_json TEXT, stacked INTEGER, elapsed_s REAL, ts TEXT, provenance TEXT
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_ingest_fix_events.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/knowledge/schema.sql r2g-rtl2gds/tests/test_ingest_fix_events.py
git commit -m "feat(knowledge): fix_events/fix_trajectories/run_violations/archive schema"
```

---

### Task 1B: Make the knowledge store shippable (un-gitignore + commit)

**Files:** Modify `.gitignore`; commit `r2g-rtl2gds/knowledge/runs.sqlite` + `heuristics.json`.

Per spec **D14** the skill must ship pre-trained, so the store leaves `.gitignore`.

- [ ] **Step 1: Un-ignore the store.** Edit `.gitignore` — comment out lines 242–243:
```
# r2g-rtl2gds/knowledge/runs.sqlite      <- tracked: the skill ships pre-trained (spec D14)
# r2g-rtl2gds/knowledge/heuristics.json  <- tracked
```
Keep `design_cases/` (212) and `failure_candidates.json` (244) ignored. Add ignores for transient WAL/journal files:
```
r2g-rtl2gds/knowledge/*.sqlite-journal
r2g-rtl2gds/knowledge/*.sqlite-wal
```
- [ ] **Step 2: Verify** — `git check-ignore r2g-rtl2gds/knowledge/runs.sqlite` prints **nothing**.
- [ ] **Step 3: Commit the current store** as the shippable baseline:
```bash
git add .gitignore r2g-rtl2gds/knowledge/runs.sqlite r2g-rtl2gds/knowledge/heuristics.json
git commit -m "chore(knowledge): track the knowledge store so the skill ships pre-trained (D14)"
```
- [ ] **Step 4:** Runbook note — re-commit `runs.sqlite` + `heuristics.json` (+ `fix_events_archive.sqlite` once it exists) at each learning milestone (after Task 16 and each campaign wave), NOT every micro-run.

---

### Task 2: `fix_model.py` — pure strategy ranking

**Files:**
- Create: `r2g-rtl2gds/scripts/reports/fix_model.py`
- Test: `r2g-rtl2gds/tests/test_fix_model.py`

**Design note (divergence from `fmax_model.select_model`, deliberate):** Fmax *hard-gates* below `N_MIN` because using a wrong deterioration term silently declares false timing closure (high stakes). A mis-ranked fix strategy merely gets tried and falls through (low stakes, self-correcting), and the user explicitly wants *all* solutions proposed with priorities. So `fix_model` does **not** hard-gate — it always ranks by a Beta(1,1)-smoothed clearance score (`(successes+1)/(attempts+2)`), where an **untried** strategy scores the neutral prior `0.5`, a **proven winner** scores high, and a **proven loser** scores low but is never zeroed (still explorable). Confidence is surfaced via `n`/`provenance`, not by withholding the recommendation.

- [ ] **Step 1: Write the failing test**

Create `r2g-rtl2gds/tests/test_fix_model.py`:
```python
"""Unit tests for the pure fix-strategy ranking model."""
from __future__ import annotations
import pytest
import fix_model as fxm


STATIC = ["antenna_diode_repair", "antenna_density_relief", "lvs_macro_cdl"]


def test_cold_start_returns_static_order():
    ranked = fxm.rank_strategies(None, STATIC)
    assert [r["strategy"] for r in ranked] == STATIC
    assert all(r["provenance"] == "cold-start" for r in ranked)
    assert all(r["attempts"] == 0 for r in ranked)


def test_proven_winner_outranks_untried_outranks_loser():
    entry = {"strategies": {
        "antenna_density_relief": {"attempts": 11, "successes": 9, "failures": 2},
        "lvs_macro_cdl":          {"attempts": 3,  "successes": 0, "failures": 3},
    }, "n_sessions": 14}
    ranked = fxm.rank_strategies(entry, STATIC)
    order = [r["strategy"] for r in ranked]
    # winner (9/11) first, untried diode_repair (0.5 prior) middle, loser (0/3) last
    assert order[0] == "antenna_density_relief"
    assert order[1] == "antenna_diode_repair"      # untried -> neutral prior 0.5
    assert order[2] == "lvs_macro_cdl"             # proven loser, but still present
    assert ranked[2]["score"] < 0.5 < ranked[0]["score"]


def test_smoothing_tames_single_lucky_win():
    entry = {"strategies": {"antenna_diode_repair": {"attempts": 1, "successes": 1, "failures": 0}},
             "n_sessions": 1}
    ranked = fxm.rank_strategies(entry, STATIC)
    win = next(r for r in ranked if r["strategy"] == "antenna_diode_repair")
    # 1/1 -> (1+1)/(1+2)=0.667, only just above the 0.5 untried prior.
    assert win["score"] == pytest.approx(2/3, abs=1e-6)


def test_evidence_and_provenance_surfaced():
    entry = {"strategies": {"antenna_density_relief": {"attempts": 6, "successes": 5,
             "failures": 1, "median_reduction_pct": 0.97}}, "n_sessions": 6}
    ranked = fxm.rank_strategies(entry, STATIC)
    top = next(r for r in ranked if r["strategy"] == "antenna_density_relief")
    assert top["provenance"].startswith("learned(n=6")
    assert top["successes"] == 5 and top["failures"] == 1
    assert "median_reduction_pct" in top


def test_never_drops_a_static_strategy():
    entry = {"strategies": {"antenna_diode_repair": {"attempts": 2, "successes": 2, "failures": 0}},
             "n_sessions": 2}
    ranked = fxm.rank_strategies(entry, STATIC)
    assert set(r["strategy"] for r in ranked) == set(STATIC)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_fix_model.py -v`
Expected: FAIL — `No module named 'fix_model'`.

- [ ] **Step 3: Write `fix_model.py`**

Create `r2g-rtl2gds/scripts/reports/fix_model.py`:
```python
#!/usr/bin/env python3
"""Pure strategy-ranking model for the fix-learning loop. No I/O, no subprocess
— fully unit-testable. Mirrors the role of fmax_model.py but for violation-fix
strategy selection (spec 2026-06-05 §5.3 / §6).

A "recipe entry" is the Tier-3 aggregate for one (check_type, violation_class):
    {"strategies": {strategy_id: {"attempts","successes","failures",
                                  "median_reduction_pct"?}}, "n_sessions": int}

rank_strategies() returns ALL static-catalog strategies, priority-ordered, each
annotated with evidence. Untried strategies get the neutral Beta(1,1) prior so
they are explored after proven winners but before proven losers; a proven loser
is down-ranked, never zeroed (never permanently blacklisted).
"""
from __future__ import annotations

# Smoothing prior: Beta(1,1). score = (successes + 1) / (attempts + 2).
# attempts=0 -> 0.5 (neutral); 9/11 -> 0.77; 0/3 -> 0.2.
def _score(successes: int, attempts: int) -> float:
    return (successes + 1) / (attempts + 2)


def rank_strategies(recipe_entry: dict | None, static_order: list[str]) -> list[dict]:
    """Rank `static_order` strategies by smoothed historical clearance.

    recipe_entry: Tier-3 aggregate for this (check, violation_class), or None
                  (cold start). static_order: catalog order (the deterministic
                  tiebreaker and the full set that must always be returned).
    """
    stats = (recipe_entry or {}).get("strategies", {})
    n_sessions = (recipe_entry or {}).get("n_sessions", 0)
    ranked: list[dict] = []
    for pos, sid in enumerate(static_order):
        s = stats.get(sid)
        if s:
            attempts = int(s.get("attempts", 0))
            successes = int(s.get("successes", 0))
            failures = int(s.get("failures", max(0, attempts - successes)))
            score = _score(successes, attempts)
            prov = f"learned(n={n_sessions},tried={attempts})"
        else:
            attempts = successes = failures = 0
            score = _score(0, 0)  # 0.5 neutral prior
            prov = "cold-start"
        item = {
            "strategy": sid, "score": score, "static_pos": pos,
            "attempts": attempts, "successes": successes, "failures": failures,
            "provenance": prov,
        }
        if s and s.get("median_reduction_pct") is not None:
            item["median_reduction_pct"] = s["median_reduction_pct"]
        ranked.append(item)
    # Primary: score desc. Secondary: catalog position asc (stable, deterministic).
    ranked.sort(key=lambda r: (-r["score"], r["static_pos"]))
    return ranked
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_fix_model.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/scripts/reports/fix_model.py r2g-rtl2gds/tests/test_fix_model.py
git commit -m "feat(reports): pure fix-strategy ranking model (negative learning + smoothing)"
```

---

### Task 2B: `fix_log_manager.py` — autonomous merge helpers (pure)

**Files:**
- Create: `r2g-rtl2gds/knowledge/fix_log_manager.py` (pure helpers; `manage()`/archive land in Task 5B once `learn` exists)
- Test: `r2g-rtl2gds/tests/test_fix_log_manager.py`

Implements the config-normalized merge key (D11) + detail-blob bounding (D13).

- [ ] **Step 1: Write the failing test**

Create `r2g-rtl2gds/tests/test_fix_log_manager.py`:
```python
"""Unit tests for the autonomous fix-log manager (pure helpers)."""
from __future__ import annotations
import json
import fix_log_manager as flm


def test_canonical_key_merges_within_tolerance():
    e1 = {"check_type": "drc", "violation_class": "M2_ANTENNA",
          "strategy": "antenna_density_relief",
          "cumulative_config_json": json.dumps({"CORE_UTILIZATION": "15"})}
    e2 = dict(e1, cumulative_config_json=json.dumps({"CORE_UTILIZATION": "14"}))  # within 15%
    e3 = dict(e1, cumulative_config_json=json.dumps({"CORE_UTILIZATION": "5"}))   # far
    assert flm.canonical_action_key(e1) == flm.canonical_action_key(e2)
    assert flm.canonical_action_key(e1) != flm.canonical_action_key(e3)


def test_canonical_key_keeps_violation_class_distinct():
    base = {"check_type": "drc", "strategy": "antenna_diode_repair",
            "cumulative_config_json": "{}"}
    assert (flm.canonical_action_key(dict(base, violation_class="M2_ANTENNA"))
            != flm.canonical_action_key(dict(base, violation_class="M3_ANTENNA")))


def test_dedup_collapses_repeats_keeps_last():
    evs = [{"iter": 1, "check_type": "drc", "violation_class": "M2_ANTENNA",
            "strategy": "antenna_diode_repair", "cumulative_config_json": "{}", "after_count": 9},
           {"iter": 2, "check_type": "drc", "violation_class": "M2_ANTENNA",
            "strategy": "antenna_diode_repair", "cumulative_config_json": "{}", "after_count": 3}]
    out = flm.dedup_events_by_action(evs)
    assert len(out) == 1 and out[0]["after_count"] == 3   # freshest wins


def test_bound_rule_details_caps_samples():
    b = flm.bound_rule_details({"samples": list(range(100))}, top_n=20)
    assert b["total"] == 100 and len(b["samples"]) == 20 and b["truncated"] is True
```

- [ ] **Step 2: Run → fail** (`No module named 'fix_log_manager'`).

- [ ] **Step 3: Write `fix_log_manager.py` (pure helpers)**

```python
#!/usr/bin/env python3
"""Autonomous fix-log manager (spec 2026-06-05 §5.5). PURE helpers here:
config-normalized merge key (D11) + detail-blob bounding (D13). Stateful
manage()/archive routines are added in the learn task once learn_heuristics
exists. Model/ranking logic stays in fix_model.py.
"""
from __future__ import annotations
import json
import math

CONFIG_TOL = 0.15            # ±15% numeric tolerance for "same action"
RULE_DETAIL_TOP_N = 20       # cap verbose per-violation detail (D13)
FIX_EVENTS_MAX_ROWS = 50000  # archive trigger (D13)
DB_MAX_MB = 200


def _bucket(val, tol: float) -> str:
    """Bucket a numeric value into a log-spaced band at relative tolerance `tol`,
    so near-equal values collapse; non-numeric values pass through verbatim."""
    try:
        f = float(val)
    except (TypeError, ValueError):
        return str(val)
    if f == 0:
        return "z"
    return ("+" if f > 0 else "-") + str(int(math.floor(math.log(abs(f)) / math.log(1.0 + tol))))


def canonical_action_key(event: dict, tol: float = CONFIG_TOL) -> tuple:
    """Config-normalized merge key (check, violation_class, strategy, config-sig).
    Same knob within `tol` collapses; distinct knobs/values stay separate."""
    raw = event.get("cumulative_config_json") or event.get("config_delta_json") or "{}"
    try:
        d = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        d = {}
    sig = tuple(sorted((k, _bucket(v, tol)) for k, v in d.items()))
    return (event.get("check_type"), event.get("violation_class"),
            event.get("strategy"), sig)


def dedup_events_by_action(events: list[dict], tol: float = CONFIG_TOL) -> list[dict]:
    """Collapse identical canonical actions within an episode to the LAST (freshest)
    occurrence so retries don't inflate counts. Ordered by iter."""
    ordered = sorted(events, key=lambda e: (e.get("iter") or 0))
    collapsed: dict[tuple, dict] = {}
    for e in ordered:
        collapsed[canonical_action_key(e, tol)] = e   # last wins
    return sorted(collapsed.values(), key=lambda e: (e.get("iter") or 0))


def bound_rule_details(details, top_n: int = RULE_DETAIL_TOP_N):
    """Cap a verbose detail blob: top_n sample entries + a total count (D13)."""
    if details is None:
        return None
    items = (details["samples"] if isinstance(details, dict) and "samples" in details
             else details if isinstance(details, list) else None)
    if items is None:
        return details
    return {"total": len(items), "samples": list(items)[:top_n], "truncated": len(items) > top_n}
```

- [ ] **Step 4: Run → PASS. Step 5: Commit**
```bash
git add r2g-rtl2gds/knowledge/fix_log_manager.py r2g-rtl2gds/tests/test_fix_log_manager.py
git commit -m "feat(knowledge): fix-log manager pure helpers (config-normalized merge + blob bound)"
```

---

### Task 3: Enrich `fix_signoff.sh` — session id + lossless `fix_log.jsonl`

**Files:**
- Modify: `r2g-rtl2gds/scripts/flow/fix_signoff.sh` (the `_log_iter` function + session-id mint + `cleared` verdict)
- Test: `r2g-rtl2gds/tests/test_fix_signoff_log.py`

The shell keeps its existing verdict strings; it adds `fix_session_id`, `from_stage`, `violation_class`, the before/after category vectors, and the cumulative config block to each `fix_log.jsonl` line, and flips the final verdict to `cleared` when `after == 0`. The ingester (Task 4) normalizes the rest.

- [ ] **Step 1: Write the failing test**

Create `r2g-rtl2gds/tests/test_fix_signoff_log.py`:
```python
"""fix_signoff.sh emits an enriched, session-keyed fix_log.jsonl."""
from __future__ import annotations
import json
import os
import stat
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
FIX_SIGNOFF = SKILL / "scripts" / "flow" / "fix_signoff.sh"


def _stub(path: Path, body: str):
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_fix_log_has_session_id_and_violation_class(tmp_path):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n"
        "# >>> r2g signoff-fix (auto) >>>\nexport MAX_REPAIR_ANTENNAS_ITER_DRT = 10\n"
        "# <<< r2g signoff-fix (auto) <<<\n")
    # drc.json: one antenna category, 5 violations -> after stub makes it 0.
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 5,
         "categories": {"M2_ANTENNA": {"count": 5}}}))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    # diagnose --next yields one strategy then STOP; --apply is a no-op success.
    _stub(bindir / "diagnose.py",
          'if [[ "$*" == *"--next"* ]]; then\n'
          '  if [[ -f /tmp/_did_$$ ]]; then echo -e "STOP\\tresidual\\tdone"; \n'
          '  else echo -e "antenna_diode_repair\\troute\\tdrc"; fi\n'
          'elif [[ "$*" == *"--apply"* ]]; then touch /tmp/_did_$$; echo "{}"; fi')
    # run_orfs / run_drc: no-ops. extract_drc: write a CLEAN drc.json (count 0).
    _stub(bindir / "noop.sh", 'exit 0')
    _stub(bindir / "extract.py",
          'python3 - "$@" <<\'PY\'\nimport json,sys\n'
          'open(sys.argv[2],"w").write(json.dumps({"status":"clean","total_violations":0,"categories":{}}))\nPY')

    env = dict(os.environ,
               R2G_DIAGNOSE=str(bindir / "diagnose.py"),
               R2G_RUN_ORFS=str(bindir / "noop.sh"),
               R2G_RUN_DRC=str(bindir / "noop.sh"),
               R2G_EXTRACT_DRC=str(bindir / "extract.py"))
    subprocess.run(["bash", str(FIX_SIGNOFF), str(proj), "nangate45",
                    "--check", "drc", "--max-iters", "2"], env=env, check=False)

    lines = [json.loads(l) for l in (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    applied = [r for r in lines if r["strategy"] == "antenna_diode_repair"]
    assert applied, "expected an applied iteration row"
    row = applied[0]
    assert row["fix_session_id"]                       # minted, non-empty
    assert row["check"] == "drc"
    assert row["violation_class"] == "M2_ANTENNA"      # dominant category captured
    assert row["from_stage"] == "route"
    assert row["verdict"] == "cleared"                 # after == 0
    assert json.loads(row["before_categories"]) == {"M2_ANTENNA": {"count": 5}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_fix_signoff_log.py -v`
Expected: FAIL — `KeyError: 'fix_session_id'`.

- [ ] **Step 3: Edit `fix_signoff.sh`**

Add a session-id mint after `LOG="$REPORTS/fix_log.jsonl"` (after line 43):
```bash
# Stable episode id for this fixing run (spec §5.1). All iterations of this
# invocation share it; the ingester groups fix_events by it.
FIX_SESSION_ID="$(python3 -c 'import hashlib,sys,time
h=hashlib.sha1(); h.update(sys.argv[1].encode()); h.update(sys.argv[2].encode())
h.update(str(time.time()).encode()); print(h.hexdigest()[:16])' "$PROJECT_DIR" "$CHECK")"
```

Replace the entire `_log_iter()` function (lines 58-65) with the enriched version:
```bash
_log_iter() {  # check iter strategy before after verdict from_stage
  python3 -c 'import json,sys,os
check,it,strategy,before,after,verdict,from_stage=sys.argv[1:8]
proj,sid,logp=sys.argv[8],sys.argv[9],sys.argv[10]
rep=os.path.join(proj,"reports",check+".json")
def vclass_and_cats(p):
    try: d=json.load(open(p))
    except Exception: return None,None,None
    if check=="drc":
        cats=d.get("categories") or {}
        dom=max(cats,key=lambda k:(cats[k].get("count") or 0)) if cats else None
        return dom,cats,d.get("status")
    return d.get("mismatch_class"),{"mismatch_count":d.get("mismatch_count")},d.get("status")
vclass,cats,status=vclass_and_cats(rep)
# cumulative applied-fix block from config.mk marked region
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
       violation_class=vclass,after_status=status,
       before_categories=json.dumps(cats) if cats is not None else None,
       cumulative_config=json.dumps(cum,sort_keys=True),
       ts=sys.argv[11])
open(logp,"a").write(json.dumps(o)+"\n")' \
    "$1" "$2" "$3" "$4" "$5" "$6" "${7:-}" "$PROJECT_DIR" "$FIX_SESSION_ID" "$LOG" "$(date -u +%FT%TZ)"
}
```

Update the four `_log_iter` call sites in `fix_one()` to pass `from_stage` ($rerun) as the 7th arg and to use `cleared` when clean. Change line 80 (the STOP path) — leave as is but add the from_stage slot:
```bash
      _log_iter "$check" "$it" "none" "$before" "$before" "stop_${rerun}" ""
```
Change line 95 (rerun_failed):
```bash
        _log_iter "$check" "$it" "$sid" "$before" "$before" "rerun_failed_rc$rc" "$rerun"
```
Change line 86 (apply_failed):
```bash
      _log_iter "$check" "$it" "$sid" "$before" "$before" "apply_failed" "$rerun"
```
Make the budget **adaptive (D12)**: in the defaults line (`...MAX_ITERS=3; RESUME=0`) change `MAX_ITERS=3` → `MAX_ITERS=8` (the cap) and add `BASE_ITERS=3`; add `local noimp=0` to the `fix_one()` local declaration (line 68). Then replace lines 102-108 (verdict + log + clean check) with:
```bash
    verdict="applied"
    if [[ -n "$before" && -n "$after" ]] && python3 -c "import sys;sys.exit(0 if float('$after')>=float('$before') else 1)" 2>/dev/null; then
      verdict="no_improvement"; noimp=$((noimp+1))
    else
      noimp=0
    fi
    [[ "$after" == "0" ]] && verdict="cleared"
    _log_iter "$check" "$it" "$sid" "$before" "$after" "$verdict" "$rerun"
    echo "[$check] iter $it: $before -> $after ($verdict)"
    if [[ "$after" == "0" ]]; then echo "[$check] CLEAN"; return 0; fi
    # Adaptive budget (D12): past base, stop after 2 consecutive non-improving iters.
    if (( it >= BASE_ITERS && noimp >= 2 )); then
      echo "[$check] $noimp non-improving past base $BASE_ITERS; stopping"; return 0
    fi
```
The loop header `for ((it=1; it<=MAX_ITERS; it++))` now caps at 8; the early-stop enforces the base-3 / 2-strike rule. **Also add `tests/test_fix_signoff_adaptive.py`:** a steadily-improving stub (counts 100→80→60→40→20→0) runs >3 iters and clears; an immediately-stuck stub (count stays 50) logs exactly 3 iterations then stops.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_fix_signoff_log.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/scripts/flow/fix_signoff.sh r2g-rtl2gds/tests/test_fix_signoff_log.py
git commit -m "feat(flow): fix_signoff.sh emits session-keyed, lossless fix_log.jsonl"
```

---

### Task 4: Ingest `fix_log.jsonl` → `fix_events` + write `run_violations`

**Files:**
- Modify: `r2g-rtl2gds/knowledge/ingest_run.py` (add two helpers + two hook calls inside `ingest()`)
- Test: `r2g-rtl2gds/tests/test_ingest_fix_events.py` (extend)

- [ ] **Step 1: Write the failing test (append to `test_ingest_fix_events.py`)**

```python
import json as _json
from pathlib import Path
import ingest_run


def _mk_project(tmp_path, name="demo", platform="nangate45", drc_status="clean",
                fix_log=None):
    proj = tmp_path / name
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = {platform}\n")
    (proj / "reports" / "ppa.json").write_text(_json.dumps({"summary": {}, "geometry": {}}))
    (proj / "reports" / "drc.json").write_text(_json.dumps(
        {"status": drc_status, "total_violations": 0, "categories": {}}))
    if fix_log is not None:
        (proj / "reports" / "fix_log.jsonl").write_text(
            "\n".join(_json.dumps(r) for r in fix_log) + "\n")
    return proj


def test_ingest_reads_fix_log_into_fix_events(tmp_path, tmp_knowledge_dir):
    fix_log = [
        {"check": "drc", "iter": 1, "strategy": "antenna_density_relief",
         "before": "147", "after": "147", "verdict": "no_improvement",
         "from_stage": "floorplan", "fix_session_id": "sessA",
         "violation_class": "M3_ANTENNA",
         "before_categories": _json.dumps({"M3_ANTENNA": {"count": 147}}),
         "cumulative_config": _json.dumps({"CORE_UTILIZATION": "15"}),
         "ts": "2026-06-05T00:00:00Z"},
        {"check": "drc", "iter": 2, "strategy": "antenna_diode_repair",
         "before": "147", "after": "0", "verdict": "cleared",
         "from_stage": "route", "fix_session_id": "sessA",
         "violation_class": "M3_ANTENNA",
         "before_categories": _json.dumps({"M3_ANTENNA": {"count": 147}}),
         "cumulative_config": _json.dumps({"SKIP_ANTENNA_REPAIR": "1"}),
         "ts": "2026-06-05T00:01:00Z"},
    ]
    proj = _mk_project(tmp_path, fix_log=fix_log)
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    ingest_run.ingest(proj, conn,
                      families_path=tmp_knowledge_dir / "families.json")

    rows = list(conn.execute(
        "SELECT iter, strategy, verdict, violation_class, check_type, "
        "from_stage, design_family, platform FROM fix_events ORDER BY iter"))
    assert len(rows) == 2
    assert rows[0][2] == "no_change"      # 'no_improvement' normalized
    assert rows[1][2] == "cleared"
    assert rows[1][3] == "M3_ANTENNA"
    assert rows[1][4] == "drc" and rows[1][5] == "route"
    assert rows[0][6] == "demo" and rows[0][7] == "nangate45"  # identity backfilled

    # idempotent re-ingest: no duplicate fix_events
    ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    assert conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0] == 2
    conn.close()


def test_ingest_writes_run_violations_snapshot(tmp_path, tmp_knowledge_dir):
    proj = _mk_project(tmp_path, drc_status="clean")
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    run_id = ingest_run.ingest(proj, conn,
                               families_path=tmp_knowledge_dir / "families.json")
    rv = conn.execute("SELECT run_id, drc_status, design_family FROM run_violations "
                      "WHERE run_id=?", (run_id,)).fetchone()
    assert rv is not None and rv[1] == "clean" and rv[2] == "demo"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_ingest_fix_events.py -k "fix_log or run_violations" -v`
Expected: FAIL — no rows in `fix_events` / `run_violations`.

- [ ] **Step 3: Add helpers + hooks to `ingest_run.py`**

Add near the top-level helpers (after `_read_stage_log`, ~line 160):
```python
# Map the shell's legacy verdict strings to the canonical fix verdict vocabulary.
_VERDICT_MAP = {"cleared": "cleared", "applied": "win", "no_improvement": "no_change"}


def _normalize_verdict(raw: str | None, before, after) -> str:
    if raw in _VERDICT_MAP:
        v = _VERDICT_MAP[raw]
        # 'applied' with a worse count is a regression, not a win.
        if v == "win" and before is not None and after is not None and after > before:
            return "regression"
        if v == "win" and before is not None and after is not None and after == before:
            return "no_change"
        return v
    return "inconclusive"   # stop_* / apply_failed / rerun_failed_* / unknown


def _read_fix_log(project: Path) -> list[dict]:
    return _read_stage_log(project / "reports" / "fix_log.jsonl")


def _ingest_fix_events(conn: sqlite3.Connection, project: Path,
                       design_name: str, design_family: str, platform: str) -> int:
    """Read reports/fix_log.jsonl into fix_events (idempotent via UNIQUE)."""
    rows = _read_fix_log(project)
    n = 0
    for r in rows:
        sid = r.get("fix_session_id")
        if not sid:
            continue
        before = _to_float(r.get("before"))
        after = _to_float(r.get("after"))
        conn.execute(
            "INSERT OR IGNORE INTO fix_events "
            "(fix_session_id, project_path, design_name, design_family, platform, "
            " check_type, violation_class, iter, strategy, from_stage, "
            " before_count, after_count, before_categories_json, after_categories_json, "
            " before_status, after_status, verdict, cumulative_config_json, ts, provenance) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, str(project.resolve()), design_name, design_family, platform,
             r.get("check"), r.get("violation_class"), _to_int(r.get("iter")),
             r.get("strategy"), r.get("from_stage"), before, after,
             r.get("before_categories"), r.get("after_categories"),
             r.get("before_status"), r.get("after_status"),
             _normalize_verdict(r.get("verdict"), before, after),
             r.get("cumulative_config"), r.get("ts"), "live"))
        n += 1
    return n


def _write_run_violations(conn: sqlite3.Connection, run_id: str,
                          design_family: str, platform: str,
                          drc: dict, lvs: dict, tcheck: dict, wns) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO run_violations "
        "(run_id, design_family, platform, drc_status, drc_categories_json, "
        " lvs_status, lvs_mismatch_class, timing_tier, wns_ns, snapshot_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (run_id, design_family, platform, drc.get("status"),
         json.dumps(drc.get("categories") or {}, sort_keys=True),
         lvs.get("status"), lvs.get("mismatch_class"), tcheck.get("tier"), wns,
         _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"))
```

Inside `ingest()`, immediately before `_record_lineage(...)` (line 442), add:
```python
    _ingest_fix_events(conn, project, design_name, design_family, platform)
    _write_run_violations(conn, run_id, design_family, platform, drc, lvs, tcheck,
                          _to_float(timing.get("setup_wns")))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_ingest_fix_events.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/knowledge/ingest_run.py r2g-rtl2gds/tests/test_ingest_fix_events.py
git commit -m "feat(knowledge): ingest fix_log.jsonl into fix_events + run_violations snapshot"
```

---

### Task 5: Derive `fix_trajectories` + `fix_recipes` in `learn_heuristics.py`

**Files:**
- Modify: `r2g-rtl2gds/knowledge/learn_heuristics.py` (add a fix-aggregation pass; keep the DB conn open to write `fix_trajectories`)
- Test: `r2g-rtl2gds/tests/test_learn_fix.py`

- [ ] **Step 1: Write the failing test**

Create `r2g-rtl2gds/tests/test_learn_fix.py`:
```python
"""learn_heuristics derives fix_trajectories (Tier-2) + fix_recipes (Tier-3)."""
from __future__ import annotations
import json
import knowledge_db
import learn_heuristics


def _ev(conn, **row):
    cols = ("fix_session_id", "design_family", "platform", "check_type",
            "violation_class", "iter", "strategy", "before_count", "after_count",
            "verdict", "cumulative_config_json")
    d = dict.fromkeys(cols)
    d.update(row)
    ph = ", ".join(f":{c}" for c in cols)
    conn.execute(f"INSERT INTO fix_events ({', '.join(cols)}) VALUES ({ph})", d)


def _seed(conn):
    # Episode 1 (resolved): density_relief failed, diode_repair cleared.
    _ev(conn, fix_session_id="s1", design_family="ethernet", platform="nangate45",
        check_type="drc", violation_class="M2_ANTENNA", iter=1,
        strategy="antenna_density_relief", before_count=147, after_count=147,
        verdict="no_change")
    _ev(conn, fix_session_id="s1", design_family="ethernet", platform="nangate45",
        check_type="drc", violation_class="M2_ANTENNA", iter=2,
        strategy="antenna_diode_repair", before_count=147, after_count=0,
        verdict="cleared", cumulative_config_json='{"SKIP_ANTENNA_REPAIR": "1"}')
    # Episode 2 (abandoned): diode_repair tried, never cleared.
    _ev(conn, fix_session_id="s2", design_family="ethernet", platform="nangate45",
        check_type="drc", violation_class="M2_ANTENNA", iter=1,
        strategy="antenna_diode_repair", before_count=9, after_count=3,
        verdict="win")


def test_learn_emits_trajectories_and_recipes(tmp_knowledge_dir):
    db = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    _seed(conn)
    conn.commit()
    conn.close()

    out = tmp_knowledge_dir / "heuristics.json"
    data = learn_heuristics.learn(db, out)

    # Tier-3 recipes in heuristics.json
    rec = data["families"]["ethernet"]["platforms"]["nangate45"]["fix_recipes"]
    strat = rec["drc"]["M2_ANTENNA"]["strategies"]
    assert strat["antenna_diode_repair"]["successes"] == 1      # s1 cleared
    assert strat["antenna_diode_repair"]["attempts"] == 2       # s1 + s2
    assert strat["antenna_density_relief"]["failures"] == 1     # s1 no_change
    assert rec["drc"]["M2_ANTENNA"]["n_sessions"] == 2          # abandoned counted (survivorship)

    # Tier-2 trajectories materialized in the DB
    conn = knowledge_db.connect(db)
    traj = {r[0]: r for r in conn.execute(
        "SELECT fix_session_id, outcome, winning_strategy FROM fix_trajectories")}
    assert traj["s1"][1] == "resolved" and traj["s1"][2] == "antenna_diode_repair"
    assert traj["s2"][1] == "abandoned" and traj["s2"][2] is None
    conn.close()


def test_learn_fix_is_idempotent(tmp_knowledge_dir):
    db = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    _seed(conn)
    conn.commit()
    conn.close()
    out = tmp_knowledge_dir / "heuristics.json"
    learn_heuristics.learn(db, out)
    learn_heuristics.learn(db, out)   # re-derive from scratch
    conn = knowledge_db.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM fix_trajectories").fetchone()[0] == 2
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_learn_fix.py -v`
Expected: FAIL — `KeyError: 'fix_recipes'`.

- [ ] **Step 3: Edit `learn_heuristics.py`**

Add these module-level functions (after `_family_platform_entry`, ~line 132):
```python
def _build_trajectory(events: list[dict]) -> dict:
    """Collapse one episode's fix_events (sorted by iter) into a trajectory row."""
    events = sorted(events, key=lambda e: (e.get("iter") or 0))
    first = events[0]
    path = [{"iter": e.get("iter"), "strategy": e.get("strategy"),
             "before": e.get("before_count"), "after": e.get("after_count"),
             "verdict": e.get("verdict")} for e in events]
    win = next((e for e in events if e.get("verdict") == "cleared"), None)
    failed = sorted({e.get("strategy") for e in events
                     if e.get("verdict") in ("no_change", "regression")
                     and e.get("strategy")})
    return {
        "fix_session_id": first.get("fix_session_id"),
        "project_path": first.get("project_path"),
        "design_name": first.get("design_name"),
        "design_family": first.get("design_family"),
        "platform": first.get("platform"),
        "check_type": first.get("check_type"),
        "violation_class": first.get("violation_class"),
        "path_json": json.dumps(path),
        "n_iters": len(events),
        "outcome": "resolved" if win else "abandoned",
        "winning_strategy": win.get("strategy") if win else None,
        "winning_config_json": win.get("cumulative_config_json") if win else None,
        "failed_strategies_json": json.dumps(failed),
        "initial_count": first.get("before_count"),
        "final_count": events[-1].get("after_count"),
        "total_elapsed_s": sum(e.get("elapsed_s") or 0.0 for e in events) or None,
    }


def _rebuild_fix_trajectories(conn) -> list[dict]:
    """Re-derive Tier-2 from Tier-1 (full rebuild — idempotent)."""
    cur = conn.execute("SELECT * FROM fix_events")
    cols = [c[0] for c in cur.description]
    events = [dict(zip(cols, r)) for r in cur.fetchall()]
    by_session: dict[str, list[dict]] = {}
    for e in events:
        by_session.setdefault(e["fix_session_id"], []).append(e)
    trajectories = [_build_trajectory(evs) for evs in by_session.values()]
    conn.execute("DELETE FROM fix_trajectories")
    for t in trajectories:
        keys = list(t.keys())
        ph = ", ".join(f":{k}" for k in keys)
        conn.execute(f"INSERT INTO fix_trajectories ({', '.join(keys)}) VALUES ({ph})", t)
    conn.commit()
    return events


def _fix_recipes_for_group(events: list[dict]) -> dict:
    """Tier-3 aggregate for one (family, platform): nested by check_type ->
    violation_class -> {strategies: {sid: {attempts,successes,failures,
    median_reduction_pct}}, n_sessions}."""
    out: dict = {}
    # group events by (check, vclass)
    buckets: dict[tuple, list[dict]] = {}
    for e in events:
        buckets.setdefault((e.get("check_type"), e.get("violation_class")), []).append(e)
    for (check, vclass), evs in buckets.items():
        if not check:
            continue
        strategies: dict[str, dict] = {}
        reductions: dict[str, list[float]] = {}
        for e in evs:
            sid = e.get("strategy")
            if not sid or sid == "none":
                continue
            s = strategies.setdefault(sid, {"attempts": 0, "successes": 0, "failures": 0})
            s["attempts"] += 1
            if e.get("verdict") == "cleared":
                s["successes"] += 1
            elif e.get("verdict") in ("no_change", "regression"):
                s["failures"] += 1
            bc, ac = e.get("before_count"), e.get("after_count")
            if bc and ac is not None and bc > 0:
                reductions.setdefault(sid, []).append((bc - ac) / bc)
        for sid, red in reductions.items():
            strategies[sid]["median_reduction_pct"] = statistics.median(red)
        out.setdefault(check, {})[vclass] = {
            "strategies": strategies,
            "n_sessions": len({e.get("fix_session_id") for e in evs}),
        }
    return out
```

Replace the body of `learn()` (lines 135-165) so it keeps the connection open, rebuilds trajectories, and folds `fix_recipes` into each family entry:
```python
def learn(db_path: Path | str,
          out_path: Path | str) -> dict:
    db_path = Path(db_path)
    out_path = Path(out_path)

    conn = knowledge_db.connect(db_path)
    try:
        rows = _fetch_rows(conn)
        fix_events = _rebuild_fix_trajectories(conn)   # Tier-2 (idempotent rebuild)
    finally:
        # keep conn for recipe grouping below, then close
        pass

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        fam = r.get("design_family") or "unknown"
        plat = r.get("platform") or "unknown"
        groups.setdefault((fam, plat), []).append(r)

    fix_groups: dict[tuple[str, str], list[dict]] = {}
    for e in fix_events:
        fam = e.get("design_family") or "unknown"
        plat = e.get("platform") or "unknown"
        fix_groups.setdefault((fam, plat), []).append(e)

    families: dict[str, dict] = {}
    for (fam, plat), group_rows in groups.items():
        entry = _family_platform_entry(group_rows)
        if entry is None:
            continue
        fam_obj = families.setdefault(fam, {"platforms": {}})
        fam_obj["platforms"][plat] = entry

    # Fold Tier-3 fix_recipes into existing entries, and create entries for
    # families that have fix history but no signoff-success run yet.
    for (fam, plat), evs in fix_groups.items():
        recipes = _fix_recipes_for_group(evs)
        if not recipes:
            continue
        fam_obj = families.setdefault(fam, {"platforms": {}})
        entry = fam_obj["platforms"].setdefault(plat, {"sample_size": 0,
                                                        "success_count": 0,
                                                        "success_rate": 0.0})
        entry["fix_recipes"] = recipes

    conn.close()

    data = {
        "generated_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source_run_count": len(rows),
        "min_successful_runs_required": MIN_SUCCESSFUL,
        "families": families,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_learn_fix.py tests/test_learn_heuristics.py -v`
Expected: PASS (new fix tests + all existing learn tests still green).

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/knowledge/learn_heuristics.py r2g-rtl2gds/tests/test_learn_fix.py
git commit -m "feat(knowledge): learn fix_trajectories (Tier-2) + fix_recipes (Tier-3)"
```

---

### Task 5B: Autonomous `manage()` — auto-learn, archive, archival-safe recipes

**Files:**
- Modify: `r2g-rtl2gds/knowledge/fix_log_manager.py` (add stateful routines)
- Modify: `r2g-rtl2gds/knowledge/learn_heuristics.py` (recipes from trajectories; dedup via canonical key)
- Modify: `r2g-rtl2gds/knowledge/ingest_run.py` (auto-invoke `manage()` at end of CLI ingest)
- Test: `r2g-rtl2gds/tests/test_fix_log_manager.py` (extend)

Two correctness moves: (1) recipes derive from **`fix_trajectories`** (never archived), not raw `fix_events`, so archival is **lossless for learning**; (2) `manage()` runs automatically after CLI ingest (env `R2G_FIX_AUTOLEARN`, default on) and archives old raw past the threshold into a sidecar `fix_events_archive.sqlite`, keeping the committed hot DB lean (D13/D14).

- [ ] **Step 1: Write the failing test** (append to `test_fix_log_manager.py`)
```python
def test_manage_relearns_and_recipes_survive_archive(tmp_knowledge_dir, monkeypatch):
    import json, knowledge_db, fix_log_manager as flm
    db = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    cols = ("fix_session_id","design_family","platform","check_type","violation_class",
            "iter","strategy","before_count","after_count","verdict")
    for it,(strat,bc,ac,v) in enumerate(
            [("antenna_density_relief",147,147,"no_change"),
             ("antenna_diode_repair",147,0,"cleared")], start=1):
        conn.execute(f"INSERT INTO fix_events ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                     ("s1","ethernet","nangate45","drc","M2_ANTENNA",it,strat,bc,ac,v))
    conn.commit(); conn.close()

    out = tmp_knowledge_dir / "heuristics.json"
    monkeypatch.setattr(flm, "FIX_EVENTS_MAX_ROWS", 1)   # force archival
    rep = flm.manage(db, out_path=out)

    data = json.loads(out.read_text())
    strat = (data["families"]["ethernet"]["platforms"]["nangate45"]
             ["fix_recipes"]["drc"]["M2_ANTENNA"]["strategies"])
    assert strat["antenna_diode_repair"]["successes"] == 1   # survived archival
    assert rep["archived"] >= 1
    conn = knowledge_db.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0] == 0
    conn.close()
    assert (tmp_knowledge_dir / "fix_events_archive.sqlite").exists()
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3a: Recipes from trajectories + dedup (`learn_heuristics.py`).**
At the top of `_build_trajectory` add `import fix_log_manager` then `events = fix_log_manager.dedup_events_by_action(events)`. Add `_recipes_from_trajectories`:
```python
def _recipes_from_trajectories(trajectories: list[dict]) -> dict[tuple, dict]:
    """Per (family, platform): check -> violation_class -> {strategies, n_sessions}.
    Derived from trajectory path_json so archived raw never changes the counts."""
    acc: dict[tuple, dict] = {}
    for t in trajectories:
        fam = t.get("design_family") or "unknown"
        plat = t.get("platform") or "unknown"
        check, vclass = t.get("check_type"), t.get("violation_class")
        if not check:
            continue
        node = (acc.setdefault((fam, plat), {}).setdefault(check, {})
                .setdefault(vclass, {"strategies": {}, "_sessions": set()}))
        node["_sessions"].add(t.get("fix_session_id"))
        for step in json.loads(t.get("path_json") or "[]"):
            sid = step.get("strategy")
            if not sid or sid == "none":
                continue
            s = node["strategies"].setdefault(sid, {"attempts": 0, "successes": 0,
                                                    "failures": 0, "_red": []})
            s["attempts"] += 1
            if step.get("verdict") == "cleared":
                s["successes"] += 1
            elif step.get("verdict") in ("no_change", "regression"):
                s["failures"] += 1
            bc, ac = step.get("before"), step.get("after")
            if bc and ac is not None and bc > 0:
                s["_red"].append((bc - ac) / bc)
    final: dict[tuple, dict] = {}
    for key, checks in acc.items():
        final[key] = {}
        for check, vmap in checks.items():
            final[key][check] = {}
            for vclass, node in vmap.items():
                strategies = {}
                for sid, s in node["strategies"].items():
                    red = s.pop("_red")
                    if red:
                        s["median_reduction_pct"] = statistics.median(red)
                    strategies[sid] = s
                final[key][check][vclass] = {"strategies": strategies,
                                             "n_sessions": len(node["_sessions"])}
    return final
```
In `learn()`, **replace** the `for (fam, plat), evs in fix_groups.items(): recipes = _fix_recipes_for_group(evs)` block (and the `fix_groups` construction) with a trajectory re-read:
```python
    conn2 = knowledge_db.connect(db_path)
    cur = conn2.execute("SELECT * FROM fix_trajectories")
    tcols = [c[0] for c in cur.description]
    trajectories = [dict(zip(tcols, r)) for r in cur.fetchall()]
    conn2.close()
    for (fam, plat), recipes in _recipes_from_trajectories(trajectories).items():
        if not recipes:
            continue
        entry = (families.setdefault(fam, {"platforms": {}})["platforms"]
                 .setdefault(plat, {"sample_size": 0, "success_count": 0, "success_rate": 0.0}))
        entry["fix_recipes"] = recipes
```
Delete the now-unused `_fix_recipes_for_group` (recipes come from trajectories now). The Task 5 test still passes (seeded `fix_events` → trajectories → identical recipe counts).

- [ ] **Step 3b: Stateful routines (`fix_log_manager.py`).**
```python
def db_size_mb(db_path) -> float:
    import os
    return os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0.0


def _archive_db_path(db_path):
    from pathlib import Path
    return Path(db_path).with_name("fix_events_archive.sqlite")


def archive_old_raw(db_path, *, max_rows=None, max_mb=None) -> int:
    """Move raw fix_events of fully-merged episodes (those with a fix_trajectory),
    oldest first, into the sidecar archive DB when the hot DB exceeds a threshold.
    Trajectories are never moved, so recipes are unaffected. Returns rows archived."""
    import knowledge_db
    max_rows = FIX_EVENTS_MAX_ROWS if max_rows is None else max_rows
    max_mb = DB_MAX_MB if max_mb is None else max_mb
    conn = knowledge_db.connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0]
        if n <= max_rows and db_size_mb(db_path) <= max_mb:
            return 0
        arch = _archive_db_path(db_path)
        ac = knowledge_db.connect(arch); knowledge_db.ensure_schema(ac); ac.close()
        conn.execute("ATTACH DATABASE ? AS arch", (str(arch),))
        sessions = [r[0] for r in conn.execute(
            "SELECT e.fix_session_id FROM fix_events e "
            "JOIN fix_trajectories t USING(fix_session_id) "
            "GROUP BY e.fix_session_id ORDER BY MIN(e.ts)")]
        archived = 0
        for sid in sessions:
            if (conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0] <= max_rows
                    and db_size_mb(db_path) <= max_mb):
                break
            conn.execute("INSERT INTO arch.fix_events_archive "
                         "SELECT * FROM fix_events WHERE fix_session_id = ?", (sid,))
            archived += conn.execute(
                "DELETE FROM fix_events WHERE fix_session_id = ?", (sid,)).rowcount
        conn.commit()
        conn.execute("DETACH DATABASE arch")
    finally:
        conn.close()
    # VACUUM needs its own autocommit connection (cannot run inside a txn).
    vac = knowledge_db.connect(db_path); vac.isolation_level = None
    vac.execute("VACUUM"); vac.close()
    return archived


def manage(db_path, *, out_path=None, autolearn=True) -> dict:
    """Autonomous post-ingest step: re-derive Tier-2/Tier-3 (learn) then enforce the
    size policy. Returns {rows, archived, db_mb}."""
    import knowledge_db
    if out_path is None:
        out_path = knowledge_db.DEFAULT_KNOWLEDGE_DIR / "heuristics.json"
    if autolearn:
        import learn_heuristics
        learn_heuristics.learn(db_path, out_path)   # builds trajectories+recipes FIRST
    archived = archive_old_raw(db_path)             # safe: trajectories already built
    conn = knowledge_db.connect(db_path)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM fix_events").fetchone()[0]
    finally:
        conn.close()
    return {"rows": rows, "archived": archived, "db_mb": round(db_size_mb(db_path), 2)}
```
Note the `INSERT INTO arch.fix_events_archive SELECT *` relies on `fix_events_archive` columns matching `fix_events`'s order minus the autoincrement semantics — they do (Task 1 DDL lists the same columns in the same order, `fix_event_id` first as a plain INTEGER in the archive).

- [ ] **Step 3c: Auto-invoke from `ingest_run.py main()`** after a successful `ingest(...)` (env-gated; never breaks a flow ingest):
```python
    import os
    if os.environ.get("R2G_FIX_AUTOLEARN", "1") == "1":
        try:
            import fix_log_manager
            fix_log_manager.manage(args.db)
        except Exception as exc:
            print(f"WARNING: fix_log_manager.manage skipped: {exc}", file=sys.stderr)
```

- [ ] **Step 4: Run** `python -m pytest tests/test_fix_log_manager.py tests/test_learn_fix.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add r2g-rtl2gds/knowledge/fix_log_manager.py r2g-rtl2gds/knowledge/learn_heuristics.py r2g-rtl2gds/knowledge/ingest_run.py r2g-rtl2gds/tests/test_fix_log_manager.py
git commit -m "feat(knowledge): autonomous manage() — auto-learn, archive-on-threshold, recipes from trajectories"
```

---

### Task 6: Rank strategies in `diagnose_signoff_fix.py` + `--list`

**Files:**
- Modify: `r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py`
- Test: `r2g-rtl2gds/tests/test_diagnose_ranking.py`

`build_plan` gains an optional `recipes` arg (the Tier-3 entry for this check/violation_class). When present, the strategy list is reordered by `fix_model.rank_strategies`; safety/`exclude`/`_applied` filtering is unchanged and applied AFTER ranking. A `--list` CLI prints the full ranked candidate set with evidence. The hard safety clamps live in the catalog/`apply_edits` (e.g. `PLACE_DENSITY_LB_ADDON` is never an edit) — ranking only reorders existing real-fix strategies, so clamps stay absolute.

- [ ] **Step 1: Write the failing test**

Create `r2g-rtl2gds/tests/test_diagnose_ranking.py`:
```python
"""diagnose_signoff_fix ranks strategies by learned recipes; safety unchanged."""
from __future__ import annotations
import diagnose_signoff_fix as dsf


def test_ranking_reorders_non_nangate_antenna_strategies():
    # sky130hd antenna -> catalog order is [diode_iters, density_relief].
    cfg = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}
    drc = {"status": "fail", "total_violations": 10,
           "categories": {"M1_ANTENNA": {"count": 10}}}
    # Learned: density_relief is the proven winner here.
    recipes = {"strategies": {
        "antenna_density_relief": {"attempts": 8, "successes": 7, "failures": 1},
        "antenna_diode_iters":    {"attempts": 8, "successes": 1, "failures": 7},
    }, "n_sessions": 8}
    plan = dsf.build_plan(drc, {}, cfg, check="drc", recipes=recipes)
    ids = [s["id"] for s in plan["strategies"]]
    assert ids[0] == "antenna_density_relief"     # learned winner promoted
    assert "ranking" in plan and plan["ranking"][0]["strategy"] == "antenna_density_relief"


def test_cold_start_preserves_catalog_order():
    cfg = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}
    drc = {"status": "fail", "total_violations": 10,
           "categories": {"M1_ANTENNA": {"count": 10}}}
    plan = dsf.build_plan(drc, {}, cfg, check="drc", recipes=None)
    ids = [s["id"] for s in plan["strategies"]]
    assert ids == ["antenna_diode_iters", "antenna_density_relief"]


def test_safety_density_addon_never_an_edit():
    # No strategy may ever edit PLACE_DENSITY_LB_ADDON (hard rule).
    cfg = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}
    drc = {"status": "fail", "categories": {"M1_ANTENNA": {"count": 10}}}
    plan = dsf.build_plan(drc, {}, cfg, check="drc", recipes=None)
    for s in plan["strategies"]:
        assert "PLACE_DENSITY_LB_ADDON" not in s["config_edits"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_diagnose_ranking.py -v`
Expected: FAIL — `build_plan() got an unexpected keyword argument 'recipes'`.

- [ ] **Step 3: Edit `diagnose_signoff_fix.py`**

Add an import + sys.path bootstrap near the top (after line 17), so the pure model is importable whether run as a script or imported in tests (conftest already puts `scripts/reports` on the path):
```python
try:
    import fix_model
except ImportError:                       # script run outside the test sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fix_model
```

Add a ranking helper and thread `recipes` through `build_plan`:
```python
def _rank_plan_strategies(plan: dict, recipes: dict | None) -> dict:
    """Reorder plan['strategies'] by fix_model and attach the full ranking."""
    if not plan.get("strategies"):
        return plan
    static_order = [s["id"] for s in plan["strategies"]]
    ranking = fix_model.rank_strategies(recipes, static_order)
    by_id = {s["id"]: s for s in plan["strategies"]}
    plan["strategies"] = [by_id[r["strategy"]] for r in ranking if r["strategy"] in by_id]
    plan["ranking"] = ranking
    return plan
```

Change `build_plan` (line 219) to accept and apply recipes:
```python
def build_plan(drc: dict, lvs: dict, cfg: dict, *, check: str = "drc",
               exclude=(), recipes: dict | None = None) -> dict:
    """Pure: (drc.json, lvs.json, parsed config.mk) -> ordered fix plan dict.
    When `recipes` (a Tier-3 fix_recipes entry for this check/violation_class)
    is given, strategies are re-ranked by empirical clearance (fix_model)."""
    excl = set(exclude or ())
    plan = _drc_plan(drc or {}, cfg, excl) if check == "drc" else _lvs_plan(lvs or {}, cfg, excl)
    return _rank_plan_strategies(plan, recipes)
```

Add a recipes loader + `--list` to `main()`. After `cfg = parse_config(cfg_text)` (line 262) add:
```python
    recipes = _load_recipes(proj, check=args.check, drc=drc, lvs=lvs)
```
and pass `recipes=recipes` into the `build_plan(...)` call (line 264). Add the loader near `_load` (line 244):
```python
def _load_recipes(proj: Path, *, check: str, drc: dict, lvs: dict,
                  heuristics: Path | None = None) -> dict | None:
    """Look up the Tier-3 fix_recipes entry for this design's family/platform and
    the current violation_class. Returns None (cold start) if absent."""
    hp = heuristics or (Path(__file__).resolve().parents[1] / "knowledge" / "heuristics.json")
    if not hp.exists():
        return None
    cfg = parse_config((proj / "constraints" / "config.mk").read_text(encoding="utf-8")
                       if (proj / "constraints" / "config.mk").exists() else "")
    try:
        import knowledge_db
        families = knowledge_db.load_families()
        fam = knowledge_db.infer_family(cfg.get("DESIGN_NAME", ""), families)
    except Exception:
        fam = (cfg.get("DESIGN_NAME", "") or "").split("_", 1)[0].lower()
    plat = cfg.get("PLATFORM", "nangate45")
    data = json.loads(hp.read_text(encoding="utf-8"))
    entry = (data.get("families", {}).get(fam, {})
             .get("platforms", {}).get(plat, {}).get("fix_recipes"))
    if not entry:
        return None
    if check == "drc":
        cats = drc.get("categories") or {}
        vclass = max(cats, key=lambda k: cats[k].get("count") or 0) if cats else None
    else:
        vclass = lvs.get("mismatch_class")
    return entry.get(check, {}).get(vclass)
```
Add the `--list` arg (after the `--next` arg, line 253):
```python
    ap.add_argument("--list", action="store_true",
                    help="print the full priority-ranked candidate list as JSON")
```
and handle it (before `if args.next:`, line 285):
```python
    if args.list:
        print(json.dumps(plan.get("ranking", []), indent=2))
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_diagnose_ranking.py -v`
Expected: PASS (3 tests). Also run the existing diagnose tests if present: `python -m pytest tests/ -k diagnose -v`.

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py r2g-rtl2gds/tests/test_diagnose_ranking.py
git commit -m "feat(reports): rank signoff-fix strategies by learned recipes + --list"
```

---

### Task 7: `check_timing.py --journal` (timing fixes become fix_events)

**Files:**
- Read first, then Modify: `r2g-rtl2gds/scripts/reports/check_timing.py`
- Test: `r2g-rtl2gds/tests/test_check_timing_journal.py`

- [ ] **Step 1: Read the file** to learn the exact `main()`/argparse structure and the `reports/timing_check.json` shape.

Run: `sed -n '1,60p;380,460p' r2g-rtl2gds/scripts/reports/check_timing.py` (orient on `main()` + the tier/option fields).

- [ ] **Step 2: Write the failing test**

Create `r2g-rtl2gds/tests/test_check_timing_journal.py`:
```python
"""check_timing.py --journal appends a timing fix_event line to fix_log.jsonl."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
CHECK_TIMING = SKILL / "scripts" / "reports" / "check_timing.py"


def test_journal_appends_timing_fix_event(tmp_path):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    before = proj / "reports" / "before.json"
    after = proj / "reports" / "after.json"
    before.write_text(json.dumps({"tier": "moderate", "wns_ns": -3.0,
                                  "clock_period_ns": 10.0}))
    after.write_text(json.dumps({"tier": "clean", "wns_ns": 0.1,
                                 "clock_period_ns": 13.0}))
    subprocess.run(["python3", str(CHECK_TIMING), "--journal",
                    "--project", str(proj), "--before", str(before),
                    "--after", str(after), "--strategy", "period_relax"], check=True)
    rows = [json.loads(l) for l in (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    assert len(rows) == 1
    r = rows[0]
    assert r["check"] == "timing" and r["strategy"] == "period_relax"
    assert r["violation_class"] == "moderate"     # the before tier
    assert r["verdict"] == "cleared"              # after tier == clean
    assert r["fix_session_id"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_check_timing_journal.py -v`
Expected: FAIL — `--journal` not recognized.

- [ ] **Step 4: Add the `--journal` subcommand to `check_timing.py`**

Add this function and wire it into `main()` as an early branch (before the normal tier-check logic). Use `|WNS|` as the before/after count so timing is comparable to DRC/LVS counts:
```python
def _journal(project, before_path, after_path, strategy):
    """Append a check=timing fix_event to <project>/reports/fix_log.jsonl."""
    import hashlib, time
    b = json.loads(Path(before_path).read_text(encoding="utf-8"))
    a = json.loads(Path(after_path).read_text(encoding="utf-8"))
    before_tier = b.get("tier")
    after_tier = a.get("tier")
    sid = hashlib.sha1((str(project) + "timing" + str(time.time())).encode()).hexdigest()[:16]
    verdict = "cleared" if after_tier in ("clean", "minor") else (
        "win" if abs(a.get("wns_ns", 0)) < abs(b.get("wns_ns", 0)) else "no_change")
    row = {
        "check": "timing", "iter": 1, "strategy": strategy,
        "before": abs(b.get("wns_ns")) if b.get("wns_ns") is not None else None,
        "after": abs(a.get("wns_ns")) if a.get("wns_ns") is not None else None,
        "verdict": verdict, "from_stage": None, "fix_session_id": sid,
        "violation_class": before_tier, "after_status": after_tier,
        "before_categories": json.dumps({"tier": before_tier}),
        "cumulative_config": json.dumps({"clock_period_ns": a.get("clock_period_ns")}),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    log = Path(project) / "reports" / "fix_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(json.dumps(row) + "\n")
```
In `main()` argparse, add `--journal`, `--project`, `--before`, `--after`, `--strategy`, and at the top of `main()` (after parsing) add:
```python
    if getattr(args, "journal", False):
        _journal(args.project, args.before, args.after, args.strategy)
        return 0
```

- [ ] **Step 5: Run test + commit**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_check_timing_journal.py -v` → PASS
```bash
git add r2g-rtl2gds/scripts/reports/check_timing.py r2g-rtl2gds/tests/test_check_timing_journal.py
git commit -m "feat(reports): check_timing --journal records timing fixes as fix_events"
```

---

### Task 8: Rank backend-stage proposals in `analyze_execution.py`

**Files:**
- Read first, then Modify: `r2g-rtl2gds/knowledge/analyze_execution.py`
- Test: `r2g-rtl2gds/tests/test_analyze_ranking.py`

- [ ] **Step 1: Read `analyze_execution.py`** — find `analyze()` (~line 230) and the shape of the proposal list it returns (rule-based + BM25). Identify where proposals are ordered.

Run: `sed -n '200,320p' r2g-rtl2gds/knowledge/analyze_execution.py`

- [ ] **Step 2: Write the failing test** (`tests/test_analyze_ranking.py`): seed `fix_events` for a `check_type='orfs'`/stage violation_class, then assert `analyze()` orders its proposals so the historically-clearing strategy ranks first. (Mirror the recipe lookup + `fix_model.rank_strategies` call used in Task 6; reuse `_load_recipes`-style lookup against `heuristics.json` keyed by family/platform/stage.)

```python
def test_backend_proposals_reranked_by_history(tmp_knowledge_dir, monkeypatch):
    # build a heuristics.json with a winning recipe for ('orfs','place')
    import json, analyze_execution
    h = tmp_knowledge_dir / "heuristics.json"
    h.write_text(json.dumps({"families": {"aes": {"platforms": {"nangate45": {
        "fix_recipes": {"orfs": {"place": {"strategies": {
            "place_density_relief": {"attempts": 6, "successes": 5, "failures": 1}},
            "n_sessions": 6}}}}}}}}))
    ranked = analyze_execution.rank_proposals(
        ["util_reduce", "place_density_relief"],
        family="aes", platform="nangate45", stage="place", heuristics_path=h)
    assert ranked[0] == "place_density_relief"
```

- [ ] **Step 3: Add a small `rank_proposals(proposal_ids, *, family, platform, stage, heuristics_path)` helper** to `analyze_execution.py` that loads `fix_recipes['orfs'][stage]` and calls `fix_model.rank_strategies`, then call it where `analyze()` finalizes its ordered list. (`check_type='orfs'` is the bucket backend-stage fix_events use; the fixers that record them set `check='orfs'`, `violation_class=<stage>`.)

- [ ] **Step 4: Run test → PASS. Step 5: Commit**
```bash
git add r2g-rtl2gds/knowledge/analyze_execution.py r2g-rtl2gds/tests/test_analyze_ranking.py
git commit -m "feat(knowledge): rank backend-stage fix proposals by learned recipes"
```

---

### Task 9: Fix-effectiveness projection in `build_lineage_view.py`

**Files:**
- Read first, then Modify: `r2g-rtl2gds/scripts/reports/build_lineage_view.py`
- Test: `r2g-rtl2gds/tests/test_lineage_fix_view.py`

- [ ] **Step 1: Read `build_lineage_view.py`** — it opens the DB read-only (`mode=ro`) and returns `{"health": ..., "provenance": ...}`. Find the top-level builder.

Run: `sed -n '1,80p' r2g-rtl2gds/scripts/reports/build_lineage_view.py`

- [ ] **Step 2: Write the failing test** (`tests/test_lineage_fix_view.py`): seed `fix_trajectories`, call the view builder, assert a new `fix_effectiveness` key lists, per `(family, platform, check, violation_class)`, each strategy's resolved/abandoned counts + clearance rate. Read-only (no writes to the DB).

- [ ] **Step 3: Add a `_fix_effectiveness(conn)` projection** that `SELECT`s from `fix_trajectories` grouped by family/platform/check/violation_class/winning_strategy, and include it in the returned dict under `"fix_effectiveness"`. Keep `mode=ro`.

- [ ] **Step 4: Run test → PASS. Step 5: Commit**
```bash
git add r2g-rtl2gds/scripts/reports/build_lineage_view.py r2g-rtl2gds/tests/test_lineage_fix_view.py
git commit -m "feat(reports): fix-effectiveness projection in lineage view"
```

---

### Task 10: Empirical-vs-static arm in `eval_heuristics.py`

**Files:**
- Read first, then Modify: `r2g-rtl2gds/knowledge/eval_heuristics.py`
- Test: `r2g-rtl2gds/tests/test_eval_fix_arm.py`

- [ ] **Step 1: Read `eval_heuristics.py`** — find `emit`/`summarize` and the `eval_arm` mechanism (`naive`|`learned`).

- [ ] **Step 2: Write the failing test** (`tests/test_eval_fix_arm.py`): given two fixing runs (one with ranked strategy ordering, one with static catalog order) recorded as `fix_trajectories`, `summarize_fix_arms()` classifies the pair on payoff = (violations cleared, total iterations to clear, wall-clock). Assert the ranked arm with fewer iterations to `resolved` is scored a `win`.

- [ ] **Step 3: Add `summarize_fix_arms(db_path)`** that compares trajectories tagged `eval_arm IN ('ranked','static')` (the fix loop writes the tag into `cumulative_config`/a new `eval_arm` field on the trajectory) on iters-to-resolve + elapsed, emitting `fix_eval_summary.json`.

- [ ] **Step 4: Run test → PASS. Step 5: Commit**
```bash
git add r2g-rtl2gds/knowledge/eval_heuristics.py r2g-rtl2gds/tests/test_eval_fix_arm.py
git commit -m "feat(knowledge): A/B arm for empirical-vs-static fix ordering"
```

---

### Task 11: Evidence-backed fix candidates in `mine_rules.py`

**Files:**
- Read first, then Modify: `r2g-rtl2gds/knowledge/mine_rules.py`
- Test: `r2g-rtl2gds/tests/test_mine_fix_candidates.py`

- [ ] **Step 1: Read `mine_rules.py`** — it groups `failure_events` and writes `failure_candidates.json` (human review queue). Find the writer.

- [ ] **Step 2: Write the failing test** (`tests/test_mine_fix_candidates.py`): seed `fix_trajectories` with ≥3 resolved episodes sharing `(family, check, violation_class, winning_strategy)`; assert `mine_rules` adds a `fix_candidates` array to `failure_candidates.json`, each entry carrying `{family, platform, check, violation_class, winning_strategy, resolved, abandoned, clearance_rate, example_session}` for human promotion into `failure-patterns.md`.

- [ ] **Step 3: Add a `_mine_fix_candidates(conn)`** that reads `fix_trajectories`, rolls up per `(family, platform, check, violation_class, winning_strategy)`, keeps those with `resolved >= 3`, and writes them under a `fix_candidates` key. Do not auto-write `failure-patterns.md` (human-curated, per spec D1/§12).

- [ ] **Step 4: Run test → PASS. Step 5: Commit**
```bash
git add r2g-rtl2gds/knowledge/mine_rules.py r2g-rtl2gds/tests/test_mine_fix_candidates.py
git commit -m "feat(knowledge): mine evidence-backed fix candidates for human promotion"
```

---

### Task 12: `backfill_fix_events.py` — mine `_batch/*.jsonl`

**Files:**
- Create: `r2g-rtl2gds/knowledge/backfill_fix_events.py`
- Test: `r2g-rtl2gds/tests/test_backfill_fix_events.py`

The historical failure→success gold lives in `design_cases/_batch/{recover_pass*,retry_pass*,antenna_fix_*,beol_drc_*}.jsonl`. Each shape maps to a synthetic `fix_event` (provenance `backfill:<file>`). Inspect a few real files first to confirm field names.

- [ ] **Step 1: Inspect the real batch files** to confirm record shapes.

Run:
```bash
for f in antenna_fix retry_pass4 recover_pass4 beol_drc; do
  echo "== $f =="; ls design_cases/_batch/${f}*.jsonl 2>/dev/null | head -1 | xargs -r head -n 2
done
```
Note the actual keys (e.g. `design`, `before`, `after`, `from_stage`, `strategy`/`action`, `status`).

- [ ] **Step 2: Write the failing test** (`tests/test_backfill_fix_events.py`): write a small fixture batch jsonl in `tmp_path` matching the shapes observed in Step 1, run `backfill_fix_events.backfill(tmp_batch_dir, conn, families)`, and assert one `fix_event` per record with `provenance` starting `backfill:`, correct `check_type`/`violation_class`/`verdict` (`cleared` when `after==0`), and `fix_session_id` stable per design+file.
```python
def test_backfill_antenna_fix(tmp_path, tmp_knowledge_dir):
    import json, knowledge_db, backfill_fix_events
    batch = tmp_path / "_batch"; batch.mkdir()
    (batch / "antenna_fix_2026.jsonl").write_text(
        json.dumps({"design": "verilog_ethernet_eth_demux", "platform": "nangate45",
                    "before": 147, "after": 3, "strategy": "antenna_diode_repair",
                    "from_stage": "route", "violation_class": "M3_ANTENNA"}) + "\n")
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    fams = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    n = backfill_fix_events.backfill(batch, conn, fams)
    assert n == 1
    row = conn.execute("SELECT check_type, verdict, provenance, design_family "
                       "FROM fix_events").fetchone()
    assert row[0] == "drc" and row[1] == "win"   # after=3 (>0) -> win, not cleared
    assert row[2].startswith("backfill:antenna_fix")
    conn.close()
```

- [ ] **Step 3: Write `backfill_fix_events.py`** with a per-file-type parser (a small dispatch keyed on the filename stem), each yielding normalized event dicts, then `INSERT OR IGNORE` into `fix_events`. Map: `antenna_fix_*` → check=drc; `beol_drc_*` → check=drc; `retry_pass*`/`recover_pass*`/`orfs_retry` → check=orfs, violation_class=`from_stage`. `verdict='cleared'` iff `after==0` else `'win'` if `after<before` else `'no_change'`. `fix_session_id = sha1(design+file)[:16]`. Provide a `main()` with `--batch-dir`/`--db`.

- [ ] **Step 4: Run test → PASS. Step 5: Commit**
```bash
git add r2g-rtl2gds/knowledge/backfill_fix_events.py r2g-rtl2gds/tests/test_backfill_fix_events.py
git commit -m "feat(knowledge): backfill historical fix transitions from _batch/*.jsonl"
```

---

### Task 13: `repair_run_status.py` — reconcile dead `partial` rows

**Files:**
- Create: `r2g-rtl2gds/knowledge/repair_run_status.py`
- Test: `r2g-rtl2gds/tests/test_repair_run_status.py`

747/750 `runs` rows have `orfs_status='partial'` because their `stage_log.jsonl` was incomplete. `is_success` already admits signoff-positive partials, so the runs *are* learnable — but the **per-run report files** are the ground truth for the real status. This one-time pass re-derives `orfs_status`/signoff from each project's `reports/*.json` and updates the row (so the corpus the learner sees matches reality). Backs up `runs.sqlite` first; read-from-reports only; reversible.

- [ ] **Step 1: Write the failing test** (`tests/test_repair_run_status.py`): insert a `runs` row with `orfs_status='partial'`, `project_path` → a fixture project whose `reports/{drc,lvs,rcx}.json` are all clean and whose `stage_log.jsonl` shows all 6 stages pass; run `repair_run_status.repair(cases_root, conn)`; assert the row's `orfs_status` flips to `'pass'` and a `runs.sqlite.bak` was created.

- [ ] **Step 2: Run → FAIL (no module).**

- [ ] **Step 3: Write `repair_run_status.py`:** `repair(cases_root, conn)` iterates `runs` rows, finds the project dir by `project_path`, re-reads `backend/RUN_*/stage_log.jsonl` via `ingest_run._read_stage_log` + `_derive_orfs_status`, and `UPDATE`s `orfs_status`/`orfs_fail_stage` when the re-derived value differs. A `--db` `main()` copies the DB to `<db>.bak` before writing (`shutil.copy2`), prints a before/after status histogram, and is idempotent (re-running changes nothing).

- [ ] **Step 4: Run test → PASS. Step 5: Commit**
```bash
git add r2g-rtl2gds/knowledge/repair_run_status.py r2g-rtl2gds/tests/test_repair_run_status.py
git commit -m "feat(knowledge): repair dead orfs_status='partial' rows from reports"
```

---

### Task 14: Documentation

**Files:**
- Modify: `r2g-rtl2gds/SKILL.md`, `r2g-rtl2gds/references/signoff-fixing.md`, `r2g-rtl2gds/references/orfs-playbook.md`, `r2g-rtl2gds/knowledge/README.md`

- [ ] **Step 1:** `SKILL.md` — under the signoff section, add a "Fix-Learning Loop" sub-step: `fix_signoff.sh`/`check_timing --journal` record fix_events; step-10 ingest now also reads `fix_log.jsonl` + writes `run_violations`; `diagnose_signoff_fix.py --list` shows the evidence-ranked candidate set; `learn_heuristics.py` derives `fix_trajectories`/`fix_recipes`.
- [ ] **Step 2:** `references/signoff-fixing.md` — document the three tiers, the ranked-candidate fall-through, and that `failure-patterns.md` stays human-curated (fed by `mine_rules` `fix_candidates`).
- [ ] **Step 3:** `knowledge/README.md` — add the `fix_events`/`fix_trajectories`/`run_violations` schema + the `fix_recipes` heuristics.json sub-key + `backfill_fix_events.py`/`repair_run_status.py` usage.
- [ ] **Step 4:** `references/orfs-playbook.md` — one-paragraph pointer to the loop.
- [ ] **Step 5: Commit**
```bash
git add r2g-rtl2gds/SKILL.md r2g-rtl2gds/references/signoff-fixing.md r2g-rtl2gds/references/orfs-playbook.md r2g-rtl2gds/knowledge/README.md
git commit -m "docs(skill): document the fix-learning loop (three tiers + ranked candidates)"
```

---

### Task 15: Full-suite green gate + byte-stable regression

- [ ] **Step 1: Run the whole suite**

Run: `cd r2g-rtl2gds && python -m pytest tests/ -q`
Expected: all green (357 prior + new tests). Fix any regressions before proceeding.

- [ ] **Step 2: Byte-stable extractor regression** on a nangate45 design to confirm unchanged paths didn't drift (use the existing golden gate if present, else compare `extract_ppa.py` output on `design_cases/aes_core` before/after).

- [ ] **Step 3: Commit any fixups**, then this completes Part A (the mechanism).

---

## Part B — Backfill, pilot, and the corpus campaign (operational runbook — gated)

> These tasks execute the flow; they are not TDD. Each is a runbook with concrete commands. **Phase D (Task 18) is a hard STOP for user go/no-go before the large compute spend.**

### Task 16: Backfill + repair + first learn (Phase B)

- [ ] **Step 1: Back up the DB** — `cp r2g-rtl2gds/knowledge/runs.sqlite r2g-rtl2gds/knowledge/runs.sqlite.pre-fixlearn.bak`
- [ ] **Step 2: Repair dead rows** — `python3 r2g-rtl2gds/knowledge/repair_run_status.py --db r2g-rtl2gds/knowledge/runs.sqlite` ; record the before/after `orfs_status` histogram it prints.
- [ ] **Step 3: Backfill history** — `python3 r2g-rtl2gds/knowledge/backfill_fix_events.py --batch-dir design_cases/_batch --db r2g-rtl2gds/knowledge/runs.sqlite` ; record the count of `fix_events` inserted.
- [ ] **Step 4: Re-learn** — `python3 r2g-rtl2gds/knowledge/learn_heuristics.py` ; confirm `heuristics.json` now contains `fix_recipes` for the ethernet/iccad/wb2axip families and `fix_trajectories` is populated (`sqlite3 ... "SELECT outcome, COUNT(*) FROM fix_trajectories GROUP BY outcome"`).
- [ ] **Step 5: Sanity-check the dashboard** fix-effectiveness panel renders (`build_lineage_view.py`), then **commit the regenerated store** as the shippable pre-trained baseline (D14): `git add r2g-rtl2gds/knowledge/runs.sqlite r2g-rtl2gds/knowledge/heuristics.json r2g-rtl2gds/knowledge/fix_events_archive.sqlite && git commit -m "chore(knowledge): backfilled+repaired store"`.

### Task 17: Pilot one-of-each (Phase C)

Prove capture→learn→improved-suggestion end-to-end on one design per case type. For each: run the fix, confirm `fix_events` recorded the iterations, re-learn, confirm `diagnose --list` reflects the new evidence.

- [ ] **Step 1: Synth `#include` re-run** — pick `darkriscv_core` (or another `incomplete_missing_headers` design); fix the include path/file collection; `run_synth.sh` → `run_orfs.sh`; ingest.
- [ ] **Step 2: Timing fix** — `iccad2015_unit16_in1` (WNS −4.51). Use `check_timing.py` to pick the period; re-run; then `check_timing.py --journal --project ... --before ... --after ... --strategy period_relax`; ingest.
- [ ] **Step 3: DRC fix** — `verilog_ethernet_eth_demux` (real fail, 3 viol). `fix_signoff.sh <proj> nangate45 --check drc`; ingest.
- [ ] **Step 4: LVS triage** — `wb2axip_axi2axilite` (`real_connectivity`). `fix_signoff.sh ... --check lvs` (expect honest residual); ingest — this records a valuable *abandoned* episode.
- [ ] **Step 5: Re-learn + verify the loop** — `learn_heuristics.py`; then `diagnose_signoff_fix.py <eth_demux> --check drc --list` shows the ranking shifted by the pilot evidence. Capture the before/after ranking in the checkpoint notes.

### Task 18: CHECKPOINT (Phase D) — STOP for user go/no-go

- [ ] **Step 1:** Present to the user: repair histogram, backfilled `fix_events` count, pilot results (the 4 designs + the before/after `--list` ranking), and the proposed Phase-F wave plan (size buckets, LVS serialization for >100K-cell, BOOM time-box). **Do not start Phase E/F until the user approves the compute spend.**

### Task 19: Fixing campaign (Phase E)

- [ ] **Step 1:** 28 RTL-`#include`/synth re-runs (cheap) — batch via the existing batch tooling; ingest each.
- [ ] **Step 2:** ~5 PD-recovery candidates (arm_core is confirmed intractable — time-box it) ; ingest.
- [ ] **Step 3:** Remaining violations: 4 timing (iccad2015 units), eth_demux DRC + 7 DRC-stuck, wb2axip ×2 LVS. Use `fix_signoff.sh` / `check_timing --journal`. Honor hard rules (FLOW_VARIANT isolation; no parallel LVS on >100K-cell).
- [ ] **Step 4:** Ingest + re-learn after each wave; watch `fix_recipes` warm up.

### Task 20: Full-corpus enrichment (Phase F)

- [ ] **Step 1:** Build the wave list: all RTL designs, size-bucketed; exclude none except time-boxing the 9 BOOMs.
- [ ] **Step 2:** Run waves respecting concurrency hard rules (size-adaptive parallelism; serialize/≤2 LVS for >100K-cell). Each design: `run_orfs.sh` → signoff → ingest (writes `run_violations` for every run + `fix_events` where violations were fixed).
- [ ] **Step 3:** After each wave: ingest → `learn_heuristics.py` → `build_lineage_view.py`. Log dropped/timed-out designs explicitly (no silent caps).
- [ ] **Step 4:** Final: **commit the enriched store** (`runs.sqlite` + `heuristics.json` + `fix_events_archive.sqlite`) — the skill is now pre-trained and ships with its experience (D14). Summarize per-family/per-platform coverage and the warmed `fix_recipes`.

---

## Self-Review

**Spec coverage:** D1 Approach-1 → Tasks 2,5,6 (fix_model template). D2 record-failures → Task 4/5 (verdict incl. negatives; recipes count failures). D3 three tiers → Tasks 1,4,5. D4 enumerate-all + fall-through → Tasks 2,6 (`--list`, no hard gate). D5 survivorship → Task 5 (abandoned counted; `test_learn_emits_trajectories_and_recipes`). D6 backfill+repair → Tasks 12,13,16. D7 timing journal → Task 7. D8 staged campaign/worklist → Tasks 17-19. D9 lossless detail + run_violations → Tasks 1,4. D10 all-RTL re-run → Task 20. Observability/eval/mine → Tasks 9,10,11. Docs → Task 14. **D11** autonomous merge/manager → Tasks 2B,5B. **D12** adaptive iteration budget → Task 3. **D13** size mgmt (bound blobs + archive-on-threshold) → Tasks 1,2B,5B. **D14** portability (ship pre-trained) → Task 1B + runbook commits (Tasks 16,20).

**Placeholders:** Tasks 8-11 intentionally use "read first, then implement" because those files were not read verbatim during planning — each still carries a concrete failing test and the exact helper to add. All other tasks have complete code.

**Type consistency:** verdict vocabulary (`cleared|win|no_change|regression|inconclusive`) is defined once (Task 4 `_normalize_verdict`) and used in Tasks 5,7,12. `rank_strategies(recipe_entry, static_order)` signature is identical in Tasks 2,6,8. `fix_recipes[check][violation_class] = {"strategies": {...}, "n_sessions": int}` shape matches across Tasks 5,6,8. `build_plan(..., recipes=None)` matches Task 6 test + caller.

---

## Implementation Log — 2026-06-06 (branch `feat/fix-learning-loop`, off `main` e2164bb)

**Status:** Part A (the mechanism, Tasks 0–15) is fully implemented and TDD-green — full suite **373 passed / 8 skipped** (baseline was 331/8; **+42 new tests**, zero regressions). Part B **Task 16** (backfill + repair + first learn) ran locally. **Tasks 17–20** (pilot + campaign — real EDA flows / large compute) are paused at the **Task 18** checkpoint pending user go/no-go.

**Commits (oldest→newest):** `cffb3b2` spec+plan · `1131b9a` track store (D14) · `d970732` T1 schema · `67e70c4` T2 fix_model · `c89c7c3` T2B helpers · `431a3fd` T3 fix_signoff · `0e51713` T7 check_timing · `352183e` T12 backfill · `d62757a` T13 repair · `fe621d5` T4 ingest · `314b81a` T5 learn · `aa7415f` T5B manage · `4748210` T6 diagnose-rank · `0b4cfe0` T8 analyze-rank · `13dc2f0` T9 lineage fix-view · `40dbf47` T10 eval arm · `a73c066` T11 mine fix-candidates · `6b4e98f` T14 docs · `0bcec8f` Task 16 store.

**Divergences from the as-written plan** (each was required to make the plan's own tests pass, or to match real data):
1. **T2B `_bucket()`:** `math.floor`→`round` (the floor band-edge artifact put 14 and 15 in adjacent bands, breaking the within-tolerance merge test).
2. **T3 `_log_iter()`:** the plan re-read `reports/<check>.json` at log time, but `_run_extract` overwrites it with the post-fix *clean* report first → `before_categories`/`violation_class` would be empty. Added a `_snapshot()` taken at iteration start (pre-fix) and passed into `_log_iter`. Also: `$DIAGNOSE`/`$EXTRACT_*` are now invoked directly (their real files are `+x` with `#!/usr/bin/env python3` shebangs) instead of `python3 $DIAGNOSE`, because the plan's verbatim test stubs are bash scripts.
3. **T7:** `check_timing.py main()` uses manual `sys.argv` parsing, not argparse; `--journal` is handled as the first statement in `main()`.
4. **T8:** `analyze()` proposals are dicts keyed by `"parameter"`, not strategy-id strings; `rank_proposals()` was added as a standalone helper (the plan test calls it directly) and deliberately **not** wired into `analyze()` (would change existing output ordering).
5. **T9:** `_fix_effectiveness` guards a missing `fix_trajectories` table (mode=ro cannot `CREATE`); the existing `test_build_lineage_view` exact-key-set assertion was updated to include the additive `"fix_effectiveness"` key.
6. **T10:** no DB column added — `eval_arm` is encoded inside the existing `winning_config_json` field; a `summarize-fix` CLI subcommand was added.
7. **T12:** real `_batch` record shapes differ from the plan's invented fixture (`antenna_fix`: `{design,inst,status,before,after,wall_s}`; `beol_drc`: `{…,violations}` — no before-count; `retry/recover/orfs_retry`: `{…,orfs,from_stage?}` — no counts). The parser + tests were rewritten against the real shapes. Synthetic strategy labels: `beol_only_drc`, `rerun_from_stage`, `antenna_diode_repair`.
8. **T5/learn():** added `knowledge_db.ensure_schema(conn)` at entry so legacy DBs lacking the new tables don't crash; removed a now-dead `contextlib` import.

**Findings carried to the checkpoint:**
- **`repair_run_status` is a near-no-op on the current corpus** (partial 747→749, unknown 3→1). Real `stage_log.jsonl` stores integer exit codes (`"status": 0`), which `_derive_orfs_status` correctly does **not** treat as `"pass"`, and `knowledge_db.is_success` already credits signoff-positive partials to the learner. So the repair is a faithful no-invent reconciliation, **not** a mass `partial→pass` flip. (Possible follow-up: a reports-based status reconciler, if operators want clean partials shown as `pass` in `orfs_status`.)
- **Task 16 result:** 382 fix_events (drc 267 cleared / 1 win; orfs 70 cleared / 44 no_change) → 337 resolved + 45 abandoned trajectories → **122 family/platform `fix_recipes` entries** across 130 families. The read-only `fix_effectiveness` projection renders 142 groups.
- **Minor backfill data-quality:** a few `recover_pass` rows carry `platform=None` and a timeout value (e.g. `'14400'`) as `violation_class`. Cosmetic; affects only backfilled rows (the live loop records clean values). Candidate follow-up: tighten the backfill `from_stage`/`platform` mapping.

---

## Implementation Log — 2026-06-06 (Part B: Task 17 pilot + a live-loop bug fix)

**Status:** Task 17 (Phase C pilot) **executed** via a dynamic 4-agent workflow — one design per case type. The live `capture → learn` loop is now proven end-to-end (until now it had only ever run on *backfilled* data). Paused at the **Task 18 checkpoint** for user go/no-go before Phase E/F (Tasks 19–20, the large compute spend). Full suite **374 passed / 8 skipped** (was 373/8; +1 regression test).

**Pilot results (4 designs; all honest, no fabricated numbers):**

| Design | Case | Outcome | Live fix_event |
|---|---|---|---|
| `darkriscv_core` | synth `#include` | **completed** (incomplete→setup_complete) — reconstructed `rtl/config.vh` (minimal RV32I 3-stage) → full nangate45 backend, WNS +6.159, 0 route-DRC | none (run-completion) |
| `iccad2015_unit16_in1` | timing | **win** — `period_relax` 10→15 ns, WNS −4.508→−1.526 (severe→moderate, 66% reduction) | 1 (`timing`/`period_relax`/`win`) |
| `verilog_ethernet_eth_demux` | DRC | **residual** (honest) — 3× METAL5_ANTENNA; diode-repair already exhausted (irreducible per-net-PAR vs per-gate modeling gap) | 1 (`drc`/`none`→`inconclusive`) |
| `wb2axip_axi2axilite` | LVS | **abandoned** (honest) — `real_connectivity` genuine layout defect, no v1 lever | 1 (`lvs`/`none`→`inconclusive`) |

**Store delta:** fix_events 382→385 (**live 0→3**), run_violations **0→4**, fix_recipes entries 142→145 (added `test/nangate45 timing/severe` → `period_relax` red=0.66, plus honest no-lever records for `eth/drc/METAL5_ANTENNA` and `axi/lvs/real_connectivity`).

**Bug found + fixed (the pilot's payoff):** `ingest_run._normalize_verdict` only mapped the shell's legacy verdict strings, so the **canonical** verdicts `win`/`no_change`/`regression` emitted directly by `check_timing.py --journal` (Task 7) fell through to `inconclusive` — silently dropping the learning signal from every timing-journal episode. Backfill never exposed this (it writes canonical verdicts straight into `fix_events`); only the *live* timing path did. Fix: `_VERDICT_MAP` now passes canonical strings through idempotently (+ regression test `test_normalize_verdict_passes_through_canonical_strings`; doc note in `references/signoff-fixing.md` "Verdict vocabulary"). This is a 9th divergence-class finding, consistent with the verdict-vocabulary single-source invariant (Task 4).

**Non-bugs ruled out during the pilot:** (a) `extract_ppa` *does* store final WNS/TNS from `6_report.json` under `summary.timing.*`; an early "None" was an ad-hoc-script wrong-key error, not a code bug. (b) `repair_run_status` remains the documented near-no-op.

**Carried to the Task 18 checkpoint (open follow-ups, NOT blockers):**
- Family-inference granularity differs between backfill and live ingest (backfill maps `axi2axilite`→`axi`; live ingest keeps `axi2axilite`). Cosmetic now; tighten `infer_family` for consistency *before* the campaign aggregates recipes.
- `run_violations.timing_tier` for the iccad2015 re-run reads a stale `timing_check.json` (`severe`) while `wns_ns` is fresh (−1.526). Snapshot-freshness nit; does not affect recipes (those derive from Tier-2 trajectories).
- iccad2015 needs a longer period (or retiming) for full closure — a campaign action, not pilot scope.

---

## Implementation Log — 2026-06-06 (Part B: Task 19 targeted campaign — Phase E)

**Status:** User approved **Task 19 (targeted) ONLY** at the Task 18 checkpoint, re-checkpoint before Phase F. Executed via dynamic workflows (Waves A+C, then Wave B 2-phase) + direct serial ingest. **Paused for go/no-go before Task 20 (Phase F full sweep).**

**Store delta (pre-pilot baseline → end of Phase E):** fix_events 382→388 (**live 0→6**), run_violations **0→28**, runs 750→772, fix_trajectories 382→388.

**Wave A — violations (the learning gold):**
- Timing `period_relax`: `unit18_in1` **cleared** (WNS −3.32→+0.07, period 10→16), `unit10_in1` **cleared** (−0.88→+0.36, 10→12.4); `unit16_in1` (pilot) **win** (−4.51→−1.53). `unit19_in2` slow (3× 2400s timeouts at global route; resuming at 3600s — not blocking the milestone).
- LVS: `wb2axip_axilsingle` **honest abandoned** (438 real_connectivity, no v1 lever) — joins `wb2axip_axi2axilite` (pilot).
- **Warmed recipe (the payoff):** `test/nangate45 timing` now has `period_relax` evidence — severe: 2 att/1 succ, median WNS reduction **82%**; minor: 1/1, **59%**. The loop is producing an evidence-ranked suggestion from live episodes.

**Wave C — PD-recovery:** `RISCyMCU_src_MCU_Toplevel` **completed** (fixed PDN-0185 small-die metal4 strap), `vtr…clog2_test` **completed** (degenerate 3-bit pass-through; enlarged floorplan). `i2c_master` **blocked** — TritonCTS SIGSEGV (`separateMacroRegSinks`) at 4_1_cts (OpenROAD 26Q1 bug; honest, recorded). 9 BOOMs + arm_core skipped (documented-intractable).

**Wave B — #include header completions (2-phase: fetch/reconstruct + yosys-validate → fan-out backend): 18 of 22 completed.** Headers fetched from canonical upstreams (olgirard/openmsp430 `openMSP430_defines.v` 935L + `openGFX430_defines.v`; opencores `ethmac_defines.v` via the ORFS src mirror; kiclu/rv6 `config.vh`; SI-RISCV `e203_defines.v`) or reconstructed (ibex `prim_assert.sv` stub, RV32I `Def.v`, Riscy_SoC `opcodes.vh`). Every design elaboration-gated by standalone yosys before any backend run (no wasted backends). 4 incomplete (`openGFX430`, `rv6_pd`, `MS_DMAC`, `RV32I_ALU`) — session-limit casualties mid-route, resumable. **Cost note:** Wave B was token-heavy (32 agents, ~1.76M subagent tokens) and **hit the session limit** (resets 12:30pm ET); the remaining ingest/learn/commit were done with direct non-agent commands.

**Carried forward (for Phase F or a follow-up):** 4 incomplete Wave B designs (resumable); `unit19_in2` (resuming); `i2c_master` CTS SIGSEGV (candidate `failure-patterns.md` entry — OpenROAD TritonCTS bug); family-inference granularity (backfill `axi` vs live `axi2axilite`) still open and should be unified before a full-corpus Phase F so recipes aggregate consistently.

**Loose ends tidied (2026-06-06, user chose "tidy loose ends first" at the Phase-F re-checkpoint):**
- **Family-inference fixed** (commit `69a206c`): root cause was live ingest using `config.mk` DESIGN_NAME (drops the source-repo prefix) while `backfill_fix_events` uses the project-dir basename. New `ingest_run._project_family()` prefers a curated DESIGN_NAME mapping/pattern, else infers from the dir basename — so live + backfill share one family namespace. +regression test. Data-refreshed (commit later): deleted the 6 old-family live fix_events, re-ingested all 32 session designs → `iccad2015/nangate45 timing` and `wb2axip/nangate45 lvs` recipes now aggregate correctly (no more `test`/`axi2axilite`/`axilsingle` singletons).
- **Wave B completed 22/22**: the 4 stragglers (openGFX430, rv6_pd, MS_DMAC, RV32I_ALU) re-ran to 6_final via direct (non-agent) `run_orfs`.
- **`unit19_in2` cleared**: `period_relax` 10→16ns, WNS −3.315→+0.082 (severe→clean). `iccad2015/nangate45 timing/severe` recipe is now **3 attempts / 2 successes, 97.5% median WNS reduction**.
- **`i2c_master` CTS SIGSEGV**: confirmed against the existing `failure-patterns.md` "SIGSEGV in CTS init (`separateMacroRegSinks`)" variant (already documented; added the concrete instance).
- **Final store:** fix_events 388 (**live 7**), run_violations 33, runs 773. Suite 374/8 green. Commits `7dc4b6e`, `7b5d77d`, `971c6ff`, `69a206c`, + store refreshes. **Phase F (Task 20) still deferred** pending a future go/no-go.

---

## Implementation Log — 2026-06-07 (pre-Phase-F correctness review + Task 20 go)

**Status:** User authorized Task 20 (Phase F). Before the full-corpus enrichment, a two-stage
multi-agent review hardened the loop: (1) an **adversarial bug-hunt workflow** (6 reviewers over
the whole diff, every candidate independently refuted/confirmed) surfaced **15 verified bugs / 21
refuted** (36 candidates); (2) a **parallel TDD fix workflow** (6 agents on disjoint file-groups,
shared canonical specs) fixed them. Both seeds I expected — `run_violations.timing_tier` staleness
and backfill `platform=None` — were *refuted as bugs in themselves* (the timing_tier nit is
cosmetic and does not flow into recipes, per the plan; platform was handled as part of the family
unification below). Full suite **375 → 394 passed / 8 skipped** (+19 tests, zero regressions).

**15 bugs fixed** (each TDD, each with a concrete repro from the hunt):
1. **Cross-check session pollution (#2/#8, high):** `fix_signoff.sh` defaults to `--check both` and
   mints ONE `fix_session_id`, so DRC+LVS events shared a session and Tier-2 mis-filed LVS
   strategies under a DRC `violation_class`. Fix: group trajectories by `(fix_session_id,
   check_type)`; widened `fix_trajectories` PK to the composite (idempotent rebuild self-heals
   already-ingested mixed sessions).
2. **Family-namespace divergence (#1/#5, high):** backfill inferred family from the non-unique
   short `design` (DESIGN_NAME) while live ingest used the dir basename → recipes fragmented
   (`koios_lenet`→`myproject`, all iccad → `top`). Fix: backfill + the recipe reader
   (`diagnose_signoff_fix._load_recipes`) now use the **canonical rule** (explicit-or-dir-basename),
   matching `ingest_run._project_family`.
3. **Backfill session-id collisions (#4, med):** 22 iccad benchmarks share `design="top"`, so
   `sha1(design+file)` collided and `UNIQUE(session,iter,strategy)` dropped ~28 real records. Fix:
   key identity on the unique `case` (dir basename) → **backfill 382 → 410 rows** (recovered the 28).
4. **Timeout leak into `violation_class` (#6, med):** real `retry_pass3.jsonl` has
   `from_stage="14400"/"7200"` (a timeout) → junk `fix_recipes['orfs']['14400']`. Fix: validate
   `from_stage` against the ORFS stage vocabulary; non-stage → `violation_class='full'`.
5. **Backfill platform (extension of #1):** records carried `platform=None` → recipes stranded in
   the `'unknown'` bucket the reader never queries. Fix: resolve each record's real platform from
   its `config.mk` (fallback nangate45) → **0 NULL platforms; 66 families' DRC antenna/beol recipes
   now reachable at nangate45**; family/platform entries de-fragmented 131 → 107.
6. **Timing-journal key mismatch (#3, high):** `check_timing.py --journal` read `wns_ns`/
   `clock_period_ns` but the real `timing_check.json` uses `wns`/`clock_period` → WNS lost, wins
   mislabeled (the pre-existing test passed only because its fixture used the wrong keys). Fix:
   real-key-primary with legacy fallback; rewrote the test to the real schema.
7. **`minor` mislabeled `cleared` (#9, med):** `minor` tier is still WNS<0. Fix: `cleared` only when
   `after_tier=='clean'`; else `win`/`regression`/`no_change`.
8. **`win` never credited (#7/#11, med):** a `win` added an attempt but no success, ranking a
   reliable improver below an untried strategy. Fix: separate `wins` counter; score is now
   `(successes + 0.5·wins + 1)/(attempts + 2)` (backward compatible — `wins` defaults 0).
9. **Projection inflation (#10/#15, med):** `mine_rules` + `build_lineage_view` grouped by
   `winning_strategy` (null for abandoned) → every strategy showed `clearance_rate=1.0` + a phantom
   `null` bucket. Fix: attribute per strategy from `path_json`, matching Tier-3.
10. **Archival not lossless (#12, low):** Tier-2 rebuild read hot `fix_events` only, so a post-archive
    re-learn destroyed archived episodes' trajectories. Fix: archive-aware rebuild (ATTACH + UNION).
11. **Empty after-count phantom win (#14, low):** an unparseable re-check left verdict `applied`→`win`.
    Fix: emit `recheck_unparsed` (→ ingester normalizes to `inconclusive`).
12. **DRC reader vocab fallback (#13, low):** reader keyed the specific dominant category but coarse
    historical recipes are `antenna`/`beol`. Fix: coarse-bucket fallback when the exact lookup misses.

**Store regenerated** (purge backfill rows → re-backfill → re-learn): **417 fix_events** (410
backfill + 7 live), **0 NULL platforms**, no numeric `violation_class`, no junk families,
composite-PK `fix_trajectories` correctly split by check (drc 269 / lvs 2 / orfs 142 / timing 4),
recipes carry the `wins` counter. `runs.sqlite.pre-bugfix.bak` saved. Docs updated
(`references/signoff-fixing.md` "Correctness invariants"; `schema.sql` comment retargeted).
