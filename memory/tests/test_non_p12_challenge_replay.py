"""Fail-closed boundaries for R3 non-P12 challenge replay."""
from __future__ import annotations

import hashlib
import json

import pytest

from tehm.evaluation.non_p12_challenge import (
    NonP12ChallengeReplayError, replay_capability_gap_challenge,
)


def _report(tmp_path, *, source=b"source", derived=b"derived", **overrides):
    source_path = tmp_path / "source.sqlite"
    derived_path = tmp_path / "derived.sqlite"
    source_path.write_bytes(source)
    derived_path.write_bytes(derived)
    report = {
        "version": "r3-capability-gap-challenge-v1",
        "campaign_id": "replay-boundary-test",
        "canonical_memory_mutation": "none",
        "production_runtime": {
            "promotion_attempted": False,
            "production_promotion_eligible": False,
            "runtime_authority_changed": False,
        },
        "real_oracle": "icarus/vvp",
        "source_db": str(source_path),
        "source_db_sha256": hashlib.sha256(source).hexdigest(),
        "derived_db": str(derived_path),
        "derived_db_sha256": hashlib.sha256(derived).hexdigest(),
    }
    report.update(overrides)
    path = tmp_path / "challenge.json"
    path.write_text(json.dumps(report))
    return path


def test_non_p12_replay_rejects_production_boundary(tmp_path):
    report = _report(
        tmp_path,
        canonical_memory_mutation="write",
    )
    with pytest.raises(NonP12ChallengeReplayError, match="canonical-memory"):
        replay_capability_gap_challenge(report)


def test_non_p12_replay_rejects_unfrozen_database_sidecar(tmp_path):
    report = _report(tmp_path)
    (tmp_path / "source.sqlite-wal").write_bytes(b"journal")
    with pytest.raises(NonP12ChallengeReplayError, match="frozen snapshot"):
        replay_capability_gap_challenge(report)
