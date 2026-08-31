"""Derive conservative negative applicability contexts from typed evidence."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from tehm.causal.mechanism import load_transition_facts
from tehm.ids import stable_dumps


def _path_support(row: sqlite3.Row) -> dict:
    try:
        support = json.loads(row["support_json"] or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("mechanism knowledge path support is malformed") from exc
    if not isinstance(support, dict):
        raise ValueError("mechanism knowledge path support must be an object")
    return support


def derive_negative_context(
        conn: sqlite3.Connection, path_row: sqlite3.Row,
        source_transition_ids: Iterable[str]) -> tuple[dict, ...]:
    """Return only contexts evidenced by competing same-family transitions.

    A missing negative context is not treated as proof of broad applicability;
    the builder simply returns an empty tuple.  Existing explicit support
    metadata is retained only when it is a list of JSON objects.
    """
    source_ids = {str(value) for value in source_transition_ids}
    if not source_ids or any(not value for value in source_ids):
        raise ValueError("mechanism knowledge source transition IDs are malformed")
    result: list[dict] = []
    support = _path_support(path_row)
    explicit = support.get("negative_applicability")
    if explicit is not None:
        if not isinstance(explicit, list) or any(not isinstance(item, dict)
                                                  for item in explicit):
            raise ValueError("mechanism knowledge negative applicability is malformed")
        result.extend(dict(item) for item in explicit)
    campaigns = support.get("source_campaigns")
    if (not isinstance(campaigns, list) or len(campaigns) != 1 or
            type(campaigns[0]) is not str or not campaigns[0].strip()):
        raise ValueError("mechanism knowledge source campaign witness is malformed")
    campaign_id = campaigns[0].strip()
    # Negative context is derived only from the same learner campaign and
    # training split.  Looking at held-out/calibration rows here would leak
    # evaluation evidence into a training-derived knowledge claim.
    rows = conn.execute(
        """SELECT DISTINCT t.transition_id
             FROM tehm_transitions t
             JOIN tehm_dataset_membership dm
               ON dm.transition_id=t.transition_id
            WHERE dm.campaign_id=? AND dm.split='training'
              AND dm.learner_eligible=1
            ORDER BY t.transition_id""", (campaign_id,)
    ).fetchall()
    # Load the path family/profile once; source IDs are excluded explicitly so
    # a claim cannot describe its own positive support as a negative context.
    family = str(path_row["mechanism_family"])
    profile = path_row["compatibility_profile"]
    source_facts = [load_transition_facts(conn, value) for value in sorted(source_ids)]
    source_outcomes = {facts.outcome for facts in source_facts}
    source_actions = {facts.action_digest for facts in source_facts}
    for row in rows:
        transition_id = str(row["transition_id"])
        if transition_id in source_ids:
            continue
        facts = load_transition_facts(conn, transition_id)
        if facts.mechanism_family != family or facts.compatibility_profile != profile:
            continue
        if facts.action_digest in source_actions and facts.outcome in source_outcomes:
            continue
        result.append({
            "mechanism_family": family,
            "compatibility_profile": profile,
            "competing_action_digest": facts.action_digest,
            "observed_outcome": facts.outcome,
            "reason": "COMPETING_TYPED_OUTCOME",
        })
    unique = {stable_dumps(item): item for item in result}
    return tuple(unique[key] for key in sorted(unique))


__all__ = ["derive_negative_context"]
