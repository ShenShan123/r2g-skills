"""Deterministic novelty detection for online observations."""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

from tehm.causal.mechanism import load_transition_facts
from tehm.causal.witness import parse_source_transition_ids
from tehm.ids import stable_dumps


NOVELTY_RECEIPT_VERSION = "novelty-receipt-v0.1"


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"novelty receipt {field} is required")
    return value.strip()


@dataclass(frozen=True)
class NoveltyReceipt:
    """Content-addressed learner/audit novelty diagnostic."""

    transition_id: str
    campaign_id: str
    mechanism_family: str
    compatibility_profile: str | None
    status: str
    path_exists: bool
    lineage_id: str | None
    learner_eligible: bool
    version: str = NOVELTY_RECEIPT_VERSION

    def __post_init__(self) -> None:
        for field_name in ("transition_id", "campaign_id", "mechanism_family", "status"):
            object.__setattr__(self, field_name,
                               _text(getattr(self, field_name), field_name))
        if self.compatibility_profile is not None:
            object.__setattr__(self, "compatibility_profile",
                               _text(self.compatibility_profile, "compatibility_profile"))
        if self.lineage_id is not None:
            object.__setattr__(self, "lineage_id", _text(self.lineage_id, "lineage_id"))
        if self.status not in {"NOVEL_MECHANISM", "KNOWN_MECHANISM"}:
            raise ValueError("novelty receipt status is invalid")
        if type(self.path_exists) is not bool or type(self.learner_eligible) is not bool:
            raise ValueError("novelty receipt boolean fields are invalid")
        if self.version != NOVELTY_RECEIPT_VERSION:
            raise ValueError("novelty receipt version is invalid")

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "transition_id": self.transition_id,
            "campaign_id": self.campaign_id,
            "mechanism_family": self.mechanism_family,
            "compatibility_profile": self.compatibility_profile,
            "status": self.status,
            "path_exists": self.path_exists,
            "lineage_id": self.lineage_id,
            "learner_eligible": self.learner_eligible,
        }

    @property
    def receipt_digest(self) -> str:
        return "sha256:" + hashlib.sha256(
            stable_dumps(self.to_dict()).encode()).hexdigest()

    @property
    def receipt_id(self) -> str:
        return "novelty_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, payload: object) -> "NoveltyReceipt":
        if not isinstance(payload, Mapping):
            raise ValueError("novelty receipt must be an object")
        required = {
            "version", "transition_id", "campaign_id", "mechanism_family",
            "compatibility_profile", "status", "path_exists", "lineage_id",
            "learner_eligible",
        }
        if not required <= set(payload):
            raise ValueError("novelty receipt is missing fields")
        receipt = cls(
            version=payload["version"], transition_id=payload["transition_id"],
            campaign_id=payload["campaign_id"],
            mechanism_family=payload["mechanism_family"],
            compatibility_profile=payload["compatibility_profile"],
            status=payload["status"], path_exists=payload["path_exists"],
            lineage_id=payload["lineage_id"],
            learner_eligible=payload["learner_eligible"],
        )
        if payload.get("receipt_digest") not in (None, receipt.receipt_digest):
            raise ValueError("novelty receipt digest mismatch")
        if payload.get("receipt_id") not in (None, receipt.receipt_id):
            raise ValueError("novelty receipt ID mismatch")
        return receipt


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
        source_ids, _error = parse_source_transition_ids(
            row["source_transitions_json"])
        if source_ids is None:
            continue
        placeholders = ",".join("?" for _ in source_ids)
        eligible = conn.execute(
            f"""SELECT COUNT(*) AS n FROM tehm_dataset_membership
                  WHERE campaign_id=? AND split='training'
                    AND learner_eligible=1
                    AND transition_id IN ({placeholders})""",
                    (campaign_id, *source_ids)).fetchone()
        if int(eligible["n"] if eligible else 0) == len(source_ids):
            existing = True
            break
    membership = conn.execute(
        "SELECT split, learner_eligible FROM tehm_dataset_membership "
        "WHERE campaign_id=? AND transition_id=? ORDER BY split LIMIT 1",
        (campaign_id, transition_id)).fetchone()
    learner_eligible = bool(
        membership and membership["split"] == "training" and
        int(membership["learner_eligible"]) == 1)
    return NoveltyReceipt(
        status="KNOWN_MECHANISM" if existing else "NOVEL_MECHANISM",
        mechanism_family=facts.mechanism_family,
        compatibility_profile=facts.compatibility_profile,
        transition_id=transition_id, campaign_id=campaign_id,
        path_exists=bool(existing), lineage_id=facts.lineage_id,
        learner_eligible=learner_eligible).to_dict()


__all__ = ["NOVELTY_RECEIPT_VERSION", "NoveltyReceipt", "detect_novelty"]
