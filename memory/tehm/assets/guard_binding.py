"""Source-only bounded acceptance/completion guard localization.

Unlike alpha binding this contract permits unrelated datapaths/FSM prefixes.
It proves a syntactic acceptance/transition mismatch, NOT functional equivalence
or safe promotion. Independent target/regression/rollback evidence is required.
No manifests, testbenches, names-as-answers, or filesystem reads are inputs.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import replace
import hashlib
import re

from tehm.ids import stable_dumps
from tehm.rtl.guard_conjunction import DOMAIN, apply_guard_conjunction
from tehm.rtl.verilog_parse import parse_verilog, _strip_comments, _balanced_end

CONTRACT = "rtl_acceptance_completion_guard_binding_v1"
PROFILE = "rtl.fsm.guard_conjunction.v1"
IDENT = r"[A-Za-z_]\w*"
SPEC = {"version": CONTRACT, "domain": DOMAIN, "profile": PROFILE,
        "locator": "unique_scalar_acceptance_terminal_transition",
        "proof_scope": "syntactic_not_functional", "answer_fields_consumed": False}


def _digest(value):
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _conditions(body):
    """Balanced if conditions with their single true statement (subset only)."""
    for match in re.finditer(r"\bif\s*\(", body):
        lo, depth, i = match.end(), 1, match.end()
        while i < len(body) and depth:
            depth += (body[i] == "(") - (body[i] == ")")
            i += 1
        if depth:
            raise ValueError("unbalanced sequential condition")
        j = i
        while j < len(body) and body[j].isspace():
            j += 1
        end = _balanced_end(body, j) if body.startswith("begin", j) else body.find(";", j) + 1
        if end <= j:
            raise ValueError("unsupported sequential branch")
        yield body[lo:i-1], body[j:end]


def _gates_datapath(block, *, state, source_state, alias, signals):
    # Support (state==S && accept) and ((state==A || state==S) && accept).
    # Arbitrary boolean reasoning, OR of acceptance signals and nested
    # conditional writes are deliberately outside this contract.
    for condition, statement in _conditions(block.body):
        compact = re.sub(r"\s+", "", condition)
        direct = f"{state}=={source_state}&&{alias}"
        group = re.fullmatch(rf"\((?P<states>{state}=={IDENT}(?:\|\|{state}=={IDENT})*)\)&&{alias}", compact)
        accepted = compact == direct or (group is not None and
            source_state in re.findall(rf"{state}==({IDENT})", group["states"]))
        if not accepted or re.search(r"\b(?:if|case|casex|casez)\b", statement):
            continue
        writes = re.findall(rf"\b({IDENT})\s*<=\s*[^;]+;", statement)
        if any(name in signals and signals[name].kind == "output" for name in writes):
            return True
    return False


def locate_guard_conjunction(source: str) -> dict:
    if not isinstance(source, str):
        raise ValueError("RTL source must be text")
    text = _strip_comments(source).strip()
    if any(token in text for token in ('`', '"', '\\')):
        raise ValueError("unsupported lexical constructs")
    modules = parse_verilog(text)
    if len(modules) != 1:
        raise ValueError("locator requires one module")
    module = modules[0]
    sequential = [b for b in module.always_blocks if b.is_sequential]
    fsms = [f for b in module.always_blocks for f in b.fsms]
    if len(fsms) != 1 or len(sequential) != 1:
        raise ValueError("locator requires one FSM and one sequential block")
    block, fsm = sequential[0], fsms[0]
    if len(block.sensitivity) != 2 or block.sensitivity[0] != "posedge":
        raise ValueError("locator supports only a single positive-edge clock")
    state, reg = fsm.case_expr, fsm.reg_name
    if not all(re.fullmatch(IDENT, v) for v in (state, reg)):
        raise ValueError("FSM coordinates must be identifiers")
    if not re.search(rf"\b{state}\s*<=\s*{reg}\s*;", block.body):
        raise ValueError("sequential state register does not track next-state")
    signals = module.signals
    aliases = list(re.finditer(rf"\bwire\s+(?P<alias>{IDENT})\s*=\s*"
                               rf"(?P<a>{IDENT})\s*&&\s*(?P<b>{IDENT})\s*;", text))
    completions = list(re.finditer(rf"\bassign\s+(?P<output>{IDENT})\s*=\s*"
                                   rf"{re.escape(state)}\s*==\s*(?P<terminal>{IDENT})\s*;", text))
    candidates = []
    for completion in completions:
        output, terminal = completion["output"], completion["terminal"]
        sig = signals.get(output)
        if sig is None or sig.kind != "output" or sig.width is not None:
            continue
        terminal_items = [i for i in fsm.items if i.label == terminal]
        if len(terminal_items) != 1 or not re.fullmatch(rf"\s*{reg}\s*=\s*{IDENT}\s*;\s*", terminal_items[0].raw):
            continue
        for item in fsm.items:
            if item.target != f"{reg} = {terminal}" or not re.fullmatch(IDENT, item.condition or ""):
                continue
            for alias in aliases:
                operands = (alias["a"], alias["b"])
                if operands[0] == operands[1] or item.condition not in operands:
                    continue
                if any(v not in signals or signals[v].kind != "input" or
                       signals[v].width is not None for v in operands):
                    continue
                if not _gates_datapath(block, state=state, source_state=item.label,
                                       alias=alias["alias"], signals=signals):
                    continue
                missing = next(v for v in operands if v != item.condition)
                payload = {"domain": DOMAIN, "compatibility_profile": PROFILE,
                           "module": module.name, "case_expr": state, "reg": reg,
                           "source_state": item.label, "target_state": terminal,
                           "add_condition": missing}
                _, edit = apply_guard_conjunction(text, **{k: payload[k] for k in
                    ("module", "case_expr", "reg", "source_state", "target_state", "add_condition")})
                if edit["rewritten"] != 1:
                    continue
                candidates.append({"payload": payload, "acceptance_alias": alias["alias"],
                                   "acceptance_inputs": list(operands),
                                   "completion_output": output, "clock": block.sensitivity[1],
                                   "source": text, "spec": dict(SPEC)})
    if len(candidates) != 1:
        raise ValueError("acceptance/completion mismatch is absent or ambiguous")
    return candidates[0]


def with_guard_conjunction_binding(proposal, training_source: str):
    located = locate_guard_conjunction(training_source)
    definition = copy.deepcopy(proposal.definition)
    action = definition.get("action") or {}
    if action.get("domain") != DOMAIN or action.get("payload") != located["payload"]:
        raise ValueError("training proposal does not match the source-only locator")
    definition["binding_template"] = {"contract": CONTRACT, "spec": dict(SPEC),
                                      "spec_digest": _digest(SPEC)}
    return replace(proposal, definition=definition)


def bind_guard_asset_to_source(asset: Mapping, source: str, *, design_id: str) -> dict:
    if not isinstance(asset, Mapping) or not isinstance(design_id, str) or not design_id:
        raise ValueError("asset and design_id are required")
    template = (asset.get("definition") or {}).get("binding_template")
    expected_template = {"contract": CONTRACT, "spec": dict(SPEC), "spec_digest": _digest(SPEC)}
    if template != expected_template:
        raise ValueError("asset has no exact frozen guard locator contract")
    action = asset["definition"].get("action") or {}
    if action.get("domain") != DOMAIN or (asset.get("compatibility") or {}).get("compatibility_profile") != PROFILE:
        raise ValueError("asset domain/profile does not match locator contract")
    located = locate_guard_conjunction(source)
    bound = copy.deepcopy(dict(asset))
    bound["definition"]["action"]["payload"] = located["payload"]
    evidence = {"asset_id": asset.get("asset_id"), "design_id": design_id, **located}
    bound["provenance"] = {**dict(asset.get("provenance") or {}),
                           "bound_design": design_id, "binding_contract": CONTRACT,
                           "binding_source": "rtl_source", "answer_fields_consumed": False,
                           "binding_evidence": evidence, "binding_digest": _digest(evidence)}
    return bound


def verify_guard_binding(bound: Mapping, asset: Mapping) -> bool:
    try:
        evidence = bound["provenance"]["binding_evidence"]
        expected = bind_guard_asset_to_source(asset, evidence["source"], design_id=evidence["design_id"])
        return stable_dumps(expected) == stable_dumps(dict(bound))
    except (KeyError, TypeError, ValueError, AttributeError, NotImplementedError):
        return False
