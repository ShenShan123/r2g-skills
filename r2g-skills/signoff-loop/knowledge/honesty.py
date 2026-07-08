#!/usr/bin/env python3
"""Executable honesty gates for the knowledge store — IMPORTABLE, not test-only.

This is the single home of the CLAUDE.md "Fast honesty check" + knowledge/README.md
invariants (~20-21), refactored out of tests/test_honesty_invariants.py so that
PRODUCTION code can run them too:

  * ``knowledge_sync.merge`` runs them AFTER a cross-operator merge and REFUSES
    (rolls back) a merge that would break an invariant — a dishonest merge never
    lands in the local store.
  * the CI gate (``python3 knowledge/honesty.py --db knowledge.sqlite``) runs them
    over the REAL committed store, per plan §1.3 ("run the honesty invariants
    against a real store snapshot, not just mocks").
  * tests/test_honesty_invariants.py imports these same functions, so the gate the
    test asserts and the gate production runs can NEVER drift (mirrors README
    invariant 6: one predicate, many callers).

Each check returns ``(ok: bool, detail: str)`` and reads ONLY knowledge.sqlite via
raw sqlite (no learner code, no journal.sqlite) so it is dependency-light and safe
to call from any context, including a half-built merge transaction.

The five HARD gates (a breach means the loop is silently lying):

  H3            count(runs orfs_status='fail') == count of those carrying a matching
                'orfs-fail-%' failure_event           (check_fail_event_parity)
  H3-coverage   every 'fail' run carries >=1 failure_event, offender NAMED
                (check_every_fail_has_event)
  H3-inverse    NO 'orfs-fail-%' event sits on a non-'fail' run (the merge-introduced
                fail/partial conflation)              (check_no_event_on_nonfail_run)
  Gate-A        ab_trials non-empty whenever any fail/partial run exists
                (check_ab_trials_nonempty_when_failures)
  Derivability  every 'orfs-fail-<stage>[-<code>]' signature names the stage the run
                aborted on                            (check_failure_events_derivable)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_KNOWLEDGE_DIR = Path(__file__).resolve().parent
if str(_KNOWLEDGE_DIR) not in sys.path:
    sys.path.insert(0, str(_KNOWLEDGE_DIR))
import knowledge_db  # noqa: E402


# ── honesty checks (reusable; dependency-light — raw sqlite, no learner code) ──
def check_fail_event_parity(conn: sqlite3.Connection) -> tuple[bool, str]:
    """H3: count(runs orfs_status='fail') == count(distinct fail runs carrying an
    'orfs-fail-%' failure_event). A fail run with no event = the learner is blind to
    the whole backend-failure class."""
    n_fail = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE orfs_status = 'fail'").fetchone()[0]
    n_with_event = conn.execute(
        "SELECT COUNT(DISTINCT r.run_id) FROM runs r "
        "JOIN failure_events f ON f.run_id = r.run_id "
        "WHERE r.orfs_status = 'fail' AND f.signature LIKE 'orfs-fail-%'"
    ).fetchone()[0]
    ok = n_fail == n_with_event
    return ok, f"fail_runs={n_fail} fail_runs_with_event={n_with_event}"


def check_every_fail_has_event(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Per-run complement to H3: every 'fail' run must carry >=1 failure_event, and
    the offender is NAMED so a red CI run points at the exact lying row.

    SCOPE = orfs_status='fail' ONLY — mirrors the ingest projection contract
    (failure_events written iff orfs_status=='fail' AND fail_stage). A 'partial' run
    is the HONEST incomplete state (no stage reported 'fail'); requiring an event
    there would FABRICATE a backend failure that never happened."""
    rows = conn.execute(
        "SELECT r.run_id FROM runs r "
        "WHERE r.orfs_status = 'fail' "
        "AND NOT EXISTS (SELECT 1 FROM failure_events f WHERE f.run_id = r.run_id)"
    ).fetchall()
    missing = [r[0] for r in rows]
    return (not missing), f"fail_without_event={missing}"


