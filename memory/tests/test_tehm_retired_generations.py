"""Retired R5 generations fail closed in the main tree (cleanup 2026-10-02, tehm.assets.retired)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

SKID_V5 = {"asset_id": "a1", "definition": {"action": {"domain": "rtl.SKID_TEMP_PAYLOAD_RESTORE_SHADOW_V5"}}}
SKID_V8 = {"asset_id": "a2", "compatibility": {"compatibility_profile": "rtl.skid.payload.v8.dev"}}
I2C_V2 = {"asset_id": "a3", "definition": {"binding_template": {
    "contract": "rtl_i2c_nack_status_latch_source_binding_shadow_v2"}}}
I2C_V3 = {"asset_id": "a4", "compatibility": {"action_domain": "rtl.I2C_NACK_STATUS_LATCH_V3",
                                              "compatibility_profile": "rtl.i2c.nack_status_latch.v3.dev"}}


def test_retired_generation_detection():
    from tehm.assets.retired import I2C_V2 as GEN_I2C_V2, SKID, retired_generation
    assert retired_generation(SKID_V5) == SKID and retired_generation(SKID_V8) == SKID
    assert retired_generation(I2C_V2) == GEN_I2C_V2
    assert retired_generation(I2C_V3) is None                 # the current generation is not retired
    assert retired_generation({"domain": "rtl.GUARD_STRENGTHEN"}) is None
    assert retired_generation("not a mapping") is None


@pytest.mark.parametrize("asset", [SKID_V5, SKID_V8, I2C_V2])
def test_lifecycle_never_makes_a_retired_asset_eligible(asset):
    from tehm.assets.lifecycle import ASSET_PROMOTION_GATES, evaluate_asset_authority, evaluate_asset_promotion_gates
    every_gate = {name: True for name in ASSET_PROMOTION_GATES}
    gates = evaluate_asset_promotion_gates(asset, every_gate, target_scope="x")
    assert not gates.eligible and gates.evidence["reason"].startswith("retired_generation:")
    derived = evaluate_asset_authority(asset, validation_receipts=[], bindings=[],
                                       rollback_receipt={"verified": True}, target_scope="x")
    assert not derived.eligible and derived.evidence["reason"].startswith("retired_generation:")


def test_proofless_source_path_refuses_retired_and_source_bound_domains():
    from tehm.assets.source_selection import verify_candidate_source_replay
    def candidate(domain):
        return SimpleNamespace(provenance={}, concrete_action={"domain": domain})
    assert not verify_candidate_source_replay(candidate("rtl.SKID_TEMP_PAYLOAD_RESTORE_SHADOW_V3"), "")
    assert not verify_candidate_source_replay(candidate("rtl.I2C_NACK_STATUS_LATCH_V2"), "")
    assert not verify_candidate_source_replay(candidate("rtl.I2C_NACK_STATUS_LATCH_V3"), "")
    assert verify_candidate_source_replay(candidate("rtl.AST_REWRITE"), "")    # legacy proof-less path unchanged
