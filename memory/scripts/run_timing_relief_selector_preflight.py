#!/usr/bin/env python3
"""Run a baseline-first, selector-before-action prospective lane.

The lane deliberately separates baseline observation from the density-relief
after arm.  It materializes and runs only the 50% baseline for every frozen
lineage, obtains a real graph/PPA context, then calls the read-only typed
selector.  An abstained lineage never gets an after project or an ORFS action
run.  Selected items are emitted as a normal batch manifest for the existing
staging-only ORFS phases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from run_orfs_batch0 import (  # noqa: E402
    _bind_sdc,
    _load,
    _write,
    build_source_freeze,
    run_equivalence,
    run_graph_contexts,
    run_signoff,
)
from run_orfs_diversity_campaign import _materialize, run_projects  # noqa: E402
from tehm.batch_lane import (  # noqa: E402
    BATCH_LANE_VERSION,
    assess_full_oracle,
    canonical_snapshots,
    assert_snapshots_unchanged,
)
from tehm.physical.memory import PhysicalEffectMemory  # noqa: E402
from tehm.physical.utility_contracts import (  # noqa: E402
    contract_action,
    select_contract_proposal,
    timing_relief_budgeted_v1,
    timing_relief_budgeted_v2_50_to_45,
    utility_contract_digest,
)


VERSION = "timing-relief-selector-preflight-v1"
ACTION_FAMILY = "DENSITY_RELIEF"
DEFAULT_SPEC = ROOT / "evaluation" / "timing_relief_selector_preflight_spec_v1.json"
DEFAULT_POLICY = ROOT.parent / "evidence" / "tehm-authority-v1" / "v4" / "conformal_calibration_report.json"
DEFAULT_SUPPORT_DB = ROOT.parent / "evidence" / "tehm-authority-v1" / "v4" / "staging" / "tehm.sqlite"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--orfs-root", type=Path,
                    default=Path(os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")))
    ap.add_argument("--source-spec", type=Path, default=DEFAULT_SPEC)
    ap.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    ap.add_argument("--support-db", type=Path, default=DEFAULT_SUPPORT_DB)
    ap.add_argument("--phase", choices=("prepare", "baseline", "select", "all"), default="all")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--cpus-per-run", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--signoff-timeout", type=int, default=900)
    ap.add_argument("--equivalence-timeout", type=int, default=300)
    args = ap.parse_args(argv)

    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    before_canonical = canonical_snapshots()
    if args.phase in {"prepare", "all"}:
        manifest = prepare(root, args.orfs_root.resolve(), args.source_spec.resolve())
        if args.phase == "prepare":
            return 0
    else:
        manifest = _load(root / "preflight_manifest.json")
        if not manifest:
            raise RuntimeError(f"preflight manifest missing: {root / 'preflight_manifest.json'}")

    if args.phase in {"baseline", "all"}:
        run_baselines(root, manifest, workers=max(1, args.workers), cpus=max(1, args.cpus_per_run),
                      timeout=max(1, args.timeout), equivalence_timeout=max(1, args.equivalence_timeout),
                      signoff_timeout=max(1, args.signoff_timeout))
        if args.phase == "baseline":
            assert_snapshots_unchanged(before_canonical, canonical_snapshots())
            return 0

    if args.phase in {"select", "all"}:
        decisions = select_after_baselines(
            root, manifest, policy_path=args.policy.resolve(), support_db=args.support_db.resolve())
        assert_snapshots_unchanged(before_canonical, canonical_snapshots())
        print(json.dumps({
            "version": VERSION,
            "campaign_id": manifest["campaign_id"],
            "sample_count": len(decisions),
            "proposed_count": sum(x["status"] == "PROPOSED" for x in decisions),
            "abstained_count": sum(x["status"] == "ABSTAINED" for x in decisions),
            "after_projects_materialized": sum(bool(x.get("after_project")) for x in decisions),
        }, sort_keys=True))
    return 0


def prepare(root: Path, orfs_root: Path, source_spec: Path) -> dict:
    spec = json.loads(source_spec.read_text())
    contract = _contract_from_id(spec.get("contract_id"))
    expected_digest = utility_contract_digest(contract)
    if spec.get("contract_id") != contract["contract_id"]:
        raise ValueError("preflight spec contract_id mismatch")
    if spec.get("utility_contract_digest") != expected_digest:
        raise ValueError("preflight spec utility_contract_digest mismatch")
    sdc_template = (REPO_ROOT / spec["sdc_template"]).resolve()
    if not sdc_template.is_file():
        raise FileNotFoundError(sdc_template)
    before_util = int(spec.get("before_core_utilization", 50))
    after_util = int(spec["after_core_utilization"])
    expected_action = contract_action(contract)
    if str(expected_action["payload"]["config_edits"].get("CORE_UTILIZATION")) != str(after_util):
        raise ValueError("preflight after utilization does not match contract action")
    items = []
    seen_lineages, seen_digests = set(), set()
    for raw in spec.get("lineages") or ():
        lineage = str(raw["id"])
        rtl = (REPO_ROOT / raw["rtl"]).resolve()
        if lineage in seen_lineages or not rtl.is_file():
            raise ValueError(f"invalid/duplicate preflight lineage: {lineage}")
        seen_lineages.add(lineage)
        source_digest = _sha(rtl)
        if source_digest in seen_digests:
            raise ValueError(f"duplicate RTL source digest: {lineage}")
        seen_digests.add(source_digest)
        template = orfs_root / "flow" / "designs" / str(spec["platform"]) / "gcd"
        cfg = template / "config.mk"
        if not cfg.is_file():
            raise FileNotFoundError(cfg)
        project = _materialize(
            root / "baseline" / f"{spec['platform']}_{raw['design']}_u{before_util}",
            cfg, sdc_template, {
                "DESIGN_NAME": str(raw["top"]),
                "VERILOG_FILES": str(rtl),
                "CORE_UTILIZATION": str(before_util),
                "PLACE_DENSITY_LB_ADDON": "0.25",
                "EQUIVALENCE_CHECK": "0",
            })
        _bind_sdc(project / "constraints" / "constraint.sdc",
                  top=str(raw["top"]), clock_port=str(spec["clock_port"]))
        items.append({
            "case_id": f"{spec['platform']}:{raw['design']}:preflight:{before_util}->{after_util}",
            "lineage_id": lineage,
            "design": str(raw["design"]),
            "top": str(raw["top"]),
            "platform": str(spec["platform"]),
            "family": ACTION_FAMILY,
            "check": "route",
            "split": str(raw["split"]),
            "role": "selector_preflight",
            "rtl_files": [str(rtl)],
            "source_digest": source_digest,
            "config_edits": {"CORE_UTILIZATION": str(after_util)},
            "before_project": str(project),
            "after_project": None,
        })
    if len(items) < 6:
        raise ValueError("selector preflight requires at least six lineages")
    splits = {key: sorted(x["lineage_id"] for x in items if x["split"] == key)
              for key in ("support", "calibration", "heldout")}
    if splits["support"] or len(splits["calibration"]) != 2 or len(splits["heldout"]) != 4:
        raise ValueError("preflight split must be 2 calibration + 4 heldout, with no support")
    freeze = build_source_freeze(root, orfs_root, source_spec)
    manifest = {
        "version": VERSION,
        "campaign_id": f"{contract['contract_id'].lower()}-selector-preflight",
        "batch_lane_version": BATCH_LANE_VERSION,
        "orfs_root": str(orfs_root),
        "source_spec": str(source_spec),
        "source_spec_sha256": _sha(source_spec),
        "source_freeze": str((root / "source_freeze.json").resolve()),
        "source_freeze_sha256": _sha(root / "source_freeze.json"),
        "contract_id": contract["contract_id"],
        "utility_contract_digest": expected_digest,
        "action_signature": {
            "domain": expected_action["domain"],
            "family": ACTION_FAMILY,
            "config_edits": {"CORE_UTILIZATION": str(after_util)},
            "operation_point": f"{before_util}->{after_util}",
            "utility_contract_id": contract["contract_id"],
        },
        "before_core_utilization": before_util,
        "after_core_utilization": after_util,
        "items": items,
        "firewall": {
            "version": "timing-relief-selector-preflight-firewall-v1",
            "calibration": splits["calibration"],
            "heldout": splits["heldout"],
            "support": [],
            "disjoint": True,
            "source_digests_unique": len(seen_digests) == len(items),
        },
        "selector_policy": "external_policy_read_only",
        "canonical_memory_mutation": "none",
        "promotion_attempted": False,
        "baseline_only_before_selection": True,
    }
    _write(root / "preflight_manifest.json", manifest)
    return manifest


def run_baselines(root: Path, manifest: dict, *, workers: int, cpus: int,
                  timeout: int, equivalence_timeout: int, signoff_timeout: int) -> None:
    # The existing executor accepts before/after pairs.  Both sides point at
    # the same baseline project here, so its deduplicated runner executes one
    # baseline per lineage and cannot accidentally run the action arm.
    runner_manifest = {**manifest, "items": [
        {**item, "after_project": item["before_project"]} for item in manifest["items"]]}
    _write(root / "baseline_runner_manifest.json", runner_manifest)
    run_projects(root, runner_manifest, workers=workers, cpus=cpus,
                 timeout=timeout, supervisor_grace=90)
    run_equivalence(root, runner_manifest, timeout=equivalence_timeout)
    run_signoff(root, runner_manifest, timeout=signoff_timeout)
    run_graph_contexts(root, runner_manifest)
    _write(root / "baseline_report.json", _baseline_report(root, manifest))


def select_after_baselines(root: Path, manifest: dict, *, policy_path: Path,
                           support_db: Path) -> list[dict]:
    policy_payload = json.loads(policy_path.read_text())
    policy = policy_payload.get("policy", policy_payload)
    if policy.get("status") != "ready":
        raise ValueError("selector calibration policy is not ready")
    conn = sqlite3.connect(f"file:{support_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    contract = _contract_from_id(manifest.get("contract_id"))
    action = contract_action(contract)
    decisions = []
    before_canonical = canonical_snapshots()
    try:
        memory = PhysicalEffectMemory(conn)
        for item in manifest["items"]:
            project = Path(item["before_project"])
            baseline = assess_full_oracle(project, rtl_files=[Path(x) for x in item["rtl_files"]])
            graph = baseline.get("graph") or {}
            result = select_contract_proposal(
                memory,
                graph_context=graph,
                baseline_ppa=_ppa(baseline.get("ppa_metrics") or {}),
                calibration_policy=policy,
                hard_checks=baseline.get("checks") or {},
                obligation_coverage=1.0,
                action=action,
                contract=contract,
            )
            decision = {
                "case_id": item["case_id"],
                "lineage_id": item["lineage_id"],
                "split": item["split"],
                "status": result.get("status"),
                "abstain_reasons": result.get("abstain_reasons", []),
                "nearest_distance": (result.get("prediction") or {}).get("nearest_distance"),
                "support": (result.get("prediction") or {}).get("support"),
                "baseline_checks": baseline.get("checks"),
                "baseline_ppa_metrics": baseline.get("ppa_metrics"),
                "baseline_graph_digest": graph.get("digest"),
                "policy_sha256": _sha(policy_path),
                "contract_id": contract["contract_id"],
                "utility_contract_digest": utility_contract_digest(contract),
                "canonical_memory_mutation": "none",
                "promotion_eligible": False,
            }
            if result.get("status") == "PROPOSED":
                after = _materialize_after(root, manifest, item)
                decision["after_project"] = str(after)
            decisions.append(decision)
    finally:
        conn.close()
    assert_snapshots_unchanged(before_canonical, canonical_snapshots())
    _write(root / "selector_decisions.json", {
        "version": VERSION,
        "campaign_id": manifest["campaign_id"],
        "policy": str(policy_path.resolve()),
        "policy_sha256": _sha(policy_path),
        "contract_id": contract["contract_id"],
        "utility_contract_digest": utility_contract_digest(contract),
        "sample_count": len(decisions),
        "proposed_count": sum(x["status"] == "PROPOSED" for x in decisions),
        "abstained_count": sum(x["status"] == "ABSTAINED" for x in decisions),
        "decisions": decisions,
        "canonical_memory_mutation": "none",
        "promotion_attempted": False,
    })
    selected = []
    by_lineage = {x["lineage_id"]: x for x in decisions if x.get("status") == "PROPOSED"}
    for item in manifest["items"]:
        decision = by_lineage.get(item["lineage_id"])
        if not decision:
            continue
        selected.append({**item, "role": "selector_selected",
                         "after_project": decision["after_project"]})
    selected_manifest = {**manifest, "campaign_id": manifest["campaign_id"] + "-selected",
                         "items": selected,
                         "firewall": {**manifest["firewall"],
                                      "selected_lineages": sorted(by_lineage)}}
    _write(root / "selected_campaign_manifest.json", selected_manifest)
    _write(root / "stop_report.json", _stop_report(
        root, manifest, decisions, policy_payload, before_canonical))
    return decisions


def _materialize_after(root: Path, manifest: dict, item: dict) -> Path:
    orfs_root = Path(manifest["orfs_root"])
    template = orfs_root / "flow" / "designs" / item["platform"] / "gcd"
    after_util = int(manifest["after_core_utilization"])
    source_spec = Path(manifest["source_spec"])
    spec = json.loads(source_spec.read_text())
    sdc_template = (REPO_ROOT / spec["sdc_template"]).resolve()
    project = _materialize(
        root / "selected" / f"{item['design']}_u{after_util}",
        template / "config.mk", sdc_template,
        {"DESIGN_NAME": item["top"], "VERILOG_FILES": item["rtl_files"][0],
         "CORE_UTILIZATION": str(after_util), "PLACE_DENSITY_LB_ADDON": "0.25",
         "EQUIVALENCE_CHECK": "0"})
    _bind_sdc(project / "constraints" / "constraint.sdc",
              top=item["top"], clock_port="clk")
    return project


def _baseline_report(root: Path, manifest: dict) -> dict:
    rows = []
    for item in manifest["items"]:
        assessment = assess_full_oracle(Path(item["before_project"]),
                                        rtl_files=[Path(x) for x in item["rtl_files"]])
        rows.append({"case_id": item["case_id"], "lineage_id": item["lineage_id"],
                     "project": item["before_project"], "assessment": assessment})
    return {"version": VERSION, "campaign_id": manifest["campaign_id"],
            "baseline_count": len(rows), "results": rows,
            "canonical_memory_mutation": "none", "promotion_attempted": False}


def _stop_report(root: Path, manifest: dict, decisions: list[dict],
                 policy_payload: dict, canonical_before: list[dict]) -> dict:
    proposed = [x for x in decisions if x.get("status") == "PROPOSED"]
    coverage = len(proposed) / len(decisions) if decisions else None
    calibration = (policy_payload.get("policy", policy_payload).get("calibration") or {})
    operation_point = str((manifest.get("action_signature") or {}).get(
        "operation_point", "unknown"))
    stop_status = "STOP_{}_LOW_PROPOSAL_COVERAGE".format(
        operation_point.replace("->", "_TO_"))
    return {
        "version": VERSION,
        "status": stop_status if coverage is not None and coverage < 0.5 else "CONTINUE_SELECTED_ORFS",
        "campaign_id": manifest["campaign_id"],
        "sample_count": len(decisions),
        "proposed_count": len(proposed),
        "proposal_coverage": coverage,
        "proposal_coverage_minimum": 0.5,
        "selected_harmful_rate": None,
        "selected_harmful_rate_status": "NOT_EVALUABLE_NO_SELECTED_AFTER_ARMS",
        "conformal_coverage": calibration.get("empirical_coverage"),
        "cross_lineage_te": None,
        "cross_lineage_te_status": "NOT_EVALUABLE_NO_SELECTED_AFTER_ARMS",
        "promotion_gates": {
            "rollback_verified": False, "registry_verified": False,
            "obligation_coverage": False, "cross_lineage_te": False,
            "harmful_rate": False, "conformal_coverage": False,
        },
        "canonical_before": canonical_before,
        "canonical_after": canonical_snapshots(),
        "canonical_memory_mutation": "none",
        "promotion_attempted": False,
        "reason": (f"selector proposal coverage below frozen 50% threshold for "
                   f"{operation_point}; keep this action shadow-only until a "
                   "separately authorized cohort clears the gates"),
    }


def _ppa(metrics: dict) -> dict:
    return {"summary": {
        "timing": {"setup_wns": metrics.get("wns_ns"), "setup_tns": metrics.get("tns_ns")},
        "area": {"design_area_um2": metrics.get("area_um2")},
        "power": {"total_power_w": metrics.get("power_w")},
    }}


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _contract_from_id(contract_id: str) -> dict:
    factories = {
        "TIMING_RELIEF_BUDGETED_V1": timing_relief_budgeted_v1,
        "TIMING_RELIEF_BUDGETED_V2_50_TO_45": timing_relief_budgeted_v2_50_to_45,
    }
    try:
        return factories[str(contract_id)]()
    except KeyError as exc:
        raise ValueError(f"unsupported preflight contract: {contract_id}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
