"""RAM-only v6 Asset/core wiring conformance; TRAIN authority stays closed."""
from __future__ import annotations

import copy
import argparse
import json
import sqlite3
from pathlib import Path

from tehm import db
from tehm.adapters import research_r5_rtl_scoped_v3 as train
from tehm.assets.authority import record_asset_authority, verify_asset_authority
from tehm.assets.lifecycle import evaluate_asset_authority
from tehm.assets.registry import get_asset, get_asset_status, set_asset_status
from tehm.assets.skid_binding_v6 import (
    CONTRACT, is_skid_v6_asset, with_skid_payload_binding_v6,
)
from tehm.assets.source_selection import (
    source_contract, verify_candidate_source_replay, verify_source_copy,
)
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.evaluation.research_r5_skid_binding_v6 import STATE_SKID_CONTEXT
from tehm.rtl.rtl_actions import RTL_ACTION_DOMAINS, apply_rtl_action
from tehm.rtl.skid_payload_action_v6 import DOMAIN, PROFILE, payload_from_source_v6

ROOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot")
FAULT = ROOT / "dev/skillsurf-drain-v6-r1/skid_buffer_drain_fault.sv"
CAPTURE_FAULT = ROOT / "qualification/skillsurf-skid-screen-r1/skid_buffer_fault.sv"
CLEAN = Path("/data1/zhangdy/RTL/RTL_testbench/SkillSurf/systemverilog/rtl/skid_buffer.sv")


def _reject(fn) -> bool:
    try:
        fn()
    except (ValueError, TypeError, KeyError):
        return True
    return False


