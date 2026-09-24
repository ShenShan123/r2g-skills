"""R5 DEV-only source-only binding across axis and ZipCPU-style skid shapes.

The selected draft asset, buggy RTL text, and exact public configuration are
the only inputs. This module has no oracle, repository, manifest, or file API.
It proves a bounded syntactic data-source mismatch, not functional repair.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from tehm.rtl.verilog_parse import _balanced_end

from . import research_r5_skid_binding as v1
from . import research_r5_skid_binding_v2 as v2


CONTRACT = "rtl_skid_temp_payload_binding_dev_v3"
OPERATOR = "rtl.SKID_TEMP_PAYLOAD_RESTORE_DEV_V3"
PROFILE = "rtl.skid.temp_payload.v3.dev"
TEMPLATE = {"contract": CONTRACT, "operator": OPERATOR, "profile": PROFILE,
            "proof_scope": "unique_syntactic_temp_payload_mismatch_not_functional"}
ZIPCPU_CONTEXT = {"DW": 8, "OPT_OUTREG": 1, "OPT_LOWPOWER": 0,
                  "OPT_PASSTHROUGH": 0, "OPT_INITIAL": 1}
CONTEXTS = {**v2.CONTEXTS, "zipcpu_registered": ZIPCPU_CONTEXT}
_PARAMETER = re.compile(
    rf"\bparameter\s*(?:\[[^\]]+\]\s*)?(?P<name>{v1.IDENT})\s*=\s*"
    r"(?P<value>[^,\n)]+)")
_REG_DATA_PATH = re.compile(
    r"\balways\s*@\s*\(\s*posedge\s+i_clk\s*\)\s*"
    r"if\s*\(\s*OPT_LOWPOWER\s*&&\s*i_reset\s*\)\s*"
    r"o_data\s*<=\s*0\s*;\s*"
    r"else\s+if\s*\(\s*!o_valid\s*\|\|\s*i_ready\s*\)\s*begin\s*"
    r"if\s*\(\s*r_valid\s*\)\s*o_data\s*<=\s*"
    rf"(?P<rhs>{v1.IDENT})\s*;\s*"
    r"else\s+if\s*\(\s*!OPT_LOWPOWER\s*\|\|\s*i_valid\s*\)\s*"
    r"o_data\s*<=\s*i_data\s*;\s*"
    r"else\s*o_data\s*<=\s*0\s*;\s*end\b")
_PARAMETER_DEFAULTS = {"DW": "8", "OPT_OUTREG": "1", "OPT_LOWPOWER": "0",
                       "OPT_PASSTHROUGH": "0", "OPT_INITIAL": "1'b1"}


def _result(status: str, reason: str, **detail: object) -> dict:
    return {"contract": CONTRACT, "status": status, "reason": reason, **detail}


def _zipcpu(source: str, context: dict) -> dict:
    text = v1._mask_comments(source)
    modules = list(re.finditer(rf"\bmodule\s+(?P<name>{v1.IDENT})\b", text))
    ends = list(re.finditer(r"\bendmodule\b", text))
    if len(modules) != 1 or len(ends) != 1 or modules[0].start() >= ends[0].start():
        return _result("UNSUPPORTED", "exactly_one_module_required")
    if modules[0]["name"] != "skidbuffer":
        return _result("NO_MATCH", "zipcpu_skid_module_shape_absent")
    module_start = modules[0].start()
    if not re.fullmatch(r"\s*`default_nettype\s+none\s*", text[:module_start]):
        return _result("UNSUPPORTED", "unexpected_premodule_directive_or_code")
    if text[ends[0].end():].strip():
        return _result("UNSUPPORTED", "unexpected_postmodule_code")
    body = text[module_start:ends[0].end()]
    formal = list(re.finditer(r"`ifdef\s+FORMAL\b", body))
    if len(formal) != 1:
        return _result("UNSUPPORTED", "one_formal_tail_boundary_required")
    functional = body[:formal[0].start()]
    if re.search(r"[`\"\\]", functional):
        return _result("UNSUPPORTED", "functional_region_has_lexical_construct")
    parameters = list(_PARAMETER.finditer(functional))
    values = {match["name"]: match["value"].strip() for match in parameters}
    if len(parameters) != len(_PARAMETER_DEFAULTS) or values != _PARAMETER_DEFAULTS:
        return _result("UNSUPPORTED", "unsupported_parameter_declaration")
    witnesses = (
        r"\binput\s+wire\s*\[\s*DW\s*-\s*1\s*:\s*0\s*\]\s+i_data\b",
        r"\boutput\s+reg\s*\[\s*DW\s*-\s*1\s*:\s*0\s*\]\s+o_data\b",
        r"\breg\s*\[\s*DW\s*-\s*1\s*:\s*0\s*\]\s+r_data\b",
        r"\breg\s+r_valid\b",
        r"\br_data\s*<=\s*i_data\s*;",
        r"\bassign\s+w_data\s*=\s*r_data\s*;",
        r"\bassign\s+o_ready\s*=\s*!r_valid\s*;",
        r"\bassign\s+o_valid\s*=\s*ro_valid\s*;",
        r"\bro_valid\s*<=\s*\(\s*i_valid\s*\|\|\s*r_valid\s*\)\s*;",
    )
    if any(len(re.findall(pattern, functional)) != 1 for pattern in witnesses):
        return _result("NO_MATCH", "zipcpu_storage_or_handshake_witness_missing")
    labels = list(re.finditer(r"\belse\s+begin\s*:\s*REG_OUTPUT\b", functional))
    if len(labels) != 1:
        return _result("NO_MATCH", "unique_registered_output_branch_required")
    opening = re.search(r"\bbegin\b", labels[0][0])
    assert opening is not None
    begin = labels[0].start() + opening.start()
    end = _balanced_end(functional, begin)
    if end >= len(functional) or functional[end - 3:end] != "end":
        return _result("UNSUPPORTED", "registered_output_branch_unbalanced")
    branch = functional[begin:end]
    if (len(re.findall(r"\bif\s*\(\s*r_valid\s*\)", branch)) != 1 or
            len(re.findall(r"\bo_data\s*<=", branch)) != 4 or
            len(re.findall(r"\balways\s*@\s*\(\s*posedge\s+i_clk\s*\)", branch)) != 2):
        return _result("AMBIGUOUS", "registered_output_data_path_not_unique")
    matches = list(_REG_DATA_PATH.finditer(branch))
    if len(matches) != 1:
        return _result("NO_MATCH", "registered_output_data_path_absent")
    rhs = matches[0]["rhs"]
    if rhs == "r_data":
        return _result("NO_MATCH", "payload_source_already_r_data")
    if rhs != "i_data":
        return _result("UNSUPPORTED", "unrecognized_payload_source_expression")
    rhs_start = module_start + begin + matches[0].start("rhs")
    rhs_end = rhs_start + len(rhs)
    if source[rhs_start:rhs_end] != rhs:
        return _result("UNSUPPORTED", "source_offset_does_not_replay")
    witness = {"shape": "zipcpu_registered", "module": modules[0]["name"],
               "clock": "i_clk", "output_register": "o_data",
               "temp_register": "r_data", "input_payload": "i_data",
               "old_rhs": rhs, "replacement_rhs": "r_data",
               "rhs_span": [rhs_start, rhs_end],
               "source_sha256": v1._source_sha(source), "public_context": context,
               "proof_scope": TEMPLATE["proof_scope"]}
    return _result("BOUND", "unique_zipcpu_registered_payload_source_mismatch",
                   witness=witness, witness_digest=v1._digest(witness))


def bind_skid_payload_v3(asset: Mapping, source: str, public_context: Mapping) -> dict:
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
    if context == ZIPCPU_CONTEXT:
        return _zipcpu(source, context)
    legacy = v2.bind_skid_payload_v2({"binding_template": v2.TEMPLATE}, source, context)
    if legacy["status"] != "BOUND":
        return _result(legacy["status"], legacy["reason"])
    witness = {**legacy["witness"], "proof_scope": TEMPLATE["proof_scope"]}
    return _result("BOUND", legacy["reason"], witness=witness,
                   witness_digest=v1._digest(witness))


def apply_bound_skid_payload_v3(asset: Mapping, source: str,
                                public_context: Mapping,
                                binding: Mapping) -> tuple[str, dict]:
    fresh = bind_skid_payload_v3(asset, source, public_context)
    if fresh.get("status") != "BOUND" or not isinstance(binding, Mapping) or dict(binding) != fresh:
        raise ValueError("binding is stale, tampered, or not uniquely supported")
    witness = fresh["witness"]
    start, end = witness["rhs_span"]
    edited = source[:start] + witness["replacement_rhs"] + source[end:]
    healthy_reason = ("payload_source_already_r_data" if witness["shape"] ==
                      "zipcpu_registered" else "payload_source_already_temp_register")
    if (edited == source or bind_skid_payload_v3(asset, edited, public_context).get("reason")
            != healthy_reason):
        raise ValueError("candidate did not become the supported healthy structure")
    return edited, {"contract": CONTRACT, "operator": OPERATOR,
                    "before_sha256": v1._source_sha(source),
                    "after_sha256": v1._source_sha(edited),
                    "binding_digest": fresh["witness_digest"], "rewritten": 1,
                    "functional_correctness": "not_asserted_requires_authoritative_oracle"}
