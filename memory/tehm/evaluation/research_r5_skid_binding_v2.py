"""R5 DEV generation 2: source-only binding across two skid payload shapes.

This is not a production action or evidence of Memory transfer.  The caller
provides only the selected draft Asset, buggy RTL text, and public parameters.
No filesystem, testbench, mutant recipe, oracle, or answer path is consulted.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from tehm.rtl.verilog_parse import _balanced_end

from . import research_r5_skid_binding as v1


CONTRACT = "rtl_skid_temp_payload_binding_dev_v2"
OPERATOR = "rtl.SKID_TEMP_PAYLOAD_RESTORE_DEV_V2"
PROFILE = "rtl.skid.temp_payload.v2.dev"
TEMPLATE = {"contract": CONTRACT, "operator": OPERATOR, "profile": PROFILE,
            "proof_scope": "unique_syntactic_temp_payload_mismatch_not_functional"}
CONTEXTS = {"register": {"DATA_WIDTH": 8, "REG_TYPE": 2},
            "broadcast": {"DATA_WIDTH": 8, "M_COUNT": 2}}
_BROADCAST_OUTPUT = r"\bassign\s+m_axis_tdata\s*=\s*\{\s*M_COUNT\s*\{\s*m_axis_tdata_reg\s*\}\s*\}\s*;"


def _result(status: str, reason: str, **detail: object) -> dict:
    return {"contract": CONTRACT, "status": status, "reason": reason, **detail}


def _broadcast(source: str, public_context: Mapping) -> dict:
    text = v1._mask_comments(source)
    modules = list(re.finditer(rf"\bmodule\s+(?P<name>{v1.IDENT})\b", text))
    ends = list(re.finditer(r"\bendmodule\b", text))
    if len(modules) != 1 or len(ends) != 1 or modules[0].start() >= ends[0].start():
        return _result("UNSUPPORTED", "exactly_one_module_required")
    start, end = modules[0].start(), ends[0].end()
    body = text[start:end]
    if re.search(r"[`\"\\]", body):
        return _result("UNSUPPORTED", "unsupported_lexical_construct")
    if len(re.findall(_BROADCAST_OUTPUT, body)) != 1:
        return _result("NO_MATCH", "broadcast_output_shape_absent_or_repeated")
    if re.search(r"\bREG_TYPE\b", body):
        return _result("UNSUPPORTED", "mixed_register_and_broadcast_shapes")
    required = (
        r"\breg\s*\[[^\]]+\]\s+m_axis_tdata_reg\b",
        r"\breg\s*\[[^\]]+\]\s+temp_m_axis_tdata_reg\b",
        r"\bstore_axis_temp_to_output\s*=\s*1'b1\s*;",
    )
    if (any(len(re.findall(pattern, body)) != 1 for pattern in required) or
            any(re.search(rf"\b{name}\b", body) is None for name in
                ("temp_m_axis_tvalid_reg", "m_axis_tvalid_reg"))):
        return _result("NO_MATCH", "required_skid_structure_absent_or_repeated")
    widths = [re.search(rf"\breg\s*(\[[^\]]+\])\s+{name}\b", body)
              for name in ("m_axis_tdata_reg", "temp_m_axis_tdata_reg")]
    if any(match is None for match in widths) or widths[0][1] != widths[1][1]:
        return _result("UNSUPPORTED", "payload_storage_width_mismatch")
    sequential = []
    for match in re.finditer(
            rf"\balways\s*@\s*\(\s*posedge\s+(?P<clock>{v1.IDENT})\s*\)\s*begin\b", body):
        begin = body.rfind("begin", match.start(), match.end())
        block_end = _balanced_end(body, begin)
        if block_end < len(body) and body[block_end-3:block_end] == "end":
            sequential.append((start + match.start(), body[match.start():block_end],
                               match["clock"]))
    if len(sequential) != 1:
        return _result("UNSUPPORTED", "one_positive_edge_sequential_block_required")
    seq_start, seq, clock = sequential[0]
    direct = v1._branch(seq, "store_axis_input_to_output")
    transfer = v1._branch(seq, "store_axis_temp_to_output")
    capture = v1._branch(seq, "store_axis_input_to_temp")
    if any(len(branches) > 1 for branches in (direct, transfer, capture)):
        return _result("AMBIGUOUS", "multiple_data_path_branches")
    if not all((direct, transfer, capture)):
        return _result("NO_MATCH", "data_path_branch_missing")
    if not direct[0][0] < transfer[0][0] < capture[0][0]:
        return _result("UNSUPPORTED", "unexpected_branch_order")
    assignments = [v1._assignment(branch[0][2], lhs) for branch, lhs in (
        (direct, "m_axis_tdata_reg"), (transfer, "m_axis_tdata_reg"),
        (capture, "temp_m_axis_tdata_reg"))]
    if any(len(matches) > 1 for matches in assignments):
        return _result("AMBIGUOUS", "multiple_payload_assignments")
    if not all(assignments):
        return _result("NO_MATCH", "payload_assignment_missing")
    if assignments[0][0]["rhs"] != "s_axis_tdata" or assignments[2][0]["rhs"] != "s_axis_tdata":
        return _result("UNSUPPORTED", "input_or_temp_capture_not_direct")
    for field in ("tkeep", "tlast"):
        if not re.search(rf"\bm_axis_{field}_reg\s*<=\s*temp_m_axis_{field}_reg\s*;",
                         transfer[0][2]):
            return _result("UNSUPPORTED", f"temp_{field}_witness_missing")
    old_rhs = assignments[1][0]["rhs"]
    if old_rhs == "temp_m_axis_tdata_reg":
        return _result("NO_MATCH", "payload_source_already_temp_register")
    if old_rhs != "s_axis_tdata":
        return _result("UNSUPPORTED", "unrecognized_payload_source_expression")
    opening = re.search(r"\bbegin\b", seq[transfer[0][0]:transfer[0][1]])
    if opening is None:
        return _result("UNSUPPORTED", "transfer_branch_unbalanced")
    rhs_start = seq_start + transfer[0][0] + opening.end() + assignments[1][0].start("rhs")
    rhs_end = rhs_start + len(old_rhs)
    if source[rhs_start:rhs_end] != old_rhs:
        return _result("UNSUPPORTED", "source_offset_does_not_replay")
    witness = {"shape": "broadcast", "module": modules[0]["name"], "clock": clock,
               "output_register": "m_axis_tdata_reg", "temp_register": "temp_m_axis_tdata_reg",
               "input_payload": "s_axis_tdata", "old_rhs": old_rhs,
               "replacement_rhs": "temp_m_axis_tdata_reg", "rhs_span": [rhs_start, rhs_end],
               "source_sha256": v1._source_sha(source),
               "public_context": dict(public_context),
               "proof_scope": TEMPLATE["proof_scope"]}
    return _result("BOUND", "unique_temp_payload_source_mismatch",
                   witness=witness, witness_digest=v1._digest(witness))


def bind_skid_payload_v2(asset: Mapping, source: str, public_context: Mapping) -> dict:
    """Return a source-derived witness or fail closed within the v2 DEV contract."""
    if not isinstance(asset, Mapping) or asset.get("binding_template") != TEMPLATE:
        return _result("UNSUPPORTED", "unfrozen_or_wrong_asset_template")
    if not isinstance(public_context, Mapping):
        return _result("UNSUPPORTED", "unsupported_or_extra_public_parameters")
    context = dict(public_context)
    if context not in CONTEXTS.values():
        return _result("UNSUPPORTED", "unsupported_or_extra_public_parameters")
    if not isinstance(source, str) or not source or len(source) > 2_000_000:
        return _result("UNSUPPORTED", "source_must_be_bounded_utf8_text")
    try:
        source.encode("utf-8")
    except UnicodeError:
        return _result("UNSUPPORTED", "source_not_utf8")
    if context == CONTEXTS["register"]:
        legacy = v1.bind_skid_payload({"binding_template": v1.TEMPLATE}, source, context)
        if legacy["status"] != "BOUND":
            return _result(legacy["status"], legacy["reason"])
        witness = {**legacy["witness"], "shape": "register"}
        return _result("BOUND", legacy["reason"], witness=witness,
                       witness_digest=v1._digest(witness))
    return _broadcast(source, context)


def apply_bound_skid_payload_v2(asset: Mapping, source: str,
                                public_context: Mapping, binding: Mapping) -> tuple[str, dict]:
    """Rebind exact source bytes and perform exactly one witnessed RHS edit."""
    fresh = bind_skid_payload_v2(asset, source, public_context)
    if fresh.get("status") != "BOUND" or not isinstance(binding, Mapping) or dict(binding) != fresh:
        raise ValueError("binding is stale, tampered, or not uniquely supported")
    witness = fresh["witness"]
    start, end = witness["rhs_span"]
    edited = source[:start] + witness["replacement_rhs"] + source[end:]
    if (edited == source or
            bind_skid_payload_v2(asset, edited, public_context).get("reason") !=
            "payload_source_already_temp_register"):
        raise ValueError("candidate did not become the supported healthy structure")
    return edited, {"contract": CONTRACT, "operator": OPERATOR,
                    "before_sha256": v1._source_sha(source),
                    "after_sha256": v1._source_sha(edited),
                    "binding_digest": fresh["witness_digest"], "rewritten": 1,
                    "functional_correctness": "not_asserted_requires_native_oracle"}
