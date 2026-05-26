#!/usr/bin/env python3
from pathlib import Path
import sys
import re

TEMPLATE = """design_name: {design_name}
top_module: {top_module}
description: {description}
inputs:
  - name: clk
    width: 1
    desc: main clock
  - name: rst_n
    width: 1
    desc: active-low reset
outputs: []
clock:
  name: clk
  edge: posedge
reset:
  name: rst_n
  active_level: low
  sync_or_async: async
functional_requirements:
  - fill me
timing_target: 10
target_flow: orfs
target_platform: nangate45
verification_targets:
  - reset behavior
assumptions:
  - generated from free-form spec, review before backend
"""


def guess_design_name(text: str) -> str:
    lower = text.lower()
    if "fifo" in lower:
        return "simple_fifo"
    if "counter" in lower:
        return "counter"
    if "arbiter" in lower:
        return "arbiter"

    patterns = [
        r"(?:module|called|named)\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"top[_ -]?module\s*(?:is|=)?\s*([A-Za-z_][A-Za-z0-9_]*)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return m.group(1)

    return "design_top"


def guess_description(text: str) -> str:
    first = " ".join(text.strip().splitlines()[:2]).strip()
    return first or "hardware block from free-form spec"


def main():
    if len(sys.argv) < 3:
        print("usage: normalize_spec.py <raw-spec.md> <normalized-spec.yaml>", file=sys.stderr)
        sys.exit(1)

    raw_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    text = raw_path.read_text(encoding="utf-8")
    design_name = guess_design_name(text)
    desc = guess_description(text)
    yaml_text = TEMPLATE.format(
        design_name=design_name,
        top_module=design_name,
        description=desc.replace(':', '-')
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml_text, encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
