"""Fail-closed checks for the read-only Revision3 StateShift replay."""
from __future__ import annotations

import pytest

from tehm.evaluation.state_shift_replay import StateShiftReplayError, replay


def test_state_shift_replay_requires_an_artifact_directory(tmp_path):
    with pytest.raises(StateShiftReplayError, match="campaign manifest is missing"):
        replay(tmp_path)


def test_state_shift_replay_rejects_a_non_directory(tmp_path):
    path = tmp_path / "not-a-directory"
    path.write_text("x")
    with pytest.raises(StateShiftReplayError, match="not a directory"):
        replay(path)
