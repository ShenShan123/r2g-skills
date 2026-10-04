"""Core shadow action over the committed I2C NACK v3 source-only binder.

Only the v3 ``bind``/``apply`` decide the edit (v3 delegates the three v2 interfaces to
the frozen v2 binder). The freecores four-role closure reuses the frozen v2
``tehm-closure-v1`` transport. Grants no TRAIN or Memory authority.
"""
from collections.abc import Mapping
import copy
import hashlib
from pathlib import Path

from tehm.evaluation import research_r5_i2c_nack_binding_dev_v3 as binder
from tehm.rtl import i2c_nack_action_v2 as transport

DOMAIN = "rtl.I2C_NACK_STATUS_LATCH_V3"
PROFILE = binder.PROFILE
PAYLOAD_KEYS = frozenset({"domain", "compatibility_profile", "public_context",
                          "source_sha256", "action_digest", "binding_contract"})
V3_SHA256 = "26cb04bf8d0a147769cc1b23b32274828f7fe82e731fdf679d91981da17ef75f"
if hashlib.sha256(Path(binder.__file__).read_bytes()).hexdigest() != V3_SHA256:
    raise ImportError("I2C v3 binder drift")

ASSET = {"binding_template": binder.TEMPLATE}
CLOSURE_INTERFACE = transport.binder.INTERFACE  # wishbone_status_err_bit_7


def _binder_input(source: str, public_context: Mapping):
    if not isinstance(source, str) or not isinstance(public_context, Mapping):
        raise ValueError("I2C v3 requires str source and explicit public context")
    if public_context.get("interface") == CLOSURE_INTERFACE:
        return transport.decode_closure(source)
    if source.startswith("// @tehm-closure-v1 "):
        raise ValueError("I2C v3 closure supplied for a single-file interface")
    return source


def source_binding(source, public_context):
    binding = binder.bind(ASSET, _binder_input(source, public_context), public_context)
    if binding.get("status") != "BOUND":
        raise ValueError("I2C v3 source binding rejected: %s/%s" % (binding.get("status"), binding.get("reason")))
    return binding


def payload_from_source_i2c_v3(source, public_context):
    binding = source_binding(source, public_context)
    return {"domain": DOMAIN, "compatibility_profile": PROFILE,
            "public_context": copy.deepcopy(dict(public_context)),
            "source_sha256": "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "action_digest": binding["witness"]["action_digest"],
            "binding_contract": binder.CONTRACT}


def apply_i2c_nack_action_v3(source, payload):
    if not isinstance(payload, Mapping) or set(payload) != PAYLOAD_KEYS:
        raise ValueError("I2C v3 action requires exact payload fields")
    context = payload["public_context"]
    if dict(payload) != payload_from_source_i2c_v3(source, context):
        raise ValueError("I2C v3 action payload is stale or tampered")
    closure = _binder_input(source, context)
    binding = source_binding(source, context)
    candidate, receipt = binder.apply(ASSET, closure, context, binding)
    new_source = transport.encode_closure(candidate) if isinstance(candidate, dict) else candidate
    return new_source, {**receipt, "domain": DOMAIN, "compatibility_profile": PROFILE,
                        "rewritten": 1, "source_binding_rederived": True,
                        "memory_authority_granted": False}
