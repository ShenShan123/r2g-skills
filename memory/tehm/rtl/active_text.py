"""Bounded, offset/line-preserving Verilog active text (core parser v0.4 fallback).

Contract: memory/evaluation/research_r5_core_parser_v04_contract_20260929.md. Same
semantics as the committed I2C v3 binder layer: comments masked, display-task string
literals masked, at most one `timescale before the first module, and non-nested
`ifdef/`ifndef/`else/`endif evaluated against an explicit macro set. Anything else
raises ValueError (callers fail closed).
"""
from __future__ import annotations

import re

IDENT = r"[A-Za-z_][A-Za-z_0-9]*"
SYSTEM_TASKS = r"\$(?:display|write|strobe|monitor)"
TIMESCALE = re.compile(r"`timescale\s+\d+\s*(?:s|ms|us|ns|ps|fs)\s*/\s*\d+\s*(?:s|ms|us|ns|ps|fs)")


def _blank(text: str) -> str:
    return "".join("\n" if c == "\n" else " " for c in text)


def _lex(source: str) -> tuple[str, list[tuple[int, int]]]:
    out, strings, i, n = [], [], 0, len(source)
    while i < n:
        if source.startswith("//", i):
            j = source.find("\n", i)
            j = n if j < 0 else j
            out.append(_blank(source[i:j])); i = j
        elif source.startswith("/*", i):
            j = source.find("*/", i + 2)
            if j < 0:
                raise ValueError("unterminated_comment")
            out.append(_blank(source[i:j + 2])); i = j + 2
        elif source[i] == '"':
            j = i + 1
            while j < n and source[j] != '"':
                if source[j] == "\n":
                    raise ValueError("unterminated_string")
                j += 2 if source[j] == "\\" else 1
            if j >= n:
                raise ValueError("unterminated_string")
            strings.append((i, j + 1))
            out.append(source[i:j + 1]); i = j + 1
        else:
            out.append(source[i]); i += 1
    return "".join(out), strings


def _mask_strings(text: str, strings: list[tuple[int, int]]) -> str:
    masked = text
    for start, stop in strings:
        masked = masked[:start] + _blank(masked[start:stop]) + masked[stop:]
    for start, _ in strings:
        depth, k = 0, start - 1
        while k >= 0:
            if masked[k] == ")":
                depth += 1
            elif masked[k] == "(":
                if depth == 0:
                    break
                depth -= 1
            k -= 1
        if k < 0 or not re.search(SYSTEM_TASKS + r"\s*$", masked[:k]):
            raise ValueError("unsupported_string_literal")
    return masked


def active_text(source: str, defined_macros=()) -> str:
    """Return the masked active text, or raise ValueError on any unsupported construct."""
    defined = set(defined_macros)
    text, strings = _lex(source)
    text = _mask_strings(text, strings)
    offset, out, block, seen_timescale = 0, [], None, False
    first_module = re.search(r"\bmodule\b", text)
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("`"):
            if TIMESCALE.fullmatch(stripped):
                if seen_timescale or (first_module and offset > first_module.start()):
                    raise ValueError("unsupported_timescale_position")
                seen_timescale = True
            elif re.fullmatch(r"`(ifdef|ifndef)\s+" + IDENT, stripped):
                if block is not None:
                    raise ValueError("nested_conditional")
                kind, name = stripped[1:].split()
                block = {"active": (name in defined) == (kind == "ifdef"), "else": False}
            elif stripped == "`else":
                if block is None or block["else"]:
                    raise ValueError("unbalanced_conditional")
                block = {"active": not block["active"], "else": True}
            elif stripped == "`endif":
                if block is None:
                    raise ValueError("unbalanced_conditional")
                block = None
            else:
                raise ValueError("unsupported_directive")
            out.append(_blank(line))
        elif block is not None and not block["active"]:
            out.append(_blank(line))
        else:
            out.append(line)
        offset += len(line)
    if block is not None:
        raise ValueError("unbalanced_conditional")
    active = "".join(out)
    if re.search(r'[`"\\]|[^\x09\x0a\x0d\x20-\x7e]', active):
        raise ValueError("unsupported_active_lexical_construct")
    return active
