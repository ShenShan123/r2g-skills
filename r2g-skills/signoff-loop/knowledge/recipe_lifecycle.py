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

GRANDFATHERED = "promoted"   # absent-row default for the STATIC cold-start path
# Fail-closed marker for the LEARNED (indexed-recipe) path: a strategy present in
# heuristics.json with NO recipe_status row means its candidate enqueue never landed
# (a crashed/partial learn rebuild) — so it is UNVALIDATED, not grandfathered-live
# (P0-2, failure-patterns #48, 2026-07-14). filter_promoted treats it as non-promoted.
UNROSTERED = "unrostered"

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
        # SYSTEM-LOCAL time with numeric offset (2026-07-04, operator request) —
    # replaces utcnow()+"Z". Readers must compare timestamps via julianday()
    # (parses both regimes), never lexicographically.
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


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
            "strategy, status, provenance, generation, status_version, updated_at) "
            "VALUES (?,?,?,?,?,?,?,1,?)",
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
        "status, provenance, status_version, updated_at) VALUES (?,?,?,?,?,?,1,?)",
        (key["symptom_id"], key["design_class"], key["platform"],
         key["strategy"], "candidate", provenance, _now()))
    conn.commit()
    return True


def get_status(conn, *, symptom_id, design_class, platform, strategy,
               default: str = GRANDFATHERED) -> str:
    """Lifecycle status for a recipe key. `default` is returned when NO row exists:
    GRANDFATHERED ('promoted') for the STATIC cold-start path (an un-A/B'd baseline
    strategy is legitimately allowed to run on a novel symptom), UNROSTERED for the
    LEARNED indexed-recipe path (filter_promoted passes it to FAIL CLOSED — P0-2)."""
    row = conn.execute(
        "SELECT status FROM recipe_status WHERE symptom_id=? AND design_class=?"
        " AND platform=? AND strategy=?",
        (symptom_id, design_class, platform, strategy)).fetchone()
    return row[0] if row else default


def _set(conn, status, provenance, *, symptom_id, design_class, platform,
         strategy) -> None:
    # status_version bumps on EVERY transition (2026-07-16 issue 6): `generation`
    # never moved on promote/demote, so a demotion between an A/B trial's plan and
    # its judge was invisible to the staleness guard (engineer_loop stamps the
    # planned version on each arm and cancels the trial when it moved). COALESCE
    # keeps the bump correct on legacy NULL-version rows.
    conn.execute(
        "INSERT INTO recipe_status (symptom_id, design_class, platform, strategy,"
        " status, provenance, status_version, updated_at) VALUES (?,?,?,?,?,?,1,?) "
        "ON CONFLICT(symptom_id, design_class, platform, strategy) DO UPDATE SET"
        " status=excluded.status, provenance=excluded.provenance,"
        " status_version=COALESCE(recipe_status.status_version,0)+1,"
        " updated_at=excluded.updated_at",
        (symptom_id, design_class, platform, strategy, status, provenance, _now()))
    conn.commit()


def get_status_version(conn, *, symptom_id, design_class, platform,
                       strategy) -> int | None:
    """Current status_version for a recipe key; None when the row is absent or a
    legacy row predates versioning. The A/B plan/judge staleness handshake."""
    row = conn.execute(
        "SELECT status_version FROM recipe_status WHERE symptom_id=? AND "
        "design_class=? AND platform=? AND strategy=?",
        (symptom_id, design_class, platform, strategy)).fetchone()
    return row[0] if row else None


def promote(conn, *, evidence: str, **key) -> None:
    _set(conn, "promoted", evidence, **key)


def demote(conn, *, reason: str, **key) -> None:
    _set(conn, "shadow", reason, **key)


def stage_shadow(conn, *, provenance: str, **key) -> None:
    """Agent-authored strategy entry point (decision 7): outside the live pool
    until its A/B win."""
    _set(conn, "shadow", provenance, **key)


