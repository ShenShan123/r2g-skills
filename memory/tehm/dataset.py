"""Explicit dataset membership and learner-firewall operations.

Canonical evidence is append-only, while an experiment's role for that
evidence is a separate, versioned fact.  Keeping the split outside the
canonical transition prevents a held-out/A-B row retained for audit from being
silently consumed by crystallization.
"""
from __future__ import annotations

import sqlite3

from tehm import db as tehm_db

SPLITS = frozenset({"training", "calibration", "heldout", "ab"})


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
    if split not in SPLITS:
        raise ValueError(f"unknown dataset split: {split!r}")
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
    eligible = (split == "training") if learner_eligible is None else bool(learner_eligible)
    existing = conn.execute(
        """SELECT split, learner_eligible, frozen_snapshot_digest
             FROM tehm_dataset_membership
            WHERE transition_id=? AND campaign_id=?""",
        (transition_id, campaign_id)).fetchone()
    if existing is not None:
        old_authority = bool(
            existing["learner_eligible"] and existing["split"] == "training")
        # A campaign membership is an evidence-firewall fact.  Reclassifying
        # audit evidence into learner support in place would let held-out or
        # calibration data become training merely by overwriting one row.
        # Start a new campaign for a new learner partition instead.
        if not old_authority and eligible:
            raise ValueError(
                "dataset membership cannot be upgraded to learner support in place")
        old_digest = existing["frozen_snapshot_digest"]
        if old_digest is not None and old_digest != frozen_snapshot_digest:
            raise ValueError(
                "dataset membership frozen_snapshot_digest is immutable")
        if (existing["split"] == split and
                bool(existing["learner_eligible"]) == bool(eligible) and
                (old_digest == frozen_snapshot_digest or
                 (old_digest is None and frozen_snapshot_digest is None))):
            return
        # Explicit reclassification (normally training -> audit-only) is
        # represented as an UPDATE, never a replace/delete+insert.  The
        # transition itself remains immutable and all learner queries still
        # require training + learner_eligible=1.
        conn.execute(
            """UPDATE tehm_dataset_membership
                  SET split=?, learner_eligible=?, frozen_snapshot_digest=?,
                      assigned_at=?
                WHERE transition_id=? AND campaign_id=?""",
            (split, int(eligible), frozen_snapshot_digest, tehm_db.now_local(),
             transition_id, campaign_id))
        return
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
