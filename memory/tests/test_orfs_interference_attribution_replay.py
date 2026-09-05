"""Fail-closed checks for the ORFS P14 attribution replay."""
from __future__ import annotations

import json
import pytest

from tehm.evaluation.orfs_interference_attribution_replay import (
    OrfsInterferenceAttributionReplayError, replay,
)


def test_orfs_p14_replay_requires_a_report(tmp_path):
    with pytest.raises(OrfsInterferenceAttributionReplayError,
                       match="P14 strategy attribution is missing"):
        replay(tmp_path, shadow_artifacts=tmp_path / "shadow",
               challenge_artifacts=tmp_path / "challenge")


def test_orfs_p14_replay_rejects_non_directory(tmp_path):
    path = tmp_path / "not-a-directory"
    path.write_text("x")
    with pytest.raises(OrfsInterferenceAttributionReplayError,
                       match="not a directory"):
        replay(path, shadow_artifacts=tmp_path / "shadow",
               challenge_artifacts=tmp_path / "challenge")


def test_historical_success_without_router_evidence_is_not_accepted(tmp_path):
    boundary = {"evaluation_only": True, "canonical_memory_mutation": "none",
                "memory_docs_submitted": False, "production_authority_changed": False,
                "production_runtime_imported": False, "strategy_attribution_eligible": True}
    for name, payload in (("p14_strategy_attribution.json", boundary),
                          ("summary.json", boundary),
                          ("p14_capability_attribution.json", {})):
        (tmp_path / name).write_text(json.dumps(payload))
    with pytest.raises(OrfsInterferenceAttributionReplayError,
                       match="actual-router replay is missing"):
        replay(tmp_path, shadow_artifacts=tmp_path / "shadow",
               challenge_artifacts=tmp_path / "challenge")
