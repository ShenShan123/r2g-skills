"""Conformance and candidate emission for R5 DEV-only source binder v4.

The test input is the already-observed staged faulty RTL plus public knobs.
The two TRAIN checks cold-replay their evaluator receipts. The target's
private oracle, clean source, and mutation metadata are never binder inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from tehm.adapters.research_r5_rtl_scoped import CASES, acquisition, verify_acquisition
from tehm.evaluation.research_r5_skid_binding_v4 import (
    FETCH_CONTEXT, TEMPLATE, apply_bound_skid_payload_v4, bind_skid_payload_v4,
)


ROOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot")
INPUT = (ROOT / "transfer/ue-riscv-fetch-pilot-r1/agent-inputs/task-001/rtl/riscv_fetch.v")
DEV = ROOT / "dev"
EXPECTED_INPUT_SHA = "sha256:33a80886e1c776cc67395e9128940a5200624990c9316c7fbc8469be06aae2ec"
ASSET = {"binding_template": TEMPLATE}


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _replace(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError("v4 conformance mutation site is not unique")
    return source.replace(before, after, 1)


def _reject_action(source: str, context: dict, binding: dict) -> bool:
    try:
        apply_bound_skid_payload_v4(ASSET, source, context, binding)
    except (ValueError, TypeError, KeyError):
        return True
    return False


def check() -> tuple[dict, str]:
    raw = INPUT.read_bytes()
    if _sha(raw) != EXPECTED_INPUT_SHA or INPUT.is_symlink():
        raise ValueError("DEV staged source identity drift")
    source = raw.decode("utf-8")
    binding = bind_skid_payload_v4(ASSET, source, FETCH_CONTEXT)
    if binding.get("status") != "BOUND":
        raise ValueError("DEV fetch source was not uniquely bound")
    candidate, action = apply_bound_skid_payload_v4(
        ASSET, source, FETCH_CONTEXT, binding)
    cases = {
        "dev_fetch_unique_bound": binding["reason"] ==
            "unique_fetch_buffered_payload_source_mismatch",
        "dev_candidate_one_edit": action["rewritten"] == 1 and candidate != source,
        "dev_candidate_becomes_healthy": bind_skid_payload_v4(
            ASSET, candidate, FETCH_CONTEXT)["reason"] ==
            "payload_source_already_buffered_slice",
        "dev_candidate_does_not_reapply": _reject_action(candidate, FETCH_CONTEXT, binding),
        "wrong_template_rejected": bind_skid_payload_v4(
            {"binding_template": {}}, source, FETCH_CONTEXT)["status"] == "UNSUPPORTED",
        "wrong_public_parameter_rejected": bind_skid_payload_v4(
            ASSET, source, {"SUPPORT_MMU": 0})["status"] == "UNSUPPORTED",
        "extra_public_parameter_rejected": bind_skid_payload_v4(
            ASSET, source, {"SUPPORT_MMU": 1, "hidden": 1})["status"] == "UNSUPPORTED",
        "module_mismatch_rejected": bind_skid_payload_v4(
            ASSET, _replace(source, "module riscv_fetch", "module other_fetch"),
            FETCH_CONTEXT)["status"] == "NO_MATCH",
        "buffer_width_change_rejected": bind_skid_payload_v4(
            ASSET, _replace(source, "reg [65:0]  skid_buffer_q;",
                            "reg [64:0]  skid_buffer_q;"), FETCH_CONTEXT)["status"] == "UNSUPPORTED",
        "capture_source_change_rejected": bind_skid_payload_v4(
            ASSET, _replace(source, "fetch_pc_o, fetch_instr_o};",
                            "fetch_pc_o, icache_inst_i};"), FETCH_CONTEXT)["status"] == "UNSUPPORTED",
        "backpressure_guard_change_rejected": bind_skid_payload_v4(
            ASSET, _replace(source, "fetch_valid_o && !fetch_accept_i",
                            "fetch_valid_o && fetch_accept_i"), FETCH_CONTEXT)["status"] == "UNSUPPORTED",
        "unrecognized_rhs_rejected": bind_skid_payload_v4(
            ASSET, _replace(source, "skid_valid_q ? icache_inst_i          : icache_inst_i;",
                            "skid_valid_q ? other_inst_i           : icache_inst_i;"),
            FETCH_CONTEXT)["status"] == "UNSUPPORTED",
        "duplicate_output_rejected": bind_skid_payload_v4(
            ASSET, source.replace("\n\nendmodule", "\nassign fetch_instr_o = icache_inst_i;\n\nendmodule"),
            FETCH_CONTEXT)["status"] == "AMBIGUOUS",
        "unexpected_directive_rejected": bind_skid_payload_v4(
            ASSET, _replace(source, "module riscv_fetch",
                            "`define PRIVATE_ALIAS skid_buffer_q\nmodule riscv_fetch"),
            FETCH_CONTEXT)["status"] == "UNSUPPORTED",
        "extra_buffer_write_rejected": bind_skid_payload_v4(
            ASSET, source.replace("\n\nendmodule",
                "\nskid_buffer_q <= 66'b0;\n\nendmodule"),
            FETCH_CONTEXT)["status"] == "UNSUPPORTED",
        "stale_source_rejected": _reject_action(source + "\n// changed\n", FETCH_CONTEXT, binding),
        "tampered_binding_rejected": _reject_action(source, FETCH_CONTEXT,
            {**binding, "witness_digest": "sha256:tampered"}),
        "wrong_context_action_rejected": _reject_action(source, {"SUPPORT_MMU": 0}, binding),
        "candidate_hash_matches_receipt": _sha(candidate.encode("utf-8")) ==
            action["after_sha256"],
    }
    with patch("builtins.open", side_effect=AssertionError("file access")):
        with patch("pathlib.Path.read_text", side_effect=AssertionError("file access")):
            with patch("pathlib.Path.read_bytes", side_effect=AssertionError("file access")):
                try:
                    again = bind_skid_payload_v4(ASSET, source, FETCH_CONTEXT)
                    edited_again, _ = apply_bound_skid_payload_v4(
                        ASSET, source, FETCH_CONTEXT, again)
                    cases["binder_no_file_access"] = (
                        again == binding and edited_again == candidate)
                except AssertionError:
                    cases["binder_no_file_access"] = False

    train = {}
    for case in sorted(CASES):
        checked = verify_acquisition(acquisition(case, "treatment"))
        path = (Path(checked["work"]) / "agent-inputs" /
                (checked["source_file"] if case == "axis_register" else "skidbuffer.v"))
        training_source = path.read_text(encoding="utf-8")
        training_binding = bind_skid_payload_v4(
            ASSET, training_source, checked["public_context"])
        if training_binding.get("status") != "BOUND":
            raise ValueError("v4 rejected frozen TRAIN source: " + case)
        training_candidate, training_action = apply_bound_skid_payload_v4(
            ASSET, training_source, checked["public_context"], training_binding)
        train[case] = {
            "train_receipt_digest": checked["train_receipt_digest"],
            "candidate_sha256": _sha(training_candidate.encode("utf-8")),
            "expected_candidate_sha256": checked["after_source_sha256"],
            "bound_reason": training_binding["reason"],
        }
        cases["train_" + case + "_candidate_replayed"] = (
            train[case]["candidate_sha256"] == train[case]["expected_candidate_sha256"] and
            training_action["rewritten"] == 1)
    report = {
        "schema": "tehm-r5-skid-v4-dev-conformance-v2",
        "role": "DEV_OBSERVED_NOT_HELDOUT_NOT_MEMORY_TRANSFER",
        "valid": all(cases.values()), "case_count": len(cases),
        "failed": sorted(name for name, ok in cases.items() if not ok),
        "cases": cases, "source_sha256": _sha(raw),
        "candidate_sha256": action["after_sha256"],
        "binding": binding, "action": action, "train_replays": train,
        "target_private_oracle_read": False, "model_calls": 0,
        "memory_m_plus_constructed": False,
    }
    return report, candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target = args.output.resolve(strict=False)
    if target.parent != DEV or target.exists() or target.is_symlink():
        raise ValueError("v4 DEV output must be a new direct child of pilot dev")
    report, candidate = check()
    if not report["valid"]:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    target.mkdir()
    (target / "candidate.v").write_text(candidate, encoding="utf-8")
    (target / "source-only-check.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"valid": True, "case_count": report["case_count"],
                      "candidate_sha256": report["candidate_sha256"],
                      "output": str(target)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
