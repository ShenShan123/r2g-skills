"""RTL action domains + parser-backed rewrite primitives.

The executor intentionally works on a small synthesizable Verilog subset.  It
parses the source before *and* after every structured rewrite, and refuses an
edit when the requested AST context is absent.  This is stronger than a global
regular-expression replacement while keeping the executor dependency-free.
"""
from __future__ import annotations

import re

from tehm.rtl.verilog_parse import parse_verilog

RTL_ACTION_VERSION = "rtl-actions-v0.1"
RTL_ACTION_DOMAINS = (
    "rtl.AST_REWRITE", "rtl.GUARD_STRENGTHEN", "rtl.RESET_RESTORE",
    "rtl.WIDTH_CORRECT", "rtl.PRIORITY_REORDER",
)


def apply_rtl_action(source: str, payload: dict) -> tuple[str, dict]:
    """Apply one rtl.* action to Verilog source; returns (new_source, edit)."""
    domain = payload.get("domain")
    if domain == "rtl.GUARD_STRENGTHEN":
        return apply_guard_strengthen(
            source,
            source_state=str(payload.get("source_state", "")),
            target_state=str(payload.get("target_state", "")),
            add_condition=str(payload.get("add_condition", "")),
            reg=str(payload.get("reg", "next_state")),
            module=payload.get("module"),
        )
    if domain == "rtl.AST_REWRITE":
        return apply_ast_rewrite(source, payload)
    if domain == "rtl.RESET_RESTORE":
        return apply_reset_restore(source, payload)
    if domain == "rtl.WIDTH_CORRECT":
        return apply_width_correct(source, payload)
    if domain == "rtl.PRIORITY_REORDER":
        return apply_priority_reorder(source, payload)
    raise NotImplementedError(
        f"{domain} rewrite is not implemented; supported: "
        f"{', '.join(RTL_ACTION_DOMAINS)}")


def apply_guard_strengthen(source: str, *, source_state: str, target_state: str,
                           add_condition: str, reg: str = "next_state",
                           module: str | None = None) -> tuple[str, dict]:
    """rtl.GUARD_STRENGTHEN: ``SRC: REG = TGT;`` -> ``SRC: if (COND) REG = TGT;``

    Scoped to the named module when given. ``add_condition`` is a raw Verilog
    expression (e.g. ``ack`` or ``ack && ready``). Returns the edited source and
    a structured edit descriptor.
    """
    # negative lookahead: never touch a transition that already carries an
    # ``if (...)`` guard (idempotent on a previously-fixed source).
    pattern = re.compile(
        rf"(?P<prefix>{re.escape(source_state)}\s*:\s*)"
        rf"(?!if\s*\([^)]*\))"
        rf"{re.escape(reg)}\s*(?:<=|=)\s*{re.escape(target_state)}\s*;")

    def _repl(m: re.Match) -> str:
        return (f"{m.group('prefix')}if ({add_condition}) "
                f"{reg} = {target_state};")

    scope = source
    if module:
        scope = _module_block(source, module)
    if scope is None:
        return source, {"rewritten": 0, "error": f"module {module!r} not found"}
    # comment-aware: only rewrite inside non-comment code regions, so example
    # transitions inside doc comments are never touched.
    pieces: list[str] = []
    last = 0
    total = 0
    for start, end in _code_regions(scope):
        pieces.append(scope[last:start])
        new_code, n = pattern.subn(_repl, scope[start:end])
        total += n
        pieces.append(new_code)
        last = end
    pieces.append(scope[last:])
    new_scope = "".join(pieces)
    new_source = source.replace(scope, new_scope)
    return new_source, {
        "rewritten": total,
        "edit": f"{source_state}: if ({add_condition}) {reg} = {target_state};",
    }


