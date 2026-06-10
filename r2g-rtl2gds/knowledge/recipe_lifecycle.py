#!/usr/bin/env python3
"""Recipe lifecycle: shadow -> candidate -> promoted (engineer-loop §5.3).

Only PROMOTED recipes affect live ranking. New/changed recipes from a learn
rebuild become 'candidate' and are enqueued for inline A/B (ab_runner). An A/B
win promotes; loss/inconclusive reverts to shadow. Agent-authored strategies
enter as shadow — NO special trust (decision 7). Absent row = promoted
(grandfathering bootstrap for pre-lifecycle learned recipes).
"""
from __future__ import annotations

import datetime as _dt
import json

GRANDFATHERED = "promoted"   # absent-row default


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


def pending_candidates(conn) -> list[dict]:
    """All candidate rows awaiting an A/B trial (ab_runner's work queue)."""
    cur = conn.execute(
        "SELECT symptom_id, design_class, platform, strategy FROM recipe_status"
        " WHERE status='candidate' ORDER BY updated_at")
    return [dict(zip(("symptom_id", "design_class", "platform", "strategy"), r))
            for r in cur.fetchall()]