def check_ab_trials_nonempty_when_failures(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Gate-A (README inv 20): once the corpus has any fail/partial run, ab_trials
    must be non-empty — an empty ab_trials alongside fail/partial rows means the A/B
    loop is inert and lying. No fail/partial rows -> vacuously OK."""
    n_failpartial = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE orfs_status IN ('fail', 'partial')"
    ).fetchone()[0]
    n_trials = conn.execute("SELECT COUNT(*) FROM ab_trials").fetchone()[0]
    ok = (n_failpartial == 0) or (n_trials > 0)
    return ok, f"failpartial_runs={n_failpartial} ab_trials={n_trials}"


def _signature_stage(signature: str) -> str | None:
    """Stage named by an 'orfs-fail-<stage>[-<code>]' signature. Handles BOTH the
    bare form ('orfs-fail-route') and the coded form ('orfs-fail-place-DPL-0036')."""
    if not signature.startswith("orfs-fail-"):
        return None
    rest = signature[len("orfs-fail-"):]
    return rest.split("-", 1)[0] if rest else None


def check_no_event_on_nonfail_run(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Inverse of check_every_fail_has_event (the direction the original four gates
    NEVER policed): an 'orfs-fail-%' failure_event — the backend-abort projection — may
    sit ONLY on a run whose orfs_status='fail'. An orfs-fail event on a 'partial'/'pass'/
    NULL run is the fail/partial CONFLATION CLAUDE.md forbids ('partial = honest
    incomplete; events would fabricate failures'). This is reachable via the MERGE path:
    a run_id collision keeps a local 'partial' run (local wins) yet adds the bundle's
    'fail' event, leaving an abort signature on a run that, per orfs_status, did not
    fail. Scoped to 'orfs-fail-%' ONLY — diagnosis events ('synthesis_errors',
    'unconstrained_timing') legitimately annotate pass/partial runs and are out of
    scope. (Added 2026-06-23 after the knowledge_sync merge review found the hole.)"""
    rows = conn.execute(
        "SELECT f.run_id, f.signature, r.orfs_status FROM failure_events f "
        "JOIN runs r ON r.run_id = f.run_id "
        "WHERE f.signature LIKE 'orfs-fail-%' "
        "AND (r.orfs_status IS NULL OR r.orfs_status != 'fail')"
    ).fetchall()
    bad = [(rid, sig, st) for rid, sig, st in rows]
    return (not bad), f"event_on_nonfail_run={bad}"


def check_failure_events_derivable(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Derivability: failure_events are a derived projection of orfs_fail_stage, so an
    'orfs-fail-<stage>[-<code>]' signature must name the stage its run aborted on. A
    signature whose <stage> disagrees with runs.orfs_fail_stage is a desync (a repair
    tool that wrote one column but not the projected event)."""
    rows = conn.execute(
        "SELECT f.run_id, f.signature, r.orfs_fail_stage "
        "FROM failure_events f JOIN runs r ON r.run_id = f.run_id "
        "WHERE f.signature LIKE 'orfs-fail-%'"
    ).fetchall()
    bad = []
    for run_id, signature, fail_stage in rows:
        sig_stage = _signature_stage(signature)
        if sig_stage is None or fail_stage is None or sig_stage != fail_stage:
            bad.append((run_id, signature, fail_stage))
    return (not bad), f"stage_mismatch={bad}"


# The HARD gates, in report order. Each entry: (name, fn). All four are blocking;
# a False from any of them means the store is lying and the merge/CI must fail.
HARD_CHECKS = (
    ("fail_event_parity", check_fail_event_parity),
    ("every_fail_has_event", check_every_fail_has_event),
    ("no_event_on_nonfail_run", check_no_event_on_nonfail_run),
    ("ab_trials_nonempty_when_failures", check_ab_trials_nonempty_when_failures),
    ("failure_events_derivable", check_failure_events_derivable),
)


def run_all(conn: sqlite3.Connection) -> tuple[bool, list[dict]]:
    """Run every HARD honesty gate over `conn`. Returns (all_ok, report) where
    report is a list of {name, ok, detail} in deterministic HARD_CHECKS order.
    Pure read-only; safe to call inside an open (uncommitted) merge transaction."""
    report: list[dict] = []
    all_ok = True
    for name, fn in HARD_CHECKS:
        ok, detail = fn(conn)
        all_ok = all_ok and ok
        report.append({"name": name, "ok": ok, "detail": detail})
    return all_ok, report


def format_report(report: list[dict]) -> str:
    lines = []
    for r in report:
        mark = "OK  " if r["ok"] else "FAIL"
        lines.append(f"  [{mark}] {r['name']}: {r['detail']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH,
                   help="knowledge.sqlite to audit (default: the shipped store)")
    args = p.parse_args(argv)

    conn = knowledge_db.connect(args.db)
    try:
        all_ok, report = run_all(conn)
    finally:
        conn.close()

    print(f"Honesty gates over {args.db}:")
    print(format_report(report))
    if all_ok:
        print("ALL HONESTY GATES GREEN.")
        return 0
    print("HONESTY BREACH — the store is silently lying. See FAIL rows above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
