"""Deterministic novelty detection for online observations."""
from __future__ import annotations

import json
import sqlite3

from tehm.causal.mechanism import load_transition_facts


def detect_novelty(conn: sqlite3.Connection, transition_id: str,
                   *, campaign_id: str = "live") -> dict:
    if not campaign_id:
        raise ValueError("campaign_id is required")
    facts = load_transition_facts(conn, transition_id)
    # A path is learner knowledge only when *all* of its source transitions
    # belong to this campaign's training learner set.  Looking at every path
    # globally would let a held-out/calibration shadow path suppress a
    # learner-side NOVEL_MECHANISM trigger (and therefore leak evaluation
    # structure into online consolidation).  Keep the check in Python rather
    # than relying on SQLite JSON extensions so it is portable and fail-closed
    # on malformed source lists.
    rows = conn.execute(
        """SELECT source_transitions_json FROM tehm_causal_paths
             WHERE mechanism_family=? AND compatibility_profile IS ?
               AND status IN ('shadow', 'candidate', 'validated')""",
        (facts.mechanism_family, facts.compatibility_profile)).fetchall()
    existing = False
    for row in rows:
        try:
            source_ids = json.loads(row["source_transitions_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(source_ids, list) or not source_ids:
            continue
        placeholders = ",".join("?" for _ in source_ids)
        eligible = conn.execute(
            f"""SELECT COUNT(*) AS n FROM tehm_dataset_membership
                  WHERE campaign_id=? AND split='training'
                    AND learner_eligible=1
                    AND transition_id IN ({placeholders})""",
            (campaign_id, *[str(item) for item in source_ids])).fetchone()
        if int(eligible["n"] if eligible else 0) == len(source_ids):
            existing = True
            break
    return {
        "status": "KNOWN_MECHANISM" if existing else "NOVEL_MECHANISM",
        "mechanism_family": facts.mechanism_family,
        "compatibility_profile": facts.compatibility_profile,
        "transition_id": transition_id,
        "campaign_id": campaign_id,
        "path_exists": bool(existing),
    }


__all__ = ["detect_novelty"]
