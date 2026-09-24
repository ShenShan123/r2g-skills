"""RAM-only conformance checks for the R5 shadow skid Asset wiring.

This deliberately supplies no TRAIN Knowledge, lifecycle promotion or oracle
receipt. Passing means only static/core interface integrity on a DEV fixture.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from tehm import db
from tehm.assets.registry import get_asset, get_asset_status
from tehm.assets.skid_binding import with_skid_payload_binding
from tehm.assets.source_selection import (
    source_contract, source_runtime_binding, verify_candidate_source_replay,
    verify_source_copy,
)
from tehm.assets.structural_binding import bind_rtl_asset_to_source
from tehm.assets.synthesis import build_rtl_asset_proposal, register_asset_proposal
from tehm.assets.validation import validate_rtl_rewrite_asset
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.skid_payload_action import DOMAIN, PROFILE, payload_from_source
from tehm.rtl.verilog_parse import parse_verilog


def _reject(call) -> bool:
    try:
        call()
    except (ValueError, TypeError):
        return True
    return False


def check(fault_path: Path, clean_path: Path, unrelated_path: Path) -> dict:
    source = fault_path.read_text(encoding="utf-8")
    clean = clean_path.read_text(encoding="utf-8")
    unrelated = unrelated_path.read_text(encoding="utf-8")
    payload = payload_from_source(source)
    fixed, edit = apply_rtl_action(source, payload)
    proposal = build_rtl_asset_proposal(
        {}, name="r5-skid-dev-conformance-only",
        transformation_family="skid_payload_restore_shadow",
        action_payload_template=payload, compatibility_profile=PROFILE,
        verifier_obligations=("DEV native target", "DEV non-target preservation"),
        creator="researcher_assisted_dev_contract_fixture")
    proposal = with_skid_payload_binding(proposal, source)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        db.ensure_schema(conn)
        registration = register_asset_proposal(conn, proposal)
        registered = get_asset(conn, registration.asset_id)
        bound = bind_rtl_asset_to_source(
            registered, source, design_id="DEV_axis_register")
        status = get_asset_status(
            conn, asset_id=registration.asset_id,
            target_scope=registration.target_scope)
        validation = validate_rtl_rewrite_asset(registered, source)
        changed_payload = copy.deepcopy(payload)
        changed_payload["witness_digest"] = "sha256:" + "0" * 64
        extra_payload = {**payload, "gold_path": "/forbidden"}
        altered_bound = copy.deepcopy(bound)
        altered_bound["definition"]["action"]["payload"]["module"] = "forged"
        altered_template = copy.deepcopy(registered)
        altered_template["definition"]["binding_template"]["spec"]["proof_scope"] = "forged"
        cases = {
            "registered_draft_only": registration.status == "draft" and
                status["status"] == "draft",
            "registered_contract_recognized": source_contract(registered) ==
                "rtl_skid_payload_source_binding_shadow_v1",
            "registered_copy_replays": verify_source_copy(bound, registered),
            "bound_payload_exact": bound["definition"]["action"]["payload"] == payload,
            "core_action_one_edit": fixed != source and edit["rewritten"] == 1 and
                edit["domain"] == DOMAIN and edit["source_binding_rederived"] is True,
            "dev_clean_identity_posthoc": fixed == clean,
            "core_parser_before_after": len(parse_verilog(source)) == 1 and
                len(parse_verilog(fixed)) == 1,
            "static_validation_not_oracle": validation.static_valid is True and
                validation.independent_verifier is False and
                validation.oracle_verdict is None,
            "tampered_payload_rejected": _reject(
                lambda: apply_rtl_action(source, changed_payload)),
            "extra_answer_field_rejected": _reject(
                lambda: apply_rtl_action(source, extra_payload)),
            "healthy_source_binding_rejected": _reject(
                lambda: bind_rtl_asset_to_source(
                    registered, clean, design_id="DEV_healthy_control")),
            "unrelated_source_binding_rejected": _reject(
                lambda: bind_rtl_asset_to_source(
                    registered, unrelated, design_id="DEV_unrelated_control")),
            "healthy_source_no_action": _reject(
                lambda: apply_rtl_action(clean, payload)),
            "unrelated_source_no_action": _reject(
                lambda: apply_rtl_action(unrelated, payload)),
            "tampered_bound_copy_rejected": not verify_source_copy(
                altered_bound, registered),
            "tampered_template_rejected": _reject(
                lambda: bind_rtl_asset_to_source(
                    altered_template, source, design_id="DEV_axis_register")),
            "no_knowledge_no_runtime_binding": _reject(
                lambda: source_runtime_binding(bound, "nonexistent@1")),
            "no_source_replay_no_candidate": not verify_candidate_source_replay(
                SimpleNamespace(provenance={}, concrete_action={"domain": DOMAIN}), source),
            "source_unchanged": source == fault_path.read_text(encoding="utf-8"),
        }
        return {"valid": all(cases.values()), "case_count": len(cases),
                "cases": cases, "failed": sorted(k for k, ok in cases.items() if not ok),
                "asset_status": status["status"],
                "static_validation_status": validation.status,
                "memory_knowledge_count": db.count_rows(conn, "tehm_mechanism_knowledge")}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fault", type=Path, required=True)
    parser.add_argument("--clean", type=Path, required=True)
    parser.add_argument("--unrelated", type=Path, required=True)
    args = parser.parse_args()
    result = check(args.fault, args.clean, args.unrelated)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
