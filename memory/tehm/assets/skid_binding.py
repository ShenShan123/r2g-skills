"""R5 shadow-only, source-replayable Asset binding for one skid payload shape.

This is a content-bound Asset interface, not a Knowledge/admission shortcut.
The exact DEV locator is reused as a frozen syntactic recognizer; only a
qualified TRAIN repair may later supply an authoritative Asset provenance.
"""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from dataclasses import replace

from tehm.evaluation.research_r5_skid_binding import CONTEXT, TEMPLATE
from tehm.ids import stable_dumps
from tehm.rtl.skid_payload_action import DOMAIN, PAYLOAD_KEYS, PROFILE, payload_from_source


CONTRACT = "rtl_skid_payload_source_binding_shadow_v1"
SPEC = {"contract": CONTRACT, "domain": DOMAIN, "profile": PROFILE,
        "public_context": dict(CONTEXT), "locator_template": dict(TEMPLATE),
        "proof_scope": "unique_syntactic_mismatch_not_functional",
        "answer_fields_consumed": False}


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _template() -> dict:
    return {"contract": CONTRACT, "spec": copy.deepcopy(SPEC),
            "spec_digest": _digest(SPEC)}


def with_skid_payload_binding(proposal, training_source: str):
    """Freeze the source-only locator against a TRAIN proposal, without admission."""
    action = proposal.definition.get("action") or {}
    if (action.get("domain") != DOMAIN or
            action.get("payload") != payload_from_source(training_source) or
            proposal.compatibility.get("compatibility_profile") != PROFILE):
        raise ValueError("skid training proposal does not match source-only action")
    definition = copy.deepcopy(proposal.definition)
    definition["binding_template"] = _template()
    return replace(proposal, definition=definition)


def bind_skid_asset_to_source(asset: Mapping, source: str, *, design_id: str) -> dict:
    """Bind registered Asset to buggy RTL bytes only, with deterministic replay."""
    if (not isinstance(asset, Mapping) or
            not isinstance(design_id, str) or not design_id):
        raise ValueError("skid asset and design_id are required")
    definition = asset.get("definition")
    if not isinstance(definition, Mapping) or definition.get("binding_template") != _template():
        raise ValueError("skid Asset has no exact frozen binding template")
    action = definition.get("action") or {}
    compatibility = asset.get("compatibility") or {}
    training_payload = action.get("payload")
    if (not isinstance(training_payload, Mapping) or
            set(training_payload) != PAYLOAD_KEYS or
            training_payload.get("domain") != DOMAIN or
            training_payload.get("compatibility_profile") != PROFILE or
            action.get("domain") != DOMAIN or
            compatibility.get("compatibility_profile") != PROFILE or
            training_payload.get("public_context") != CONTEXT):
        raise ValueError("skid Asset domain/profile/context mismatch")
    payload = payload_from_source(source)
    bound = copy.deepcopy(dict(asset))
    bound["definition"]["action"]["payload"] = payload
    evidence = {"asset_id": asset.get("asset_id"), "design_id": design_id,
                "source": source, "public_context": dict(CONTEXT),
                "payload": payload, "spec_digest": _digest(SPEC)}
    bound["provenance"] = {**dict(asset.get("provenance") or {}),
                           "bound_design": design_id,
                           "binding_contract": CONTRACT,
                           "binding_source": "rtl_source",
                           "answer_fields_consumed": False,
                           "binding_evidence": evidence,
                           "binding_digest": _digest(evidence)}
    return bound


def verify_skid_binding(bound: Mapping, registered: Mapping) -> bool:
    try:
        evidence = bound["provenance"]["binding_evidence"]
        expected = bind_skid_asset_to_source(
            registered, evidence["source"], design_id=evidence["design_id"])
        return stable_dumps(expected) == stable_dumps(dict(bound))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
