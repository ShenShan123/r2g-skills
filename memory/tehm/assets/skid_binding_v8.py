"""Source-bound v8 draft Asset bridge; raw TRAIN authority remains closed."""
from collections.abc import Mapping
import copy
from dataclasses import replace
import hashlib

from tehm.ids import stable_dumps
from tehm.rtl.skid_payload_action_v8 import (
    DOMAIN, PROFILE, PAYLOAD_KEYS, payload_from_source_v8,
)

CONTRACT = "rtl_skid_payload_source_binding_shadow_v8"
SPEC = {"contract": CONTRACT, "domain": DOMAIN, "profile": PROFILE,
        "grammar_dispatch": "explicit_macro_context_mux_v8_else_frozen_v7",
        "proof_scope": "unique_syntactic_mismatch_not_functional",
        "answer_fields_consumed": False}


def digest(value):
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def template():
    return {"contract": CONTRACT, "spec": copy.deepcopy(SPEC), "spec_digest": digest(SPEC)}


def with_skid_payload_binding_v8(proposal, source, public_context):
    action = proposal.definition.get("action") or {}
    if (action.get("domain") != DOMAIN or
            action.get("payload") != payload_from_source_v8(source, public_context) or
            proposal.compatibility.get("compatibility_profile") != PROFILE):
        raise ValueError("v8 proposal is not source-derived")
    definition = copy.deepcopy(proposal.definition)
    definition["binding_template"] = template()
    return replace(proposal, definition=definition)


def bind_skid_asset_to_source_v8(asset, source, *, design_id, public_context):
    if not isinstance(asset, Mapping) or not isinstance(design_id, str) or not design_id:
        raise ValueError("v8 Asset and opaque design ID required")
    definition = asset.get("definition")
    compatibility = asset.get("compatibility")
    if (not isinstance(definition, Mapping) or definition.get("binding_template") != template()
            or not isinstance(compatibility, Mapping)
            or compatibility.get("compatibility_profile") != PROFILE
            or compatibility.get("action_domain") != DOMAIN):
        raise ValueError("v8 template or compatibility mismatch")
    action = definition.get("action")
    old = action.get("payload") if isinstance(action, Mapping) else None
    if (not isinstance(old, Mapping) or set(old) != PAYLOAD_KEYS or
            action.get("domain") != DOMAIN or old.get("domain") != DOMAIN or
            old.get("compatibility_profile") != PROFILE):
        raise ValueError("v8 training payload shape mismatch")
    payload = payload_from_source_v8(source, public_context)
    bound = copy.deepcopy(dict(asset))
    bound["definition"]["action"]["payload"] = payload
    evidence = {"asset_id": asset.get("asset_id"), "design_id": design_id,
                "source": source, "public_context": copy.deepcopy(dict(public_context)),
                "payload": payload, "spec_digest": digest(SPEC)}
    bound["provenance"] = {**dict(asset.get("provenance") or {}),
                           "bound_design": design_id, "binding_contract": CONTRACT,
                           "binding_source": "rtl_source", "answer_fields_consumed": False,
                           "binding_evidence": evidence, "binding_digest": digest(evidence)}
    return bound


def verify_skid_binding_v8(bound, registered):
    try:
        evidence = bound["provenance"]["binding_evidence"]
        expected = bind_skid_asset_to_source_v8(
            registered, evidence["source"], design_id=evidence["design_id"],
            public_context=evidence["public_context"])
        return stable_dumps(expected) == stable_dumps(dict(bound))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def is_skid_v8_asset(asset):
    if not isinstance(asset, Mapping):
        return False
    definition = asset.get("definition")
    compatibility = asset.get("compatibility")
    if isinstance(compatibility, Mapping) and (
            compatibility.get("compatibility_profile") == PROFILE or
            compatibility.get("action_domain") == DOMAIN):
        return True
    if not isinstance(definition, Mapping):
        return False
    binding = definition.get("binding_template")
    action = definition.get("action")
    payload = action.get("payload") if isinstance(action, Mapping) else None
    return bool((isinstance(binding, Mapping) and binding.get("contract") == CONTRACT) or
                (isinstance(action, Mapping) and action.get("domain") == DOMAIN) or
                (isinstance(payload, Mapping) and (payload.get("domain") == DOMAIN or
                    payload.get("compatibility_profile") == PROFILE)))
