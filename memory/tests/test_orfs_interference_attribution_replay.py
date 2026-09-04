"""Fail-closed checks for the ORFS P14 attribution replay."""
from __future__ import annotations

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
