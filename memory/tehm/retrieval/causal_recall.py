"""Evaluation-only recall over causal shadow paths.

This module is intentionally not imported by the production retrieval
pipeline.  It provides an interpretable R0/R1/R2 comparison lane while the
existing promoted-only rule retrieval remains unchanged.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, replace

from contracts import MemoryQuery
from tehm.canonical.transition import Action, ObservationDelta, classify_outcome
from tehm.canonical.verifier import VerifierSnapshot
from tehm.ids import transition_id
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
    mechanism_score: float = 0.0
    utility_score: float = 0.5
    risk_penalty: float = 0.5
    quality_status: str = "NOT_ESTABLISHED"
    quality_source: str = "prior"
    quality_evidence_transition_ids: tuple[str, ...] = ()
    quality_reason: str = ""
    evidence_support_score: float = 1.0
    evidence_support_count: int = 0
    evidence_lineage_count: int = 0
    evidence_support_status: str = "NOT_ESTABLISHED"
    evidence_support_reason: str = ""

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
            "mechanism_score": self.mechanism_score,
            "utility_score": self.utility_score,
            "risk_penalty": self.risk_penalty,
            "quality_status": self.quality_status,
            "quality_source": self.quality_source,
            "quality_evidence_transition_ids": list(
                self.quality_evidence_transition_ids),
            "quality_reason": self.quality_reason,
            "evidence_support_score": self.evidence_support_score,
            "evidence_support_count": self.evidence_support_count,
            "evidence_lineage_count": self.evidence_lineage_count,
            "evidence_support_status": self.evidence_support_status,
            "evidence_support_reason": self.evidence_support_reason,
        }


@dataclass(frozen=True)
class CausalPathQuality:
    """Bounded quality factors for evaluation-only causal reranking.

    ``NOT_ESTABLISHED`` is deliberately conservative: absent utility/risk
    evidence receives neutral utility and a 0.5 risk penalty, so a path can be
    inspected for recall but cannot outrank a fully evidenced low-risk path.
    Explicitly malformed quality fields return no score and are excluded.
    """

    utility_score: float
    risk_penalty: float
    status: str
    reason: str = ""
    source: str = "prior"
    evidence_transition_ids: tuple[str, ...] = ()
    evidence_support_score: float = 1.0
    evidence_support_count: int = 0
    evidence_lineage_count: int = 0
    evidence_support_status: str = "NOT_ESTABLISHED"
    evidence_support_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "utility_score": self.utility_score,
            "risk_penalty": self.risk_penalty,
            "status": self.status,
            "reason": self.reason,
            "source": self.source,
            "evidence_transition_ids": list(self.evidence_transition_ids),
            "evidence_support_score": self.evidence_support_score,
            "evidence_support_count": self.evidence_support_count,
            "evidence_lineage_count": self.evidence_lineage_count,
            "evidence_support_status": self.evidence_support_status,
            "evidence_support_reason": self.evidence_support_reason,
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


def _quality_number(value: object) -> float | None:
    """Return a finite [0, 1] quality value, or reject the field."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        return None
    return round(number, 6)


def _path_quality(path) -> CausalPathQuality | None:
    """Parse optional path quality without treating missing evidence as safe."""
    raw = _get_path_value(path, "support", None)
    if raw is None:
        raw = _get_path_value(path, "support_json", {})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
    if not isinstance(raw, Mapping):
        return None
    support = dict(raw)

    utility_value = support.get("utility_score")
    if utility_value is None:
        utility = support.get("utility")
        if isinstance(utility, Mapping):
            utility_value = (utility.get("score")
                             if utility.get("score") is not None
                             else utility.get("normalized"))
            if utility_value is None and utility:
                return None
        elif utility is not None:
            utility_value = utility
    risk_value = support.get("risk_penalty")
    if risk_value is None:
        risk = support.get("risk")
        if isinstance(risk, Mapping):
            risk_value = (risk.get("penalty")
                          if risk.get("penalty") is not None
                          else risk.get("harmful_rate"))
            if risk_value is None and risk:
                return None
        elif risk is not None:
            risk_value = risk
    utility_explicit = utility_value is not None
    risk_explicit = risk_value is not None
    utility_score = (0.5 if not utility_explicit else
                     _quality_number(utility_value))
    risk_penalty = (0.5 if not risk_explicit else
                    _quality_number(risk_value))
    if utility_score is None or risk_penalty is None:
        return None
    status = "ESTABLISHED" if utility_explicit and risk_explicit else "NOT_ESTABLISHED"
    return CausalPathQuality(
        utility_score=utility_score, risk_penalty=risk_penalty, status=status,
        reason=("utility_and_risk_bound" if status == "ESTABLISHED"
                else "utility_or_risk_not_established"),
        source="path_support" if utility_explicit or risk_explicit else "prior",
    )


