#!/usr/bin/env python3
"""Validate and freeze a future-lineage Parametric campaign manifest.

This command only freezes metadata.  It does not run ORFS/RTL, mutate TEHM, or
turn planned lineages into evidence.  A case must declare whether it belongs to
the observational shadow cohort or the later decision cohort.  Decision targets
must expose at least two distinct legal candidate actions so ranking utility is
identifiable rather than inferred from a single executed action.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from tehm.ids import stable_dumps  # noqa: E402
from tehm.parametric.shadow_campaign import ShadowCampaignError, digest  # noqa: E402
from tehm.physical.utility_contracts import (  # noqa: E402
    TIMING_RELIEF_BUDGETED_V2_50_TO_45_ID,
    TIMING_RELIEF_BUDGETED_V1_ID,
    timing_relief_budgeted_v1,
    timing_relief_budgeted_v2_50_to_45,
    utility_contract_digest,
    validate_utility_contract,
)
from tehm.sync import canonical_json  # noqa: E402


def _read(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowCampaignError(f"cannot read manifest input: {path}") from exc
    if not isinstance(value, dict):
        raise ShadowCampaignError("manifest input must be an object")
    return value


def _lineages(value, key: str) -> set[str]:
    rows = value.get(key) or []
    if not isinstance(rows, list) or any(not isinstance(item, str) or not item for item in rows):
        raise ShadowCampaignError(f"firewall.{key} must be a list of non-empty strings")
    return set(rows)


def validate(manifest: dict) -> dict:
    if manifest.get("version") != "parametric-prospective-manifest-v1":
        raise ShadowCampaignError("unsupported prospective manifest version")
    if manifest.get("status") not in {"PLANNED", "FROZEN", "EXECUTED"}:
        raise ShadowCampaignError("manifest status must be PLANNED, FROZEN, or EXECUTED")
    contract_fields = _validate_contract_binding(manifest)
    freeze = manifest.get("source_freeze")
    if not isinstance(freeze, dict) or not freeze.get("bundle_digest") or not freeze.get("manifest_digest"):
        raise ShadowCampaignError("source_freeze must bind bundle_digest and manifest_digest")
    firewall = manifest.get("firewall")
    if not isinstance(firewall, dict):
        raise ShadowCampaignError("firewall is required")
    training = _lineages(firewall, "training_lineages")
    calibration = _lineages(firewall, "calibration_lineages")
    heldout = _lineages(firewall, "heldout_lineages")
    ab = _lineages(firewall, "ab_lineages")
    protected = training | calibration | heldout | ab
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ShadowCampaignError("prospective manifest needs at least one planned case")
    ids, target_groups, future_lineages = set(), {}, set()
    for case in cases:
        if not isinstance(case, dict):
            raise ShadowCampaignError("each prospective case must be an object")
        for key in ("case_id", "target_id", "lineage_id", "platform", "family", "phase"):
            if not isinstance(case.get(key), str) or not case[key]:
                raise ShadowCampaignError(f"case missing non-empty {key}")
        if case["case_id"] in ids:
            raise ShadowCampaignError(f"duplicate case_id: {case['case_id']}")
        ids.add(case["case_id"])
        if case["phase"] not in {"observation", "decision"}:
            raise ShadowCampaignError(f"unsupported case phase: {case['phase']}")
        if case["lineage_id"] in protected:
            raise ShadowCampaignError(
                f"future lineage overlaps protected firewall: {case['lineage_id']}")
        future_lineages.add(case["lineage_id"])
        context_digest = case.get("graph_context_digest")
        if not isinstance(context_digest, str) or not context_digest:
            raise ShadowCampaignError(f"case missing graph_context_digest: {case['case_id']}")
        actions = case.get("candidate_actions")
        if not isinstance(actions, list) or not actions:
            raise ShadowCampaignError(f"case needs candidate_actions: {case['case_id']}")
        action_digests = {digest(action) for action in actions if isinstance(action, dict)}
        if len(action_digests) != len(actions):
            raise ShadowCampaignError(f"candidate_actions must be non-empty objects: {case['case_id']}")
        if case["phase"] == "decision" and len(action_digests) < 2:
            raise ShadowCampaignError(
                f"decision case needs >=2 distinct candidate actions: {case['case_id']}")
        target_groups.setdefault((case["phase"], case["target_id"]), []).append(case)
    if len(future_lineages) < 2:
        raise ShadowCampaignError("prospective cohort must contain at least two independent future lineages")
    metrics = manifest.get("pre_registered_metrics")
    if not isinstance(metrics, dict):
        raise ShadowCampaignError("pre_registered_metrics is required")
    required = ("hard_ood_ceiling", "min_interval_coverage", "max_harmful_rate",
                "min_obligation_coverage")
    for key in required:
        if key not in metrics:
            raise ShadowCampaignError(f"missing pre-registered metric threshold: {key}")
    if float(metrics["hard_ood_ceiling"]) > 3.0:
        raise ShadowCampaignError("hard OOD ceiling cannot be widened above 3.0")
    if not 0.0 <= float(metrics["min_interval_coverage"]) <= 1.0:
        raise ShadowCampaignError("min_interval_coverage must be in [0,1]")
    if not 0.0 <= float(metrics["max_harmful_rate"]) <= 1.0:
        raise ShadowCampaignError("max_harmful_rate must be in [0,1]")
    if not 0.0 <= float(metrics["min_obligation_coverage"]) <= 1.0:
        raise ShadowCampaignError("min_obligation_coverage must be in [0,1]")
    decision_cases = any(case["phase"] == "decision" for case in cases)
    decision_gate = manifest.get("decision_gate")
    if decision_cases:
        if not isinstance(decision_gate, dict):
            raise ShadowCampaignError(
                "decision cases require a pre-registered decision_gate")
        gate_required = (
            "min_observation_proposal_coverage",
            "min_observation_outcome_coverage",
            "min_observation_obligation_coverage",
            "required_physical_metrics",
            "min_metric_evaluations",
        )
        for key in gate_required:
            if key not in decision_gate:
                raise ShadowCampaignError(f"decision_gate missing: {key}")
        for key in gate_required[:3]:
            if not 0.0 <= float(decision_gate[key]) <= 1.0:
                raise ShadowCampaignError(f"decision_gate.{key} must be in [0,1]")
        required_metrics = decision_gate["required_physical_metrics"]
        if (not isinstance(required_metrics, list) or not required_metrics or
                any(not isinstance(item, str) or not item for item in required_metrics)):
            raise ShadowCampaignError(
                "decision_gate.required_physical_metrics must be a non-empty list")
        if (isinstance(decision_gate["min_metric_evaluations"], bool) or
                not isinstance(decision_gate["min_metric_evaluations"], int) or
                decision_gate["min_metric_evaluations"] < 1):
            raise ShadowCampaignError(
                "decision_gate.min_metric_evaluations must be a positive integer")
        normalized_gate = {
            "min_observation_proposal_coverage": float(
                decision_gate["min_observation_proposal_coverage"]),
            "min_observation_outcome_coverage": float(
                decision_gate["min_observation_outcome_coverage"]),
            "min_observation_obligation_coverage": float(
                decision_gate["min_observation_obligation_coverage"]),
            "required_physical_metrics": sorted(set(required_metrics)),
            "min_metric_evaluations": int(decision_gate["min_metric_evaluations"]),
        }
    else:
        normalized_gate = None
    return {
        "version": "parametric-prospective-manifest-v1",
        "status": manifest["status"],
        **contract_fields,
        "source_freeze": dict(freeze),
        "firewall": {
            "training_lineages": sorted(training),
            "calibration_lineages": sorted(calibration),
            "heldout_lineages": sorted(heldout),
            "ab_lineages": sorted(ab),
            "future_lineages": sorted(future_lineages),
            "disjoint": not bool(future_lineages & protected),
        },
        "cases": list(cases),
        "pre_registered_metrics": dict(metrics),
        "decision_gate": normalized_gate,
        "target_groups": {
            f"{phase}:{target}": len(rows)
            for (phase, target), rows in sorted(target_groups.items())
        },
        "validation": {
            "case_count": len(cases),
            "future_lineage_count": len(future_lineages),
            "decision_targets_with_multiple_actions": sorted(
                f"{phase}:{target}" for (phase, target), rows in target_groups.items()
                if phase == "decision" and len({digest(action) for row in rows for action in row["candidate_actions"]}) >= 2),
            "no_canonical_memory_mutation": True,
        },
    }


def _validate_contract_binding(manifest: dict) -> dict:
    """Validate optional typed-contract fields without breaking legacy manifests."""
    present = any(key in manifest for key in
                  ("contract_id", "utility_contract_digest", "action_signature"))
    if not present:
        return {}
    contract_id = manifest.get("contract_id")
    digest_value = manifest.get("utility_contract_digest")
    signature = manifest.get("action_signature")
    if not isinstance(contract_id, str) or not contract_id:
        raise ShadowCampaignError("typed prospective manifest requires contract_id")
    if not isinstance(digest_value, str) or len(digest_value) != 64:
        raise ShadowCampaignError("typed prospective manifest requires a 64-char utility_contract_digest")
    if not isinstance(signature, dict):
        raise ShadowCampaignError("typed prospective manifest requires action_signature")
    known_contracts = {
        TIMING_RELIEF_BUDGETED_V1_ID: timing_relief_budgeted_v1,
        TIMING_RELIEF_BUDGETED_V2_50_TO_45_ID: timing_relief_budgeted_v2_50_to_45,
    }
    contract_factory = known_contracts.get(contract_id)
    if contract_factory is not None:
        contract = contract_factory()
        validate_utility_contract(contract)
        if digest_value != utility_contract_digest(contract):
            raise ShadowCampaignError("utility contract digest does not match frozen contract")
        expected = contract["action_signature"]
        actual = {
            "domain": signature.get("domain"),
            "transformation_family": signature.get("family"),
            "config_edits": signature.get("config_edits"),
            "operation_point": signature.get("operation_point"),
        }
        if actual != expected:
            raise ShadowCampaignError("prospective action_signature does not match utility contract")
    return {
        "contract_id": contract_id,
        "utility_contract_digest": digest_value,
        "action_signature": dict(signature),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        result = validate(_read(args.input))
    except ShadowCampaignError as exc:
        print(f"prospective manifest refused: {exc}", file=sys.stderr)
        return 2
    result["manifest_digest"] = digest(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json(result))
    print(json.dumps({"ok": True, "output": str(args.output),
                      "manifest_digest": result["manifest_digest"],
                      "future_lineages": result["firewall"]["future_lineages"]},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