def apply_ast_rewrite(source: str, payload: dict) -> tuple[str, dict]:
    """rtl.AST_REWRITE: a generic single-target literal edit (target: new text).

    ``payload = {"target": <regex>, "replacement": <text>, "count": n}``.
    """
    target = payload.get("target")
    replacement = payload.get("replacement")
    if not target or replacement is None:
        raise ValueError("rtl.AST_REWRITE requires payload.target (regex) and "
                         "payload.replacement")
    count = int(payload.get("count", 0))
    new_source, n = re.subn(str(target), str(replacement), source,
                            count=count)
    return new_source, {"rewritten": n, "edit": f"{target} -> {replacement}"}


def apply_reset_restore(source: str, payload: dict) -> tuple[str, dict]:
    """Restore a reset-branch assignment using parser-scoped source spans.

    Required payload fields are ``target`` and ``replacement``.  ``module``
    and ``reset_signal`` are optional; when omitted, the first sequential block
    with an active-low reset branch is selected.  The target is searched only
    inside the parsed reset branch, so an identical assignment in a normal
    branch or a comment cannot be changed accidentally.
    """
    target = _require_text(payload, "target")
    replacement = _require_text(payload, "replacement")
    module_name = payload.get("module")
    reset_signal = payload.get("reset_signal")
    modules = parse_verilog(source)
    module = _select_module(modules, module_name)
    if module is None:
        raise ValueError(f"RESET_RESTORE module is not parseable: {module_name!r}")
    module_span = _module_span(source, module.name)
    if module_span is None:
        raise ValueError(f"RESET_RESTORE module source span not found: {module.name}")
    start, end = module_span
    scoped = source[start:end]
    branches = _reset_branch_spans(scoped, reset_signal=reset_signal)
    if not branches:
        raise ValueError("RESET_RESTORE requires a parsed active-low reset branch")
    replacement_count = int(payload.get("count", 1))
    new_scoped, rewritten = _replace_literal_in_spans(
        scoped, branches, target, replacement, count=replacement_count)
    if rewritten == 0:
        raise ValueError("RESET_RESTORE target is absent from reset branch")
    new_source = source[:start] + new_scoped + source[end:]
    _require_parse_after(new_source, module.name, "RESET_RESTORE")
    return new_source, {
        "rewritten": rewritten,
        "edit": f"reset branch: {target} -> {replacement}",
        "parser_backed": True,
        "module": module.name,
        "reset_signal": reset_signal,
    }


def apply_width_correct(source: str, payload: dict) -> tuple[str, dict]:
    """Apply a width correction to an assignment validated by the parser.

    The action carries an explicit ``target``/``replacement`` pair and may
    carry ``signal`` and ``module`` to constrain the edit.  The target must be
    present in a parsed assignment (LHS or RHS), and the post-edit source must
    still parse.  No implicit truncation, extension, or guessed width is
    introduced by the executor.
    """
    target = _require_text(payload, "target")
    replacement = _require_text(payload, "replacement")
    module_name = payload.get("module")
    signal = payload.get("signal")
    modules = parse_verilog(source)
    module = _select_module(modules, module_name)
    if module is None:
        raise ValueError(f"WIDTH_CORRECT module is not parseable: {module_name!r}")
    if signal and signal not in module.signals:
        raise ValueError(f"WIDTH_CORRECT signal is not declared: {signal}")
    module_span = _module_span(source, module.name)
    if module_span is None:
        raise ValueError(f"WIDTH_CORRECT module source span not found: {module.name}")
    start, end = module_span
    scoped = source[start:end]
    assignments = [a for block in module.always_blocks for a in block.assigns]
    target_expr = target.strip().rstrip(";").strip()
    matching = [a for a in assignments
                if target_expr in (a.lhs, a.rhs) or
                target_expr == f"{a.lhs} = {a.rhs}"]
    if not matching:
        raise ValueError("WIDTH_CORRECT target is not present in a parsed assignment")
    if signal and not any(signal in (a.lhs, a.rhs) for a in matching):
        raise ValueError("WIDTH_CORRECT signal does not constrain the target assignment")
    regions = _code_regions(scoped)
    new_scoped, rewritten = _replace_literal_in_spans(
        scoped, regions, target, replacement,
        count=int(payload.get("count", 1)))
    if rewritten == 0:
        raise ValueError("WIDTH_CORRECT target did not rewrite")
    new_source = source[:start] + new_scoped + source[end:]
    _require_parse_after(new_source, module.name, "WIDTH_CORRECT")
    return new_source, {
        "rewritten": rewritten,
        "edit": f"width correction: {target} -> {replacement}",
        "parser_backed": True,
        "module": module.name,
        "signal": signal,
    }


