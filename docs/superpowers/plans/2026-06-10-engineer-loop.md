# Engineer Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the r2g-rtl2gds learning loop: full-flow Tier-0 journaling (separate journal DB), recipe lifecycle with inline A/B efficacy gating, autonomous campaign orchestrator + agent escalation, symptom×design_class×platform recipe index, provenance tracing, strength metrics, and dead-code cleanup.

**Spec:** `docs/superpowers/specs/2026-06-09-engineer-loop-design.md` (rev 58ffa7d). Read it first.

**Architecture:** Two linked SQLite DBs — new gitignored `knowledge/journal.sqlite` (evidence: actions/log_summaries/tool_bugs) and existing git-tracked `knowledge/runs.sqlite` + `heuristics.json` (conclusions) — joined by `symptom_id`/`run_id`/`fix_session_id`. A deterministic orchestrator (`scripts/loop/engineer_loop.py`) drives flow→ingest→learn→A/B→promote; unknowns go to an `escalations` queue for the agent tier.

**Tech stack:** Python 3.10 stdlib only (sqlite3, json, hashlib, argparse), bash, pytest. NO new dependencies. Follow existing conventions: `knowledge_db.py` for schema/migrations, pure-model modules (like `fix_model.py`), env-gated never-break-the-flow side effects (like `R2G_FIX_AUTOLEARN`).

**Conventions all tasks MUST follow:**
- Tests live in `r2g-rtl2gds/tests/`; `conftest.py` already puts `knowledge/`, `scripts/reports/`, `scripts/flow/` on `sys.path` — import modules bare (`import journal_db`).
- Run tests from the skill root: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/<file> -v`.
- Every commit message uses `feat(skill):` / `fix(skill):` / `test(skill):` / `chore(skill):` prefixes.
- After the LAST task, run the FULL suite: `python3 -m pytest tests/ -q` (baseline: 417 passed; must not regress).
- All schema changes must be legacy-DB safe (CREATE TABLE IF NOT EXISTS + `_ADDED_COLUMNS`-style idempotent ALTERs).

---

### Task 1: Journal DB schema + `journal_db.py`

**Files:**
- Create: `r2g-rtl2gds/knowledge/journal_schema.sql`
- Create: `r2g-rtl2gds/knowledge/journal_db.py`
- Test: `r2g-rtl2gds/tests/test_journal_db.py`
- Modify: `.gitignore` (repo root)

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the Tier-0 journal DB (engineer-loop spec §5.2, decisions 10/11)."""
from pathlib import Path

import journal_db


def _conn(tmp_path: Path):
    c = journal_db.connect(tmp_path / "journal.sqlite")
    journal_db.ensure_schema(c)
    return c


def test_schema_creates_three_tables(tmp_path):
    c = _conn(tmp_path)
    tables = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"actions", "log_summaries", "tool_bugs"} <= tables


def test_wal_mode_enabled(tmp_path):
    c = _conn(tmp_path)
    assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_append_action_returns_id_and_persists(tmp_path):
    c = _conn(tmp_path)
    aid = journal_db.append_action(
        c, project_path="/p/x", actor="loop", action_type="tool_invoke",
        payload={"cmd": "make route", "exit_code": 0, "duration_s": 12.5},
        design="aes", platform="nangate45")
    row = c.execute("SELECT project_path, actor, action_type, run_id "
                    "FROM actions WHERE action_id=?", (aid,)).fetchone()
    assert row == ("/p/x", "loop", "tool_invoke", None)


def test_backfill_run_id_links_all_rows_for_project(tmp_path):
    c = _conn(tmp_path)
    journal_db.append_action(c, project_path="/p/x", actor="loop",
                             action_type="tool_invoke", payload={})
    journal_db.append_log_summary(c, project_path="/p/x", stage="route",
                                  tool="openroad", source_path="/p/x/l.log",
                                  status="pass", digest="ok")
    journal_db.append_tool_bug(c, project_path="/p/x", stage="cts",
                               tool="openroad", signature="SIGSEGV in repair",
                               symptom_id="abc123", log_excerpt="...")
    n = journal_db.backfill_run_id(c, project_path="/p/x", run_id="RUN1")
    assert n == 3
    for t in ("actions", "log_summaries", "tool_bugs"):
        assert c.execute(f"SELECT run_id FROM {t}").fetchone()[0] == "RUN1"


def test_backfill_does_not_clobber_existing_run_id(tmp_path):
    c = _conn(tmp_path)
    journal_db.append_action(c, project_path="/p/x", actor="loop",
                             action_type="promote", payload={}, run_id="OLD")
    assert journal_db.backfill_run_id(c, project_path="/p/x", run_id="NEW") == 0
    assert c.execute("SELECT run_id FROM actions").fetchone()[0] == "OLD"


def test_ensure_schema_idempotent_on_legacy_db(tmp_path):
    c = _conn(tmp_path)
    journal_db.ensure_schema(c)   # second call must not raise
    journal_db.ensure_schema(c)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_journal_db.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'journal_db'`

- [ ] **Step 3: Write `journal_schema.sql`**

```sql
-- Tier-0 journal DB (engineer-loop spec 2026-06-09 §5.2, decisions 10/11).
-- SEPARATE file knowledge/journal.sqlite — gitignored, high-volume EVIDENCE.
-- Conclusions live in runs.sqlite/heuristics.json. Append-only tables.

CREATE TABLE IF NOT EXISTS actions (
    action_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    project_path     TEXT NOT NULL,
    run_id           TEXT,              -- back-filled at ingest (run_id minted then)
    fix_session_id   TEXT,
    design           TEXT,
    platform         TEXT,
    actor            TEXT NOT NULL,     -- loop | agent | operator
    action_type      TEXT NOT NULL,     -- config_knob_delta|sdc_edit|stage_rerun|
                                        -- tool_invoke|escalate|ab_launch|promote|demote
    payload_json     TEXT,              -- knob old/new, cmd, exit code, duration, log path
    parent_action_id INTEGER,           -- groups a stacked fix
    symptom_id       TEXT               -- the bug being acted on (nullable)
);
CREATE INDEX IF NOT EXISTS idx_actions_project ON actions(project_path);
CREATE INDEX IF NOT EXISTS idx_actions_run     ON actions(run_id);
CREATE INDEX IF NOT EXISTS idx_actions_session ON actions(fix_session_id);
CREATE INDEX IF NOT EXISTS idx_actions_symptom ON actions(symptom_id);

CREATE TABLE IF NOT EXISTS log_summaries (
    summary_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    project_path  TEXT NOT NULL,
    run_id        TEXT,                 -- back-filled at ingest
    action_id     INTEGER,              -- the producing command's actions row
    stage         TEXT,
    tool          TEXT,
    source_path   TEXT,                 -- the raw log/report file (may rotate away)
    status        TEXT,                 -- pass | fail | unknown
    error_count   INTEGER,
    warning_count INTEGER,
    first_error   TEXT,
    last_lines    TEXT,                 -- bounded tail, only on failure
    metrics_json  TEXT,                 -- key numbers (wns, violation counts, ...)
    digest        TEXT                  -- compact deterministic text summary
);
CREATE INDEX IF NOT EXISTS idx_summaries_project ON log_summaries(project_path);
CREATE INDEX IF NOT EXISTS idx_summaries_run     ON log_summaries(run_id);

CREATE TABLE IF NOT EXISTS tool_bugs (
    bug_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    project_path   TEXT NOT NULL,
    run_id         TEXT,                -- back-filled at ingest
    action_id      INTEGER,
    stage          TEXT,
    tool           TEXT,
    signature      TEXT,                -- normalized error line
    symptom_id     TEXT,                -- cross-DB bug identity (decision 11)
    signature_json TEXT,
    log_excerpt    TEXT
);
CREATE INDEX IF NOT EXISTS idx_bugs_project ON tool_bugs(project_path);
CREATE INDEX IF NOT EXISTS idx_bugs_symptom ON tool_bugs(symptom_id);
```

- [ ] **Step 4: Write `journal_db.py`**

```python
#!/usr/bin/env python3
"""Tier-0 journal DB helpers (engineer-loop spec §5.2, decisions 10/11).

SEPARATE high-volume gitignored SQLite file (default knowledge/journal.sqlite).
EVIDENCE only — learning conclusions stay in runs.sqlite/heuristics.json, so
journal loss/rotation never loses a recipe. WAL + busy_timeout: safe for
concurrent append-only flow workers. Mirrors knowledge_db.py conventions.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path

DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent
DEFAULT_JOURNAL_PATH = DEFAULT_KNOWLEDGE_DIR / "journal.sqlite"
DEFAULT_SCHEMA_PATH = DEFAULT_KNOWLEDGE_DIR / "journal_schema.sql"

ACTION_TYPES = ("config_knob_delta", "sdc_edit", "stage_rerun", "tool_invoke",
                "escalate", "ab_launch", "promote", "demote")


def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def connect(db_path: Path | str = DEFAULT_JOURNAL_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


# Idempotent ALTERs for legacy journal DBs (same pattern as knowledge_db).
_ADDED_COLUMNS: dict[str, dict[str, str]] = {}


def ensure_schema(conn: sqlite3.Connection,
                  schema_path: Path | str = DEFAULT_SCHEMA_PATH) -> None:
    conn.executescript(Path(schema_path).read_text(encoding="utf-8"))
    for table, cols in _ADDED_COLUMNS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()


def append_action(conn, *, project_path: str, actor: str, action_type: str,
                  payload: dict | None = None, design: str | None = None,
                  platform: str | None = None, run_id: str | None = None,
                  fix_session_id: str | None = None,
                  parent_action_id: int | None = None,
                  symptom_id: str | None = None, ts: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO actions (ts, project_path, run_id, fix_session_id, design,"
        " platform, actor, action_type, payload_json, parent_action_id, symptom_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (ts or _now(), project_path, run_id, fix_session_id, design, platform,
         actor, action_type, json.dumps(payload or {}, sort_keys=True),
         parent_action_id, symptom_id))
    conn.commit()
    return cur.lastrowid


def append_log_summary(conn, *, project_path: str, stage: str | None,
                       tool: str | None, source_path: str | None,
                       status: str | None, digest: str,
                       error_count: int | None = None,
                       warning_count: int | None = None,
                       first_error: str | None = None,
                       last_lines: str | None = None,
                       metrics: dict | None = None,
                       action_id: int | None = None,
                       run_id: str | None = None, ts: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO log_summaries (ts, project_path, run_id, action_id, stage,"
        " tool, source_path, status, error_count, warning_count, first_error,"
        " last_lines, metrics_json, digest) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ts or _now(), project_path, run_id, action_id, stage, tool, source_path,
         status, error_count, warning_count, first_error, last_lines,
         json.dumps(metrics or {}, sort_keys=True), digest))
    conn.commit()
    return cur.lastrowid


def append_tool_bug(conn, *, project_path: str, stage: str | None,
                    tool: str | None, signature: str,
                    symptom_id: str | None = None,
                    signature_json: str | None = None,
                    log_excerpt: str | None = None,
                    action_id: int | None = None,
                    run_id: str | None = None, ts: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO tool_bugs (ts, project_path, run_id, action_id, stage, tool,"
        " signature, symptom_id, signature_json, log_excerpt)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ts or _now(), project_path, run_id, action_id, stage, tool, signature,
         symptom_id, signature_json, log_excerpt))
    conn.commit()
    return cur.lastrowid


def backfill_run_id(conn, *, project_path: str, run_id: str) -> int:
    """Link this project's journal rows to the run_id minted at ingest.
    Only fills NULLs — never clobbers an existing link (re-ingest safe)."""
    n = 0
    for t in ("actions", "log_summaries", "tool_bugs"):
        n += conn.execute(
            f"UPDATE {t} SET run_id=? WHERE project_path=? AND run_id IS NULL",
            (run_id, project_path)).rowcount
    conn.commit()
    return n
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_journal_db.py -v`
Expected: 6 passed

- [ ] **Step 6: Gitignore the journal DB**

In repo-root `.gitignore`, after the existing line `r2g-rtl2gds/knowledge/*.sqlite-wal`, add:

```gitignore
# Tier-0 journal DB: high-volume EVIDENCE, never shipped (engineer-loop decision 11)
r2g-rtl2gds/knowledge/journal.sqlite
r2g-rtl2gds/knowledge/journal.sqlite-*
```

- [ ] **Step 7: Commit**

```bash
git add r2g-rtl2gds/knowledge/journal_schema.sql r2g-rtl2gds/knowledge/journal_db.py r2g-rtl2gds/tests/test_journal_db.py .gitignore
git commit -m "feat(skill): Tier-0 journal DB (actions/log_summaries/tool_bugs, separate gitignored sqlite)"
```

---

### Task 2: `summarize_log.py` — deterministic log/report digests + bug detection

**Files:**
- Create: `r2g-rtl2gds/knowledge/summarize_log.py`
- Test: `r2g-rtl2gds/tests/test_summarize_log.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the deterministic log summarizer (spec decision 10)."""
import summarize_log


PASS_LOG = """[INFO GRT-0001] starting global route
[WARNING GRT-0044] congestion at gcell (1,2)
Finished route: 0 violations.
"""

FAIL_LOG = """[INFO DRT-0001] start detailed routing
[ERROR DRT-0085] cannot fix violation
[WARNING DRT-0009] net u1/n3 ripped up
Signal 11 received
""" + "\n".join(f"tail line {i}" for i in range(40))


def test_pass_log_counts_and_digest():
    s = summarize_log.summarize_text(PASS_LOG, status_hint="pass")
    assert s["error_count"] == 0
    assert s["warning_count"] == 1
    assert s["first_error"] is None
    assert s["last_lines"] is None            # tail only kept on failure
    assert "0 errors, 1 warnings" in s["digest"]


def test_fail_log_first_error_and_bounded_tail():
    s = summarize_log.summarize_text(FAIL_LOG, status_hint="fail")
    assert s["error_count"] == 1
    assert "[ERROR DRT-0085]" in s["first_error"]
    tail = s["last_lines"].splitlines()
    assert len(tail) <= summarize_log.TAIL_LINES
    assert tail[-1] == "tail line 39"


def test_detect_bugs_finds_sigsegv_with_symptom():
    bugs = summarize_log.detect_bugs(FAIL_LOG, check="orfs_stage", vclass="route")
    assert len(bugs) == 1
    b = bugs[0]
    assert "signal 11" in b["signature"].lower()
    assert b["symptom_id"] and len(b["symptom_id"]) == 16


def test_summarize_report_json_extracts_metrics():
    rep = {"status": "fail", "total_violations": 7,
           "categories": {"M3_ANTENNA": {"count": 7}}}
    s = summarize_log.summarize_report(rep, kind="drc")
    assert s["status"] == "fail"
    assert s["metrics"]["total_violations"] == 7
    assert "M3_ANTENNA" in s["digest"]


def test_deterministic():
    a = summarize_log.summarize_text(FAIL_LOG, status_hint="fail")
    b = summarize_log.summarize_text(FAIL_LOG, status_hint="fail")
    assert a == b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_summarize_log.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'summarize_log'`

- [ ] **Step 3: Write `summarize_log.py`**

```python
#!/usr/bin/env python3
"""Deterministic, stdlib-only log/report summarizer (engineer-loop decision 10).

Produces the log_summaries digest rows and tool_bugs detections for the Tier-0
journal. NEVER an LLM call — pure text extraction, fully reproducible. Raw log
files may rotate; the digest stored in journal.sqlite survives.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# knowledge/ sibling import (works as script or test module)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import symptom  # noqa: E402

TAIL_LINES = 25
EXCERPT_CHARS = 2000

_ERROR_RE = re.compile(r"^\s*(\[ERROR\b|ERROR[: ]|.*\[ERROR )", re.I)
_WARN_RE = re.compile(r"^\s*(\[WARNING\b|WARNING[: ]|.*\[WARNING )", re.I)
# EDA-tool bug signatures -> normalized signature text (orfs_stage symptoms).
_BUG_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"signal 1[01]\b|SIGSEGV|Segmentation fault", re.I), "sigsegv"),
    (re.compile(r"assert(ion)? (fail|violat)", re.I), "internal_assertion"),
    (re.compile(r"std::bad_alloc|out of memory|OOM killer", re.I), "oom"),
    (re.compile(r"Killed\b.*timeout|TIMEOUT reached", re.I), "timeout"),
]


def summarize_text(text: str, *, status_hint: str | None = None) -> dict:
    lines = text.splitlines()
    errors = [ln for ln in lines if _ERROR_RE.match(ln)]
    warnings = [ln for ln in lines if _WARN_RE.match(ln)]
    status = status_hint or ("fail" if errors else "pass")
    failed = status not in ("pass", "clean", "complete")
    digest = (f"{status}: {len(errors)} errors, {len(warnings)} warnings, "
              f"{len(lines)} lines")
    if errors:
        digest += f"; first_error={errors[0].strip()[:120]}"
    return {
        "status": status,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "first_error": errors[0].strip()[:300] if errors else None,
        "last_lines": "\n".join(lines[-TAIL_LINES:]) if failed else None,
        "digest": digest,
    }


def summarize_file(path: Path | str, *, status_hint: str | None = None) -> dict:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"status": "unknown", "error_count": None, "warning_count": None,
                "first_error": None, "last_lines": None,
                "digest": f"unreadable: {p}"}
    return summarize_text(text, status_hint=status_hint)


def summarize_report(report: dict, *, kind: str) -> dict:
    """Digest a parsed reports/<kind>.json (drc/lvs/rcx/ppa/timing_check)."""
    metrics: dict = {}
    for k in ("total_violations", "mismatch_count", "status", "tier",
              "wns_ns", "setup_wns"):
        if report.get(k) is not None:
            metrics[k] = report[k]
    cats = report.get("categories") or {}
    top = sorted(cats, key=lambda c: -(cats[c].get("count") or 0))[:5]
    digest = f"{kind} {report.get('status', 'unknown')}"
    if top:
        digest += " top:" + ",".join(f"{c}={cats[c].get('count')}" for c in top)
    return {"status": report.get("status"), "metrics": metrics, "digest": digest}


def detect_bugs(text: str, *, check: str = "orfs_stage",
                vclass: str | None = None) -> list[dict]:
    """Scan a log for EDA-tool bug signatures; tag each with its symptom_id so
    the journal-side bug links to knowledge-side symptoms (decision 11)."""
    bugs: list[dict] = []
    for ln in text.splitlines():
        for pat, label in _BUG_PATTERNS:
            if pat.search(ln):
                sig = symptom.canonical_signature(check, vclass or label, None)
                bugs.append({
                    "signature": f"{label}: {ln.strip()[:200]}",
                    "symptom_id": symptom.symptom_id(sig),
                    "signature_json": json.dumps(sig, sort_keys=True),
                    "log_excerpt": ln.strip()[:EXCERPT_CHARS],
                })
                break
    # One bug row per distinct label (first occurrence wins) — keep it bounded.
    seen, uniq = set(), []
    for b in bugs:
        lab = b["signature"].split(":", 1)[0]
        if lab not in seen:
            seen.add(lab)
            uniq.append(b)
    return uniq
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_summarize_log.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/knowledge/summarize_log.py r2g-rtl2gds/tests/test_summarize_log.py
git commit -m "feat(skill): deterministic log/report summarizer + EDA bug detection for journal"
```

