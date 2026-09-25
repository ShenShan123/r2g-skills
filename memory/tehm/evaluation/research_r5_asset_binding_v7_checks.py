"""RAM-only v7 draft Asset conformance; deliberately no authority admission."""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
from pathlib import Path

from tehm import db
from tehm.adapters import research_r5_rtl_scoped_v3 as old_train
from tehm.assets.authority import record_asset_authority, verify_asset_authority
from tehm.assets.lifecycle import evaluate_asset_authority
from tehm.assets.registry import get_asset, get_asset_status, set_asset_status
from tehm.assets.skid_binding_v7 import (
    CONTRACT, is_skid_v7_asset, with_skid_payload_binding_v7,
)
from tehm.assets.source_selection import (
    source_contract, verify_candidate_source_replay, verify_source_copy,
)
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.evaluation.research_r5_skid_binding_v7 import LIBSV_CONTEXT
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.skid_payload_action_v7 import DOMAIN, PROFILE, payload_from_source_v7

ROOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot")
TRAIN = ROOT / "training/skid-v7-r1"


def rejected(fn) -> bool:
    try:
        fn()
    except (ValueError, TypeError, KeyError):
        return True
    return False


def check() -> dict:
    audit = json.loads((TRAIN / "audit-r1.json").read_bytes())
    if audit.get("raw_six_arm_valid") is not True:
        raise ValueError("LibSV TRAIN raw audit is not valid")
    libsv_root = TRAIN / "fresh-r1/evaluator-inputs/source"
    sources = {"libsv_train": (libsv_root / "fault/skid_buffer.sv").read_text()}
    contexts = {"libsv_train": LIBSV_CONTEXT}
    expected_libsv = (libsv_root / "clean/skid_buffer.sv").read_text()
    for case in ("axis_register", "zipcpu_skidbuffer"):
        row = old_train.verify_acquisition(old_train.acquisition(case, "treatment"))
        sources[case] = old_train._source(row)
        contexts[case] = row["public_context"]
    first = sources["axis_register"]
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-v7-ram-draft-not-authority",
        transformation_family="skid_payload_restore_shadow_v7",
        action_payload_template=payload_from_source_v7(first, contexts["axis_register"]),
        compatibility_profile=PROFILE,
        verifier_obligations=("TRAIN target", "TRAIN preservation"),
        creator="researcher_assisted_train_v7_ram_draft")
    proposal = with_skid_payload_binding_v7(proposal, first, contexts["axis_register"])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registered = register_asset_proposal(conn, proposal)
        asset = get_asset(conn, registered.asset_id)
        if asset is None:
            raise ValueError("RAM draft registration missing")
        cases = {
            "draft_only": registered.status == "draft" and
                get_asset_status(conn, asset_id=registered.asset_id,
                                 target_scope=registered.target_scope)["status"] == "draft",
            "source_contract_v7": source_contract(asset) == CONTRACT and is_skid_v7_asset(asset),
            "empty_knowledge_memory": db.count_rows(conn, "tehm_mechanism_knowledge") == 0,
        }
        bounds = []
        synthetic = []
        for name, source in sources.items():
            context = contexts[name]
            bound = bind_rtl_asset_to_source(
                asset, source, design_id=name, public_context=context)
            payload = bound["definition"]["action"]["payload"]
            edited, action = apply_rtl_action(source, payload)
            static = validate_rtl_rewrite_asset(bound, source)
            cases[name + ":source_copy"] = verify_source_copy(bound, asset)
            cases[name + ":one_edit"] = (
                edited != source and action.get("rewritten") == 1 and
                action.get("source_binding_rederived") is True)
            cases[name + ":static_only"] = (
                static.status == "SHADOW_STATIC_PASS" and
                static.independent_verifier is False)
            cases[name + ":stale_payload_rejected"] = rejected(lambda:
                apply_rtl_action(source + "\n// stale\n", payload))
            forged = copy.deepcopy(bound)
            forged["provenance"]["binding_evidence"]["source"] += "\n"
            cases[name + ":forged_copy_rejected"] = not verify_source_copy(forged, asset)
            cases[name + ":proofless_candidate_rejected"] = not verify_candidate_source_replay(
                type("Candidate", (), {"provenance": {},
                     "concrete_action": {"domain": DOMAIN}})(), source)
            if name == "libsv_train":
                cases["libsv_train_candidate_matches_qualified_clean"] = edited == expected_libsv
            bounds.append(bound)
            synthetic.append({**static.to_dict(), "independent_verifier": True,
                              "oracle_verdict": "PASS", "regression_verdict": "PASS",
                              "errors": []})
        bad_template = copy.deepcopy(asset)
        bad_template["definition"]["binding_template"]["spec_digest"] = "sha256:forged"
        cases["template_tamper_rejected"] = rejected(lambda: bind_rtl_asset_to_source(
            bad_template, sources["libsv_train"], design_id="libsv_train",
            public_context=LIBSV_CONTEXT))
        cases["extra_public_field_rejected"] = rejected(lambda: bind_rtl_asset_to_source(
            asset, sources["libsv_train"], design_id="libsv_train",
            public_context={"DATA_WIDTH": 32, "oracle": "leak"}))
        forged_rollback = {"verified": True, "version": "forged-v7-rollback"}
        gate = evaluate_asset_authority(
            asset, validation_receipts=synthetic, bindings=bounds,
            rollback_receipt=forged_rollback, target_scope=PROFILE, min_lineages=2)
        cases["v7_raw_train_gate_closed"] = (
            not gate.eligible and not gate.checks["cross_lineage_verified"] and
            not gate.checks["rollback_verified"] and
            gate.evidence["lineage_gate_reason"] == "audited_v7_train_bundle_missing")
        metadata = {"axis_register": "alexforencich/verilog-axis",
                    "zipcpu_skidbuffer": "ZipCPU/wb2axip",
                    "libsv_train": "bensampson5/libsv"}
        ordered = list(sources)
        strict = record_asset_authority(
            conn, asset_id=registered.asset_id, target_scope=PROFILE,
            validation_receipts=[{"receipt": row, "split": "training",
                                  "lineage_id": metadata[name], "source_id": name}
                                 for name, row in zip(ordered, synthetic)],
            bindings=[{"asset": row, "split": "training",
                       "lineage_id": metadata[name], "source_id": name}
                      for name, row in zip(ordered, bounds)],
            rollback_receipt={"receipt": forged_rollback, "split": "ab",
                              "source_id": "forged-rollback"}, min_lineages=2)
        cold = verify_asset_authority(conn, strict)
        cases["forged_strict_authority_ineligible"] = not strict.eligible and not cold["eligible"]
        set_asset_status(conn, asset_id=registered.asset_id,
                         target_scope=registered.target_scope, status="shadow")
        set_asset_status(conn, asset_id=registered.asset_id,
                         target_scope=registered.target_scope, status="candidate")
        cases["non_strict_promotion_rejected"] = rejected(lambda: set_asset_status(
            conn, asset_id=registered.asset_id, target_scope=registered.target_scope,
            status="promoted", gates={key: True for key in gate.checks}))
        cases["forged_strict_promotion_rejected"] = rejected(lambda: set_asset_status(
            conn, asset_id=registered.asset_id, target_scope=registered.target_scope,
            status="promoted", strict_asset_authority=True, authority_receipt=strict))
        cases["candidate_status_unchanged"] = get_asset_status(
            conn, asset_id=registered.asset_id,
            target_scope=registered.target_scope)["status"] == "candidate"
        return {"valid": all(cases.values()), "case_count": len(cases),
                "failed": sorted(name for name, passed in cases.items() if not passed),
                "cases": cases, "role": "RAM_DRAFT_V7_NO_ASSET_AUTHORITY",
                "asset_id": registered.asset_id, "asset_promoted": False,
                "memory_constructed": False, "heldout_transfer": False,
                "gate_missing": list(gate.missing)}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = check()
    if args.output:
        if args.output.exists() or args.output.parent != TRAIN:
            raise ValueError("v7 RAM draft receipt must be fresh under TRAIN")
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"valid": result["valid"], "case_count": result["case_count"],
                      "failed": result["failed"], "gate_missing": result["gate_missing"]},
                     sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
