"""Source-bound I2C NACK v3 Asset bridge; authority requires separate strict TRAIN replay."""
from collections.abc import Mapping
import copy
from dataclasses import replace
import hashlib

from tehm.ids import stable_dumps
from tehm.rtl.i2c_nack_action_v3 import (
    DOMAIN, PROFILE, PAYLOAD_KEYS, payload_from_source_i2c_v3,
)

CONTRACT = "rtl_i2c_nack_status_latch_source_binding_shadow_v3"
SPEC = {"contract": CONTRACT, "domain": DOMAIN, "profile": PROFILE,
        "binder": "research_r5_i2c_nack_binding_dev_v3@cefe16c",
        "closure_transport": "tehm-closure-v1",
        "proof_scope": "unique_syntactic_public_status_latch_only",
        "answer_fields_consumed": False}


def digest(value):
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def template():
    return {"contract": CONTRACT, "spec": copy.deepcopy(SPEC), "spec_digest": digest(SPEC)}


def with_i2c_nack_binding_v3(proposal, source, public_context):
    action = proposal.definition.get("action") or {}
    if (action.get("domain") != DOMAIN or
            action.get("payload") != payload_from_source_i2c_v3(source, public_context) or
            proposal.compatibility.get("compatibility_profile") != PROFILE):
        raise ValueError("I2C v3 proposal is not source-derived")
    definition = copy.deepcopy(proposal.definition)
    definition["binding_template"] = template()
    return replace(proposal, definition=definition)


def bind_i2c_asset_to_source_v3(asset, source, *, design_id, public_context):
    if not isinstance(asset, Mapping) or not isinstance(design_id, str) or not design_id:
        raise ValueError("I2C v3 Asset and opaque design ID required")
    definition = asset.get("definition")
    compatibility = asset.get("compatibility")
    if (not isinstance(definition, Mapping) or definition.get("binding_template") != template()
            or not isinstance(compatibility, Mapping)
            or compatibility.get("compatibility_profile") != PROFILE
            or compatibility.get("action_domain") != DOMAIN):
        raise ValueError("I2C v3 template or compatibility mismatch")
    action = definition.get("action")
    old = action.get("payload") if isinstance(action, Mapping) else None
    if (not isinstance(old, Mapping) or set(old) != PAYLOAD_KEYS or
            action.get("domain") != DOMAIN or old.get("domain") != DOMAIN or
            old.get("compatibility_profile") != PROFILE):
        raise ValueError("I2C v3 training payload shape mismatch")
    payload = payload_from_source_i2c_v3(source, public_context)
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


def is_i2c_v3_asset(asset):
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
