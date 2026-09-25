"""R5 DEV v4 source-bound shadow action; no oracle or production authority."""
from __future__ import annotations

from collections.abc import Mapping

from tehm.evaluation.research_r5_skid_binding_v4 import (
    TEMPLATE, apply_bound_skid_payload_v4, bind_skid_payload_v4,
)


DOMAIN = "rtl.SKID_TEMP_PAYLOAD_RESTORE_SHADOW_V4"
PROFILE = "rtl.skid.temp_payload.v4.dev"
PAYLOAD_KEYS = frozenset({
    "domain", "compatibility_profile", "module", "public_context",
    "source_sha256", "witness_digest",
})


def payload_from_source_v4(source: str, public_context: Mapping) -> dict:
    """Derive one exact payload from buggy source and public configuration."""
    binding = bind_skid_payload_v4(
        {"binding_template": TEMPLATE}, source, public_context)
    if binding.get("status") != "BOUND":
        raise ValueError(f"skid v4 source binding rejected: {binding.get('reason')}")
    witness = binding["witness"]
    return {"domain": DOMAIN, "compatibility_profile": PROFILE,
            "module": witness["module"],
            "public_context": dict(public_context),
            "source_sha256": witness["source_sha256"],
            "witness_digest": binding["witness_digest"]}


def apply_skid_payload_action_v4(source: str, payload: Mapping) -> tuple[str, dict]:
    """Re-derive the binding before a single witnessed RTL RHS edit."""
    if not isinstance(payload, Mapping) or set(payload) != PAYLOAD_KEYS:
        raise ValueError("skid v4 action payload fields are not exact")
    context = payload.get("public_context")
    if not isinstance(context, Mapping):
        raise ValueError("skid v4 public context is missing")
    expected = payload_from_source_v4(source, context)
    if dict(payload) != expected:
        raise ValueError("skid v4 action payload is stale or mismatched")
    binding = bind_skid_payload_v4(
        {"binding_template": TEMPLATE}, source, context)
    edited, receipt = apply_bound_skid_payload_v4(
        {"binding_template": TEMPLATE}, source, context, binding)
    return edited, {**receipt, "domain": DOMAIN,
                    "compatibility_profile": PROFILE,
                    "source_binding_rederived": True}


__all__ = ["DOMAIN", "PROFILE", "PAYLOAD_KEYS", "payload_from_source_v4",
           "apply_skid_payload_action_v4"]
