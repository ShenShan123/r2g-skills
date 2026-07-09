#!/usr/bin/env python3
"""Project synth-frontend failure classes into knowledge.sqlite — Phase-4 bridge.

The rtl-acquire classifier (repair/classify_failed_candidates.py) and the JSON
failure casebook are the JOURNAL side: high-volume, machine-local hypotheses.
This script is the promoter that projects the distilled per-design class into
the knowledge-side tables via the documented ingest contract:

  * `<project>/reports/diagnosis.json` issues[] -> failure_events
    (kind `synth-frontend-<class>`, e.g. synth-frontend-template_placeholder),
    alongside the generic orfs-fail-synth event ingest_run.py already writes.
  * `<project>/reports/fix_log.jsonl` -> fix_events/fix_trajectories for the
    FINAL deterministic decision (exclude = deliberate abandonment -> negative
    learning; retry outcome read from the refreshed index.csv).

Then re-ingests each touched project (idempotent; failure_events are rebuilt
per run on every ingest). Run AFTER classification + the retry wave so the
index reflects final outcomes.

Fast honesty check (r2g invariant): every synth-fail acquire run must carry a
frontend failure_event after this runs — `--check` verifies exactly that.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
for p in (SCRIPTS_DIR, SCRIPTS_DIR / "repair"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from classify_failed_candidates import classify  # noqa: E402
from skill_env import (  # noqa: E402
    default_out_root,
    default_workspace_root,
    knowledge_dir,
    resolve_str_env,
)

FAILED_STATUSES = {"synth_failed"}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_index(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_diagnosis(project: Path, kind: str, summary: str, action: str) -> None:
    reports = project / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    diag_path = reports / "diagnosis.json"
    diag: dict = {}
    if diag_path.exists():
        try:
            diag = json.loads(diag_path.read_text(encoding="utf-8"))
        except Exception:
            diag = {}
    issues = [i for i in (diag.get("issues") or [])
              if not str(i.get("kind", "")).startswith("synth-frontend-")]
    issues.append({
        "kind": kind,
        "stage": "synth",
        "summary": summary[:300],
        "action": action,
        "source": "rtl-acquire/project_frontend_diagnosis",
        "ts": now_iso(),
    })
    diag["issues"] = issues
    diag_path.write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")


def append_fix_row(project: Path, design: str, reason: str, action: str,
                   cleared: bool) -> None:
    reports = project / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    fix_log = reports / "fix_log.jsonl"
    session = f"acquire-frontend-{design}"
    row = {
        "fix_session_id": session,
        "check": "synth",
        "violation_class": f"frontend_{reason}",
        "iter": 0,
        "strategy": f"acquire_{action}",
        "from_stage": "synth",
        "before": 1,
        "after": 0 if cleared else 1,
        "before_status": "fail",
        "after_status": "clean" if cleared else ("excluded" if action == "exclude" else "fail"),
        # exclude = deliberate abandonment (negative learning); a cleared retry
        # is a win; an uncleared retry is recorded as no_change.
        "verdict": "cleared" if cleared else "no_change",
        "predicates": {"acquire_action": action, "frontend_class": reason},
        "ts": now_iso(),
    }
    # Idempotent per (session, iter, strategy): drop a stale row for the same key.
    rows = []
    if fix_log.exists():
        for line in fix_log.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if not (r.get("fix_session_id") == session and r.get("iter") == 0
                    and r.get("strategy") == row["strategy"]):
                rows.append(r)
    rows.append(row)
    fix_log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                       encoding="utf-8")


def ingest(project: Path) -> bool:
    import subprocess  # noqa: PLC0415
    ingest_script = knowledge_dir() / "ingest_run.py"
    if not ingest_script.exists():
        print(f"WARNING: ingest script missing: {ingest_script}", file=sys.stderr)
        return False
    db = resolve_str_env("R2G_KNOWLEDGE_DB", "")
    cmd = [sys.executable, str(ingest_script), str(project)]
    if db:
        cmd += ["--db", db]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"WARNING: ingest failed for {project.name}: "
              f"{(result.stderr or result.stdout).strip()[-300:]}", file=sys.stderr)
        return False
    return True


def check_honesty(db_path: str) -> int:
    """count(acquire synth-fail runs) must equal count carrying a frontend event."""
    import sqlite3  # noqa: PLC0415
    conn = sqlite3.connect(db_path)
    fails = conn.execute(
        "SELECT COUNT(*) FROM runs r WHERE r.flow_scope='synth_only' "
        "AND r.orfs_status='fail'").fetchone()[0]
    with_frontend = conn.execute(
        "SELECT COUNT(DISTINCT r.run_id) FROM runs r "
        "JOIN failure_events fe ON fe.run_id = r.run_id "
        "WHERE r.flow_scope='synth_only' AND r.orfs_status='fail' "
        "AND fe.signature LIKE 'synth-frontend-%'").fetchone()[0]
    print(f"synth_only fail runs: {fails}; with frontend failure_event: {with_frontend}")
    if fails != with_frontend:
        print("HONESTY VIOLATION: synth-fail acquire runs without a frontend "
              "failure_event — run project_frontend_diagnosis after classification.",
              file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index-csv", type=Path, default=None)
    ap.add_argument("--projects-root", type=Path, default=None)
    ap.add_argument("--skip-ingest", action="store_true")
    ap.add_argument("--check", metavar="DB",
                    help="Run the fast honesty check against a knowledge db and exit")
    args = ap.parse_args()

    if args.check:
        return check_honesty(args.check)

    index_csv = args.index_csv or (default_out_root() / "index.csv")
    projects_root = args.projects_root or (default_workspace_root() / "synth_projects")

    touched = 0
    for row in load_index(index_csv):
        design = row.get("design", "")
        status = row.get("status", "")
        if not design:
            continue
        project = projects_root / design
        if not (project / "constraints" / "config.mk").exists():
            continue  # no flow was ever staged for this candidate
        if status in FAILED_STATUSES:
            action, reason = classify(row.get("source_path", ""), row.get("notes", ""))
            write_diagnosis(project, f"synth-frontend-{reason}", row.get("notes", ""), action)
            append_fix_row(project, design, reason, action, cleared=False)
        elif status in {"success", "graph_skipped", "graph_failed"}:
            # A design that previously failed and now synthesizes: close the
            # trajectory as cleared (retry win). Only if a fix session exists.
            fix_log = project / "reports" / "fix_log.jsonl"
            if not fix_log.exists():
                continue
            action, reason = classify(row.get("source_path", ""), row.get("notes", ""))
            append_fix_row(project, design, reason, "retry", cleared=True)
        else:
            continue
        if not args.skip_ingest:
            ingest(project)
        touched += 1

    print(f"projected diagnosis for {touched} designs (index: {index_csv})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