---

### Task 3: `journal_action.py` CLI — one entry point for shell + agent journaling

**Files:**
- Create: `r2g-rtl2gds/knowledge/journal_action.py`
- Test: `r2g-rtl2gds/tests/test_journal_action.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the journal_action CLI (spec §5.2 producers)."""
import json
import subprocess
import sys
from pathlib import Path

import journal_db

CLI = Path(__file__).resolve().parents[1] / "knowledge" / "journal_action.py"


def _run(args, env_extra=None):
    import os
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(CLI)] + args,
                          capture_output=True, text=True, env=env)


def test_action_subcommand_appends_row(tmp_path):
    db = tmp_path / "journal.sqlite"
    r = _run(["action", "--project", "/p/x", "--actor", "agent",
              "--type", "config_knob_delta",
              "--payload", json.dumps({"knob": "CORE_UTILIZATION",
                                       "old": "30", "new": "20"}),
              "--db", str(db)])
    assert r.returncode == 0, r.stderr
    c = journal_db.connect(db)
    row = c.execute("SELECT actor, action_type, payload_json FROM actions").fetchone()
    assert row[0] == "agent" and row[1] == "config_knob_delta"
    assert json.loads(row[2])["knob"] == "CORE_UTILIZATION"


def test_summarize_subcommand_digests_log_file(tmp_path):
    db = tmp_path / "journal.sqlite"
    log = tmp_path / "route.log"
    log.write_text("[ERROR DRT-0085] cannot fix violation\nSignal 11 received\n")
    r = _run(["summarize", "--project", "/p/x", "--stage", "route",
              "--tool", "openroad", "--log", str(log), "--status", "fail",
              "--db", str(db)])
    assert r.returncode == 0, r.stderr
    c = journal_db.connect(db)
    assert c.execute("SELECT COUNT(*) FROM log_summaries").fetchone()[0] == 1
    # Failure log with a SIGSEGV pattern also lands a tool_bugs row.
    assert c.execute("SELECT COUNT(*) FROM tool_bugs").fetchone()[0] == 1


def test_journal_disabled_is_silent_noop(tmp_path):
    db = tmp_path / "journal.sqlite"
    r = _run(["action", "--project", "/p/x", "--actor", "loop",
              "--type", "promote", "--db", str(db)],
             env_extra={"R2G_JOURNAL": "0"})
    assert r.returncode == 0
    assert not db.exists()


def test_bad_db_path_warns_but_exits_zero(tmp_path):
    # Journal failures must NEVER break the flow (spec §7).
    r = _run(["action", "--project", "/p/x", "--actor", "loop",
              "--type", "promote", "--db", "/nonexistent/dir/x/j.sqlite"])
    assert r.returncode == 0
    assert "WARNING" in r.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_journal_action.py -v`
Expected: FAIL (CLI file does not exist → FileNotFoundError / returncode != 0)

- [ ] **Step 3: Write `journal_action.py`**

```python
#!/usr/bin/env python3
"""Append one Tier-0 journal entry. ONE entry point for every producer —
fix_signoff.sh, run_orfs.sh, run_{drc,lvs,rcx}.sh, engineer_loop.py and the
agent tier all journal identically (spec §5.2).

Contract: NEVER breaks the caller. Any failure prints WARNING to stderr and
exits 0. R2G_JOURNAL=0 disables journaling entirely (silent no-op).

Usage:
  journal_action.py action --project P --actor loop --type tool_invoke \
      [--payload JSON] [--design D] [--platform PL] [--session SID] \
      [--symptom SID16] [--parent N] [--db PATH]
  journal_action.py summarize --project P --stage route --tool openroad \
      --log FILE [--status pass|fail] [--action-id N] [--db PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _cmd_action(args) -> None:
    import journal_db
    conn = journal_db.connect(args.db)
    journal_db.ensure_schema(conn)
    journal_db.append_action(
        conn, project_path=args.project, actor=args.actor,
        action_type=args.type, payload=json.loads(args.payload or "{}"),
        design=args.design, platform=args.platform,
        fix_session_id=args.session, parent_action_id=args.parent,
        symptom_id=args.symptom)
    conn.close()


def _cmd_summarize(args) -> None:
    import journal_db
    import summarize_log
    conn = journal_db.connect(args.db)
    journal_db.ensure_schema(conn)
    s = summarize_log.summarize_file(args.log, status_hint=args.status)
    journal_db.append_log_summary(
        conn, project_path=args.project, stage=args.stage, tool=args.tool,
        source_path=str(args.log), status=s["status"],
        error_count=s["error_count"], warning_count=s["warning_count"],
        first_error=s["first_error"], last_lines=s["last_lines"],
        digest=s["digest"], action_id=args.action_id)
    if s["status"] not in ("pass", "clean", "complete", None):
        text = Path(args.log).read_text(encoding="utf-8", errors="ignore")
        for b in summarize_log.detect_bugs(text, vclass=args.stage):
            journal_db.append_tool_bug(
                conn, project_path=args.project, stage=args.stage,
                tool=args.tool, signature=b["signature"],
                symptom_id=b["symptom_id"], signature_json=b["signature_json"],
                log_excerpt=b["log_excerpt"], action_id=args.action_id)
    conn.close()


def main(argv=None) -> int:
    if os.environ.get("R2G_JOURNAL", "1") == "0":
        return 0
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pa = sub.add_parser("action")
    pa.add_argument("--project", required=True)
    pa.add_argument("--actor", required=True, choices=("loop", "agent", "operator"))
    pa.add_argument("--type", required=True)
    pa.add_argument("--payload")
    pa.add_argument("--design")
    pa.add_argument("--platform")
    pa.add_argument("--session")
    pa.add_argument("--symptom")
    pa.add_argument("--parent", type=int)
    pa.add_argument("--db", default=None)
    pa.set_defaults(fn=_cmd_action)
    ps = sub.add_parser("summarize")
    ps.add_argument("--project", required=True)
    ps.add_argument("--stage")
    ps.add_argument("--tool")
    ps.add_argument("--log", required=True)
    ps.add_argument("--status")
    ps.add_argument("--action-id", type=int, default=None)
    ps.add_argument("--db", default=None)
    ps.set_defaults(fn=_cmd_summarize)
    args = ap.parse_args(argv)
    if args.db is None:
        import journal_db
        args.db = journal_db.DEFAULT_JOURNAL_PATH
    try:
        args.fn(args)
    except Exception as exc:                      # never break the caller
        print(f"WARNING: journal_action skipped: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_journal_action.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/knowledge/journal_action.py r2g-rtl2gds/tests/test_journal_action.py
git commit -m "feat(skill): journal_action CLI — single never-breaking journaling entry point"
```

---

### Task 4: Instrument the flow scripts (full-flow telemetry producers)

**Files:**
- Modify: `r2g-rtl2gds/scripts/flow/run_orfs.sh` (stage loop, around line 228 where it appends to `stage_log.jsonl`)
- Modify: `r2g-rtl2gds/scripts/flow/fix_signoff.sh` (in `fix_one`, after the `--apply` succeeds, ~line 190)
- Test: `r2g-rtl2gds/tests/test_flow_journaling.py`

Pattern: every hook is a `journal_action.py` call guarded by `|| true` — journaling can never break the flow. `R2G_JOURNAL_DB` overrides the DB path for tests; `R2G_JOURNAL=0` disables.

- [ ] **Step 1: Write the failing test**

Tests drive the *shell hook functions* directly with a fake project, the same seam style `test_fix_signoff_logging.py` already uses (read that file first and copy its fake-runner fixture approach).

```python
"""Flow scripts journal commands/summaries/bugs into the journal DB (spec §5.2)."""
import json
import os
import subprocess
from pathlib import Path

import journal_db

SKILL = Path(__file__).resolve().parents[1]


def test_run_orfs_stage_journals_action_and_summary(tmp_path):
    """_journal_stage <stage> <status> <elapsed> <log> appends a tool_invoke
    action + a log summary; on failure also a tool_bugs row."""
    db = tmp_path / "journal.sqlite"
    proj = tmp_path / "proj"
    (proj / "backend").mkdir(parents=True)
    log = proj / "backend" / "5_route.log"
    log.write_text("[ERROR DRT-0085] cannot fix\nSignal 11 received\n")
    env = dict(os.environ, R2G_JOURNAL_DB=str(db))
    # Source run_orfs.sh's journal helper in isolation (guarded by sourcing flag).
    r = subprocess.run(
        ["bash", "-c",
         f'R2G_SOURCE_ONLY=1 source "{SKILL}/scripts/flow/run_orfs.sh"; '
         f'PROJECT_DIR="{proj}"; PLATFORM=nangate45; '
         f'_journal_stage route fail 42 "{log}"'],
        capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    c = journal_db.connect(db)
    act = c.execute("SELECT action_type, payload_json FROM actions").fetchone()
    assert act[0] == "tool_invoke"
    assert json.loads(act[1])["stage"] == "route"
    assert c.execute("SELECT COUNT(*) FROM log_summaries").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM tool_bugs").fetchone()[0] == 1


def test_fix_signoff_journals_each_knob_delta(tmp_path):
    """fix_signoff.sh's _journal_knob_deltas splits a config_edits dict into one
    config_knob_delta action per knob (spec: each knob INDIVIDUALLY)."""
    db = tmp_path / "journal.sqlite"
    proj = tmp_path / "proj"
    proj.mkdir()
    env = dict(os.environ, R2G_JOURNAL_DB=str(db))
    edits = json.dumps({"SKIP_ANTENNA_REPAIR": "1",
                        "MAX_REPAIR_ANTENNAS_ITER_DRT": "10"})
    r = subprocess.run(
        ["bash", "-c",
         f'R2G_SOURCE_ONLY=1 source "{SKILL}/scripts/flow/fix_signoff.sh"; '
         f'PROJECT_DIR="{proj}"; FIX_SESSION_ID=abcd1234abcd1234; '
         f"_journal_knob_deltas '{edits}' antenna_diode_repair"],
        capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    c = journal_db.connect(db)
    rows = c.execute("SELECT action_type, fix_session_id, payload_json "
                     "FROM actions ORDER BY action_id").fetchall()
    assert len(rows) == 2
    assert all(t == "config_knob_delta" for t, _, _ in rows)
    assert all(s == "abcd1234abcd1234" for _, s, _ in rows)
    knobs = {json.loads(p)["knob"] for _, _, p in rows}
    assert knobs == {"SKIP_ANTENNA_REPAIR", "MAX_REPAIR_ANTENNAS_ITER_DRT"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_flow_journaling.py -v`
Expected: FAIL (`_journal_stage: command not found` and `R2G_SOURCE_ONLY` unsupported)

- [ ] **Step 3: Add the source-only guard + `_journal_stage` to `run_orfs.sh`**

Near the top of `run_orfs.sh` (after `SCRIPT_DIR` is computed), define:

```bash
KNOWLEDGE_DIR_J="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../knowledge" && pwd)"
JOURNAL="${R2G_JOURNAL_CLI:-$KNOWLEDGE_DIR_J/journal_action.py}"

_journal_stage() {  # stage status elapsed_s log_file — never breaks the flow
  local stage="$1" status="$2" elapsed="$3" log="$4"
  python3 "$JOURNAL" action --project "$PROJECT_DIR" --actor loop \
    --type tool_invoke --platform "${PLATFORM:-}" \
    --payload "{\"stage\":\"$stage\",\"status\":\"$status\",\"elapsed_s\":$elapsed,\"log\":\"$log\",\"cmd\":\"make $stage\"}" \
    ${R2G_JOURNAL_DB:+--db "$R2G_JOURNAL_DB"} 2>/dev/null || true
  [[ -f "$log" ]] && python3 "$JOURNAL" summarize --project "$PROJECT_DIR" \
    --stage "$stage" --tool openroad --log "$log" --status "$status" \
    ${R2G_JOURNAL_DB:+--db "$R2G_JOURNAL_DB"} 2>/dev/null || true
}

# Test seam: allow sourcing helpers without executing the flow.
[[ "${R2G_SOURCE_ONLY:-0}" == "1" ]] && return 0 2>/dev/null
```

Then, at the existing stage-loop point that appends to `$BACKEND_DIR/stage_log.jsonl` (line ~228), add right after that echo (the stage's ORFS log lives under the ORFS run dir; pass the most specific log path available at that point — the `make` output tee'd file the stage loop already manages):

```bash
_journal_stage "$stage" "$([[ $STAGE_STATUS == \"pass\" || $STAGE_STATUS == 0 ]] && echo pass || echo fail)" "$stage_elapsed" "$STAGE_LOG_FILE"
```

NOTE for the implementer: inspect the surrounding loop in `run_orfs.sh` first — reuse the *actual* variable holding the per-stage log path (search for `tee` or the `2>&1` redirect target near line 228) and the actual `$STAGE_STATUS` convention (it is the embedded value written into stage_log.jsonl). Keep the call ONE line guarded by the function's internal `|| true`.

- [ ] **Step 4: Add `_journal_knob_deltas` to `fix_signoff.sh`**

After the `DIAGNOSE=` line (~line 39), add the same `JOURNAL=`/source-only guard pattern as Step 3 (reusing `$KNOWLEDGE_DIR` which fix_signoff.sh already computes), then:

```bash
_journal_knob_deltas() {  # config_edits_json strategy_id — one action per knob
  python3 - "$1" "$2" "$PROJECT_DIR" "$FIX_SESSION_ID" <<'PYEOF' 2>/dev/null || true
import json, os, subprocess, sys
edits, strat, proj, sess = json.loads(sys.argv[1] or "{}"), sys.argv[2], sys.argv[3], sys.argv[4]
cli = os.path.join(os.environ.get("R2G_KNOWLEDGE_DIR", ""), "journal_action.py")
for knob, new in edits.items():
    args = [sys.executable, cli, "action", "--project", proj, "--actor", "loop",
            "--type", "config_knob_delta", "--session", sess,
            "--payload", json.dumps({"knob": knob, "new": str(new), "strategy": strat})]
    db = os.environ.get("R2G_JOURNAL_DB")
    if db:
        args += ["--db", db]
    subprocess.run(args, check=False)
PYEOF
}
[[ "${R2G_SOURCE_ONLY:-0}" == "1" ]] && return 0 2>/dev/null
```

Then in `fix_one`, right after `cfg_delta="$(python3 -c ...)"` extracts the applied edits (~line 189), add:

```bash
    _journal_knob_deltas "$cfg_delta" "$sid"
```

