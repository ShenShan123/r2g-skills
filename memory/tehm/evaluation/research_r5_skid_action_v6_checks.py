"""Cold DEV-only action-envelope checks for the bounded v6 skid operator."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from tehm.adapters.research_r5_rtl_scoped import CASES, acquisition, verify_acquisition
from tehm.evaluation.research_r5_skid_binding_v6 import STATE_SKID_CONTEXT
from tehm.rtl.skid_payload_action_v6 import (
    DOMAIN, PROFILE, apply_skid_payload_action_v6, payload_from_source_v6,
)

ROOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot")
FAULT = ROOT / "dev/skillsurf-drain-v6-r1/skid_buffer_drain_fault.sv"
CLEAN = Path("/data1/zhangdy/RTL/RTL_testbench/SkillSurf/systemverilog/rtl/skid_buffer.sv")
CAPTURE_FAULT = ROOT / "qualification/skillsurf-skid-screen-r1/skid_buffer_fault.sv"
FAULT_SHA = "sha256:5ec912df2f54f3e4b95fe35d3c4338a6440312cb3c1907491af93ad9f8c908a3"
CLEAN_SHA = "sha256:7415ad31787cd599fd90c34fde39167e5d0b308249806bed335e5f723da44403"


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _reject(source: str, payload: object) -> bool:
    try:
        apply_skid_payload_action_v6(source, payload)
    except (ValueError, TypeError, KeyError):
        return True
    return False


def check() -> dict:
    fault, clean = FAULT.read_bytes(), CLEAN.read_bytes()
    if (FAULT.is_symlink() or CLEAN.is_symlink() or
            _sha(fault) != FAULT_SHA or _sha(clean) != CLEAN_SHA):
        raise ValueError("v6 DEV source identity drift")
    source = fault.decode("utf-8")
    payload = payload_from_source_v6(source, STATE_SKID_CONTEXT)
    candidate, receipt = apply_skid_payload_action_v6(source, payload)
    cases = {
        "exact_dev_payload": set(payload) == {
            "domain", "compatibility_profile", "module", "public_context",
            "source_sha256", "witness_digest"} and
            payload["domain"] == DOMAIN and payload["compatibility_profile"] == PROFILE,
        "dev_candidate_matches_clean": candidate.encode() == clean and
            _sha(candidate.encode()) == CLEAN_SHA and receipt["rewritten"] == 1,
        "source_rederived": receipt["source_binding_rederived"] is True and
            receipt["domain"] == DOMAIN,
        "stale_source_rejected": _reject(source + "\n// changed\n", payload),
        "wrong_domain_rejected": _reject(source, {**payload, "domain": "rtl.other"}),
        "wrong_module_rejected": _reject(source, {**payload, "module": "gold"}),
        "wrong_digest_rejected": _reject(source, {**payload,
            "witness_digest": "sha256:" + "0" * 64}),
        "wrong_context_rejected": _reject(source, {**payload,
            "public_context": {"WIDTH": 16}}),
        "extra_field_rejected": _reject(source, {**payload, "gold_edit": "buffer"}),
        "missing_field_rejected": _reject(source,
            {key: value for key, value in payload.items() if key != "source_sha256"}),
        "non_mapping_rejected": _reject(source, None),
        "healthy_source_rejected": _reject(clean.decode("utf-8"), payload),
        "capture_side_fault_rejected": _reject(
            CAPTURE_FAULT.read_text(encoding="utf-8"), payload),
        "source_not_modified": FAULT.read_bytes() == fault,
    }
    with patch("builtins.open", side_effect=AssertionError("file access")):
        with patch("pathlib.Path.read_text", side_effect=AssertionError("file access")):
            with patch("pathlib.Path.read_bytes", side_effect=AssertionError("file access")):
                with patch("socket.socket", side_effect=AssertionError("network access")):
                    try:
                        again = payload_from_source_v6(source, STATE_SKID_CONTEXT)
                        replayed, _ = apply_skid_payload_action_v6(source, again)
                        cases["action_no_file_or_network"] = again == payload and replayed == candidate
                    except AssertionError:
                        cases["action_no_file_or_network"] = False
    train = {}
    for case in sorted(CASES):
        checked = verify_acquisition(acquisition(case, "treatment"))
        source_file = (Path(checked["work"]) / "agent-inputs" /
                       (checked["source_file"] if case == "axis_register" else "skidbuffer.v"))
        train_source = source_file.read_text(encoding="utf-8")
        train_payload = payload_from_source_v6(train_source, checked["public_context"])
        train_candidate, train_receipt = apply_skid_payload_action_v6(
            train_source, train_payload)
        cases["train_" + case + "_action_replays"] = (
            _sha(train_candidate.encode()) == checked["after_source_sha256"] and
            train_receipt["rewritten"] == 1 and
            train_receipt.get("delegated_from") is not None)
        train[case] = {"source_sha256": _sha(train_source.encode()),
                       "candidate_sha256": _sha(train_candidate.encode()),
                       "train_receipt_digest": checked["train_receipt_digest"]}
    return {
        "schema": "tehm-r5-skid-v6-dev-action-envelope-conformance-v1",
        "role": "DEV_OBSERVED_NOT_TRANSFER_NOT_MEMORY",
        "valid": all(cases.values()), "case_count": len(cases),
        "failed": sorted(key for key, passed in cases.items() if not passed),
        "cases": cases, "dev_fault_source_sha256": FAULT_SHA,
        "dev_candidate_source_sha256": _sha(candidate.encode()),
        "payload": payload, "action": receipt, "train_delegations": train,
        "model_calls": 0, "core_asset_registered": False,
        "memory_m_plus_v6_constructed": False, "independent_transfer": False,
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
            raise ValueError("v6 DEV action output must be fresh")
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif json.loads(args.verify.read_bytes()) != report:
        raise ValueError("v6 DEV action report replay drift")
    print(json.dumps({"valid": report["valid"], "case_count": report["case_count"],
                      "failed": report["failed"],
                      "dev_candidate_source_sha256": report["dev_candidate_source_sha256"]},
                     sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
