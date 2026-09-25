"""Fail-closed DEV conformance for the new state-skid drain branch.

External SkillSurf is already observed DEV material, never a held-out target.
This script has no target oracle access during binding/action checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from unittest.mock import patch

from tehm.adapters.research_r5_rtl_scoped import CASES, acquisition, verify_acquisition
from tehm.evaluation.research_r5_skid_binding_v5 import TEMPLATE as V5_TEMPLATE
from tehm.evaluation.research_r5_skid_binding_v5 import bind_skid_payload_v5
from tehm.evaluation.research_r5_skid_binding_v6 import (
    STATE_SKID_CONTEXT, TEMPLATE, apply_bound_skid_payload_v6,
    bind_skid_payload_v6,
)


PILOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot")
DEV = PILOT / "dev/skillsurf-drain-v6-r1"
FAULT = DEV / "skid_buffer_drain_fault.sv"
CLEAN = Path("/data1/zhangdy/RTL/RTL_testbench/SkillSurf/systemverilog/rtl/skid_buffer.sv")
CAPTURE_FAULT = PILOT / "qualification/skillsurf-skid-screen-r1/skid_buffer_fault.sv"
PROBE = DEV / "cold-r1/receipt.json"
FAULT_SHA = "sha256:5ec912df2f54f3e4b95fe35d3c4338a6440312cb3c1907491af93ad9f8c908a3"
CLEAN_SHA = "sha256:7415ad31787cd599fd90c34fde39167e5d0b308249806bed335e5f723da44403"
CAPTURE_SHA = "sha256:a6ec4656880afd807dfde8051390e8c9d7ff70193018f37987d555f2eb3d76d0"
ASSET = {"binding_template": TEMPLATE}


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def replace_once(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError("v6 DEV mutation anchor not unique")
    return source.replace(before, after, 1)


def result(source: str, context: dict | None = None) -> dict:
    return bind_skid_payload_v6(
        ASSET, source, STATE_SKID_CONTEXT if context is None else context)


def reject_action(source: str, context: dict, binding: dict) -> bool:
    try:
        apply_bound_skid_payload_v6(ASSET, source, context, binding)
    except (ValueError, TypeError, KeyError):
        return True
    return False


def check() -> dict:
    fault_raw, clean_raw, capture_raw = (FAULT.read_bytes(), CLEAN.read_bytes(),
                                          CAPTURE_FAULT.read_bytes())
    if (FAULT.is_symlink() or CLEAN.is_symlink() or CAPTURE_FAULT.is_symlink() or
            sha(fault_raw) != FAULT_SHA or sha(clean_raw) != CLEAN_SHA or
            sha(capture_raw) != CAPTURE_SHA):
        raise ValueError("v6 DEV source identity drift")
    probe = json.loads(PROBE.read_bytes())
    if (probe.get("valid") is not True or
            probe.get("role") != "DEV_OBSERVED_NOT_TRANSFER_NOT_TRAIN" or
            [probe["arms"][key]["verdict"] for key in (
                "clean-target", "fault-target", "clean-preservation",
                "fault-preservation")] != ["PASS", "FAIL", "PASS", "PASS"]):
        raise ValueError("v6 DEV native probe drift")
    source = fault_raw.decode()
    binding = result(source)
    if binding.get("status") != "BOUND":
        raise ValueError("qualified DEV drain source did not bind")
    candidate, receipt = apply_bound_skid_payload_v6(
        ASSET, source, STATE_SKID_CONTEXT, binding)
    witness = binding["witness"]
    start, stop = witness["rhs_span"]
    renamed_module = replace_once(source, "module skid_buffer", "module renamed_skid")
    renamed_buffer = re.sub(r"\bbuffer\b", "hold_payload", source)
    if renamed_buffer == source:
        raise ValueError("buffer alpha-renaming fixture did not change")
    renamed_module_binding = result(renamed_module)
    renamed_buffer_binding = result(renamed_buffer)
    cases = {
        "unique_dev_drain_bound": binding["reason"] ==
            "unique_state_machine_buffered_drain_mismatch",
        "one_exact_source_edit": receipt.get("rewritten") == 1 and
            candidate == source[:start] + "buffer" + source[stop:] and
            witness["old_rhs"] == "s_data",
        "candidate_matches_clean": candidate.encode() == clean_raw and
            sha(candidate.encode()) == CLEAN_SHA,
        "candidate_becomes_healthy_no_match": result(candidate)["reason"] ==
            "full_drain_already_uses_buffered_payload",
        "idempotent_no_second_action": reject_action(candidate, STATE_SKID_CONTEXT, binding),
        "capture_side_fault_not_misbound": result(capture_raw.decode())["status"] != "BOUND",
        "wrong_template_rejected": bind_skid_payload_v6(
            {"binding_template": V5_TEMPLATE}, source, STATE_SKID_CONTEXT)["status"] ==
            "UNSUPPORTED",
        "wrong_width_parameter_rejected": result(source, {"WIDTH": 16})["status"] ==
            "UNSUPPORTED",
        "extra_public_parameter_rejected": result(source, {"WIDTH": 8, "gold": 1})["status"] ==
            "UNSUPPORTED",
        "module_alpha_rename_still_bound": renamed_module_binding["status"] == "BOUND" and
            renamed_module_binding["witness"]["module"] == "renamed_skid",
        "buffer_alpha_rename_still_bound": renamed_buffer_binding["status"] == "BOUND" and
            renamed_buffer_binding["witness"]["replacement_rhs"] == "hold_payload",
        "second_module_no_match": result(source + "\nmodule unrelated; endmodule\n")["status"] ==
            "NO_MATCH",
        "unrelated_module_no_match": result("module unrelated; endmodule\n")["status"] ==
            "NO_MATCH",
        "trailing_code_rejected": result(source + "\nwire leaked;\n")["status"] ==
            "UNSUPPORTED",
        "extra_state_write_ambiguous": result(replace_once(
            source, "state_next = state;",
            "state_next = state;\n    state_next = EMPTY;"))["status"] == "AMBIGUOUS",
        "normal_path_change_rejected": result(replace_once(
            source, "PARTIAL: if (m_ready && s_valid)    m_data <= s_data;",
            "PARTIAL: if (m_ready && s_valid)    m_data <= buffer;"))["status"] != "BOUND",
        "capture_source_change_rejected": result(replace_once(
            source, "buffer <= s_data;", "buffer <= m_data;"))["status"] != "BOUND",
        "capture_guard_change_rejected": result(replace_once(
            source, "state == PARTIAL && state_next == FULL",
            "state == EMPTY && state_next == FULL"))["status"] != "BOUND",
        "state_transition_change_rejected": result(replace_once(
            source, "FULL   : if      ( m_ready)             state_next = PARTIAL;",
            "FULL   : if      ( m_ready)             state_next = EMPTY;"))["status"] != "BOUND",
        "reset_change_rejected": result(replace_once(
            source, "{m_valid, s_ready, buffer, m_data} <= 0;",
            "{m_valid, s_ready, m_data} <= 0;"))["status"] != "BOUND",
        "ready_change_rejected": result(replace_once(
            source, "s_ready <= state_next != FULL;",
            "s_ready <= state_next != EMPTY;"))["status"] != "BOUND",
        "unrecognized_drain_source_rejected": result(replace_once(
            source, "FULL   : if (m_ready)               m_data <= s_data;",
            "FULL   : if (m_ready)               m_data <= other;"))["status"] != "BOUND",
        "duplicate_full_drain_ambiguous": result(replace_once(
            source, "FULL   : if (m_ready)               m_data <= s_data;",
            "FULL   : if (m_ready) m_data <= s_data;\n"
            "        FULL: if (m_ready) m_data <= s_data;"))["status"] == "AMBIGUOUS",
        "extra_output_write_ambiguous": result(replace_once(
            source, "FULL   : if (m_ready)               m_data <= s_data;",
            "FULL   : if (m_ready) m_data <= s_data;\n"
            "        m_data <= s_data;"))["status"] == "AMBIGUOUS",
        "unexpected_directive_rejected": result(replace_once(
            source, "module skid_buffer", "`include \"answer.svh\"\nmodule skid_buffer"))[
                "status"] == "UNSUPPORTED",
        "comment_decoy_ignored": result(source + "\n// FULL: if (m_ready) m_data <= buffer;\n")[
            "status"] == "BOUND",
        "source_not_mutated": FAULT.read_bytes() == fault_raw,
        "stale_source_action_rejected": reject_action(
            source + "\n// later source\n", STATE_SKID_CONTEXT, binding),
        "tampered_witness_action_rejected": reject_action(
            source, STATE_SKID_CONTEXT,
            {**binding, "witness_digest": "sha256:" + "0" * 64}),
        "wrong_context_action_rejected": reject_action(
            source, {"WIDTH": 16}, binding),
    }
    with patch("builtins.open", side_effect=AssertionError("file access")):
        with patch("pathlib.Path.read_text", side_effect=AssertionError("file access")):
            with patch("pathlib.Path.read_bytes", side_effect=AssertionError("file access")):
                with patch("socket.socket", side_effect=AssertionError("network access")):
                    try:
                        fresh = result(source)
                        regenerated, _ = apply_bound_skid_payload_v6(
                            ASSET, source, STATE_SKID_CONTEXT, fresh)
                        cases["binder_action_no_file_or_network"] = (
                            fresh == binding and regenerated == candidate)
                    except AssertionError:
                        cases["binder_action_no_file_or_network"] = False
    train = {}
    for case in sorted(CASES):
        checked = verify_acquisition(acquisition(case, "treatment"))
        source_file = (Path(checked["work"]) / "agent-inputs" /
                       (checked["source_file"] if case == "axis_register" else "skidbuffer.v"))
        train_source = source_file.read_text(encoding="utf-8")
        old = bind_skid_payload_v5(
            {"binding_template": V5_TEMPLATE}, train_source, checked["public_context"])
        new = result(train_source, checked["public_context"])
        if old["status"] != "BOUND" or new["status"] != "BOUND":
            raise ValueError("v6 lost a TRAIN delegation: " + case)
        replayed, action = apply_bound_skid_payload_v6(
            ASSET, train_source, checked["public_context"], new)
        cases["train_" + case + "_delegate_replays"] = (
            sha(replayed.encode()) == checked["after_source_sha256"] and
            action["rewritten"] == 1 and
            new["witness"]["rhs_span"] == old["witness"]["rhs_span"])
        train[case] = {"source_sha256": sha(train_source.encode()),
                       "candidate_sha256": sha(replayed.encode()),
                       "train_receipt_digest": checked["train_receipt_digest"]}
    return {
        "schema": "tehm-r5-skid-v6-dev-source-only-conformance-v1",
        "role": "DEV_OBSERVED_NOT_TRANSFER_NOT_MEMORY",
        "valid": all(cases.values()),
        "case_count": len(cases),
        "failed": sorted(key for key, passed in cases.items() if not passed),
        "cases": cases,
        "fault_source_sha256": FAULT_SHA,
        "candidate_source_sha256": sha(candidate.encode()),
        "binding": binding,
        "action": receipt,
        "train_delegations": train,
        "source_only_binder_oracle_calls": 0,
        "independent_transfer": False,
        "memory_m_plus_v6_constructed": False,
        "model_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    report = check()
    if args.output is not None:
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("v6 DEV report output must be fresh")
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    elif json.loads(args.verify.read_bytes()) != report:
        raise ValueError("v6 DEV report replay drift")
    print(json.dumps({"valid": report["valid"], "case_count": report["case_count"],
                      "failed": report["failed"],
                      "candidate_source_sha256": report["candidate_source_sha256"]},
                     sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
