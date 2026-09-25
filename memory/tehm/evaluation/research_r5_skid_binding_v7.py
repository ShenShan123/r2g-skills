"""R5 DEV-only v7 source binding for three bounded skid-payload structures.

LibSV's capture-side branch is new. Frozen v6 and PULP DEV binding are delegated
without changes. All outcomes are syntactic proposals, never oracle verdicts.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from tehm.rtl.verilog_parse import _balanced_end

from . import research_r5_pulp_spill_binding_v1 as pulp
from . import research_r5_skid_binding as base
from . import research_r5_skid_binding_v6 as v6


CONTRACT = "rtl_skid_temp_payload_binding_dev_v7"
OPERATOR = "rtl.SKID_TEMP_PAYLOAD_RESTORE_DEV_V7"
PROFILE = "rtl.skid.temp_payload.v7.dev"
TEMPLATE = {"contract": CONTRACT, "operator": OPERATOR, "profile": PROFILE,
            "proof_scope": "unique_syntactic_buffered_payload_source_not_functional"}
LIBSV_CONTEXT = {"DATA_WIDTH": 32}


def _result(status: str, reason: str, **detail: object) -> dict:
    return {"contract": CONTRACT, "status": status, "reason": reason, **detail}


def _find(source: str, pattern: str) -> list[re.Match[str]]:
    return list(re.finditer(pattern, source, re.MULTILINE | re.DOTALL))


def _libsv(source: str, context: dict) -> dict:
    if not source or len(source) > 65536:
        return _result("UNSUPPORTED", "source_size_or_empty")
    masked = base._mask_comments(source)
    modules = _find(masked, r"\bmodule\s+skid_buffer\b")
    endings = _find(masked, r"\bendmodule\b")
    if len(modules) != 1 or len(endings) != 1:
        return _result("NO_MATCH", "unique_libsv_skid_module_required")
    if modules[0].start() >= endings[0].start():
        return _result("UNSUPPORTED", "module_boundary_invalid")
    before = masked[:modules[0].start()]
    after = masked[endings[0].end():]
    if not re.fullmatch(r"\s*`ifndef\s+LIBSV_FIFOS_SKID_BUFFER\s+"
                        r"`define\s+LIBSV_FIFOS_SKID_BUFFER\s*", before):
        return _result("UNSUPPORTED", "source_guard_preamble_mismatch")
    if not re.fullmatch(r"\s*`endif\s*", after):
        return _result("UNSUPPORTED", "source_guard_postamble_mismatch")
    body = masked[modules[0].start():endings[0].end()]
    if _find(body, r"(?m)^[ \t]*`"):
        return _result("UNSUPPORTED", "preprocessor_inside_module")
    if len(_find(body, r"\balways_ff\b")) != 2 or len(
            _find(body, r"\balways_comb\b")) != 2:
        return _result("UNSUPPORTED", "unexpected_process_structure")
    witnesses = {
        "parameter": r"\bparameter\s+int\s+DATA_WIDTH\s*=\s*32\b",
        "input_payload": r"\binput\s+logic\s*\[\s*DATA_WIDTH\s*-\s*1\s*:\s*0\s*\]\s*i_data\b",
        "output_payload": r"\boutput\s+logic\s*\[\s*DATA_WIDTH\s*-\s*1\s*:\s*0\s*\]\s*o_data\b",
        "ready_input": r"\binput\s+logic\s+i_output_ready\b",
        "valid_input": r"\binput\s+logic\s+i_input_valid\b",
        "valid_output": r"\boutput\s+logic\s+o_output_valid\b",
        "ready_output": r"\boutput\s+logic\s+o_input_ready\b",
        "state_declaration": r"\btypedef\s+enum\s+logic\s*\[\s*2\s*:\s*0\s*\]\s*\{\s*EMPTY\s*=\s*3'b001\s*,\s*BUSY\s*=\s*3'b010\s*,\s*FULL\s*=\s*3'b100\s*\}\s*state_t\s*;",
        "state_variables": r"\bstate_t\s+state\s*,\s*next_state\s*;",
        "buffer_declaration": r"\blogic\s*\[\s*DATA_WIDTH\s*-\s*1\s*:\s*0\s*\]\s*buffer\s*;",
        "accept": r"\baccept\s*=\s*i_input_valid\s*&&\s*o_input_ready\s*;",
        "transmit": r"\btransmit\s*=\s*o_output_valid\s*&&\s*i_output_ready\s*;",
        "empty_to_busy": r"\bEMPTY\s*:\s*begin\s+next_state\s*=\s*EMPTY\s*;\s*if\s*\(\s*accept\s*\)\s*next_state\s*=\s*BUSY\s*;\s*end",
        "busy_to_full": r"\bBUSY\s*:\s*begin\s+next_state\s*=\s*BUSY\s*;\s*if\s*\(\s*accept\s*&&\s*!transmit\s*\)\s*next_state\s*=\s*FULL\s*;\s*else\s+if\s*\(\s*!accept\s*&&\s*transmit\s*\)\s*next_state\s*=\s*EMPTY\s*;\s*end",
        "full_to_busy": r"\bFULL\s*:\s*begin\s+next_state\s*=\s*FULL\s*;\s*if\s*\(\s*transmit\s*\)\s*next_state\s*=\s*BUSY\s*;\s*end",
        "registered_ready": r"\bo_input_ready\s*<=\s*next_state\s*!=\s*FULL\s*;",
        "registered_valid": r"\bo_output_valid\s*<=\s*next_state\s*!=\s*EMPTY\s*;",
        "buffer_write_enable": r"\bbuffer_write_en\s*=\s*state\s*==\s*BUSY\s*&&\s*accept\s*&&\s*!transmit\s*;",
        "output_write_enable": r"\bo_data_write_en\s*=\s*\(\s*state\s*==\s*EMPTY\s*&&\s*accept\s*&&\s*!transmit\s*\)\s*\|\|\s*\(\s*state\s*==\s*BUSY\s*&&\s*accept\s*&&\s*transmit\s*\)\s*\|\|\s*\(\s*state\s*==\s*FULL\s*&&\s*!accept\s*&&\s*transmit\s*\)\s*;",
        "full_output": r"\bif\s*\(\s*state\s*==\s*FULL\s*\)\s*o_data\s*<=\s*buffer\s*;\s*else\s+o_data\s*<=\s*i_data\s*;",
        "buffer_reset": r"\bif\s*\(\s*!i_aresetn\s*\|\|\s*i_clear\s*\)\s*begin\s+o_data\s*<=\s*'0\s*;\s*buffer\s*<=\s*'0\s*;",
    }
    missing = [name for name, pattern in witnesses.items() if len(_find(body, pattern)) != 1]
    if missing:
        return _result("UNSUPPORTED", "libsv_flow_witness_missing_or_ambiguous", missing=missing)
    # Count every drive, not only nonblocking assignments: an added blocking
    # or continuous drive would make the proposed rewrite unsafe to certify.
    if len(_find(body, r"\bbuffer\s*(?:<=|=(?!=))")) != 2 or len(
            _find(body, r"\bo_data\s*(?:<=|=(?!=))")) != 3:
        return _result("AMBIGUOUS", "payload_write_cardinality_mismatch")
    process = _find(body, r"\balways_ff\s*@\s*\(\s*posedge\s+i_clock\s*,\s*negedge\s+i_aresetn\s*\)\s*begin\s*:\s*o_data_and_buffer_logic\b")
    if len(process) != 1:
        return _result("UNSUPPORTED", "unique_payload_process_required")
    begin = body.rfind("begin", process[0].start(), process[0].end())
    end = _balanced_end(body, begin)
    if begin < 0 or end >= len(body) or body[end-3:end] != "end":
        return _result("UNSUPPORTED", "payload_process_unbalanced")
    capture = _find(body, r"\bif\s*\(\s*buffer_write_en\s*\)\s*begin\s*"
                          r"buffer\s*<=\s*(?P<rhs>i_data|'0|[A-Za-z_]\w*)\s*;\s*end")
    if len(capture) != 1 or not (begin < capture[0].start() < capture[0].end() < end):
        return _result("AMBIGUOUS", "unique_buffer_capture_in_payload_process_required")
    rhs = capture[0]["rhs"]
    if rhs == "i_data":
        return _result("NO_MATCH", "buffer_already_captures_input_payload")
    if rhs != "'0":
        return _result("UNSUPPORTED", "unrecognized_buffer_capture_source")
    offset = modules[0].start()
    start, stop = capture[0].span("rhs")
    start += offset
    stop += offset
    if source[start:stop] != rhs:
        return _result("UNSUPPORTED", "source_offset_does_not_replay")
    witness = {"shape": "libsv_busy_to_full_buffer_capture",
               "module": "skid_buffer", "input_payload": "i_data",
               "buffer_payload": "buffer", "old_rhs": rhs,
               "replacement_rhs": "i_data", "rhs_span": [start, stop],
               "source_sha256": base._source_sha(source),
               "public_context": context, "proof_scope": TEMPLATE["proof_scope"],
               "structural_requirements": "libsv_busy_full_exact_control_and_two_writes_v1"}
    return _result("BOUND", "unique_capture_side_buffered_payload_mismatch",
                   witness=witness, witness_digest=base._digest(witness))


def bind_skid_payload_v7(asset: Mapping, source: str,
                         public_context: Mapping) -> dict:
    if not isinstance(asset, Mapping) or asset.get("binding_template") != TEMPLATE:
        return _result("UNSUPPORTED", "binding_template_mismatch")
    if not isinstance(source, str) or not isinstance(public_context, Mapping):
        return _result("UNSUPPORTED", "source_or_public_context_invalid")
    context = dict(public_context)
    if context == LIBSV_CONTEXT:
        return _libsv(source, context)
    if context == pulp.PUBLIC_CONTEXT:
        old = pulp.bind_spill_payload_v1({"binding_template": pulp.TEMPLATE}, source, context)
        branch = "pulp_delegate:"
        delegated_from = pulp.CONTRACT
    else:
        old = v6.bind_skid_payload_v6({"binding_template": v6.TEMPLATE}, source, context)
        branch = "v6_delegate:"
        delegated_from = v6.CONTRACT
    if old["status"] != "BOUND":
        return _result(old["status"], branch + old["reason"])
    witness = {**old["witness"], "proof_scope": TEMPLATE["proof_scope"],
               "delegated_from": delegated_from}
    return _result("BOUND", branch + old["reason"], witness=witness,
                   witness_digest=base._digest(witness))


def apply_bound_skid_payload_v7(asset: Mapping, source: str,
                                public_context: Mapping,
                                binding: Mapping) -> tuple[str, dict]:
    fresh = bind_skid_payload_v7(asset, source, public_context)
    if fresh.get("status") != "BOUND" or not isinstance(binding, Mapping) or dict(binding) != fresh:
        raise ValueError("v7 binding is stale, tampered, or unsupported")
    witness = fresh["witness"]
    delegate = witness.get("delegated_from")
    if delegate in (pulp.CONTRACT, v6.CONTRACT):
        module = pulp if delegate == pulp.CONTRACT else v6
        old = (module.bind_spill_payload_v1(
            {"binding_template": module.TEMPLATE}, source, public_context)
            if module is pulp else module.bind_skid_payload_v6(
                {"binding_template": module.TEMPLATE}, source, public_context))
        edited, receipt = (module.apply_bound_spill_payload_v1(
            {"binding_template": module.TEMPLATE}, source, public_context, old)
            if module is pulp else module.apply_bound_skid_payload_v6(
                {"binding_template": module.TEMPLATE}, source, public_context, old))
        return edited, {**receipt, "binding_contract": CONTRACT, "operator": OPERATOR,
                        "profile": PROFILE, "source_binding_rederived": True,
                        "delegated_from": delegate}
    start, stop = witness["rhs_span"]
    edited = source[:start] + witness["replacement_rhs"] + source[stop:]
    healthy = bind_skid_payload_v7(asset, edited, public_context)
    if healthy.get("reason") != "buffer_already_captures_input_payload":
        raise ValueError("v7 candidate did not become supported healthy structure")
    return edited, {"binding_contract": CONTRACT, "operator": OPERATOR,
                    "profile": PROFILE, "rewritten": 1,
                    "source_binding_rederived": True,
                    "before_source_sha256": base._source_sha(source),
                    "after_source_sha256": base._source_sha(edited),
                    "witness_digest": fresh["witness_digest"],
                    "proof_scope": TEMPLATE["proof_scope"]}


__all__ = ["CONTRACT", "OPERATOR", "PROFILE", "TEMPLATE", "LIBSV_CONTEXT",
           "bind_skid_payload_v7", "apply_bound_skid_payload_v7"]
