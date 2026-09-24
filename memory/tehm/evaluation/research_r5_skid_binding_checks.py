"""Adversarial DEV checks for the source-only skid payload binder."""
from __future__ import annotations

from unittest.mock import patch

from .research_r5_skid_binding import (
    CONTEXT, TEMPLATE, apply_bound_skid_payload, bind_skid_payload,
)


def run_checks(*, clean: str, faulty: str, unrelated: str, similar: str) -> dict:
    asset = {"binding_template": TEMPLATE}
    raw = faulty
    bound = bind_skid_payload(asset, faulty, CONTEXT)
    cases: dict[str, bool] = {
        "unique_fault_bound": bound["status"] == "BOUND",
        "healthy_no_match": bind_skid_payload(asset, clean, CONTEXT)["status"] == "NO_MATCH",
        "unrelated_no_match": bind_skid_payload(asset, unrelated, CONTEXT)["status"] == "NO_MATCH",
        "similar_healthy_no_match": bind_skid_payload(asset, similar, CONTEXT)["status"] == "NO_MATCH",
        "unsupported_reg_type": bind_skid_payload(asset, faulty, {"DATA_WIDTH": 8, "REG_TYPE": 1})["status"] == "UNSUPPORTED",
        "unsupported_extra_context": bind_skid_payload(asset, faulty,
            {**CONTEXT, "gold_path": "/private/answer.v"})["status"] == "UNSUPPORTED",
        "unsupported_asset": bind_skid_payload({"binding_template": {"contract": "old"}},
            faulty, CONTEXT)["status"] == "UNSUPPORTED",
        "unsupported_two_modules": bind_skid_payload(asset, faulty + "\nmodule other; endmodule\n",
            CONTEXT)["status"] == "UNSUPPORTED",
        "unsupported_lexical": bind_skid_payload(asset, faulty.replace("// datapath", "`include \"answer.v\"\n// datapath", 1),
            CONTEXT)["status"] == "UNSUPPORTED",
    }
    branch = "        end else if (store_axis_temp_to_output) begin"
    duplicate_branch = faulty.replace(branch, branch + "\n" + branch, 1)
    cases["ambiguous_branch"] = bind_skid_payload(asset, duplicate_branch, CONTEXT)["status"] == "AMBIGUOUS"
    double_write = faulty.replace(
        "            m_axis_tdata_reg <= s_axis_tdata;\n            m_axis_tkeep_reg <= temp_m_axis_tkeep_reg;",
        "            m_axis_tdata_reg <= s_axis_tdata;\n            m_axis_tdata_reg <= s_axis_tdata;\n            m_axis_tkeep_reg <= temp_m_axis_tkeep_reg;", 1)
    cases["ambiguous_assignment"] = bind_skid_payload(asset, double_write, CONTEXT)["status"] == "AMBIGUOUS"
    commented = "// evaluator answer path /private/gold.v must be ignored\n" + faulty
    cases["comment_does_not_supply_slots"] = bind_skid_payload(asset, commented, CONTEXT)["status"] == "BOUND"

    if bound["status"] == "BOUND":
        edited, edit = apply_bound_skid_payload(asset, faulty, CONTEXT, bound)
        cases["actual_single_edit"] = edited == clean and edit["rewritten"] == 1
        cases["original_source_unchanged"] = raw == faulty
        cases["idempotent_rejection"] = bind_skid_payload(asset, edited, CONTEXT)["status"] == "NO_MATCH"
        try:
            apply_bound_skid_payload(asset, edited, CONTEXT, bound)
            cases["repeat_action_rejected"] = False
        except ValueError:
            cases["repeat_action_rejected"] = True
        forged = {**bound, "witness_digest": "sha256:" + "0" * 64}
        try:
            apply_bound_skid_payload(asset, faulty, CONTEXT, forged)
            cases["tampered_witness_rejected"] = False
        except ValueError:
            cases["tampered_witness_rejected"] = True
        forged_span = {**bound, "witness": {**bound["witness"], "rhs_span": [0, 1]}}
        try:
            apply_bound_skid_payload(asset, faulty, CONTEXT, forged_span)
            cases["tampered_span_rejected"] = False
        except ValueError:
            cases["tampered_span_rejected"] = True
    with patch("builtins.open", side_effect=AssertionError("file access")):
        with patch("pathlib.Path.read_text", side_effect=AssertionError("file access")):
            try:
                probe = bind_skid_payload(asset, faulty, CONTEXT)
                if probe["status"] == "BOUND":
                    apply_bound_skid_payload(asset, faulty, CONTEXT, probe)
                cases["no_file_access"] = probe["status"] == "BOUND"
            except AssertionError:
                cases["no_file_access"] = False
    return {"valid": all(cases.values()), "case_count": len(cases),
            "failed": [name for name, ok in cases.items() if not ok], "cases": cases}
