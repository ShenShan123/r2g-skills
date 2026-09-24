"""R5 shadow-only, source-rebound skid payload action.

The locator remains the frozen DEV generation. This adapter gives the core
RTL executor an exact action payload and refuses stale or answer-derived
coordinates. It does not register an Asset or grant Memory authority.
"""
from __future__ import annotations

from collections.abc import Mapping

from tehm.evaluation.research_r5_skid_binding import (
    CONTEXT, TEMPLATE, apply_bound_skid_payload, bind_skid_payload,
)


DOMAIN = "rtl.SKID_TEMP_PAYLOAD_RESTORE_SHADOW"
PROFILE = "rtl.skid.temp_payload.v1.dev"
PAYLOAD_KEYS = frozenset({
    "domain", "compatibility_profile", "module", "public_context",
    "source_sha256", "witness_digest",
})


def payload_from_source(source: str) -> dict:
    """Derive one executable payload using only the buggy RTL and public scope."""
    result = bind_skid_payload({"binding_template": TEMPLATE}, source, CONTEXT)
    if result.get("status") != "BOUND":
        raise ValueError(f"skid source binding rejected: {result.get('reason')}")
    witness = result["witness"]
    return {"domain": DOMAIN, "compatibility_profile": PROFILE,
            "module": witness["module"], "public_context": dict(CONTEXT),
            "source_sha256": witness["source_sha256"],
            "witness_digest": result["witness_digest"]}


def apply_skid_payload_action(source: str, payload: Mapping) -> tuple[str, dict]:
    """Re-derive the unique source witness before one actual RTL edit."""
    if not isinstance(payload, Mapping) or set(payload) != PAYLOAD_KEYS:
        raise ValueError("skid action payload fields are not exact")
    expected = payload_from_source(source)
    if dict(payload) != expected:
        raise ValueError("skid action payload is stale or mismatched")
    binding = bind_skid_payload({"binding_template": TEMPLATE}, source, CONTEXT)
    edited, receipt = apply_bound_skid_payload(
        {"binding_template": TEMPLATE}, source, CONTEXT, binding)
    return edited, {**receipt, "domain": DOMAIN,
                    "compatibility_profile": PROFILE,
                    "source_binding_rederived": True}
