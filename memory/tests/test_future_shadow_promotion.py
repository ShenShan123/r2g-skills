"""Durable future-shadow promotion keeps the verifier receipt auditable."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from promote_future_shadow_evidence import promote  # noqa: E402


def test_promote_future_shadow_copies_replay_evidence(tmp_path):
    source = tmp_path / "scratch"
    evidence = tmp_path / "evidence"
    source.mkdir()
    receipt = {"ok": True, "roundtrip_byte_stable": True,
               "bundle_digest": "bundle", "manifest_digest": "manifest"}
    (source / "replay_evidence.json").write_text(json.dumps(receipt))

    result = promote(source, evidence)

    assert (evidence / "replay_evidence.json").is_file()
    assert json.loads((evidence / "replay_evidence.json").read_text()) == receipt
    assert result["orfs_run_trees_promoted"] is False
    assert result["canonical_memory_mutation"] == "none"
