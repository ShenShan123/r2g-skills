"""Source-bound v8 Asset bridge; authority requires separate strict TRAIN replay."""
from collections.abc import Mapping
import copy
import hashlib

from tehm.ids import stable_dumps
from tehm.rtl.skid_payload_action_v8 import DOMAIN, PROFILE

CONTRACT = "rtl_skid_payload_source_binding_shadow_v8"
SPEC = {"contract": CONTRACT, "domain": DOMAIN, "profile": PROFILE,
        "grammar_dispatch": "explicit_macro_context_mux_v8_else_frozen_v7",
        "proof_scope": "unique_syntactic_mismatch_not_functional",
        "answer_fields_consumed": False}


def digest(value):
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def template():
    return {"contract": CONTRACT, "spec": copy.deepcopy(SPEC), "spec_digest": digest(SPEC)}


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