_UTILITY_SCORE = {"PARETO_SAFE": 1.0, "NEUTRAL": 0.5, "HARMFUL": 0.0}
_RISK_PENALTY = {"PARETO_SAFE": 0.0, "NEUTRAL": 0.5, "HARMFUL": 1.0}


def _decode_quality_json(raw: object, *, field: str):
    """Decode a canonical JSON field and reject malformed evidence."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"canonical quality {field} is malformed JSON") from exc
    return raw


def _canonical_transition_quality(
    conn: sqlite3.Connection, source_ids: tuple[str, ...],
) -> tuple[CausalPathQuality | None, bool]:
    """Derive quality from immutable transition observations when available.

    The raw ``utility_verdict`` is a canonical observation, not a caller-side
    path annotation.  We aggregate utility by mean and risk by worst case; an
    unknown verdict remains conservative rather than being treated as safe.
    The boolean result distinguishes an unavailable quality signal from a
    malformed canonical witness so retrieval can fail closed on corruption.
    """
    if conn is None or not source_ids:
        return None, False
    placeholders = ",".join("?" for _ in source_ids)
    rows = conn.execute(
        "SELECT transition_id, source_state_id, target_state_id, action_json, "
        "observation_delta_json, verifier_json, outcome, "
        "created_regressions_json, newly_observed_json "
        f"FROM tehm_transitions WHERE transition_id IN ({placeholders})",
        source_ids).fetchall()
    if len(rows) != len(source_ids):
        return None, True
    by_id = {str(row["transition_id"]): row for row in rows}
    if set(by_id) != set(source_ids):
        return None, True
    utility_values: list[float] = []
    risk_values: list[float] = []
    for source_transition_id in source_ids:
        row = by_id[source_transition_id]
        try:
            action = _decode_quality_json(
                row["action_json"], field="action")
            delta = _decode_quality_json(
                row["observation_delta_json"], field="observation_delta")
            verifier = _decode_quality_json(
                row["verifier_json"], field="verifier")
            regressions = _decode_quality_json(
                row["created_regressions_json"], field="created_regressions")
            newly_observed = _decode_quality_json(
                row["newly_observed_json"], field="newly_observed")
        except ValueError:
            return None, True
        if (not isinstance(action, Mapping) or not isinstance(delta, Mapping)
                or not isinstance(verifier, Mapping)):
            return None, True
        if not isinstance(regressions, list) or not isinstance(newly_observed, list):
            return None, True
        delta_regressions = delta.get("created_regressions", [])
        delta_newly_observed = delta.get("newly_observed_failures", [])
        if (not isinstance(delta_regressions, list)
                or not isinstance(delta_newly_observed, list)
                or regressions != delta_regressions
                or newly_observed != delta_newly_observed):
            return None, True
        try:
            canonical_action = Action.from_dict(dict(action))
            canonical_delta = ObservationDelta.from_dict(dict(delta))
            canonical_verifier = VerifierSnapshot.from_dict(dict(verifier))
        except (TypeError, ValueError):
            return None, True
        expected_id = transition_id(
            source_state_id=str(row["source_state_id"]),
            target_state_id=str(row["target_state_id"]),
            action=canonical_action.to_dict(),
            observation_delta=canonical_delta.to_dict(),
            verifier=canonical_verifier.content())
        if expected_id != source_transition_id or str(row["outcome"] or "") != classify_outcome(
                canonical_delta, canonical_verifier):
            return None, True
        verdict = str(delta.get("utility_verdict") or "UNKNOWN").upper()
        if verdict in _UTILITY_SCORE:
            utility_values.append(_UTILITY_SCORE[verdict])
            risk_values.append(_RISK_PENALTY[verdict])
        if regressions or newly_observed or str(row["outcome"] or "").upper() in {
                "FAIL", "REGRESSION"}:
            risk_values.append(1.0)
    if not utility_values and not risk_values:
        return None, False
    utility = (sum(utility_values) / len(utility_values)
               if utility_values else 0.5)
    risk = max(risk_values) if risk_values else 0.5
    complete = len(utility_values) == len(source_ids)
    quality = CausalPathQuality(
        utility_score=round(utility, 6), risk_penalty=round(risk, 6),
        status="ESTABLISHED" if complete else "NOT_ESTABLISHED",
        reason=("canonical_utility_verdict_bound" if complete
                else "canonical_quality_partially_established"),
        source="canonical_transition",
        evidence_transition_ids=tuple(sorted(source_ids)),
    )
    return quality, False


def _get_path_value(path, key: str, default=None):
    """Read a mapping-like or sqlite row path without coupling to row type."""
    if isinstance(path, Mapping):
        return path.get(key, default)
    try:
        return path[key]
    except (KeyError, IndexError, TypeError):
        return getattr(path, key, default)


def _path_source_ids(path) -> tuple[str, ...] | None:
    """Normalize a path's source witness for support/evidence accounting."""
    source_raw = _get_path_value(path, "source_transition_ids", None)
    if source_raw is None:
        source_raw = _get_path_value(path, "source_transitions_json", [])
    source_ids = _source_transition_ids(source_raw)
    if source_ids is not None:
        return source_ids
    if isinstance(source_raw, (list, tuple)):
        values = tuple(str(item).strip() for item in source_raw)
        if (values and all(values) and len(set(values)) == len(values)):
            return tuple(sorted(values))
    return None