def apply_priority_reorder(source: str, payload: dict) -> tuple[str, dict]:
    """Reorder two parser-discovered ``case`` items without text guessing.

    Payload: ``module`` (optional), ``case_expr`` (optional),
    ``higher_label`` and ``lower_label``.  The complete case-item chunks are
    swapped, preserving their guards and statements.  The post-edit parser
    must observe the requested order and the same case labels.
    """
    higher = _require_text(payload, "higher_label")
    lower = _require_text(payload, "lower_label")
    if higher == lower:
        raise ValueError("PRIORITY_REORDER labels must differ")
    modules = parse_verilog(source)
    module = _select_module(modules, payload.get("module"))
    if module is None:
        raise ValueError("PRIORITY_REORDER module is not parseable")
    case_expr = str(payload.get("case_expr") or "").strip()
    fsm = next((fsm for block in module.always_blocks for fsm in block.fsms
                if not case_expr or fsm.case_expr.strip() == case_expr), None)
    if fsm is None:
        raise ValueError("PRIORITY_REORDER case expression is not present in parsed FSM")
    module_span = _module_span(source, module.name)
    if module_span is None:
        raise ValueError(f"PRIORITY_REORDER module source span not found: {module.name}")
    start, end = module_span
    scoped = source[start:end]
    case_match = re.search(
        rf"\bcase(?:z|x)?\s*\(\s*{re.escape(fsm.case_expr.strip())}\s*\)",
        scoped)
    if not case_match:
        raise ValueError("PRIORITY_REORDER case source span not found")
    body_start = case_match.end()
    body_end = _balanced_case_end(scoped, body_start)
    body = scoped[body_start:body_end]
    labels = list(re.finditer(
        r"(?m)^(?P<indent>\s*)(?P<label>(?:\d+'[bBoOdDhH][0-9a-fA-FxXzZ?_]+|"
        r"[A-Za-z_]\w*|default))\s*:", body))
    by_label = {m.group("label"): m for m in labels}
    if higher not in by_label or lower not in by_label:
        raise ValueError("PRIORITY_REORDER labels are absent from parsed case")
    chunks = []
    for idx, match in enumerate(labels):
        finish = labels[idx + 1].start() if idx + 1 < len(labels) else len(body)
        chunks.append((match.group("label"), body[match.start():finish]))
    i = next(i for i, item in enumerate(chunks) if item[0] == higher)
    j = next(i for i, item in enumerate(chunks) if item[0] == lower)
    chunks[i], chunks[j] = chunks[j], chunks[i]
    new_body = "".join(chunk for _, chunk in chunks)
    new_scoped = scoped[:body_start] + new_body + scoped[body_end:]
    new_source = source[:start] + new_scoped + source[end:]
    after_modules = parse_verilog(new_source)
    after_module = _select_module(after_modules, module.name)
    after_fsm = next((candidate for block in after_module.always_blocks
                      for candidate in block.fsms
                      if candidate.case_expr.strip() == fsm.case_expr.strip()), None) \
        if after_module else None
    after_labels = [item.label for item in after_fsm.items] if after_fsm else []
    if not after_fsm or higher not in after_labels or lower not in after_labels:
        raise ValueError("PRIORITY_REORDER produced an unparsable case")
    return new_source, {
        "rewritten": 1,
        "edit": f"case {fsm.case_expr}: {higher} <-> {lower}",
        "parser_backed": True,
        "module": module.name,
        "case_expr": fsm.case_expr,
        "before_order": [item.label for item in fsm.items],
        "after_order": after_labels,
    }


