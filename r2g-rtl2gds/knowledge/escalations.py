#!/usr/bin/env python3
"""Escalation queue API (engineer-loop spec §5.5). The loop opens; the agent
tier drains (see references/engineer-loop.md). Dedup: one OPEN escalation per
(design, reason) — repeats refresh nothing (the original already says it all).
"""
from __future__ import annotations

import datetime as _dt
import os as _os

REASONS = ("unknown_symptom", "catalog_exhausted", "unseen_crash",
           "repeated_regression",
           # A backend route abort whose route_relief fixer is exhausted (util at
           # floor) or inapplicable (DIE_AREA-sized, no CORE_UTILIZATION knob).
           # A KNOWN, recipe-backed residual — NOT an unseen crash (2026-06-17).
           "route_congestion_residual",
           # An A/B route arm whose flow produced NO backend (clone/setup aborted
           # before any stage ran): the arm cannot be judged, so it is escalated
           # rather than ingested as a junk orfs_status='unknown' row that would
           # poison the verdict (2026-06-23 audit, bug #3).
           "route_arm_incomplete",
           # A learner-enqueued A/B candidate whose Gate-B is structurally
           # unreachable: fewer than n_ab_designs resolvable on-disk subjects, so
           # plan_trial returns None forever. Surfaced (not silently skipped) so a
           # genuinely-good recipe stuck as 'candidate' is visible (2026-06-23
           # audit, bug #8). Left 'candidate' so a later drain auto-retries when
           # the corpus regrows — never demoted (demotion is terminal).
           "unvalidatable_insufficient_subjects",
           # An A/B candidate whose arms CANNOT diverge: a no-op strategy
           # (lvs_resolve_unknown), or a candidate that has accrued AB_INCONCLUSIVE_MAX
           # inconclusive trials with zero decisive verdicts. Planning it only burns a
           # full signoff per repeat for a guaranteed-inconclusive verdict. Skipped +
           # surfaced, left 'candidate' (inconclusive is non-terminal) (2026-06-24).
           "ab_coverage_gap")


def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _journal_escalate(*, design: str, project_path: str, reason: str,
                      symptom_id: str | None, notes: str | None) -> None:
    """Best-effort Tier-B3 journal of an escalation DECISION. ADVISORY only — the
    knowledge.sqlite escalations row is the source of truth; honors R2G_JOURNAL and
    never raises (a telemetry failure must not block opening the escalation)."""
    if _os.environ.get("R2G_JOURNAL", "1") == "0":
        return
    try:
        import journal_db
        conn = journal_db.connect(
            _os.environ.get("R2G_JOURNAL_DB") or journal_db.DEFAULT_JOURNAL_PATH)
        journal_db.ensure_schema(conn)
        journal_db.append_action(
            conn, project_path=project_path or "", actor="loop",
            action_type="escalate", design=design, symptom_id=symptom_id,
            payload={"reason": reason, "symptom_id": symptom_id, "notes": notes})
        conn.close()
    except Exception:
        pass


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
    _journal_escalate(design=design, project_path=project_path, reason=reason,
                      symptom_id=symptom_id, notes=notes)   # advisory (Tier B3)
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


def resolve_for_design(conn, design: str, *, notes: str | None = None) -> int:
    """Auto-close every OPEN escalation for `design` as 'drained'. Called when the
    loop drives a design to `clean` (a later successful flow/fix supersedes an
    earlier abort), so the escalation queue stays an honest view of what is still
    stuck — not a graveyard of stale aborts a subsequent run already cleared
    (2026-06-17). Returns the number of escalations closed. No-op if none open."""
    rows = conn.execute(
        "SELECT escalation_id FROM escalations WHERE design=? AND status='open'",
        (design,)).fetchall()
    for (eid,) in rows:
        conn.execute(
            "UPDATE escalations SET status='drained', "
            "notes=COALESCE(?, notes), resolved_at=? WHERE escalation_id=?",
            (notes, _now(), eid))
    conn.commit()
    return len(rows)
