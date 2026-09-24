"""R5 shadow-only action for the explicit-parameter skid payload DEV v2 locator."""
from __future__ import annotations

from collections.abc import Mapping

from tehm.evaluation.research_r5_skid_binding_v2 import (
    TEMPLATE, apply_bound_skid_payload_v2, bind_skid_payload_v2,
)


DOMAIN = "rtl.SKID_TEMP_PAYLOAD_RESTORE_SHADOW_V2"
PROFILE = "rtl.skid.temp_payload.v2.dev"
PAYLOAD_KEYS = frozenset({
    "domain", "compatibility_profile", "module", "public_context",
    "source_sha256", "witness_digest",
})


def payload_from_source_v2(source: str, public_context: Mapping) -> dict:
    """Derive one payload from buggy source and explicitly supplied public knobs."""
    binding = bind_skid_payload_v2(
        {"binding_template": TEMPLATE}, source, public_context)
    if binding.get("status") != "BOUND":
        raise ValueError(f"skid v2 source binding rejected: {binding.get('reason')}")
    witness = binding["witness"]
    return {"domain": DOMAIN, "compatibility_profile": PROFILE,
            "module": witness["module"],
            "public_context": dict(public_context),
            "source_sha256": witness["source_sha256"],
            "witness_digest": binding["witness_digest"]}


def apply_skid_payload_action_v2(source: str, payload: Mapping) -> tuple[str, dict]:
    """Rebind exact source/context before performing one witnessed RHS edit."""
    if not isinstance(payload, Mapping) or set(payload) != PAYLOAD_KEYS:
        raise ValueError("skid v2 action payload fields are not exact")
    context = payload.get("public_context")
    if not isinstance(context, Mapping):
        raise ValueError("skid v2 public context is missing")
    expected = payload_from_source_v2(source, context)
    if dict(payload) != expected:
        raise ValueError("skid v2 action payload is stale or mismatched")
    binding = bind_skid_payload_v2(
        {"binding_template": TEMPLATE}, source, context)
    edited, receipt = apply_bound_skid_payload_v2(
        {"binding_template": TEMPLATE}, source, context, binding)
    return edited, {**receipt, "domain": DOMAIN,
                    "compatibility_profile": PROFILE,
                    "source_binding_rederived": True}