def revalidate(conn, *, reason: str, **key) -> None:
    """Force a recipe back to 'candidate' — the DETERMINISTIC state for TIED
    decisive A/B evidence (2026-07-16 agent-logic issue 2). judge_recipe used to
    return None on a tie, leaving whatever transient promote/demote the FIRST
    decisive row had set — the lifecycle was a function of trial ORDER, not of
    the corpus (win-then-loss stayed promoted; loss-then-win stayed shadow).
    Tied evidence is UNRESOLVED: the recipe re-enters the A/B queue and is
    re-planned next drain; it must never inherit an order-dependent state.
    Unlike enqueue_candidate this intentionally OVERWRITES an existing row —
    the corpus-aggregate judge is the caller and owns the transition."""
    _set(conn, "candidate", reason, **key)


def park(conn, *, reason: str = "nondivergent_no_real_edit", **key) -> None:
    """Move a candidate to 'parked' — NON-terminal bookkeeping meaning the A/B harness
    cannot differentiate its arms (no real edit divergence), so it should not be planned.
    Used for a candidate whose strategy has NO application path (P0-6, 2026-07-15): its
    arms would be byte-identical and every trial a guaranteed-inconclusive no-op.
    pending_candidates/filter_promoted ignore 'parked' exactly like 'shadow'."""
    _set(conn, "parked", reason, **key)


def filter_promoted(conn, recipe_entry: dict | None, *, symptom_id: str,
                    design_class: str, platform: str) -> dict | None:
    """Strip non-promoted strategies from a LEARNED (indexed) recipe entry before live
    ranking. FAIL-CLOSED on an absent row (P0-2, failure-patterns #48, 2026-07-14):
    every learner recipe is enqueued as a candidate by diff_and_enqueue + covered by
    ensure_rostered, so a strategy present in heuristics with NO recipe_status row means
    its enqueue never completed (a crashed/partial learn) — it is UNVALIDATED and must
    not be live-trusted. Passing default=UNROSTERED (not the cold-start GRANDFATHERED)
    is what makes the absent row strip here, closing the old fail-open where a
    heuristics-written-but-never-enqueued recipe ranked as if promoted."""
    if not recipe_entry:
        return recipe_entry
    kept = {s: v for s, v in (recipe_entry.get("strategies") or {}).items()
            if get_status(conn, symptom_id=symptom_id, design_class=design_class,
                          platform=platform, strategy=s,
                          default=UNROSTERED) == "promoted"}
    out = dict(recipe_entry)
    out["strategies"] = kept
    return out


def unrostered_keys(conn, heur: dict) -> list[tuple]:
    """Concrete (non-rollup, non-NONDIVERGENT) recipe keys present in `heur` that carry
    NO recipe_status row — the P0-2 fail-open surface. An EMPTY list is the healthy
    invariant: every learned recipe is rostered as at least a candidate, so
    filter_promoted's fail-closed default never strips a recipe merely because its
    enqueue was skipped. Used by ensure_rostered (self-heal) and the honesty CLI."""
    have = set(conn.execute(
        "SELECT symptom_id, design_class, platform, strategy FROM recipe_status"))
    return [key for key, _ in _iter_keys(heur)
            if key[3] not in NONDIVERGENT_STRATEGIES and key not in have]


def ensure_rostered(conn, heur: dict) -> list[tuple]:
    """P0-2 coverage self-heal (failure-patterns #48): guarantee every concrete learned
    recipe key has a lifecycle row, so filter_promoted's fail-closed default never
    strips a recipe just because a crashed/partial diff_and_enqueue skipped it. A
    still-unrostered key is enqueued as a **candidate** (provenance 'learner_coverage')
    — the fail-closed choice: unvalidated until it wins A/B, NEVER fabricated as
    promoted. Idempotent; returns the keys it newly rostered. NONDIVERGENT strategies
    stay out of the queue (park_nondivergent owns them)."""
    rostered = unrostered_keys(conn, heur)
    for key in rostered:
        conn.execute(
            "INSERT OR IGNORE INTO recipe_status (symptom_id, design_class, platform, "
            "strategy, status, provenance, generation, status_version, updated_at) "
            "VALUES (?,?,?,?,?,?,?,1,?)",
            (*key, "candidate", "learner_coverage", heur.get("generation"), _now()))
    conn.commit()
    return rostered


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
        " WHERE status='candidate' ORDER BY julianday(updated_at)")
    return [dict(zip(("symptom_id", "design_class", "platform", "strategy"), r))
            for r in cur.fetchall()]
