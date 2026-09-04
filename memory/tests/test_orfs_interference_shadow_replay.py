"""Fail-closed checks for the ORFS interference shadow replay."""
from __future__ import annotations

import pytest

from tehm.evaluation.orfs_interference_shadow_replay import (
    OrfsInterferenceShadowReplayError, replay,
)


def test_orfs_shadow_replay_requires_a_manifest(tmp_path):
    with pytest.raises(OrfsInterferenceShadowReplayError, match="campaign manifest is missing"):
        replay(tmp_path)


def test_orfs_shadow_replay_rejects_an_incomplete_campaign(tmp_path):
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "campaign_manifest.json").write_text(
        '{"version":"tehm-r3-orfs-interference-shadow-v0.1",'
        '"campaign_id":"shadow","lane":"EVOLUTION_CHALLENGE",'
        '"reason":"MEMORY_INTERFERENCE","backend":"external_orfs",'
        '"evaluation_only":true,"canonical_memory_mutation":"none",'
        '"production_runtime_imported":false,"memory_docs_submitted":false,'
        '"production_integration":"not_attempted","post_cases":[],'
        '"post_routes":{},"post_candidate_payloads":{}}\n')
    with pytest.raises(OrfsInterferenceShadowReplayError, match="requires at least|post inputs|neither cohort"):
        replay(tmp_path)
