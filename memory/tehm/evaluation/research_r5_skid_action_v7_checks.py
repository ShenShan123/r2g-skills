"""DEV/TRAIN v7 action regression; a PASS grants no Asset authority."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from tehm.evaluation import research_r5_skid_binding_v7 as binder
from tehm.evaluation import research_r5_pulp_spill_binding_v1 as pulp
from tehm.evaluation import research_r5_skid_binding_v6 as v6
from tehm.rtl.rtl_actions import RTL_ACTION_DOMAINS, RTL_ACTION_VERSION, apply_rtl_action
from tehm.rtl.skid_payload_action_v7 import DOMAIN, PROFILE, payload_from_source_v7

ROOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def check() -> dict:
    base = ROOT / "training/skid-v7-r1/fresh-r1/evaluator-inputs/source"
    fault = (base / "fault/skid_buffer.sv").read_text()
    clean = (base / "clean/skid_buffer.sv").read_text()
    pulp_root = ROOT / "dev/pulp-spill-oracle-v1/evaluator-private/source"
    pulp_fault = (pulp_root / "fault/cc_spill_register_flushable.sv").read_text()
    pulp_clean = (pulp_root / "clean/cc_spill_register_flushable.sv").read_text()
    state_fault = (ROOT / "dev/skillsurf-drain-v6-r1/skid_buffer_drain_fault.sv").read_text()
    sources = (("libsv_train", fault, clean, binder.LIBSV_CONTEXT),
               ("pulp_dev", pulp_fault, pulp_clean, pulp.PUBLIC_CONTEXT),
               ("v6_dev", state_fault, None, v6.STATE_SKID_CONTEXT))
    cases = {"domain_in_catalog": DOMAIN in RTL_ACTION_DOMAINS,
             "catalog_version_bumped": RTL_ACTION_VERSION == "rtl-actions-v0.9"}
    for name, source, expected, context in sources:
        payload = payload_from_source_v7(source, context)
        edited, receipt = apply_rtl_action(source, payload)
        cases[name + ":route_and_rewrite"] = (
            payload["domain"] == DOMAIN and payload["compatibility_profile"] == PROFILE and
            receipt["rewritten"] == 1 and edited != source and
            receipt["source_binding_rederived"] is True)
        cases[name + ":healthy_no_match"] = binder.bind_skid_payload_v7(
            {"binding_template": binder.TEMPLATE}, edited, context)["status"] == "NO_MATCH"
        if expected is not None:
            cases[name + ":expected_bytes"] = edited == expected
        mutated = copy.deepcopy(payload)
        mutated["witness_digest"] = "sha256:forged"
        try:
            apply_rtl_action(source, mutated)
        except ValueError:
            cases[name + ":forged_payload_rejected"] = True
        else:
            cases[name + ":forged_payload_rejected"] = False
        try:
            apply_rtl_action(source + "\n// stale\n", payload)
        except ValueError:
            cases[name + ":stale_source_rejected"] = True
        else:
            cases[name + ":stale_source_rejected"] = False
    try:
        payload_from_source_v7(fault, {"DATA_WIDTH": 32, "oracle": "leak"})
    except ValueError:
        cases["extra_public_field_rejected"] = True
    else:
        cases["extra_public_field_rejected"] = False
    try:
        payload_from_source_v7(clean, binder.LIBSV_CONTEXT)
    except ValueError:
        cases["healthy_training_source_rejected"] = True
    else:
        cases["healthy_training_source_rejected"] = False
    return {"valid": all(cases.values()), "case_count": len(cases),
            "failed": sorted(name for name, passed in cases.items() if not passed),
            "cases": cases, "role": "DEV_TRAIN_ACTION_NOT_ASSET_AUTHORITY",
            "train_fault_sha256": _sha(fault), "train_candidate_sha256": _sha(clean),
            "memory_constructed": False, "heldout_transfer": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = check()
    if args.output:
        if args.output.exists() or args.output.parent != ROOT / "training/skid-v7-r1":
            raise ValueError("v7 action receipt must be a fresh TRAIN-local file")
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"valid": result["valid"], "case_count": result["case_count"],
                      "failed": result["failed"]}, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
