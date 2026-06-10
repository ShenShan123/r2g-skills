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
