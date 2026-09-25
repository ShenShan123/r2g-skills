"""Versioned v6 shadow Asset binding from supplied RTL and public knobs.

The TRAIN payload is a source witness, never a portable target answer. This
binding confers neither functional, Memory, nor production authority.
"""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from dataclasses import replace

from tehm.evaluation.research_r5_skid_binding_v6 import TEMPLATE
from tehm.ids import stable_dumps
from tehm.rtl.skid_payload_action_v6 import (
    DOMAIN, PAYLOAD_KEYS, PROFILE, payload_from_source_v6,
)

CONTRACT = "rtl_skid_payload_source_binding_shadow_v6"
SPEC = {"contract": CONTRACT, "domain": DOMAIN, "profile": PROFILE,
        "locator_template": dict(TEMPLATE), "public_context_required": True,
        "proof_scope": "unique_syntactic_mismatch_not_functional",
        "answer_fields_consumed": False}


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _template() -> dict:
    return {"contract": CONTRACT, "spec": copy.deepcopy(SPEC),
            "spec_digest": _digest(SPEC)}


def with_skid_payload_binding_v6(proposal, training_source: str,
                                 training_public_context: Mapping):
    action = proposal.definition.get("action") or {}
    if (action.get("domain") != DOMAIN or
            action.get("payload") != payload_from_source_v6(
                training_source, training_public_context) or
            proposal.compatibility.get("compatibility_profile") != PROFILE):
        raise ValueError("skid v6 proposal does not match source-only action")
    definition = copy.deepcopy(proposal.definition)
    definition["binding_template"] = _template()
    return replace(proposal, definition=definition)


def bind_skid_asset_to_source_v6(asset: Mapping, source: str, *,
                                 design_id: str,
                                 public_context: Mapping | None) -> dict:
    if (not isinstance(asset, Mapping) or not isinstance(design_id, str) or
            not design_id or not isinstance(public_context, Mapping)):
        raise ValueError("skid v6 requires Asset, design_id, and public context")
    definition = asset.get("definition")
    if not isinstance(definition, Mapping) or definition.get("binding_template") != _template():
        raise ValueError("skid v6 Asset has no exact frozen binding template")
    action = definition.get("action") or {}
    compatibility = asset.get("compatibility") or {}
    training_payload = action.get("payload")
    if (not isinstance(training_payload, Mapping) or
            set(training_payload) != PAYLOAD_KEYS or
            training_payload.get("domain") != DOMAIN or
            training_payload.get("compatibility_profile") != PROFILE or
            action.get("domain") != DOMAIN or
            compatibility.get("compatibility_profile") != PROFILE):
        raise ValueError("skid v6 Asset domain/profile/context mismatch")
    payload = payload_from_source_v6(source, public_context)
    bound = copy.deepcopy(dict(asset))
    bound["definition"]["action"]["payload"] = payload
    evidence = {"asset_id": asset.get("asset_id"), "design_id": design_id,
                "source": source, "public_context": dict(public_context),
                "payload": payload, "spec_digest": _digest(SPEC)}
    bound["provenance"] = {**dict(asset.get("provenance") or {}),
                           "bound_design": design_id,
                           "binding_contract": CONTRACT,
                           "binding_source": "rtl_source",
                           "answer_fields_consumed": False,
                           "binding_evidence": evidence,
                           "binding_digest": _digest(evidence)}
    return bound


def verify_skid_binding_v6(bound: Mapping, registered: Mapping) -> bool:
    try:
        evidence = bound["provenance"]["binding_evidence"]
        expected = bind_skid_asset_to_source_v6(
            registered, evidence["source"], design_id=evidence["design_id"],
            public_context=evidence["public_context"])
        return stable_dumps(expected) == stable_dumps(dict(bound))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def is_skid_v6_asset(asset: Mapping) -> bool:
    if not isinstance(asset, Mapping):
        return False
    definition = asset.get("definition")
    if not isinstance(definition, Mapping):
        return False
    template = definition.get("binding_template")
    action = definition.get("action")
    return bool((isinstance(template, Mapping) and
                 template.get("contract") == CONTRACT) or
                (isinstance(action, Mapping) and action.get("domain") == DOMAIN))


__all__ = ["CONTRACT", "SPEC", "with_skid_payload_binding_v6",
           "bind_skid_asset_to_source_v6", "verify_skid_binding_v6",
           "is_skid_v6_asset"]
