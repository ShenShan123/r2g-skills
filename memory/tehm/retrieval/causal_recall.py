"""Evaluation-only recall over causal shadow paths.

This module is intentionally not imported by the production retrieval
pipeline.  It provides an interpretable R0/R1/R2 comparison lane while the
existing promoted-only rule retrieval remains unchanged.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from contracts import MemoryQuery
from tehm.causal.matcher import match_causal_path
from tehm.causal.path_builder import validate_persisted_path_row


@dataclass(frozen=True)
class CausalPathMatch:
    path_id: str
    mechanism_family: str
    compatibility_profile: str | None
    evidence_level: str
    score: float
    status: str
    source_transition_ids: tuple[str, ...]
    evidence_weight: float = 0.0
    mechanism_match: bool = False
    matched_fields: tuple[str, ...] = ()
    mismatched_fields: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "path_id": self.path_id,
            "mechanism_family": self.mechanism_family,
            "compatibility_profile": self.compatibility_profile,
            "evidence_level": self.evidence_level,
            "score": self.score,
            "status": self.status,
            "source_transition_ids": list(self.source_transition_ids),
            "evidence_weight": self.evidence_weight,
            "mechanism_match": self.mechanism_match,
            "matched_fields": list(self.matched_fields),
            "mismatched_fields": list(self.mismatched_fields),
            "reason": self.reason,
        }


def _source_transition_ids(raw: object) -> tuple[str, ...] | None:
    """Decode a derived path witness; malformed rows are not searchable."""
    try:
        values = json.loads(raw or "[]") if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(values, list) or not values:
        return None
    ids = tuple(str(value).strip() for value in values)
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        return None
    return tuple(sorted(ids))


def retrieve_causal_paths(
    conn: sqlite3.Connection,
    query: MemoryQuery | dict,
    *,
    campaign_id: str = "live",
    limit: int = 10,
    include_shadow: bool = True,
) -> list[CausalPathMatch]:
    """Return learner-eligible causal paths for an isolated evaluator.

    ``include_shadow`` is useful for research comparison and is never a
    production authority switch.  Paths whose source transitions are not
    learner eligible in ``campaign_id`` are filtered before scoring.
    """
    if not campaign_id:
        raise ValueError("campaign_id is required")
    plan = query.query_plan if isinstance(query, MemoryQuery) else dict(query or {})
    statuses = ("shadow", "candidate", "validated") if include_shadow else ("candidate", "validated")
    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"""SELECT * FROM tehm_causal_paths
              WHERE status IN ({placeholders})
              ORDER BY path_id""", statuses).fetchall()
    matches: list[CausalPathMatch] = []
    for row in rows:
        try:
            validate_persisted_path_row(row, conn)
        except ValueError:
            # Causal paths are derived shadow objects.  A malformed/tampered
            # path must disappear from the evaluator rather than contribute a
            # score or become an implicit authority input.
            continue
        source_ids = _source_transition_ids(row["source_transitions_json"])
        if source_ids is None:
            continue
        placeholders_ids = ",".join("?" for _ in source_ids)
        eligible = conn.execute(
            f"""SELECT COUNT(*) AS n FROM tehm_dataset_membership
                  WHERE campaign_id=? AND split='training' AND learner_eligible=1
                    AND transition_id IN ({placeholders_ids})""",
            (campaign_id, *source_ids)).fetchone()["n"]
        if int(eligible) != len(source_ids):
            continue
        match = match_causal_path(row, plan)
        if not match.eligible:
            continue
        matches.append(CausalPathMatch(
            path_id=row["path_id"], mechanism_family=row["mechanism_family"],
            compatibility_profile=row["compatibility_profile"],
            evidence_level=row["evidence_level"], score=match.score,
            status=row["status"], source_transition_ids=source_ids,
            evidence_weight=match.evidence_weight,
            mechanism_match=match.mechanism_match,
            matched_fields=match.matched_fields,
            mismatched_fields=match.mismatched_fields,
            reason=match.reason))
    matches.sort(key=lambda item: (-item.score, item.path_id))
    return matches[:max(0, int(limit))]


__all__ = ["CausalPathMatch", "retrieve_causal_paths"]
