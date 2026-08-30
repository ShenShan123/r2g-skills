"""Contract binding at the calibrated future-case generation boundary."""
from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from prepare_calibrated_shadow_cases import build  # noqa: E402
from tehm.physical.utility_contracts import (  # noqa: E402
    contract_action,
    density_relief_nonregression_32,
    utility_contract_digest,
)


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value))
    return path


def test_contract_policy_generates_strict_observation_only_manifest(tmp_path):
    contract = density_relief_nonregression_32()
    action = contract_action(contract)
    samples = []
    for suffix in ("x", "y"):
        samples.append({
            "case_id": f"future-prospective-{suffix}:sky130hs:logic:base0",
            "lineage_id": f"future-prospective-{suffix}:sky130hs:logic:base0",
            "graph_context": {"digest": f"ctx-{suffix}"},
            "action": {"domain": action["domain"],
                       "transformation_family": action["transformation_family"],
                       "payload": {"config_edits": {"CORE_UTILIZATION": "32"}}},
            "before_ppa": {}, "after_ppa": {},
        })
    samples_path = _write(tmp_path / "samples.json", {"samples": samples})
    policy = {
        "status": "ready",
        "scope": {"platform": "sky130hs", "family": "DENSITY_RELIEF",
                  "dataset_tier": "strict_clean"},
        "utility_contract_id": contract["contract_id"],
        "utility_contract_digest": utility_contract_digest(contract),
    }
    policy_path = _write(tmp_path / "policy.json", {"policy": policy})
    campaign_path = _write(tmp_path / "campaign.json", {"items": []})
    training_path = _write(tmp_path / "training.json", {
        "training_lineages": ["train:a"],
        "firewall": {"heldout_lineages": ["heldout:a"]},
    })
    source_path = _write(tmp_path / "source.json", {
        "bundle_digest": "bundle", "manifest_digest": "manifest",
    })
    out = tmp_path / "out"
    result = build(Namespace(
        samples=samples_path, policy_report=policy_path,
        campaign_manifest=campaign_path, training_manifest=training_path,
        source_freeze=source_path, out_dir=out, future_suffix=["x", "y"],
    ))
    assert result["observation_cases"] == 2
    assert result["decision_cases"] == 0
    manifest = json.loads((out / "manifest.normalized.json").read_text())
    assert manifest["require_utility_contract"] is True
    assert manifest["contract_id"] == contract["contract_id"]
    rows = [json.loads(line) for line in (out / "cases.jsonl").read_text().splitlines()
            if line.strip()]
    assert len(rows) == 2
    assert all(row["action"]["payload"]["utility_contract_id"] ==
               contract["contract_id"] for row in rows)
    assert (out / "decision_cases.jsonl").read_text() == "\n"
