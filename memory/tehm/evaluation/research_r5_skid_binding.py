"""R5 DEV-only, source-only skid-buffer payload binding and edit prototype.

This module is deliberately outside the production RTL action registry. It
recognizes one bounded structural family and proves only a syntactic mismatch.
It neither reads files nor judges functional correctness or Memory authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from tehm.rtl.verilog_parse import _balanced_end


CONTRACT = "rtl_skid_temp_payload_binding_dev_v1"
OPERATOR = "rtl.SKID_TEMP_PAYLOAD_RESTORE_DEV"
PROFILE = "rtl.skid.temp_payload.v1.dev"
CONTEXT = {"DATA_WIDTH": 8, "REG_TYPE": 2}
TEMPLATE = {"contract": CONTRACT, "operator": OPERATOR, "profile": PROFILE,
            "proof_scope": "unique_syntactic_temp_payload_mismatch_not_functional"}
IDENT = r"[A-Za-z_]\w*"
_UNIQUE = (
    r"\bassign\s+m_axis_tdata\s*=\s*m_axis_tdata_reg\s*;",
    r"\breg\s*\[[^\]]+\]\s+m_axis_tdata_reg\b",
    r"\breg\s*\[[^\]]+\]\s+temp_m_axis_tdata_reg\b",
    r"\bstore_axis_temp_to_output\s*=\s*1'b1\s*;",
)
_PRESENT = (r"\btemp_m_axis_tvalid_reg\b", r"\bm_axis_tvalid_reg\b")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _source_sha(source: str) -> str:
    return "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def _mask_comments(source: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*",
                  lambda match: "".join("\n" if c == "\n" else " " for c in match[0]),
                  source, flags=re.S)


def _result(status: str, reason: str, **detail: object) -> dict:
    return {"contract": CONTRACT, "status": status, "reason": reason, **detail}


def _branch(text: str, name: str) -> list[tuple[int, int, str]]:
    pattern = rf"\b(?:else\s+)?if\s*\(\s*{re.escape(name)}\s*\)\s*begin\b"
    found = []
    for match in re.finditer(pattern, text):
        begin = text.rfind("begin", match.start(), match.end())
        end = _balanced_end(text, begin)
        if end == len(text) or not text[end-3:end] == "end":
            continue
        found.append((match.start(), end, text[match.end():end-3]))
    return found


def _assignment(branch: str, lhs: str) -> list[re.Match[str]]:
    return list(re.finditer(rf"\b{re.escape(lhs)}\s*<=\s*(?P<rhs>{IDENT})\s*;", branch))


def bind_skid_payload(asset: Mapping, source: str, public_context: Mapping) -> dict:
    """Return BOUND/NO_MATCH/AMBIGUOUS/UNSUPPORTED from permitted text only."""
    if not isinstance(asset, Mapping) or asset.get("binding_template") != TEMPLATE:
        return _result("UNSUPPORTED", "unfrozen_or_wrong_asset_template")
    if not isinstance(public_context, Mapping) or dict(public_context) != CONTEXT:
        return _result("UNSUPPORTED", "unsupported_or_extra_public_parameters")
    if not isinstance(source, str) or not source or len(source) > 2_000_000:
        return _result("UNSUPPORTED", "source_must_be_bounded_utf8_text")
    try:
        source.encode("utf-8")
    except UnicodeError:
        return _result("UNSUPPORTED", "source_not_utf8")
    text = _mask_comments(source)
    modules = list(re.finditer(rf"\bmodule\s+(?P<name>{IDENT})\b", text))
    ends = list(re.finditer(r"\bendmodule\b", text))
    if len(modules) != 1 or len(ends) != 1 or modules[0].start() >= ends[0].start():
        return _result("UNSUPPORTED", "exactly_one_module_required")
    module_start, module_end = modules[0].start(), ends[0].end()
    body = text[module_start:module_end]
    if re.search(r"[`\"\\]", body):
        return _result("UNSUPPORTED", "unsupported_lexical_construct")
    branches = list(re.finditer(r"\bif\s*\(\s*REG_TYPE\s*>\s*1\s*\)\s*begin\b", body))
    if len(branches) != 1:
        return _result("NO_MATCH", "no_unique_reg_type_skid_branch")
    skid_start = body.rfind("begin", branches[0].start(), branches[0].end())
    skid_end = _balanced_end(body, skid_start)
    if skid_end >= len(body) or body[skid_end-3:skid_end] != "end":
        return _result("UNSUPPORTED", "unbalanced_reg_type_skid_branch")
    skid = body[skid_start:skid_end]
    if (any(len(re.findall(pattern, skid)) != 1 for pattern in _UNIQUE) or
            any(re.search(pattern, skid) is None for pattern in _PRESENT)):
        return _result("NO_MATCH", "required_skid_structure_absent_or_repeated")
    # Preserve the exact width shape in both storage registers.
    widths = [re.search(rf"\breg\s*(\[[^\]]+\])\s+{name}\b", skid)
              for name in ("m_axis_tdata_reg", "temp_m_axis_tdata_reg")]
    if any(match is None for match in widths) or widths[0][1] != widths[1][1]:
        return _result("UNSUPPORTED", "payload_storage_width_mismatch")
    sequential = []
    for match in re.finditer(rf"\balways\s*@\s*\(\s*posedge\s+(?P<clock>{IDENT})\s*\)\s*begin\b", skid):
        begin = skid.rfind("begin", match.start(), match.end())
        end = _balanced_end(skid, begin)
        if end < len(skid) and skid[end-3:end] == "end":
            sequential.append((module_start + skid_start + match.start(),
                               module_start + skid_start + end,
                               skid[match.start():end], match["clock"]))
    if len(sequential) != 1:
        return _result("UNSUPPORTED", "one_positive_edge_sequential_block_required")
    seq_start, _, seq, clock = sequential[0]
    direct = _branch(seq, "store_axis_input_to_output")
    capture = _branch(seq, "store_axis_input_to_temp")
    transfer = _branch(seq, "store_axis_temp_to_output")
    if any(len(branches) > 1 for branches in (direct, capture, transfer)):
        return _result("AMBIGUOUS", "multiple_data_path_branches")
    if not all((direct, capture, transfer)):
        return _result("NO_MATCH", "data_path_branch_missing")
    if not (direct[0][0] < transfer[0][0] < capture[0][0]):
        return _result("UNSUPPORTED", "unexpected_branch_order")
    direct_assign = _assignment(direct[0][2], "m_axis_tdata_reg")
    capture_assign = _assignment(capture[0][2], "temp_m_axis_tdata_reg")
    transfer_assign = _assignment(transfer[0][2], "m_axis_tdata_reg")
    if any(len(matches) > 1 for matches in (direct_assign, capture_assign, transfer_assign)):
        return _result("AMBIGUOUS", "multiple_payload_assignments")
    if not all((direct_assign, capture_assign, transfer_assign)):
        return _result("NO_MATCH", "payload_assignment_missing")
    if direct_assign[0]["rhs"] != "s_axis_tdata" or capture_assign[0]["rhs"] != "s_axis_tdata":
        return _result("UNSUPPORTED", "input_or_temp_capture_not_direct")
    if not re.search(r"\bm_axis_tkeep_reg\s*<=\s*temp_m_axis_tkeep_reg\s*;", transfer[0][2]):
        return _result("UNSUPPORTED", "temp_keep_witness_missing")
    if not re.search(r"\bm_axis_tlast_reg\s*<=\s*temp_m_axis_tlast_reg\s*;", transfer[0][2]):
        return _result("UNSUPPORTED", "temp_last_witness_missing")
    old_rhs = transfer_assign[0]["rhs"]
    if old_rhs == "temp_m_axis_tdata_reg":
        return _result("NO_MATCH", "payload_source_already_temp_register")
    if old_rhs != "s_axis_tdata":
        return _result("UNSUPPORTED", "unrecognized_payload_source_expression")
    # Offset is derived from the target buggy source, never from a task manifest.
    transfer_branch_offset = seq_start + transfer[0][0]
    opening = re.search(r"\bbegin\b", seq[transfer[0][0]:transfer[0][1]])
    if opening is None:
        return _result("UNSUPPORTED", "transfer_branch_unbalanced")
    rhs_start = (transfer_branch_offset + opening.end() +
                 transfer_assign[0].start("rhs"))
    rhs_end = rhs_start + len(old_rhs)
    if source[rhs_start:rhs_end] != old_rhs:
        return _result("UNSUPPORTED", "source_offset_does_not_replay")
    witness = {"module": modules[0]["name"], "clock": clock,
               "output_register": "m_axis_tdata_reg",
               "temp_register": "temp_m_axis_tdata_reg",
               "input_payload": "s_axis_tdata", "old_rhs": old_rhs,
               "replacement_rhs": "temp_m_axis_tdata_reg",
               "rhs_span": [rhs_start, rhs_end], "source_sha256": _source_sha(source),
               "public_context": dict(public_context),
               "proof_scope": TEMPLATE["proof_scope"]}
    return _result("BOUND", "unique_temp_payload_source_mismatch",
                   witness=witness, witness_digest=_digest(witness))


def apply_bound_skid_payload(asset: Mapping, source: str,
                             public_context: Mapping, binding: Mapping) -> tuple[str, dict]:
    """Rebind exact bytes before one edit; no oracle search or file access."""
    fresh = bind_skid_payload(asset, source, public_context)
    if fresh.get("status") != "BOUND" or dict(binding) != fresh:
        raise ValueError("binding is stale, tampered, or not uniquely supported")
    witness = fresh["witness"]
    start, end = witness["rhs_span"]
    edited = source[:start] + witness["replacement_rhs"] + source[end:]
    if (edited == source or
            bind_skid_payload(asset, edited, public_context).get("reason") !=
            "payload_source_already_temp_register"):
        raise ValueError("candidate did not become the supported healthy structure")
    return edited, {"contract": CONTRACT, "operator": OPERATOR,
                    "before_sha256": _source_sha(source),
                    "after_sha256": _source_sha(edited),
                    "binding_digest": fresh["witness_digest"], "rewritten": 1,
                    "functional_correctness": "not_asserted_requires_native_oracle"}
