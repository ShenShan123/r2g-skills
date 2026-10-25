"""R5 DEV-only v6 source binding for a bounded state-machine skid drain.

The new branch is developed on an observed independent-repository source.
It is not a transferred repair, Memory Asset, or production authority.  The
old v5 branch is delegated without changing its definition or test surface.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from tehm.rtl.verilog_parse import _balanced_end

from . import research_r5_skid_binding as v1
from . import research_r5_skid_binding_v5 as v5


CONTRACT = "rtl_skid_temp_payload_binding_dev_v6"
OPERATOR = "rtl.SKID_TEMP_PAYLOAD_RESTORE_DEV_V6"
PROFILE = "rtl.skid.temp_payload.v6.dev"
TEMPLATE = {
    "contract": CONTRACT,
    "operator": OPERATOR,
    "profile": PROFILE,
    "proof_scope": "unique_syntactic_buffered_drain_mismatch_not_functional",
}
STATE_SKID_CONTEXT = {"WIDTH": 8}
_IDENT = r"[A-Za-z_]\w*"


def _result(status: str, reason: str, **detail: object) -> dict:
    return {"contract": CONTRACT, "status": status, "reason": reason, **detail}


def _one(text: str, pattern: str) -> re.Match[str] | None:
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    return matches[0] if len(matches) == 1 else None


def _state_skid(source: str, context: dict) -> dict:
    masked = v1._mask_comments(source)
    modules = list(re.finditer(r"\bmodule\s+([A-Za-z_]\w*)\b", masked))
    endings = list(re.finditer(r"\bendmodule\b", masked))
    if len(modules) != 1 or len(endings) != 1:
        return _result("NO_MATCH", "unique_module_required")
    if modules[0].start() >= endings[0].start():
        return _result("UNSUPPORTED", "module_boundary_invalid")
    preamble = re.sub(r"(?m)^[ \t]*`timescale[^\n]*", "", masked[:modules[0].start()])
    if preamble.strip() or masked[endings[0].end():].strip():
        return _result("UNSUPPORTED", "code_outside_unique_module")
    directives = re.findall(r"(?m)^[ \t]*`([A-Za-z_]\w*)[ \t]*([^\n]*)", masked)
    if any(name != "timescale" for name, _ in directives) or len(directives) > 1:
        return _result("UNSUPPORTED", "preprocessor_directive_not_supported")
    if re.search(r"[\\\"]", masked):
        return _result("UNSUPPORTED", "unsupported_lexical_construct")
    header = _one(masked, rf"\bmodule\s+{modules[0][1]}\s*#\s*\(")
    if header is None:
        return _result("NO_MATCH", "parameterized_state_skid_module_not_found")
    if _one(masked, r"\bparameter\s+WIDTH\s*=\s*8\b") is None:
        return _result("UNSUPPORTED", "width_parameter_contract_missing")
    input_port = _one(masked, rf"\binput\s+logic\s*\[\s*WIDTH\s*-\s*1\s*:\s*0\s*\]\s*(?P<name>{_IDENT})\b")
    output_port = _one(masked, rf"\boutput\s+logic\s*\[\s*WIDTH\s*-\s*1\s*:\s*0\s*\]\s*(?P<name>{_IDENT})\b")
    if input_port is None or output_port is None:
        return _result("UNSUPPORTED", "unique_payload_ports_required")
    incoming, outgoing = input_port["name"], output_port["name"]
    if incoming == outgoing:
        return _result("UNSUPPORTED", "payload_ports_alias")
    controls = [
        r"\binput\s+logic\s+clk\s*,\s*rstn\s*,\s*s_valid\s*,\s*m_ready\b",
        r"\boutput\s+logic\s+m_valid\s*,\s*s_ready\b",
        r"\benum\s*\{\s*EMPTY\s*,\s*PARTIAL\s*,\s*FULL\s*\}\s*state\s*,\s*state_next\s*;",
        r"\bstate_next\s*=\s*state\s*;",
        r"\bm_valid\s*<=\s*state_next\s*!=\s*EMPTY\s*;",
        r"\bs_ready\s*<=\s*state_next\s*!=\s*FULL\s*;",
        r"\bEMPTY\s*:\s*if\s*\(\s*s_valid\s*\)\s*state_next\s*=\s*PARTIAL\s*;",
        r"\bPARTIAL\s*:\s*if\s*\(\s*!m_ready\s*&&\s*s_valid\s*\)\s*state_next\s*=\s*FULL\s*;",
        r"\bFULL\s*:\s*if\s*\(\s*m_ready\s*\)\s*state_next\s*=\s*PARTIAL\s*;",
    ]
    missing = [index for index, pattern in enumerate(controls) if _one(masked, pattern) is None]
    if missing:
        return _result("UNSUPPORTED", "state_or_handshake_witness_missing", missing=missing)
    if (len(re.findall(r"\balways(?:_comb|_ff)?\b", masked)) != 3 or
            len(re.findall(r"\balways_comb\b", masked)) != 1 or
            len(re.findall(r"\balways\s*@\s*\(\s*posedge\s+clk\s*\)", masked)) != 2):
        return _result("UNSUPPORTED", "unexpected_always_block_structure")
    if (len(re.findall(r"\bstate_next\s*=(?!=)", masked)) != 5 or
            len(re.findall(r"\bstate\s*<=", masked)) != 2 or
            len(re.findall(r"\bm_valid\s*<=", masked)) != 1 or
            len(re.findall(r"\bs_ready\s*<=", masked)) != 1):
        return _result("AMBIGUOUS", "state_or_handshake_write_cardinality_mismatch")
    capture_pattern = (rf"\bif\s*\(\s*state\s*==\s*PARTIAL\s*&&\s*"
                       rf"state_next\s*==\s*FULL\s*\)\s*"
                       rf"(?P<buffer>{_IDENT})\s*<=\s*(?P<src>{_IDENT})\s*;")
    capture = _one(masked, capture_pattern)
    if capture is None:
        return _result("AMBIGUOUS", "unique_capture_guard_required")
    buffer = capture["buffer"]
    if capture["src"] != incoming or buffer in {incoming, outgoing}:
        return _result("UNSUPPORTED", "capture_does_not_store_incoming_payload")
    if _one(masked, rf"\blogic\s*\[\s*WIDTH\s*-\s*1\s*:\s*0\s*\]\s+{buffer}\s*;") is None:
        return _result("UNSUPPORTED", "buffer_width_or_declaration_mismatch")
    reset = (rf"\bif\s*\(\s*!rstn\s*\)\s*\{{\s*m_valid\s*,\s*s_ready\s*,\s*"
             rf"{buffer}\s*,\s*{outgoing}\s*\}}\s*<=\s*0\s*;")
    active = _one(masked, reset + r"\s*else\s+begin\b")
    if active is None:
        return _result("UNSUPPORTED", "reset_and_active_data_path_missing")
    begin = masked.rfind("begin", active.start(), active.end())
    end = _balanced_end(masked, begin)
    if begin < 0 or end == len(masked) or masked[end - 3:end] != "end":
        return _result("UNSUPPORTED", "active_data_path_unbalanced")
    if not (begin < capture.start() < capture.end() < end):
        return _result("UNSUPPORTED", "capture_outside_active_data_path")
    normal_empty = (rf"\bEMPTY\s*:\s*if\s*\(\s*state_next\s*==\s*PARTIAL\s*\)\s*"
                    rf"{outgoing}\s*<=\s*{incoming}\s*;")
    normal_partial = (rf"\bPARTIAL\s*:\s*if\s*\(\s*m_ready\s*&&\s*s_valid\s*\)\s*"
                      rf"{outgoing}\s*<=\s*{incoming}\s*;")
    drain_pattern = (rf"\bFULL\s*:\s*if\s*\(\s*m_ready\s*\)\s*"
                     rf"(?P<out>{_IDENT})\s*<=\s*(?P<rhs>{_IDENT})\s*;")
    drain = _one(masked, drain_pattern)
    if drain is None:
        return _result("AMBIGUOUS", "unique_full_drain_required")
    normal_empty_match = _one(masked, normal_empty)
    normal_partial_match = _one(masked, normal_partial)
    output_cases = [match for match in re.finditer(
        r"\bunique\s+case\s*\(\s*state\s*\)", masked)
        if begin < match.start() < end]
    if len(output_cases) != 1:
        return _result("UNSUPPORTED", "unique_output_case_required")
    case_start = masked.find("case", output_cases[0].start(), output_cases[0].end())
    case_end = _balanced_end(masked, case_start)
    if (case_end >= end or masked[case_end - 7:case_end] != "endcase" or
            capture.end() >= case_start or normal_empty_match is None or
            normal_partial_match is None or
            not all(case_start < item.start() < item.end() < case_end
                    for item in (normal_empty_match, normal_partial_match, drain))):
        return _result("UNSUPPORTED", "output_case_relationship_mismatch")
    if drain["out"] != outgoing or not (begin < drain.start() < drain.end() < end):
        return _result("UNSUPPORTED", "normal_or_full_drain_relationship_mismatch")
    if (len(re.findall(rf"\b{outgoing}\s*<=", masked)) != 3 or
            len(re.findall(rf"\b{buffer}\s*<=", masked)) != 1):
        return _result("AMBIGUOUS", "payload_write_cardinality_mismatch")
    rhs = drain["rhs"]
    if rhs == buffer:
        return _result("NO_MATCH", "full_drain_already_uses_buffered_payload")
    if rhs != incoming:
        return _result("UNSUPPORTED", "unrecognized_full_drain_payload_source")
    start, stop = drain.span("rhs")
    if source[start:stop] != incoming:
        return _result("UNSUPPORTED", "source_offset_does_not_replay")
    witness = {
        "shape": "state_machine_full_slot_drain",
        "module": modules[0][1],
        "input_payload": incoming,
        "buffer_payload": buffer,
        "output_payload": outgoing,
        "old_rhs": source[start:stop],
        "replacement_rhs": buffer,
        "rhs_span": [start, stop],
        "source_sha256": v1._source_sha(source),
        "public_context": context,
        "proof_scope": TEMPLATE["proof_scope"],
        "structural_requirements": "single_state_machine_single_full_drain_exact_relations_v1",
    }
    return _result("BOUND", "unique_state_machine_buffered_drain_mismatch",
                   witness=witness, witness_digest=v1._digest(witness))


def bind_skid_payload_v6(asset: Mapping, source: str,
                         public_context: Mapping) -> dict:
    if not isinstance(asset, Mapping) or asset.get("binding_template") != TEMPLATE:
        return _result("UNSUPPORTED", "binding_template_mismatch")
    if not isinstance(source, str) or not isinstance(public_context, Mapping):
        return _result("UNSUPPORTED", "source_or_public_context_invalid")
    context = dict(public_context)
    if context == STATE_SKID_CONTEXT:
        return _state_skid(source, context)
    if context not in [v5.AXIS_SKID_CONTEXT, v5.v4.FETCH_CONTEXT,
                       *v5.v3.CONTEXTS.values()]:
        return _result("UNSUPPORTED", "unsupported_or_extra_public_parameters")
    old = v5.bind_skid_payload_v5({"binding_template": v5.TEMPLATE}, source, context)
    if old["status"] != "BOUND":
        return _result(old["status"], "v5_delegate:" + old["reason"])
    witness = {**old["witness"], "proof_scope": TEMPLATE["proof_scope"],
               "delegated_from": v5.CONTRACT}
    return _result("BOUND", "v5_delegate:" + old["reason"],
                   witness=witness, witness_digest=v1._digest(witness))


def apply_bound_skid_payload_v6(asset: Mapping, source: str,
                                public_context: Mapping, binding: Mapping) -> tuple[str, dict]:
    fresh = bind_skid_payload_v6(asset, source, public_context)
    if (fresh.get("status") != "BOUND" or not isinstance(binding, Mapping) or
            dict(binding) != fresh):
        raise ValueError("v6 binding is stale, tampered, or not uniquely supported")
    witness = fresh["witness"]
    if witness.get("delegated_from") == v5.CONTRACT:
        old = v5.bind_skid_payload_v5(
            {"binding_template": v5.TEMPLATE}, source, public_context)
        edited, receipt = v5.apply_bound_skid_payload_v5(
            {"binding_template": v5.TEMPLATE}, source, public_context, old)
        return edited, {**receipt, "binding_contract": CONTRACT,
                        "source_binding_rederived": True,
                        "delegated_from": v5.CONTRACT}
    start, stop = witness["rhs_span"]
    replacement = witness["replacement_rhs"]
    edited = source[:start] + replacement + source[stop:]
    healthy = bind_skid_payload_v6(asset, edited, public_context)
    if healthy.get("reason") != "full_drain_already_uses_buffered_payload":
        raise ValueError("v6 candidate did not become healthy supported structure")
    return edited, {
        "binding_contract": CONTRACT,
        "operator": OPERATOR,
        "profile": PROFILE,
        "rewritten": 1,
        "source_binding_rederived": True,
        "before_source_sha256": v1._source_sha(source),
        "after_source_sha256": v1._source_sha(edited),
        "witness_digest": fresh["witness_digest"],
        "proof_scope": TEMPLATE["proof_scope"],
    }


__all__ = ["CONTRACT", "OPERATOR", "PROFILE", "TEMPLATE",
           "STATE_SKID_CONTEXT", "bind_skid_payload_v6", "apply_bound_skid_payload_v6"]
