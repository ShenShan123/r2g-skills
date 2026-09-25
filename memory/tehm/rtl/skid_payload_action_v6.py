"""DEV-only v6 source-bound skid action; not registered as a Memory Asset.

The payload is recomputed from supplied RTL and public context before any edit.
This module neither reads an oracle nor confers TRAIN or transfer authority.
"""
from __future__ import annotations

from collections.abc import Mapping

from tehm.evaluation.research_r5_skid_binding_v6 import (
    PROFILE, TEMPLATE, apply_bound_skid_payload_v6, bind_skid_payload_v6,
)

DOMAIN = "rtl.SKID_TEMP_PAYLOAD_RESTORE_SHADOW_V6"
PAYLOAD_KEYS = frozenset({
    "domain", "compatibility_profile", "module", "public_context",
    "source_sha256", "witness_digest",
})


def payload_from_source_v6(source: str, public_context: Mapping) -> dict:
    binding = bind_skid_payload_v6(
        {"binding_template": TEMPLATE}, source, public_context)
    if binding.get("status") != "BOUND":
        raise ValueError(f"skid v6 source binding rejected: {binding.get('reason')}")
    witness = binding["witness"]
    return {"domain": DOMAIN, "compatibility_profile": PROFILE,
            "module": witness["module"],
            "public_context": dict(public_context),
            "source_sha256": witness["source_sha256"],
            "witness_digest": binding["witness_digest"]}


def apply_skid_payload_action_v6(source: str, payload: Mapping) -> tuple[str, dict]:
    if not isinstance(payload, Mapping) or set(payload) != PAYLOAD_KEYS:
        raise ValueError("skid v6 action payload fields are not exact")
    context = payload.get("public_context")
    if not isinstance(context, Mapping):
        raise ValueError("skid v6 public context is missing")
    expected = payload_from_source_v6(source, context)
    if dict(payload) != expected:
        raise ValueError("skid v6 action payload is stale or mismatched")
    binding = bind_skid_payload_v6(
        {"binding_template": TEMPLATE}, source, context)
    edited, receipt = apply_bound_skid_payload_v6(
        {"binding_template": TEMPLATE}, source, context, binding)
    return edited, {**receipt, "domain": DOMAIN,
                    "compatibility_profile": PROFILE,
                    "source_binding_rederived": True}


__all__ = ["DOMAIN", "PROFILE", "PAYLOAD_KEYS", "payload_from_source_v6",
           "apply_skid_payload_action_v6"]
