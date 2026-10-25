"""DB-replayable, mutation-independent CAPABILITY_GAP source witness.

This diagnostic carries no lifecycle or mutation authority. Consumers must
verify it against the immutable pre-update source connection; a serialized
admitted flag, receipt digest, or candidate after-state is not sufficient.
No canonical schema is changed and only shadow routing is used.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

from contracts import MemoryQuery, MemoryRoutingDecision
from tehm.assets.gap_detector import detect_capability_gaps
from tehm.assets.receipts import CapabilityGapReceipt
from tehm.causal.mechanism import load_transition_facts
from tehm.ids import stable_dumps
from tehm.retrieval.memory_router import route_memory
from .admission import EvolutionAdmissionReceipt, admit_evolution_reason
from .reason_derivation import EvolutionReasonDerivationReceipt, derive_capability_gap_reason

GAP_SOURCE_VERSION = "capability-gap-source-witness-v1"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORBIDDEN = frozenset({"localized_update_plan", "mutation_plan", "replacement_knowledge",
    "replacement_asset", "shadow_after_state", "production_authority", "fix", "gold_patch",
    "repaired_rtl", "heldout_answer"})


def _digest(value):
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _logical(conn):
    return _digest("\n".join(conn.iterdump()))


def _json(value):
    return json.loads(stable_dumps(value))


def _forbidden(value):
    if isinstance(value, Mapping):
        return bool(_FORBIDDEN & set(value)) or any(_forbidden(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_forbidden(v) for v in value)
    return False


@dataclass(frozen=True)
class CapabilityGapSourceReceipt:
    campaign_id: str
    case_id: str
    source_memory_digest: str
    transition_ids: tuple[str, ...]
    source_query: dict
    source_facts_digest: str
    source_membership_digest: str
    gap: dict
    routing: dict
    reason: dict
    admission: dict
    version: str = GAP_SOURCE_VERSION
    evaluation_only: bool = True
    canonical_memory_mutation: str = "none"

    def __post_init__(self):
        if (self.version != GAP_SOURCE_VERSION or self.evaluation_only is not True or
                self.canonical_memory_mutation != "none"):
            raise ValueError("gap source receipt must be versioned evaluation-only evidence")
        for field in ("campaign_id", "case_id"):
            if type(getattr(self, field)) is not str or not getattr(self, field).strip():
                raise ValueError("gap source receipt requires campaign/case identity")
        for field in ("source_memory_digest", "source_facts_digest", "source_membership_digest"):
            if type(getattr(self, field)) is not str or not _DIGEST.fullmatch(getattr(self, field)):
                raise ValueError("gap source receipt requires exact source digests")
        ids = self.transition_ids
        if (not isinstance(ids, (tuple, list)) or len(ids) < 2 or
                any(type(v) is not str or not v.strip() for v in ids) or len(set(ids)) != len(ids)):
            raise ValueError("gap source receipt requires unique source transitions")
        object.__setattr__(self, "transition_ids", tuple(sorted(ids)))
        for field in ("source_query", "gap", "routing", "reason", "admission"):
            value = getattr(self, field)
            if not isinstance(value, Mapping) or _forbidden(value):
                raise ValueError("gap source receipt contains non-source or mutation-dependent inputs")
            object.__setattr__(self, field, _json(dict(value)))
        MemoryQuery(**self.source_query)
        gap = CapabilityGapReceipt.from_dict(self.gap)
        route = MemoryRoutingDecision.from_dict(self.routing)
        reason = EvolutionReasonDerivationReceipt.from_dict(self.reason)
        admission = EvolutionAdmissionReceipt.from_dict(self.admission)
        expected = derive_capability_gap_reason(gap, campaign_id=self.campaign_id, case_id=self.case_id,
            failure_transition_ids=self.transition_ids, routing=route)
        if (expected is None or expected.to_dict() != reason.to_dict() or
                admission.campaign_id != self.campaign_id or admission.case_id != self.case_id or
                admission.reason != "CAPABILITY_GAP" or not admission.admitted or
                tuple(gap.evidence_transitions) != self.transition_ids):
            raise ValueError("gap source receipt has inconsistent typed source reason/admission")
        expected_admission = admit_evolution_reason(reason, campaign_id=self.campaign_id, learner_eligible=True,
            capability_gap=gap, failure_transition_ids=self.transition_ids, routing=route)
        if admission.to_dict() != expected_admission.to_dict():
            raise ValueError("gap source receipt admission does not replay its immutable inputs")

    def to_dict(self):
        return _json({field.name: getattr(self, field.name) for field in dataclasses.fields(self)})

    @property
    def receipt_digest(self):
        return _digest(self.to_dict())

    @property
    def receipt_id(self):
        return "gap_source_" + self.receipt_digest.split(":", 1)[1][:24]

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError("gap source receipt must be an object")
        fields = {field.name for field in dataclasses.fields(cls)}
        if not fields <= set(value) or set(value) - fields - {"receipt_id", "receipt_digest"}:
            raise ValueError("gap source receipt has missing or non-source fields")
        result = cls(**{key: value[key] for key in fields})
        if value.get("receipt_id", result.receipt_id) != result.receipt_id or value.get("receipt_digest", result.receipt_digest) != result.receipt_digest:
            raise ValueError("gap source receipt digest mismatch")
        return result


def derive_capability_gap_source(conn: sqlite3.Connection, *, campaign_id: str, case_id: str,
        query: MemoryQuery, transition_ids: tuple[str, ...] | list[str]) -> CapabilityGapSourceReceipt | None:
    """Derive from current pre-update source; never accept supplied gap labels."""
    if not isinstance(conn, sqlite3.Connection):
        raise TypeError("gap source derivation requires a SQLite source connection")
    if not isinstance(query, MemoryQuery) or _forbidden(dataclasses.asdict(query)):
        raise ValueError("gap source derivation requires a source-only typed query")
    plan = query.query_plan
    if not isinstance(plan, Mapping) or not plan.get("mechanism_family") or not plan.get("compatibility_profile"):
        raise ValueError("gap source query requires explicit mechanism/profile")
    before = _logical(conn)
    prior_query_only = conn.execute("PRAGMA query_only").fetchone()[0]
    conn.execute("PRAGMA query_only=ON")
    try:
        gaps = detect_capability_gaps(conn, campaign_id=campaign_id, transition_ids=transition_ids)
        route = route_memory(conn, query, mode="shadow", memory_budget=1, no_memory_budget=1, persist_state=False, commit=False)
        if route.decision != "NO_SKILL" or route.no_skill_reason != "NO_MATCH" or route.selected_asset_ids or route.memory_budget:
            return None
        selected = [g for g in gaps if g.mechanism_family == plan["mechanism_family"] and
            g.compatibility_profile == plan["compatibility_profile"]]
        if len(selected) != 1:
            return None
        gap = selected[0]
        ids = tuple(sorted(transition_ids))
        reason = derive_capability_gap_reason(gap, campaign_id=campaign_id, case_id=case_id,
            failure_transition_ids=ids, routing=route)
        if reason is None:
            return None
        members = [dict(conn.execute("SELECT * FROM tehm_dataset_membership WHERE transition_id=? AND campaign_id=?",
            (identity, campaign_id)).fetchone()) for identity in ids]
        facts = [dataclasses.asdict(load_transition_facts(conn, identity)) for identity in ids]
        admission = admit_evolution_reason(reason, campaign_id=campaign_id,
            learner_eligible=all(m["split"] == "training" and m["learner_eligible"] == 1 for m in members),
            capability_gap=gap, failure_transition_ids=ids, routing=route)
        if not admission.admitted:
            return None
        return CapabilityGapSourceReceipt(campaign_id=campaign_id, case_id=case_id, source_memory_digest=before,
            transition_ids=ids, source_query=dataclasses.asdict(query), source_facts_digest=_digest(facts),
            source_membership_digest=_digest(members), gap=gap.to_dict(), routing=route.to_dict(),
            reason=reason.to_dict(), admission=admission.to_dict())
    finally:
        conn.execute("PRAGMA query_only=" + str(int(prior_query_only)))
        if _logical(conn) != before:
            raise RuntimeError("gap source derivation changed its pre-update source state")


def verify_capability_gap_source(conn: sqlite3.Connection, value) -> dict:
    """Recheck every source fact/role/route/digest; never trust admitted flags."""
    try:
        receipt = (CapabilityGapSourceReceipt.from_dict(value.to_dict()) if isinstance(value, CapabilityGapSourceReceipt)
            else CapabilityGapSourceReceipt.from_dict(value))
        actual = derive_capability_gap_source(conn, campaign_id=receipt.campaign_id, case_id=receipt.case_id,
            query=MemoryQuery(**receipt.source_query), transition_ids=receipt.transition_ids)
        if actual is None or actual.to_dict() != receipt.to_dict():
            raise ValueError("gap source receipt differs from independently rederived source state")
    except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        return {"verified": False, "eligible": False, "reason": str(exc)}
    return {"verified": True, "eligible": True, "receipt_id": actual.receipt_id,
        "receipt_digest": actual.receipt_digest, "source_memory_digest": actual.source_memory_digest}


__all__ = ["GAP_SOURCE_VERSION", "CapabilityGapSourceReceipt", "derive_capability_gap_source", "verify_capability_gap_source"]
