"""DEV/TRAIN v7 source-bound skid action, without Asset authority by itself.

All target-specific payload fields are rederived from source and public
context. No oracle, reference RTL, mutation metadata, or file path is read.
"""
from __future__ import annotations

from collections.abc import Mapping

from tehm.evaluation.research_r5_skid_binding_v7 import (
    PROFILE, TEMPLATE, apply_bound_skid_payload_v7, bind_skid_payload_v7,
)

DOMAIN = "rtl.SKID_TEMP_PAYLOAD_RESTORE_SHADOW_V7"
PAYLOAD_KEYS = frozenset({
    "domain", "compatibility_profile", "module", "public_context",
    "source_sha256", "witness_digest",
})


def payload_from_source_v7(source: str, public_context: Mapping) -> dict:
    binding = bind_skid_payload_v7(
        {"binding_template": TEMPLATE}, source, public_context)
    if binding.get("status") != "BOUND":
        raise ValueError(f"skid v7 source binding rejected: {binding.get('reason')}")
    witness = binding["witness"]
    return {"domain": DOMAIN, "compatibility_profile": PROFILE,
            "module": witness["module"],
            "public_context": dict(public_context),
            "source_sha256": witness["source_sha256"],
            "witness_digest": binding["witness_digest"]}


def apply_skid_payload_action_v7(source: str, payload: Mapping) -> tuple[str, dict]:
    if not isinstance(payload, Mapping) or set(payload) != PAYLOAD_KEYS:
        raise ValueError("skid v7 action payload fields are not exact")
    context = payload.get("public_context")
    if not isinstance(context, Mapping):
        raise ValueError("skid v7 public context is missing")
    expected = payload_from_source_v7(source, context)
    if dict(payload) != expected:
        raise ValueError("skid v7 action payload is stale or mismatched")
    binding = bind_skid_payload_v7(
        {"binding_template": TEMPLATE}, source, context)
    edited, receipt = apply_bound_skid_payload_v7(
        {"binding_template": TEMPLATE}, source, context, binding)
    return edited, {**receipt, "domain": DOMAIN,
                    "compatibility_profile": PROFILE,
                    "source_binding_rederived": True}


__all__ = ["DOMAIN", "PROFILE", "PAYLOAD_KEYS", "payload_from_source_v7",
           "apply_skid_payload_action_v7"]
