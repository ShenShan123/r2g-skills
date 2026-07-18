#!/usr/bin/env python3
"""Escalation queue API (engineer-loop spec §5.5). The loop opens; the agent
tier drains (see references/engineer-loop.md). Dedup: one OPEN escalation per
(design, reason) — repeats refresh nothing (the original already says it all).
"""
from __future__ import annotations

import os as _os
from knowledge_db import now_local as _now  # invariant 32: the ONE stamp

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
           "ab_coverage_gap",
           # A place backend abort that survived the auto die-resize retry: FLW-0024
           # (cells exceed even the auto-sized die) or PPL-0024 (IO pins exceed even the
           # enlarged perimeter). KNOWN, recipe-backed residuals — NOT unseen crashes; the
           # die lever (CORE_UTILIZATION) is exhausted (place_density_residual was emitted
           # by process_one since 2026-06-23 but never registered here — a latent crash on
           # the rare FLW-0024 residual; pin_overflow_residual added 2026-06-26).
           "place_density_residual", "pin_overflow_residual",
           # A synth abort whose synth_memory_relax recovery (raise SYNTH_MEMORY_MAX_BITS +
           # re-flow) could NOT clear it — the memory is too large to FF-expand and needs a
           # fakeram hard macro (engineer_loop.py process_one, 2026-06-28). KNOWN, recipe-backed
           # residual — NOT an unseen crash. Emitted by the loop since 2026-06-28 but never
           # registered here → open_escalation raised ValueError → the worker CRASHED and the
           # design was mislabeled `worker_exc:ValueError`, burying the honest reason (the EXACT
           # latent-crash class documented for place_density_residual above; fixed 2026-06-30).
           "synth_memory_residual",
           # A floorplan PDN abort (PDN-0185: the die is too NARROW to lay sky130hd's
           # met4/met5 power straps — a tiny CORE_UTILIZATION-auto-sized die is ~27um but a
           # strap set needs ~28.8um, so pdngen aborts REGARDLESS of utilization). RECOVERABLE
           # by flooring the die to an explicit PDN-feasible size (engineer_loop.py process_one,
           # 2026-07-01 sky130 round); a residual survives only when that floor itself cannot
           # lay straps. KNOWN, recipe-backed — NOT an unseen crash. MUST be registered here or
           # open_escalation raises ValueError and crashes the worker (the exact latent-crash
           # class the synth_memory_residual note above documents).
           "pdn_strap_residual",
           # A synth abort from an unresolved `include -- the harvested RTL is INCOMPLETE (a
           # header was never shipped upstream), NOT a crash and NOT a novel symptom. Emitted
           # by process_one (_is_synth_missing_header, engineer_loop.py) but never registered
           # here -> open_escalation raised ValueError -> the worker CRASHED and 24 real designs
           # (PYGMY_V32I/RISC_V/RISCV_Tang_E203/I2SRV32/MS_DMAC) were mislabeled
           # `worker_exc:ValueError` in the 2026-07-02 sky130 round, burying the honest reason
           # (the EXACT latent-crash class documented for synth_memory_residual / pdn_strap_residual
           # above -- the fifth repeat of this bug; see the systemic test guard added with this fix).
           "incomplete_missing_header",
           # A yosys synth abort that hit the run_orfs.sh wrapper wall-clock timeout -- a large
           # design, not a crash. Honest reason (routes to the ORFS_TIMEOUT / simplification
           # runbook). ALSO emitted by process_one but unregistered -> the same latent worker
           # crash waiting to fire on the next synth-timeout design (fixed alongside
           # incomplete_missing_header, 2026-07-02).
           "synth_timeout",
           # A post-fix signoff residual whose DRC/LVS scan never FINISHED (status='stuck' --
           # the documented big-die / KLayout-stuck pattern): diagnose STOPs before any
           # strategy runs, so this is NOT an exhausted catalog. Routed separately
           # (_signoff_escalation_reason, 2026-07-05) so the queue's triage reads "scan
           # bound / die size", not "tried everything". 13 of 37 catalog_exhausted
           # escalations in the sky130 round were actually this class.
           "signoff_stuck_scan",
           # A crash at the CTS stage -- commonly a TritonCTS initOneClockTree segfault on a
           # pathological clock structure (2026-07-12 i2c_master). A TOOL crash, not a
           # flow-config abort the loop can fix, but a RECOGNIZABLE class, not an "unseen"
           # mystery -- labeled honestly by process_one (failure-patterns.md #41). MUST be
           # registered here or open_escalation raises ValueError and crashes the worker (the
           # exact latent-crash class the residual notes above document -- see the systemic
           # test_all_loop_emitted_reasons_are_registered guard).
           "cts_crash",
           # A cross-check repair CYCLE: the design revisited a prior global signoff state
           # (a DRC<->timing ping-pong across check phases that check-local dead evidence
           # cannot see — P1-18, 2026-07-15). Surfaced by process_one so the operator stops
           # spending full-flow compute alternating between locally-successful repairs.
           "repair_cycle_nonconverged")


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
