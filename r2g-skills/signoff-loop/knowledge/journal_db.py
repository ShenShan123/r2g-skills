#!/usr/bin/env python3
"""The ENTIRE Tier-0 journal memory system in one module (engineer-loop spec
§5.2, decisions 10/11; 2026-07-18 consolidation of journal_db + journal_action
+ summarize_log).

Three layers, one file:
  * DB helpers   — connect/ensure_schema/append_* over the SEPARATE high-volume
    gitignored SQLite file (default knowledge/journal.sqlite). EVIDENCE only —
    learning conclusions stay in knowledge.sqlite/heuristics.json, so journal
    loss/rotation never loses a recipe. WAL + busy_timeout: safe for concurrent
    append-only flow workers. Mirrors knowledge_db.py conventions.
  * Summarizer   — deterministic, stdlib-only log/report digests (decision 10).
    NEVER an LLM call — pure text extraction, fully reproducible. Raw log files
    may rotate; the digest stored in journal.sqlite survives.
  * CLI          — ONE entry point for every producer: fix_signoff.sh,
    run_orfs.sh, run_{drc,lvs,rcx}.sh, engineer_loop.py and the agent tier all
    journal identically. Contract: NEVER breaks the caller — any failure prints
    WARNING to stderr and exits 0; R2G_JOURNAL=0 disables journaling entirely.

Usage (CLI):
  journal_db.py action --project P --actor loop --type tool_invoke \
      [--payload JSON] [--design D] [--platform PL] [--session SID] \
      [--symptom SID16] [--parent N] [--db PATH]
  journal_db.py summarize --project P --stage route --tool openroad \
      --log FILE [--status pass|fail] [--action-id N] [--db PATH]
  journal_db.py report --project P --kind drc --file reports/drc.json \
      [--tool klayout] [--action-id N] [--db PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

# knowledge/ sibling imports (works as script or test module)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import symptom  # noqa: E402
from knowledge_db import now_local as _now  # noqa: E402  (invariant 32: ONE stamp)

DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent
DEFAULT_JOURNAL_PATH = DEFAULT_KNOWLEDGE_DIR / "journal.sqlite"
DEFAULT_SCHEMA_PATH = DEFAULT_KNOWLEDGE_DIR / "journal_schema.sql"

ACTION_TYPES = ("config_knob_delta", "sdc_edit", "stage_rerun", "tool_invoke",
                "escalate", "ab_launch", "promote", "demote")


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


# --- Summarizer (formerly summarize_log.py; engineer-loop decision 10) ------
# Deterministic, stdlib-only — NEVER an LLM call. Produces the log_summaries
# digest rows and tool_bugs detections for the Tier-0 journal.

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


# --- CLI (formerly journal_action.py; spec §5.2 producers) ------------------
# Contract: NEVER breaks the caller. Any failure prints WARNING to stderr and
# exits 0. R2G_JOURNAL=0 disables journaling entirely (silent no-op).

def _cmd_action(args) -> None:
    conn = connect(args.db)
    ensure_schema(conn)
    append_action(
        conn, project_path=args.project, actor=args.actor,
        action_type=args.type, payload=json.loads(args.payload or "{}"),
        design=args.design, platform=args.platform,
        fix_session_id=args.session, parent_action_id=args.parent,
        symptom_id=args.symptom)
    conn.close()


def _cmd_summarize(args) -> None:
    conn = connect(args.db)
    ensure_schema(conn)
    s = summarize_file(args.log, status_hint=args.status)
    append_log_summary(
        conn, project_path=args.project, stage=args.stage, tool=args.tool,
        source_path=str(args.log), status=s["status"],
        error_count=s["error_count"], warning_count=s["warning_count"],
        first_error=s["first_error"], last_lines=s["last_lines"],
        digest=s["digest"], action_id=args.action_id)
    if s["status"] not in ("pass", "clean", "complete", None):
        text = Path(args.log).read_text(encoding="utf-8", errors="ignore")
        for b in detect_bugs(text, vclass=args.stage):
            append_tool_bug(
                conn, project_path=args.project, stage=args.stage,
                tool=args.tool, signature=b["signature"],
                symptom_id=b["symptom_id"], signature_json=b["signature_json"],
                log_excerpt=b["log_excerpt"], action_id=args.action_id)
    conn.close()


def _cmd_report(args) -> None:
    """Digest a JSON report produced by ORFS / any EDA tool (spec rev 3)."""
    conn = connect(args.db)
    ensure_schema(conn)
    rep = json.loads(Path(args.file).read_text(encoding="utf-8"))
    s = summarize_report(rep, kind=args.kind)
    append_log_summary(
        conn, project_path=args.project, stage=args.kind,
        tool=args.tool or "report", source_path=str(args.file),
        status=s["status"], metrics=s["metrics"], digest=s["digest"],
        action_id=args.action_id)
    conn.close()


def main(argv=None) -> int:
    if os.environ.get("R2G_JOURNAL", "1") == "0":
        return 0
    ap = argparse.ArgumentParser(
        description="Append one Tier-0 journal entry (action/summarize/report).")
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
    pr = sub.add_parser("report")
    pr.add_argument("--project", required=True)
    pr.add_argument("--kind", required=True)    # drc|lvs|rcx|ppa|timing_check|...
    pr.add_argument("--file", required=True)
    pr.add_argument("--tool")
    pr.add_argument("--action-id", type=int, default=None)
    pr.add_argument("--db", default=None)
    pr.set_defaults(fn=_cmd_report)
    args = ap.parse_args(argv)
    if args.db is None:
        args.db = os.environ.get("R2G_JOURNAL_DB") or DEFAULT_JOURNAL_PATH
    try:
        args.fn(args)
    except Exception as exc:                      # never break the caller
        print(f"WARNING: journal write skipped: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
