"""R5 DEV-only source-only binding for three v3 shapes plus one fetch shape.

The fetch branch is deliberately narrow. It does not inspect a clean source,
testbench, mutation manifest, oracle result, repository history, or filesystem.
It proves a unique syntactic mismatch only; functional correctness requires a
separate evaluator oracle. The observed ultraembedded task is DEV, not heldout.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from . import research_r5_skid_binding as v1
from . import research_r5_skid_binding_v3 as v3


CONTRACT = "rtl_skid_temp_payload_binding_dev_v4"
OPERATOR = "rtl.SKID_TEMP_PAYLOAD_RESTORE_DEV_V4"
PROFILE = "rtl.skid.temp_payload.v4.dev"
TEMPLATE = {
    "contract": CONTRACT,
    "operator": OPERATOR,
    "profile": PROFILE,
    "proof_scope": "unique_syntactic_temp_payload_mismatch_not_functional",
}
FETCH_CONTEXT = {"SUPPORT_MMU": 1}
_FETCH_MODULE = "riscv_fetch"
_FETCH_HEALTHY = "skid_buffer_q[31:0]"
_FETCH_FRESH = "icache_inst_i"
_FETCH_SELECT = re.compile(
    r"\bassign\s+fetch_instr_o\s*=\s*skid_valid_q\s*\?\s*"
    r"(?P<rhs>[A-Za-z_][A-Za-z_0-9]*(?:\s*\[\s*\d+\s*:\s*\d+\s*\])?)"
    r"\s*:\s*icache_inst_i\s*;"
)


def _result(status: str, reason: str, **detail: object) -> dict:
    return {"contract": CONTRACT, "status": status, "reason": reason, **detail}


def _one(text: str, pattern: str) -> bool:
    return len(re.findall(pattern, text, re.MULTILINE)) == 1


def _fetch(source: str, context: dict) -> dict:
    text = v1._mask_comments(source)
    modules = list(re.finditer(r"\bmodule\s+(\w+)\b", text))
    endings = list(re.finditer(r"\bendmodule\b", text))
    if len(modules) != 1 or len(endings) != 1 or modules[0].group(1) != _FETCH_MODULE:
        return _result("NO_MATCH", "unique_fetch_module_required")
    if modules[0].start() >= endings[0].start():
        return _result("UNSUPPORTED", "module_boundary_invalid")
    directives = re.findall(r"(?m)^[ \t]*`([A-Za-z_]\w*)[ \t]*(.*)$", text)
    if [(name, argument.strip()) for name, argument in directives] != [
            ("include", '"riscv_defs.v"')]:
        return _result("UNSUPPORTED", "unexpected_fetch_preprocessor_directive")
    if not _one(text, r"\bparameter\s+SUPPORT_MMU\s*=\s*1\b"):
        return _result("UNSUPPORTED", "fetch_parameter_declaration_mismatch")
    # All witnesses are in the public buggy source. The structural proof is
    # intentionally stronger than matching the defective output line alone.
    required = {
        "output_width": r"\boutput\s*\[\s*31\s*:\s*0\s*\]\s+fetch_instr_o\b",
        "fresh_width": r"\binput\s*\[\s*31\s*:\s*0\s*\]\s+icache_inst_i\b",
        "buffer_width": r"\breg\s*\[\s*65\s*:\s*0\s*\]\s+skid_buffer_q\s*;",
        "buffer_valid": r"\breg\s+skid_valid_q\s*;",
        "backpressure_guard": r"\belse\s+if\s*\(\s*fetch_valid_o\s*&&\s*!fetch_accept_i\s*\)",
        "valid_capture": r"\bskid_valid_q\s*<=\s*1'b1\s*;",
        "payload_capture": r"\bskid_buffer_q\s*<=\s*\{\s*fetch_fault_page_o\s*,\s*fetch_fault_fetch_o\s*,\s*fetch_pc_o\s*,\s*fetch_instr_o\s*\}\s*;",
        "capture_block": r"\belse\s+if\s*\(\s*fetch_valid_o\s*&&\s*!fetch_accept_i\s*\)\s*begin\s*skid_valid_q\s*<=\s*1'b1\s*;\s*skid_buffer_q\s*<=\s*\{\s*fetch_fault_page_o\s*,\s*fetch_fault_fetch_o\s*,\s*fetch_pc_o\s*,\s*fetch_instr_o\s*\}\s*;\s*end\b",
        "valid_select": r"\bassign\s+fetch_valid_o\s*=\s*\(\s*icache_valid_i\s*\|\|\s*skid_valid_q\s*\)\s*&\s*!fetch_resp_drop_w\s*;",
        "pc_select": r"\bassign\s+fetch_pc_o\s*=\s*skid_valid_q\s*\?\s*skid_buffer_q\s*\[\s*63\s*:\s*32\s*\]\s*:\s*\{\s*pc_d_q\s*\[\s*31\s*:\s*2\s*\]\s*,\s*2'b0\s*\}\s*;",
        "fetch_fault_select": r"\bassign\s+fetch_fault_fetch_o\s*=\s*skid_valid_q\s*\?\s*skid_buffer_q\s*\[\s*64\s*\]\s*:\s*icache_error_i\s*;",
        "page_fault_select": r"\bassign\s+fetch_fault_page_o\s*=\s*skid_valid_q\s*\?\s*skid_buffer_q\s*\[\s*65\s*\]\s*:\s*icache_page_fault_i\s*;",
    }
    missing = [name for name, pattern in required.items() if not _one(text, pattern)]
    if missing:
        return _result("UNSUPPORTED", "fetch_structural_witness_missing", missing=missing)
    if (len(re.findall(r"\bskid_buffer_q\s*<=", text)) != 3 or
            len(re.findall(r"\bskid_valid_q\s*<=", text)) != 3 or
            len(re.findall(r"\bskid_buffer_q\s*<=\s*66'b0\s*;", text)) != 2 or
            len(re.findall(r"\bskid_valid_q\s*<=\s*1'b0\s*;", text)) != 2):
        return _result("UNSUPPORTED", "fetch_buffer_write_cardinality_mismatch")

    if not _one(text, r"\bassign\s+fetch_instr_o\s*="):
        return _result("AMBIGUOUS", "fetch_instruction_assignment_not_unique")
    matches = list(_FETCH_SELECT.finditer(text))
    if len(matches) != 1:
        return _result("NO_MATCH", "fetch_instruction_select_shape_absent")
    rhs = matches[0].group("rhs")
    normalized = re.sub(r"\s+", "", rhs)
    if normalized == _FETCH_HEALTHY:
        return _result("NO_MATCH", "payload_source_already_buffered_slice")
    if normalized != _FETCH_FRESH:
        return _result("UNSUPPORTED", "unrecognized_fetch_payload_source")
    start, end = matches[0].span("rhs")
    if source[start:end] != rhs or source[start:end].strip() != _FETCH_FRESH:
        return _result("UNSUPPORTED", "source_offset_does_not_replay")
    witness = {
        "shape": "fetch_buffered_instruction", "module": _FETCH_MODULE,
        "old_rhs": rhs, "replacement_rhs": _FETCH_HEALTHY,
        "rhs_span": [start, end], "source_sha256": v1._source_sha(source),
        "public_context": context, "proof_scope": TEMPLATE["proof_scope"],
        "structural_requirements": sorted(required),
    }
    return _result("BOUND", "unique_fetch_buffered_payload_source_mismatch",
                   witness=witness, witness_digest=v1._digest(witness))


def bind_skid_payload_v4(asset: Mapping, source: str,
                         public_context: Mapping) -> dict:
    if not isinstance(asset, Mapping) or asset.get("binding_template") != TEMPLATE:
        return _result("UNSUPPORTED", "unfrozen_or_wrong_asset_template")
    if not isinstance(public_context, Mapping):
        return _result("UNSUPPORTED", "unsupported_or_extra_public_parameters")
    context = dict(public_context)
    if context != FETCH_CONTEXT and context not in v3.CONTEXTS.values():
        return _result("UNSUPPORTED", "unsupported_or_extra_public_parameters")
    if not isinstance(source, str) or not source or len(source) > 2_000_000:
        return _result("UNSUPPORTED", "source_must_be_bounded_utf8_text")
    try:
        source.encode("utf-8")
    except UnicodeError:
        return _result("UNSUPPORTED", "source_not_utf8")
    if context == FETCH_CONTEXT:
        return _fetch(source, context)
    legacy = v3.bind_skid_payload_v3({"binding_template": v3.TEMPLATE}, source, context)
    if legacy["status"] != "BOUND":
        return _result(legacy["status"], legacy["reason"])
    witness = {**legacy["witness"], "proof_scope": TEMPLATE["proof_scope"]}
    return _result("BOUND", legacy["reason"], witness=witness,
                   witness_digest=v1._digest(witness))


def apply_bound_skid_payload_v4(asset: Mapping, source: str,
                                public_context: Mapping,
                                binding: Mapping) -> tuple[str, dict]:
    fresh = bind_skid_payload_v4(asset, source, public_context)
    if fresh.get("status") != "BOUND" or not isinstance(binding, Mapping) or dict(binding) != fresh:
        raise ValueError("binding is stale, tampered, or not uniquely supported")
    witness = fresh["witness"]
    start, end = witness["rhs_span"]
    edited = source[:start] + witness["replacement_rhs"] + source[end:]
    healthy_reason = ("payload_source_already_buffered_slice"
                      if witness["shape"] == "fetch_buffered_instruction" else
                      "payload_source_already_r_data" if witness["shape"] == "zipcpu_registered" else
                      "payload_source_already_temp_register")
    if (edited == source or
            bind_skid_payload_v4(asset, edited, public_context).get("reason") != healthy_reason):
        raise ValueError("candidate did not become the supported healthy structure")
    return edited, {"contract": CONTRACT, "operator": OPERATOR,
                    "before_sha256": v1._source_sha(source),
                    "after_sha256": v1._source_sha(edited),
                    "binding_digest": fresh["witness_digest"], "rewritten": 1,
                    "functional_correctness": "not_asserted_requires_authoritative_oracle"}


__all__ = ["CONTRACT", "OPERATOR", "PROFILE", "TEMPLATE", "FETCH_CONTEXT",
           "bind_skid_payload_v4", "apply_bound_skid_payload_v4"]
