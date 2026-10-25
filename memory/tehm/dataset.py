"""Explicit dataset membership and learner-firewall operations.

Canonical evidence is append-only, while an experiment's role for that
evidence is a separate, campaign-scoped fact.  Keeping the split outside the
canonical transition prevents a held-out/A-B row retained for audit from being
silently consumed by crystallization.  A membership row is immutable after its
first write; a new learner partition gets a new campaign ID.
"""
from __future__ import annotations

import sqlite3

from tehm import db as tehm_db

SPLITS = frozenset({"training", "calibration", "heldout", "ab"})


def require_learner_bool(value: object, *, field: str = "learner_eligible") -> bool:
    """Require an API/evidence value to be a real JSON/Python boolean.

    ``bool(value)`` is unsafe at the evidence boundary: values such as the
    string ``"false"`` are truthy and would otherwise become learner support.
    Callers that accept a public learner flag must use this helper rather than
    coercing arbitrary objects.
    """
    if type(value) is not bool:
        raise ValueError(f"{field} must be a boolean")
    return value


def normalize_stored_learner_bool(value: object, *,
                                  field: str = "learner_eligible") -> bool:
    """Normalize a persisted SQLite learner bit, fail-closed on weak types.

    SQLite returns ``INTEGER`` columns as ``int``.  A direct row mapping may
    instead contain a JSON boolean, which is also unambiguous.  Strings,
    floats and arbitrary truthy values are deliberately rejected.
    """
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise ValueError(f"{field} must be stored as integer 0/1 or boolean")


def validate_membership_row(row: object) -> tuple[bool, str]:
    """Validate and return ``(learner_eligible, split)`` for a membership row.

    This is the read-side firewall shared by online/evolution consumers.  The
    schema provides the first line of defence; derived readers must still
    reject malformed copied rows and contradictory non-training membership.
    """
    try:
        split = row["split"]  # type: ignore[index]
        raw_eligible = row["learner_eligible"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("dataset membership row is missing split/learner_eligible") from exc
    if type(split) is not str or split not in SPLITS:
        raise ValueError("dataset membership split is invalid")
    eligible = normalize_stored_learner_bool(raw_eligible)
    if eligible and split != "training":
        raise ValueError("non-training dataset membership cannot be learner-eligible")
    return eligible, split


def assign_transition(conn: sqlite3.Connection, *, transition_id: str,
                      campaign_id: str = "live", split: str = "training",
                      learner_eligible: bool | None = None,
                      frozen_snapshot_digest: str | None = None) -> None:
    """Assign one transition to a campaign split.

    ``learner_eligible`` defaults to true only for training.  Non-training
    splits remain audit-only and cannot be opted into learner support.
    """
    if not transition_id or not campaign_id:
        raise ValueError("transition_id and campaign_id are required")
    if type(split) is not str or split not in SPLITS:
        raise ValueError(f"unknown dataset split: {split!r}")
    if learner_eligible is not None:
        require_learner_bool(learner_eligible)
    # Calibration, held-out, and A/B evidence may be retained for audit, but
    # none of those splits can be learner support.  Reject contradictory
    # requests instead of persisting a row that a learner query could consume.
    if split != "training" and learner_eligible is True:
        raise ValueError(
            "only training evidence may be marked learner_eligible")
    if conn.execute(
            "SELECT 1 FROM tehm_transitions WHERE transition_id=?",
            (transition_id,)).fetchone() is None:
        raise ValueError(f"unknown TEHM transition: {transition_id}")
    eligible = (split == "training") if learner_eligible is None else learner_eligible
    existing = conn.execute(
        """SELECT split, learner_eligible, frozen_snapshot_digest
             FROM tehm_dataset_membership
            WHERE transition_id=? AND campaign_id=?""",
        (transition_id, campaign_id)).fetchone()
    if existing is not None:
        old_eligible, old_split = validate_membership_row(existing)
        old_authority = old_eligible and old_split == "training"
        # A campaign membership is an evidence-firewall fact.  Reclassifying
        # audit evidence into learner support in place would let held-out or
        # calibration data become training merely by overwriting one row.
        # Start a new campaign for a new learner partition instead.
        if not old_authority and eligible:
            raise ValueError(
                "dataset membership cannot be upgraded to learner support in place")
        old_digest = existing["frozen_snapshot_digest"]
        if old_digest != frozen_snapshot_digest:
            raise ValueError(
                "dataset membership frozen_snapshot_digest is immutable; "
                "create a new campaign for a new snapshot")
        if (old_split == split and
                old_eligible == eligible and
                (old_digest == frozen_snapshot_digest or
                 (old_digest is None and frozen_snapshot_digest is None))):
            return
        # A membership row is an immutable audit fact.  In-place
        # reclassification would make a previously consumed training row look
        # held-out (or vice versa) on replay, invalidating source-freeze and
        # learner-support claims.  New partitions must use a new campaign ID;
        # an exact replay remains idempotent above.
        raise ValueError(
            "dataset membership is immutable; create a new campaign for a "
            "new split or learner partition")
    conn.execute(
        """INSERT INTO tehm_dataset_membership
           (transition_id, campaign_id, split, learner_eligible,
            frozen_snapshot_digest, assigned_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (transition_id, campaign_id, split, int(eligible),
         frozen_snapshot_digest, tehm_db.now_local()))


def assign_lineage(conn: sqlite3.Connection, *, lineage_id: str,
                   campaign_id: str, split: str,
                   learner_eligible: bool | None = None,
                   frozen_snapshot_digest: str | None = None) -> int:
    """Assign all transitions whose source state belongs to ``lineage_id``."""
    rows = conn.execute(
        """SELECT t.transition_id
             FROM tehm_transitions t
             JOIN tehm_states s ON s.state_id=t.source_state_id
            WHERE s.lineage_id=?""", (lineage_id,)).fetchall()
    had_outer_transaction = conn.in_transaction
    savepoint = "tehm_dataset_lineage_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        for row in rows:
            assign_transition(
                conn, transition_id=row["transition_id"], campaign_id=campaign_id,
                split=split, learner_eligible=learner_eligible,
                frozen_snapshot_digest=frozen_snapshot_digest)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    if not had_outer_transaction:
        conn.commit()
    return len(rows)


def learner_transition_predicate(*, campaign_id: str) -> tuple[str, tuple[str]]:
    """SQL predicate used by crystallization for a frozen campaign."""
    if not campaign_id:
        raise ValueError("campaign_id is required")
    return (
        "EXISTS (SELECT 1 FROM tehm_dataset_membership dm "
        "WHERE dm.transition_id=t.transition_id AND dm.campaign_id=? "
        "AND dm.split='training' AND dm.learner_eligible=1)",
        (campaign_id,),
    )
