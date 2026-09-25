"""Source-only conformance checks for observed DEV axis_skid and v4 delegation.

The observed fault, clean file, and qualification receipt are DEV test inputs.
The v5 binder itself is given only supplied faulty source and public context.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from tehm.adapters.research_r5_rtl_scoped import CASES, acquisition, verify_acquisition
from tehm.evaluation.research_r5_skid_binding_v4 import (
    FETCH_CONTEXT, TEMPLATE as V4_TEMPLATE, bind_skid_payload_v4,
)
from tehm.evaluation.research_r5_skid_binding_v5 import (
    AXIS_SKID_CONTEXT, TEMPLATE, apply_bound_skid_payload_v5,
    bind_skid_payload_v5,
)


PILOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot")
FAULT = PILOT / "qualification/tick2trade-axis-skid-fault-r1/rtl/axis_skid.sv"
CLEAN = Path("/data1/zhangdy/RTL/RTL_testbench/namangoyal-work/fpga-tick-to-trade/rtl/axis_skid.sv")
QUAL = PILOT / "qualification/tick2trade-axis-skid-qualification-r1.json"
FETCH = PILOT / "transfer/ue-riscv-fetch-pilot-r1/agent-inputs/task-001/rtl/riscv_fetch.v"
DEV = PILOT / "dev"
FAULT_SHA = "sha256:caae468c6ed9f94e45958f7033d0abee3fa5f576d0279d61de1a0a2bf384c3db"
CLEAN_SHA = "sha256:652723dccd678e33d23547086b3efae819a9060ff4897b1391331beaca5e7253"
FETCH_SHA = "sha256:33a80886e1c776cc67395e9128940a5200624990c9316c7fbc8469be06aae2ec"
ASSET = {"binding_template": TEMPLATE}


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def replace_once(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError("v5 DEV test mutation anchor is not unique")
    return source.replace(before, after, 1)


def status(source: str, context: dict | None = None) -> str:
    return bind_skid_payload_v5(
        ASSET, source, AXIS_SKID_CONTEXT if context is None else context)["status"]


def reason(source: str, context: dict | None = None) -> str:
    return bind_skid_payload_v5(
        ASSET, source, AXIS_SKID_CONTEXT if context is None else context)["reason"]


def reject_action(source: str, context: dict, binding: dict) -> bool:
    try:
        apply_bound_skid_payload_v5(ASSET, source, context, binding)
    except (ValueError, TypeError, KeyError):
        return True
    return False


def check() -> tuple[dict, str]:
    fault_raw, clean_raw = FAULT.read_bytes(), CLEAN.read_bytes()
    if (FAULT.is_symlink() or CLEAN.is_symlink() or
            sha(fault_raw) != FAULT_SHA or sha(clean_raw) != CLEAN_SHA):
        raise ValueError("observed DEV source identity drift")
    qualification = json.loads(QUAL.read_bytes())
    if (not qualification.get("valid") or
            qualification.get("role") != "QUALIFICATION_DEV_OBSERVED_NOT_TRANSFER" or
            qualification.get("fault_rtl_sha256") != FAULT_SHA or
            qualification.get("clean_rtl_sha256") != CLEAN_SHA or
            qualification.get("scoped_adapter", {}).get("fault_target_verdict") != "FAIL" or
            qualification.get("scoped_adapter", {}).get("fault_preservation_verdict") != "PASS"):
        raise ValueError("observed DEV qualification receipt drift")
    source = fault_raw.decode("utf-8")
    binding = bind_skid_payload_v5(ASSET, source, AXIS_SKID_CONTEXT)
    if binding.get("status") != "BOUND":
        raise ValueError("observed DEV source was not uniquely bound")
    candidate, action = apply_bound_skid_payload_v5(
        ASSET, source, AXIS_SKID_CONTEXT, binding)
    witness = binding["witness"]
    start, end = witness["rhs_span"]
    cases = {
        "unique_occupied_slot_bound": binding["reason"] ==
            "unique_occupied_skid_slot_payload_mismatch",
        "exact_one_source_edit": action["rewritten"] == 1 and
            candidate == source[:start] + "skid_data" + source[end:] and
            source[start:end].strip() == "s_tdata",
        "candidate_matches_observed_clean_sha": sha(candidate.encode()) == CLEAN_SHA and
            candidate == clean_raw.decode("utf-8"),
        "candidate_becomes_healthy_no_match": reason(candidate) ==
            "payload_source_already_buffered_register",
        "idempotent_no_second_action": reject_action(candidate, AXIS_SKID_CONTEXT, binding),
        "wrong_template_rejected": bind_skid_payload_v5(
            {"binding_template": V4_TEMPLATE}, source, AXIS_SKID_CONTEXT)["status"] ==
            "UNSUPPORTED",
        "wrong_width_parameter_rejected": status(
            source, {"DATA_WIDTH": 16, "SKID_SLOTS": 1}) == "UNSUPPORTED",
        "extra_public_parameter_rejected": status(
            source, {"DATA_WIDTH": 8, "SKID_SLOTS": 1, "gold": 1}) == "UNSUPPORTED",
        "other_module_no_match": status(replace_once(
            source, "module axis_skid", "module unrelated_fifo")) == "NO_MATCH",
        "second_module_no_match": status(source + "\nmodule unrelated; endmodule\n") == "NO_MATCH",
        "data_width_change_unsupported": status(replace_once(
            source, "input  wire  [7:0] s_tdata", "input wire [15:0] s_tdata")) ==
            "UNSUPPORTED",
        "buffer_width_change_unsupported": status(replace_once(
            source, "logic [7:0] skid_data;", "logic [6:0] skid_data;")) ==
            "UNSUPPORTED",
        "ready_decode_change_unsupported": status(replace_once(
            source, "assign s_tready = (state == EMPTY);",
            "assign s_tready = (state == FULL);")) == "UNSUPPORTED",
        "output_ready_change_unsupported": status(replace_once(
            source, "wire out_ready = !m_tvalid || m_beat;",
            "wire out_ready = !m_tvalid && m_beat;")) == "UNSUPPORTED",
        "full_slot_guard_change_rejected": status(replace_once(
            source, "if (state == FULL) begin", "if (state == EMPTY) begin")) != "BOUND",
        "normal_flow_change_unsupported": status(replace_once(
            source, "m_tlast  <= s_tlast;", "m_tlast <= skid_last;")) ==
            "UNSUPPORTED",
        "capture_source_change_unsupported": status(replace_once(
            source, "skid_data <= s_tdata;", "skid_data <= m_tdata;")) ==
            "UNSUPPORTED",
        "full_slot_metadata_change_unsupported": status(replace_once(
            source, "m_tlast  <= skid_last;", "m_tlast <= s_tlast;")) ==
            "UNSUPPORTED",
        "unrecognized_payload_rhs_unsupported": status(replace_once(
            source, "m_tdata  <= s_tdata;\n                    m_tlast  <= skid_last;",
            "m_tdata <= other_data;\n                    m_tlast  <= skid_last;")) ==
            "UNSUPPORTED",
        "duplicate_full_slot_branch_ambiguous": status(replace_once(
            source, "if (state == FULL) begin", "if (state == FULL) begin\n"
            "                    if (state == FULL) begin\n"
            "                    m_tdata <= s_tdata;\n                    end")) == "AMBIGUOUS",
        "extra_output_write_ambiguous": status(replace_once(
            source, "m_tdata  <= s_tdata;\n                    m_tlast  <= skid_last;",
            "m_tdata <= s_tdata;\n                    m_tdata <= s_tdata;\n"
            "                    m_tlast  <= skid_last;")) == "AMBIGUOUS",
        "extra_always_block_rejected": status(source.replace(
            "\n`ifdef FORMAL", "\nalways_comb begin\n  other = 1'b0;\nend\n"
            "`ifdef FORMAL")) ==
            "UNSUPPORTED",
        "unexpected_directive_rejected": status(replace_once(
            source, "module axis_skid", "`define HIDDEN m_tdata\nmodule axis_skid")) ==
            "UNSUPPORTED",
        "comment_decoy_ignored": status(source + "\n// m_tdata <= skid_data;\n") ==
            "BOUND",
        "source_not_mutated_by_binding": FAULT.read_bytes() == fault_raw,
        "stale_source_action_rejected": reject_action(
            source + "\n// modified\n", AXIS_SKID_CONTEXT, binding),
        "tampered_binding_action_rejected": reject_action(
            source, AXIS_SKID_CONTEXT,
            {**binding, "witness_digest": "sha256:" + "0" * 64}),
        "wrong_context_action_rejected": reject_action(
            source, {"DATA_WIDTH": 16, "SKID_SLOTS": 1}, binding),
    }
    with patch("builtins.open", side_effect=AssertionError("file access")):
        with patch("pathlib.Path.read_text", side_effect=AssertionError("file access")):
            with patch("pathlib.Path.read_bytes", side_effect=AssertionError("file access")):
                with patch("socket.socket", side_effect=AssertionError("network access")):
                    try:
                        again = bind_skid_payload_v5(ASSET, source, AXIS_SKID_CONTEXT)
                        regenerated, _ = apply_bound_skid_payload_v5(
                            ASSET, source, AXIS_SKID_CONTEXT, again)
                        cases["binder_action_no_file_or_network_access"] = (
                            again == binding and regenerated == candidate)
                    except AssertionError:
                        cases["binder_action_no_file_or_network_access"] = False
    train = {}
    for case in sorted(CASES):
        checked = verify_acquisition(acquisition(case, "treatment"))
        source_path = (Path(checked["work"]) / "agent-inputs" /
                       (checked["source_file"] if case == "axis_register" else "skidbuffer.v"))
        training_source = source_path.read_text(encoding="utf-8")
        old = bind_skid_payload_v4(
            {"binding_template": V4_TEMPLATE}, training_source,
            checked["public_context"])
        new = bind_skid_payload_v5(ASSET, training_source, checked["public_context"])
        if old["status"] != "BOUND" or new["status"] != "BOUND":
            raise ValueError("v5 lost a v4 TRAIN binding: " + case)
        rewritten, receipt = apply_bound_skid_payload_v5(
            ASSET, training_source, checked["public_context"], new)
        train[case] = {
            "source_sha256": sha(training_source.encode()),
            "candidate_sha256": sha(rewritten.encode()),
            "expected_candidate_sha256": checked["after_source_sha256"],
            "train_receipt_digest": checked["train_receipt_digest"],
        }
        cases["train_" + case + "_v4_delegate_replays"] = (
            train[case]["candidate_sha256"] == checked["after_source_sha256"] and
            receipt["rewritten"] == 1 and
            old["witness"]["rhs_span"] == new["witness"]["rhs_span"])
    fetch_bytes = FETCH.read_bytes()
    if FETCH.is_symlink() or sha(fetch_bytes) != FETCH_SHA:
        raise ValueError("observed fetch DEV source drift")
    fetch_source = fetch_bytes.decode("utf-8")
    fetch_binding = bind_skid_payload_v5(ASSET, fetch_source, FETCH_CONTEXT)
    if fetch_binding["status"] != "BOUND":
        raise ValueError("v5 lost observed fetch v4 binding")
    fetch_candidate, _ = apply_bound_skid_payload_v5(
        ASSET, fetch_source, FETCH_CONTEXT, fetch_binding)
    cases["observed_fetch_v4_delegate_replays"] = (
        sha(fetch_candidate.encode()) ==
        "sha256:aadb484ee05359d72e874e30e2656350a31e56c7ccae03bffa1c94d1a2aa76b5")
    result = {
        "schema": "tehm-r5-skid-v5-dev-source-only-conformance-v1",
        "role": "DEV_OBSERVED_NOT_HELDOUT_NOT_MEMORY_TRANSFER",
        "valid": all(cases.values()),
        "case_count": len(cases),
        "failed": sorted(name for name, passed in cases.items() if not passed),
        "cases": cases,
        "source_sha256": FAULT_SHA,
        "candidate_sha256": sha(candidate.encode()),
        "binding": binding,
        "action": action,
        "train_replays": train,
        "target_private_oracle_read_by_binder": False,
        "target_native_oracle_executed": False,
        "memory_m_plus_v5_constructed": False,
        "model_calls": 0,
    }
    return result, candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    report, candidate = check()
    if not report["valid"]:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    if args.output is not None:
        output = args.output
        if output.parent != DEV or output.exists() or output.is_symlink():
            raise ValueError("v5 DEV output must be a fresh direct child of pilot dev")
        output.mkdir()
        (output / "candidate.sv").write_text(candidate, encoding="utf-8")
        (output / "source-only-check.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        if ((args.verify / "candidate.sv").read_text(encoding="utf-8") != candidate or
                json.loads((args.verify / "source-only-check.json").read_bytes()) != report):
            raise ValueError("v5 DEV source-only receipt drift")
    print(json.dumps({"valid": report["valid"], "case_count": report["case_count"],
                      "candidate_sha256": report["candidate_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
