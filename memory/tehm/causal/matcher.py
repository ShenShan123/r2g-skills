"""Explainable matching for causal shadow paths.

The matcher is deliberately evaluation-only.  It distinguishes a path that
shares a compatibility profile from one that also shares the observed
mechanism details; it never grants runtime or promotion authority.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from collections.abc import Mapping

from tehm.ids import stable_dumps

from .evidence_level import evidence_rank


_DETAIL_FIELDS = (
    "action_domain", "transformation_family", "module", "source_state",
    "target_state", "guard", "failure_graph_digest", "causal_context_digest",
)
_QUERY_LIST_FIELDS = ("required_effect", "forbidden_effects",
                      "prior_action_digests")


@dataclass(frozen=True)
class MechanismMatch:
    """A transparent, non-authoritative causal path match receipt."""

    eligible: bool
    score: float
    evidence_weight: float
    family_match: bool
    profile_match: bool
    mechanism_match: bool
    matched_fields: tuple[str, ...] = field(default_factory=tuple)
    mismatched_fields: tuple[str, ...] = field(default_factory=tuple)
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "score": self.score,
            "evidence_weight": self.evidence_weight,
            "family_match": self.family_match,
            "profile_match": self.profile_match,
            "mechanism_match": self.mechanism_match,
            "matched_fields": list(self.matched_fields),
            "mismatched_fields": list(self.mismatched_fields),
            "missing_fields": list(self.missing_fields),
            "reason": self.reason,
        }


def _get(value, key: str, default=None):
    if isinstance(value, Mapping):
        return value.get(key, default)
    # ``sqlite3.Row`` exposes mapping-style indexing without registering as a
    # collections.abc.Mapping.  Keep the matcher independent of the concrete
    # row type so it can consume both DB rows and in-memory candidates.
    try:
        return value[key]
    except (KeyError, IndexError, TypeError):
        pass
    return getattr(value, key, default)


_MISSING = object()


def _support(path) -> dict | None:
    """Decode path support without falling back through corrupt evidence.

    Persisted paths require ``support_json`` to be an object.  A malformed
    support payload must not be replaced with an empty mapping because that
    would activate the coarse family/profile fallback and hide missing
    mechanism witnesses.  A genuinely absent field is retained only for
    legacy in-memory candidates.
    """
    support = _get(path, "support", _MISSING)
    if support is _MISSING:
        support = _get(path, "support_json", _MISSING)
    if support is _MISSING:
        return {}
    if support is None:
        return None
    if isinstance(support, str):
        if not support.strip():
            return None
        try:
            support = json.loads(support)
        except (TypeError, json.JSONDecodeError):
            return None
    return dict(support) if isinstance(support, Mapping) else None


def _signatures(path, support: dict) -> tuple[dict, ...] | None:
    if "mechanism_signatures" in support:
        signatures = support["mechanism_signatures"]
        if (not isinstance(signatures, list) or not signatures or
                any(not isinstance(item, Mapping) for item in signatures)):
            return None
        return tuple(dict(item) for item in signatures)
    if "mechanism_signature" in support:
        one = support["mechanism_signature"]
        return (dict(one),) if isinstance(one, Mapping) else None
    # Paths produced before mechanism signatures were persisted remain
    # searchable only at the coarse family/profile level.
    return ({
        "mechanism_family": _get(path, "mechanism_family"),
        "compatibility_profile": _get(path, "compatibility_profile"),
    },)


def _query_error(query_plan: object) -> str | None:
    """Validate the typed causal query surface before matching.

    A malformed query must not silently become an unconstrained metadata query:
    doing so can turn a caller typo into an apparently successful causal recall.
    The matcher remains evaluation-only, but its negative result must still be
    deterministic and auditable.
    """
    if query_plan is not None and not isinstance(query_plan, Mapping):
        return "malformed_query_plan"
    plan = dict(query_plan or {})
    for field in ("mechanism_signature", "causal_path_features"):
        value = plan.get(field, _MISSING)
        if value is not _MISSING and value is not None and not isinstance(value, Mapping):
            return f"malformed_query_{field}"
    signature = plan.get("mechanism_signature")
    features = plan.get("causal_path_features")
    merged = {}
    if isinstance(features, Mapping):
        merged.update(features)
    if isinstance(signature, Mapping):
        merged.update(signature)
    for field in ("mechanism_family", "compatibility_profile"):
        value = merged.get(field, plan.get(field, _MISSING))
        if value is not _MISSING and value is not None and (
                not isinstance(value, str) or not value.strip()):
            return f"malformed_query_{field}"
    for field in _DETAIL_FIELDS:
        value = merged.get(field, plan.get(field, _MISSING))
        if value is not _MISSING and value is not None and (
                not isinstance(value, str) or not value.strip()):
            return f"malformed_query_{field}"
    for field in _QUERY_LIST_FIELDS:
        value = plan.get(field, _MISSING)
        if value is _MISSING or value is None:
            continue
        if field == "prior_action_digests":
            values = value
        else:
            values = [value] if isinstance(value, str) else value
        if (not isinstance(values, (list, tuple)) or
                any(not isinstance(item, str) or not item.strip()
                    for item in values)):
            return f"malformed_query_{field}"
    return None


def _normal(value) -> str:
    if isinstance(value, (dict, list, tuple)):
        return stable_dumps(value)
    return str(value)


def match_causal_path(path, query_plan: Mapping | None) -> MechanismMatch:
    """Match one path against a frozen query plan.

    Family/profile mismatches are hard vetoes.  When detailed mechanism fields
    are supplied, a path is eligible only if one stored signature agrees with
    every supplied field.  Missing path metadata therefore fails closed rather
    than turning an old coarse path into a false causal transfer.
    """
    query_error = _query_error(query_plan)
    if query_error:
        return MechanismMatch(
            eligible=False, score=0.0, evidence_weight=0.0,
            family_match=False, profile_match=False,
            mechanism_match=False, reason=query_error)
    plan = dict(query_plan or {})
    query_sig = plan.get("mechanism_signature")
    query_sig = dict(query_sig) if isinstance(query_sig, Mapping) else {}
    path_features = plan.get("causal_path_features")
    if isinstance(path_features, Mapping):
        # Explicit causal-path features are an additive query refinement; a
        # mechanism_signature value wins when both spell the same dimension.
        query_sig = {**dict(path_features), **query_sig}
    # ``mechanism_family`` (the observed failure mechanism) and
    # ``transformation_family`` (the executable edit family) are distinct
    # dimensions.  Older code treated the latter as a fallback for the
    # former, which made an R0 metadata query such as
    # ``GUARD_STRENGTHEN`` incorrectly veto every path whose mechanism family
    # was ``HANDSHAKE_COMPLETION``.  Keep the family veto explicit and match
    # the transformation through the typed mechanism signature below.
    query_family = (query_sig.get("mechanism_family") or
                    plan.get("mechanism_family"))
    query_profile = (plan.get("compatibility_profile") or
                     query_sig.get("compatibility_profile"))
    path_family = _get(path, "mechanism_family")
    path_profile = _get(path, "compatibility_profile")
    family_match = bool(query_family is None or str(query_family) == str(path_family))
    profile_match = bool(query_profile is None or
                         str(query_profile) == str(path_profile))
    if not family_match or not profile_match:
        reason = "mechanism_family_mismatch" if not family_match else "compatibility_profile_mismatch"
        return MechanismMatch(
            eligible=False, score=0.0, evidence_weight=0.0,
            family_match=family_match, profile_match=profile_match,
            mechanism_match=False, reason=reason)

    detail_query = {}
    for field in _DETAIL_FIELDS:
        value = query_sig.get(field)
        if value is None:
            value = plan.get(field)
        if value is not None:
            detail_query[field] = value

    support = _support(path)
    if support is None:
        return MechanismMatch(
            eligible=False, score=0.0, evidence_weight=0.0,
            family_match=family_match, profile_match=profile_match,
            mechanism_match=False, reason="malformed_support")
    signatures = _signatures(path, support)
    if signatures is None:
        return MechanismMatch(
            eligible=False, score=0.0, evidence_weight=0.0,
            family_match=family_match, profile_match=profile_match,
            mechanism_match=False, reason="malformed_mechanism_signatures")
    matched: set[str] = set()
    mismatched: set[str] = set()
    missing: set[str] = set()
    best_signature_matches: set[str] = set()
    if detail_query:
        per_signature = []
        for signature in signatures:
            current_match: set[str] = set()
            current_missing: set[str] = set()
            for field, expected in detail_query.items():
                if field not in signature or signature[field] is None:
                    current_missing.add(field)
                elif _normal(signature[field]) == _normal(expected):
                    current_match.add(field)
            per_signature.append((current_match, current_missing))
        best_signature_matches, best_missing = max(
            per_signature, key=lambda pair: (len(pair[0]), -len(pair[1])))
        matched.update(best_signature_matches)
        missing.update(best_missing)
        # A detail mismatch is a veto even when family and profile match.  The
        # receipt still exposes which dimensions disagreed for negative-slice
        # evaluation.
        mismatched.update(set(detail_query) - best_signature_matches - missing)
        mechanism_match = not mismatched and not missing
    else:
        mechanism_match = True

    effects = support.get("primary_effect_keys") or []
    if isinstance(effects, str):
        effects = [effects]
    required_effect = plan.get("required_effect")
    if required_effect is not None:
        required = {str(required_effect)} if isinstance(required_effect, str) else {
            str(item) for item in (required_effect or [])}
        if not required or not required.intersection(str(item) for item in effects):
            return MechanismMatch(
                eligible=False, score=0.0, evidence_weight=0.0,
                family_match=family_match, profile_match=profile_match,
                mechanism_match=mechanism_match,
                matched_fields=tuple(sorted(matched)),
                mismatched_fields=tuple(sorted(mismatched)),
                missing_fields=tuple(sorted(missing)),
                reason="required_effect_not_supported")
    forbidden = plan.get("forbidden_effects") or []
    if isinstance(forbidden, str):
        forbidden = [forbidden]
    if set(str(item) for item in effects).intersection(str(item) for item in forbidden):
        return MechanismMatch(
            eligible=False, score=0.0, evidence_weight=0.0,
            family_match=family_match, profile_match=profile_match,
            mechanism_match=mechanism_match,
            matched_fields=tuple(sorted(matched)),
            mismatched_fields=tuple(sorted(mismatched)),
            missing_fields=tuple(sorted(missing)),
            reason="forbidden_effect_present")

    action_digests = support.get("action_digests") or []
    prior = plan.get("prior_action_digests") or []
    if set(str(item) for item in action_digests).intersection(str(item) for item in prior):
        return MechanismMatch(
            eligible=False, score=0.0, evidence_weight=0.0,
            family_match=family_match, profile_match=profile_match,
            mechanism_match=mechanism_match,
            matched_fields=tuple(sorted(matched)),
            mismatched_fields=tuple(sorted(mismatched)),
            missing_fields=tuple(sorted(missing)),
            reason="prior_action_already_attempted")

    if not mechanism_match:
        return MechanismMatch(
            eligible=False, score=0.0, evidence_weight=0.0,
            family_match=family_match, profile_match=profile_match,
            mechanism_match=False,
            matched_fields=tuple(sorted(matched)),
            mismatched_fields=tuple(sorted(mismatched)),
            missing_fields=tuple(sorted(missing)),
            reason="mechanism_detail_mismatch")

    # Transparent dimension score.  Evidence level is a separate multiplier:
    # an L0/L1 path may be useful for recall experiments but cannot score like
    # replicated/transfer-supported evidence.
    dimensions: list[tuple[bool, float]] = []
    if query_family is not None:
        dimensions.append((family_match, 0.35))
    if query_profile is not None:
        dimensions.append((profile_match, 0.25))
    if detail_query:
        dimensions.append((bool(detail_query) and len(matched) == len(detail_query), 0.40))
    if not dimensions:
        return MechanismMatch(
            eligible=False, score=0.0, evidence_weight=0.0,
            family_match=family_match, profile_match=profile_match,
            mechanism_match=True, reason="query_has_no_causal_constraints")
    raw = sum(weight for passed, weight in dimensions if passed) / sum(
        weight for _, weight in dimensions)
    level = str(_get(path, "evidence_level", "L0_ASSOCIATION"))
    try:
        evidence_weight = 0.5 + 0.5 * evidence_rank(level) / 4.0
    except ValueError:
        evidence_weight = 0.5
    score = round(raw * evidence_weight, 6)
    return MechanismMatch(
        eligible=True, score=score, evidence_weight=evidence_weight,
        family_match=family_match, profile_match=profile_match,
        mechanism_match=True, matched_fields=tuple(sorted(matched)),
        mismatched_fields=tuple(sorted(mismatched)),
        missing_fields=tuple(sorted(missing)), reason="causal_mechanism_match")


__all__ = ["MechanismMatch", "match_causal_path"]
