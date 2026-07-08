#!/usr/bin/env python3
"""Pure strategy-ranking model for the fix-learning loop. No I/O, no subprocess
— fully unit-testable. Mirrors the role of fmax_model.py but for violation-fix
strategy selection (spec 2026-06-05 §5.3 / §6).

A "recipe entry" is the Tier-3 aggregate for one (check_type, violation_class):
    {"strategies": {strategy_id: {"attempts","successes","failures","wins"?,
                                  "median_reduction_pct"?}}, "n_sessions": int}

rank_strategies() returns ALL static-catalog strategies, priority-ordered, each
annotated with evidence. Untried strategies get the neutral Beta(1,1) prior so
they are explored after proven winners but before proven losers; a proven loser
is down-ranked, never zeroed (never permanently blacklisted).
"""
from __future__ import annotations

# Smoothing prior: Beta(1,1) with half credit for partial-improvement 'win's:
#   score = (successes + 0.5*wins + 1) / (attempts + 2).
# A 'win' is a real partial improvement that didn't fully clear the violation, so
# it earns half a success — enough to rank a reliable improver above a pure loser
# (and above an untried 0.5 prior once it accrues clears too) without claiming a
# full clearance (bug #7/#11). wins defaults to 0 — fully backward compatible with
# recipes predating the counter. attempts=0 -> 0.5 (neutral); 9/11 -> 0.77; 0/3 -> 0.2.
def _score(successes: int, attempts: int, wins: int = 0) -> float:
    return (successes + 0.5 * wins + 1) / (attempts + 2)


POOLED_MIN_ATTEMPTS = 5   # confidence floor (engineer-loop spec §5.7.2)


def rank_strategies(recipe_entry: dict | None, static_order: list[str],
                    pooled: dict | None = None,
                    pooled_min_attempts: int = POOLED_MIN_ATTEMPTS) -> list[dict]:
    """Rank `static_order` strategies by smoothed historical clearance.

    recipe_entry: Tier-3 aggregate for this (check, violation_class) — or, in the
                  symptom-indexed path, the current platform's by_platform slice —
                  or None (cold start). static_order: catalog order (the
                  deterministic tiebreaker and the full set that must always be
                  returned). pooled: optional cross-platform symptom-level stats
                  (spec 2026-06-09) used as an INFORMED prior for strategies the
                  local recipe has no data for — lifts a transfer-worthy strategy
                  above the flat-0.5 untried prior. pooled=None -> legacy behavior.
    """
    stats = (recipe_entry or {}).get("strategies", {})
    n_sessions = (recipe_entry or {}).get("n_sessions", 0)
    pooled = pooled or {}
    ranked: list[dict] = []
    for pos, sid in enumerate(static_order):
        s = stats.get(sid)
        if s:
            attempts = int(s.get("attempts", 0))
            successes = int(s.get("successes", 0))
            wins = int(s.get("wins", 0))   # default 0 -> backward compatible
            failures = int(s.get("failures", max(0, attempts - successes)))
            score = _score(successes, attempts, wins)
            plat_n = int(s.get("platform_count", 0) or 0)
            prov = f"learned(n={n_sessions},tried={attempts})"
        elif sid in pooled:
            ps = pooled[sid]
            attempts = int(ps.get("attempts", 0))
            successes = int(ps.get("successes", 0))
            wins = int(ps.get("wins", 0))
            failures = int(ps.get("failures", max(0, attempts - successes)))
            score = _score(successes, attempts, wins)
            plat_n = int(ps.get("platform_count", 0) or 0)
            # Confidence floor (engineer-loop §5.7.2): a pooled-only strategy
            # below the attempt floor must not outrank a locally PROVEN one.
            local_proven = [
                _score(int(v.get("successes", 0)), int(v.get("attempts", 0)),
                       int(v.get("wins", 0)))
                for v in stats.values() if int(v.get("successes", 0)) >= 1]
            if attempts < pooled_min_attempts and local_proven:
                score = min(score, max(local_proven) - 1e-6)
            prov = f"prior(pooled,tried={attempts})"
        else:
            attempts = successes = failures = wins = 0
            plat_n = 0
            score = _score(0, 0)  # 0.5 neutral prior
            prov = "cold-start"
        # Win 1 tiebreaker: mean dense outcome_score over runs that used this
        # strategy (populated by the learner). Defaults to 0.0 when absent, so a
        # legacy recipe without the field ranks byte-identically (the secondary key
        # is then a constant). Clean-rate (`score`) always dominates; outcome_score
        # only orders WITHIN equal clean-rate.
        mean_os = (s or {}).get("mean_outcome_score")
        item = {
            "strategy": sid, "score": score, "static_pos": pos,
            "attempts": attempts, "successes": successes, "failures": failures,
            "wins": wins, "provenance": prov,
            "mean_outcome_score": mean_os,
            # Cross-platform corroboration: count of distinct platforms this
            # strategy succeeded on (spec 2026-06-18, the 'transfer' mission). A
            # fix proven across N platforms is trusted ahead of a one-design
            # fluke. Pure SORT TIEBREAKER (below score + outcome_score, above
            # catalog position) — never a score override, so a clearly stronger
            # clearer always wins. Defaults 0 -> legacy recipes rank unchanged.
            "platform_count": plat_n,
        }
        if s and s.get("median_reduction_pct") is not None:
            item["median_reduction_pct"] = s["median_reduction_pct"]
        ranked.append(item)
    # rank_key = (success_rate_beta, mean_outcome_score, platform_count) lexico-
    # graphically, then catalog position asc (stable, deterministic). The
    # platform_count tiebreaker only orders WITHIN equal (score, outcome_score)
    # so corroborated-across-platforms beats a single-platform fluke, but it can
    # never lift a worse-clearing strategy above a better one.
    ranked.sort(key=lambda r: (-r["score"], -(r["mean_outcome_score"] or 0.0),
                               -r["platform_count"], r["static_pos"]))
    return ranked