CAUTION: `fix_signoff.sh` runs with `set -euo pipefail` and ends with real flow execution at line ~236 (`: > "$LOG"` etc.). The `R2G_SOURCE_ONLY` guard MUST be placed before that bottom section AND after all function definitions — place it immediately after the last function definition (`fix_one`'s closing brace).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_flow_journaling.py tests/test_fix_signoff_logging.py tests/test_fix_signoff_log.py tests/test_fix_signoff_adaptive.py -v`
Expected: new tests pass AND the three existing fix_signoff test files still pass (the guard must not change current behavior).

- [ ] **Step 6: Add summary hooks to run_drc.sh / run_lvs.sh / run_rcx.sh**

In each of `r2g-rtl2gds/scripts/flow/run_drc.sh`, `run_lvs.sh`, `run_rcx.sh`: find where the tool finishes and its log path is known (each script already captures a log under `$PROJECT_DIR/<check>/` or `reports/` — grep for `tee\|\.log` in each), append ONE guarded line:

```bash
python3 "$KNOWLEDGE_DIR/journal_action.py" summarize --project "$PROJECT_DIR" \
  --stage <check> --tool <klayout|openrcx> --log "$THE_LOG" \
  ${R2G_JOURNAL_DB:+--db "$R2G_JOURNAL_DB"} 2>/dev/null || true
```

(where `<check>` is drc/lvs/rcx and `$KNOWLEDGE_DIR` is computed the same way as in fix_signoff.sh — add the two-line computation if the script lacks it). No test beyond a smoke `bash -n` syntax check: `bash -n scripts/flow/run_drc.sh scripts/flow/run_lvs.sh scripts/flow/run_rcx.sh`.

- [ ] **Step 7: Commit**

```bash
git add r2g-rtl2gds/scripts/flow/run_orfs.sh r2g-rtl2gds/scripts/flow/fix_signoff.sh r2g-rtl2gds/scripts/flow/run_drc.sh r2g-rtl2gds/scripts/flow/run_lvs.sh r2g-rtl2gds/scripts/flow/run_rcx.sh r2g-rtl2gds/tests/test_flow_journaling.py
git commit -m "feat(skill): journal full-flow telemetry from run_orfs/fix_signoff/run_{drc,lvs,rcx}"
```

---

### Task 5: Ingest — design_class + strength stamps + generation + journal run_id back-fill

**Files:**
- Modify: `r2g-rtl2gds/knowledge/knowledge_db.py` (`_ADDED_COLUMNS["runs"]`)
- Modify: `r2g-rtl2gds/knowledge/ingest_run.py` (`ingest()` + `row` dict)
- Test: `r2g-rtl2gds/tests/test_ingest_engineer_loop.py`

- [ ] **Step 1: Write the failing test**

Use the existing `test_ingest_run.py` fixture style (read it first): it builds a fake project dir with `constraints/config.mk` + `reports/*.json` and calls `ingest_run.ingest(project, conn)`.

```python
"""Ingest stamps design_class/strength/generation and back-fills journal run_id."""
import json
from pathlib import Path

import ingest_run
import journal_db
import knowledge_db


def _mk_project(tmp_path: Path, name="aes_unit1", cells=1200) -> Path:
    p = tmp_path / name
    (p / "constraints").mkdir(parents=True)
    (p / "reports").mkdir()
    (p / "rtl").mkdir()
    (p / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = aes\nexport PLATFORM = nangate45\n")
    (p / "rtl" / "design.v").write_text("module aes(); // sbox cipher\nendmodule\n")
    (p / "reports" / "ppa.json").write_text(json.dumps(
        {"summary": {"timing": {"setup_wns": -0.05}},
         "geometry": {"instance_count": cells}}))
    (p / "reports" / "drc.json").write_text(json.dumps({"status": "clean"}))
    (p / "reports" / "lvs.json").write_text(json.dumps({"status": "clean"}))
    return p


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "runs.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def test_design_class_stamped_structurally(tmp_path):
    conn = _conn(tmp_path)
    rid = ingest_run.ingest(_mk_project(tmp_path), conn)
    row = conn.execute("SELECT design_class FROM runs WHERE run_id=?",
                       (rid,)).fetchone()
    # RTL contains 'cipher'/'sbox' -> crypto; 1200 cells -> small
    assert row[0] == "crypto/small"


def test_first_attempt_clean_true_then_false_for_repeat(tmp_path):
    conn = _conn(tmp_path)
    p = _mk_project(tmp_path)
    ingest_run.ingest(p, conn)
    first = conn.execute("SELECT first_attempt_clean FROM runs").fetchone()[0]
    assert first == 1
    # touch ppa.json -> new run_id, same design+platform -> not first attempt
    ppa = p / "reports" / "ppa.json"
    ppa.write_text(ppa.read_text())
    import os
    os.utime(ppa, (os.path.getmtime(ppa) + 5, os.path.getmtime(ppa) + 5))
    rid2 = ingest_run.ingest(p, conn)
    assert conn.execute("SELECT first_attempt_clean FROM runs WHERE run_id=?",
                        (rid2,)).fetchone()[0] == 0


def test_generation_stamped_from_heuristics(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    h = tmp_path / "heuristics.json"
    h.write_text(json.dumps({"generation": 7, "families": {}}))
    monkeypatch.setenv("R2G_HEURISTICS_PATH", str(h))
    rid = ingest_run.ingest(_mk_project(tmp_path), conn)
    assert conn.execute("SELECT heuristics_generation FROM runs WHERE run_id=?",
                        (rid,)).fetchone()[0] == 7


def test_journal_run_id_backfilled(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    p = _mk_project(tmp_path)
    jdb = tmp_path / "journal.sqlite"
    monkeypatch.setenv("R2G_JOURNAL_DB", str(jdb))
    jc = journal_db.connect(jdb)
    journal_db.ensure_schema(jc)
    journal_db.append_action(jc, project_path=str(p.resolve()), actor="loop",
                             action_type="tool_invoke", payload={})
    rid = ingest_run.ingest(p, conn)
    assert jc.execute("SELECT run_id FROM actions").fetchone()[0] == rid


def test_fix_iters_to_clean_from_fix_log(tmp_path):
    conn = _conn(tmp_path)
    p = _mk_project(tmp_path)
    (p / "reports" / "fix_log.jsonl").write_text("\n".join([
        json.dumps({"fix_session_id": "s1", "check": "drc", "iter": 1,
                    "strategy": "a", "before": 9, "after": 4, "verdict": "applied"}),
        json.dumps({"fix_session_id": "s1", "check": "drc", "iter": 2,
                    "strategy": "b", "before": 4, "after": 0, "verdict": "cleared"}),
    ]))
    rid = ingest_run.ingest(p, conn)
    assert conn.execute("SELECT fix_iters_to_clean FROM runs WHERE run_id=?",
                        (rid,)).fetchone()[0] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_ingest_engineer_loop.py -v`
Expected: FAIL (`no such column: design_class` etc.)

- [ ] **Step 3: Add the runs columns in `knowledge_db.py`**

Extend `_ADDED_COLUMNS["runs"]`:

```python
    "runs": {
        "lvs_mismatch_class": "TEXT",
        "eval_arm": "TEXT",
        # Engineer-loop (spec 2026-06-09 decisions 6+8): structural class stamp
        # ("<design_type>/<size_class>", never the name) + strength metrics +
        # the heuristics generation in force when this run executed.
        "design_class": "TEXT",
        "heuristics_generation": "INTEGER",
        "first_attempt_clean": "INTEGER",
        "fix_iters_to_clean": "INTEGER",
        "wall_s_to_clean": "REAL",
    },
```

- [ ] **Step 4: Implement in `ingest_run.py`**

Add a helper near `_project_family` (design_type via RTL keyword scan — same keywords as `suggest_config.detect_design_type`, duplicated here deliberately because `suggest_config` lives outside knowledge/ and needs a project layout; keep both in sync via comment):

```python
# Size bands match suggest_config.recommend (tiny<100, small<5000, medium<50000).
def _size_class(cell_count: int | None) -> str:
    if not cell_count:
        return "unknown"
    if cell_count < 100:
        return "tiny"
    if cell_count < 5000:
        return "small"
    if cell_count < 50000:
        return "medium"
    return "large"


# Keep keyword sets in sync with suggest_config.detect_design_type (the
# canonical classifier; this is the ingest-side mirror for stored runs).
_BUS_KW = ("crossbar", "arbiter", "interconnect", "wb_conmax", "axi_", "ahb_")
_CRYPTO_KW = ("aes", "sha", "des_", "cipher", "encrypt", "sbox")


def _design_type(project: Path, cfg: dict[str, str]) -> str:
    blob = ""
    rtl_dir = project / "rtl"
    if rtl_dir.is_dir():
        for f in sorted(rtl_dir.glob("*.v"))[:50]:
            try:
                blob += f.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                pass
    if any(k in blob for k in _BUS_KW):
        return "bus_heavy"
    if any(k in blob for k in _CRYPTO_KW):
        return "crypto"
    if "sram" in blob or cfg.get("ADDITIONAL_LEFS"):
        return "macro_heavy"
    return "logic"


def _heuristics_generation() -> int | None:
    import os
    hp = Path(os.environ.get("R2G_HEURISTICS_PATH",
              knowledge_db.DEFAULT_KNOWLEDGE_DIR / "heuristics.json"))
    data = _read_json(hp) or {}
    return data.get("generation")
```

Inside `ingest()`, before the `row = {` dict is built, compute:

```python
    design_class = f"{_design_type(project, cfg)}/{_size_class(cell_count)}"
    prior = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE design_name=? AND platform=? AND run_id!=?",
        (design_name, platform, run_id)).fetchone()[0]
    is_clean = (drc.get("status") in ("clean", "clean_beol")
                and lvs.get("status") in ("clean", "skipped", None))
    fix_rows = _read_fix_log(project)
    cleared = [r for r in fix_rows if r.get("verdict") == "cleared"]
    fix_iters_to_clean = max((_to_int(r.get("iter")) or 0 for r in cleared),
                             default=None) if cleared else None
```

(NOTE: `cell_count` is computed ~30 lines above the `row` dict today — move the `design_class` computation AFTER the existing `cell_count` assignment.)

Add to the `row` dict (after `"eval_arm"`):

```python
        "design_class":          design_class,
        "heuristics_generation": _heuristics_generation(),
        "first_attempt_clean":   (1 if is_clean else 0) if prior == 0 else 0,
        "fix_iters_to_clean":    fix_iters_to_clean,
        "wall_s_to_clean":       total_elapsed if is_clean else None,
```

At the end of `ingest()` (after `_record_lineage`, before `conn.commit()`), back-fill the journal (never-breaking):

```python
    try:
        import os
        import journal_db
        jpath = os.environ.get("R2G_JOURNAL_DB", journal_db.DEFAULT_JOURNAL_PATH)
        if Path(jpath).exists():
            jc = journal_db.connect(jpath)
            journal_db.backfill_run_id(jc, project_path=str(project.resolve()),
                                       run_id=run_id)
            jc.close()
    except Exception as exc:
        print(f"WARNING: journal run_id backfill skipped: {exc}", file=sys.stderr)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_ingest_engineer_loop.py tests/test_ingest_run.py tests/test_ingest_fix_events.py -v`
Expected: new tests pass; existing ingest tests unaffected.

- [ ] **Step 6: Commit**

```bash
git add r2g-rtl2gds/knowledge/knowledge_db.py r2g-rtl2gds/knowledge/ingest_run.py r2g-rtl2gds/tests/test_ingest_engineer_loop.py
git commit -m "feat(skill): ingest stamps design_class + strength metrics + generation; back-fills journal run_id"
```

---

### Task 6: Learner — `recipes[symptom_id][design_class][platform]` projection + generation counter

**Files:**
- Modify: `r2g-rtl2gds/knowledge/learn_heuristics.py`
- Modify: `r2g-rtl2gds/knowledge/schema.sql` (one new tiny table `meta`)
- Test: `r2g-rtl2gds/tests/test_learn_recipes_indexed.py`

Decision-8 index: primary `symptom_id`, conditioning `design_class` then `platform`, with `"*"` pooled rollups materialized at each relaxation level. The existing `symptoms` projection stays (backward compat for `load_symptom_recipe`); the NEW `recipes` projection is the decision-8 view. `design_class` for a trajectory comes from joining `runs` on `project_path` (trajectories carry `project_path`).

- [ ] **Step 1: Write the failing test**

```python
"""Decision-8 recipe projection + monotonic generation counter."""
import json

import knowledge_db
import learn_heuristics


def _seed(conn, *, design_class="crypto/small", platform="nangate45",
          sid="s1", strategy="antenna_diode_repair", verdict="cleared"):
    conn.execute(
        "INSERT OR REPLACE INTO runs (run_id, project_path, design_name, "
        "design_family, platform, ingested_at, design_class) "
        "VALUES (?,?,?,?,?,?,?)",
        (f"r_{sid}", f"/p/{sid}", f"d_{sid}", "fam", platform,
         "2026-06-10T00:00:00Z", design_class))
    conn.execute(
        "INSERT OR IGNORE INTO fix_events (fix_session_id, project_path, "
        "design_name, platform, check_type, violation_class, iter, strategy, "
        "before_count, after_count, verdict, ts, provenance, symptom_id, "
        "signature_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, f"/p/{sid}", f"d_{sid}", platform, "drc", "antenna", 1, strategy,
         5, 0 if verdict == "cleared" else 5, verdict,
         "2026-06-10T00:00:00Z", "live", "deadbeef00000001",
         json.dumps({"check": "drc", "class": "antenna", "predicates": {}})))
    conn.commit()


def test_recipes_keyed_symptom_class_platform(tmp_path):
    db = tmp_path / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed(conn)
    data = learn_heuristics.learn(db, tmp_path / "heuristics.json")
    node = data["recipes"]["deadbeef00000001"]["crypto/small"]["nangate45"]
    assert node["strategies"]["antenna_diode_repair"]["successes"] == 1


def test_star_rollups_pool_across_class_and_platform(tmp_path):
    db = tmp_path / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed(conn, design_class="crypto/small", platform="nangate45", sid="s1")
    _seed(conn, design_class="logic/medium", platform="sky130hd", sid="s2")
    data = learn_heuristics.learn(db, tmp_path / "heuristics.json")
    bucket = data["recipes"]["deadbeef00000001"]
    # class rollup pools both classes for one platform-agnostic view
    assert bucket["*"]["*"]["strategies"]["antenna_diode_repair"]["attempts"] == 2
    assert bucket["crypto/small"]["*"]["strategies"][
        "antenna_diode_repair"]["attempts"] == 1


def test_generation_increments_monotonically(tmp_path):
    db = tmp_path / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed(conn)
    d1 = learn_heuristics.learn(db, tmp_path / "heuristics.json")
    d2 = learn_heuristics.learn(db, tmp_path / "heuristics.json")
    assert d2["generation"] == d1["generation"] + 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_learn_recipes_indexed.py -v`
Expected: FAIL with `KeyError: 'recipes'`

- [ ] **Step 3: Add `meta` table to `schema.sql`**

Append at the end of `schema.sql`:

```sql
-- Engineer-loop (spec 2026-06-09): single-row store metadata. 'generation' is a
-- monotonic counter bumped by every learn_heuristics.learn() rebuild; stamped
-- into heuristics.json and onto runs.heuristics_generation at ingest.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
```

- [ ] **Step 4: Implement in `learn_heuristics.py`**

Add after `_symptom_recipes_from_trajectories`:

```python
def _bump_generation(conn) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key='generation'").fetchone()
    gen = (int(row[0]) if row else 0) + 1
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('generation', ?)",
                 (str(gen),))
    conn.commit()
    return gen


def _design_class_by_project(conn) -> dict[str, str]:
    return {r[0]: (r[1] or "unknown/unknown") for r in conn.execute(
        "SELECT project_path, design_class FROM runs WHERE project_path IS NOT NULL")}


def _indexed_recipes(trajectories: list[dict],
                     class_of: dict[str, str]) -> dict:
    """Decision-8 projection: recipes[symptom_id][design_class][platform] with
    '*' pooled rollups at each relaxation level. Strategy counts mirror
    _recipes_from_trajectories semantics (cleared/win/no_change/regression)."""
    def _node():
        return {"strategies": {}, "_sessions": set()}

    acc: dict = {}
    for t in trajectories:
        sid = t.get("symptom_id")
        if not sid:
            continue
        dclass = class_of.get(t.get("project_path") or "", "unknown/unknown")
        plat = t.get("platform") or "unknown"
        bucket = acc.setdefault(sid, {})
        targets = [bucket.setdefault(dc, {}).setdefault(p, _node())
                   for dc in (dclass, "*") for p in (plat, "*")]
        for step in json.loads(t.get("path_json") or "[]"):
            strat = step.get("strategy")
            if not strat or strat == "none":
                continue
            verdict = step.get("verdict")
            for node in targets:
                node["_sessions"].add(t.get("fix_session_id"))
                s = node["strategies"].setdefault(
                    strat, {"attempts": 0, "successes": 0, "failures": 0, "wins": 0})
                s["attempts"] += 1
                if verdict == "cleared":
                    s["successes"] += 1
                elif verdict == "win":
                    s["wins"] += 1
                elif verdict in ("no_change", "regression"):
                    s["failures"] += 1
    for sid, classes in acc.items():
        for dc, plats in classes.items():
            for p, node in plats.items():
                node["n_sessions"] = len(node.pop("_sessions"))
    return acc
```

In `learn()`: after trajectories are re-read on `conn2`, ALSO fetch `class_of = _design_class_by_project(conn2)` before `conn2.close()`; reopen a connection (or do the bump on `conn2` before close) to call `gen = _bump_generation(conn2)`. Then extend the output dict:

```python
    data = {
        "generated_at": ...,                      # unchanged
        "source_run_count": len(rows),
        "min_successful_runs_required": MIN_SUCCESSFUL,
        "schema_version": 3,                       # decision-8 recipes projection
        "generation": gen,
        "families": families,
        "symptoms": _symptom_recipes_from_trajectories(trajectories),
        "recipes": _indexed_recipes(trajectories, class_of),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_learn_recipes_indexed.py tests/test_learn_heuristics.py tests/test_learn_fix.py -v`
Expected: new pass; existing learner tests unaffected (additive output keys only).

- [ ] **Step 6: Commit**

```bash
git add r2g-rtl2gds/knowledge/learn_heuristics.py r2g-rtl2gds/knowledge/schema.sql r2g-rtl2gds/tests/test_learn_recipes_indexed.py
git commit -m "feat(skill): decision-8 recipe projection (symptom x design_class x platform) + generation counter"
```

---

### Task 7: `recipe_lifecycle.py` — shadow→candidate→promoted with generation diff

**Files:**
- Create: `r2g-rtl2gds/knowledge/recipe_lifecycle.py`
- Modify: `r2g-rtl2gds/knowledge/schema.sql` (new `recipe_status` table)
- Test: `r2g-rtl2gds/tests/test_recipe_lifecycle.py`

- [ ] **Step 1: Write the failing test**

```python
"""Recipe lifecycle: efficacy-gated promotion (spec §5.3, decisions 7+8)."""
import json

import knowledge_db
import recipe_lifecycle


KEY = dict(symptom_id="deadbeef00000001", design_class="crypto/small",
           platform="nangate45", strategy="antenna_diode_repair")


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "runs.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def _heur(gen, attempts):
    return {"generation": gen, "recipes": {KEY["symptom_id"]: {
        KEY["design_class"]: {KEY["platform"]: {
            "strategies": {KEY["strategy"]: {"attempts": attempts,
                                             "successes": attempts,
                                             "failures": 0, "wins": 0}},
            "n_sessions": attempts}}}}}


def test_diff_enqueues_new_recipe_as_candidate(tmp_path):
    conn = _conn(tmp_path)
    cands = recipe_lifecycle.diff_and_enqueue(conn, _heur(2, 1), prev=_heur(1, 0))
    assert cands == [tuple(KEY.values())]
    st = recipe_lifecycle.get_status(conn, **KEY)
    assert st == "candidate"


def test_unchanged_recipe_not_reenqueued(tmp_path):
    conn = _conn(tmp_path)
    recipe_lifecycle.diff_and_enqueue(conn, _heur(2, 1), prev=_heur(1, 0))
    assert recipe_lifecycle.diff_and_enqueue(conn, _heur(3, 1), prev=_heur(2, 1)) == []


def test_promote_requires_candidate_and_records_provenance(tmp_path):
    conn = _conn(tmp_path)
    recipe_lifecycle.diff_and_enqueue(conn, _heur(2, 1), prev=_heur(1, 0))
    recipe_lifecycle.promote(conn, **KEY, evidence="ab_trial:42")
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"


def test_demote_on_loss_reverts_to_shadow(tmp_path):
    conn = _conn(tmp_path)
    recipe_lifecycle.diff_and_enqueue(conn, _heur(2, 1), prev=_heur(1, 0))
    recipe_lifecycle.demote(conn, **KEY, reason="ab_loss")
    assert recipe_lifecycle.get_status(conn, **KEY) == "shadow"


def test_unknown_key_defaults_to_promoted_for_grandfathered(tmp_path):
    # Pre-lifecycle learned recipes are grandfathered (spec §5.3 bootstrap):
    # absent row -> treated as promoted so existing live ranking keeps working.
    conn = _conn(tmp_path)
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"


def test_filter_promoted_strips_unpromoted_strategies(tmp_path):
    conn = _conn(tmp_path)
    recipe_lifecycle.diff_and_enqueue(conn, _heur(2, 1), prev=_heur(1, 0))
    entry = {"strategies": {KEY["strategy"]: {"attempts": 1, "successes": 1},
                            "other_strat": {"attempts": 2, "successes": 0}},
             "n_sessions": 3}
    out = recipe_lifecycle.filter_promoted(conn, entry, symptom_id=KEY["symptom_id"],
                                           design_class=KEY["design_class"],
                                           platform=KEY["platform"])
    # candidate (not yet promoted) is stripped; absent-row strategy grandfathered.
    assert "antenna_diode_repair" not in out["strategies"]
    assert "other_strat" in out["strategies"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_recipe_lifecycle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recipe_lifecycle'`

- [ ] **Step 3: Add `recipe_status` to `schema.sql`**

```sql
-- Engineer-loop recipe lifecycle (spec §5.3, decisions 7+8). Status of one
-- strategy under one decision-8 key. Absent row = 'promoted' (grandfathered:
-- recipes learned before the lifecycle shipped keep working; everything NEW
-- enters via diff_and_enqueue as 'candidate' and must win its A/B).
CREATE TABLE IF NOT EXISTS recipe_status (
    symptom_id    TEXT NOT NULL,
    design_class  TEXT NOT NULL,
    platform      TEXT NOT NULL,
    strategy      TEXT NOT NULL,
    status        TEXT NOT NULL,        -- shadow | candidate | promoted
    provenance    TEXT,                 -- ab_trial:<id> | grandfathered:<date> | agent:<sid>
    generation    INTEGER,              -- generation that produced/changed it
    updated_at    TEXT,
    PRIMARY KEY (symptom_id, design_class, platform, strategy)
);
```

- [ ] **Step 4: Write `recipe_lifecycle.py`**

```python
#!/usr/bin/env python3
"""Recipe lifecycle: shadow -> candidate -> promoted (engineer-loop §5.3).

Only PROMOTED recipes affect live ranking. New/changed recipes from a learn
rebuild become 'candidate' and are enqueued for inline A/B (ab_runner). An A/B
win promotes; loss/inconclusive reverts to shadow. Agent-authored strategies
enter as shadow — NO special trust (decision 7). Absent row = promoted
(grandfathering bootstrap for pre-lifecycle learned recipes).
"""
from __future__ import annotations

import datetime as _dt
import json

GRANDFATHERED = "promoted"   # absent-row default


def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _iter_keys(heur: dict):
    for sid, classes in (heur.get("recipes") or {}).items():
        for dclass, plats in classes.items():
            if dclass == "*":
                continue           # rollups are views, not lifecycle keys
            for plat, node in plats.items():
                if plat == "*":
                    continue
                for strat, stats in (node.get("strategies") or {}).items():
                    yield (sid, dclass, plat, strat), stats


def diff_and_enqueue(conn, heur: dict, *, prev: dict | None) -> list[tuple]:
    """Compare current vs previous heuristics recipes; mark new/changed
    strategy entries 'candidate' (unless already candidate/promoted/shadow from
    an earlier identical diff). Returns the enqueued keys."""
    prev_stats = dict(_iter_keys(prev or {}))
    enqueued: list[tuple] = []
    for key, stats in _iter_keys(heur):
        if prev_stats.get(key) == stats:
            continue
        row = conn.execute(
            "SELECT status FROM recipe_status WHERE symptom_id=? AND "
            "design_class=? AND platform=? AND strategy=?", key).fetchone()
        if row is not None:
            continue               # already in lifecycle — A/B verdict owns it
        conn.execute(
            "INSERT INTO recipe_status (symptom_id, design_class, platform, "
            "strategy, status, provenance, generation, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (*key, "candidate", "learner_diff", heur.get("generation"), _now()))
        enqueued.append(key)
    conn.commit()
    return enqueued


def get_status(conn, *, symptom_id, design_class, platform, strategy) -> str:
    row = conn.execute(
        "SELECT status FROM recipe_status WHERE symptom_id=? AND design_class=?"
        " AND platform=? AND strategy=?",
        (symptom_id, design_class, platform, strategy)).fetchone()
    return row[0] if row else GRANDFATHERED


def _set(conn, status, provenance, *, symptom_id, design_class, platform,
         strategy) -> None:
    conn.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy,"
        " status, provenance, updated_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(symptom_id, design_class, platform, strategy) DO UPDATE SET"
        " status=excluded.status, provenance=excluded.provenance,"
        " updated_at=excluded.updated_at",
        (symptom_id, design_class, platform, strategy, status, provenance, _now()))
    conn.commit()


def promote(conn, *, evidence: str, **key) -> None:
    _set(conn, "promoted", evidence, **key)


def demote(conn, *, reason: str, **key) -> None:
    _set(conn, "shadow", reason, **key)


def stage_shadow(conn, *, provenance: str, **key) -> None:
    """Agent-authored strategy entry point (decision 7): outside the live pool
    until its A/B win."""
    _set(conn, "shadow", provenance, **key)


def filter_promoted(conn, recipe_entry: dict | None, *, symptom_id: str,
                    design_class: str, platform: str) -> dict | None:
    """Strip non-promoted strategies from a recipe entry before live ranking."""
    if not recipe_entry:
        return recipe_entry
    kept = {s: v for s, v in (recipe_entry.get("strategies") or {}).items()
            if get_status(conn, symptom_id=symptom_id, design_class=design_class,
                          platform=platform, strategy=s) == "promoted"}
    out = dict(recipe_entry)
    out["strategies"] = kept
    return out


def pending_candidates(conn) -> list[dict]:
    """All candidate rows awaiting an A/B trial (ab_runner's work queue)."""
    cur = conn.execute(
        "SELECT symptom_id, design_class, platform, strategy FROM recipe_status"
        " WHERE status='candidate' ORDER BY updated_at")
    return [dict(zip(("symptom_id", "design_class", "platform", "strategy"), r))
            for r in cur.fetchall()]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_recipe_lifecycle.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add r2g-rtl2gds/knowledge/recipe_lifecycle.py r2g-rtl2gds/knowledge/schema.sql r2g-rtl2gds/tests/test_recipe_lifecycle.py
git commit -m "feat(skill): recipe lifecycle shadow->candidate->promoted with grandfathering + promoted-only filter"
```

---

### Task 8: Confidence floor in `fix_model.py` + decision-8 relaxation lookup in diagnose

**Files:**
- Modify: `r2g-rtl2gds/scripts/reports/fix_model.py` (`rank_strategies`)
- Modify: `r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py` (new `load_indexed_recipe`, wire into `main`)
- Test: `r2g-rtl2gds/tests/test_confidence_floor.py`

- [ ] **Step 1: Write the failing test**

```python
"""Confidence floor (spec §5.7.2) + decision-8 relaxation order lookup."""
import json

import fix_model
import diagnose_signoff_fix as dsf


def test_pooled_only_strategy_cannot_outrank_local_winner_below_floor():
    local = {"strategies": {"local_win": {"attempts": 2, "successes": 2}},
             "n_sessions": 2}
    pooled = {"pooled_hot": {"attempts": 3, "successes": 3}}   # n=3 < floor 5
    ranked = fix_model.rank_strategies(local, ["local_win", "pooled_hot"],
                                       pooled=pooled, pooled_min_attempts=5)
    assert ranked[0]["strategy"] == "local_win"


def test_pooled_strategy_with_enough_attempts_may_outrank():
    local = {"strategies": {"local_win": {"attempts": 2, "successes": 1}},
             "n_sessions": 2}
    pooled = {"pooled_hot": {"attempts": 9, "successes": 9}}   # n=9 >= 5
    ranked = fix_model.rank_strategies(local, ["local_win", "pooled_hot"],
                                       pooled=pooled, pooled_min_attempts=5)
    assert ranked[0]["strategy"] == "pooled_hot"


def test_default_pooled_min_attempts_is_5():
    assert fix_model.POOLED_MIN_ATTEMPTS == 5


def _heur(tmp_path):
    sid = dsf.symptom.symptom_id(
        dsf.symptom.canonical_signature("drc", "antenna", None))
    data = {"generation": 1, "recipes": {sid: {
        "crypto/small": {"nangate45": {
            "strategies": {"antenna_diode_repair": {"attempts": 2, "successes": 2,
                                                    "failures": 0, "wins": 0}},
            "n_sessions": 2},
            "*": {"strategies": {"antenna_diode_repair": {"attempts": 4,
                  "successes": 3, "failures": 1, "wins": 0}}, "n_sessions": 4}},
        "*": {"*": {"strategies": {"antenna_diode_repair": {"attempts": 9,
              "successes": 7, "failures": 2, "wins": 0}}, "n_sessions": 9}}}}}
    hp = tmp_path / "heuristics.json"
    hp.write_text(json.dumps(data))
    return hp, sid


def test_indexed_lookup_exact_key_first(tmp_path):
    hp, _ = _heur(tmp_path)
    recipe, pooled, level = dsf.load_indexed_recipe(
        check="drc", platform="nangate45", design_class="crypto/small",
        drc={"status": "fail", "categories": {"antenna": {"count": 3}}},
        lvs={}, heuristics=hp)
    assert level == "exact"
    assert recipe["strategies"]["antenna_diode_repair"]["attempts"] == 2
    # pooled prior comes from the global rollup
    assert pooled["antenna_diode_repair"]["attempts"] == 9


def test_indexed_lookup_relaxes_class_then_platform(tmp_path):
    hp, _ = _heur(tmp_path)
    # unseen design_class -> falls to platform-pooled slice under '*' class? No:
    # relaxation order is exact -> pooled class ('*' under same class? NO —
    # '*' DESIGN CLASS, same platform) -> pooled platform ('*','*').
    recipe, _, level = dsf.load_indexed_recipe(
        check="drc", platform="nangate45", design_class="bus_heavy/large",
        drc={"status": "fail", "categories": {"antenna": {"count": 3}}},
        lvs={}, heuristics=hp)
    assert level == "pooled_platform"   # '*' class for nangate45 absent -> global
    assert recipe["strategies"]["antenna_diode_repair"]["attempts"] == 9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_confidence_floor.py -v`
Expected: FAIL (`TypeError: rank_strategies() got an unexpected keyword argument 'pooled_min_attempts'`)

- [ ] **Step 3: Implement the floor in `fix_model.py`**

Add module constant and extend the signature (keep full backward compat — default applies the floor only to the pooled branch):

```python
POOLED_MIN_ATTEMPTS = 5   # confidence floor (engineer-loop spec §5.7.2)


def rank_strategies(recipe_entry: dict | None, static_order: list[str],
                    pooled: dict | None = None,
                    pooled_min_attempts: int = POOLED_MIN_ATTEMPTS) -> list[dict]:
```

In the `elif sid in pooled:` branch, after computing `score`, apply the floor: if any LOCAL strategy has ≥1 success and this pooled strategy's `attempts < pooled_min_attempts`, cap its score just below the best local score:

```python
        elif sid in pooled:
            ps = pooled[sid]
            attempts = int(ps.get("attempts", 0))
            successes = int(ps.get("successes", 0))
            wins = int(ps.get("wins", 0))
            failures = int(ps.get("failures", max(0, attempts - successes)))
            score = _score(successes, attempts, wins)
            # Confidence floor (engineer-loop §5.7.2): a pooled-only strategy
            # below the attempt floor must not outrank a locally PROVEN one.
            local_proven = [
                _score(int(v.get("successes", 0)), int(v.get("attempts", 0)),
                       int(v.get("wins", 0)))
                for v in stats.values() if int(v.get("successes", 0)) >= 1]
            if attempts < pooled_min_attempts and local_proven:
                score = min(score, max(local_proven) - 1e-6)
            prov = f"prior(pooled,tried={attempts})"
```

- [ ] **Step 4: Add `load_indexed_recipe` to `diagnose_signoff_fix.py`**

Add after `load_symptom_recipe` (same file-layout conventions; `design_class` parameter is computed by the caller):

```python
def load_indexed_recipe(*, check: str, platform: str, design_class: str,
                        drc: dict, lvs: dict, heuristics: Path | None = None):
    """Decision-8 lookup with relaxation: recipes[sid][design_class][platform]
    -> recipes[sid]['*'][platform] (pooled class) -> recipes[sid]['*']['*']
    (pooled platform). Returns (recipe_entry|None, pooled_prior, match_level).
    pooled_prior is always the global rollup (recipes[sid]['*']['*'])."""
    hp = heuristics or (Path(__file__).resolve().parents[2]
                        / "knowledge" / "heuristics.json")
    if not hp.exists():
        return None, {}, "none"
    data = json.loads(hp.read_text(encoding="utf-8"))
    recipes = data.get("recipes") or {}
    if check == "drc":
        cats = drc.get("categories") or {}
        vclass = max(cats, key=lambda k: cats[k].get("count") or 0) if cats else None
        report = drc
    else:
        vclass, report = lvs.get("mismatch_class"), lvs
    sig = symptom.canonical_signature(check, vclass,
                                      symptom.predicates_for(check, report))
    bucket = recipes.get(symptom.symptom_id(sig)) or {}
    glob = (bucket.get("*") or {}).get("*") or {}
    pooled = {s: {k: v.get(k, 0) for k in ("attempts", "successes",
                                           "wins", "failures")}
              for s, v in (glob.get("strategies") or {}).items()}
    for dclass, plat, level in ((design_class, platform, "exact"),
                                ("*", platform, "pooled_class"),
                                ("*", "*", "pooled_platform")):
        node = (bucket.get(dclass) or {}).get(plat)
        if node and node.get("strategies"):
            return node, pooled, level
    return None, pooled, "none"
```

Compute `design_class` in `main()` (mirror ingest: `_design_type`/`_size_class` are ingest-side; here derive from project the same way `suggest_config` does — import is already available via the knowledge path inserted for `symptom`):

```python
    # Decision-8 indexed lookup first; legacy symptom/family lookups as fallback.
    try:
        import suggest_config as _sc
        _stats = _sc.parse_synth_stats(proj / "synth")
        _cells = _stats.get("cell_count", 0)
        _size = ("unknown" if not _cells else "tiny" if _cells < 100 else
                 "small" if _cells < 5000 else "medium" if _cells < 50000
                 else "large")
        design_class = f"{_sc.detect_design_type(proj, cfg)}/{_size}"
    except Exception:
        design_class = "unknown/unknown"
    idx_recipe, idx_pooled, idx_level = load_indexed_recipe(
        check=args.check, platform=plat, design_class=design_class,
        drc=drc, lvs=lvs)
    if idx_recipe is not None:
        recipes, pooled = idx_recipe, idx_pooled
    else:
        sym_recipe, pooled = load_symptom_recipe(...)   # existing lines unchanged
        recipes = sym_recipe if sym_recipe is not None else _load_recipes(...)
```

Also wire `recipe_lifecycle.filter_promoted` here (promoted-only live ranking, Task 7): after the recipe is resolved and BEFORE `build_plan`, when the indexed path matched:

```python
    if idx_recipe is not None:
        try:
            import knowledge_db
            import recipe_lifecycle
            _kc = knowledge_db.connect()
            knowledge_db.ensure_schema(_kc)
            recipes = recipe_lifecycle.filter_promoted(
                _kc, recipes, symptom_id=symptom.symptom_id(_cursig),
                design_class=design_class, platform=plat)
            _kc.close()
        except Exception:
            pass    # lifecycle filter must never break diagnosis
```

(`_cursig` = the signature computed for the lookup; factor `load_indexed_recipe`'s signature computation into a small helper `_current_signature(check, drc, lvs)` reused by both — implementer's choice, keep `_current_vclass` consistent.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_confidence_floor.py tests/test_fix_model.py tests/test_diagnose_ranking.py tests/test_diagnose_symptom_lookup.py tests/test_diagnose_signoff_fix.py -v`
Expected: new tests pass; ALL existing diagnose/fix_model tests still pass (floor defaults must not change rankings when no local success exists, which is the legacy situation those tests encode).

- [ ] **Step 6: Commit**

```bash
git add r2g-rtl2gds/scripts/reports/fix_model.py r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py r2g-rtl2gds/tests/test_confidence_floor.py
git commit -m "feat(skill): confidence floor + decision-8 indexed recipe lookup with relaxation + promoted-only filter"
```

---

### Task 9: Timing strategy catalog (`period_relax`, `utilization_reduce`)

**Files:**
- Modify: `r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py` (add `_timing_plan`, accept `--check timing`)
- Test: `r2g-rtl2gds/tests/test_diagnose_timing.py`

The timing journal already exists (`check_timing.py --journal`, verdicts already canonical). What's missing is a proposal catalog so timing recipes flow through ranking + lifecycle like DRC/LVS (spec §5.7.4). `period_relax` mirrors the proven iccad2015 recipe (3 attempts / 2 successes, 97.5% redux).

- [ ] **Step 1: Write the failing test**

```python
"""Timing fix catalog (spec §5.7.4): period_relax + utilization_reduce."""
import json

import diagnose_signoff_fix as dsf


def test_timing_severe_offers_period_relax_first():
    tcheck = {"tier": "severe", "wns_ns": -1.2, "clock_period_ns": 4.0}
    plan = dsf.build_plan({}, {}, {"PLATFORM": "nangate45",
                                   "CORE_UTILIZATION": "30"},
                          check="timing", tcheck=tcheck)
    ids = [s["id"] for s in plan["strategies"]]
    assert ids[0] == "period_relax"
    assert "utilization_reduce" in ids
    pr = plan["strategies"][0]
    # relaxed period = old period - WNS (slack-absorbing), rounded up 5%
    assert float(pr["sdc_edits"]["CLOCK_PERIOD"]) >= 5.2


def test_timing_clean_offers_nothing():
    plan = dsf.build_plan({}, {}, {"PLATFORM": "nangate45"},
                          check="timing", tcheck={"tier": "clean"})
    assert plan["strategies"] == []


def test_timing_minor_excludes_period_relax():
    # minor tier auto-fixes via existing flow; only utilization relief offered
    plan = dsf.build_plan({}, {}, {"PLATFORM": "nangate45",
                                   "CORE_UTILIZATION": "30"},
                          check="timing", tcheck={"tier": "minor",
                                                  "wns_ns": -0.02})
    ids = [s["id"] for s in plan["strategies"]]
    assert "period_relax" not in ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_diagnose_timing.py -v`
Expected: FAIL (`build_plan() got an unexpected keyword argument 'tcheck'`)

- [ ] **Step 3: Implement `_timing_plan` + extend `build_plan`**

```python
def _timing_plan(tcheck: dict, cfg: dict, exclude: set) -> dict:
    tier = tcheck.get("tier", "unknown")
    wns = tcheck.get("wns_ns")
    plan = {"check": "timing", "status": tier, "violation_count": None,
            "dominant_category": tier, "strategies": [], "residual_reason": None}
    if tier in ("clean", "unknown", None):
        return plan
    try:
        cur_util = int(float(cfg.get("CORE_UTILIZATION", "")))
    except (TypeError, ValueError):
        cur_util = 30
    strategies = []
    if tier in ("moderate", "severe") and wns is not None:
        period = tcheck.get("clock_period_ns")
        if period:
            # Absorb the negative slack then add 5% margin (proven iccad2015
            # period_relax recipe: 3 att / 2 succ, 97.5% WNS reduction).
            relaxed = round((float(period) - float(wns)) * 1.05, 3)
            strategies.append(
                {"id": "period_relax",
                 "rationale": f"Relax clock period {period} -> {relaxed} ns to "
                              "absorb WNS with 5% margin (validated recipe).",
                 "config_edits": {}, "sdc_edits": {"CLOCK_PERIOD": str(relaxed)},
                 "rerun_from": "synth", "recheck": "timing", "auto_apply": True})
    strategies.append(
        {"id": "utilization_reduce",
         "rationale": "Lower CORE_UTILIZATION to give placement/CTS slack "
                      "headroom (never touches PLACE_DENSITY_LB_ADDON).",
         "config_edits": {"CORE_UTILIZATION": str(max(5, cur_util - 5))},
         "sdc_edits": {}, "rerun_from": "floorplan", "recheck": "timing",
         "auto_apply": True})
    plan["strategies"] = [s for s in strategies if s["id"] not in exclude]
    return plan
```

Extend `build_plan` signature: `def build_plan(drc, lvs, cfg, *, check="drc", exclude=(), recipes=None, tcheck=None)` and route:

```python
    if check == "timing":
        plan = _timing_plan(tcheck or {}, cfg, excl)
    elif check == "drc":
        plan = _drc_plan(drc or {}, cfg, excl)
    else:
        plan = _lvs_plan(lvs or {}, cfg, excl)
```

In `main()`: add `"timing"` to `--check` choices; load `tcheck = _load(proj / "reports" / "timing_check.json")`; pass `tcheck=tcheck` to `build_plan`. `--apply` for a strategy with `sdc_edits` rewrites `CLOCK_PERIOD` via the SDC file the same way `scripts/reports/check_timing.py`'s journal flow expects — find the `create_clock -period <X>` line in `constraints/*.sdc` and substitute the value (reuse the regex approach in `check_timing.py::_journal`'s before/after handling; read that function first). Journal the edit as an `sdc_edit` action via `journal_action.py` (same guarded-call pattern as Task 4).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_diagnose_timing.py tests/test_diagnose_signoff_fix.py tests/test_check_timing_journal.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py r2g-rtl2gds/tests/test_diagnose_timing.py
git commit -m "feat(skill): timing fix catalog (period_relax/utilization_reduce) through the same ranking path"
```

---

### Task 10: `escalations` table + queue API

**Files:**
- Modify: `r2g-rtl2gds/knowledge/schema.sql` (new `escalations` table)
- Create: `r2g-rtl2gds/knowledge/escalations.py`
- Test: `r2g-rtl2gds/tests/test_escalations.py`

- [ ] **Step 1: Write the failing test**

```python
"""Escalation queue (spec §5.5): the loop records, the agent drains."""
import escalations
import knowledge_db


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "runs.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def test_open_and_list(tmp_path):
    conn = _conn(tmp_path)
    eid = escalations.open_escalation(
        conn, design="aes_x", project_path="/p/aes_x", run_id="r1",
        reason="catalog_exhausted", symptom_id="deadbeef00000001",
        notes="3 strategies tried, residual 4 violations")
    rows = escalations.list_open(conn)
    assert len(rows) == 1 and rows[0]["escalation_id"] == eid
    assert rows[0]["reason"] == "catalog_exhausted"


def test_duplicate_open_for_same_design_reason_is_noop(tmp_path):
    conn = _conn(tmp_path)
    escalations.open_escalation(conn, design="aes_x", project_path="/p/aes_x",
                                run_id="r1", reason="unknown_symptom")
    escalations.open_escalation(conn, design="aes_x", project_path="/p/aes_x",
                                run_id="r2", reason="unknown_symptom")
    assert len(escalations.list_open(conn)) == 1


def test_resolve_marks_drained(tmp_path):
    conn = _conn(tmp_path)
    eid = escalations.open_escalation(conn, design="aes_x",
                                      project_path="/p/aes_x", run_id="r1",
                                      reason="unseen_crash")
    escalations.resolve(conn, eid, status="drained",
                        notes="authored shadow strategy cts_skip_clone")
    assert escalations.list_open(conn) == []


def test_invalid_reason_rejected(tmp_path):
    conn = _conn(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        escalations.open_escalation(conn, design="x", project_path="/p/x",
                                    run_id="r", reason="bogus")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_escalations.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'escalations'`

- [ ] **Step 3: Add table to `schema.sql`**

```sql
-- Engineer-loop escalation queue (spec §5.5): problems the deterministic core
-- cannot handle; drained by the agent tier. The loop NEVER blocks on these.
CREATE TABLE IF NOT EXISTS escalations (
    escalation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    design        TEXT,
    project_path  TEXT,
    run_id        TEXT,
    symptom_id    TEXT,
    reason        TEXT NOT NULL,   -- unknown_symptom|catalog_exhausted|unseen_crash|repeated_regression
    status        TEXT NOT NULL DEFAULT 'open',   -- open | drained | wont_fix
    notes         TEXT,
    created_at    TEXT,
    resolved_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_escalations_status ON escalations(status);
```

- [ ] **Step 4: Write `escalations.py`**

```python
#!/usr/bin/env python3
"""Escalation queue API (engineer-loop spec §5.5). The loop opens; the agent
tier drains (see references/engineer-loop.md). Dedup: one OPEN escalation per
(design, reason) — repeats refresh nothing (the original already says it all).
"""
from __future__ import annotations

import datetime as _dt

REASONS = ("unknown_symptom", "catalog_exhausted", "unseen_crash",
           "repeated_regression")


def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def open_escalation(conn, *, design: str, project_path: str, run_id: str | None,
                    reason: str, symptom_id: str | None = None,
                    notes: str | None = None) -> int | None:
    if reason not in REASONS:
        raise ValueError(f"unknown escalation reason: {reason}")
    dup = conn.execute(
        "SELECT escalation_id FROM escalations WHERE design=? AND reason=? "
        "AND status='open'", (design, reason)).fetchone()
    if dup:
        return dup[0]
    cur = conn.execute(
        "INSERT INTO escalations (design, project_path, run_id, symptom_id, "
        "reason, status, notes, created_at) VALUES (?,?,?,?,?,'open',?,?)",
        (design, project_path, run_id, symptom_id, reason, notes, _now()))
    conn.commit()
    return cur.lastrowid


def list_open(conn) -> list[dict]:
    cur = conn.execute(
        "SELECT escalation_id, design, project_path, run_id, symptom_id, "
        "reason, notes, created_at FROM escalations WHERE status='open' "
        "ORDER BY created_at")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def resolve(conn, escalation_id: int, *, status: str, notes: str | None = None) -> None:
    assert status in ("drained", "wont_fix")
    conn.execute(
        "UPDATE escalations SET status=?, notes=COALESCE(?, notes), resolved_at=? "
        "WHERE escalation_id=?", (status, notes, _now(), escalation_id))
    conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_escalations.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add r2g-rtl2gds/knowledge/schema.sql r2g-rtl2gds/knowledge/escalations.py r2g-rtl2gds/tests/test_escalations.py
git commit -m "feat(skill): escalations queue (open/list/resolve, deduped per design+reason)"
```

---

### Task 11: `ab_runner.py` — inline recipe A/B trials + `ab_trials` table

**Files:**
- Modify: `r2g-rtl2gds/knowledge/schema.sql` (new `ab_trials` table)
- Create: `r2g-rtl2gds/knowledge/ab_runner.py`
- Test: `r2g-rtl2gds/tests/test_ab_runner.py`

ab_runner does NOT run flows itself — it *plans* trials (matched designs + arm definitions) and *judges* finished trials. The orchestrator (Task 12) executes arms as ordinary ledger entries. Verdict semantics reuse `knowledge_db.is_success` + wall-clock honesty (same as `eval_heuristics`).

- [ ] **Step 1: Write the failing test**

```python
"""Inline recipe A/B (spec §5.4): match, plan arms, judge honestly, promote."""
import json

import ab_runner
import knowledge_db
import recipe_lifecycle

KEY = dict(symptom_id="deadbeef00000001", design_class="crypto/small",
           platform="nangate45", strategy="antenna_diode_repair")


def _conn(tmp_path):
    c = knowledge_db.connect(tmp_path / "runs.sqlite")
    knowledge_db.ensure_schema(c)
    return c


def _seed_history(conn, n=3, design_class="crypto/small", platform="nangate45"):
    """run_violations rows whose symptom matches KEY, attached to small runs."""
    for i in range(n):
        rid = f"r{i}"
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, project_path, design_name, "
            "platform, ingested_at, cell_count, design_class) "
            "VALUES (?,?,?,?,?,?,?)",
            (rid, f"/p/d{i}", f"d{i}", platform, "2026-06-10T00:00:00Z",
             1000 + i, design_class))
        conn.execute(
            "INSERT OR REPLACE INTO run_violations (run_id, platform, "
            "drc_status, symptom_id, snapshot_ts) VALUES (?,?,?,?,?)",
            (rid, platform, "fail", KEY["symptom_id"], "2026-06-10T00:00:00Z"))
    conn.commit()


def test_plan_trial_selects_cheapest_matched_designs(tmp_path):
    conn = _conn(tmp_path)
    _seed_history(conn)
    trial = ab_runner.plan_trial(conn, **KEY, n_designs=2)
    assert [d["design_name"] for d in trial["designs"]] == ["d0", "d1"]
    assert trial["arm_a"]["exclude_strategy"] == KEY["strategy"]
    assert trial["arm_b"]["rank_first_strategy"] == KEY["strategy"]


def test_plan_trial_relaxes_class_when_exact_too_few(tmp_path):
    conn = _conn(tmp_path)
    _seed_history(conn, n=1, design_class="crypto/small")
    _seed_history_other = _seed_history(conn, n=2, design_class="logic/medium")
    trial = ab_runner.plan_trial(conn, **KEY, n_designs=2)
    assert trial["match_level"] == "pooled_class"
    assert len(trial["designs"]) == 2


def test_judge_win_promotes(tmp_path):
    conn = _conn(tmp_path)
    recipe_lifecycle.stage_shadow(conn, provenance="test", **KEY)
    arm_a = {"is_success": False, "wall_s": 900.0, "fix_iters": None}
    arm_b = {"is_success": True, "wall_s": 600.0, "fix_iters": 2}
    verdict = ab_runner.judge(arm_a, arm_b)
    assert verdict == "win"
    tid = ab_runner.record_trial(conn, key=KEY, verdict=verdict,
                                 arm_a_run_id="ra", arm_b_run_id="rb",
                                 metrics={"a": arm_a, "b": arm_b})
    assert recipe_lifecycle.get_status(conn, **KEY) == "promoted"
    row = conn.execute("SELECT verdict FROM ab_trials WHERE trial_id=?",
                       (tid,)).fetchone()
    assert row[0] == "win"


def test_judge_both_fail_is_inconclusive_never_win(tmp_path):
    arm_a = {"is_success": False, "wall_s": 900.0, "fix_iters": None}
    arm_b = {"is_success": False, "wall_s": 100.0, "fix_iters": None}
    assert ab_runner.judge(arm_a, arm_b) == "inconclusive"


def test_judge_crash_arm_is_inconclusive(tmp_path):
    assert ab_runner.judge(None, {"is_success": True, "wall_s": 1.0,
                                  "fix_iters": 0}) == "inconclusive"


def test_loss_reverts_candidate_to_shadow(tmp_path):
    conn = _conn(tmp_path)
    recipe_lifecycle.stage_shadow(conn, provenance="test", **KEY)
    ab_runner.record_trial(conn, key=KEY, verdict="loss", arm_a_run_id="ra",
                           arm_b_run_id="rb", metrics={})
    assert recipe_lifecycle.get_status(conn, **KEY) == "shadow"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_ab_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ab_runner'`

- [ ] **Step 3: Add `ab_trials` to `schema.sql`**

```sql
-- Engineer-loop inline recipe A/B (spec §5.4). One row per finished trial.
-- A 'win' (arm B usable signed-off AND cheaper/cleaner) promotes the recipe;
-- loss/inconclusive reverts to shadow. Crash arms are inconclusive, never wins.
CREATE TABLE IF NOT EXISTS ab_trials (
    trial_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    symptom_id    TEXT NOT NULL,
    design_class  TEXT NOT NULL,
    platform      TEXT NOT NULL,
    strategy      TEXT NOT NULL,
    arm_a_run_id  TEXT,
    arm_b_run_id  TEXT,
    verdict       TEXT,             -- win | loss | inconclusive
    metrics_json  TEXT,
    match_level   TEXT,             -- exact | pooled_class | pooled_platform
    ts            TEXT
);
CREATE INDEX IF NOT EXISTS idx_ab_trials_key
    ON ab_trials(symptom_id, design_class, platform, strategy);
```

- [ ] **Step 4: Write `ab_runner.py`**

```python
#!/usr/bin/env python3
"""Inline recipe A/B planner + judge (engineer-loop spec §5.4).

plan_trial(): pick matched designs from run_violations history (same symptom,
decision-8 relaxation, CHEAPEST first — Phase-0 small-design-first), and define
the two arms. The ORCHESTRATOR executes arms as ordinary ledger entries with
distinct FLOW_VARIANT project dirs; this module never runs flows.

judge(): honest verdict — arm B must be a USABLE signed-off result AND better
(cheaper wall-clock, or equal-cost with fewer fix iters). Both-fail or crashed
arm -> inconclusive, NEVER a win (inherits eval_heuristics invariant 11).
"""
from __future__ import annotations

import datetime as _dt
import json

import recipe_lifecycle

N_DESIGNS_DEFAULT = 2     # min matched designs per trial (spec §5.4)


def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def plan_trial(conn, *, symptom_id: str, design_class: str, platform: str,
               strategy: str, n_designs: int = N_DESIGNS_DEFAULT) -> dict | None:
    """Returns {designs, arm_a, arm_b, match_level} or None if no match."""
    def _q(extra_sql: str, params: tuple) -> list[dict]:
        cur = conn.execute(
            "SELECT r.design_name, r.project_path, r.cell_count "
            "FROM run_violations v JOIN runs r USING(run_id) "
            f"WHERE v.symptom_id=? {extra_sql} "
            "GROUP BY r.design_name ORDER BY MIN(r.cell_count)",
            (symptom_id, *params))
        return [dict(zip(("design_name", "project_path", "cell_count"), x))
                for x in cur.fetchall()]

    for extra, params, level in (
            ("AND r.design_class=? AND r.platform=?", (design_class, platform),
             "exact"),
            ("AND r.platform=?", (platform,), "pooled_class"),
            ("", (), "pooled_platform")):
        designs = _q(extra, params)
        if len(designs) >= n_designs:
            return {
                "designs": designs[:n_designs],
                "match_level": level,
                "arm_a": {"exclude_strategy": strategy},
                "arm_b": {"rank_first_strategy": strategy},
                "key": {"symptom_id": symptom_id, "design_class": design_class,
                        "platform": platform, "strategy": strategy},
            }
    return None


def judge(arm_a: dict | None, arm_b: dict | None) -> str:
    """arm dicts: {is_success: bool, wall_s: float|None, fix_iters: int|None}.
    None = the arm crashed / produced no judgeable result."""
    if arm_a is None or arm_b is None:
        return "inconclusive"
    if not arm_b.get("is_success"):
        return "inconclusive" if not arm_a.get("is_success") else "loss"
    if not arm_a.get("is_success"):
        return "win"                      # B usable where A was not
    wa, wb = arm_a.get("wall_s"), arm_b.get("wall_s")
    if wa is not None and wb is not None and wb < wa * 0.98:
        return "win"
    ia, ib = arm_a.get("fix_iters"), arm_b.get("fix_iters")
    if ia is not None and ib is not None and ib < ia:
        return "win"
    if wa is not None and wb is not None and wb > wa * 1.02:
        return "loss"
    return "inconclusive"


def record_trial(conn, *, key: dict, verdict: str, arm_a_run_id: str | None,
                 arm_b_run_id: str | None, metrics: dict,
                 match_level: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO ab_trials (symptom_id, design_class, platform, strategy, "
        "arm_a_run_id, arm_b_run_id, verdict, metrics_json, match_level, ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (key["symptom_id"], key["design_class"], key["platform"],
         key["strategy"], arm_a_run_id, arm_b_run_id, verdict,
         json.dumps(metrics, sort_keys=True), match_level, _now()))
    conn.commit()
    tid = cur.lastrowid
    if verdict == "win":
        recipe_lifecycle.promote(conn, evidence=f"ab_trial:{tid}", **key)
    else:
        recipe_lifecycle.demote(conn, reason=f"ab_{verdict}:{tid}", **key)
    return tid


def auto_demote_on_regression(conn, *, key: dict, window: int = 2) -> bool:
    """Spec §7: a PROMOTED recipe with `window` consecutive live regressions on
    its symptom is auto-demoted + escalated. Counts recent fix_events for this
    strategy+symptom; returns True if demoted."""
    rows = conn.execute(
        "SELECT verdict FROM fix_events WHERE symptom_id=? AND strategy=? "
        "ORDER BY fix_event_id DESC LIMIT ?",
        (key["symptom_id"], key["strategy"], window)).fetchall()
    if len(rows) == window and all(r[0] == "regression" for r in rows):
        recipe_lifecycle.demote(conn, reason="repeated_regression", **key)
        import escalations
        escalations.open_escalation(
            conn, design=f"recipe:{key['strategy']}", project_path="",
            run_id=None, reason="repeated_regression",
            symptom_id=key["symptom_id"],
            notes=json.dumps(key, sort_keys=True))
        return True
    return False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_ab_runner.py -v`
Expected: 6 passed (also add a test for `auto_demote_on_regression` if time allows: seed 2 regression fix_events, assert demoted + escalation opened).

- [ ] **Step 6: Commit**

```bash
git add r2g-rtl2gds/knowledge/schema.sql r2g-rtl2gds/knowledge/ab_runner.py r2g-rtl2gds/tests/test_ab_runner.py
git commit -m "feat(skill): ab_runner — matched-design trial planning, honest judge, promote/demote + auto-demotion"
```

---

### Task 12: Arm plumbing — `--rank-first` in diagnose + env passthrough in fix_signoff

**Files:**
- Modify: `r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py` (new `--rank-first` flag)
- Modify: `r2g-rtl2gds/scripts/flow/fix_signoff.sh` (honor `R2G_FIX_EXCLUDE` / `R2G_FIX_RANK_FIRST`)
- Test: `r2g-rtl2gds/tests/test_arm_plumbing.py`

A/B arms need a way to run the SAME fix loop with one strategy excluded (arm A) or forced to rank first (arm B), without changing the loop's code path.

- [ ] **Step 1: Write the failing test**

```python
"""A/B arm plumbing: --rank-first + R2G_FIX_EXCLUDE/R2G_FIX_RANK_FIRST."""
import json
from pathlib import Path

import diagnose_signoff_fix as dsf


def _proj(tmp_path):
    p = tmp_path / "proj"
    (p / "constraints").mkdir(parents=True)
    (p / "reports").mkdir()
    (p / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = x\nexport PLATFORM = sky130hd\n"
        "export CORE_UTILIZATION = 30\n")
    (p / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 5,
         "categories": {"M3_ANTENNA": {"count": 5}}}))
    return p


def test_rank_first_reorders_plan(tmp_path, capsys):
    proj = _proj(tmp_path)
    # default order on sky130hd: antenna_diode_iters then antenna_density_relief
    rc = dsf.main([str(proj), "--check", "drc", "--list",
                   "--rank-first", "antenna_density_relief"])
    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["strategies"][0]["id"] == "antenna_density_relief"


def test_rank_first_unknown_id_is_harmless(tmp_path, capsys):
    proj = _proj(tmp_path)
    rc = dsf.main([str(proj), "--check", "drc", "--list",
                   "--rank-first", "no_such_strategy"])
    assert rc == 0   # plan unchanged, no crash
```

Shell side (same `R2G_SOURCE_ONLY` seam as Task 4): assert `fix_one`'s diagnose call includes the env exclusions —

```python
def test_fix_signoff_env_passthrough_appears_in_diagnose_args(tmp_path):
    import os
    import subprocess
    SKILL = Path(__file__).resolve().parents[1]
    fake = tmp_path / "fake_diagnose.py"
    fake.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        "open(sys.argv[0] + '.args', 'a').write(' '.join(sys.argv[1:]) + chr(10))\n"
        "print('STOP\\tresidual\\ttest')\n")
    fake.chmod(0o755)
    proj = _proj(tmp_path)
    env = dict(os.environ, R2G_DIAGNOSE=str(fake),
               R2G_FIX_EXCLUDE="abandoned_strategy",
               R2G_FIX_RANK_FIRST="hot_strategy", R2G_JOURNAL="0")
    subprocess.run(["bash", str(SKILL / "scripts/flow/fix_signoff.sh"),
                    str(proj), "sky130hd", "--check", "drc"],
                   capture_output=True, text=True, env=env)
    args = (fake.parent / (fake.name + ".args")).read_text()
    assert "--exclude abandoned_strategy" in args.replace("  ", " ")
    assert "--rank-first hot_strategy" in args
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_arm_plumbing.py -v`
Expected: FAIL (`unrecognized arguments: --rank-first`)

- [ ] **Step 3: Implement `--rank-first` in diagnose**

In `main()` argparse: `ap.add_argument("--rank-first", default=None, help="force this strategy id to the head of the ranked plan (A/B arm B)")`. After `_rank_plan_strategies(plan, recipes, pooled=pooled)`:

```python
    if args.rank_first:
        head = [s for s in plan["strategies"] if s["id"] == args.rank_first]
        rest = [s for s in plan["strategies"] if s["id"] != args.rank_first]
        plan["strategies"] = head + rest
```

- [ ] **Step 4: Implement env passthrough in `fix_signoff.sh`**

In `fix_one`, change the diagnose `--next` invocation line to merge env into the per-run exclusions and append the rank-first flag when set:

```bash
    local all_excl="${tried}${R2G_FIX_EXCLUDE:+${tried:+,}$R2G_FIX_EXCLUDE}"
    line="$("$DIAGNOSE" "$PROJECT_DIR" --check "$check" --exclude "$all_excl" \
            ${R2G_FIX_RANK_FIRST:+--rank-first "$R2G_FIX_RANK_FIRST"} --next)"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_arm_plumbing.py tests/test_fix_signoff_adaptive.py tests/test_fix_signoff_logging.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py r2g-rtl2gds/scripts/flow/fix_signoff.sh r2g-rtl2gds/tests/test_arm_plumbing.py
git commit -m "feat(skill): A/B arm plumbing — diagnose --rank-first + fix_signoff env exclude/rank-first"
```

---

### Task 13: `engineer_loop.py` — campaign orchestrator

**Files:**
- Create: `r2g-rtl2gds/scripts/loop/engineer_loop.py`
- Test: `r2g-rtl2gds/tests/test_engineer_loop.py`
- Modify: `r2g-rtl2gds/tests/conftest.py` (add `scripts/loop/` to sys.path, same pattern as the existing blocks)

Design: a SINGLE-process sequential driver in Phase 1 (workers=1 default; the host's parallelism is reclaimed later — correctness first). Every step is a subprocess call to an existing script, overridable via env seams for tests (`R2G_LOOP_RUN_FLOW`, `R2G_LOOP_FIX`, `R2G_LOOP_INGEST` — mirroring fix_signoff's `R2G_RUN_ORFS` convention). The ledger is a JSONL file: one line per state transition, last state wins (resume = replay).

- [ ] **Step 1: Write the failing test**

```python
"""Engineer-loop orchestrator: ledger state machine + one full turn (spec §5.1/§6)."""
import json
import os
from pathlib import Path

import engineer_loop


def _entry(name="d0", kind="normal"):
    return {"design": name, "project_path": f"/p/{name}",
            "platform": "nangate45", "kind": kind}


def test_ledger_roundtrip_and_resume(tmp_path):
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    led.add(_entry("d0"))
    led.add(_entry("d1"))
    led.set_state("d0", "clean")
    led2 = engineer_loop.Ledger(tmp_path / "ledger.jsonl")   # re-open = resume
    assert led2.state("d0") == "clean"
    assert led2.state("d1") == "pending"
    assert [e["design"] for e in led2.pending()] == ["d1"]


def test_state_transitions_are_legal_only(tmp_path):
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    led.add(_entry("d0"))
    import pytest
    with pytest.raises(ValueError):
        led.set_state("d0", "bogus_state")


def test_process_one_clean_path(tmp_path, monkeypatch):
    """Flow pass + clean signoff -> state clean; ingest called once."""
    calls = []
    monkeypatch.setattr(engineer_loop, "_run_flow",
                        lambda e: calls.append(("flow", e["design"])) or 0)
    monkeypatch.setattr(engineer_loop, "_signoff_status",
                        lambda e: {"drc": "clean", "lvs": "clean"})
    monkeypatch.setattr(engineer_loop, "_ingest",
                        lambda e: calls.append(("ingest", e["design"])) or "rid")
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    led.add(_entry("d0"))
    engineer_loop.process_one(led, led.pending()[0], conn=None)
    assert led.state("d0") == "clean"
    assert ("flow", "d0") in calls and ("ingest", "d0") in calls


def test_process_one_fix_path_then_escalate(tmp_path, monkeypatch):
    """Violations + fix loop fails to clear -> escalated, loop continues."""
    import knowledge_db
    conn = knowledge_db.connect(tmp_path / "runs.sqlite")
    knowledge_db.ensure_schema(conn)
    monkeypatch.setattr(engineer_loop, "_run_flow", lambda e: 0)
    monkeypatch.setattr(engineer_loop, "_signoff_status",
                        lambda e: {"drc": "fail", "lvs": "clean"})
    monkeypatch.setattr(engineer_loop, "_run_fix", lambda e: 2)   # residual
    monkeypatch.setattr(engineer_loop, "_ingest", lambda e: "rid")
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    led.add(_entry("d0"))
    engineer_loop.process_one(led, led.pending()[0], conn=conn)
    assert led.state("d0") == "escalated"
    import escalations
    assert escalations.list_open(conn)[0]["reason"] == "catalog_exhausted"


def test_learn_cycle_enqueues_candidates_and_ab_arms(tmp_path, monkeypatch):
    """After ingest, learn -> recipe diff -> A/B arms appended to the ledger."""
    import knowledge_db
    conn = knowledge_db.connect(tmp_path / "runs.sqlite")
    knowledge_db.ensure_schema(conn)
    conn.execute("INSERT OR REPLACE INTO runs (run_id, project_path, design_name,"
                 " platform, ingested_at, cell_count, design_class) "
                 "VALUES ('r0','/p/d0','d0','nangate45','t',900,'crypto/small')")
    conn.execute("INSERT OR REPLACE INTO run_violations (run_id, platform,"
                 " drc_status, symptom_id, snapshot_ts) "
                 "VALUES ('r0','nangate45','fail','deadbeef00000001','t')")
    conn.commit()
    heur_new = {"generation": 2, "recipes": {"deadbeef00000001": {
        "crypto/small": {"nangate45": {"strategies": {"s_new": {
            "attempts": 1, "successes": 1, "failures": 0, "wins": 0}},
            "n_sessions": 1}}}}}
    monkeypatch.setattr(engineer_loop, "_learn", lambda: heur_new)
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    engineer_loop.learn_cycle(led, conn, prev_heur={"generation": 1,
                                                    "recipes": {}},
                              n_ab_designs=1)
    arms = [e for e in led.entries() if e["kind"] == "ab_arm"]
    assert len(arms) == 2       # arm A + arm B for the one matched design
    assert {a["arm"] for a in arms} == {"A", "B"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_engineer_loop.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engineer_loop'` (also add the conftest block first: copy the `FLOW_DIR_SCRIPTS` stanza, pointing at `scripts/loop`).

- [ ] **Step 3: Write `engineer_loop.py`**

```python
#!/usr/bin/env python3
"""Engineer-loop campaign orchestrator (spec §5.1, §6). Deterministic core:
pull design -> flow -> signoff -> fix -> ingest -> learn -> recipe diff ->
A/B arms (as ordinary ledger entries) -> verdict -> promote/demote. Unknowns
go to the escalations queue; the loop NEVER blocks on them.

Usage:
  engineer_loop.py run --ledger design_cases/_loop/ledger.jsonl [--max N]
  engineer_loop.py add --ledger L --project <dir> [--platform nangate45]
  engineer_loop.py status --ledger L

Hard rules honored: unique FLOW_VARIANT per project dir (run_orfs derives it
from the basename — A/B arms copy to <design>_ab{A,B}_<strategy8> dirs);
single LVS at a time (workers=1 in Phase 1); PLACE_DENSITY clamps live in
diagnose/suggest and are never touched here.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = SKILL_ROOT / "knowledge"
FLOW = SKILL_ROOT / "scripts" / "flow"
sys.path.insert(0, str(KNOWLEDGE))

STATES = ("pending", "flow", "signoff", "fixing", "clean", "escalated",
          "abandoned")


def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


class Ledger:
    """JSONL event log; last state per design wins. Append-only -> resumable."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, dict] = {}
        if self.path.exists():
            for ln in self.path.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                e = json.loads(ln)
                cur = self._entries.setdefault(e["design"], e)
                cur.update(e)

    def _append(self, obj: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, sort_keys=True) + "\n")

    def add(self, entry: dict) -> None:
        e = dict(entry)
        e.setdefault("kind", "normal")
        e.setdefault("state", "pending")
        e["ts"] = _now()
        self._entries[e["design"]] = dict(self._entries.get(e["design"], {}), **e)
        self._append(e)

    def set_state(self, design: str, state: str, **extra) -> None:
        if state not in STATES:
            raise ValueError(f"illegal state: {state}")
        e = {"design": design, "state": state, "ts": _now(), **extra}
        self._entries[design].update(e)
        self._append(e)

    def state(self, design: str) -> str:
        return self._entries[design]["state"]

    def entries(self) -> list[dict]:
        return list(self._entries.values())

    def pending(self) -> list[dict]:
        return [e for e in self._entries.values() if e["state"] == "pending"]


# ---- subprocess seams (monkeypatched in tests; env-overridable like
# fix_signoff's R2G_RUN_ORFS) -------------------------------------------------

def _script(env_key: str, default: Path) -> str:
    return os.environ.get(env_key, str(default))


def _run_flow(entry: dict) -> int:
    return subprocess.run(
        ["bash", _script("R2G_LOOP_RUN_FLOW", FLOW / "run_orfs.sh"),
         entry["project_path"], entry["platform"]]).returncode


def _run_fix(entry: dict) -> int:
    env = dict(os.environ)
    if entry.get("kind") == "ab_arm":
        if entry.get("arm") == "A":
            env["R2G_FIX_EXCLUDE"] = entry["strategy"]
        else:
            env["R2G_FIX_RANK_FIRST"] = entry["strategy"]
    return subprocess.run(
        ["bash", _script("R2G_LOOP_FIX", FLOW / "fix_signoff.sh"),
         entry["project_path"], entry["platform"], "--check", "both"],
        env=env).returncode


def _ingest(entry: dict) -> str | None:
    r = subprocess.run(
        [sys.executable, _script("R2G_LOOP_INGEST", KNOWLEDGE / "ingest_run.py"),
         entry["project_path"]], capture_output=True, text=True)
    for tok in (r.stdout or "").split():
        if tok.startswith("run_id="):
            return tok.split("=", 1)[1]
    return None


def _signoff_status(entry: dict) -> dict:
    out = {}
    for check in ("drc", "lvs"):
        p = Path(entry["project_path"]) / "reports" / f"{check}.json"
        try:
            out[check] = json.loads(p.read_text()).get("status", "unknown")
        except Exception:
            out[check] = "unknown"
    return out


def _learn() -> dict:
    import learn_heuristics
    import knowledge_db
    return learn_heuristics.learn(knowledge_db.DEFAULT_DB_PATH,
                                  KNOWLEDGE / "heuristics.json")


# ---- the loop ---------------------------------------------------------------

def process_one(led: Ledger, entry: dict, conn) -> None:
    design = entry["design"]
    led.set_state(design, "flow")
    rc = _run_flow(entry)
    if rc != 0:
        led.set_state(design, "escalated", reason="unseen_crash")
        if conn is not None:
            import escalations
            escalations.open_escalation(
                conn, design=design, project_path=entry["project_path"],
                run_id=None, reason="unseen_crash",
                notes=f"run_orfs rc={rc}")
        _ingest(entry)                      # partial runs still teach
        return
    led.set_state(design, "signoff")
    status = _signoff_status(entry)
    if all(v in ("clean", "clean_beol", "skipped") for v in status.values()):
        _ingest(entry)
        led.set_state(design, "clean")
        return
    led.set_state(design, "fixing")
    fix_rc = _run_fix(entry)
    _ingest(entry)
    if fix_rc == 0:
        led.set_state(design, "clean")
    else:
        led.set_state(design, "escalated", reason="catalog_exhausted")
        if conn is not None:
            import escalations
            escalations.open_escalation(
                conn, design=design, project_path=entry["project_path"],
                run_id=None, reason="catalog_exhausted",
                notes=json.dumps(status, sort_keys=True))


def learn_cycle(led: Ledger, conn, *, prev_heur: dict | None,
                n_ab_designs: int = 2) -> dict:
    """learn -> diff -> enqueue candidates -> plan A/B trials -> append arm
    entries to the ledger (the SAME loop executes them)."""
    import ab_runner
    import recipe_lifecycle
    heur = _learn()
    cands = recipe_lifecycle.diff_and_enqueue(conn, heur, prev=prev_heur)
    for key in recipe_lifecycle.pending_candidates(conn):
        trial = ab_runner.plan_trial(conn, **key, n_designs=n_ab_designs)
        if trial is None:
            continue
        strat8 = key["strategy"][:8]
        for d in trial["designs"]:
            for arm in ("A", "B"):
                src = Path(d["project_path"])
                dst = src.parent / f"{src.name}_ab{arm}_{strat8}"
                if src.is_dir() and not dst.exists():
                    shutil.copytree(src, dst,
                                    ignore=shutil.ignore_patterns("backend", "*.gds"))
                led.add({"design": dst.name, "project_path": str(dst),
                         "platform": key["platform"], "kind": "ab_arm",
                         "arm": arm, "strategy": key["strategy"],
                         "ab_key": key, "match_level": trial["match_level"]})
    return heur


def judge_finished_trials(led: Ledger, conn) -> None:
    """Pair finished A/B arms by (base design, strategy) and record verdicts."""
    import ab_runner
    import knowledge_db
    arms = [e for e in led.entries() if e["kind"] == "ab_arm"
            and e["state"] in ("clean", "escalated", "abandoned")
            and not e.get("judged")]
    by_pair: dict[tuple, dict] = {}
    for e in arms:
        base = e["design"].rsplit("_ab", 1)[0]
        by_pair.setdefault((base, e["strategy"]), {})[e["arm"]] = e
    for (base, strat), pair in by_pair.items():
        if set(pair) != {"A", "B"}:
            continue
        metrics = {}
        for arm, e in pair.items():
            row = conn.execute(
                "SELECT total_elapsed_s, fix_iters_to_clean, drc_status, "
                "lvs_status, rcx_status, lvs_mismatch_class, orfs_status "
                "FROM runs WHERE project_path=? ORDER BY ingested_at DESC "
                "LIMIT 1", (e["project_path"],)).fetchone()
            if row is None:
                metrics[arm] = None
                continue
            cols = ("total_elapsed_s", "fix_iters_to_clean", "drc_status",
                    "lvs_status", "rcx_status", "lvs_mismatch_class",
                    "orfs_status")
            r = dict(zip(cols, row))
            metrics[arm] = {"is_success": knowledge_db.is_success(r),
                            "wall_s": r["total_elapsed_s"],
                            "fix_iters": r["fix_iters_to_clean"]}
        verdict = ab_runner.judge(metrics.get("A"), metrics.get("B"))
        ab_runner.record_trial(
            conn, key=pair["B"]["ab_key"], verdict=verdict,
            arm_a_run_id=None, arm_b_run_id=None,
            metrics=metrics, match_level=pair["B"].get("match_level"))
        for e in pair.values():
            led.set_state(e["design"], e["state"], judged=True)


def run(ledger_path: Path, *, max_designs: int | None = None) -> None:
    import knowledge_db
    led = Ledger(ledger_path)
    conn = knowledge_db.connect()
    knowledge_db.ensure_schema(conn)
    prev_heur = None
    hp = KNOWLEDGE / "heuristics.json"
    if hp.exists():
        prev_heur = json.loads(hp.read_text())
    done = 0
    while True:
        pending = led.pending()
        if not pending or (max_designs and done >= max_designs):
            break
        entry = pending[0]
        process_one(led, entry, conn)
        done += 1
        heur = learn_cycle(led, conn, prev_heur=prev_heur)
        judge_finished_trials(led, conn)
        prev_heur = heur
    conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("run")
    pr.add_argument("--ledger", required=True, type=Path)
    pr.add_argument("--max", type=int, default=None)
    pa = sub.add_parser("add")
    pa.add_argument("--ledger", required=True, type=Path)
    pa.add_argument("--project", required=True)
    pa.add_argument("--platform", default="nangate45")
    ps = sub.add_parser("status")
    ps.add_argument("--ledger", required=True, type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "run":
        run(args.ledger, max_designs=args.max)
    elif args.cmd == "add":
        led = Ledger(args.ledger)
        led.add({"design": Path(args.project).name,
                 "project_path": str(Path(args.project).resolve()),
                 "platform": args.platform})
    else:
        led = Ledger(args.ledger)
        from collections import Counter
        for state, n in Counter(e["state"] for e in led.entries()).items():
            print(f"{state:10s} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_engineer_loop.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/scripts/loop/engineer_loop.py r2g-rtl2gds/tests/test_engineer_loop.py r2g-rtl2gds/tests/conftest.py
git commit -m "feat(skill): engineer_loop campaign orchestrator — ledger, fix path, learn cycle, A/B arms, judging"
```

---

### Task 14: `trace_provenance.py` — solution ← action ← design ← bug

**Files:**
- Create: `r2g-rtl2gds/knowledge/trace_provenance.py`
- Test: `r2g-rtl2gds/tests/test_trace_provenance.py`

- [ ] **Step 1: Write the failing test**

```python
"""Cross-DB provenance (spec §5.9, decision 11): both query directions."""
import json

import journal_db
import knowledge_db
import recipe_lifecycle
import trace_provenance

KEY = dict(symptom_id="deadbeef00000001", design_class="crypto/small",
           platform="nangate45", strategy="antenna_diode_repair")


def _setup(tmp_path):
    kc = knowledge_db.connect(tmp_path / "runs.sqlite")
    knowledge_db.ensure_schema(kc)
    jc = journal_db.connect(tmp_path / "journal.sqlite")
    journal_db.ensure_schema(jc)
    # knowledge side: run + trajectory + promoted recipe + trial
    kc.execute("INSERT OR REPLACE INTO runs (run_id, project_path, design_name,"
               " platform, ingested_at, design_class) "
               "VALUES ('r1','/p/d1','d1','nangate45','t','crypto/small')")
    kc.execute("INSERT OR REPLACE INTO fix_trajectories (fix_session_id,"
               " project_path, design_name, platform, check_type,"
               " violation_class, path_json, outcome, winning_strategy,"
               " symptom_id) VALUES ('sess1','/p/d1','d1','nangate45','drc',"
               "'antenna','[]','resolved','antenna_diode_repair',"
               "'deadbeef00000001')")
    kc.execute("INSERT INTO ab_trials (symptom_id, design_class, platform,"
               " strategy, verdict, ts) VALUES (?,?,?,?,'win','t')",
               tuple(KEY.values()))
    recipe_lifecycle.promote(kc, evidence="ab_trial:1", **KEY)
    kc.commit()
    # journal side: action + bug for the same session/run
    journal_db.append_action(jc, project_path="/p/d1", actor="loop",
                             action_type="config_knob_delta",
                             payload={"knob": "SKIP_ANTENNA_REPAIR", "new": "1"},
                             fix_session_id="sess1", run_id="r1")
    journal_db.append_tool_bug(jc, project_path="/p/d1", stage="route",
                               tool="openroad", signature="antenna ratio",
                               symptom_id="deadbeef00000001", run_id="r1")
    return kc, jc


def test_solution_to_origin_tree(tmp_path):
    _setup(tmp_path)
    tree = trace_provenance.solution_origin(
        knowledge_db_path=tmp_path / "runs.sqlite",
        journal_db_path=tmp_path / "journal.sqlite", **KEY)
    assert tree["status"] == "promoted"
    assert tree["ab_trials"][0]["verdict"] == "win"
    assert tree["episodes"][0]["design_name"] == "d1"
    assert tree["episodes"][0]["actions"][0]["action_type"] == "config_knob_delta"
    assert tree["bugs"][0]["signature"] == "antenna ratio"


def test_bug_to_solutions(tmp_path):
    _setup(tmp_path)
    sols = trace_provenance.bug_solutions(
        knowledge_db_path=tmp_path / "runs.sqlite",
        symptom_id="deadbeef00000001")
    assert sols[0]["strategy"] == "antenna_diode_repair"
    assert sols[0]["status"] == "promoted"
    assert "d1" in sols[0]["proven_on"]


def test_read_only_no_writes(tmp_path):
    kc, jc = _setup(tmp_path)
    before = (tmp_path / "runs.sqlite").stat().st_mtime_ns
    trace_provenance.solution_origin(
        knowledge_db_path=tmp_path / "runs.sqlite",
        journal_db_path=tmp_path / "journal.sqlite", **KEY)
    assert (tmp_path / "runs.sqlite").stat().st_mtime_ns == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_trace_provenance.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `trace_provenance.py`**

```python
#!/usr/bin/env python3
"""Cross-DB provenance tracing (engineer-loop spec §5.9, decision 11).

Read-only on BOTH DBs (URI mode=ro, same discipline as build_lineage_view).
solution_origin(): recipe -> ab_trials + fix_trajectories -> journal actions
-> runs/designs -> symptoms/tool_bugs. bug_solutions(): symptom -> every known
solution + lifecycle status + the designs it was proven on.

CLI:
  trace_provenance.py solution --symptom S --class C --platform P --strategy ST
  trace_provenance.py bug --symptom S
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

KNOWLEDGE = Path(__file__).resolve().parent
DEFAULT_KDB = KNOWLEDGE / "runs.sqlite"
DEFAULT_JDB = KNOWLEDGE / "journal.sqlite"


def _ro(path: Path | str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)


def _rows(conn, sql, params=()) -> list[dict]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def solution_origin(*, symptom_id: str, design_class: str, platform: str,
                    strategy: str, knowledge_db_path=DEFAULT_KDB,
                    journal_db_path=DEFAULT_JDB) -> dict:
    kc = _ro(knowledge_db_path)
    try:
        st = kc.execute(
            "SELECT status, provenance FROM recipe_status WHERE symptom_id=? "
            "AND design_class=? AND platform=? AND strategy=?",
            (symptom_id, design_class, platform, strategy)).fetchone()
        trials = _rows(kc, "SELECT trial_id, verdict, match_level, metrics_json,"
                           " ts FROM ab_trials WHERE symptom_id=? AND strategy=?"
                           " ORDER BY trial_id", (symptom_id, strategy))
        episodes = _rows(kc, "SELECT fix_session_id, design_name, project_path,"
                             " platform, outcome, winning_strategy "
                             "FROM fix_trajectories WHERE symptom_id=? AND"
                             " winning_strategy=?", (symptom_id, strategy))
        symptoms = _rows(kc, "SELECT check_type, class, predicates_json "
                             "FROM symptoms WHERE symptom_id=?", (symptom_id,))
    finally:
        kc.close()
    bugs: list[dict] = []
    if Path(journal_db_path).exists():
        jc = _ro(journal_db_path)
        try:
            for ep in episodes:
                ep["actions"] = _rows(
                    jc, "SELECT action_id, action_type, payload_json, ts "
                        "FROM actions WHERE fix_session_id=? ORDER BY action_id",
                    (ep["fix_session_id"],))
            bugs = _rows(jc, "SELECT project_path, stage, tool, signature, ts "
                             "FROM tool_bugs WHERE symptom_id=?", (symptom_id,))
        finally:
            jc.close()
    else:
        for ep in episodes:
            ep["actions"] = []
    return {
        "key": {"symptom_id": symptom_id, "design_class": design_class,
                "platform": platform, "strategy": strategy},
        "status": st[0] if st else "promoted",     # grandfathered default
        "provenance": st[1] if st else "grandfathered",
        "symptom": symptoms[0] if symptoms else None,
        "ab_trials": trials,
        "episodes": episodes,                       # designs + their actions
        "bugs": bugs,
    }


def bug_solutions(*, symptom_id: str, knowledge_db_path=DEFAULT_KDB) -> list[dict]:
    kc = _ro(knowledge_db_path)
    try:
        sols = _rows(kc, "SELECT winning_strategy AS strategy,"
                         " COUNT(*) AS n_resolved,"
                         " GROUP_CONCAT(DISTINCT design_name) AS proven_on "
                         "FROM fix_trajectories WHERE symptom_id=? AND"
                         " outcome='resolved' AND winning_strategy IS NOT NULL "
                         "GROUP BY winning_strategy ORDER BY n_resolved DESC",
                     (symptom_id,))
        for s in sols:
            row = kc.execute(
                "SELECT status FROM recipe_status WHERE symptom_id=? AND"
                " strategy=? ORDER BY updated_at DESC LIMIT 1",
                (symptom_id, s["strategy"])).fetchone()
            s["status"] = row[0] if row else "promoted"
            s["proven_on"] = (s["proven_on"] or "").split(",")
    finally:
        kc.close()
    return sols


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("solution")
    for f in ("--symptom", "--class", "--platform", "--strategy"):
        ps.add_argument(f, required=True, dest=f.lstrip("-").replace("class", "dclass"))
    pb = sub.add_parser("bug")
    pb.add_argument("--symptom", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "solution":
        out = solution_origin(symptom_id=args.symptom, design_class=args.dclass,
                              platform=args.platform, strategy=args.strategy)
    else:
        out = bug_solutions(symptom_id=args.symptom)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_trace_provenance.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add r2g-rtl2gds/knowledge/trace_provenance.py r2g-rtl2gds/tests/test_trace_provenance.py
git commit -m "feat(skill): trace_provenance — solution<-action<-design<-bug across both DBs (read-only)"
```

---

### Task 15: `build_strength_report.py` + dashboard panel

**Files:**
- Create: `r2g-rtl2gds/scripts/reports/build_strength_report.py`
- Modify: `r2g-rtl2gds/scripts/dashboard/generate_multi_project_dashboard.py` (add panel; read `build_lineage_view.py`'s integration first and mirror it)
- Test: `r2g-rtl2gds/tests/test_strength_report.py`

- [ ] **Step 1: Write the failing test**

```python
"""Strength metrics (spec §5.6 / decision 6): trends vs heuristics generation."""
import build_strength_report
import knowledge_db


def _seed(conn, gen, first_clean, iters, design="d", n=4):
    for i in range(n):
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, project_path, design_name,"
            " platform, ingested_at, heuristics_generation,"
            " first_attempt_clean, fix_iters_to_clean, wall_s_to_clean)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (f"r{gen}_{design}_{i}", f"/p/{design}{gen}{i}", f"{design}{gen}{i}",
             "nangate45", "t", gen, first_clean, iters, 100.0 * (i + 1)))
    conn.commit()


def test_report_groups_by_generation(tmp_path):
    conn = knowledge_db.connect(tmp_path / "runs.sqlite")
    knowledge_db.ensure_schema(conn)
    _seed(conn, gen=1, first_clean=0, iters=4)
    _seed(conn, gen=2, first_clean=1, iters=1)
    rep = build_strength_report.build(tmp_path / "runs.sqlite")
    gens = {g["generation"]: g for g in rep["generations"]}
    assert gens[1]["first_pass_clean_rate"] == 0.0
    assert gens[2]["first_pass_clean_rate"] == 1.0
    assert gens[2]["median_fix_iters_to_clean"] == 1
    assert rep["trend"]["first_pass_improving"] is True


def test_transfer_evidence_lists_cross_design_wins(tmp_path):
    conn = knowledge_db.connect(tmp_path / "runs.sqlite")
    knowledge_db.ensure_schema(conn)
    for d, sess in (("alpha", "s1"), ("beta", "s2")):
        conn.execute(
            "INSERT OR REPLACE INTO fix_trajectories (fix_session_id,"
            " project_path, design_name, platform, check_type, path_json,"
            " outcome, winning_strategy, symptom_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (sess, f"/p/{d}", d, "nangate45", "drc", "[]", "resolved",
             "antenna_diode_repair", "deadbeef00000001"))
    conn.commit()
    rep = build_strength_report.build(tmp_path / "runs.sqlite")
    ev = rep["transfer_evidence"][0]
    assert ev["symptom_id"] == "deadbeef00000001"
    assert set(ev["designs"]) == {"alpha", "beta"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_strength_report.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `build_strength_report.py`**

```python
#!/usr/bin/env python3
"""Strength metrics projection (engineer-loop spec §5.6, decision 6).

Read-only over runs.sqlite (mode=ro, like build_lineage_view). Strength =
first-pass clean rate UP and median iterations/wall-clock-to-clean DOWN as the
heuristics generation grows. Also lists per-symptom transfer evidence (one
strategy resolving the same symptom on 2+ distinct designs).

CLI: build_strength_report.py [--db PATH] [--out reports/strength.json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"


def _ro(path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)


def build(db_path) -> dict:
    conn = _ro(db_path)
    try:
        cur = conn.execute(
            "SELECT heuristics_generation AS gen, first_attempt_clean,"
            " fix_iters_to_clean, wall_s_to_clean FROM runs"
            " WHERE heuristics_generation IS NOT NULL")
        rows = [dict(zip([c[0] for c in cur.description], r))
                for r in cur.fetchall()]
        tcur = conn.execute(
            "SELECT symptom_id, winning_strategy, design_name"
            " FROM fix_trajectories WHERE outcome='resolved'"
            " AND winning_strategy IS NOT NULL AND symptom_id IS NOT NULL")
        traj = tcur.fetchall()
    finally:
        conn.close()

    by_gen: dict[int, list[dict]] = {}
    for r in rows:
        by_gen.setdefault(r["gen"], []).append(r)
    generations = []
    for gen in sorted(by_gen):
        g = by_gen[gen]
        firsts = [r["first_attempt_clean"] for r in g
                  if r["first_attempt_clean"] is not None]
        iters = [r["fix_iters_to_clean"] for r in g
                 if r["fix_iters_to_clean"] is not None]
        walls = [r["wall_s_to_clean"] for r in g
                 if r["wall_s_to_clean"] is not None]
        generations.append({
            "generation": gen,
            "n_runs": len(g),
            "first_pass_clean_rate": (sum(firsts) / len(firsts)) if firsts else None,
            "median_fix_iters_to_clean": (int(statistics.median(iters))
                                          if iters else None),
            "median_wall_s_to_clean": (statistics.median(walls)
                                       if walls else None),
        })
    rated = [g for g in generations if g["first_pass_clean_rate"] is not None]
    improving = (len(rated) >= 2
                 and rated[-1]["first_pass_clean_rate"]
                 > rated[0]["first_pass_clean_rate"])

    transfer: dict[tuple, set] = {}
    for sid, strat, design in traj:
        transfer.setdefault((sid, strat), set()).add(design)
    transfer_evidence = [
        {"symptom_id": sid, "strategy": strat, "designs": sorted(designs)}
        for (sid, strat), designs in sorted(transfer.items())
        if len(designs) >= 2]

    return {"generations": generations,
            "trend": {"first_pass_improving": improving},
            "transfer_evidence": transfer_evidence}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=KNOWLEDGE / "runs.sqlite")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    rep = build(args.db)
    text = json.dumps(rep, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Dashboard panel**

Read how `generate_multi_project_dashboard.py` embeds `build_lineage_view.py`'s panels (grep for `lineage` in `scripts/dashboard/`). Mirror that: call `build_strength_report.build(...)` guarded by try/except, render a small "Skill strength" HTML section — a table of `generation | n_runs | first-pass clean % | median iters | median wall_s` plus a one-line trend badge (improving/flat). No test beyond the build() unit tests; verify by regenerating the dashboard once locally.

- [ ] **Step 5: Run tests, commit**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_strength_report.py -v`
Expected: 2 passed

```bash
git add r2g-rtl2gds/scripts/reports/build_strength_report.py r2g-rtl2gds/scripts/dashboard/generate_multi_project_dashboard.py r2g-rtl2gds/tests/test_strength_report.py
git commit -m "feat(skill): strength report (first-pass + iters-to-clean vs generation) + dashboard panel"
```

---

### Task 16: Documentation — agent runbook, SKILL.md, knowledge/README.md

**Files:**
- Create: `r2g-rtl2gds/references/engineer-loop.md`
- Modify: `r2g-rtl2gds/SKILL.md` (new "Engineer Loop (campaign mode)" section)
- Modify: `r2g-rtl2gds/knowledge/README.md` (journal DB + lifecycle + provenance)

No code tests — this is prose. Verify by re-reading against the spec.

- [ ] **Step 1: Write `references/engineer-loop.md`**

Contents (~150 lines), covering:
1. **Loop overview** — the §4 architecture diagram + one-paragraph walkthrough.
2. **Running a campaign** — `python3 scripts/loop/engineer_loop.py add --ledger design_cases/_loop/ledger.jsonl --project <dir>`; `... run --ledger ... [--max N]`; `... status --ledger ...`; resumability (kill/restart safe); env knobs (`R2G_JOURNAL=0`, `R2G_JOURNAL_DB`, seams `R2G_LOOP_*`).
3. **Escalation drain (agent runbook)** — the exact procedure: `python3 -c "import escalations, knowledge_db; ..."` or a `escalations.py` CLI listing; for each open item: (a) inspect `trace_provenance.py bug --symptom <sid>` + `diagnose_signoff_fix.py --list` (lessons attached) + `references/failure-patterns.md`; (b) author a NEW strategy into the appropriate catalog in `diagnose_signoff_fix.py` with a symptom predicate; (c) stage it `shadow` via `recipe_lifecycle.stage_shadow(conn, provenance='agent:<escalation_id>', ...)`; (d) journal every action via `journal_action.py action --actor agent ...`; (e) `escalations.resolve(...)` + re-add the design to the ledger. State explicitly: **agent strategies get no special trust — they must win their A/B to promote (decision 7)**.
4. **Provenance queries** — both `trace_provenance.py` CLI forms with example output.
5. **Safety invariants** — hard clamps absolute; prose never auto-merged; promotion is evidence-only; journal failures never break flows.

- [ ] **Step 2: Add the SKILL.md section**

After the existing signoff-fixing section in `r2g-rtl2gds/SKILL.md`, add ~20 lines: when to use campaign mode (multi-design runs), the three CLI commands, the escalation-drain pointer to `references/engineer-loop.md`, and the hard rules (never two same DESIGN_NAME+FLOW_VARIANT; LVS concurrency; loop runs with workers=1 in Phase 1).

- [ ] **Step 3: Update `knowledge/README.md`**

Add a "Engineer Loop (spec 2026-06-09)" section: the two-DB table (journal.sqlite = evidence, gitignored; runs.sqlite/heuristics.json = conclusions, tracked), the new tables (actions/log_summaries/tool_bugs; recipe_status/ab_trials/escalations/meta), the lifecycle diagram (shadow→candidate→promoted, A/B gated), and invariant additions: (16) only promoted recipes rank live; (17) journal archival loses no conclusions; (18) provenance chain queryable via trace_provenance.py.

- [ ] **Step 4: Commit**

```bash
git add r2g-rtl2gds/references/engineer-loop.md r2g-rtl2gds/SKILL.md r2g-rtl2gds/knowledge/README.md
git commit -m "docs(skill): engineer-loop runbook + SKILL.md campaign mode + knowledge README update"
```

---

### Task 17: Dry-run integration test (mocked flow, full loop turn)

**Files:**
- Test: `r2g-rtl2gds/tests/test_engineer_loop_integration.py`

End-to-end on synthetic designs with the subprocess seams pointed at fake scripts — proves the WHOLE chain (flow→journal→ingest→learn→candidate→A/B arms→judge→promote→strength) without EDA tools, deterministically.

- [ ] **Step 1: Write the integration test**

```python
"""Full loop turn, mocked flow (spec §9 integration). No EDA tools needed."""
import json
import os
from pathlib import Path

import engineer_loop
import knowledge_db
import recipe_lifecycle


def _fake_scripts(tmp_path: Path) -> dict:
    """Fake run_orfs/fix_signoff/ingest that fabricate plausible artifacts."""
    flow = tmp_path / "fake_flow.sh"
    flow.write_text("#!/bin/bash\n"
                    "mkdir -p \"$1/reports\" \"$1/backend\"\n"
                    "echo '{\"stage\":\"finish\",\"status\":0,\"elapsed_s\":10}'"
                    " > \"$1/backend/stage_log.jsonl\"\n"
                    "exit 0\n")
    fix = tmp_path / "fake_fix.sh"
    fix.write_text("#!/bin/bash\n"
                   "# arm B (rank-first set) clears; arm A leaves residual\n"
                   "if [ -n \"$R2G_FIX_RANK_FIRST\" ]; then\n"
                   "  echo '{\"status\": \"clean\"}' > \"$1/reports/drc.json\"\n"
                   "  exit 0\n"
                   "fi\n"
                   "exit 2\n")
    for f in (flow, fix):
        f.chmod(0o755)
    return {"R2G_LOOP_RUN_FLOW": str(flow), "R2G_LOOP_FIX": str(fix)}


def _mk_failing_project(tmp_path, name):
    p = tmp_path / "designs" / name
    (p / "constraints").mkdir(parents=True)
    (p / "reports").mkdir()
    (p / "constraints" / "config.mk").write_text(
        f"export DESIGN_NAME = {name}\nexport PLATFORM = nangate45\n")
    (p / "reports" / "drc.json").write_text(json.dumps(
        {"status": "fail", "total_violations": 3,
         "categories": {"antenna": {"count": 3}}}))
    (p / "reports" / "lvs.json").write_text(json.dumps({"status": "clean"}))
    (p / "reports" / "ppa.json").write_text(json.dumps(
        {"summary": {}, "geometry": {"instance_count": 900}}))
    return p


def test_full_turn_runs_arms_and_promotes_winner(tmp_path, monkeypatch):
    import ab_runner
    import ingest_run
    db = tmp_path / "runs.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    monkeypatch.setenv("R2G_JOURNAL_DB", str(tmp_path / "journal.sqlite"))
    for k, v in _fake_scripts(tmp_path).items():
        monkeypatch.setenv(k, v)
    # real ingest against the temp DB; real learn against it too
    monkeypatch.setattr(engineer_loop, "_ingest",
                        lambda e: ingest_run.ingest(Path(e["project_path"]), conn))
    monkeypatch.setattr(
        engineer_loop, "_learn",
        lambda: __import__("learn_heuristics").learn(
            db, tmp_path / "heuristics.json"))

    # Two seed designs whose drc fails -> fixing path -> arm A escalates.
    led = engineer_loop.Ledger(tmp_path / "ledger.jsonl")
    for n in ("alpha_small", "beta_small"):
        p = _mk_failing_project(tmp_path, n)
        led.add({"design": n, "project_path": str(p), "platform": "nangate45"})
    engineer_loop.run = engineer_loop.run  # (CLI path not used; drive directly)
    prev = {"generation": 0, "recipes": {}}
    for entry in list(led.pending()):
        engineer_loop.process_one(led, entry, conn)
    heur = engineer_loop.learn_cycle(led, conn, prev_heur=prev, n_ab_designs=1)

    # The fix episodes produced a learned recipe -> candidate -> arm entries.
    arms = [e for e in led.entries() if e["kind"] == "ab_arm"]
    assert len(arms) == 2 and {a["arm"] for a in arms} == {"A", "B"}

    # Execute the arms (resume semantics: they are just pending entries).
    for entry in list(led.pending()):
        engineer_loop.process_one(led, entry, conn)
    engineer_loop.judge_finished_trials(led, conn)

    key = arms[0]["ab_key"]
    row = conn.execute("SELECT verdict FROM ab_trials WHERE strategy=?",
                       (key["strategy"],)).fetchone()
    assert row is not None and row[0] in ("win", "loss", "inconclusive")
    if row[0] == "win":          # fake arm B clears -> expected path
        assert recipe_lifecycle.get_status(conn, **key) == "promoted"

    # Arm A (exclude) exits 2 -> at least one catalog_exhausted escalation.
    import escalations
    assert any(e["reason"] == "catalog_exhausted"
               for e in escalations.list_open(conn))

    # Strength projection sees the ingested runs.
    import build_strength_report
    rep = build_strength_report.build(db)
    assert isinstance(rep["generations"], list)
```

Implementer notes: (a) the fake fix script must also write a `fix_log.jsonl`
with one `cleared` iteration in the rank-first branch so `learn_cycle` has a
trajectory to learn a candidate from — extend `_fake_scripts` accordingly
(echo a JSON line into `$1/reports/fix_log.jsonl` before exiting); (b)
everything stays under `tmp_path` — `R2G_JOURNAL_DB`, `--db`, ledger, designs;
no global state; (c) target wall-time `< 30 s`.

- [ ] **Step 2: Run it**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/test_engineer_loop_integration.py -v`
Expected: PASS. Iterate on loop bugs surfaced here — this is the test that catches cross-module seams.

- [ ] **Step 3: Commit**

```bash
git add r2g-rtl2gds/tests/test_engineer_loop_integration.py
git commit -m "test(skill): dry-run integration — full loop turn with mocked flow, A/B promotion + escalation"
```

---

### Task 18: Dead-code & script cleanup (spec §5.8)

**Files:**
- Delete: see inventory below
- Modify: `r2g-rtl2gds/references/lessons-learned.md` (one-line historical notes where warranted)

- [ ] **Step 1: Verify zero references for each candidate, then delete**

For EACH file below, run the gate first; delete only on zero hits (excluding the file itself and `docs/`):

```bash
cd /proj/workarea/user5/agent-r2g
for f in tools/_cmp_v3_vs_skill.sh tools/_complete_signoff_side.sh \
         tools/_finish_signoff_remainder.sh tools/_koios_attempt.sh \
         tools/_lvs_recovery.sh tools/_wait_bulk.sh tools/_wait_koios.sh \
         tools/_wf_complete_remaining.js tools/retry_pass4.sh \
         tools/pass4_recover_timeouts.sh tools/pass4_status.sh \
         tools/retry_boom_pass3.sh tools/retry_boom_pass4.sh \
         tools/retry_boom_timeouts.sh tools/pending_recovers.txt install.sh; do
  hits=$(grep -rl "$(basename $f)" --exclude-dir=.git --exclude-dir=docs \
         --exclude="$(basename $f)" r2g-rtl2gds/ tools/ CLAUDE.md 2>/dev/null | wc -l)
  echo "$f: $hits references"
done
```

Then `git rm` the tracked ones / `rm` the untracked ones with 0 hits. Also remove scratch dirs: `tools/_boom_finish_logs/`, `tools/_boom_route_fast_logs/`, `tools/_drc_band_410k*.log`, `tools/_lvs_test_*.log`, `last_graph/` (untracked scratch — plain `rm -rf` after the same grep gate on `last_graph`).

DO NOT delete in Phase 1 (post-loop candidates, spec §5.8 — they remain the operator path until the Phase-0 live run passes): `tools/sky130_campaign.py`, `tools/run_sky130_design.sh`, `tools/mk_sky130_project.py`, `tools/launch_boom_route_fast.sh`, `tools/batch_*.sh`, `tools/install_nangate45_*.sh` (installers are live).

- [ ] **Step 2: Record historical value**

For the retired campaign drivers (`retry_*`, `pass4_*`): add ONE line each to the existing campaign-history section of `r2g-rtl2gds/references/lessons-learned.md` (find the corpus-results tables) noting the tool name and what campaign it served, only where that campaign is not already documented.

- [ ] **Step 3: Full suite + commit**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/ -q`
Expected: no regressions (deletions are repo-level tools, not skill code).

```bash
git add -A
git commit -m "chore(skill): dead-code cleanup — retire one-off operator scripts + scratch logs (spec §5.8)"
```

---

### Task 19: Final verification + store regeneration

- [ ] **Step 1: Full test suite**

Run: `cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds && python3 -m pytest tests/ -q`
Expected: 417 baseline + ~35 new, 0 failures (8 pre-existing skips OK).

- [ ] **Step 2: Regenerate the live store with the new learner**

```bash
cd /proj/workarea/user5/agent-r2g/r2g-rtl2gds
python3 knowledge/learn_heuristics.py
python3 -c "import json; d=json.load(open('knowledge/heuristics.json'));\
print('generation', d.get('generation'), '| recipes', len(d.get('recipes', {})),\
'| symptoms', len(d.get('symptoms', {})))"
```

Expected: generation ≥ 1, recipes keyed by symptom hashes. Spot-check one known symptom (the iccad2015 timing one) appears under `recipes` with `design_class` sub-keys.

- [ ] **Step 3: Smoke the loop CLI end-to-end on one real small design**

Pick the smallest existing clean nangate45 project from `design_cases/` (use `tools/sky130_campaign.py`-style query or just `sqlite3 knowledge/runs.sqlite "SELECT project_path FROM runs WHERE platform='nangate45' ORDER BY cell_count LIMIT 1"`). Then:

```bash
python3 scripts/loop/engineer_loop.py add --ledger /tmp/el_smoke/ledger.jsonl --project <that-dir>
python3 scripts/loop/engineer_loop.py run --ledger /tmp/el_smoke/ledger.jsonl --max 1
python3 scripts/loop/engineer_loop.py status --ledger /tmp/el_smoke/ledger.jsonl
```

Expected: terminal state `clean` (or honest `escalated` with an escalations row), journal rows present (`sqlite3 knowledge/journal.sqlite "SELECT COUNT(*) FROM actions"` > 0).

- [ ] **Step 4: Commit store refresh**

```bash
git add r2g-rtl2gds/knowledge/heuristics.json r2g-rtl2gds/knowledge/runs.sqlite
git commit -m "chore(knowledge): regenerate store with generation counter + decision-8 recipes projection"
```

- [ ] **Step 5: Update docs/superpowers + memory**

Per project convention (memory: feedback_update_plan_spec_docs): add a dated completion note to the spec + this plan, and update auto-memory `project_symptom_indexed_memory.md` / create `project_engineer_loop.md` with the outcome.

---

## Plan self-review (done at authoring time)

- **Spec coverage:** §5.1→T13; §5.2→T1-T5; §5.3→T7; §5.4→T11-T13; §5.5→T10,T16; §5.6→T15; §5.7.1/.2→T8 (pooled priors already live in diagnose — T8 adds the floor + decision-8 lookup); §5.7.3 lessons → already wired (`attach_lessons` in diagnose main, verified 2026-06-10); §5.7.4→T9; §5.8→T18; §5.9→T14; decisions 8 (index)→T6+T8; 10 (telemetry)→T2-T4; 11 (two DBs)→T1,T5,T14.
- **Known judgment calls for the implementer:** (a) Task 4's exact `run_orfs.sh` variable names must be read from the script (the plan cites line anchors, not exact vars — the stage loop changed before and may drift); (b) Task 8's `_cursig` refactor is the implementer's choice of helper shape; (c) Task 13 `judge_finished_trials` matches arm runs by `project_path` — arms MUST be ingested before judging (the loop's `process_one` already ingests every design including arms).
- **Type consistency check:** `recipe_lifecycle` keys are kwargs `(symptom_id, design_class, platform, strategy)` everywhere (T7/T8/T11/T13/T14); `judge` arm dicts `{is_success, wall_s, fix_iters}` (T11/T13); ledger entries carry `kind`/`arm`/`strategy`/`ab_key` (T11's `plan_trial` output consumed by T13's `learn_cycle`).

## Execution

Recommended order is task order (each builds on the previous). Tasks 1-3, 10, 14, 15 are independent enough to parallelize across subagents if desired; Tasks 4-9, 11-13 are sequential.
