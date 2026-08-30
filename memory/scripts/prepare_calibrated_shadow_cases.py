#!/usr/bin/env python3
"""Freeze observation/decision case metadata for a calibrated shadow cohort.

This command only writes compact JSON/JSONL metadata under the caller's
scratch directory.  It never captures the samples into TEHM and refuses to
reuse calibration held-out lineages as future cases.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MEMORY_ROOT.parent
sys.path.insert(0, str(MEMORY_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_orfs_diversity_campaign import _load  # noqa: E402
from tehm.adapters.orfs_pair import build_orfs_pair_record  # noqa: E402
from tehm.sync import canonical_json  # noqa: E402


def _read(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def build(args) -> dict:
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sample_doc = _read(args.samples)
    samples = sample_doc.get("samples") or []
    selected = [sample for sample in samples
                if any(str(sample.get("lineage_id", "")).startswith(
                    f"future-prospective-{suffix}:") for suffix in args.future_suffix)]
    if len(selected) != len(args.future_suffix):
        raise ValueError("each requested future suffix must have one evaluatable sample")
    policy_report = _read(args.policy_report)
    # A calibration expansion report may contain both the diagnostic
    # family-wide policy (which can legitimately be coverage_failed) and a
    # separately materialized lineage-grouped shadow policy.  Shadow cases
    # must bind the latter when it exists; silently falling back to the
    # diagnostic policy would either abstain for the wrong reason or reuse a
    # non-action-specific interval.
    materialized = policy_report.get("shadow_policy_materialization") or {}
    policy = materialized.get("policy") or policy_report.get("policy") or {}
    snapshot_digest = policy_report.get("staging_snapshot_digest")
    memory_binding = ({"memory_snapshot_digest": str(snapshot_digest)}
                      if isinstance(snapshot_digest, str) and snapshot_digest else {})
    heldout = set((policy.get("firewall") or {}).get("heldout_lineages") or [])
    selected_lineages = {str(sample["lineage_id"]) for sample in selected}
    if heldout & selected_lineages:
        raise ValueError("future cohort overlaps calibration held-out lineage")
    source = _read(args.source_freeze)
    training = (_read(args.training_manifest).get("training_lineages") or
                ((_read(args.training_manifest).get("firewall") or {}).get(
                    "training_lineages") or []))
    old_heldout = ((_read(args.training_manifest).get("firewall") or {}).get(
        "heldout_lineages") or [])
    policy_scope = dict(policy.get("scope") or {})
    if not policy_scope:
        policy_key = policy.get("policy_key")
        if isinstance(policy_key, str):
            parts = policy_key.split("|")
            if len(parts) == 3:
                policy_scope = {"platform": parts[0], "family": parts[1],
                                "dataset_tier": parts[2]}
    if set(("platform", "family", "dataset_tier")) - set(policy_scope):
        raise ValueError("calibration policy must provide an explicit platform/family/dataset_tier scope")
    item_by_lineage = {
        item["lineage_id"]: item
        for item in (_read(args.campaign_manifest).get("items") or [])
    }
    rows, outcomes = [], []
    for sample in sorted(selected, key=lambda x: x["lineage_id"]):
        lineage = sample["lineage_id"]
        context = sample["graph_context"]
        action = sample["action"]
        observation_id = f"{lineage}:observation"
        rows.append({
            "case_id": observation_id, "target_id": f"{lineage}:target",
            "lineage_id": lineage, "platform": "sky130hs",
            "family": "DENSITY_RELIEF", "phase": "observation",
            "source_lineage": lineage,
            "graph_context_digest": context["digest"],
            "candidate_actions": [action], "policy_scope": policy_scope,
            "graph_context": context, "calibration_policy": policy,
            **memory_binding,
            "action": action,
        })
        # Pre-register two legal alternatives for a later decision round, but
        # do not execute them unless the observation gate passes.
        candidates = []
        for util in ("20", "25"):
            candidate = json.loads(json.dumps(action))
            candidate.setdefault("payload", {}).setdefault("config_edits", {})[
                "CORE_UTILIZATION"] = util
            candidates.append(candidate)
        rows.append({
            "case_id": f"{lineage}:decision", "target_id": f"{lineage}:target",
            "lineage_id": lineage, "platform": "sky130hs",
            "family": "DENSITY_RELIEF", "phase": "decision",
            "source_lineage": lineage,
            "graph_context_digest": context["digest"],
            "candidate_actions": candidates, "policy_scope": policy_scope,
            "graph_context": context, "calibration_policy": policy,
            **memory_binding,
        })
        item = item_by_lineage.get(lineage)
        verification = {}
        if item:
            record = build_orfs_pair_record(
                Path(item["before_project"]), Path(item["after_project"]),
                lineage_id=lineage, target_check="route",
                config_edits=item["config_edits"],
                transformation_family="DENSITY_RELIEF")
            verification = record.verification
        outcomes.append({
            "case_id": observation_id,
            "before_ppa": sample["before_ppa"], "after_ppa": sample["after_ppa"],
            "oracle": {
                "oracle_type": "ORFS_ROUTE_PPA",
                "verdict": verification.get("verdict"),
                "obligation_coverage": verification.get("obligation_coverage"),
                "verification": verification,
            },
        })
    raw = {
        "version": "parametric-prospective-manifest-v1", "status": "PLANNED",
        "source_freeze": {"bundle_digest": source["bundle_digest"],
                          "manifest_digest": source["manifest_digest"]},
        "firewall": {
            "training_lineages": sorted(set(training)),
            "calibration_lineages": sorted(heldout),
            "heldout_lineages": sorted(old_heldout), "ab_lineages": [],
        },
        "cases": rows,
        "pre_registered_metrics": {
            "hard_ood_ceiling": 3.0, "min_interval_coverage": 0.8,
            "max_harmful_rate": 0.1, "min_obligation_coverage": 0.95,
        },
        "decision_gate": {
            "min_observation_proposal_coverage": 1.0,
            "min_observation_outcome_coverage": 1.0,
            "min_observation_obligation_coverage": 1.0,
            "required_physical_metrics": ["area_um2", "power_w", "tns_ns", "wns_ns"],
            "min_metric_evaluations": len(selected),
        },
    }
    raw_path = out / "prospective_manifest.raw.json"
    _write(raw_path, raw)
    normalized = out / "manifest.normalized.json"
    checker = MEMORY_ROOT / "scripts" / "prepare_parametric_prospective_manifest.py"
    proc = subprocess.run([sys.executable, str(checker), "--input", str(raw_path),
                           "--output", str(normalized)], capture_output=True, text=True)
    (out / "manifest_validation.log").write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout + proc.stderr)
    observation = [row for row in rows if row["phase"] == "observation"]
    decision = [row for row in rows if row["phase"] == "decision"]
    (out / "cases.jsonl").write_bytes(b"\n".join(canonical_json(x) for x in observation) + b"\n")
    (out / "decision_cases.jsonl").write_bytes(b"\n".join(canonical_json(x) for x in decision) + b"\n")
    _write(out / "observation_outcomes.json", {"version": "future-shadow-v1",
                                                "outcomes": outcomes})
    (out / "observation_outcomes.jsonl").write_bytes(
        b"\n".join(canonical_json(x) for x in outcomes) + b"\n")
    _write(out / "policy.json", policy)
    _write(out / "case_binding.json", {
        "version": "future-shadow-case-binding-v1",
        "future_lineages": sorted(selected_lineages),
        "calibration_heldout_lineages": sorted(heldout),
        "policy_status": policy.get("status"),
        "policy_kind": policy.get("policy_kind"),
        "policy_scope": policy_scope,
        "memory_snapshot_digest": snapshot_digest,
        "promotion_eligible": False,
        "canonical_memory_mutation": "none",
    })
    return {"manifest": str(normalized), "future_lineages": sorted(selected_lineages),
            "observation_cases": len(observation), "decision_cases": len(decision),
            "outcomes": str(out / "observation_outcomes.json")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--policy-report", type=Path, required=True)
    ap.add_argument("--campaign-manifest", type=Path, required=True)
    ap.add_argument("--training-manifest", type=Path, required=True)
    ap.add_argument("--source-freeze", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--future-suffix", action="append", required=True)
    args = ap.parse_args(argv)
    print(json.dumps(build(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
