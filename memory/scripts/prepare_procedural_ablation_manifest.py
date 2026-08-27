#!/usr/bin/env python3
"""Validate and freeze the next procedural-memory ablation task manifest.

The manifest is a planning/evaluation boundary, not evidence.  A task whose
fixture and oracle receipts are not materialized remains explicitly pending;
the command never captures a transition or mutates a TEHM database.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from tehm.parametric.shadow_campaign import ShadowCampaignError, digest  # noqa: E402
from tehm.sync import canonical_json  # noqa: E402


COMPONENTS = {
    "role_view": "M5",
    "predicate_view": "M6",
    "validity_gate": "M4",
    "obligation_transfer": "M7",
}
TASK_FAMILIES = {
    "structural_role_collision",
    "unknown_predicate_and_mechanism_confusion",
    "degenerate_or_unstable_rule",
    "obligation_preservation_and_recovery_branch",
    "reset_semantic_loss",
    "positive_role_predicate_validity_binding",
}
MECHANISM_COMPONENT = "full_mechanism_path"


def _read(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowCampaignError(f"cannot read procedural manifest: {path}") from exc
    if not isinstance(value, dict):
        raise ShadowCampaignError("procedural manifest must be an object")
    return value


def _strings(value, label: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or any(
            not isinstance(item, str) or (nonempty and not item)
            for item in value):
        raise ShadowCampaignError(f"{label} must be a list of strings")
    return list(value)


def validate(manifest: dict, *, repo_root: Path | None = None) -> dict:
    version = manifest.get("version")
    if version not in {"procedural-ablation-task-manifest-v1",
                       "procedural-growth-ablation-task-manifest-v2",
                       "procedural-mechanism-ablation-task-manifest-v2"}:
        raise ShadowCampaignError("unsupported procedural ablation manifest version")
    growth_manifest = version == "procedural-growth-ablation-task-manifest-v2"
    mechanism_manifest = version == "procedural-mechanism-ablation-task-manifest-v2"
    status = manifest.get("status")
    if status not in {"PLANNED", "FROZEN", "EXECUTED"}:
        raise ShadowCampaignError("status must be PLANNED, FROZEN, or EXECUTED")
    source = manifest.get("source_snapshot")
    if (not isinstance(source, dict) or not source.get("canonical_bundle") or
            not source.get("bundle_digest")):
        raise ShadowCampaignError("source_snapshot must bind canonical_bundle and bundle_digest")
    firewall = manifest.get("firewall")
    if not isinstance(firewall, dict):
        raise ShadowCampaignError("firewall is required")
    training = set(_strings(firewall.get("training_lineages"),
                            "firewall.training_lineages"))
    protected = training | set(_strings(firewall.get("heldout_lineages"),
                                        "firewall.heldout_lineages"))
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ShadowCampaignError("tasks must be a non-empty list")
    ids, components, lineages = set(), set(), set()
    normalized_tasks = []
    for task in tasks:
        if not isinstance(task, dict):
            raise ShadowCampaignError("each task must be an object")
        for key in ("task_id", "component", "ablated_arm", "task_family",
                    "lineage_id", "lineage_cluster", "required_signal"):
            if not isinstance(task.get(key), str) or not task[key]:
                raise ShadowCampaignError(f"task missing non-empty {key}")
        task_id = task["task_id"]
        if task_id in ids:
            raise ShadowCampaignError(f"duplicate task_id: {task_id}")
        ids.add(task_id)
        component = task["component"]
        if component not in COMPONENTS and not (mechanism_manifest and
                                                component == MECHANISM_COMPONENT):
            raise ShadowCampaignError(f"unsupported component: {component}")
        expected_arm = COMPONENTS.get(component, "M7")
        if task["ablated_arm"] != expected_arm:
            raise ShadowCampaignError(
                f"ablated arm does not match component {component}: {task_id}")
        # The four named component tasks are unique primary contrasts.  A
        # mechanism-family manifest may add multiple positive-path tasks to
        # measure executable coverage; those are intentionally repeatable.
        if component in components and component != MECHANISM_COMPONENT:
            raise ShadowCampaignError(f"component needs one primary task: {component}")
        components.add(component)
        if task["task_family"] not in TASK_FAMILIES:
            raise ShadowCampaignError(f"unsupported task family: {task['task_family']}")
        lineage = task["lineage_id"]
        if lineage in protected:
            raise ShadowCampaignError(f"task lineage overlaps protected firewall: {lineage}")
        lineages.add(lineage)
        oracle_requirements = _strings(task.get("oracle_requirements"),
                                       f"oracle_requirements for {task_id}")
        source_status = task.get("source_status", "PENDING_MATERIALIZATION")
        if source_status not in {"PENDING_MATERIALIZATION", "READY"}:
            raise ShadowCampaignError(f"unsupported source_status for {task_id}")
        fixture = task.get("fixture")
        if source_status == "READY":
            if not isinstance(fixture, str) or not fixture:
                raise ShadowCampaignError(f"ready task needs fixture: {task_id}")
            if repo_root is not None:
                root = Path(repo_root).resolve()
                candidate = (root / fixture).resolve()
                if root not in candidate.parents and candidate != root:
                    raise ShadowCampaignError(f"fixture escapes repo root: {task_id}")
                if not candidate.exists():
                    raise ShadowCampaignError(f"fixture does not exist: {candidate}")
        elif fixture is not None and not isinstance(fixture, str):
            raise ShadowCampaignError(f"pending fixture must be null or a path: {task_id}")
        normalized_tasks.append({
            "task_id": task_id,
            "component": component,
            "ablated_arm": task["ablated_arm"],
            "task_family": task["task_family"],
            "transformation_family": task.get("transformation_family"),
            "lineage_id": lineage,
            "lineage_cluster": task["lineage_cluster"],
            "required_signal": task["required_signal"],
            "oracle_requirements": oracle_requirements,
            "source_status": source_status,
            "fixture": fixture,
        })
    missing = sorted(set(COMPONENTS) - components)
    if missing and not growth_manifest:
        raise ShadowCampaignError(f"missing component tasks: {', '.join(missing)}")
    if len(lineages) < 2:
        raise ShadowCampaignError("procedural cohort needs at least two future lineages")

    acceptance = manifest.get("acceptance")
    if not isinstance(acceptance, dict):
        raise ShadowCampaignError("acceptance is required")
    required_acceptance = (
        "min_non_singleton_effect_groups", "min_cross_lineage_rule_support",
        "min_validated_rules", "min_rule_coverage", "min_vcg",
        "max_harmful_activation_rate", "require_cluster_intervals",
    )
    for key in required_acceptance:
        if key not in acceptance:
            raise ShadowCampaignError(f"acceptance missing: {key}")
    for key in ("min_rule_coverage", "min_vcg", "max_harmful_activation_rate"):
        if not 0.0 <= float(acceptance[key]) <= 1.0:
            raise ShadowCampaignError(f"acceptance.{key} must be in [0,1]")
    for key in ("min_non_singleton_effect_groups", "min_cross_lineage_rule_support",
                "min_validated_rules"):
        if isinstance(acceptance[key], bool) or not isinstance(acceptance[key], int) or acceptance[key] < 1:
            raise ShadowCampaignError(f"acceptance.{key} must be a positive integer")
    if not isinstance(acceptance["require_cluster_intervals"], bool):
        raise ShadowCampaignError("acceptance.require_cluster_intervals must be boolean")
    task_sources = manifest.get("task_sources") or {}
    if not isinstance(task_sources, dict):
        raise ShadowCampaignError("task_sources must be an object")
    result = {
        "version": version,
        "status": status,
        "source_snapshot": dict(source),
        "firewall": {
            "training_lineages": sorted(training),
            "heldout_lineages": sorted(set(firewall.get("heldout_lineages") or [])),
            "future_lineages": sorted(lineages),
            "disjoint": not bool(lineages & protected),
        },
        "tasks": normalized_tasks,
        "acceptance": dict(acceptance),
        "task_sources": dict(task_sources),
        "validation": {
            "task_count": len(normalized_tasks),
            "component_count": len(components),
            "future_lineage_count": len(lineages),
            "no_canonical_memory_mutation": True,
            # A READY fixture is only a materialized input.  It is not an
            # oracle receipt or an executed result; those claims require a
            # later manifest status of EXECUTED plus a replay report.
            "fixtures_materialized": all(
                task["source_status"] == "READY" for task in normalized_tasks),
            "executed_evidence": bool(
                status == "EXECUTED" and
                str(task_sources.get("status", "")) == "EXECUTED"),
        },
    }
    if growth_manifest and isinstance(manifest.get("rule_selection"), dict):
        result["rule_selection"] = dict(manifest["rule_selection"])
        result["validation"]["growth_manifest"] = True
    if mechanism_manifest:
        result["validation"]["mechanism_manifest"] = True
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--repo-root", type=Path, default=None)
    args = ap.parse_args(argv)
    try:
        result = validate(_read(args.input), repo_root=args.repo_root)
    except ShadowCampaignError as exc:
        print(f"procedural manifest refused: {exc}", file=sys.stderr)
        return 2
    result["manifest_digest"] = digest(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json(result))
    print(json.dumps({"ok": True, "output": str(args.output),
                      "manifest_digest": result["manifest_digest"],
                      "task_count": result["validation"]["task_count"]},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
