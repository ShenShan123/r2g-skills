"""RAM-only generation-4 TRAIN conformance using the frozen v3 check matrix.

The v3 matrix is executed against the v4 adapter in a single isolated test
process; its original v3 module and frozen receipts are not modified.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from tehm.adapters import research_r5_rtl_scoped_v3 as v3
from tehm.adapters import research_r5_rtl_scoped_v4 as adapter
from tehm.evaluation import research_r5_rtl_scoped_v3_checks as common
from tehm.rtl.skid_payload_action_v6 import DOMAIN, PROFILE
from tehm.rtl.rtl_actions import apply_rtl_action

ROOT = Path("/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev")
RAM_CLOCK = "2026-09-25T00:00:00+00:00"


def _reject(call) -> bool:
    try:
        call()
    except (ValueError, KeyError, TypeError):
        return True
    return False


def check() -> dict:
    original = common.adapter
    try:
        common.adapter = adapter
        with patch("tehm.db.now_local", return_value=RAM_CLOCK):
            base = common.check()
    finally:
        common.adapter = original
    cases = {name.replace("v5_action", "v6_action"): passed
             for name, passed in base["cases"].items()}
    source = adapter.verify_acquisition(adapter.acquisition("axis_register", "treatment"))
    record = adapter.build_record(adapter.acquisition("axis_register", "treatment"))
    cases["v6_scoped_identity"] = (
        record.verification["scoped_execution"]["version"] == adapter.SCOPED_VERSION and
        record.record_id.startswith("r5-rtl-train-v4:") and
        record.action["domain"] == DOMAIN and
        record.verification["scope"] == PROFILE and
        base["shared_contract_digest"] == adapter._digest(adapter.MEASUREMENT_CONTRACT))
    cases["v3_acquisition_not_v4"] = _reject(lambda:
        adapter.verify_acquisition(v3.acquisition("axis_register", "treatment")))
    cases["v3_record_not_v4"] = _reject(lambda:
        adapter.replay_record(v3.build_record(v3.acquisition(
            "axis_register", "treatment"))))
    cases["v4_acquisition_not_v3"] = _reject(lambda:
        v3.verify_acquisition(adapter.acquisition("axis_register", "treatment")))
    faulty = adapter._source(source)
    candidate, edit = apply_rtl_action(faulty, record.action["payload"])
    cases["exact_v6_candidate_oracle"] = (
        edit.get("rewritten") == 1 and
        adapter.oracle_for(source, record, "target")(candidate, {})["verdict"] == "PASS" and
        adapter.oracle_for(source, record, "preservation")(candidate, {})["verdict"] == "PASS" and
        adapter.oracle_for(source, record, "target")(faulty, {})["verdict"] == "FAIL")
    return {
        **base,
        "schema": "tehm-r5-shared-measurement-v4-ram-conformance-v2",
        "cases": cases,
        "case_count": len(cases),
        "failed": sorted(name for name, passed in cases.items() if not passed),
        "valid": all(cases.values()),
        "ram_fixture_clock": RAM_CLOCK,
        "adapter_profile": PROFILE,
        "adapter_campaign": adapter.CAMPAIGN,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    report = check()
    if args.output is not None:
        if args.output.parent != ROOT or args.output.exists() or args.output.is_symlink():
            raise ValueError("v4 TRAIN RAM receipt must be fresh inside pilot dev")
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    elif json.loads(args.verify.read_bytes()) != report:
        raise ValueError("v4 TRAIN RAM conformance receipt drift")
    print(json.dumps({"valid": report["valid"], "case_count": report["case_count"],
                      "failed": report["failed"],
                      "knowledge_object_id": report["knowledge_object_id"]},
                     sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
