"""Versioned operator: bounded, unique conditional FSM guard conjunction.

This rewrite does not register Assets or establish production authority.
Only positive conjunctions of scalar module inputs and single blocking state
assignments are supported. Unsupported or ambiguous syntax is rejected.
"""
from __future__ import annotations

import re

from .verilog_parse import parse_verilog, _balanced_end

DOMAIN = "rtl.FSM_GUARD_CONJOIN"
VERSION = "rtl-guard-conjunction-v1"
IDENT = r"[A-Za-z_]\w*"


def _mask_comments(source: str) -> str:
    # Preserve every offset; comments cannot introduce modules/labels/guards.
    return re.sub(r"/\*.*?\*/|//[^\n]*",
                  lambda m: "".join("\n" if c == "\n" else " " for c in m[0]),
                  source, flags=re.S)


def apply_guard_conjunction(source: str, *, module: str, case_expr: str,
                            reg: str, source_state: str, target_state: str,
                            add_condition: str) -> tuple[str, dict]:
    args = (module, case_expr, reg, source_state, target_state, add_condition)
    if any(not isinstance(v, str) or not re.fullmatch(IDENT, v) for v in args):
        raise ValueError("all guard-conjunction coordinates must be identifiers")
    modules = [m for m in parse_verilog(source) if m.name == module]
    if len(modules) != 1:
        raise ValueError("named module must be unique")
    parsed = modules[0]
    signal = parsed.signals.get(add_condition)
    if signal is None or signal.kind != "input" or signal.width is not None:
        raise ValueError("added condition must be a scalar module input")
    for name in (case_expr, reg):
        if name not in parsed.signals or parsed.signals[name].kind != "reg":
            raise ValueError("FSM coordinates must be declared registers")
    fsms = [f for b in parsed.always_blocks if not b.is_sequential
            for f in b.fsms if f.case_expr == case_expr and f.reg_name == reg]
    if len(fsms) != 1:
        raise ValueError("combinational FSM must be unique")
    items = [i for i in fsms[0].items if i.label == source_state]
    if len(items) != 1 or items[0].target != f"{reg} = {target_state}":
        raise ValueError("transition coordinates are absent or ambiguous")
    if target_state not in {i.label for i in fsms[0].items}:
        raise ValueError("target must be a declared FSM case label")
    masked = _mask_comments(source)
    spans = list(re.finditer(rf"\bmodule\s+{re.escape(module)}\b.*?\bendmodule\b",
                             masked, re.S))
    if len(spans) != 1:
        raise ValueError("source module span must be unique")
    start, end = spans[0].span()
    scope = masked[start:end]
    cases = []
    for block in re.finditer(r"\balways\s*@\s*\((?P<sens>[^)]*)\)\s*begin\b", scope):
        if re.search(r"\b(?:posedge|negedge)\b", block["sens"]):
            continue
        begin = block.end() - len("begin")
        stop = _balanced_end(scope, begin)
        body = scope[block.end():stop]
        for case in re.finditer(rf"\bcase\s*\(\s*{re.escape(case_expr)}\s*\)"
                                r"(?P<body>.*?)\bendcase\b", body, re.S):
            cases.append((block.end() + case.start("body"), case["body"]))
    if len(cases) != 1:
        raise ValueError("exact source case span must be unique")
    case_offset, case_body = cases[0]
    labels = list(re.finditer(rf"(?m)^\s*(?P<label>{IDENT})\s*:", case_body))
    selected = [(label.end(), labels[i + 1].start() if i + 1 < len(labels)
                 else len(case_body)) for i, label in enumerate(labels)
                if label["label"] == source_state]
    if len(selected) != 1:
        raise ValueError("source label span must be unique")
    lo, hi = selected[0]
    item = case_body[lo:hi]
    match = re.fullmatch(rf"\s*if\s*\((?P<guard>{IDENT}(?:\s*&&\s*{IDENT})*)\)"
                         rf"\s*{re.escape(reg)}\s*=\s*{re.escape(target_state)}\s*;\s*",
                         item)
    if match is None:
        raise ValueError("only a single positive guarded blocking transition is supported")
    atoms = re.findall(IDENT, match["guard"])
    if len(set(atoms)) != len(atoms):
        raise ValueError("duplicate guard atoms are unsupported")
    for atom in atoms:
        sig = parsed.signals.get(atom)
        if sig is None or sig.kind != "input" or sig.width is not None:
            raise ValueError("existing guard must contain scalar module inputs only")
    descriptor = {"domain": DOMAIN, "version": VERSION, "module": module,
                  "case_expr": case_expr, "reg": reg, "source_state": source_state,
                  "target_state": target_state, "add_condition": add_condition,
                  "original_guard_atoms": atoms}
    if add_condition in atoms:
        return source, dict(descriptor, rewritten=0, status="ALREADY_CONJOINED")
    offset = start + case_offset + lo + match.end("guard")
    rewritten = source[:offset] + " && " + add_condition + source[offset:]
    after = [m for m in parse_verilog(rewritten) if m.name == module]
    new_items = [i for b in after[0].always_blocks for f in b.fsms
                 if f.case_expr == case_expr and f.reg_name == reg
                 for i in f.items if i.label == source_state]
    if len(after) != 1 or len(new_items) != 1:
        raise ValueError("rewritten FSM did not parse uniquely")
    if new_items[0].target != items[0].target or re.findall(IDENT, new_items[0].condition or "") != atoms + [add_condition]:
        raise ValueError("rewrite changed more than the selected guard")
    return rewritten, dict(descriptor, rewritten=1, status="CONJOINED",
                           source_insert_offset=offset)
