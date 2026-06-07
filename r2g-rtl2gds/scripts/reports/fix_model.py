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


def rank_strategies(recipe_entry: dict | None, static_order: list[str]) -> list[dict]:
    """Rank `static_order` strategies by smoothed historical clearance.

    recipe_entry: Tier-3 aggregate for this (check, violation_class), or None
                  (cold start). static_order: catalog order (the deterministic
                  tiebreaker and the full set that must always be returned).
    """
    stats = (recipe_entry or {}).get("strategies", {})
    n_sessions = (recipe_entry or {}).get("n_sessions", 0)
    ranked: list[dict] = []
    for pos, sid in enumerate(static_order):
        s = stats.get(sid)
        if s:
            attempts = int(s.get("attempts", 0))
            successes = int(s.get("successes", 0))
            wins = int(s.get("wins", 0))   # default 0 -> backward compatible
            failures = int(s.get("failures", max(0, attempts - successes)))
            score = _score(successes, attempts, wins)
            prov = f"learned(n={n_sessions},tried={attempts})"
        else:
            attempts = successes = failures = wins = 0
            score = _score(0, 0)  # 0.5 neutral prior
            prov = "cold-start"
        item = {
            "strategy": sid, "score": score, "static_pos": pos,
            "attempts": attempts, "successes": successes, "failures": failures,
            "wins": wins, "provenance": prov,
        }
        if s and s.get("median_reduction_pct") is not None:
            item["median_reduction_pct"] = s["median_reduction_pct"]
        ranked.append(item)
    # Primary: score desc. Secondary: catalog position asc (stable, deterministic).
    ranked.sort(key=lambda r: (-r["score"], r["static_pos"]))
    return ranked
