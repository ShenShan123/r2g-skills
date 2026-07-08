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
  journal_action.py report --project P --kind drc --file reports/drc.json \
      [--tool klayout] [--action-id N] [--db PATH]
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


def _cmd_report(args) -> None:
    """Digest a JSON report produced by ORFS / any EDA tool (spec rev 3)."""
    import journal_db
    import summarize_log
    conn = journal_db.connect(args.db)
    journal_db.ensure_schema(conn)
    rep = json.loads(Path(args.file).read_text(encoding="utf-8"))
    s = summarize_log.summarize_report(rep, kind=args.kind)
    journal_db.append_log_summary(
        conn, project_path=args.project, stage=args.kind,
        tool=args.tool or "report", source_path=str(args.file),
        status=s["status"], metrics=s["metrics"], digest=s["digest"],
        action_id=args.action_id)
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
        import journal_db
        args.db = journal_db.DEFAULT_JOURNAL_PATH
    try:
        args.fn(args)
    except Exception as exc:                      # never break the caller
        print(f"WARNING: journal_action skipped: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