def _path_evidence_support(
    path, support: Mapping, source_ids: tuple[str, ...] | None,
    conn: sqlite3.Connection | None,
) -> CausalPathQuality | None:
    """Bound causal support to canonical source count and lineage coverage."""
    if source_ids is None:
        return CausalPathQuality(
            utility_score=0.5, risk_penalty=0.5, status="NOT_ESTABLISHED",
            reason="utility_or_risk_not_established", source="prior",
            evidence_support_score=(1.0 if conn is None else 0.5),
            evidence_support_status="NOT_ESTABLISHED",
            evidence_support_reason="source_transition_witness_unavailable",
        )
    declared_count = support.get("fragment_count")
    if (declared_count is not None
            and (isinstance(declared_count, bool)
                 or not isinstance(declared_count, int)
                 or declared_count != len(source_ids))):
        return None
    if conn is None:
        return CausalPathQuality(
            utility_score=0.5, risk_penalty=0.5, status="NOT_ESTABLISHED",
            reason="utility_or_risk_not_established", source="prior",
            evidence_support_score=1.0,
            evidence_support_count=len(source_ids),
            evidence_support_reason="canonical_support_unavailable",
        )
    placeholders = ",".join("?" for _ in source_ids)
    rows = conn.execute(
        "SELECT t.transition_id, s.lineage_id "
        "FROM tehm_transitions t JOIN tehm_states s "
        "ON s.state_id=t.source_state_id "
        f"WHERE t.transition_id IN ({placeholders})", source_ids).fetchall()
    if len(rows) != len(source_ids):
        return None
    by_id = {str(row["transition_id"]): row for row in rows}
    if set(by_id) != set(source_ids):
        return None
    lineages = {
        str(by_id[source_id]["lineage_id"])
        for source_id in source_ids
        if by_id[source_id]["lineage_id"] not in (None, "")
    }
    source_component = min(1.0, len(source_ids) / 2.0)
    lineage_component = min(1.0, len(lineages) / 2.0)
    support_score = round((source_component + lineage_component) / 2.0, 6)
    established = len(source_ids) >= 2 and len(lineages) >= 2
    return CausalPathQuality(
        utility_score=0.5, risk_penalty=0.5, status="NOT_ESTABLISHED",
        reason="utility_or_risk_not_established", source="prior",
        evidence_support_score=support_score,
        evidence_support_count=len(source_ids),
        evidence_lineage_count=len(lineages),
        evidence_support_status=("ESTABLISHED" if established
                                 else "NOT_ESTABLISHED"),
        evidence_support_reason=("independent_lineage_support_bound"
                                 if established
                                 else "causal_support_below_replication_threshold"),
    )


