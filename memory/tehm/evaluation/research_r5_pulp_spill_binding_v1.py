"""R5 DEV-only source binding for a bounded two-slot PULP spill register.

This module is not part of the frozen gen4 runtime or production profile.
Its syntactic witness only proposes a candidate; private RTL oracle owns verdicts.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from . import research_r5_skid_binding as base


CONTRACT = "rtl_two_slot_spill_payload_binding_dev_v1"
OPERATOR = "rtl.TWO_SLOT_SPILL_PAYLOAD_RESTORE_DEV_V1"
PROFILE = "rtl.two_slot.spill_payload.v1.dev"
PUBLIC_CONTEXT = {"data_t_bits": 8, "Bypass": False}
TEMPLATE = {
    "contract": CONTRACT,
    "operator": OPERATOR,
    "profile": PROFILE,
    "proof_scope": "unique_syntactic_b_slot_payload_source_not_functional",
}


def _result(status: str, reason: str, **detail: object) -> dict:
    return {"contract": CONTRACT, "status": status, "reason": reason, **detail}


def _matches(source: str, pattern: str) -> list[re.Match[str]]:
    return list(re.finditer(pattern, source, re.MULTILINE | re.DOTALL))


def bind_spill_payload_v1(asset: Mapping, source: str,
                          public_context: Mapping) -> dict:
    """Bind only from selected asset, buggy source, and exact public parameters."""
    if not isinstance(asset, Mapping) or asset.get("binding_template") != TEMPLATE:
        return _result("UNSUPPORTED", "binding_template_mismatch")
    if not isinstance(source, str) or not isinstance(public_context, Mapping):
        return _result("UNSUPPORTED", "source_or_public_context_invalid")
    if dict(public_context) != PUBLIC_CONTEXT:
        return _result("UNSUPPORTED", "unsupported_or_extra_public_parameters")
    if len(source) > 65536:
        return _result("UNSUPPORTED", "source_size_limit")
    masked = base._mask_comments(source)
    if len(_matches(masked, r"\bmodule\s+cc_spill_register_flushable\b")) != 1 or len(
            _matches(masked, r"\bendmodule\b")) != 1:
        return _result("NO_MATCH", "unique_spill_module_required")
    structural = {
        "type_parameter": r"\bparameter\s+type\s+data_t\s*=\s*logic\b",
        "bypass_parameter": r"\bparameter\s+bit\s+Bypass\s*=\s*1'b0\b",
        "non_bypass_branch": r"\bif\s*\(\s*Bypass\s*\)\s*begin\s*:\s*gen_bypass\b.*?\bend\s+else\s+begin\s*:\s*gen_spill_reg\b",
        "a_data_declaration": r"\bdata_t\s+a_data_q\s*;",
        "b_data_declaration": r"\bdata_t\s+b_data_q\s*;",
        "a_valid_declaration": r"\blogic\s+a_full_q\s*;",
        "b_valid_declaration": r"\blogic\s+b_full_q\s*;",
        "a_capture": r"`FFLARNC\s*\(\s*a_data_q\s*,\s*data_i\s*,\s*a_fill\s*,\s*clr_i\s*,\s*data_t'\s*\(\s*'0\s*\)\s*,\s*clk_i\s*,\s*rst_ni\s*\)",
        "a_occupancy": r"`FFLARNC\s*\(\s*a_full_q\s*,\s*a_fill\s*,\s*a_fill\s*\|\|\s*a_drain\s*,\s*clr_i\s*,\s*'0\s*,\s*clk_i\s*,\s*rst_ni\s*\)",
        "b_occupancy": r"`FFLARNC\s*\(\s*b_full_q\s*,\s*b_fill\s*,\s*b_fill\s*\|\|\s*b_drain\s*,\s*clr_i\s*,\s*'0\s*,\s*clk_i\s*,\s*rst_ni\s*\)",
        "a_fill": r"\bassign\s+a_fill\s*=\s*valid_i\s*&&\s*ready_o\s*&&\s*\(\s*!flush_i\s*\)\s*;",
        "a_drain": r"\bassign\s+a_drain\s*=\s*\(\s*a_full_q\s*&&\s*!b_full_q\s*\)\s*\|\|\s*flush_i\s*;",
        "b_fill": r"\bassign\s+b_fill\s*=\s*a_drain\s*&&\s*\(\s*!ready_i\s*\)\s*&&\s*\(\s*!flush_i\s*\)\s*;",
        "b_drain": r"\bassign\s+b_drain\s*=\s*\(\s*b_full_q\s*&&\s*ready_i\s*\)\s*\|\|\s*flush_i\s*;",
        "ready": r"\bassign\s+ready_o\s*=\s*!a_full_q\s*\|\|\s*!b_full_q\s*;",
        "valid": r"\bassign\s+valid_o\s*=\s*a_full_q\s*\|\s*b_full_q\s*;",
        "output_mux": r"\bassign\s+data_o\s*=\s*b_full_q\s*\?\s*b_data_q\s*:\s*a_data_q\s*;",
    }
    missing = [name for name, pattern in structural.items()
               if len(_matches(masked, pattern)) != 1]
    if missing:
        return _result("UNSUPPORTED", "spill_structure_witness_missing_or_ambiguous",
                       missing=missing)
    if len(_matches(masked, r"`FFLARNC\s*\(")) != 4:
        return _result("AMBIGUOUS", "unexpected_register_macro_cardinality")
    b_capture = _matches(
        masked,
        r"`FFLARNC\s*\(\s*b_data_q\s*,\s*(?P<rhs>[A-Za-z_]\w*)\s*,\s*b_fill\s*,\s*clr_i\s*,\s*data_t'\s*\(\s*'0\s*\)\s*,\s*clk_i\s*,\s*rst_ni\s*\)",
    )
    if len(b_capture) != 1:
        return _result("AMBIGUOUS", "unique_b_capture_required")
    if len(_matches(masked, r"\bb_data_q\s*(?:<=|=(?!=))")) != 0:
        return _result("AMBIGUOUS", "additional_b_payload_write")
    rhs = b_capture[0]["rhs"]
    if rhs == "a_data_q":
        return _result("NO_MATCH", "b_slot_already_captures_a_payload")
    if rhs != "data_i":
        return _result("UNSUPPORTED", "unrecognized_b_payload_source")
    start, stop = b_capture[0].span("rhs")
    if source[start:stop] != rhs:
        return _result("UNSUPPORTED", "source_offset_does_not_replay")
    witness = {
        "shape": "two_slot_b_fill_payload_capture",
        "module": "cc_spill_register_flushable",
        "a_payload": "a_data_q",
        "b_payload": "b_data_q",
        "old_rhs": rhs,
        "replacement_rhs": "a_data_q",
        "rhs_span": [start, stop],
        "source_sha256": base._source_sha(source),
        "public_context": dict(public_context),
        "proof_scope": TEMPLATE["proof_scope"],
        "structural_requirements": "pulp_two_slot_four_fflar_nc_exact_flow_v1",
    }
    return _result("BOUND", "unique_b_slot_source_mismatch", witness=witness,
                   witness_digest=base._digest(witness))


def apply_bound_spill_payload_v1(asset: Mapping, source: str,
                                 public_context: Mapping,
                                 binding: Mapping) -> tuple[str, dict]:
    fresh = bind_spill_payload_v1(asset, source, public_context)
    if fresh.get("status") != "BOUND" or not isinstance(binding, Mapping) or dict(binding) != fresh:
        raise ValueError("v1 PULP binding is stale, tampered, or unsupported")
    witness = fresh["witness"]
    start, stop = witness["rhs_span"]
    edited = source[:start] + witness["replacement_rhs"] + source[stop:]
    healthy = bind_spill_payload_v1(asset, edited, public_context)
    if healthy.get("reason") != "b_slot_already_captures_a_payload":
        raise ValueError("v1 PULP candidate did not become supported healthy structure")
    return edited, {
        "binding_contract": CONTRACT,
        "operator": OPERATOR,
        "profile": PROFILE,
        "rewritten": 1,
        "source_binding_rederived": True,
        "before_source_sha256": base._source_sha(source),
        "after_source_sha256": base._source_sha(edited),
        "witness_digest": fresh["witness_digest"],
        "proof_scope": TEMPLATE["proof_scope"],
    }


__all__ = ["CONTRACT", "OPERATOR", "PROFILE", "PUBLIC_CONTEXT", "TEMPLATE",
           "bind_spill_payload_v1", "apply_bound_spill_payload_v1"]
