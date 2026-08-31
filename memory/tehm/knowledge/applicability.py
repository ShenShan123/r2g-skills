"""Fail-closed positive/negative applicability for knowledge claims."""
from __future__ import annotations

from collections.abc import Mapping

from .claims import MechanismKnowledge
from .receipts import KnowledgeApplicabilityReceipt


def _matches(entry: Mapping, context: Mapping) -> bool:
    return all(key in context and context[key] == value
               for key, value in entry.items())


def evaluate_applicability(
        knowledge: MechanismKnowledge, context: Mapping) -> KnowledgeApplicabilityReceipt:
    if not isinstance(knowledge, MechanismKnowledge):
        raise TypeError("knowledge applicability requires MechanismKnowledge")
    if not isinstance(context, Mapping):
        raise ValueError("knowledge applicability context must be an object")
    context = dict(context)
    if (knowledge.mechanism_family is not None and
            context.get("mechanism_family") != knowledge.mechanism_family):
        return KnowledgeApplicabilityReceipt(
            knowledge.object_id, False, reason="mechanism_family_mismatch")
    if (knowledge.compatibility_profile is not None and
            context.get("compatibility_profile") != knowledge.compatibility_profile):
        return KnowledgeApplicabilityReceipt(
            knowledge.object_id, False, reason="compatibility_profile_mismatch")
    positive = tuple(
        str(index) for index, entry in enumerate(knowledge.positive_applicability)
        if _matches(entry, context))
    negative = tuple(
        str(index) for index, entry in enumerate(knowledge.negative_applicability)
        if _matches(entry, context))
    if negative:
        return KnowledgeApplicabilityReceipt(
            knowledge.object_id, False, positive, negative,
            reason="negative_applicability")
    if not positive:
        return KnowledgeApplicabilityReceipt(
            knowledge.object_id, False, positive, negative,
            reason="positive_applicability_missing")
    return KnowledgeApplicabilityReceipt(
        knowledge.object_id, True, positive, negative, reason="applicable")


__all__ = ["evaluate_applicability"]
