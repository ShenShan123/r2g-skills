#!/usr/bin/env python3
from pathlib import Path
import json
import sys

TEMPLATE_DIRS = [
    "input", "rtl", "tb", "constraints", "lint", "sim", "synth", "backend", "drc", "lvs", "rcx", "reports"
]


def main():
    if len(sys.argv) < 2:
        print("usage: init_project.py <design-name> [base-dir]", file=sys.stderr)
        sys.exit(1)

    design_name = sys.argv[1]
    base_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("design_cases")
    root = base_dir / design_name
    root.mkdir(parents=True, exist_ok=True)

    for d in TEMPLATE_DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)

    metadata = {
        "design_name": design_name,
        "status": "initialized"
    }
    (root / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(root)


if __name__ == "__main__":
    main()
