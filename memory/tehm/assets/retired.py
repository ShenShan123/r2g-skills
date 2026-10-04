"""R5 research generations retired from the main tree (cleanup 2026-10-02).

The main tree keeps only the current R5 generation (the I2C v3 line). Assets, scoped executions
and evidence of the retired generations -- the skid-payload line v1-v9 and the I2C v2 line -- are
never eligible here: every dispatcher rejects them explicitly (fail closed) instead of falling
through to a generic gate. Replay them in their frozen worktree under
``_r5_pilot/software/`` (frozen-gen2 ... frozen-gen6, frozen-i2c-v2), which holds the exact code
they were produced with. Record: memory/evaluation/tehm_code_cleanup_20261002.md.
"""
from __future__ import annotations

from collections.abc import Mapping

SKID = "r5_skid_v1_v9"
I2C_V2 = "r5_i2c_v2"

_SKID_DOMAIN_PREFIX = "rtl.SKID_"
_SKID_PROFILE_PREFIX = "rtl.skid."
_SKID_CONTRACT_PREFIX = "rtl_skid_payload_source_binding_"
_I2C_V2_DOMAIN = "rtl.I2C_NACK_STATUS_LATCH_V2"
_I2C_V2_PROFILE = "rtl.i2c.nack_status_latch.v2.dev"
_I2C_V2_CONTRACT = "rtl_i2c_nack_status_latch_source_binding_shadow_v2"

RETIRED_SCOPED_EXECUTION_VERSIONS = frozenset({
    "tehm-r5-rtl-train-scoped-v1", "tehm-r5-rtl-train-scoped-v2", "tehm-r5-rtl-train-scoped-v3",
    "tehm-r5-rtl-train-scoped-v4", "tehm-r5-rtl-train-scoped-v5", "tehm-r5-rtl-train-scoped-v8",
    "tehm-r5-rtl-train-scoped-i2c-v2"})


def _generation_of(domain=None, profile=None, contract=None) -> str | None:
    if (isinstance(domain, str) and domain.startswith(_SKID_DOMAIN_PREFIX) or
            isinstance(profile, str) and profile.startswith(_SKID_PROFILE_PREFIX) or
            isinstance(contract, str) and contract.startswith(_SKID_CONTRACT_PREFIX)):
        return SKID
    if domain == _I2C_V2_DOMAIN or profile == _I2C_V2_PROFILE or contract == _I2C_V2_CONTRACT:
        return I2C_V2
    return None


def retired_generation(asset) -> str | None:
    """The retired R5 generation an asset (or bound asset / candidate payload) belongs to, if any."""
    if not isinstance(asset, Mapping):
        return None
    compatibility = asset.get("compatibility")
    definition = asset.get("definition")
    provenance = asset.get("provenance")
    action = definition.get("action") if isinstance(definition, Mapping) else None
    payload = action.get("payload") if isinstance(action, Mapping) else None
    template = definition.get("binding_template") if isinstance(definition, Mapping) else None
    probes = [
        (compatibility.get("action_domain"), compatibility.get("compatibility_profile"), None)
        if isinstance(compatibility, Mapping) else (None, None, None),
        (action.get("domain"), None, None) if isinstance(action, Mapping) else (None, None, None),
        (payload.get("domain"), payload.get("compatibility_profile"), None)
        if isinstance(payload, Mapping) else (None, None, None),
        (None, None, template.get("contract")) if isinstance(template, Mapping) else (None, None, None),
        (None, None, provenance.get("binding_contract")) if isinstance(provenance, Mapping) else (None, None, None),
        (asset.get("domain"), asset.get("compatibility_profile"), asset.get("contract")),
    ]
    for domain, profile, contract in probes:
        generation = _generation_of(domain, profile, contract)
        if generation:
            return generation
    return None


def retired_reason(generation: str) -> str:
    return "retired_generation:%s (replay in its frozen worktree)" % generation


__all__ = ["I2C_V2", "RETIRED_SCOPED_EXECUTION_VERSIONS", "SKID", "retired_generation", "retired_reason"]