def check() -> dict:
    checked = {case: train.verify_acquisition(train.acquisition(case, "treatment"))
               for case in ("axis_register", "zipcpu_skidbuffer")}
    sources = {case: train._source(row) for case, row in checked.items()}
    contexts = {case: row["public_context"] for case, row in checked.items()}
    if any(path.is_symlink() or not path.is_file() for path in (FAULT, CLEAN, CAPTURE_FAULT)):
        raise ValueError("v6 DEV input missing or symlinked")
    dev = FAULT.read_text(encoding="utf-8")
    if train._sha(FAULT.read_bytes()) != (
            "sha256:5ec912df2f54f3e4b95fe35d3c4338a6440312cb3c1907491af93ad9f8c908a3"):
        raise ValueError("v6 DEV source identity drift")
    sources["skillsurf_drain_observed_dev"] = dev
    contexts["skillsurf_drain_observed_dev"] = STATE_SKID_CONTEXT
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-v6-ram-binding-negative-only",
        transformation_family="skid_payload_restore_shadow_v6",
        action_payload_template=payload_from_source_v6(
            sources["axis_register"], contexts["axis_register"]),
        compatibility_profile=PROFILE,
        verifier_obligations=("TRAIN target", "TRAIN preservation"),
        creator="researcher_assisted_ram_conformance_not_authority")
    proposal = with_skid_payload_binding_v6(
        proposal, sources["axis_register"], contexts["axis_register"])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registration = register_asset_proposal(conn, proposal)
        asset = get_asset(conn, registration.asset_id)
        if asset is None:
            raise ValueError("RAM-only v6 Asset registration missing")
        cases = {
            "domain_catalog_v6": DOMAIN in RTL_ACTION_DOMAINS,
            "draft_only": registration.status == "draft" and
                get_asset_status(conn, asset_id=registration.asset_id,
                                 target_scope=registration.target_scope)["status"] == "draft",
            "source_contract_v6": source_contract(asset) == CONTRACT and is_skid_v6_asset(asset),
            "empty_memory": db.count_rows(conn, "tehm_mechanism_knowledge") == 0,
        }
        bounds, validations = [], []
        for name, source in sources.items():
            context = contexts[name]
            bound = bind_rtl_asset_to_source(
                asset, source, design_id=name, public_context=context)
            payload = bound["definition"]["action"]["payload"]
            edited, action = apply_rtl_action(source, payload)
            validation = validate_rtl_rewrite_asset(bound, source)
            forged = copy.deepcopy(bound)
            forged["provenance"]["binding_evidence"]["source"] += "\n"
            bad_template = copy.deepcopy(asset)
            bad_template["definition"]["binding_template"]["spec_digest"] = "sha256:bad"
            cases.update({
                name + "_source_copy": verify_source_copy(bound, asset),
                name + "_one_edit": edited != source and action["rewritten"] == 1 and
                    action["domain"] == DOMAIN and action["source_binding_rederived"] is True,
                name + "_static_only": validation.status == "SHADOW_STATIC_PASS" and
                    validation.independent_verifier is False,
                name + "_forged_copy_rejected": not verify_source_copy(forged, asset),
                name + "_template_tamper_rejected": _reject(lambda:
                    bind_rtl_asset_to_source(bad_template, source,
                        design_id=name, public_context=context)),
                name + "_missing_context_rejected": _reject(lambda:
                    bind_rtl_asset_to_source(asset, source, design_id=name)),
                name + "_stale_source_rejected": _reject(lambda:
                    apply_rtl_action(source + "\n", payload)),
                name + "_proofless_candidate_rejected": not verify_candidate_source_replay(
                    type("Candidate", (), {"provenance": {},
                         "concrete_action": {"domain": DOMAIN}})(), source),
            })
            if name == "skillsurf_drain_observed_dev":
                cases["dev_candidate_matches_clean"] = edited.encode() == CLEAN.read_bytes()
            bounds.append(bound)
            validations.append(validation.to_dict())
        cases["capture_side_not_bound"] = _reject(lambda: bind_rtl_asset_to_source(
            asset, CAPTURE_FAULT.read_text(encoding="utf-8"),
            design_id="capture_observed_dev", public_context=STATE_SKID_CONTEXT))
        synthetic = [{**row, "independent_verifier": True,
                      "oracle_verdict": "PASS", "regression_verdict": "PASS",
                      "errors": []} for row in validations]
        gate = evaluate_asset_authority(
            asset, validation_receipts=synthetic, bindings=bounds,
            rollback_receipt={"verified": True, "version": "forged"},
            target_scope=PROFILE, min_lineages=2)
        cases["v6_raw_train_gate_closed"] = (
            not gate.eligible and
            gate.checks["cross_lineage_verified"] is False and
            gate.checks["rollback_verified"] is False and
            gate.evidence["lineage_gate_reason"] == "v6_train_bundle_not_yet_audited")
        forged_rows = [
            {"receipt": receipt, "split": "training", "lineage_id": name,
             "source_id": name}
            for name, receipt in zip(sources, synthetic)]
        forged_bindings = [
            {"asset": bound, "split": "training", "lineage_id": name,
             "source_id": name}
            for name, bound in zip(sources, bounds)]
        strict = record_asset_authority(
            conn, asset_id=registration.asset_id, target_scope=PROFILE,
            validation_receipts=forged_rows, bindings=forged_bindings,
            rollback_receipt={"receipt": {"verified": True, "version": "forged"},
                              "split": "ab", "source_id": "forged-rollback"},
            min_lineages=2)
        cold = verify_asset_authority(conn, strict)
        cases["forged_raw_authority_ineligible"] = not strict.eligible and not cold["eligible"]
        set_asset_status(conn, asset_id=registration.asset_id,
                         target_scope=registration.target_scope, status="shadow")
        set_asset_status(conn, asset_id=registration.asset_id,
                         target_scope=registration.target_scope, status="candidate")
        cases["legacy_promotion_blocked"] = _reject(lambda: set_asset_status(
            conn, asset_id=registration.asset_id, target_scope=registration.target_scope,
            status="promoted", gates={name: True for name in gate.checks}))
        cases["strict_without_receipt_blocked"] = _reject(lambda: set_asset_status(
            conn, asset_id=registration.asset_id, target_scope=registration.target_scope,
            status="promoted", strict_asset_authority=True))
        cases["strict_forged_receipt_blocked"] = _reject(lambda: set_asset_status(
            conn, asset_id=registration.asset_id, target_scope=registration.target_scope,
            status="promoted", strict_asset_authority=True,
            authority_receipt=strict))
        cases["candidate_status_unchanged"] = get_asset_status(
            conn, asset_id=registration.asset_id,
            target_scope=registration.target_scope)["status"] == "candidate"
        return {"valid": all(cases.values()), "case_count": len(cases),
                "failed": sorted(name for name, ok in cases.items() if not ok),
                "cases": cases, "role": "RAM_ONLY_V6_DEV_NOT_TRAIN_OR_HELDOUT",
                "schema": "tehm-r5-v6-ram-asset-core-conformance-v2",
                "gate_missing": list(gate.missing), "memory_persistent": False}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = check()
    if args.output is not None:
        if (args.output.parent != ROOT / "dev/skillsurf-drain-v6-r1" or
                args.output.exists() or args.output.is_symlink()):
            raise ValueError("v6 RAM receipt must be fresh inside DEV")
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    elif json.loads(args.verify.read_bytes()) != result:
        raise ValueError("v6 RAM receipt cold replay drift")
    print(json.dumps({"valid": result["valid"], "case_count": result["case_count"],
                      "failed": result["failed"], "gate_missing": result["gate_missing"]},
                     sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
