#!/usr/bin/env python3
"""Prepare and execute an observation-only prospective shadow cohort.

Two new RTL lineages (v12/v13) are routed with one fixed observation action.
The shadow predictor is generated before the fixed action is considered, while
the post-run PPA outcome is joined offline.  The script never opens TEHM for
writing; RUN trees stay in ``/tmp`` and only compact evidence is promoted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MEMORY_ROOT.parent
sys.path.insert(0, str(MEMORY_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_orfs_diversity_campaign import (  # noqa: E402
    _latest_successful_final_def, _load, _materialize, _write, run_projects,
)
from tehm.adapters.orfs_pair import build_orfs_pair_record  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402
from tehm.physical.graph_context import load_defgraph_context  # noqa: E402
from tehm.sync import canonical_json  # noqa: E402
from orfs_storage import enforce_work_root, storage_policy  # noqa: E402


VERSION = "fresh-prospective-observation-v1"
ROOT_DEFAULT = Path("/tmp/tehm-p2-prospective-v12v13")
EVIDENCE_DEFAULT = Path(
    "/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v12v13"
)
ORFS_DEFAULT = Path(
    os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")
)
LINEAGES = (
    {
        "lineage_id": "future-prospective-v12:sky130hs:future_prospective_logic_v12:base0",
        "design": "future_prospective_logic_v12", "fixture": "future_prospective_logic_v12",
        "base": "34", "action": "22",
    },
    {
        "lineage_id": "future-prospective-v13:sky130hs:future_prospective_logic_v13:base0",
        "design": "future_prospective_logic_v13", "fixture": "future_prospective_logic_v13",
        "base": "36", "action": "22",
    },
)


def _read(path: Path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(root: Path, orfs_root: Path) -> dict:
    items = []
    for spec in LINEAGES:
        template = orfs_root / "flow" / "designs" / "sky130hs" / "gcd"
        cfg = template / "config.mk"
        fixture = MEMORY_ROOT / "fixtures" / "physical_rtl" / f"{spec['fixture']}.v"
        sdc = MEMORY_ROOT / "fixtures" / "physical_rtl" / f"{spec['fixture']}.sdc"
        if not cfg.is_file() or not fixture.is_file() or not sdc.is_file():
            raise FileNotFoundError(f"prospective source/template incomplete for {spec['lineage_id']}")
        common = {
            "DESIGN_NAME": spec["design"], "VERILOG_FILES": str(fixture.resolve()),
            "PLACE_DENSITY_LB_ADDON": "0.25", "EQUIVALENCE_CHECK": "0",
            "REMOVE_CELLS_FOR_EQY": "",
        }
        slug = f"sky130hs_{spec['fixture']}_base0"
        before = _materialize(root / "cases" / f"{slug}_before", cfg, sdc,
                              {**common, "CORE_UTILIZATION": spec["base"]})
        after = _materialize(root / "cases" / f"{slug}_action22", cfg, sdc,
                             {**common, "CORE_UTILIZATION": spec["action"]})
        items.append({
            "case_id": f"{spec['lineage_id']}:DENSITY_RELIEF:{spec['base']}->{spec['action']}",
            "lineage_id": spec["lineage_id"], "platform": "sky130hs",
            "design": spec["design"], "family": "DENSITY_RELIEF", "check": "route",
            "before_project": str(before), "after_project": str(after),
            "config_edits": {"CORE_UTILIZATION": spec["action"]},
            "role": "prospective_observation", "capturable": False,
        })
    manifest = {
        "version": VERSION, "orfs_root": str(orfs_root.resolve()), "items": items,
        "storage_policy": storage_policy(root),
        "mutation_policy": "observation only; no TEHM capture or lifecycle mutation",
    }
    _write(root / "campaign_manifest.json", manifest)
    return manifest


def _run_features(project: Path) -> int:
    runner = REPO_ROOT / "r2g-skills/def-graph/scripts/flow/run_features.sh"
    log = project / "def_graph_features.log"
    with log.open("w") as stream:
        proc = subprocess.run(
            ["bash", str(runner), str(project), "sky130hs", project.name],
            stdout=stream, stderr=subprocess.STDOUT,
            env=dict(os.environ, R2G_SIGNOFF_GATE="warn"))
    return proc.returncode


def make_cases(root: Path, manifest: dict, policy_path: Path,
               source_manifest: Path, calibration_lineages: list[str]) -> dict:
    policy_report = _read(policy_path)
    policy = policy_report["policy"]
    pairs, rows, outcomes = [], [], []
    for item in manifest["items"]:
        before, after = Path(item["before_project"]), Path(item["after_project"])
        final_def = _latest_successful_final_def(before)
        if final_def is None:
            raise RuntimeError(f"missing successful base DEF: {before}")
        feature_rc = _run_features(before)
        if feature_rc != 0:
            raise RuntimeError(f"feature extraction failed for {before}: rc={feature_rc}")
        context = load_defgraph_context(before, def_path=final_def).to_dict()
        record = build_orfs_pair_record(
            before, after, lineage_id=item["lineage_id"], target_check="route",
            config_edits=item["config_edits"], transformation_family="DENSITY_RELIEF")
        action = record.action
        target = item["lineage_id"]
        obs_case = f"{target}:observation"
        decision_case = f"{target}:decision"
        action20 = dict(action)
        action20["payload"] = dict(action["payload"])
        action20["payload"]["config_edits"] = {"CORE_UTILIZATION": "20"}
        action25 = dict(action)
        action25["payload"] = dict(action["payload"])
        action25["payload"]["config_edits"] = {"CORE_UTILIZATION": "25"}
        rows.append({
            "case_id": obs_case, "target_id": f"{target}:logic:observation",
            "lineage_id": target, "platform": "sky130hs", "family": "DENSITY_RELIEF",
            "phase": "observation", "graph_context_digest": context["digest"],
            "candidate_actions": [action],
            "policy_scope": policy_report.get("policy_scope"),
        })
        rows.append({
            "case_id": decision_case, "target_id": f"{target}:logic:decision",
            "lineage_id": target, "platform": "sky130hs", "family": "DENSITY_RELIEF",
            "phase": "decision", "graph_context_digest": context["digest"],
            "candidate_actions": [action20, action25],
            "policy_scope": policy_report.get("policy_scope"),
            "graph_context": context, "calibration_policy": policy,
        })
        pairs.append({"case_id": obs_case, "lineage_id": target,
                      "graph_context": context, "action": action,
                      "before_ppa": record.before["reports"]["ppa"],
                      "after_ppa": record.after["reports"]["ppa"],
                      "obligation_coverage": record.verification.get("obligation_coverage"),
                      "verification": record.verification})
        outcomes.append({
            "case_id": obs_case,
            "before_ppa": record.before["reports"]["ppa"],
            "after_ppa": record.after["reports"]["ppa"],
            "oracle": {
                "oracle_type": "ORFS_ROUTE_PPA",
                "verdict": record.verification.get("verdict"),
                "obligation_coverage": record.verification.get("obligation_coverage"),
                "verification": record.verification,
            },
        })
    _write(root / "observation_pairs.json", {"version": VERSION, "pairs": pairs})
    _write(root / "observation_outcomes.json", {"version": VERSION, "outcomes": outcomes})
    case_rows = [row for row in rows if row.get("phase") == "observation"]
    decision_rows = [row for row in rows if row.get("phase") == "decision"]
    root.joinpath("cases.jsonl").write_bytes(
        b"\n".join(canonical_json(row) for row in case_rows) + b"\n")
    root.joinpath("decision_cases.jsonl").write_bytes(
        b"\n".join(canonical_json(row) for row in decision_rows) + b"\n")
    return {"rows": rows, "pairs": pairs, "outcomes": outcomes,
            "policy": policy, "calibration_lineages": calibration_lineages,
            "source_manifest": str(source_manifest.resolve()),
            "policy_path": str(policy_path.resolve()),
            "policy_digest": _sha256(policy_path)}


def make_prospective_manifest(root: Path, rows: list[dict], source_manifest: Path,
                              calibration_lineages: list[str]) -> Path:
    source = _read(source_manifest)
    firewall = source["firewall"]
    raw = {
        "version": "parametric-prospective-manifest-v1", "status": "PLANNED",
        "source_freeze": source["source_freeze"],
        "firewall": {
            "training_lineages": list(firewall.get("training_lineages") or []),
            "calibration_lineages": sorted(set(firewall.get("calibration_lineages") or []) |
                                             set(calibration_lineages)),
            "heldout_lineages": list(firewall.get("heldout_lineages") or []),
            "ab_lineages": list(firewall.get("ab_lineages") or []),
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
            "min_metric_evaluations": 2,
        },
    }
    raw_path = root / "prospective_manifest.raw.json"
    _write_json(raw_path, raw)
    normalized = root / "manifest.normalized.json"
    checker = MEMORY_ROOT / "scripts" / "prepare_parametric_prospective_manifest.py"
    proc = subprocess.run([sys.executable, str(checker), "--input", str(raw_path),
                           "--output", str(normalized)], capture_output=True, text=True)
    (root / "manifest_validation.log").write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"prospective manifest refused: {proc.stdout}{proc.stderr}")
    return normalized


def promote(root: Path, evidence_root: Path, manifest: dict, made: dict) -> dict:
    evidence_root.mkdir(parents=True, exist_ok=True)
    for name in ("campaign_manifest.json", "campaign_recovery_report.json",
                 "observation_pairs.json", "observation_outcomes.json",
                 "cases.jsonl", "decision_cases.jsonl",
                 "prospective_manifest.raw.json", "manifest.normalized.json",
                 "manifest_validation.log"):
        source = root / name
        if source.is_file():
            shutil.copy2(source, evidence_root / name)
    promoted = []
    for item in manifest["items"]:
        case_dir = evidence_root / "cases" / item["case_id"].replace(":", "_")
        case_dir.mkdir(parents=True, exist_ok=True)
        for side in ("before", "after"):
            project = Path(item[f"{side}_project"])
            final_def = _latest_successful_final_def(project)
            for rel in ("campaign-run-receipt.json", "reports/ppa.json",
                        "reports/route.json", "reports/features_stats.json"):
                src = project / rel
                if src.is_file():
                    shutil.copy2(src, case_dir / f"{side}_{Path(rel).name}")
            if final_def is not None:
                dst = case_dir / f"{side}_final.def"
                shutil.copy2(final_def, dst)
                promoted.append({"case_id": item["case_id"], "side": side,
                                 "def": str(dst)})
    policy_path = Path(made["policy_path"])
    policy_name = policy_path.name
    if policy_path.is_file():
        shutil.copy2(policy_path, evidence_root / policy_name)
    binding = {
        "version": "fresh-prospective-shadow-binding-v1",
        "policy": policy_name,
        "policy_sha256": made["policy_digest"],
        "shadow_status": "SHADOW_ONLY",
        "parametric_view_status": "NOT_IMPLEMENTED",
        "promotion_eligible": False,
        "canonical_memory_mutation": "none",
        "decision_gate": "decision_gate.json",
    }
    _write_json(evidence_root / "shadow_campaign_binding.json", binding)
    report = {"version": VERSION, "scratch_root": str(root),
              "evidence_root": str(evidence_root), "promoted_files": promoted,
              "observation_case_count": len(made["pairs"]),
              "future_lineages": sorted({x["lineage_id"] for x in made["rows"]}),
              "policy": policy_name, "policy_sha256": made["policy_digest"],
              "mutation": "none; canonical TEHM was not opened for writing"}
    _write_json(evidence_root / "promotion_report.json", report)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", choices=("prepare", "run", "cases", "promote", "all"), default="all")
    ap.add_argument("--root", type=Path, default=ROOT_DEFAULT)
    ap.add_argument("--evidence-root", type=Path, default=EVIDENCE_DEFAULT)
    ap.add_argument("--orfs-root", type=Path, default=ORFS_DEFAULT)
    ap.add_argument("--source-manifest", type=Path, required=True)
    ap.add_argument("--policy", type=Path, required=True)
    ap.add_argument("--calibration-lineage", action="append", default=[])
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--cpus-per-run", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=1200)
    args = ap.parse_args(argv)
    root = enforce_work_root(args.root.resolve())
    root.mkdir(parents=True, exist_ok=True)
    manifest = prepare(root, args.orfs_root.resolve()) \
        if args.phase in {"prepare", "all"} else _load(root / "campaign_manifest.json")
    if not manifest:
        raise RuntimeError("campaign manifest missing")
    if args.phase == "prepare":
        return 0
    if args.phase in {"run", "all"}:
        run_projects(root, manifest, workers=max(1, args.workers),
                     cpus=max(1, args.cpus_per_run), timeout=max(1, args.timeout))
    if args.phase == "run":
        return 0
    made = make_cases(root, manifest, args.policy, args.source_manifest,
                      args.calibration_lineage) \
        if args.phase in {"cases", "all"} else {
            "rows": _read(root / "prospective_manifest.raw.json")["cases"],
            "pairs": (_read(root / "observation_pairs.json").get("pairs") or []),
            "outcomes": (_read(root / "observation_outcomes.json").get("outcomes") or []),
            "policy_path": str(args.policy.resolve()),
            "policy_digest": _sha256(args.policy.resolve()),
        }
    normalized = make_prospective_manifest(root, made["rows"], args.source_manifest,
                                           args.calibration_lineage) \
        if args.phase in {"cases", "all"} else root / "manifest.normalized.json"
    if args.phase == "cases":
        return 0
    report = promote(root, args.evidence_root.resolve(), manifest, made)
    print(json.dumps({"ok": True, "manifest": str(normalized),
                      "observation_cases": len(made["pairs"]),
                      "evidence_root": str(args.evidence_root.resolve()),
                      "scratch_root": str(root)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