def score_causal_path(
    path, mechanism_score: float, *, conn: sqlite3.Connection | None = None,
) -> tuple[float, CausalPathQuality] | None:
    """Apply bounded causal/evidence quality for shadow retrieval only.

    The mechanism matcher supplies the causal evidence-level weight.  This
    function adds a separate source/lineage support factor so one fragment
    cannot look as strong as replicated evidence.  Canonical transition
    utility/risk remains authoritative when path support does not provide an
    explicit quality claim.
    """
    causal_score = _quality_number(mechanism_score)
    if causal_score is None:
        return None
    quality = _path_quality(path)
    support = _get_path_value(path, "support", None)
    if support is None:
        support = _get_path_value(path, "support_json", {})
    if isinstance(support, str):
        try:
            support = json.loads(support)
        except (TypeError, json.JSONDecodeError):
            return None
    if not isinstance(support, Mapping):
        return None
    support = dict(support)
    source_ids = _path_source_ids(path)
    support_quality = _path_evidence_support(path, support, source_ids, conn)
    if support_quality is None:
        return None
    if not any(key in support for key in ("utility_score", "utility",
                                          "risk_penalty", "risk")):
        if source_ids:
            canonical_quality, malformed = _canonical_transition_quality(
                conn, source_ids)
            if malformed:
                return None
            if canonical_quality is not None:
                quality = canonical_quality
    if quality is None:
        return None
    quality = replace(
        quality,
        evidence_support_score=support_quality.evidence_support_score,
        evidence_support_count=support_quality.evidence_support_count,
        evidence_lineage_count=support_quality.evidence_lineage_count,
        evidence_support_status=support_quality.evidence_support_status,
        evidence_support_reason=support_quality.evidence_support_reason,
    )
    score = (causal_score * quality.utility_score
             * quality.evidence_support_score
             * (1.0 - quality.risk_penalty))
    return round(score, 6), quality


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
        scored = score_causal_path(row, match.score, conn=conn)
        if scored is None:
            # A supplied but malformed quality claim is not silently replaced
            # with a neutral prior; exclude it from the evaluation result.
            continue
        score, quality = scored
        matches.append(CausalPathMatch(
            path_id=row["path_id"], mechanism_family=row["mechanism_family"],
            compatibility_profile=row["compatibility_profile"],
            evidence_level=row["evidence_level"], score=score,
            status=row["status"], source_transition_ids=source_ids,
            evidence_weight=match.evidence_weight,
            mechanism_match=match.mechanism_match,
            matched_fields=match.matched_fields,
            mismatched_fields=match.mismatched_fields,
            reason=match.reason, mechanism_score=match.score,
            utility_score=quality.utility_score,
            risk_penalty=quality.risk_penalty,
            quality_status=quality.status,
            quality_source=quality.source,
            quality_evidence_transition_ids=quality.evidence_transition_ids,
            quality_reason=quality.reason,
            evidence_support_score=quality.evidence_support_score,
            evidence_support_count=quality.evidence_support_count,
            evidence_lineage_count=quality.evidence_lineage_count,
            evidence_support_status=quality.evidence_support_status,
            evidence_support_reason=quality.evidence_support_reason))
    matches.sort(key=lambda item: (-item.score, item.path_id))
    return matches[:max(0, int(limit))]


__all__ = ["CausalPathMatch", "CausalPathQuality", "score_causal_path",
           "retrieve_causal_paths"]
