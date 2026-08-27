"""Fail-closed resolution of causal-edge transition witnesses.

The causal edge table is intentionally a shadow table without foreign keys.
Authority therefore must not treat an arbitrary ``evidence_refs_json`` string
as proof.  This module resolves only canonical transition IDs and valid
intervention-pair IDs, then checks their learner campaign membership before
using them as support for a path-level claim.
"""
from __future__ import annotations

import json
import sqlite3

from .evidence_level import evidence_rank


def parse_evidence_refs(raw: object) -> tuple[tuple[str, ...] | None, str | None]:
    """Parse an edge evidence witness without allowing malformed data through."""
    try:
        values = json.loads(raw or "[]") if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return None, "malformed_evidence_refs"
    if not isinstance(values, list) or not values:
        return None, "evidence_refs_missing"
    if any(not isinstance(value, str) or not value.strip() for value in values):
        return None, "malformed_evidence_refs"
    refs = tuple(value.strip() for value in values)
    if len(set(refs)) != len(refs):
        return None, "duplicate_evidence_refs"
    return refs, None


def _resolve_transition_ids(
    conn: sqlite3.Connection, refs: tuple[str, ...],
) -> tuple[tuple[str, ...], bool]:
    """Resolve refs and report whether every ref has a valid witness.

    ``evidence_refs_json`` is an authority input, not a best-effort hint.  A
    row containing one valid transition alongside an unknown or invalid pair
    must therefore be rejected as a whole; otherwise the valid subset could
    mask a forged/typoed reference.
    """
    direct: set[str] = set()
    placeholders = ",".join("?" for _ in refs)
    for row in conn.execute(
        f"SELECT transition_id FROM tehm_transitions "
        f"WHERE transition_id IN ({placeholders})", refs):
        direct.add(str(row["transition_id"]))

    valid_pairs: set[str] = set()
    pairs = conn.execute(
        f"""SELECT pair_id, control_transition_id, treatment_transition_id,
                    validity_status
               FROM tehm_intervention_pairs
              WHERE pair_id IN ({placeholders})""", refs).fetchall()
    for row in pairs:
        if row["validity_status"] == "VALID_CONTROLLED_PAIR":
            valid_pairs.add(str(row["pair_id"]))
            direct.add(str(row["control_transition_id"]))
            direct.add(str(row["treatment_transition_id"]))
    resolved_refs = {
        ref for ref in refs
        if ref in direct or ref in valid_pairs
    }
    return tuple(sorted(direct)), len(resolved_refs) == len(refs)


def learner_edge_transition_coverage(
    conn: sqlite3.Connection,
    source_transition_ids: tuple[str, ...] | list[str],
    *,
    campaign_id: str,
    required_level: str,
) -> tuple[str, ...]:
    """Return path sources covered by valid learner-controlled edges.

    Every accepted edge must resolve at least one canonical transition (or a
    valid controlled intervention pair), and all resolved transitions must be
    training/learner evidence in ``campaign_id``.  Unknown, malformed, or
    cross-campaign edge witnesses are ignored.  Returning the covered subset
    lets callers distinguish no support from incomplete path coverage.
    """
    sources = {str(item).strip() for item in source_transition_ids
               if str(item).strip()}
    if not sources or not campaign_id:
        return ()
    covered: set[str] = set()
    rows = conn.execute(
        """SELECT evidence_level, evidence_refs_json
               FROM tehm_causal_edges
              WHERE campaign_id=? AND learner_eligible=1""",
        (campaign_id,)).fetchall()
    for edge in rows:
        if evidence_rank(edge["evidence_level"]) < evidence_rank(required_level):
            continue
        refs, error = parse_evidence_refs(edge["evidence_refs_json"])
        if refs is None:
            continue
        resolved_ids, all_refs_resolved = _resolve_transition_ids(conn, refs)
        if not all_refs_resolved:
            continue
        resolved = set(resolved_ids)
        if not resolved:
            continue
        placeholders = ",".join("?" for _ in resolved)
        memberships = conn.execute(
            f"""SELECT transition_id
                   FROM tehm_dataset_membership
                  WHERE campaign_id=? AND split='training'
                    AND learner_eligible=1
                    AND transition_id IN ({placeholders})""",
            (campaign_id, *sorted(resolved))).fetchall()
        eligible = {str(row["transition_id"]) for row in memberships}
        # Do not allow one valid ref to mask another cross-campaign ref in the
        # same edge.  The complete edge witness must be learner-safe.
        if eligible != resolved:
            continue
        covered.update(sources.intersection(resolved))
    return tuple(sorted(covered))


__all__ = [
    "learner_edge_transition_coverage",
    "parse_evidence_refs",
]
