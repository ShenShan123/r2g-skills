#!/usr/bin/env python3
"""Reconcile ab_trials verdicts against the CURRENT judge (2026-06-26 honesty fix).

A trial's verdict is FROZEN at record time. When `judge_repeated`'s math is later
hardened -- e.g. the 2026-06-25 success-tie tiebreak (COST_FLOOR=0.08 + strict
`max(wb) < min(wa)` separation) that stopped wall-clock JITTER from deciding a
tie between two equally-correct arms -- the verdicts recorded under the OLD rule
stay in the corpus. `judge_recipe` counts those frozen strings, so a recipe can
sit `promoted`/`shadow` on evidence the current code would never emit.

Observed on nangate45 (2026-06-26 adversarial audit, both-skeptics high/0.95):
`antenna_diode_repair` (symptom 84ffbb.../logic/unknown) was PROMOTED on
`ab_corpus:3w1l`, and a sibling symptom (2e8098...) DEMOTED to terminal `shadow`
on a single noise LOSS. In every one of those trials BOTH arms had identical
is_success AND identical outcome_score -- they diverged only on wall_s by 2-11s.
Re-running the current `judge_repeated` on the stored samples returns
`inconclusive` for all of them (the arms did NOT do different work).

This tool re-derives each trial's verdict from its stored `metrics_json` via the
current `judge_repeated` -- but ONLY for trials that carry full A_samples /
B_samples (sparse-metric trials keep their stored verdict; honesty: never invent
a verdict from missing data) -- then re-runs `judge_recipe` per affected key.
Because `judge_recipe` leaves an already-promoted/shadow recipe UNCHANGED when
its corpus nets to ZERO decisive evidence (it cannot self-heal -- audit), this
tool also EXPLICITLY reverts such an `ab_corpus`-provenanced recipe back to
`candidate`, so it is re-validated honestly (or coverage-gapped) rather than
resting on a fabricated strength. A REAL divergent win (arm A fails / arm B
signs off -> is_success differs) survives untouched.

Idempotent (a second run flips nothing) and single-transaction. Like
repair_run_status.py it is an operator/loop reconciler over the COMMITTED
knowledge store; it touches only ab_trials.verdict + recipe_status, never runs /
failure_events, so the honesty gates stay green.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # knowledge/ importable

import ab_runner          # noqa: E402
import recipe_lifecycle   # noqa: E402


def _recompute_verdict(metrics_json: str | None, stored: str) -> str:
    """Re-derive a trial's verdict from its stored A_samples/B_samples via the CURRENT
    judge_repeated. A trial without full samples keeps its stored verdict (honesty: never
    invent a verdict from missing data -- e.g. the metrics={} trials some tests record)."""
    try:
        m = json.loads(metrics_json) if metrics_json else {}
    except (TypeError, ValueError):
        return stored
    a, b = m.get("A_samples"), m.get("B_samples")
    if not isinstance(a, list) or not isinstance(b, list) or not a or not b:
        return stored
    return ab_runner.judge_repeated(a, b)


def reconcile(conn, *, dry_run: bool = False) -> dict:
    """Re-judge every ab_trial from its metrics, re-derive recipe_status per affected key,
    and revert any now-evidence-less promoted/shadow recipe to candidate. Returns a summary
    dict (verdicts_flipped, keys_rejudged, reverted_to_candidate)."""
    rows = conn.execute(
        "SELECT trial_id, symptom_id, design_class, platform, strategy, verdict, "
        "metrics_json FROM ab_trials").fetchall()
    flipped, affected = [], set()
    for tid, sym, dc, plat, strat, verdict, mj in rows:
        nv = _recompute_verdict(mj, verdict)
        if nv != verdict:
            flipped.append({"trial_id": tid, "from": verdict, "to": nv,
                            "key": (sym, dc, plat, strat)})
            affected.add((sym, dc, plat, strat))
            if not dry_run:
                conn.execute("UPDATE ab_trials SET verdict=? WHERE trial_id=?", (nv, tid))
    rejudged, reverted = [], []
    if not dry_run:
        for sym, dc, plat, strat in sorted(affected):
            key = dict(symptom_id=sym, design_class=dc, platform=plat, strategy=strat)
            d = conn.execute(
                "SELECT SUM(verdict='win'), SUM(verdict='loss') FROM ab_trials WHERE "
                "symptom_id=? AND design_class=? AND platform=? AND strategy=?",
                (sym, dc, plat, strat)).fetchone()
            wins, losses = d[0] or 0, d[1] or 0
            transition = ab_runner.judge_recipe(conn, **key)
            if transition:
                rejudged.append({"key": key, "to": transition})
            # judge_recipe returns None (status UNCHANGED) when the corpus nets to zero
            # decisive evidence, so a recipe promoted/demoted under the old judge stays put.
            # Revert it: its A/B strength was fabricated, so re-validate it as a candidate.
            prov_row = conn.execute(
                "SELECT provenance FROM recipe_status WHERE symptom_id=? AND design_class=? "
                "AND platform=? AND strategy=?", (sym, dc, plat, strat)).fetchone()
            prov = (prov_row[0] if prov_row else "") or ""
            status = recipe_lifecycle.get_status(conn, **key)
            if (wins == 0 and losses == 0 and status in ("promoted", "shadow")
                    and prov.startswith("ab_corpus")):
                recipe_lifecycle._set(conn, "candidate",
                                      "reconcile:no_decisive_evidence", **key)
                reverted.append({"key": key, "from": status})
        conn.commit()
    return {"trials_scanned": len(rows), "verdicts_flipped": flipped,
            "keys_rejudged": rejudged, "reverted_to_candidate": reverted}


def main(argv=None) -> int:
    import argparse
    import knowledge_db
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None, help="knowledge.sqlite (default: autodetect)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what WOULD change without writing")
    args = ap.parse_args(argv)
    conn = knowledge_db.connect(args.db) if args.db else knowledge_db.connect()
    knowledge_db.ensure_schema(conn)
    out = reconcile(conn, dry_run=args.dry_run)
    tag = " (DRY RUN)" if args.dry_run else ""
    print(f"trials_scanned={out['trials_scanned']} "
          f"verdicts_flipped={len(out['verdicts_flipped'])} "
          f"keys_rejudged={len(out['keys_rejudged'])} "
          f"reverted_to_candidate={len(out['reverted_to_candidate'])}{tag}")
    for f in out["verdicts_flipped"]:
        print(f"  trial {f['trial_id']}: {f['from']} -> {f['to']}  "
              f"{'/'.join(str(x) for x in f['key'])}")
    for r in out["reverted_to_candidate"]:
        k = r["key"]
        print(f"  REVERT {r['from']} -> candidate: {k['strategy']} "
              f"{k['design_class']}/{k['platform']} sym={k['symptom_id'][:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
