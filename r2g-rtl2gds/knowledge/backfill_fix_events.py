#!/usr/bin/env python3
"""Backfill synthetic fix_events from historical design_cases/_batch/*.jsonl.

The pre-fix-learning campaign logs in design_cases/_batch/ already record the
failure -> success transitions we now want as Tier-1 fix_events. This is a
one-time, idempotent (`INSERT OR IGNORE`) backfill: each historical record maps
to a single synthetic fix iteration with provenance `backfill:<filestem>`.

Record shapes observed against the real corpus (design_cases/_batch, 2026-06-06):

  antenna_fix_*.jsonl : {design, inst, status, before, after, wall_s}
      DRC antenna repair. `before`/`after` are diode-repair violation counts.
  beol_drc_*.jsonl    : {design, inst, status, violations, drc_mode, wall_s}
      BEOL-only DRC pass. `violations` is the after-count (no before recorded).
  retry_pass*.jsonl   : {case, design, platform?, orfs, elapsed_s, from_stage, timeout}
  recover_pass*.jsonl : {case, orfs, elapsed_s, timeout, from_stage?, ...}  (no `design`)
  orfs_retry*.jsonl   : {case, design, platform, orfs, elapsed_s}
      ORFS rerun-from-stage recoveries. `orfs` == "pass" means the run closed;
      violation_class is the rerun-from stage (`from_stage`).

Mapping:
  antenna_fix_* , beol_drc_*  -> check_type = "drc"
  retry_pass* , recover_pass* , orfs_retry -> check_type = "orfs",
                                              violation_class = from_stage
  verdict: "cleared" iff after == 0, else "win" if after < before, else "no_change".
  fix_session_id = sha1(design + filename)[:16]   (stable per design+file)

CLI:
  python3 backfill_fix_events.py --batch-dir design_cases/_batch --db runs.sqlite
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

import knowledge_db

# Filename-stem prefixes we know how to parse, longest-first so e.g.
# "recover_pass" is matched before a hypothetical "recover".
_DRC_PREFIXES = ("antenna_fix", "beol_drc")
_ORFS_PREFIXES = ("recover_pass", "retry_pass", "orfs_retry")
_KNOWN_PREFIXES = _DRC_PREFIXES + _ORFS_PREFIXES


def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _session_id(design: str, filename: str) -> str:
    return hashlib.sha1(f"{design}{filename}".encode("utf-8")).hexdigest()[:16]


def _verdict(before: float | None, after: float | None) -> str:
    """cleared iff after==0, else win if after<before, else no_change."""
    if after is None:
        return "no_change"
    if after == 0:
        return "cleared"
    if before is not None and after < before:
        return "win"
    return "no_change"


def _file_prefix(stem: str) -> str | None:
    for p in _KNOWN_PREFIXES:
        if stem.startswith(p):
            return p
    return None


def _parse_drc_record(rec: dict, prefix: str) -> dict | None:
    """antenna_fix_* / beol_drc_* -> a normalized DRC fix-event dict."""
    design = rec.get("design")
    if not design:
        return None
    if prefix == "beol_drc":
        # No before-count is recorded; `violations` is the post-fix count.
        before = None
        after = rec.get("violations")
        default_class = "beol"
    else:  # antenna_fix
        before = rec.get("before")
        after = rec.get("after")
        default_class = "antenna"
    return {
        "design_name": design,
        "platform": rec.get("platform"),
        "check_type": "drc",
        "violation_class": rec.get("violation_class") or default_class,
        "from_stage": rec.get("from_stage"),
        "before_count": before,
        "after_count": after,
        "before_status": rec.get("before_status"),
        "after_status": rec.get("status"),
        "elapsed_s": rec.get("wall_s"),
        "strategy": ("antenna_diode_repair" if prefix == "antenna_fix"
                     else "beol_only_drc"),
    }


def _parse_orfs_record(rec: dict, prefix: str) -> dict | None:
    """retry_pass* / recover_pass* / orfs_retry -> a normalized ORFS fix-event dict.

    These have no violation counts. `orfs == "pass"` means the run closed; we
    encode that as a cleared transition (after_count == 0). A non-pass orfs is a
    no-change (after_count == before_count == 1, i.e. still failing).
    """
    # recover_pass* records carry only `case`; fall back to it for the design id.
    design = rec.get("design") or rec.get("case")
    if not design:
        return None
    orfs = rec.get("orfs")
    closed = (orfs == "pass")
    from_stage = rec.get("from_stage")
    return {
        "design_name": design,
        "platform": rec.get("platform"),
        "check_type": "orfs",
        "violation_class": from_stage or "full",
        "from_stage": from_stage,
        "before_count": 1,                       # was failing before the rerun
        "after_count": 0 if closed else 1,
        "before_status": "fail",
        "after_status": orfs,
        "elapsed_s": rec.get("elapsed_s"),
        "strategy": "rerun_from_stage",
    }


def _iter_events(batch_dir: Path, families: dict[str, Any]) -> Iterator[dict]:
    """Yield one normalized fix-event dict per recognized historical record."""
    for path in sorted(batch_dir.glob("*.jsonl")):
        prefix = _file_prefix(path.stem)
        if prefix is None:
            continue
        provenance = f"backfill:{path.stem}"
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if prefix in _DRC_PREFIXES:
                    norm = _parse_drc_record(rec, prefix)
                else:
                    norm = _parse_orfs_record(rec, prefix)
                if norm is None:
                    continue
                design = norm["design_name"]
                norm["design_family"] = knowledge_db.infer_family(design, families)
                norm["fix_session_id"] = _session_id(design, path.name)
                norm["verdict"] = _verdict(norm.get("before_count"),
                                           norm.get("after_count"))
                norm["provenance"] = provenance
                yield norm


_INSERT = """
INSERT OR IGNORE INTO fix_events (
    fix_session_id, project_path, design_name, design_family, platform,
    check_type, violation_class, iter, strategy, from_stage,
    before_count, after_count, before_status, after_status, verdict,
    elapsed_s, ts, provenance
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def backfill(batch_dir: Path | str, conn: sqlite3.Connection,
             families: dict[str, Any]) -> int:
    """Backfill synthetic fix_events from `batch_dir`/*.jsonl into `conn`.

    Returns the number of rows actually inserted (INSERT OR IGNORE -> re-running
    a backfill over the same files inserts nothing new).
    """
    batch_dir = Path(batch_dir)
    ts = _now()
    inserted = 0
    for ev in _iter_events(batch_dir, families):
        cur = conn.execute(_INSERT, (
            ev["fix_session_id"], None, ev["design_name"], ev["design_family"],
            ev.get("platform"), ev["check_type"], ev.get("violation_class"),
            0, ev.get("strategy"), ev.get("from_stage"),
            ev.get("before_count"), ev.get("after_count"),
            ev.get("before_status"), ev.get("after_status"), ev["verdict"],
            ev.get("elapsed_s"), ts, ev["provenance"],
        ))
        inserted += cur.rowcount
    conn.commit()
    return inserted


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Backfill historical fix transitions from "
                    "design_cases/_batch/*.jsonl into the knowledge store's "
                    "fix_events table.")
    ap.add_argument("--batch-dir", default="design_cases/_batch",
                    help="directory of historical *.jsonl batch logs")
    ap.add_argument("--db", default=str(knowledge_db.DEFAULT_DB_PATH),
                    help="path to runs.sqlite")
    args = ap.parse_args(argv)

    conn = knowledge_db.connect(args.db)
    knowledge_db.ensure_schema(conn)
    families = knowledge_db.load_families()
    n = backfill(args.batch_dir, conn, families)
    total = conn.execute(
        "SELECT COUNT(*) FROM fix_events WHERE provenance LIKE 'backfill:%'"
    ).fetchone()[0]
    conn.close()
    print(f"backfill: inserted {n} new fix_events from {args.batch_dir} "
          f"({total} backfilled rows total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
