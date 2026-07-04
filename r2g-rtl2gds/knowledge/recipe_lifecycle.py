#!/usr/bin/env python3
"""Recipe lifecycle: candidate <-> promoted/shadow (engineer-loop §5.3).

Only PROMOTED recipes affect live ranking. New/changed recipes from a learn
rebuild are authored directly as 'candidate' (Gate-A diff_and_enqueue /
enqueue_candidate) and queued for inline A/B (ab_runner). Status is then a
function of the recipe's FULL ab_trials corpus (ab_runner.judge_recipe,
2026-06-24): net-positive DECISIVE (win/loss) evidence promotes, net-negative
demotes to 'shadow', and an `inconclusive` carries no information — it never
demotes (a candidate stays candidate and is re-planned; a later win can revive a
shadow). Agent-authored strategies may enter as shadow via stage_shadow — NO
special trust (decision 7). Absent row = promoted (grandfathering bootstrap for
pre-lifecycle learned recipes).
"""
from __future__ import annotations

import datetime as _dt
import json

GRANDFATHERED = "promoted"   # absent-row default

# Strategies whose A/B arms CANNOT diverge (no real edit applied): arm A and arm B
# do byte-identical work, so every trial is guaranteed-inconclusive and burns a full
# signoff per repeat for zero information. Canonical home is HERE (the lifecycle owns
# what may enter the candidate queue); engineer_loop aliases it for its plan-time
# coverage guard. lvs_resolve_unknown re-inspects with config_edits={} (a no-op).
# Enqueue refuses these at the source (2026-07-04: 4 such rows sat as eternal
# 'candidate', re-skipped every drain); park_nondivergent() heals rows that predate
# the filter. 'parked' is NON-terminal bookkeeping, not a demotion — it only means
# "not validatable by the A/B harness"; pending_candidates/filter_promoted ignore it
# exactly like 'shadow'.
NONDIVERGENT_STRATEGIES = frozenset({"lvs_resolve_unknown"})


def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _iter_keys(heur: dict):
    for sid, classes in (heur.get("recipes") or {}).items():
        for dclass, plats in classes.items():
            if dclass == "*":
                continue           # rollups are views, not lifecycle keys
            for plat, node in plats.items():
                if plat == "*":
                    continue
                for strat, stats in (node.get("strategies") or {}).items():
                    yield (sid, dclass, plat, strat), stats


def diff_and_enqueue(conn, heur: dict, *, prev: dict | None) -> list[tuple]:
    """Compare current vs previous heuristics recipes; mark new/changed
    strategy entries 'candidate' (unless already candidate/promoted/shadow from
    an earlier identical diff). Returns the enqueued keys."""
    prev_stats = dict(_iter_keys(prev or {}))
    enqueued: list[tuple] = []
    for key, stats in _iter_keys(heur):
        if prev_stats.get(key) == stats:
            continue
        if key[3] in NONDIVERGENT_STRATEGIES:
            continue           # guaranteed-inconclusive: never enters the A/B queue
        row = conn.execute(
            "SELECT status FROM recipe_status WHERE symptom_id=? AND "
            "design_class=? AND platform=? AND strategy=?", key).fetchone()
        if row is not None:
            continue               # already in lifecycle — A/B verdict owns it
        conn.execute(
            "INSERT INTO recipe_status (symptom_id, design_class, platform, "
            "strategy, status, provenance, generation, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (*key, "candidate", "learner_diff", heur.get("generation"), _now()))
        enqueued.append(key)
    conn.commit()
    return enqueued


def enqueue_candidate(conn, *, provenance: str = "manual_revalidate",
                      **key) -> bool:
    """Force a (possibly grandfathered) recipe into 'candidate' for an A/B
    re-validation. Unlike diff_and_enqueue, this does NOT require the recipe to be
    new/changed vs a prior heuristics — it is the operator's explicit "validate
    THIS recipe" button, needed because the existing corpus's recipes are all
    grandfathered (absent row == promoted) so nothing would auto-enqueue.

    Returns True iff a new candidate row was created; a no-op (returns False) when
    the recipe is already in the lifecycle — never clobbers an existing verdict.
    """
    if key["strategy"] in NONDIVERGENT_STRATEGIES:
        return False           # guaranteed-inconclusive: never enters the A/B queue
    row = conn.execute(
        "SELECT status FROM recipe_status WHERE symptom_id=? AND design_class=? "
        "AND platform=? AND strategy=?",
        (key["symptom_id"], key["design_class"], key["platform"],
         key["strategy"])).fetchone()
    if row is not None:
        return False
    conn.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy, "
        "status, provenance, updated_at) VALUES (?,?,?,?,?,?,?)",
        (key["symptom_id"], key["design_class"], key["platform"],
         key["strategy"], "candidate", provenance, _now()))
    conn.commit()
    return True


