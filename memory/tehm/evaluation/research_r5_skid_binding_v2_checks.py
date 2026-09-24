"""Adversarial, non-oracle checks for the independent R5 DEV v2 generation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from .research_r5_skid_binding_v2 import (
    CONTEXTS, TEMPLATE, apply_bound_skid_payload_v2, bind_skid_payload_v2,
)


def run_checks(*, register_clean: str, register_fault: str,
               broadcast_clean: str, broadcast_fault: str,
               unrelated: str) -> dict:
    asset = {"binding_template": TEMPLATE}
    cases: dict[str, bool] = {}
    for shape, clean, fault in (
        ("register", register_clean, register_fault),
        ("broadcast", broadcast_clean, broadcast_fault),
    ):
        context = CONTEXTS[shape]
        bound = bind_skid_payload_v2(asset, fault, context)
        cases[f"{shape}_unique_fault_bound"] = (
            bound["status"] == "BOUND" and bound["witness"]["shape"] == shape)
        cases[f"{shape}_healthy_no_match"] = (
            bind_skid_payload_v2(asset, clean, context)["status"] == "NO_MATCH")
        cases[f"{shape}_unrelated_rejected"] = (
            bind_skid_payload_v2(asset, unrelated, context)["status"]
            in {"NO_MATCH", "UNSUPPORTED"})
        cases[f"{shape}_two_modules_unsupported"] = (
            bind_skid_payload_v2(asset, fault + "\nmodule extra; endmodule\n", context)["status"]
            == "UNSUPPORTED")
        cases[f"{shape}_extra_context_unsupported"] = (
            bind_skid_payload_v2(asset, fault,
                                 {**context, "gold_path": "/private/answer.v"})["status"]
            == "UNSUPPORTED")
        cases[f"{shape}_wrong_asset_unsupported"] = (
            bind_skid_payload_v2({"binding_template": {"contract": "other"}},
                                 fault, context)["status"] == "UNSUPPORTED")
        cases[f"{shape}_lexical_unsupported"] = (
            bind_skid_payload_v2(asset, fault.replace(
                "// datapath", '`include "answer.v"\n// datapath', 1), context)["status"]
            == "UNSUPPORTED")
        cases[f"{shape}_comment_not_evidence"] = (
            bind_skid_payload_v2(asset, "// /private/answer.v\n" + fault,
                                 context)["status"] == "BOUND")
        branch = "end else if (store_axis_temp_to_output) begin"
        cases[f"{shape}_ambiguous_branch"] = (
            bind_skid_payload_v2(asset, fault.replace(branch, branch + "\n" + branch, 1),
                                 context)["status"] == "AMBIGUOUS")
        needle = "m_axis_tdata_reg <= s_axis_tdata;"
        # The first occurrence is the direct-input path; the second is the
        # faulty temp-to-output path. Duplicate only the latter.
        first = fault.find(needle)
        second = fault.find(needle, first + len(needle)) if first >= 0 else -1
        duplicated = (fault[:second] + needle + "\n        " + fault[second:]
                      if second >= 0 else fault)
        cases[f"{shape}_ambiguous_assignment"] = (
            second >= 0 and bind_skid_payload_v2(asset, duplicated, context)["status"]
            == "AMBIGUOUS")
        if bound["status"] != "BOUND":
            continue
        edited, receipt = apply_bound_skid_payload_v2(asset, fault, context, bound)
        cases[f"{shape}_single_edit_matches_clean"] = (
            edited == clean and receipt["rewritten"] == 1)
        cases[f"{shape}_idempotent_no_match"] = (
            bind_skid_payload_v2(asset, edited, context)["status"] == "NO_MATCH")
        for label, attempted_source, attempted_binding in (
            ("repeat_rejected", edited, bound),
            ("tampered_digest_rejected", fault,
             {**bound, "witness_digest": "sha256:" + "0" * 64}),
            ("tampered_span_rejected", fault,
             {**bound, "witness": {**bound["witness"], "rhs_span": [0, 1]}}),
            ("stale_source_rejected", "\n" + fault, bound),
        ):
            try:
                apply_bound_skid_payload_v2(asset, attempted_source, context,
                                            attempted_binding)
                cases[f"{shape}_{label}"] = False
            except ValueError:
                cases[f"{shape}_{label}"] = True
        with patch("builtins.open", side_effect=AssertionError("file access")):
            with patch("pathlib.Path.read_text", side_effect=AssertionError("file access")):
                try:
                    again = bind_skid_payload_v2(asset, fault, context)
                    apply_bound_skid_payload_v2(asset, fault, context, again)
                    cases[f"{shape}_no_file_access"] = again == bound
                except AssertionError:
                    cases[f"{shape}_no_file_access"] = False
    cases["unsupported_register_mode"] = (
        bind_skid_payload_v2(asset, register_fault,
                             {"DATA_WIDTH": 8, "REG_TYPE": 1})["status"] == "UNSUPPORTED")
    cases["unsupported_broadcast_fanout"] = (
        bind_skid_payload_v2(asset, broadcast_fault,
                             {"DATA_WIDTH": 8, "M_COUNT": 3})["status"] == "UNSUPPORTED")
    cases["cross_shape_context_no_match"] = (
        bind_skid_payload_v2(asset, broadcast_fault, CONTEXTS["register"])["status"]
        == "NO_MATCH" and
        bind_skid_payload_v2(asset, register_fault, CONTEXTS["broadcast"])["status"]
        == "NO_MATCH")
    return {"valid": all(cases.values()), "case_count": len(cases),
            "failed": [name for name, ok in cases.items() if not ok], "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("register_clean", "register_fault", "broadcast_clean",
                 "broadcast_fault", "unrelated"):
        parser.add_argument("--" + name.replace("_", "-"), required=True, type=Path)
    args = parser.parse_args()
    inputs = {name: getattr(args, name).read_text() for name in (
        "register_clean", "register_fault", "broadcast_clean", "broadcast_fault",
        "unrelated")}
    result = run_checks(**inputs)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
