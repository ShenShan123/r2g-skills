"""R5 shadow-only source replay for the two-shape DEV v2 Asset template.

Registration remains draft.  Neither this binding nor its replay grants
Knowledge authority, Asset promotion, or independent oracle success.
"""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from dataclasses import replace

from tehm.evaluation.research_r5_skid_binding_v2 import TEMPLATE
from tehm.ids import stable_dumps
from tehm.rtl.skid_payload_action_v2 import (
    DOMAIN, PAYLOAD_KEYS, PROFILE, payload_from_source_v2,
)


CONTRACT = "rtl_skid_payload_source_binding_shadow_v2"
SPEC = {"contract": CONTRACT, "domain": DOMAIN, "profile": PROFILE,
        "locator_template": dict(TEMPLATE),
        "public_context_required": True,
        "proof_scope": "unique_syntactic_mismatch_not_functional",
        "answer_fields_consumed": False}


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _template() -> dict:
    return {"contract": CONTRACT, "spec": copy.deepcopy(SPEC),
            "spec_digest": _digest(SPEC)}


def with_skid_payload_binding_v2(proposal, training_source: str,
                                 training_public_context: Mapping):
    """Attach the frozen locator to a proposal; does not assert TRAIN validity."""
    action = proposal.definition.get("action") or {}
    if (action.get("domain") != DOMAIN or
            action.get("payload") != payload_from_source_v2(
                training_source, training_public_context) or
            proposal.compatibility.get("compatibility_profile") != PROFILE):
        raise ValueError("skid v2 proposal does not match source-only action")
    definition = copy.deepcopy(proposal.definition)
    definition["binding_template"] = _template()
    return replace(proposal, definition=definition)


def bind_skid_asset_to_source_v2(asset: Mapping, source: str, *,
                                 design_id: str, public_context: Mapping | None) -> dict:
    """Bind one registered Asset to exact buggy RTL and explicit public knobs."""
    if (not isinstance(asset, Mapping) or not isinstance(design_id, str) or
            not design_id or not isinstance(public_context, Mapping)):
        raise ValueError("skid v2 requires Asset, design_id, and public context")
    definition = asset.get("definition")
    if not isinstance(definition, Mapping) or definition.get("binding_template") != _template():
        raise ValueError("skid v2 Asset has no exact frozen binding template")
    action = definition.get("action") or {}
    compatibility = asset.get("compatibility") or {}
    training_payload = action.get("payload")
    if (not isinstance(training_payload, Mapping) or
            set(training_payload) != PAYLOAD_KEYS or
            training_payload.get("domain") != DOMAIN or
            training_payload.get("compatibility_profile") != PROFILE or
            action.get("domain") != DOMAIN or
            compatibility.get("compatibility_profile") != PROFILE):
        raise ValueError("skid v2 Asset domain/profile/context mismatch")
    # The training payload must still be a real, source-bound witness.  It
    # cannot be re-derived here without the training source, but its exact
    # frozen template and registry digest are independently replayed by the
    # caller.  Target context may differ from the training context.
    payload = payload_from_source_v2(source, public_context)
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


def verify_skid_binding_v2(bound: Mapping, registered: Mapping) -> bool:
    try:
        evidence = bound["provenance"]["binding_evidence"]
        expected = bind_skid_asset_to_source_v2(
            registered, evidence["source"], design_id=evidence["design_id"],
            public_context=evidence["public_context"])
        return stable_dumps(expected) == stable_dumps(dict(bound))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
