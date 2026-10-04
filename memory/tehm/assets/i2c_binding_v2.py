"""Source-bound I2C NACK v2 Asset bridge; authority requires separate strict TRAIN replay."""
from collections.abc import Mapping
import copy
import hashlib

from tehm.ids import stable_dumps
from tehm.rtl.i2c_nack_action_v2 import DOMAIN, PROFILE

CONTRACT = "rtl_i2c_nack_status_latch_source_binding_shadow_v2"
SPEC = {"contract": CONTRACT, "domain": DOMAIN, "profile": PROFILE,
        "binder": "research_r5_i2c_nack_binding_dev_v2@317c8a3",
        "closure_transport": "tehm-closure-v1",
        "proof_scope": "unique_syntactic_public_status_latch_only",
        "answer_fields_consumed": False}


def digest(value):
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def template():
    return {"contract": CONTRACT, "spec": copy.deepcopy(SPEC), "spec_digest": digest(SPEC)}


def is_i2c_v2_asset(asset):
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