def get_status(conn, *, symptom_id, design_class, platform, strategy) -> str:
    row = conn.execute(
        "SELECT status FROM recipe_status WHERE symptom_id=? AND design_class=?"
        " AND platform=? AND strategy=?",
        (symptom_id, design_class, platform, strategy)).fetchone()
    return row[0] if row else GRANDFATHERED


def _set(conn, status, provenance, *, symptom_id, design_class, platform,
         strategy) -> None:
    conn.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy,"
        " status, provenance, updated_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(symptom_id, design_class, platform, strategy) DO UPDATE SET"
        " status=excluded.status, provenance=excluded.provenance,"
        " updated_at=excluded.updated_at",
        (symptom_id, design_class, platform, strategy, status, provenance, _now()))
    conn.commit()


def promote(conn, *, evidence: str, **key) -> None:
    _set(conn, "promoted", evidence, **key)


def demote(conn, *, reason: str, **key) -> None:
    _set(conn, "shadow", reason, **key)


def stage_shadow(conn, *, provenance: str, **key) -> None:
    """Agent-authored strategy entry point (decision 7): outside the live pool
    until its A/B win."""
    _set(conn, "shadow", provenance, **key)


def filter_promoted(conn, recipe_entry: dict | None, *, symptom_id: str,
                    design_class: str, platform: str) -> dict | None:
    """Strip non-promoted strategies from a recipe entry before live ranking."""
    if not recipe_entry:
        return recipe_entry
    kept = {s: v for s, v in (recipe_entry.get("strategies") or {}).items()
            if get_status(conn, symptom_id=symptom_id, design_class=design_class,
                          platform=platform, strategy=s) == "promoted"}
    out = dict(recipe_entry)
    out["strategies"] = kept
    return out


def park_nondivergent(conn) -> int:
    """One-shot heal: move pre-filter NONDIVERGENT candidate rows out of the work
    queue to 'parked' (self-healing on any operator's DB — plan_arms calls this at
    the top of every drain, so a store that predates the enqueue filter converges
    without a manual migration). NOT a demotion: 'parked' records only that the
    A/B harness cannot differentiate the strategy's arms; the strategy itself stays
    in the static catalog. Returns the number of rows parked."""
    if not NONDIVERGENT_STRATEGIES:
        return 0
    ph = ",".join("?" for _ in NONDIVERGENT_STRATEGIES)
    cur = conn.execute(
        f"UPDATE recipe_status SET status='parked', "
        f"provenance='nondivergent_no_real_edit', updated_at=? "
        f"WHERE status='candidate' AND strategy IN ({ph})",
        (_now(), *sorted(NONDIVERGENT_STRATEGIES)))
    conn.commit()
    return cur.rowcount


def pending_candidates(conn) -> list[dict]:
    """All candidate rows awaiting an A/B trial (ab_runner's work queue)."""
    cur = conn.execute(
        "SELECT symptom_id, design_class, platform, strategy FROM recipe_status"
        " WHERE status='candidate' ORDER BY updated_at")
    return [dict(zip(("symptom_id", "design_class", "platform", "strategy"), r))
            for r in cur.fetchall()]
