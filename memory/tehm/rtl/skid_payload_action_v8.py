"""Shadow core action over frozen v7 and mux-v8 source-only grammars.

The public macro-context field selects the grammar, never source identity or
oracle results. This software extension grants no TRAIN or Memory authority.
"""
from collections.abc import Mapping
import copy

from tehm.evaluation import research_r5_skid_binding_v7 as legacy
from tehm.evaluation import research_r5_skid_binding_v8 as mux

DOMAIN = "rtl.SKID_TEMP_PAYLOAD_RESTORE_SHADOW_V8"
PROFILE = "rtl.skid.payload.v8.dev"
PAYLOAD_KEYS = frozenset({"domain", "compatibility_profile", "module",
                          "public_context", "source_sha256", "witness_digest",
                          "binding_contract"})


def source_binding(source, public_context):
    if not isinstance(public_context, Mapping):
        raise ValueError("v8 requires explicit public context")
    binder = mux if "defined_macros" in public_context else legacy
    bind = mux.bind_skid_payload_v8 if binder is mux else legacy.bind_skid_payload_v7
    binding = bind({"binding_template": binder.TEMPLATE}, source, public_context)
    if binding.get("status") != "BOUND":
        raise ValueError("v8 source binding rejected: " + str(binding.get("reason")))
    return binder, binding


def payload_from_source_v8(source, public_context):
    binder, binding = source_binding(source, public_context)
    return {"domain": DOMAIN, "compatibility_profile": PROFILE,
            "module": binding["witness"]["module"],
            "public_context": copy.deepcopy(dict(public_context)),
            "source_sha256": binding["witness"]["source_sha256"],
            "witness_digest": binding["witness_digest"],
            "binding_contract": binder.CONTRACT}


def apply_skid_payload_action_v8(source, payload):
    if not isinstance(payload, Mapping) or set(payload) != PAYLOAD_KEYS:
        raise ValueError("v8 action requires exact payload fields")
    context = payload["public_context"]
    expected = payload_from_source_v8(source, context)
    if dict(payload) != expected:
        raise ValueError("v8 action payload is stale or tampered")
    binder, binding = source_binding(source, context)
    apply = mux.apply_bound_skid_payload_v8 if binder is mux else legacy.apply_bound_skid_payload_v7
    candidate, receipt = apply({"binding_template": binder.TEMPLATE}, source, context, binding)
    return candidate, {**receipt, "domain": DOMAIN, "compatibility_profile": PROFILE,
                       "source_binding_rederived": True,
                       "memory_authority_granted": False}
