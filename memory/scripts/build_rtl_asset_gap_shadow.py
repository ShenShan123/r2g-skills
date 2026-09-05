#!/usr/bin/env python3
"""Build an answer-free alpha-transfer RTL asset-gap shadow receipt.

The input projects are ordinary parser-backed RTL fixtures executed by the
registered Icarus oracle.  Training projects are captured into a *derived*
database so the gap detector can see repeated learner-eligible evidence;
held-out binding uses only RTL and a template frozen from a verified training
fix. These hand-written alpha-equivalent fixtures do not establish independent
design generalization or Revision3 L3 expansion. Held-out and incompatible
non-target projects are validated externally and never become learner support.
The generated asset is registered as
``candidate`` only.  This script has no canonical-memory or production
promotion path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db  # noqa: E402
from tehm.assets import (  # noqa: E402
    bind_rtl_asset_to_source, build_rtl_asset_proposal, with_structural_binding,
    detect_capability_gaps, get_asset,
    record_asset_authority, verify_asset_authority,
    register_asset_proposal, set_asset_status, validate_rtl_asset_project,
    validate_rtl_rewrite_asset,
)
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.causal.orfs import _sha256  # noqa: E402
from tehm.causal.rtl import capture_rtl_causal_fragment  # noqa: E402
from tehm.rtl.compatibility import profile_for_action  # noqa: E402
from tehm.rtl.rtl_actions import apply_rtl_action  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402


def _stable_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _manifest(project: Path) -> dict:
    value = json.loads((project / "manifest.json").read_text())
    if not isinstance(value, dict):
        raise ValueError(f"manifest must be an object: {project}")
    return value


def _proposal_from_gap(gap, project: Path):
    manifest = _manifest(project)
    fix = dict(manifest.get("fix") or {})
    domain = str(fix.get("domain") or "")
    if not domain:
        raise ValueError(f"project fix has no domain: {project}")
    profile = profile_for_action({"domain": domain,
                                  "compatibility_profile": fix.get(
                                      "compatibility_profile")})
    payload = {key: fix[key] for key in (
        "domain", "module", "source_state", "target_state", "add_condition",
        "reg", "target", "replacement", "count", "reset_signal", "signal",
        "case_expr", "higher_label", "lower_label",
        "compatibility_profile") if key in fix}
    payload["domain"] = domain
    payload["compatibility_profile"] = profile
    proposal = build_rtl_asset_proposal(
        gap,
        name="rtl.handshake_guard_strengthen.template",
        transformation_family=str(fix.get("transformation_family") or
                                  "GUARD_STRENGTHEN"),
        action_payload_template=payload,
        compatibility_profile=profile,
        verifier_obligations=("RTL_TARGET_TEST_PASS",
                              "RTL_FROZEN_REGRESSION_PASS",
                              "RTL_COMPILE_PASS"),
        creator="tehm-c4-c5-shadow-synthesizer")
    return with_structural_binding(proposal, _rtl_source(project))


def _rtl_source(project: Path) -> str:
    paths = sorted((project / "rtl").glob("*.v"))
    if len(paths) != 1:
        raise ValueError("structural asset lane requires exactly one RTL source")
    return paths[0].read_text()


def _bind(asset: dict, project: Path) -> dict:
    # No manifest (including family labels) or testbench enters localization.
    return bind_rtl_asset_to_source(asset, _rtl_source(project),
                                    design_id=project.name)


def _baseline_receipt(project: Path, oracle: IcarusOracle) -> dict:
    verification = _manifest(project).get("verification") or {}
    return oracle.verify(
        sorted((project / "rtl").glob("*.v")),
        target_tb=project / verification.get("target_test", "tb/tb_handshake.v"),
        regression_tb=project / verification.get("frozen_regression", "tb/tb_basic.v"))


def _static_receipt(asset: dict, project: Path) -> dict:
    rtl_files = sorted((project / "rtl").glob("*.v"))
    if not rtl_files:
        raise ValueError(f"project has no rtl/*.v: {project}")
    return validate_rtl_rewrite_asset(
        asset, rtl_files[0].read_text()).to_dict()


def _oracle_receipt(asset: dict, project: Path, oracle: IcarusOracle) -> dict:
    return validate_rtl_asset_project(asset, project, oracle=oracle).to_dict()


def _rollback_receipt(bindings: list[dict], oracle: IcarusOracle) -> dict:
    """Verify exact source restoration after a contained asset trial.

    The check never writes the source project.  It applies the bound action in
    a temporary file, restores original bytes, and reruns the baseline oracle.
    """
    entries = []
    for item in bindings:
        project = Path(item["project"])
        manifest = _manifest(project)
        rtl_files = sorted((project / "rtl").glob("*.v"))
        if not rtl_files:
            entries.append({"project": str(project), "verified": False,
                            "reason": "project has no rtl/*.v"})
            continue
        source_path = rtl_files[0]
        original = source_path.read_bytes()
        asset = item["asset"]
        payload = dict((((asset.get("definition") or {}).get("action") or {})
                        .get("payload") or {}))
        fixed_source, edit = apply_rtl_action(original.decode(), payload)
        if not edit.get("rewritten"):
            entries.append({"project": str(project), "verified": False,
                            "reason": "asset did not rewrite during rollback trial"})
            continue
        original_digest = hashlib.sha256(original).hexdigest()
        with tempfile.TemporaryDirectory(prefix="tehm_asset_rollback_") as td:
            trial_path = Path(td) / source_path.name
            trial_path.write_text(fixed_source)
            fixed_digest = hashlib.sha256(trial_path.read_bytes()).hexdigest()
            trial_path.write_bytes(original)
            restored_digest = hashlib.sha256(trial_path.read_bytes()).hexdigest()
            verification_cfg = manifest.get("verification") or {}
            target_tb = project / verification_cfg.get("target_test", "tb/tb_handshake.v")
            regression_tb = project / verification_cfg.get("frozen_regression", "tb/tb_basic.v")
            baseline = oracle.verify(
                [trial_path, *rtl_files[1:]],
                target_tb=target_tb if target_tb.exists() else None,
                regression_tb=regression_tb if regression_tb.exists() else None)
        verified = bool(
            fixed_digest != original_digest and
            restored_digest == original_digest and
            baseline.get("target", {}).get("verdict") == "FAIL" and
            baseline.get("regression", {}).get("verdict") == "PASS")
        entries.append({
            "project": str(project),
            "original_sha256": original_digest,
            "fixed_sha256": fixed_digest,
            "restored_sha256": restored_digest,
            "oracle_executed_restored_source": True,
            "baseline_target_verdict": baseline.get("target", {}).get("verdict"),
            "baseline_regression_verdict": baseline.get("regression", {}).get("verdict"),
            "verified": verified,
        })
    return {
        "verified": bool(entries) and all(item.get("verified") is True for item in entries),
        "entries": entries,
        "oracle": "icarus/vvp",
    }


def build_rtl_asset_gap_shadow(
    source_db: Path | str,
    *,
    output_dir: Path | str,
    training_projects: list[Path | str],
    heldout_projects: list[Path | str],
    non_target_projects: list[Path | str],
    campaign_id: str = "rtl-asset-gap-shadow-r1",
    mechanism_family: str = "HANDSHAKE_COMPLETION",
) -> dict:
    if len(training_projects) < 2:
        raise ValueError("C4 gap detection needs at least two training lineages")
    if not heldout_projects:
        raise ValueError("at least one held-out project is required")
    if not non_target_projects:
        raise ValueError("at least one non-target project is required")
    source = Path(source_db).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    if any(Path(str(source) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise ValueError("source database must be a frozen sidecar-free snapshot")
    source_conn = db.connect_read_only(source)
    source_conn.close()
    train = [Path(item).resolve() for item in training_projects]
    heldout = [Path(item).resolve() for item in heldout_projects]
    non_target = [Path(item).resolve() for item in non_target_projects]
    train_lineages = {str(_manifest(path).get("design") or path.name)
                      for path in train}
    heldout_lineages = {str(_manifest(path).get("design") or path.name)
                        for path in heldout}
    if train_lineages & heldout_lineages:
        raise ValueError("held-out project lineage leaked into training")
    source_digest = _sha256(source)
    output = Path(output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("output directory must be empty; previous evidence is immutable")
    output.mkdir(parents=True, exist_ok=True)
    derived_db = output / "tehm.sqlite"
    # The input is already a validated, sidecar-free frozen snapshot. Opening
    # it via SQLite mode=ro for backup would still create WAL/SHM sidecars.
    shutil.copyfile(source, derived_db)
    conn = db.connect(derived_db)
    db.ensure_schema(conn)
    store = ArtifactStore(output / "artifacts")
    oracle = IcarusOracle()
    if not oracle.available:
        conn.close()
        raise RuntimeError("Icarus oracle is required for real C4/C5 shadow execution")

    training_receipts = []
    for project in train:
        receipt = capture_rtl_causal_fragment(
            conn, store, project, oracle=oracle, campaign_id=campaign_id,
            dataset_split="training", dataset_learner_eligible=True)
        training_receipts.append(receipt.to_dict())
    gaps = detect_capability_gaps(
        conn, campaign_id=campaign_id, min_lineages=len(train_lineages),
        min_failures=2)
    gap = next((item for item in gaps
                if item.mechanism_family == mechanism_family and
                "RTL_REWRITE_TEMPLATE" in item.missing_asset_types), None)
    if gap is None:
        conn.close()
        raise ValueError(
            f"no repeated unsupported RTL gap for mechanism {mechanism_family!r}")

    proposal = _proposal_from_gap(gap, train[0])
    proposal_dict = proposal.to_dict()
    asset_receipt = register_asset_proposal(conn, proposal)
    set_asset_status(
        conn, asset_id=asset_receipt.asset_id,
        target_scope=asset_receipt.target_scope, status="shadow",
        provenance={"shadow_execution": "pending"})
    asset = get_asset(conn, asset_receipt.asset_id)
    assert asset is not None
    asset["asset_id"] = asset_receipt.asset_id

    training_static = []
    training_oracle = []
    for project in train:
        bound = _bind(asset, project)
        training_static.append({
            "project": str(project),
            "asset": bound,
            "receipt": _static_receipt(bound, project),
        })
        training_oracle.append({
            "project": str(project),
            "receipt": _oracle_receipt(bound, project, IcarusOracle()),
        })
    heldout_receipts = []
    for project in heldout:
        bound = _bind(asset, project)
        heldout_receipts.append({
            "project": str(project),
            "asset": bound,
            "receipt": _oracle_receipt(bound, project, IcarusOracle()),
        })
    non_target_receipts = []
    for project in non_target:
        try:
            bound = _bind(asset, project)
        except (OSError, TypeError, ValueError) as exc:
            before = _baseline_receipt(project, oracle)
            after = _baseline_receipt(project, oracle)
            non_target_receipts.append({
                "project": str(project), "status": "INAPPLICABLE",
                "reason": str(exc),
                "baseline_oracle": before, "abstention_oracle": after,
                "regression_preserved": (
                    before.get("regression", {}).get("verdict") == "PASS" and
                    after.get("regression", {}).get("verdict") == "PASS" and
                    before.get("target", {}).get("verdict") in {"PASS", "FAIL"} and
                    before.get("target", {}).get("verdict") ==
                    after.get("target", {}).get("verdict")),
            })
        else:
            non_target_receipts.append({
                "project": str(project), "status": "BOUND_UNEXPECTEDLY",
                "asset": bound,
            })
    training_pass = all(
        item["receipt"].get("status") == "SHADOW_ORACLE_PASS"
        and item["receipt"].get("oracle_verdict") == "PASS"
        and item["receipt"].get("regression_verdict") == "PASS"
        for item in training_oracle)
    heldout_pass = all(
        item["receipt"].get("status") == "SHADOW_ORACLE_PASS"
        and item["receipt"].get("oracle_verdict") == "PASS"
        and item["receipt"].get("regression_verdict") == "PASS"
        for item in heldout_receipts)
    no_regression = all(item.get("status") == "INAPPLICABLE" and
                        item.get("regression_preserved") is True
                        for item in non_target_receipts)
    if not (training_pass and heldout_pass and no_regression):
        conn.close()
        raise ValueError("asset shadow execution did not satisfy its bounded receipts")
    candidate_receipt = set_asset_status(
        conn, asset_id=asset_receipt.asset_id,
        target_scope=asset_receipt.target_scope, status="candidate",
        provenance={
            "shadow_execution": "real_icarus_oracle",
            "training_lineages": sorted(train_lineages),
            "heldout_lineages": sorted(heldout_lineages),
            "cross_lineage_verified": True,
            "no_regression": True,
        })
    asset = get_asset(conn, asset_receipt.asset_id) or asset
    asset["asset_id"] = asset_receipt.asset_id
    all_bindings = [item for item in training_static] + heldout_receipts
    rollback = _rollback_receipt(all_bindings, IcarusOracle())
    validation_evidence = [
        {"receipt": item["receipt"], "split": "training",
         "lineage_id": _manifest(Path(item["project"])).get("design"),
         "project": item["project"]}
        for item in training_oracle
    ] + [
        {"receipt": item["receipt"], "split": "heldout",
         "lineage_id": _manifest(Path(item["project"])).get("design"),
         "project": item["project"]}
        for item in heldout_receipts
    ]
    binding_evidence = [
        {"asset": item["asset"], "split": "training",
         "lineage_id": _manifest(Path(item["project"])).get("design"),
         "project": item["project"]}
        for item in training_static
    ] + [
        {"asset": item["asset"], "split": "heldout",
         "lineage_id": _manifest(Path(item["project"])).get("design"),
         "project": item["project"]}
        for item in heldout_receipts
    ]
    authority_receipt = record_asset_authority(
        conn, asset_id=asset_receipt.asset_id,
        target_scope=asset_receipt.target_scope,
        validation_receipts=validation_evidence,
        bindings=binding_evidence,
        rollback_receipt=rollback,
        min_lineages=len(train_lineages))
    authority_verification = verify_asset_authority(conn, authority_receipt)
    if authority_verification["eligible"] is not True:
        conn.close()
        raise AssertionError(
            "asset authority receipt did not replay: "
            f"{authority_verification['reasons']}")
    db.checkpoint_and_close(conn)
    if _sha256(source) != source_digest:
        raise AssertionError("source canonical database changed during shadow build")
    report = {
        "version": "rtl-asset-gap-shadow-v2",
        "source_db": str(source),
        "source_db_sha256": source_digest,
        "derived_db": str(derived_db),
        "derived_db_sha256": _sha256(derived_db),
        "campaign_id": campaign_id,
        "mechanism_family": mechanism_family,
        "canonical_memory_mutation": "none",
        "training_capture": training_receipts,
        "gap_receipts": [item.to_dict() for item in gaps],
        "selected_gap": gap.to_dict(),
        "asset_proposal": proposal_dict,
        "asset_registration": asset_receipt.to_dict(),
        "training_static_validation": training_static,
        "training_oracle_validation": training_oracle,
        "heldout_validation": heldout_receipts,
        "non_target_compatibility": non_target_receipts,
        "candidate_status": candidate_receipt.to_dict(),
        "rollback_receipt": rollback,
        "asset_authority_receipt": authority_receipt.to_dict(),
        "asset_authority_verification": authority_verification,
        "asset_promotion_eligible": authority_receipt.eligible,
        "claim_scope": "alpha_equivalent_fixture_transfer",
        "l3_capability_expansion_established": False,
        "evolution_reason_admission_established": False,
        "firewall": {
            "training_lineages": sorted(train_lineages),
            "heldout_lineages": sorted(heldout_lineages),
            "disjoint": not bool(train_lineages & heldout_lineages),
            "heldout_entered_learner_memory": False,
            "heldout_binding_reads_manifest": False,
            "heldout_binding_reads_testbench": False,
            "training_proposal_source": "verified_fixture_fix",
            "independent_design_generalization_established": False,
        },
        "shadow_execution": {
            "training_pass": training_pass,
            "heldout_pass": heldout_pass,
            "no_regression": no_regression,
            "independent_oracle": "icarus/vvp",
        },
        "promotion_attempted": False,
        "production_promotion_eligible": False,
        "authority_note": (
            "Training fixes are fixture-supplied; held-out binding consumes RTL "
            "only. This establishes bounded alpha-equivalent fixture transfer, "
            "not independent-design generalization or L3 expansion. The "
            "Revision3 reason/admission chain is not established by this lane. "
            "No canonical or production authority is changed; "
            "the asset authority receipt is audit-only; no lifecycle promotion "
            "or production policy mutation is attempted."),
    }
    (output / "asset_gap_shadow_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--training-project", type=Path, action="append",
                        required=True)
    parser.add_argument("--heldout-project", type=Path, action="append",
                        required=True)
    parser.add_argument("--non-target-project", type=Path, action="append",
                        required=True)
    parser.add_argument("--campaign-id", default="rtl-asset-gap-shadow-r1")
    parser.add_argument("--mechanism-family", default="HANDSHAKE_COMPLETION")
    args = parser.parse_args(argv)
    report = build_rtl_asset_gap_shadow(
        args.source_db, output_dir=args.output,
        training_projects=args.training_project,
        heldout_projects=args.heldout_project,
        non_target_projects=args.non_target_project,
        campaign_id=args.campaign_id,
        mechanism_family=args.mechanism_family)
    print(json.dumps({
        "selected_gap": report["selected_gap"]["gap_id"],
        "asset_id": report["asset_registration"]["asset_id"],
        "candidate_status": report["candidate_status"]["status"],
        "asset_promotion_eligible": report["asset_promotion_eligible"],
        "heldout_pass": report["shadow_execution"]["heldout_pass"],
        "production_promotion_eligible": report[
            "production_promotion_eligible"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