def _require_text(payload: dict, key: str) -> str:
    value = payload.get(key)
    if value is None or str(value) == "":
        raise ValueError(f"{key} is required")
    return str(value)


def _select_module(modules: list, name: object):
    if name:
        return next((m for m in modules if m.name == str(name)), None)
    return modules[0] if modules else None


def _module_span(source: str, name: str) -> tuple[int, int] | None:
    match = re.search(
        rf"\bmodule\s+{re.escape(name)}\b.*?\bendmodule\b", source, re.S)
    return (match.start(), match.end()) if match else None


def _reset_branch_spans(text: str, *, reset_signal: object = None) -> list[tuple[int, int]]:
    signal = re.escape(str(reset_signal)) if reset_signal else r"[A-Za-z_]\w*"
    spans = []
    for match in re.finditer(
            rf"\bif\s*\(\s*!\s*(?P<signal>{signal})\s*\)", text):
        start = match.end()
        while start < len(text) and text[start].isspace():
            start += 1
        end = _statement_end(text, start)
        spans.append((start, end))
    return spans


def _statement_end(text: str, start: int) -> int:
    if text.startswith("begin", start):
        return _balanced_end(text, start)
    semi = text.find(";", start)
    return len(text) if semi < 0 else semi + 1


def _balanced_end(text: str, begin_idx: int) -> int:
    """Return the index just past the matching ``end`` for a ``begin``.

    Reset restoration needs the same small block balancer as the Verilog
    parser.  Keeping the helper local avoids making the action layer depend on
    a parser private symbol, while the case keywords prevent ``endcase`` and
    nested case items from being mistaken for the enclosing ``end``.
    """
    depth = 0
    i = begin_idx
    token = re.compile(r"\b(begin|end|casez|casex|case|endcase)\b")
    while i < len(text):
        match = token.search(text, i)
        if match is None:
            break
        word = match.group(1)
        if word in {"begin", "casez", "casex", "case"}:
            depth += 1
        else:
            depth -= 1
        if depth == 0:
            return match.end()
        i = match.end()
    return len(text)


def _balanced_case_end(text: str, start: int) -> int:
    match = re.search(r"\bendcase\b", text[start:])
    if not match:
        return len(text)
    return start + match.start()


def _replace_literal_in_spans(text: str, spans: list[tuple[int, int]],
                              target: str, replacement: str, *, count: int) -> tuple[str, int]:
    if count <= 0:
        return text, 0
    edits = []
    remaining = count
    for start, end in spans:
        for match in re.finditer(re.escape(target), text[start:end]):
            if remaining <= 0:
                break
            edits.append((start + match.start(), start + match.end()))
            remaining -= 1
    if not edits:
        return text, 0
    out = text
    for start, end in reversed(edits):
        out = out[:start] + replacement + out[end:]
    return out, len(edits)


def _require_parse_after(source: str, module_name: str, domain: str) -> None:
    modules = parse_verilog(source)
    if not any(module.name == module_name for module in modules):
        raise ValueError(f"{domain} rewrite made module unparsable: {module_name}")


def _module_block(source: str, module: str) -> str | None:
    match = re.search(
        rf"\bmodule\s+{re.escape(module)}\b.*?\bendmodule\b", source, re.S)
    return match.group(0) if match else None


def _code_regions(text: str) -> list:
    """Spans of non-comment code (// and /* */ stripped)."""
    regions: list = []
    i, n = 0, len(text)
    code_start = 0
    while i < n:
        if text.startswith("//", i):
            if code_start < i:
                regions.append((code_start, i))
            j = text.find("\n", i)
            i = n if j == -1 else j + 1
            code_start = i
        elif text.startswith("/*", i):
            if code_start < i:
                regions.append((code_start, i))
            j = text.find("*/", i)
            i = n if j == -1 else j + 2
            code_start = i
        else:
            i += 1
    if code_start < n:
        regions.append((code_start, n))
    return regions
