"""Stage 3: transparent utility/risk reranking (design doc 9.5, 20.9).

v1 multiplicative score:

    Score = Similarity * Utility * Confidence * (1 - RiskPenalty)

Symbolic veto is final: INAPPLICABLE rules are dropped here regardless of score,
and UNRESOLVED rules are down-weighted (never silently passed). The score stays
fully transparent (each term is reported), so a later neural ranker (f_theta)
can replace the scoring WITHOUT ever overriding the symbolic veto.
"""
from __future__ import annotations

from tehm.retrieval.result import INAPPLICABLE, UNRESOLVED

RERANK_VERSION = "rerank-v0.1"

# Neutral priors for untried rules (design doc 9.5 / 20.9 transparency).
_NEUTRAL_UTILITY = 0.5
_NEUTRAL_CONFIDENCE = 0.5
_UNRESOLVED_PENALTY = 0.5
_RISK_PENALTY_PER_KIND = 0.25
_RISK_PENALTY_CAP = 0.5


def rerank(candidates, *, limit: int = 10) -> list:
    """Reorder recalled candidates by the transparent score; apply the veto.

    ``candidates``: sequence of ``(rule, similarity, applicability_status)``.
    Returns ``RetrievedRule`` items, ranked, up to ``limit``.
    """
    scored = []
    for rule, similarity, status in candidates:
        if status == INAPPLICABLE:
            continue                      # symbolic veto — never overridden
        utility = _utility_score(rule)
        confidence = _confidence_score(rule)
        risk_penalty = _risk_penalty(rule)
        score = similarity * utility * confidence * (1.0 - risk_penalty)
        if status == UNRESOLVED:
            score *= _UNRESOLVED_PENALTY   # down-weight, do not drop
        from tehm.retrieval.result import RetrievedRule
        scored.append(RetrievedRule(
            rule_id=rule["rule_id"],
            candidate_id=f"tehm_rule:{rule['rule_id']}",
            transformation_family=rule.get("transformation_family") or "?",
            similarity=similarity,
            applicability_status=status,
            utility=rule.get("utility") or {},
            confidence=rule.get("confidence") or {},
            risk_penalty=risk_penalty,
            score=round(score, 6),
            source_episodes=list(rule.get("source_episodes") or []),
        ))
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored[:limit]


def _utility_score(rule: dict) -> float:
    utility = rule.get("utility") or {}
    activations = int(utility.get("activations") or 0)
    if activations <= 0:
        return _NEUTRAL_UTILITY
    positive = int(utility.get("positive") or 0)
    neutral = int(utility.get("neutral") or 0)
    return (positive + 0.5 * neutral) / activations


def _confidence_score(rule: dict) -> float:
    confidence = rule.get("confidence") or {}
    rule_conf = confidence.get("rule")
    if isinstance(rule_conf, (int, float)) and 0.0 <= rule_conf <= 1.0:
        return float(rule_conf)
    return _NEUTRAL_CONFIDENCE


def _risk_penalty(rule: dict) -> float:
    risk_profile = rule.get("risk_profile") or []
    if not risk_profile:
        return 0.0
    return min(_RISK_PENALTY_CAP,
               _RISK_PENALTY_PER_KIND * len(risk_profile))
