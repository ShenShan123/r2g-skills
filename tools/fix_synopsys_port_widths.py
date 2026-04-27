#!/usr/bin/env python3
"""Strip `[N:M]` width annotations from port-list identifiers in Faraday-style
Verilog modules. Yosys's frontend requires plain identifiers in the port list;
Faraday/Synopsys files use `name[7:0]` shorthand inside the `(...)` port list
even though widths are also declared in the body via `input [7:0] name;`.

Edits files in place. Only touches identifiers inside the first `(...)` block
following a `module <name>` header. Strips:
    name[N:M] -> name

Use:
    python3 fix_dsp_ports.py file1.v [file2.v ...]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Match `name[N:M]` where the name is a Verilog identifier and the brackets
# contain a width.
PORT_RE = re.compile(r"\b([A-Za-z_][A-Za-z_0-9]*)\s*\[\s*[0-9]+\s*:\s*[0-9]+\s*\]")


def fix_module_port_list(text: str) -> tuple[str, int]:
    """Find each `module ...(...);` header and strip [N:M] from inside the
    parenthesized port list. Returns (new_text, total_substitutions)."""
    out: list[str] = []
    pos = 0
    edits = 0
    # Use a non-greedy-and-balanced parser: find each `module <name>` keyword,
    # then capture parenthesized list (handle nested comments cautiously).
    module_iter = list(re.finditer(r"\bmodule\s+([A-Za-z_][A-Za-z_0-9]*)\s*\(", text))
    if not module_iter:
        return text, 0
    last = 0
    for m in module_iter:
        out.append(text[last:m.start()])
        # find matching close paren followed by ';'
        depth = 1
        i = m.end()
        n = len(text)
        while i < n and depth > 0:
            ch = text[i]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        # text[m.start():m.end()] is "module NAME ("
        # text[m.end():i] is the port list body (excluding closing ')')
        # text[i] is the closing ')'
        head = text[m.start():m.end()]
        body = text[m.end():i]
        new_body, k = PORT_RE.subn(r"\1", body)
        edits += k
        out.append(head + new_body + text[i] if i < n else head + new_body)
        last = i + 1 if i < n else n
    out.append(text[last:])
    return "".join(out), edits


def main(argv: list[str]) -> int:
    total = 0
    files = 0
    for arg in argv[1:]:
        path = Path(arg)
        if not path.is_file():
            print(f"skip (not a file): {path}", file=sys.stderr)
            continue
        original = path.read_text(encoding="utf-8", errors="ignore")
        new, edits = fix_module_port_list(original)
        if edits > 0:
            path.write_text(new, encoding="utf-8")
            print(f"{path}: {edits} port-width edits")
            total += edits
            files += 1
    print(f"---\nTotal: {total} edits across {files} files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
