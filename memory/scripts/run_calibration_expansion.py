#!/usr/bin/env python3
"""Expand physical calibration support and evaluate a fresh prospective cohort.

This is an evidence-only campaign.  It keeps all ORFS regeneration under
``/tmp``, copies the canonical v3 SQLite snapshot to a staging database, and
loads prior external observations as action-provenance-bound calibration
support in that staging copy.  The v14-v38 lineages are materialized for
calibration and future prospective evaluation; held-out rows are never
written to TEHM before policy evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MEMORY_ROOT.parent
sys.path.insert(0, str(MEMORY_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_orfs_diversity_campaign import (  # noqa: E402
    _latest_successful_final_def,
    _load,
    _materialize,
    _write,
    run_projects,
)
from tehm import SCHEMA_VERSION, db  # noqa: E402
from tehm.canonical.state import CanonicalState, source_digest  # noqa: E402
from tehm.dataset import assign_transition  # noqa: E402
from evaluation.freeze_pointer import resolve_bundle  # noqa: E402
from tehm.adapters.orfs_pair import build_orfs_pair_record  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.physical.calibration import calibrate_retrieval  # noqa: E402
from tehm.physical.effects import extract_deltas  # noqa: E402
from tehm.physical.graph_context import load_defgraph_context  # noqa: E402
from tehm.physical.memory import PhysicalEffectMemory, _action_signature  # noqa: E402
from tehm.physical.utility_contracts import (  # noqa: E402
    action_contract_binding_reason,
    known_utility_contracts,
    utility_contract_digest,
    validate_utility_contract,
)
from tehm.parametric.calibration import (  # noqa: E402
    calibrate_exact_groups,
    materialize_shadow_policy,
)
from tehm.sync import canonical_json  # noqa: E402
from orfs_storage import enforce_work_root, storage_policy  # noqa: E402


VERSION = "calibration-expansion-v1"
SCRATCH_DEFAULT = Path("/tmp/tehm-p2-calibration-expansion-v14v20")
EVIDENCE_DEFAULT = Path(
    "/data1/zhangdy/tehm-campaigns/tehm-p2-calibration-expansion-v14v20"
)
ORFS_DEFAULT = Path(
    os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")
)
LINEAGES = (
    {"suffix": "v14", "design": "future_prospective_logic_v14", "base": "34"},
    {"suffix": "v15", "design": "future_prospective_logic_v15", "base": "32"},
    {"suffix": "v16", "design": "future_prospective_logic_v16", "base": "32"},
    {"suffix": "v17", "design": "future_prospective_logic_v17", "base": "30"},
    {"suffix": "v18", "design": "future_prospective_logic_v18", "base": "32"},
    {"suffix": "v19", "design": "future_prospective_logic_v19", "base": "32"},
    {"suffix": "v20", "design": "future_prospective_logic_v20", "base": "32"},
    {"suffix": "v21", "design": "future_prospective_logic_v21", "base": "30"},
    {"suffix": "v22", "design": "future_prospective_logic_v22", "base": "32"},
    {"suffix": "v23", "design": "future_prospective_logic_v23", "base": "34"},
    {"suffix": "v24", "design": "future_prospective_logic_v24", "base": "30"},
    {"suffix": "v25", "design": "future_prospective_logic_v25", "base": "32"},
    {"suffix": "v26", "design": "future_prospective_logic_v26", "base": "34"},
    {"suffix": "v27", "design": "future_prospective_logic_v27", "base": "30"},
    {"suffix": "v28", "design": "future_prospective_logic_v28", "base": "32"},
    {"suffix": "v29", "design": "future_prospective_logic_v29", "base": "34"},
    {"suffix": "v30", "design": "future_prospective_logic_v30", "base": "30"},
    {"suffix": "v31", "design": "future_prospective_logic_v31", "base": "32"},
    {"suffix": "v32", "design": "future_prospective_logic_v32", "base": "34"},
    {"suffix": "v33", "design": "future_prospective_logic_v33", "base": "30", "action": "40"},
    {"suffix": "v34", "design": "future_prospective_logic_v34", "base": "32", "action": "40"},
    {"suffix": "v35", "design": "future_prospective_logic_v35", "base": "34", "action": "40"},
    {"suffix": "v36", "design": "future_prospective_logic_v36", "base": "30", "action": "40"},
    {"suffix": "v37", "design": "future_prospective_logic_v37", "base": "32", "action": "40"},
    {"suffix": "v38", "design": "future_prospective_logic_v38", "base": "34", "action": "40"},
    {"suffix": "v39", "design": "future_prospective_logic_v39", "base": "30", "action": "40"},
    {"suffix": "v40", "design": "future_prospective_logic_v40", "base": "32", "action": "40"},
    {"suffix": "v41", "design": "future_prospective_logic_v41", "base": "34", "action": "40"},
    {"suffix": "v42", "design": "future_prospective_logic_v42", "base": "30", "action": "40"},
    {"suffix": "v43", "design": "future_prospective_logic_v43", "base": "32", "action": "40"},
    {"suffix": "v44", "design": "future_prospective_logic_v44", "base": "34", "action": "40"},
    {"suffix": "v45", "design": "future_prospective_logic_v45", "base": "30", "action": "40"},
    {"suffix": "v46", "design": "future_prospective_logic_v46", "base": "32", "action": "40"},
    {"suffix": "v47", "design": "future_prospective_logic_v47", "base": "34", "action": "40"},
    {"suffix": "v48", "design": "future_prospective_logic_v48", "base": "30", "action": "40"},
    {"suffix": "v49", "design": "future_prospective_logic_v49", "base": "32", "action": "40"},
    {"suffix": "v50", "design": "future_prospective_logic_v50", "base": "34", "action": "40"},
    {"suffix": "v51", "design": "future_prospective_logic_v51", "base": "30", "action": "40"},
    {"suffix": "v52", "design": "future_prospective_logic_v52", "base": "32", "action": "40"},
    {"suffix": "v53", "design": "future_prospective_logic_v53", "base": "34", "action": "40"},
    {"suffix": "v54", "design": "future_prospective_logic_v54", "base": "30", "action": "40"},
    {"suffix": "v55", "design": "future_prospective_logic_v55", "base": "32", "action": "40"},
    {"suffix": "v56", "design": "future_prospective_logic_v56", "base": "34", "action": "40"},
    {"suffix": "v57", "design": "future_prospective_logic_v57", "base": "30", "action": "40"},
    {"suffix": "v58", "design": "future_prospective_logic_v58", "base": "32", "action": "40"},
    {"suffix": "v59", "design": "future_prospective_logic_v59", "base": "34", "action": "40"},
    {"suffix": "v60", "design": "future_prospective_logic_v60", "base": "30", "action": "40"},
    {"suffix": "v61", "design": "future_prospective_logic_v61", "base": "32", "action": "40"},
    {"suffix": "v62", "design": "future_prospective_logic_v62", "base": "34", "action": "40"},
    {"suffix": "v63", "design": "future_prospective_logic_v63", "base": "30", "action": "40"},
    {"suffix": "v64", "design": "future_prospective_logic_v64", "base": "32", "action": "40"},
    {"suffix": "v65", "design": "future_prospective_logic_v65", "base": "34", "action": "40"},
    {"suffix": "v66", "design": "future_prospective_logic_v66", "base": "30", "action": "40"},
    {"suffix": "v67", "design": "future_prospective_logic_v67", "base": "32", "action": "40"},
    {"suffix": "v68", "design": "future_prospective_logic_v68", "base": "34", "action": "40"},
    {"suffix": "v69", "design": "future_prospective_logic_v69", "base": "30", "action": "40"},
    {"suffix": "v70", "design": "future_prospective_logic_v70", "base": "32", "action": "40"},
    {"suffix": "v71", "design": "future_prospective_logic_v71", "base": "34", "action": "40"},
    {"suffix": "v72", "design": "future_prospective_logic_v72", "base": "30", "action": "40"},
    {"suffix": "v73", "design": "future_prospective_logic_v73", "base": "32", "action": "40"},
    {"suffix": "v74", "design": "future_prospective_logic_v74", "base": "34", "action": "40"},
    {"suffix": "v75", "design": "future_prospective_logic_v75", "base": "30", "action": "40"},
    {"suffix": "v76", "design": "future_prospective_logic_v76", "base": "32", "action": "40"},
    {"suffix": "v77", "design": "future_prospective_logic_v77", "base": "34", "action": "40"},
    {"suffix": "v78", "design": "future_prospective_logic_v78", "base": "30", "action": "40"},
    {"suffix": "v79", "design": "future_prospective_logic_v79", "base": "32", "action": "40"},
    {"suffix": "v80", "design": "future_prospective_logic_v80", "base": "34", "action": "40"},
    {"suffix": "v81", "design": "future_prospective_logic_v81", "base": "30", "action": "40"},
    {"suffix": "v82", "design": "future_prospective_logic_v82", "base": "32", "action": "40"},
    {"suffix": "v83", "design": "future_prospective_logic_v83", "base": "34", "action": "40"},
    {"suffix": "v84", "design": "future_prospective_logic_v84", "base": "30", "action": "40"},
    {"suffix": "v85", "design": "future_prospective_logic_v85", "base": "32", "action": "40"},
    {"suffix": "v86", "design": "future_prospective_logic_v86", "base": "34", "action": "40"},
    {"suffix": "v87", "design": "future_prospective_logic_v87", "base": "30", "action": "40"},
    {"suffix": "v88", "design": "future_prospective_logic_v88", "base": "32", "action": "40"},
    {"suffix": "v89", "design": "future_prospective_logic_v89", "base": "34", "action": "40"},
    {"suffix": "v90", "design": "future_prospective_logic_v90", "base": "30", "action": "40"},
    {"suffix": "v91", "design": "future_prospective_logic_v91", "base": "32", "action": "40"},
    {"suffix": "v92", "design": "future_prospective_logic_v92", "base": "34", "action": "40"},
    {"suffix": "v93", "design": "future_prospective_logic_v93", "base": "30",
     "action": "36", "screen_split": "support"},
    {"suffix": "v94", "design": "future_prospective_logic_v94", "base": "32",
     "action": "36", "screen_split": "support"},
    {"suffix": "v95", "design": "future_prospective_logic_v95", "base": "34",
     "action": "36", "screen_split": "support"},
    {"suffix": "v96", "design": "future_prospective_logic_v96", "base": "30",
     "action": "36", "screen_split": "heldout"},
    {"suffix": "v97", "design": "future_prospective_logic_v97", "base": "32",
     "action": "36", "screen_split": "heldout"},
    {"suffix": "v98", "design": "future_prospective_logic_v98", "base": "34",
     "action": "36", "screen_split": "heldout"},
    {"suffix": "v99", "design": "future_prospective_logic_v99", "base": "30",
     "action": "38", "screen_split": "support"},
    {"suffix": "v100", "design": "future_prospective_logic_v100", "base": "32",
     "action": "38", "screen_split": "support"},
    {"suffix": "v101", "design": "future_prospective_logic_v101", "base": "34",
     "action": "38", "screen_split": "support"},
    {"suffix": "v102", "design": "future_prospective_logic_v102", "base": "30",
     "action": "38", "screen_split": "heldout"},
    {"suffix": "v103", "design": "future_prospective_logic_v103", "base": "32",
     "action": "38", "screen_split": "heldout"},
    {"suffix": "v104", "design": "future_prospective_logic_v104", "base": "34",
     "action": "38", "screen_split": "heldout"},
    {"suffix": "v105", "design": "future_prospective_logic_v105", "base": "34",
     "action": "36", "screen_split": "support", "replacement_for": "v95"},
    {"suffix": "v106", "design": "future_prospective_logic_v106", "base": "30",
     "action": "38", "screen_split": "support", "replacement_for": "v99"},
    {"suffix": "v107", "design": "future_prospective_logic_v107", "base": "28",
     "action": "34", "screen_split": "support"},
    {"suffix": "v108", "design": "future_prospective_logic_v108", "base": "30",
     "action": "34", "screen_split": "support"},
    {"suffix": "v109", "design": "future_prospective_logic_v109", "base": "32",
     "action": "34", "screen_split": "support"},
    {"suffix": "v110", "design": "future_prospective_logic_v110", "base": "28",
     "action": "34", "screen_split": "heldout"},
    {"suffix": "v111", "design": "future_prospective_logic_v111", "base": "30",
     "action": "34", "screen_split": "heldout"},
    {"suffix": "v112", "design": "future_prospective_logic_v112", "base": "32",
     "action": "34", "screen_split": "heldout"},
    {"suffix": "v113", "design": "future_prospective_logic_v113", "base": "24",
     "action": "32", "screen_split": "support"},
    {"suffix": "v114", "design": "future_prospective_logic_v114", "base": "26",
     "action": "32", "screen_split": "support"},
    {"suffix": "v115", "design": "future_prospective_logic_v115", "base": "28",
     "action": "32", "screen_split": "support"},
    {"suffix": "v116", "design": "future_prospective_logic_v116", "base": "24",
     "action": "32", "screen_split": "heldout"},
    {"suffix": "v117", "design": "future_prospective_logic_v117", "base": "26",
     "action": "32", "screen_split": "heldout"},
    {"suffix": "v118", "design": "future_prospective_logic_v118", "base": "28",
     "action": "32", "screen_split": "heldout"},
    # These are reserved for a genuinely future observation round after the
    # v113-v118 action-32 support/calibration cohort.  They must not be used
    # to establish the policy that will later score them.
    {"suffix": "v119", "design": "future_prospective_logic_v119", "base": "24",
     "action": "32", "screen_split": "future_observation"},
    {"suffix": "v120", "design": "future_prospective_logic_v120", "base": "26",
     "action": "32", "screen_split": "future_observation"},
    {"suffix": "v121", "design": "future_prospective_logic_v121", "base": "28",
     "action": "32", "screen_split": "future_observation"},
)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def _subset_manifest(manifest: dict, suffixes: set[str] | None) -> dict:
    """Select run/sample lineages without changing the manifest on disk."""
    if not suffixes:
        return manifest
    wanted = {str(value) for value in suffixes if str(value)}
    items = [item for item in manifest.get("items", [])
             if any(str(item.get("lineage_id", "")).startswith(
                 f"future-prospective-{suffix}:") for suffix in wanted)]
    if not items:
        raise RuntimeError(f"no campaign items match requested suffixes: {sorted(wanted)}")
    return {**manifest, "items": items,
            "selected_suffixes": sorted(wanted)}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _select_specs(suffixes: set[str] | None = None) -> list[dict]:
    """Select preregistered lineages without silently widening the cohort."""
    if not suffixes:
        return [dict(spec) for spec in LINEAGES]
    wanted = {str(value) for value in suffixes if str(value)}
    selected = [dict(spec) for spec in LINEAGES if spec["suffix"] in wanted]
    missing = sorted(wanted - {spec["suffix"] for spec in selected})
    if missing:
        raise ValueError(f"unknown calibration lineage suffixes: {missing}")
    if not selected:
        raise ValueError("at least one calibration lineage suffix is required")
    return selected


def _contract_manifest_binding(contract: dict) -> dict:
    """Serialize only the immutable contract identity into a campaign freeze."""
    validate_utility_contract(contract)
    return {
        "contract_id": contract["contract_id"],
        "contract_digest": utility_contract_digest(contract),
        "action_signature": json.loads(json.dumps(contract["action_signature"])),
        "binding": "PREPARE_TIME",
    }


def _validate_contract_manifest(manifest: dict,
                                requested: dict | None = None) -> dict | None:
    """Resolve a manifest contract and reject any evaluation-time attachment."""
    frozen = manifest.get("utility_contract")
    if frozen is None:
        if requested is not None:
            raise ValueError(
                "utility contract must be pre-registered during prepare; "
                "rebuild the campaign manifest before evaluation")
        return None
    if not isinstance(frozen, dict) or frozen.get("binding") != "PREPARE_TIME":
        raise ValueError("campaign utility contract binding is malformed")
    contract_id = frozen.get("contract_id")
    factory = known_utility_contracts().get(contract_id)
    if factory is None:
        raise ValueError("campaign utility contract is not in the known catalog")
    contract = factory()
    expected = _contract_manifest_binding(contract)
    if frozen != expected:
        raise ValueError("campaign utility contract digest/signature mismatch")
    if requested is not None:
        validate_utility_contract(requested)
        if _contract_manifest_binding(requested) != expected:
            raise ValueError("requested utility contract differs from prepare-time binding")
    return contract


def prepare(root: Path, orfs_root: Path, *,
            suffixes: set[str] | None = None,
            utility_contract: dict | None = None) -> dict:
    """Materialize a source cohort, binding an optional contract before flow."""
    template = orfs_root / "flow" / "designs" / "sky130hs" / "gcd"
    cfg, _template_sdc = template / "config.mk", template / "constraint.sdc"
    if not cfg.is_file() or not _template_sdc.is_file():
        raise FileNotFoundError(f"ORFS template incomplete: {template}")
    specs = _select_specs(suffixes)
    frozen_contract = None
    if utility_contract is not None:
        frozen_contract = _contract_manifest_binding(utility_contract)
        signature = utility_contract["action_signature"]
        for spec in specs:
            action_value = str(spec.get("action", "22"))
            action = {
                "domain": signature["domain"],
                "transformation_family": signature["transformation_family"],
                "payload": {
                    "config_edits": {"CORE_UTILIZATION": action_value},
                    "utility_contract_id": utility_contract["contract_id"],
                },
            }
            reason = action_contract_binding_reason(action, utility_contract)
            if reason:
                raise ValueError(
                    f"lineage {spec['suffix']} is not bound to utility contract: {reason}")
    items = []
    for spec in specs:
        fixture = MEMORY_ROOT / "fixtures" / "physical_rtl" / f"{spec['design']}.v"
        sdc = MEMORY_ROOT / "fixtures" / "physical_rtl" / f"{spec['design']}.sdc"
        if not fixture.is_file() or not sdc.is_file():
            raise FileNotFoundError(f"prospective fixture incomplete: {fixture}")
        lineage = f"future-prospective-{spec['suffix']}:sky130hs:{spec['design']}:base0"
        common = {
            "DESIGN_NAME": spec["design"],
            "VERILOG_FILES": str(fixture.resolve()),
            "PLACE_DENSITY_LB_ADDON": "0.25",
            "EQUIVALENCE_CHECK": "0",
            "REMOVE_CELLS_FOR_EQY": "",
        }
        slug = f"sky130hs_{spec['suffix']}_base0"
        before = _materialize(root / "cases" / f"{slug}_before", cfg, sdc,
                              {**common, "CORE_UTILIZATION": spec["base"]})
        action_value = str(spec.get("action", "22"))
        after = _materialize(root / "cases" / f"{slug}_action{action_value}", cfg, sdc,
                             {**common, "CORE_UTILIZATION": action_value})
        item = {
            "case_id": f"{lineage}:DENSITY_RELIEF:{spec['base']}->{action_value}",
            "lineage_id": lineage, "platform": "sky130hs",
            "design": spec["design"], "family": "DENSITY_RELIEF", "check": "route",
            "before_project": str(before), "after_project": str(after),
            "config_edits": {"CORE_UTILIZATION": action_value},
            "screen_split": spec.get("screen_split"),
            "replacement_for": spec.get("replacement_for"),
            "role": "prospective_observation", "capturable": False,
        }
        if frozen_contract is not None:
            item["utility_contract_id"] = frozen_contract["contract_id"]
        items.append(item)
    manifest = {
        "version": VERSION, "orfs_root": str(orfs_root.resolve()), "items": items,
        "storage_policy": storage_policy(root),
        "firewall": {
            "fresh_lineages": sorted(item["lineage_id"] for item in items),
            "disjoint": True,
        },
        "mutation_policy": "new samples remain external until policy evaluation completes",
    }
    if frozen_contract is not None:
        manifest["utility_contract"] = frozen_contract
    _write_json(root / "campaign_manifest.json", manifest)
    return manifest


def _run_features(project: Path) -> int:
    runner = REPO_ROOT / "r2g-skills/def-graph/scripts/flow/run_features.sh"
    with (project / "def_graph_features.log").open("w") as out:
        proc = subprocess.run(
            ["bash", str(runner), str(project), "sky130hs", project.name],
            stdout=out, stderr=subprocess.STDOUT,
            env=dict(os.environ, R2G_SIGNOFF_GATE="warn"))
    return proc.returncode


def _strict_oracle_gate(root: Path, manifest: dict) -> dict[str, dict]:
    """Return per-case eligibility from the bound strict-oracle receipt.

    A completed ORFS flow is not, by itself, calibration evidence.  A sample
    is eligible only when both its before and after projects have a receipt
    from the newest backend run with strict signoff ``pass`` and timing
    ``clean``.  Missing or malformed receipts are represented as evidence and
    fail closed; they are never silently treated as a neutral observation.
    """
    receipt_path = root / "strict_oracle_state.json"
    if not receipt_path.is_file():
        return {
            str(item["case_id"]): {
                "eligible": False,
                "reason": "strict_oracle_missing",
                "projects": {},
            }
            for item in manifest.get("items", [])
        }
    try:
        state = _read(receipt_path)
    except (OSError, ValueError, TypeError) as exc:
        reason = f"strict_oracle_unreadable:{exc}"
        return {
            str(item["case_id"]): {
                "eligible": False, "reason": reason, "projects": {},
            }
            for item in manifest.get("items", [])
        }
    rows = {}
    for row in state.get("projects", []):
        if not isinstance(row, dict) or not row.get("project"):
            continue
        try:
            rows[str(Path(row["project"]).resolve())] = row
        except (OSError, TypeError):
            continue
    gate = {}
    for item in manifest.get("items", []):
        reasons = []
        project_receipts = {}
        for side in ("before_project", "after_project"):
            project = Path(item[side])
            key = str(project.resolve())
            row = rows.get(key)
            if row is None:
                reasons.append(f"{side}:strict_oracle_missing")
                continue
            # Reused receipts deliberately carry null process return codes;
            # a non-zero fresh return code remains a hard failure even if a
            # report was partially emitted.
            strict_rc = row.get("strict_rc")
            timing_rc = row.get("timing_rc")
            if strict_rc not in (None, 0):
                reasons.append(f"{side}:strict_rc={strict_rc}")
            if timing_rc not in (None, 0):
                reasons.append(f"{side}:timing_rc={timing_rc}")
            if row.get("timed_out"):
                reasons.append(f"{side}:timed_out")
            if not row.get("run_tag"):
                reasons.append(f"{side}:run_tag_missing")
            if row.get("strict_report_run_tag") != row.get("run_tag"):
                reasons.append(f"{side}:strict_report_run_mismatch")
            if row.get("strict_status") != "pass":
                reasons.append(f"{side}:strict_status={row.get('strict_status')}")
            if row.get("timing_status") != "clean":
                reasons.append(f"{side}:timing_status={row.get('timing_status')}")
            project_receipts[side] = {
                "project": key,
                "run_tag": row.get("run_tag"),
                "strict_report_run_tag": row.get("strict_report_run_tag"),
                "strict_status": row.get("strict_status"),
                "timing_status": row.get("timing_status"),
                "strict_report_sha256": row.get("strict_report_sha256"),
                "timing_report_sha256": row.get("timing_report_sha256"),
            }
        gate[str(item["case_id"])] = {
            "eligible": not reasons,
            "reason": None if not reasons else ";".join(reasons),
            "projects": project_receipts,
        }
    return gate


def make_samples(root: Path, manifest: dict) -> dict:
    samples, evidence = [], []
    strict_gate = _strict_oracle_gate(root, manifest)
    for item in manifest["items"]:
        case_id = str(item["case_id"])
        oracle = strict_gate.get(case_id, {
            "eligible": False, "reason": "strict_oracle_missing", "projects": {},
        })
        if not oracle["eligible"]:
            evidence.append({"case_id": case_id,
                             "status": "excluded_strict_oracle",
                             "strict_oracle": oracle})
            continue
        before, after = Path(item["before_project"]), Path(item["after_project"])
        final_def = _latest_successful_final_def(before)
        if final_def is None:
            evidence.append({"case_id": case_id, "status": "missing_successful_def",
                             "strict_oracle": oracle})
            continue
        feature_rc = _run_features(before)
        try:
            context = load_defgraph_context(before, def_path=final_def).to_dict()
            record = build_orfs_pair_record(
                before, after, lineage_id=item["lineage_id"], target_check="route",
                config_edits=item["config_edits"], transformation_family="DENSITY_RELIEF")
            observed = extract_deltas(record.before["reports"]["ppa"],
                                       record.after["reports"]["ppa"])
        except (OSError, ValueError, RuntimeError) as exc:
            evidence.append({"case_id": case_id, "status": "pair_unavailable",
                             "feature_rc": feature_rc, "error": str(exc),
                             "strict_oracle": oracle})
            continue
        samples.append({
            "case_id": case_id, "lineage_id": item["lineage_id"],
            "platform": item["platform"], "family": item["family"],
            "expected_tier": context.get("dataset_tier"), "graph_context": context,
            "action": record.action, "before_ppa": record.before["reports"]["ppa"],
            "after_ppa": record.after["reports"]["ppa"],
            "observed_deltas": observed,
        })
        evidence.append({"case_id": case_id, "status": "evaluatable",
                         "lineage_id": item["lineage_id"],
                         "context_digest": context.get("digest"),
                         "observed_deltas": observed, "feature_rc": feature_rc,
                         "strict_oracle": oracle})
    result = {
        "version": VERSION, "samples": samples, "evidence": evidence,
        "source_lineages": sorted({row["lineage_id"] for row in samples}),
        "mutation": "none; prospective samples were not recorded in TEHM",
    }
    _write_json(root / "prospective_samples.json", result)
    return result


def _strict_oracle_projects(manifest: dict) -> list[tuple[Path, str]]:
    """Return each unique before/after project once for full-oracle checks."""
    projects = {
        (Path(item[side]), str(item["platform"]))
        for item in manifest.get("items", [])
        for side in ("before_project", "after_project")
    }
    return sorted(projects, key=lambda value: (str(value[0]), value[1]))


def _strict_oracle_one(project: Path, platform: str, *, timeout: int) -> dict:
    """Run strict signoff plus the timing oracle for one completed ORFS project.

    The strict script is allowed to return non-zero: its reports are evidence
    even when a design is dirty or an infrastructure leg fails.  We therefore
    record both process return codes and normalized report status, never
    converting an unavailable obligation into a pass.
    """
    strict = REPO_ROOT / "r2g-skills/signoff-loop/scripts/flow/run_strict_signoff.sh"
    timing = REPO_ROOT / "r2g-skills/signoff-loop/scripts/reports/check_timing.py"
    strict_log = project / "strict_signoff.log"
    timing_log = project / "timing_check.log"
    reports = project / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    # Reuse only a receipt bound to the newest backend run and a timing report
    # from that same project.  A stale strict report must not satisfy a fresh
    # prospective observation.
    runs = sorted((project / "backend").glob("RUN_*"))
    run_tag = runs[-1].name if runs else None
    existing = _load(reports / "strict_signoff.json")
    reusable = bool(run_tag and existing.get("run_tag") == run_tag and
                    (reports / "timing_check.json").is_file())
    strict_rc = timing_rc = None
    timed_out = False
    if not reusable:
        env = dict(os.environ, NETGEN_TIMEOUT=str(max(1, timeout)))
        try:
            with strict_log.open("w") as output:
                proc = subprocess.run(
                    ["bash", str(strict), str(project), platform, project.name],
                    stdout=output, stderr=subprocess.STDOUT, env=env,
                    timeout=max(1, timeout), check=False)
            strict_rc = int(proc.returncode)
        except subprocess.TimeoutExpired:
            strict_rc = 124
            timed_out = True
        # check_timing is a separate oracle because run_strict_signoff's strict
        # gate does not emit the canonical timing_check.json consumed by the
        # TEHM ORFS pair adapter.
        try:
            with timing_log.open("w") as output:
                proc = subprocess.run(
                    [sys.executable, str(timing), str(project)],
                    stdout=output, stderr=subprocess.STDOUT,
                    env=env, timeout=max(1, timeout), check=False)
            timing_rc = int(proc.returncode)
        except subprocess.TimeoutExpired:
            timing_rc = 124
            timed_out = True

    strict_report = _load(reports / "strict_signoff.json")
    timing_report = _load(reports / "timing_check.json")
    timing_status = timing_report.get("status") or timing_report.get("tier")
    return {
        "project": str(project.resolve()),
        "platform": platform,
        "run_tag": run_tag,
        "strict_report_run_tag": strict_report.get("run_tag"),
        "reused": reusable,
        "strict_rc": strict_rc,
        "timing_rc": timing_rc,
        "timed_out": timed_out,
        "strict_status": strict_report.get("status"),
        "timing_status": timing_status,
        "timing_tier": timing_report.get("tier"),
        "strict_log": str(strict_log.resolve()) if strict_log.is_file() else None,
        "timing_log": str(timing_log.resolve()) if timing_log.is_file() else None,
        "strict_report_sha256": _sha256(strict_report_path)
        if (strict_report_path := reports / "strict_signoff.json").is_file()
        else None,
        "timing_report_sha256": _sha256(timing_report_path)
        if (timing_report_path := reports / "timing_check.json").is_file()
        else None,
    }


def run_strict_oracle(root: Path, manifest: dict, *, workers: int,
                      timeout: int) -> dict:
    """Materialize complete signoff obligations without touching TEHM memory."""
    projects = _strict_oracle_projects(manifest)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_strict_oracle_one, project, platform,
                               timeout=max(1, timeout))
                   for project, platform in projects]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: row["project"])
    state = {
        "version": "calibration-strict-oracle-v1",
        "requested": True,
        "required_reports": ["strict_signoff.json", "timing_check.json"],
        "projects": results,
        "all_reports_present": all(
            row["strict_status"] is not None and row["timing_status"] is not None
            for row in results),
        "mutation_policy": "oracle reports only; no TEHM capture or lifecycle mutation",
    }
    _write_json(root / "strict_oracle_state.json", state)
    return state


def _external_transition_id(sample: dict) -> str:
    payload = canonical_json({
        "lineage_id": sample["lineage_id"], "case_id": sample.get("case_id"),
        "action": sample.get("action"), "graph_context": sample.get("graph_context"),
    })
    return "external-calibration:" + hashlib.sha256(payload).hexdigest()[:24]


def _external_state(sample: dict, *, side: str,
                    transition_id: str) -> CanonicalState:
    """Build a deterministic staging state for an external observation.

    External calibration rows are deliberately not canonical evidence, but a
    staging transition still has to satisfy the same referential-integrity
    contract as a captured transition.  The previous importer created
    ``external_before:*``/``external_after:*`` references without state rows,
    which made the migrated snapshot fail H1 before policy replay.  Keep the
    state minimal and content-bound: PPA/context are preserved as the state
    source witness, while no typed view or lifecycle row is manufactured.
    """
    if side not in {"before", "after"}:
        raise ValueError(f"invalid external state side: {side!r}")
    context = sample.get("graph_context") or {}
    if not isinstance(context, dict):
        raise ValueError("external calibration graph_context must be an object")
    # PhysicalEffectMemory performs the authoritative graph digest check.  We
    # use the supplied digest when present; otherwise the state remains
    # explicitly unbound rather than inventing a graph identity.
    context_digest = str(context.get("digest") or "")
    ppa = sample.get(f"{side}_ppa") or {}
    source = {
        "external": True,
        "side": side,
        "transition_id": transition_id,
        "case_id": sample.get("case_id"),
        "lineage_id": sample.get("lineage_id"),
        "graph_context": context,
        "ppa": ppa,
    }
    verifier = {
        "oracle_type": "TARGET_TEST",
        "verdict": "PASS",
        "source": "external_calibration_sample",
    }
    return CanonicalState(
        domain="flow.signoff",
        project_id=str(sample.get("case_id") or "external-calibration"),
        design_id=str(sample.get("design") or sample.get("case_id") or ""),
        lineage_id=str(sample.get("lineage_id") or "") or None,
        source_digest=source_digest(source),
        context_graph_digest=context_digest,
        verifier_snapshot=verifier,
        artifact_manifest={},
        created_at=db.now_local(),
    )


def _persist_external_state(conn, *, state: CanonicalState) -> None:
    """Insert one immutable, staging-only state row idempotently."""
    row = state.to_row()
    existing = conn.execute(
        "SELECT * FROM tehm_states WHERE state_id=?", (row["state_id"],)
    ).fetchone()
    if existing is not None:
        # ``created_at`` is intentionally volatile and is not part of the
        # content-addressed state identity.  Every other field is evidence
        # bound and must agree on replay.
        conflicts = [
            key for key, value in row.items()
            if key != "created_at" and str(existing[key] or "") != str(value or "")
        ]
        if conflicts:
            raise ValueError(
                "external calibration state is immutable and conflicts: "
                + ",".join(conflicts))
        return
    conn.execute(
        """INSERT INTO tehm_states (
               state_id, domain, project_id, design_id, lineage_id,
               repository_ref, source_digest, context_graph_digest,
               verifier_snapshot_json, artifact_manifest_json,
               created_at, schema_version)
           VALUES (:state_id, :domain, :project_id, :design_id, :lineage_id,
                   :repository_ref, :source_digest, :context_graph_digest,
                   :verifier_snapshot_json, :artifact_manifest_json,
                   :created_at, :schema_version)""",
        row,
    )


def _persist_external_transition(conn, *, transition_id: str, sample: dict,
                                 action: dict, action_json: str,
                                 source_state_id: str | None = None,
                                 target_state_id: str | None = None) -> None:
    """Insert one staging transition without overwriting immutable evidence."""
    values = (
        transition_id, source_state_id or "external_before:" + transition_id,
        target_state_id or "external_after:" + transition_id,
        str(action.get("domain") or "flow.CONFIG_DELTA"), action_json,
        canonical_json({"original_failure": "REMOVED"}).decode(),
        canonical_json({"verdict": "PASS", "oracle_type": "TARGET_TEST"}).decode(),
        "", "PASS", "[]", "[]",
        canonical_json({"source": "external_calibration_sample",
                        "lineage_id": sample["lineage_id"]}).decode(),
        SCHEMA_VERSION)
    columns = (
        "transition_id", "source_state_id", "target_state_id", "action_domain",
        "action_json", "observation_delta_json", "verifier_json",
        "primary_effect_key", "outcome", "created_regressions_json",
        "newly_observed_json", "provenance_json", "schema_version")
    existing = conn.execute(
        "SELECT * FROM tehm_transitions WHERE transition_id=?",
        (transition_id,)).fetchone()
    if existing is not None:
        conflicts = [column for column, value in zip(columns, values)
                     if str(existing[column] or "") != str(value or "")]
        if conflicts:
            raise ValueError(
                "external calibration transition is immutable and conflicts: "
                + ",".join(conflicts))
        return
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO tehm_transitions ({','.join(columns)}) "
        f"VALUES ({placeholders})", values)


def _load_external_training(root: Path, conn, store: ArtifactStore,
                            samples: list[dict]) -> list[str]:
    """Bind external samples to staging-only transitions for action filtering."""
    physical = PhysicalEffectMemory(conn)
    lineages = []
    had_outer_transaction = conn.in_transaction
    savepoint = "tehm_calibration_external_training_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    savepoint_active = True
    try:
        for sample in samples:
            transition_id = _external_transition_id(sample)
            action = sample.get("action") or {}
            action_json = canonical_json(action).decode()
            before_state = _external_state(
                sample, side="before", transition_id=transition_id)
            after_state = _external_state(
                sample, side="after", transition_id=transition_id)
            _persist_external_state(conn, state=before_state)
            _persist_external_state(conn, state=after_state)
            # This minimal transition is deliberately staging-only.  It carries
            # the action provenance required by action-conditioned physical
            # retrieval; the actual PPA evidence remains in the physical row
            # and report.
            _persist_external_transition(
                conn, transition_id=transition_id, sample=sample,
                action=action, action_json=action_json,
                source_state_id=before_state.state_id,
                target_state_id=after_state.state_id)
            physical.record(
                transition_id=transition_id,
                action_domain=str(action.get("domain") or "flow.CONFIG_DELTA"),
                transformation_family=str(sample.get("family") or "DENSITY_RELIEF"),
                before_ppa=sample["before_ppa"], after_ppa=sample["after_ppa"],
                effect_key="", evidence_refs=[], graph_context=sample["graph_context"],
                commit=False)
            # External calibration observations are retained for audit in a
            # non-learner split.  This explicit row prevents a future learner
            # query from treating a staging support transition as implicit
            # ``live`` training evidence.
            assign_transition(
                conn, transition_id=transition_id,
                campaign_id="calibration-expansion-v1", split="calibration",
                learner_eligible=False)
            lineages.append(str(sample["lineage_id"]))
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        savepoint_active = False
        if not had_outer_transaction:
            conn.commit()
    except Exception:
        if savepoint_active:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    return sorted(set(lineages))


def _sample_from_pair(pair: dict) -> dict:
    return {
        "case_id": pair.get("case_id"), "lineage_id": pair["lineage_id"],
        "family": "DENSITY_RELIEF", "graph_context": pair["graph_context"],
        "action": pair["action"], "before_ppa": pair["before_ppa"],
        "after_ppa": pair["after_ppa"],
        "observed_deltas": extract_deltas(pair["before_ppa"], pair["after_ppa"]),
    }


def _strict_eligible_samples(path: Path) -> tuple[list[dict], list[dict]]:
    """Load only samples whose file-level evidence proves strict eligibility.

    Calibration inputs are often copied between campaign roots.  Requiring
    the matching ``evaluatable`` evidence row here prevents a stale or
    hand-edited ``samples`` array from bypassing the strict-oracle firewall.
    Legacy files without that receipt are retained in the exclusion report but
    cannot enter the staging denominator.
    """
    data = _read(path)
    evidence = {
        str(row.get("case_id")): row
        for row in (data.get("evidence") or [])
        if isinstance(row, dict) and row.get("case_id")
    }
    accepted, excluded = [], []
    for sample in data.get("samples") or []:
        case_id = str(sample.get("case_id") or "")
        row = evidence.get(case_id)
        oracle = row.get("strict_oracle") if row else None
        reason = None
        if row is None:
            reason = "strict_oracle_evidence_missing"
        elif row.get("status") != "evaluatable":
            reason = f"sample_status={row.get('status')}"
        elif not isinstance(oracle, dict) or not oracle.get("eligible"):
            reason = str((oracle or {}).get("reason") or
                         "strict_oracle_not_eligible")
        if reason is not None:
            excluded.append({"case_id": case_id, "reason": reason})
            continue
        accepted.append(sample)
    return accepted, excluded


def _grouped_shadow_admission(*, retrieval_policy: dict,
                              heldout_samples: list[dict],
                              training_lineages: list[str],
                              utility_contract: dict | None = None) -> tuple[dict, dict]:
    """Grade one frozen retrieval cohort against the Parametric shadow gates.

    ``calibrate_retrieval`` owns point prediction and OOD diagnostics.  This
    adapter only binds those already-emitted predictions to the same external
    held-out rows, then delegates safety/utility authority to the lineage-
    grouped Parametric calibrator.  No held-out row is inserted into TEHM.
    """
    evaluations = retrieval_policy.get("evaluations") or []
    grouped_samples = []
    invalid = []
    for evaluation in evaluations:
        index = evaluation.get("index")
        if (isinstance(index, bool) or not isinstance(index, int) or
                index < 0 or index >= len(heldout_samples)):
            invalid.append({"index": index, "reason": "invalid_evaluation_index"})
            continue
        if evaluation.get("status") != "evaluated":
            continue
        sample = heldout_samples[index]
        metrics = evaluation.get("metrics") or {}
        predicted = {
            str(metric): detail.get("predicted")
            for metric, detail in metrics.items()
            if isinstance(detail, dict) and detail.get("predicted") is not None
        }
        graph = sample.get("graph_context") or {}
        signature = _action_signature(sample.get("action"))
        if not predicted or signature is None:
            invalid.append({
                "index": index, "lineage_id": sample.get("lineage_id"),
                "reason": ("missing_predicted_metrics" if not predicted
                           else "invalid_action_signature"),
            })
            continue
        grouped_samples.append({
            "case_id": sample.get("case_id"),
            "lineage_id": sample.get("lineage_id"),
            "platform": graph.get("platform") or sample.get("platform"),
            "family": sample.get("family"),
            "dataset_tier": graph.get("dataset_tier") or sample.get("expected_tier"),
            "action_signature": signature,
            "predicted": predicted,
            "observed_deltas": sample.get("observed_deltas") or {},
        })
    report = calibrate_exact_groups(
        grouped_samples, training_lineages=training_lineages,
        target_coverage=0.80, min_lineages=3,
        min_samples_per_metric=3, max_harmful_rate=0.0,
        utility_contract=utility_contract)
    report["adapter_invalid_evaluations"] = invalid
    if invalid and report.get("status") == "ready_for_shadow":
        report["status"] = "shadow_calibration_failed"
        report["reason"] = "invalid_retrieval_evaluation"

    materialization = {
        "status": "not_materialized",
        "reason": "grouped_calibration_not_ready",
        "policy": None,
    }
    if report.get("status") == "ready_for_shadow":
        groups = report.get("groups") or {}
        group = next(iter(groups.values())) if len(groups) == 1 else {}
        heldout = (group.get("firewall") or {}).get("heldout_lineages") or []
        first = grouped_samples[0] if grouped_samples else {}
        distance = (retrieval_policy.get("thresholds") or {}).get("max_distance")
        if distance is None:
            materialization["reason"] = "retrieval_distance_threshold_missing"
        elif len(heldout) < 3:
            materialization["reason"] = "insufficient_heldout_lineages"
        else:
            try:
                policy = materialize_shadow_policy(
                    report,
                    scope={"platform": first.get("platform"),
                           "family": first.get("family"),
                           "dataset_tier": first.get("dataset_tier")},
                    action_signature=first.get("action_signature") or {},
                    max_distance=min(float(distance), 3.0),
                    min_unique_contexts=3,
                    utility_contract=utility_contract)
            except (TypeError, ValueError) as exc:
                materialization["reason"] = f"materialization_rejected:{exc}"
            else:
                materialization = {
                    "status": "materialized_shadow_only",
                    "reason": "all_grouped_shadow_gates_passed",
                    "policy": policy,
                }
    return report, materialization


def _grouped_shadow_readiness(*, grouped_report: dict,
                              materialization: dict) -> dict:
    """Project grouped calibration evidence into the RFC readiness contract.

    This is deliberately narrower than the legacy all-family physical
    readiness report: one exact action-signature group is enough to open a
    *shadow observation* lane, but never enough to authorize a Parametric View
    or production retrieval.  Every criterion is derived from the materialized
    policy and grouped report rather than from a status label alone.
    """
    policy = materialization.get("policy") if isinstance(materialization, dict) else None
    group_values = list((grouped_report.get("groups") or {}).values()) \
        if isinstance(grouped_report, dict) else []
    group = group_values[0] if len(group_values) == 1 else {}
    checks = (group.get("checks") or {}) if isinstance(group, dict) else {}
    firewall = (policy.get("firewall") or {}) if isinstance(policy, dict) else {}
    thresholds = (policy.get("thresholds") or {}) if isinstance(policy, dict) else {}
    calibration = (policy.get("calibration") or {}) if isinstance(policy, dict) else {}
    heldout = sorted({str(value) for value in firewall.get("heldout_lineages") or []})
    distance = thresholds.get("max_distance")
    try:
        distance_gate = (isinstance(distance, (int, float)) and
                         not isinstance(distance, bool) and
                         float(distance) > 0.0 and float(distance) <= 3.0)
    except (TypeError, ValueError):
        distance_gate = False
    try:
        empirical_coverage = float(calibration.get("empirical_coverage", 0.0))
        required_coverage = float(calibration.get("required_coverage", 0.8))
    except (TypeError, ValueError):
        empirical_coverage, required_coverage = 0.0, 1.0
    per_metric = calibration.get("conformal_quantiles") or {}
    uncertainty_gate = bool(per_metric) and all(
        isinstance(value, (int, float)) and float(value) >= 0.0
        for value in per_metric.values())
    all_ready = (materialization.get("status") == "materialized_shadow_only" and
                 grouped_report.get("status") == "ready_for_shadow" and
                 policy is not None and policy.get("status") == "ready" and
                 policy.get("shadow_only") is True and
                 policy.get("promotion_eligible") is False and
                 policy.get("canonical_memory_mutation") == "none")
    criteria = {
        "all_retrieval_policies_ready": all_ready,
        "distance_gate_satisfied": distance_gate,
        "coverage_gate_satisfied": bool(checks.get("conformal_lineage_coverage") is True and
                                          empirical_coverage >= required_coverage),
        "uncertainty_gate_satisfied": uncertainty_gate,
        "minimum_independent_heldout_lineages": 3,
        "observed_independent_heldout_lineages": len(heldout),
        "lineage_diversity_satisfied": len(heldout) >= 3,
    }
    ready = all(criteria[name] is True for name in (
        "all_retrieval_policies_ready", "distance_gate_satisfied",
        "coverage_gate_satisfied", "uncertainty_gate_satisfied",
        "lineage_diversity_satisfied"))
    return {
        "version": "parametric-shadow-readiness-v1",
        "readiness_kind": "lineage_grouped_shadow_observation",
        "status": "READY_FOR_IMPLEMENTATION" if ready else "DEFERRED_INSUFFICIENT_EVIDENCE",
        "parametric_view_status": "NOT_IMPLEMENTED",
        "shadow_only": True,
        "promotion_eligible": False,
        "canonical_memory_mutation": "none",
        "criteria": criteria,
        "policy_scope": policy.get("scope") if isinstance(policy, dict) else None,
        "policy_kind": policy.get("policy_kind") if isinstance(policy, dict) else None,
        "policy_status": policy.get("status") if isinstance(policy, dict) else None,
        "utility_contract_id": (policy.get("utility_contract_id")
                                 if isinstance(policy, dict) else None),
        "utility_contract_digest": (policy.get("utility_contract_digest")
                                    if isinstance(policy, dict) else None),
        "calibration_lineages": heldout,
        "reason": ("grouped action-signature policy satisfies shadow gates"
                   if ready else "grouped action-signature policy remains fail-closed"),
    }


def _augment_sample_ppa(sample: dict, samples_path: Path) -> dict:
    """Recover compact PPA sides from the promoted calibration case receipts."""
    if sample.get("before_ppa") and sample.get("after_ppa"):
        return dict(sample)
    root = samples_path.parent.parent if samples_path.parent.name == "calibration" \
        else samples_path.parent
    case = root / "cases" / str(sample.get("case_id", "")).replace(":", "_")
    before_path, after_path = case / "before_ppa.json", case / "after_ppa.json"
    if not before_path.is_file() or not after_path.is_file():
        raise RuntimeError(f"promoted calibration PPA evidence missing for {sample.get('case_id')}")
    return {**sample, "before_ppa": _read(before_path), "after_ppa": _read(after_path)}


def evaluate(root: Path, *, canonical_snapshot: Path,
             v10v11_samples: Path, v12v13_pairs: Path,
             training_manifest: Path,
             prior_samples: Path | tuple[Path, ...] | list[Path] | None = None,
             fresh_suffixes: set[str] | None = None,
             interval_method: str = "normal_weighted_mean_v1",
             utility_contract: dict | None = None) -> dict:
    utility_contract = _validate_contract_manifest(
        _load(root / "campaign_manifest.json"), utility_contract)
    stage = root / "staging"
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(canonical_snapshot, stage)
    stage_db = stage / "closed_loop" / "tehm.sqlite"
    store = ArtifactStore(stage / "closed_loop" / "artifacts")
    excluded_prior = []
    v10v11_rows, excluded = _strict_eligible_samples(v10v11_samples)
    excluded_prior.extend({"path": str(v10v11_samples.resolve()), **row}
                          for row in excluded)
    v10v11 = [_augment_sample_ppa(sample, v10v11_samples)
              for sample in v10v11_rows if sample.get("platform") == "sky130hs"]
    # The legacy v12/v13 pair format has no strict-oracle evidence envelope;
    # fail closed until those lineages are regenerated by this runner.
    pairs_data = _read(v12v13_pairs)
    pairs = pairs_data.get("pairs") or []
    excluded_prior.extend({"path": str(v12v13_pairs.resolve()),
                           "case_id": str(pair.get("case_id") or ""),
                           "reason": "strict_oracle_evidence_missing"}
                          for pair in pairs)
    prior = list(v10v11)
    prior_paths = []
    if prior_samples is not None:
        prior_paths = ([prior_samples] if isinstance(prior_samples, Path)
                       else list(prior_samples))
    for prior_path in prior_paths:
        prior_data, excluded = _strict_eligible_samples(prior_path)
        excluded_prior.extend({"path": str(prior_path.resolve()), **row}
                              for row in excluded)
        prior.extend(_augment_sample_ppa(sample, prior_path)
                     for sample in prior_data if sample.get("platform", "sky130hs") == "sky130hs")
    conn = db.connect(stage_db)
    db.ensure_schema(conn)
    added_lineages = _load_external_training(root, conn, store, prior)
    fresh, excluded_fresh = _strict_eligible_samples(root / "prospective_samples.json")
    excluded_prior.extend({"path": str((root / "prospective_samples.json").resolve()),
                           "split": "fresh", **row}
                          for row in excluded_fresh)
    if fresh_suffixes:
        fresh = [sample for sample in fresh
                 if any(str(sample.get("lineage_id", "")).startswith(
                     f"future-prospective-{suffix}:") for suffix in fresh_suffixes)]
    canonical_training = (_read(training_manifest).get("training_lineages") or
                          ((_read(training_manifest).get("firewall") or {}).get(
                              "training_lineages") or []))
    training = sorted(set(canonical_training) | set(added_lineages))
    physical = PhysicalEffectMemory(conn)
    policy = calibrate_retrieval(
        physical, family="DENSITY_RELIEF", heldout_samples=fresh,
        training_lineages=training, min_samples=3, min_unique_contexts=3,
        target_coverage=0.80, distance_ceiling=3.0,
        interval_method=interval_method)
    grouped_calibration, shadow_materialization = _grouped_shadow_admission(
        retrieval_policy=policy, heldout_samples=fresh,
        training_lineages=training, utility_contract=utility_contract)
    shadow_readiness = _grouped_shadow_readiness(
        grouped_report=grouped_calibration,
        materialization=shadow_materialization)
    count_after = physical.count()
    conn.close()
    report = {
        "version": VERSION, "policy": policy,
        "parametric_grouped_calibration": grouped_calibration,
        "shadow_policy_materialization": shadow_materialization,
        "parametric_shadow_readiness": shadow_readiness,
        "utility_contract": ({
            "contract_id": utility_contract["contract_id"],
            "contract_digest": utility_contract_digest(utility_contract),
        } if utility_contract is not None else None),
        "training_lineages": training,
        "added_external_lineages": added_lineages,
        "fresh_lineages": sorted({row["lineage_id"] for row in fresh}),
        "canonical_snapshot": str(canonical_snapshot.resolve()),
        "staging_snapshot": str(stage.resolve()),
        "staging_snapshot_digest": _sha256(stage_db),
        "staging_physical_count": count_after,
        "excluded_samples": excluded_prior,
        "canonical_mutation": "none; only a staging copy was opened writable",
        "promotion_eligible": False,
        "parametric_view_status": "NOT_IMPLEMENTED",
    }
    _write_json(root / "calibration_expansion_report.json", report)
    _write_json(root / "parametric_readiness.json", shadow_readiness)
    return report


def promote(root: Path, evidence_root: Path) -> dict:
    evidence_root.mkdir(parents=True, exist_ok=True)
    for name in ("campaign_manifest.json", "campaign_recovery_report.json",
                 "strict_oracle_state.json", "prospective_samples.json",
                 "calibration_expansion_report.json", "parametric_readiness.json"):
        src = root / name
        if src.is_file():
            shutil.copy2(src, evidence_root / name)
    samples = _read(root / "prospective_samples.json") \
        if (root / "prospective_samples.json").is_file() else {}
    evaluated_lineages = sorted({str(row.get("lineage_id"))
                                 for row in samples.get("samples", [])
                                 if row.get("lineage_id")})
    selected_suffixes = sorted({
        lineage.split(":", 1)[0].removeprefix("future-prospective-")
        for lineage in evaluated_lineages
        if lineage.startswith("future-prospective-")
    })
    report = {
        "version": VERSION, "scratch_root": str(root),
        "evidence_root": str(evidence_root), "promotion_eligible": False,
        "selected_suffixes": selected_suffixes,
        "evaluated_lineages": evaluated_lineages,
        "mutation": "none; canonical v3 was not opened writable",
    }
    _write_json(evidence_root / "promotion_report.json", report)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", choices=("all", "prepare", "run", "samples",
                                         "evaluate", "promote"), default="all")
    ap.add_argument("--root", type=Path, default=SCRATCH_DEFAULT)
    ap.add_argument("--evidence-root", type=Path, default=EVIDENCE_DEFAULT)
    ap.add_argument("--orfs-root", type=Path, default=ORFS_DEFAULT)
    ap.add_argument("--canonical-snapshot", type=Path,
                    default=resolve_bundle(require_exists=False))
    ap.add_argument("--v10v11-samples", type=Path, required=True)
    ap.add_argument("--v12v13-pairs", type=Path, required=True)
    ap.add_argument("--training-manifest", type=Path, required=True)
    ap.add_argument("--prior-samples", type=Path, action="append", default=[],
                    help="previous cohort promoted to staging-only training support (repeatable)")
    ap.add_argument("--fresh-suffix", action="append", default=[],
                    help="lineage suffixes to hold out for evaluation (repeatable)")
    ap.add_argument(
        "--interval-method",
        choices=("normal_weighted_mean_v1", "split_conformal_residual_v1"),
        default="normal_weighted_mean_v1",
        help="retrieval diagnostic interval; grouped shadow admission always uses lineage conformal")
    ap.add_argument(
        "--utility-contract-id", choices=sorted(known_utility_contracts()),
        help="explicit pre-registered utility contract for grouped shadow materialization")
    ap.add_argument("--run-suffix", action="append", default=[],
                    help="lineage suffixes to run/sample (repeatable; scratch-only subset)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--cpus-per-run", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--strict-oracle", action="store_true",
                    help="run strict signoff and timing reports before sampling")
    ap.add_argument("--strict-timeout", type=int, default=3600,
                    help="per-project timeout for strict signoff/timing")
    args = ap.parse_args(argv)
    root = enforce_work_root(args.root.resolve())
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "campaign_manifest.json"
    requested_contract = (known_utility_contracts()[args.utility_contract_id]()
                          if args.utility_contract_id else None)
    manifest = (prepare(root, args.orfs_root.resolve(),
                        suffixes=set(args.run_suffix) or None,
                        utility_contract=requested_contract)
                if args.phase in {"all", "prepare"}
                else _load(manifest_path))
    if not manifest:
        raise RuntimeError(f"campaign manifest missing: {manifest_path}")
    utility_contract = _validate_contract_manifest(manifest, requested_contract)
    if args.phase == "prepare":
        return 0
    run_manifest = _subset_manifest(manifest, set(args.run_suffix) or None)
    if args.phase in {"all", "run"}:
        run_projects(root, run_manifest, workers=max(1, args.workers),
                     cpus=max(1, args.cpus_per_run), timeout=max(1, args.timeout))
    if args.strict_oracle and args.phase in {"all", "run", "samples"}:
        run_strict_oracle(root, run_manifest, workers=max(1, args.workers),
                          timeout=max(1, args.strict_timeout))
    if args.phase == "run":
        return 0
    if args.phase in {"all", "samples"}:
        make_samples(root, run_manifest)
    if args.phase == "samples":
        return 0
    if args.phase in {"all", "evaluate"}:
        report = evaluate(root, canonical_snapshot=args.canonical_snapshot.resolve(),
                          v10v11_samples=args.v10v11_samples.resolve(),
                          v12v13_pairs=args.v12v13_pairs.resolve(),
                          training_manifest=args.training_manifest.resolve(),
                          prior_samples=tuple(path.resolve() for path in args.prior_samples),
                          fresh_suffixes=set(args.fresh_suffix) or None,
                          interval_method=args.interval_method,
                          utility_contract=utility_contract)
    if args.phase in {"all", "evaluate"}:
        print(json.dumps({"ok": True, "policy_status": report["policy"]["status"],
                          "shadow_admission_status": report[
                              "parametric_grouped_calibration"]["status"],
                          "coverage": report["policy"]["calibration"].get("empirical_coverage"),
                          "fresh_lineages": report["fresh_lineages"],
                          "promotion_eligible": False}, indent=2, sort_keys=True))
    if args.phase in {"all", "promote"}:
        promote(root, args.evidence_root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
