"""Answer-free transfer of a frozen, narrow RTL rewrite template.

Only identifier renaming and whitespace/comment changes are supported. This
is not semantic localization on unrelated circuits. The full token shape is
matched before executing; ambiguity or any structural change fails closed.
Target manifests, testbenches, and oracle answers are not inputs to binding.
"""
from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping
from dataclasses import replace

from tehm.ids import stable_dumps
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.verilog_parse import _strip_comments, parse_verilog

CONTRACT = "rtl_alpha_binding_v1"
_SLOTS = ("module", "source_state", "target_state", "add_condition")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*\Z", re.ASCII)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _shape(source: str) -> tuple[str, dict[str, str], str]:
    if not isinstance(source, str):
        raise ValueError("RTL source must be text")
    text = _strip_comments(source).strip()
    # Preprocessing, strings, escaped names and hierarchy need a full frontend.
    # In particular never interpret an answer embedded in a source comment.
    if any(token in text for token in ('`', '"', '\\')):
        raise ValueError("unsupported RTL lexical construct")
    modules = parse_verilog(text)
    if len(modules) != 1:
        raise ValueError("structural binding requires exactly one module")
    module = modules[0]
    fsms = [fsm for block in module.always_blocks for fsm in block.fsms]
    if len(fsms) != 1:
        raise ValueError("structural binding requires exactly one FSM")
    fsm = fsms[0]
    names = {module.name, *module.signals, fsm.case_expr, fsm.reg_name}
    names.update(item.label for item in fsm.items if item.label != "default")
    if not all(_IDENTIFIER.fullmatch(name) for name in names):
        raise ValueError("structural binding requires simple identifiers")
    # Keep operators and literals intact. Only parser-visible declared names
    # and FSM labels can be alpha-renamed, never Verilog keywords or literals.
    tokens = re.findall(r"\d+'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ?_]+|"
                        r"[A-Za-z_]\w*|\d+|\S", text, flags=re.ASCII)
    roles: dict[str, str] = {}
    normalized = []
    for token in tokens:
        if token in names:
            roles.setdefault(token, f"identifier_{len(roles)}")
            normalized.append({"identifier": roles[token]})
        else:
            normalized.append({"literal": token})
    return _digest(normalized), roles, text


def with_structural_binding(proposal, training_source: str):
    """Freeze a locator from a training repair before any held-out execution.

    The caller owns the verified-training-evidence admission. This function
    does not claim that the provided training action was independently found.
    """
    definition = copy.deepcopy(proposal.definition)
    action = definition["action"]
    payload = action["payload"]
    if (action.get("domain") != "rtl.GUARD_STRENGTHEN" or
            set(payload) != {*_SLOTS, "domain", "compatibility_profile"}):
        raise ValueError("structural template supports simple GUARD_STRENGTHEN only")
    shape, roles, _ = _shape(training_source)
    if any(payload.get(slot) not in roles for slot in _SLOTS):
        raise ValueError("training action slots are not parser-visible identifiers")
    module = parse_verilog(training_source)[0]
    signal = module.signals.get(payload["add_condition"])
    if signal is None or signal.kind != "input" or signal.width is not None:
        raise ValueError("structural guard must be a scalar input")
    _, edit = apply_rtl_action(training_source, payload)
    if not edit.get("rewritten"):
        raise ValueError("training action does not rewrite the training source")
    definition["binding_template"] = {
        "contract": CONTRACT, "source_shape_digest": shape,
        "slot_roles": {slot: roles[payload[slot]] for slot in _SLOTS},
    }
    return replace(proposal, definition=definition)


def bind_rtl_asset_to_source(asset: Mapping, source: str, *, design_id: str) -> dict:
    """Create a bound copy using RTL bytes only; never reads a project path."""
    if not isinstance(asset, Mapping) or not isinstance(design_id, str) or not design_id:
        raise ValueError("asset and design_id are required")
    definition = asset.get("definition")
    template = definition.get("binding_template") if isinstance(definition, Mapping) else None
    if not isinstance(template, Mapping) or template.get("contract") != CONTRACT:
        raise ValueError("asset has no frozen structural binding template")
    shape, roles, text = _shape(source)
    if shape != template.get("source_shape_digest"):
        raise ValueError("RTL structure does not match the frozen training template")
    slot_roles = template.get("slot_roles")
    inverse = {role: name for name, role in roles.items()}
    if (not isinstance(slot_roles, Mapping) or set(slot_roles) != set(_SLOTS) or
            any(role not in inverse for role in slot_roles.values())):
        raise ValueError("structural slot roles are malformed")
    bound = copy.deepcopy(dict(asset))
    payload = bound["definition"]["action"]["payload"]
    payload.update({slot: inverse[role] for slot, role in slot_roles.items()})
    _, edit = apply_rtl_action(text, payload)
    if not edit.get("rewritten"):
        raise ValueError("structurally bound action did not rewrite RTL")
    proof = {"asset_id": asset.get("asset_id"), "design_id": design_id,
             "source": text, "source_shape_digest": shape, "payload": payload}
    bound["provenance"] = {
        **dict(asset.get("provenance") or {}),
        "bound_design": design_id, "binding_contract": CONTRACT,
        "binding_source": "rtl_source", "answer_fields_consumed": False,
        "binding_evidence": proof, "binding_digest": _digest(proof),
    }
    return bound


def verify_structural_binding(bound: Mapping, asset: Mapping) -> bool:
    """Re-derive the exact copy from the registered template and recorded RTL."""
    try:
        provenance = bound["provenance"]
        evidence = provenance["binding_evidence"]
        expected = bind_rtl_asset_to_source(
            asset, evidence["source"], design_id=evidence["design_id"])
        return stable_dumps(expected) == stable_dumps(dict(bound))
    except (KeyError, TypeError, ValueError, AttributeError, NotImplementedError):
        return False
