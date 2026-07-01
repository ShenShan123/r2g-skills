#!/usr/bin/env python3
"""Verify BOTH memory DBs (knowledge.sqlite + journal.sqlite) are updated honestly
after every move (a flow, a fix, an A/B arm, a promote/demote, an escalation, an
ingest). One command to answer: "did the loop record what it just did, in BOTH
books, consistently?"

The closed learning loop is only as honest as its weakest writer, and the two DBs
have DISTINCT roles + DISTINCT truth-status, so they get DISTINCT severities:

  * knowledge.sqlite = what RESULTED (runs / failure_events / ab_trials /
    recipe_status / escalations). It is the SOURCE OF TRUTH and the SOLE learner
    input. If it is internally dishonest, the loop silently lies -> ALARM (hard).
  * journal.sqlite   = what was DONE (actions / log_summaries / tool_bugs), each
    row's run_id back-filled to the knowledge run minted at ingest. It is a
    best-effort, lossy, gitignored decision LEDGER. A move it failed to record is
    a forensic gap, not a corrupted lesson -> WARN (trend).

So this tool does NOT re-implement the knowledge-side honesty gates: it IMPORTS and
runs `knowledge/honesty.py::run_all` (the same five gates CI runs over the committed
store and tests/test_honesty_invariants.py asserts) so the knowledge verdict can
NEVER drift from the canonical gate. On top of that it adds the journal-liveness and
the cross-DB linkage/correspondence checks that honesty.py deliberately omits
(honesty.py is journal-free so it can run over a fresh clone with no journal present).

It prints a PASS/WARN/ALARM line per check and exits non-zero iff a HARD invariant
fails, so a wave driver or CI can gate on it. WARNs are leads to chase, not failures.

HARD invariants (ALARM + non-zero exit -- the loop is lying or blind):
  H:*  the five knowledge honesty gates (fail_event_parity, every_fail_has_event,
       no_event_on_nonfail_run, ab_trials_nonempty_when_failures,
       failure_events_derivable) over the WHOLE committed store.
  J1   journal actions table is non-empty (the journal writer is alive).
  J2   no project that has BOTH a knowledge run AND journal actions is left with
       every action run_id NULL -- ingest must back-fill run_id; a fully-unlinked
       project means ingest never connected its ledger to its result.

Trend signals (WARN only -- a best-effort ledger out of step with the truth):
  K3   per-platform: ab_trials exist but promoted==0 with >=3 inconclusive
       = the 2026-06-24 'A/B arms are identical' alarm (subtler than empty ab_trials).
  J3   fix-bearing journal actions (config_knob_delta/sdc_edit/stage_rerun) should
       mostly carry a symptom_id (provenance for the journal->knowledge promoter).
  J4   no journal action's (non-NULL) run_id dangles -- i.e. points at a run that is
       absent from knowledge.runs. Small counts are benign re-ingest residue (run_id
       keys on ppa.json mtime, so a re-ingest re-mints it and orphans old rows); a
       growing count means a writer is stamping run_ids the store can't explain.
  L1   every ab_trials symptom_id has an `ab_launch` action -- the loop journaled
       launching the arms it then judged.
  L2   every `promoted` recipe's symptom_id has a `promote` action -- the promotion
       move is in the ledger, not just the result.
  L3   every OPEN escalation that carries a symptom_id has an `escalate` action.

Usage:
  python3 tools/check_db_integrity.py [--platform nangate45] \
      [--kdb r2g-rtl2gds/knowledge/knowledge.sqlite] \
      [--jdb r2g-rtl2gds/knowledge/journal.sqlite]

Exit: 0 if no ALARM (WARNs allowed), 1 if any ALARM, 2 if the knowledge DB is missing.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEF_KDB = ROOT / "r2g-rtl2gds/knowledge/knowledge.sqlite"
DEF_JDB = ROOT / "r2g-rtl2gds/knowledge/journal.sqlite"
KNOWLEDGE_DIR = ROOT / "r2g-rtl2gds/knowledge"

# Import the canonical knowledge-side honesty gates so this tool's knowledge verdict
# can NEVER drift from the committed-store CI gate / the test that asserts it.
if str(KNOWLEDGE_DIR) not in sys.path:
    sys.path.insert(0, str(KNOWLEDGE_DIR))
import honesty  # noqa: E402

FIX_ACTIONS = ("config_knob_delta", "sdc_edit", "stage_rerun")


def _scalar(con, sql, params=()):
    row = con.execute(sql, params).fetchone()
    return row[0] if row else None


def _check_knowledge_honesty(con, results):
    """HARD: delegate to honesty.run_all -- the same five gates CI runs. Read-only;
    the gates only SELECT, so the read-only connection is fine. Run over the WHOLE
    store (honesty is a global property, never platform-scoped)."""
    _all_ok, report = honesty.run_all(con)
    for r in report:
        lvl = "PASS" if r["ok"] else "ALARM"
        results.append((lvl, "H:" + r["name"], r["detail"]))


def _check_platform_stall(con, results, platform):
    """WARN K3: ab_trials present but promoted==0 with >=3 inconclusive on a platform
    = the subtle 'A/B arms are identical' stall (verify metrics_json diverges)."""
    plats = [platform] if platform else [
        r[0] for r in con.execute(
            "SELECT DISTINCT platform FROM recipe_status WHERE platform IS NOT NULL")]
    for p in plats:
        ab_p = _scalar(con, "SELECT COUNT(*) FROM ab_trials WHERE platform=?", (p,)) or 0
        promo_p = _scalar(
            con, "SELECT COUNT(*) FROM recipe_status WHERE platform=? AND status='promoted'",
            (p,)) or 0
        incon_p = _scalar(
            con, "SELECT COUNT(*) FROM ab_trials WHERE platform=? AND verdict='inconclusive'",
            (p,)) or 0
        if ab_p > 0 and promo_p == 0 and incon_p >= 3:
            results.append(("WARN", "K3",
                            f"{p}: ab_trials={ab_p} but promoted=0 with {incon_p} inconclusive "
                            "-- possible identical-arms stall (verify metrics_json diverges)"))
        else:
            results.append(("PASS", "K3", f"{p}: ab_trials={ab_p} promoted={promo_p}"))


def _check_journal_and_crossdb(con, results, platform):
    """J1/J2 (HARD) + J3/J4/L1/L2/L3 (WARN). `con` already has journal ATTACHed as j."""
    pp = {"p": platform}  # named param; None -> the (:p IS NULL OR ...) clause is a no-op

    # J1 -- journal writer alive.
    n_act = _scalar(con, "SELECT COUNT(*) FROM j.actions") or 0
    if n_act == 0:
        results.append(("ALARM", "J1", "journal actions table is EMPTY -- the journal writer is dead"))
        return  # nothing downstream is meaningful without actions
    n_act_rid = _scalar(con, "SELECT COUNT(*) FROM j.actions WHERE run_id IS NOT NULL") or 0
    results.append(("PASS", "J1", f"journal alive: actions={n_act} ({n_act_rid} run_id-linked)"))

    # J2 -- a project with BOTH a run and journal actions must have >=1 back-filled
    # run_id. A fully-unlinked project = ingest never connected ledger to result.
    run_filter = "WHERE r.platform = :p" if platform else ""
    orphans = _scalar(con, f"""
        SELECT COUNT(*) FROM (
          SELECT DISTINCT r.project_path FROM runs r
          {run_filter}
          {'AND' if platform else 'WHERE'} EXISTS (
              SELECT 1 FROM j.actions a WHERE a.project_path = r.project_path)
          AND NOT EXISTS (
              SELECT 1 FROM j.actions a
              WHERE a.project_path = r.project_path AND a.run_id IS NOT NULL))
    """, pp if platform else {}) or 0
    if orphans == 0:
        results.append(("PASS", "J2",
                        "run_id back-fill intact: no project has a run + actions but no linkage"))
    else:
        results.append(("ALARM", "J2",
                        f"{orphans} project(s) have a knowledge run AND journal actions but "
                        "ZERO back-filled run_id -- ingest did not link the journal to the result"))

    # J3 -- symptom_id provenance on fix-bearing actions (feeds the promoter).
    qmarks = ",".join("?" * len(FIX_ACTIONS))
    n_fixact = _scalar(con,
                       f"SELECT COUNT(*) FROM j.actions WHERE action_type IN ({qmarks})",
                       FIX_ACTIONS) or 0
    if n_fixact > 0:
        n_fixact_sym = _scalar(
            con, f"SELECT COUNT(*) FROM j.actions WHERE action_type IN ({qmarks}) "
                 "AND symptom_id IS NOT NULL", FIX_ACTIONS) or 0
        cov = 100.0 * n_fixact_sym / n_fixact
        lvl = "PASS" if cov >= 50 else "WARN"
        results.append((lvl, "J3",
                        f"fix-action symptom_id provenance: {n_fixact_sym}/{n_fixact} ({cov:.0f}%)"))
    else:
        results.append(("PASS", "J3", "no fix-bearing journal actions yet"))

    # J4 -- cross-DB referential integrity: no non-NULL journal run_id may dangle.
    dangling = _scalar(con, """
        SELECT COUNT(DISTINCT a.run_id) FROM j.actions a
        LEFT JOIN runs r ON r.run_id = a.run_id
        WHERE a.run_id IS NOT NULL AND r.run_id IS NULL""") or 0
    if dangling == 0:
        results.append(("PASS", "J4", "no journal run_id dangles -- every linked action resolves to a run"))
    else:
        results.append(("WARN", "J4",
                        f"{dangling} distinct journal run_id(s) point at a run absent from "
                        "knowledge.runs -- benign re-ingest residue if small/flat, a writer bug if growing"))

    # ---- L1/L2/L3: every knowledge-side MOVE left a journal action -------------
    # Directional (knowledge => journal): the ledger may hold MORE (re-launches,
    # re-promotions) so these are >=-coverage checks, never equalities.
    l1 = _scalar(con, """
        SELECT COUNT(*) FROM (
          SELECT DISTINCT symptom_id FROM ab_trials
          WHERE (:p IS NULL OR platform = :p)
            AND symptom_id NOT IN (
              SELECT symptom_id FROM j.actions
              WHERE action_type='ab_launch' AND symptom_id IS NOT NULL))""", pp) or 0
    ab_syms = _scalar(con,
                      "SELECT COUNT(DISTINCT symptom_id) FROM ab_trials "
                      "WHERE (:p IS NULL OR platform = :p)", pp) or 0
    if l1 == 0:
        results.append(("PASS", "L1", f"every A/B-trial symptom has an ab_launch action ({ab_syms} symptoms)"))
    else:
        results.append(("WARN", "L1",
                        f"{l1}/{ab_syms} A/B-trial symptom(s) have NO ab_launch action -- "
                        "a judged A/B move the ledger never recorded"))

    l2 = _scalar(con, """
        SELECT COUNT(*) FROM (
          SELECT DISTINCT symptom_id FROM recipe_status
          WHERE status='promoted' AND (:p IS NULL OR platform = :p)
            AND symptom_id NOT IN (
              SELECT symptom_id FROM j.actions
              WHERE action_type='promote' AND symptom_id IS NOT NULL))""", pp) or 0
    promo_syms = _scalar(con,
                         "SELECT COUNT(DISTINCT symptom_id) FROM recipe_status "
                         "WHERE status='promoted' AND (:p IS NULL OR platform = :p)", pp) or 0
    if l2 == 0:
        results.append(("PASS", "L2", f"every promoted recipe has a promote action ({promo_syms} symptoms)"))
    else:
        results.append(("WARN", "L2",
                        f"{l2}/{promo_syms} promoted recipe symptom(s) have NO promote action -- "
                        "a promotion that landed in knowledge but not in the ledger"))

    # L3 -- escalations have no platform column, so this one is global by design.
    l3 = _scalar(con, """
        SELECT COUNT(*) FROM (
          SELECT DISTINCT symptom_id FROM escalations
          WHERE status='open' AND symptom_id IS NOT NULL
            AND symptom_id NOT IN (
              SELECT symptom_id FROM j.actions
              WHERE action_type='escalate' AND symptom_id IS NOT NULL))""") or 0
    open_syms = _scalar(con,
                        "SELECT COUNT(DISTINCT symptom_id) FROM escalations "
                        "WHERE status='open' AND symptom_id IS NOT NULL") or 0
    if l3 == 0:
        results.append(("PASS", "L3", f"every open symptom-escalation has an escalate action ({open_syms} symptoms)"))
    else:
        results.append(("WARN", "L3",
                        f"{l3}/{open_syms} open symptom-escalation(s) have NO escalate action"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kdb", type=Path, default=DEF_KDB)
    ap.add_argument("--jdb", type=Path, default=DEF_JDB)
    ap.add_argument("--platform", default=None,
                    help="scope the per-platform trend (K3) and cross-DB correspondence "
                         "(J2/L1/L2) checks to this platform; honesty gates stay global")
    args = ap.parse_args(argv)

    if not args.kdb.exists():
        print(f"ALARM: knowledge DB missing: {args.kdb}", file=sys.stderr)
        return 2

    con = sqlite3.connect(f"file:{args.kdb}?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout = 30000")
    have_journal = args.jdb.exists()
    if have_journal:
        con.execute("ATTACH DATABASE ? AS j", (f"file:{args.jdb}?mode=ro",))

    results = []  # (level, code, message)  level in {PASS, WARN, ALARM}
    _check_knowledge_honesty(con, results)
    _check_platform_stall(con, results, args.platform)
    if have_journal:
        _check_journal_and_crossdb(con, results, args.platform)
    else:
        results.append(("WARN", "J*",
                        f"journal DB absent ({args.jdb}) -- journal is gitignored/machine-local; "
                        "cross-DB linkage + per-move correspondence NOT verified"))

    icon = {"PASS": "ok  ", "WARN": "warn", "ALARM": "ALARM"}
    n_alarm = sum(1 for lvl, _, _ in results if lvl == "ALARM")
    n_warn = sum(1 for lvl, _, _ in results if lvl == "WARN")
    plat_note = f" platform={args.platform}" if args.platform else ""
    print(f"== DB integrity (knowledge + journal){plat_note} ==")
    for lvl, code, msg in results:
        print(f"  [{icon[lvl]}] {code}: {msg}")
    verdict = "ALARM" if n_alarm else ("WARN" if n_warn else "PASS")
    print(f"== verdict: {verdict}  ({n_alarm} alarm, {n_warn} warn, "
          f"{len(results) - n_alarm - n_warn} pass) ==")
    return 1 if n_alarm else 0


if __name__ == "__main__":
    sys.exit(main())
