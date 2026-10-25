"""R5 DEV-only v5 source binding: v4 shapes plus one elastic skid-slot shape.

The new branch is intentionally bounded to the already-observed axis_skid
module. It reads only supplied buggy RTL and explicit public parameters,
proves one syntactic buffered-payload mismatch, and grants no functional,
Memory, held-out, or production authority. V4 remains immutable.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from tehm.rtl.verilog_parse import _balanced_end

from . import research_r5_skid_binding as v1
from . import research_r5_skid_binding_v3 as v3
from . import research_r5_skid_binding_v4 as v4


CONTRACT = "rtl_skid_temp_payload_binding_dev_v5"
OPERATOR = "rtl.SKID_TEMP_PAYLOAD_RESTORE_DEV_V5"
PROFILE = "rtl.skid.temp_payload.v5.dev"
TEMPLATE = {
    "contract": CONTRACT,
    "operator": OPERATOR,
    "profile": PROFILE,
    "proof_scope": "unique_syntactic_buffered_payload_mismatch_not_functional",
}
AXIS_SKID_CONTEXT = {"DATA_WIDTH": 8, "SKID_SLOTS": 1}
_IDENT = r"[A-Za-z_]\w*"


def _result(status: str, reason: str, **detail: object) -> dict:
    return {"contract": CONTRACT, "status": status, "reason": reason, **detail}


def _one(text: str, pattern: str) -> bool:
    return len(re.findall(pattern, text, re.MULTILINE)) == 1


def _block(text: str, pattern: str) -> tuple[int, int, str] | None:
    """Return exact begin/end scope; callers check pattern cardinality first."""
    match = re.search(pattern, text)
    if match is None:
        return None
    begin = text.rfind("begin", match.start(), match.end())
    end = _balanced_end(text, begin)
    if end == len(text) or text[end - 3:end] != "end":
        return None
    return begin, end, text[match.end():end - 3]


def _assignments(body: str) -> list[re.Match[str]]:
    return list(re.finditer(rf"\b(?P<lhs>{_IDENT})\s*<=\s*"
                            rf"(?P<rhs>[^;]+?)\s*;", body))


def _axis_skid(source: str, context: dict) -> dict:
    masked = v1._mask_comments(source)
    modules = list(re.finditer(r"\bmodule\s+([A-Za-z_]\w*)\b", masked))
    endings = list(re.finditer(r"\bendmodule\b", masked))
    if len(modules) != 1 or len(endings) != 1 or modules[0][1] != "axis_skid":
        return _result("NO_MATCH", "unique_axis_skid_module_required")
    if modules[0].start() >= endings[0].start():
        return _result("UNSUPPORTED", "module_boundary_invalid")
    directives = re.findall(r"(?m)^[ \t]*`([A-Za-z_]\w*)[ \t]*([^\n]*)", masked)
    if [(name, arg.strip()) for name, arg in directives] != [
            ("default_nettype", "none"), ("ifdef", "FORMAL"),
            ("endif", ""), ("default_nettype", "wire")]:
        return _result("UNSUPPORTED", "unexpected_preprocessor_directive")
    formal = re.search(r"(?m)^[ \t]*`ifdef[ \t]+FORMAL[ \t]*$", masked)
    if formal is None or not modules[0].start() < formal.start() < endings[0].start():
        return _result("UNSUPPORTED", "formal_boundary_missing")
    synth = masked[:formal.start()]
    if re.search(r"[\\\"]", synth):
        return _result("UNSUPPORTED", "unsupported_lexical_construct")
    required = {
        "module_header": r"\bmodule\s+axis_skid\s*\(",
        "input_payload_width": r"\binput\s+wire\s*\[\s*7\s*:\s*0\s*\]\s+s_tdata\b",
        "output_payload_width": r"\boutput\s+logic\s*\[\s*7\s*:\s*0\s*\]\s+m_tdata\b",
        "clock_port": r"\binput\s+wire\s+clk\b",
        "reset_port": r"\binput\s+wire\s+rst_n\b",
        "input_valid_port": r"\binput\s+wire\s+s_tvalid\b",
        "input_last_port": r"\binput\s+wire\s+s_tlast\b",
        "input_ready_port": r"\boutput\s+wire\s+s_tready\b",
        "output_valid_port": r"\boutput\s+logic\s+m_tvalid\b",
        "output_last_port": r"\boutput\s+logic\s+m_tlast\b",
        "output_ready_port": r"\binput\s+wire\s+m_tready\b",
        "buffer_width": r"\blogic\s*\[\s*7\s*:\s*0\s*\]\s+skid_data\s*;",
        "buffer_last": r"\blogic\s+skid_last\s*;",
        "state_type": r"\btypedef\s+enum\s+logic\s*\[\s*0\s*:\s*0\s*\]\s*\{\s*EMPTY\s*,\s*FULL\s*\}\s*state_t\s*;",
        "state_register": r"\bstate_t\s+state\s*;",
        "input_beat": r"\bwire\s+s_beat\s*=\s*s_tvalid\s*&&\s*s_tready\s*;",
        "output_beat": r"\bwire\s+m_beat\s*=\s*m_tvalid\s*&&\s*m_tready\s*;",
        "output_ready": r"\bwire\s+out_ready\s*=\s*!m_tvalid\s*\|\|\s*m_beat\s*;",
        "registered_ready": r"\bassign\s+s_tready\s*=\s*\(\s*state\s*==\s*EMPTY\s*\)\s*;",
        "reset_buffer": r"\bskid_data\s*<=\s*8'h00\s*;",
    }
    missing = [name for name, pattern in required.items() if not _one(synth, pattern)]
    if missing:
        return _result("UNSUPPORTED", "axis_skid_structural_witness_missing", missing=missing)
    if (len(re.findall(r"\bm_tdata\s*<=", synth)) != 3 or
            len(re.findall(r"\bm_tlast\s*<=", synth)) != 3 or
            len(re.findall(r"\bm_tvalid\s*<=", synth)) != 3 or
            len(re.findall(r"\bskid_data\s*<=", synth)) != 2 or
            len(re.findall(r"\bskid_last\s*<=", synth)) != 2 or
            len(re.findall(r"\bstate\s*<=", synth)) != 3):
        return _result("AMBIGUOUS", "unexpected_payload_or_state_write_cardinality")
    seq_pattern = r"\balways_ff\s*@\s*\(\s*posedge\s+clk\s*\)\s*begin\b"
    if len(re.findall(r"\balways(?:_ff|_comb)?\b", synth)) != 1:
        return _result("UNSUPPORTED", "additional_sequential_or_combinational_block")
    if not _one(synth, seq_pattern):
        return _result("UNSUPPORTED", "one_sequential_block_required")
    seq = _block(synth, seq_pattern)
    if seq is None:
        return _result("UNSUPPORTED", "sequential_block_unbalanced")
    seq_start, seq_end, _ = seq
    reset_pattern = r"\bif\s*\(\s*!rst_n\s*\)\s*begin\b"
    if not _one(synth, reset_pattern):
        return _result("UNSUPPORTED", "one_reset_branch_required")
    reset = _block(synth, reset_pattern)
    if reset is None:
        return _result("UNSUPPORTED", "reset_block_unbalanced")
    reset_start, reset_end, reset_body = reset
    if not (seq_start < reset_start < reset_end < seq_end):
        return _result("UNSUPPORTED", "reset_not_inside_sequential_block")
    if (not _one(reset_body, r"\bstate\s*<=\s*EMPTY\s*;") or
            not _one(reset_body, r"\bskid_data\s*<=\s*8'h00\s*;")):
        return _result("UNSUPPORTED", "reset_state_or_buffer_mismatch")
    out_pattern = r"\bif\s*\(\s*out_ready\s*\)\s*begin\b"
    full_pattern = r"\bif\s*\(\s*state\s*==\s*FULL\s*\)\s*begin\b"
    active_match = re.match(r"\s*else\s+begin\b", synth[reset_end:seq_end])
    if active_match is None:
        return _result("UNSUPPORTED", "reset_else_active_branch_missing")
    active_begin = reset_end + active_match.end() - len("begin")
    active_end = _balanced_end(synth, active_begin)
    if active_end > seq_end:
        return _result("UNSUPPORTED", "active_branch_unbalanced")
    if not _one(synth, out_pattern):
        return _result("AMBIGUOUS", "output_ready_branch_not_unique")
    if not _one(synth, full_pattern):
        return _result("AMBIGUOUS", "full_slot_branch_not_unique")
    out = _block(synth, out_pattern)
    full = _block(synth, full_pattern)
    if out is None or full is None:
        return _result("UNSUPPORTED", "data_path_block_unbalanced")
    out_start, out_end, _ = out
    full_start, full_end, full_body = full
    if not (seq_start < reset_start < reset_end < active_begin < out_start <
            full_start < full_end < out_end < active_end < seq_end):
        return _result("UNSUPPORTED", "full_slot_not_inside_output_ready")
    if not re.match(r"\s*else\s+begin\b", synth[full_end:out_end]):
        return _result("UNSUPPORTED", "normal_flow_branch_missing")
    normal_match = re.match(r"\s*else\s+begin\b", synth[full_end:out_end])
    normal_begin = full_end + normal_match.end() - len("begin")
    normal_end = _balanced_end(synth, normal_begin)
    if normal_end >= out_end:
        return _result("UNSUPPORTED", "normal_flow_block_unbalanced")
    normal_body = synth[normal_begin + len("begin"):normal_end - len("end")]
    if not re.match(r"\s*else\s+begin\b", synth[out_end:seq_end]):
        return _result("UNSUPPORTED", "stall_branch_missing")
    stall_match = re.match(r"\s*else\s+begin\b", synth[out_end:seq_end])
    stall_begin = out_end + stall_match.end() - len("begin")
    stall_end = _balanced_end(synth, stall_begin)
    if stall_end > seq_end:
        return _result("UNSUPPORTED", "stall_block_unbalanced")
    stall_body = synth[stall_begin + len("begin"):stall_end - len("end")]
    if not _one(stall_body, r"\bif\s*\(\s*s_beat\s*\)\s*begin\b"):
        return _result("UNSUPPORTED", "stall_capture_guard_missing")
    capture = _block(stall_body, r"\bif\s*\(\s*s_beat\s*\)\s*begin\b")
    if capture is None:
        return _result("UNSUPPORTED", "stall_capture_unbalanced")
    full_assign = _assignments(full_body)
    normal_assign = _assignments(normal_body)
    capture_assign = _assignments(capture[2])
    if (len(full_assign) != 4 or len(normal_assign) != 3 or
            len(capture_assign) != 3):
        return _result("AMBIGUOUS", "data_path_assignment_cardinality_mismatch")
    def pairs(matches: list[re.Match[str]]) -> dict[str, str]:
        return {m["lhs"]: re.sub(r"\s+", "", m["rhs"]) for m in matches}
    full_pairs = pairs(full_assign)
    if (len(full_pairs) != 4 or
            {key: full_pairs[key] for key in ("m_tlast", "m_tvalid", "state")
             if key in full_pairs} !=
            {"m_tlast": "skid_last", "m_tvalid": "1'b1", "state": "EMPTY"} or
            "m_tdata" not in full_pairs):
        return _result("UNSUPPORTED", "full_slot_metadata_or_state_mismatch")
    if pairs(normal_assign) != {
            "m_tdata": "s_tdata", "m_tlast": "s_tlast", "m_tvalid": "s_beat"}:
        return _result("UNSUPPORTED", "normal_flow_payload_mismatch")
    if pairs(capture_assign) != {
            "skid_data": "s_tdata", "skid_last": "s_tlast", "state": "FULL"}:
        return _result("UNSUPPORTED", "stall_capture_payload_mismatch")
    rhs = full_pairs["m_tdata"]
    if rhs == "skid_data":
        return _result("NO_MATCH", "payload_source_already_buffered_register")
    if rhs != "s_tdata":
        return _result("UNSUPPORTED", "unrecognized_full_slot_payload_source")
    target_matches = [m for m in full_assign if m["lhs"] == "m_tdata"]
    if len(target_matches) != 1:
        return _result("AMBIGUOUS", "full_slot_payload_write_not_unique")
    full_header = re.search(full_pattern, synth)
    rhs_start = full_header.end() + target_matches[0].start("rhs")
    rhs_end = full_header.end() + target_matches[0].end("rhs")
    if source[rhs_start:rhs_end].strip() != "s_tdata":
        return _result("UNSUPPORTED", "source_offset_does_not_replay")
    witness = {
        "shape": "axis_skid_occupied_slot_drain", "module": "axis_skid",
        "input_payload": "s_tdata", "buffer_payload": "skid_data",
        "output_payload": "m_tdata", "old_rhs": source[rhs_start:rhs_end],
        "replacement_rhs": "skid_data", "rhs_span": [rhs_start, rhs_end],
        "source_sha256": v1._source_sha(source), "public_context": context,
        "proof_scope": TEMPLATE["proof_scope"],
        "structural_requirements": sorted(required),
    }
    return _result("BOUND", "unique_occupied_skid_slot_payload_mismatch",
                   witness=witness, witness_digest=v1._digest(witness))


def bind_skid_payload_v5(asset: Mapping, source: str,
                         public_context: Mapping) -> dict:
    if not isinstance(asset, Mapping) or asset.get("binding_template") != TEMPLATE:
        return _result("UNSUPPORTED", "unfrozen_or_wrong_asset_template")
    if not isinstance(public_context, Mapping):
        return _result("UNSUPPORTED", "unsupported_or_extra_public_parameters")
    context = dict(public_context)
    if (context != AXIS_SKID_CONTEXT and context != v4.FETCH_CONTEXT and
            context not in v3.CONTEXTS.values()):
        return _result("UNSUPPORTED", "unsupported_or_extra_public_parameters")
    if not isinstance(source, str) or not source or len(source) > 2_000_000:
        return _result("UNSUPPORTED", "source_must_be_bounded_utf8_text")
    try:
        source.encode("utf-8")
    except UnicodeError:
        return _result("UNSUPPORTED", "source_not_utf8")
    if context == AXIS_SKID_CONTEXT:
        return _axis_skid(source, context)
    legacy = v4.bind_skid_payload_v4(
        {"binding_template": v4.TEMPLATE}, source, context)
    if legacy["status"] != "BOUND":
        return _result(legacy["status"], legacy["reason"])
    witness = {**legacy["witness"], "proof_scope": TEMPLATE["proof_scope"]}
    return _result("BOUND", legacy["reason"], witness=witness,
                   witness_digest=v1._digest(witness))


def apply_bound_skid_payload_v5(asset: Mapping, source: str,
                                public_context: Mapping,
                                binding: Mapping) -> tuple[str, dict]:
    fresh = bind_skid_payload_v5(asset, source, public_context)
    if fresh.get("status") != "BOUND" or not isinstance(binding, Mapping) or dict(binding) != fresh:
        raise ValueError("v5 binding is stale, tampered, or not uniquely supported")
    witness = fresh["witness"]
    start, end = witness["rhs_span"]
    edited = source[:start] + witness["replacement_rhs"] + source[end:]
    healthy_reason = (
        "payload_source_already_buffered_register"
        if witness["shape"] == "axis_skid_occupied_slot_drain"
        else "payload_source_already_buffered_slice"
        if witness["shape"] == "fetch_buffered_instruction"
        else "payload_source_already_r_data"
        if witness["shape"] == "zipcpu_registered"
        else "payload_source_already_temp_register")
    if (edited == source or
            bind_skid_payload_v5(asset, edited, public_context).get("reason") != healthy_reason):
        raise ValueError("v5 candidate did not become the supported healthy structure")
    return edited, {
        "contract": CONTRACT, "operator": OPERATOR,
        "before_sha256": v1._source_sha(source),
        "after_sha256": v1._source_sha(edited),
        "binding_digest": fresh["witness_digest"], "rewritten": 1,
        "functional_correctness": "not_asserted_requires_authoritative_oracle",
    }


__all__ = ["CONTRACT", "OPERATOR", "PROFILE", "TEMPLATE", "AXIS_SKID_CONTEXT",
           "bind_skid_payload_v5", "apply_bound_skid_payload_v5"]
