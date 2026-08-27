#!/usr/bin/env python3
"""Close a derived Verilog netlist over hierarchical VDD/VSS supplies."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from power_verilog_from_spice import propagate_hierarchical_power_ports


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("verilog", type=Path)
    ap.add_argument("top_module")
    ap.add_argument("output", type=Path)
    ap.add_argument("--receipt", type=Path, required=True)
    args = ap.parse_args()

    source = args.verilog.read_text()
    output, stats = propagate_hierarchical_power_ports(source)
    receipt = {
        "schema": "r2g.normalize_power_connectivity.v1",
        "source": str(args.verilog.resolve()),
        "source_sha256": _sha(source),
        "output": str(args.output.resolve()),
        "output_sha256": _sha(output),
        "top_module": args.top_module,
        **stats,
    }
    _write(args.output, output)
    _write(args.receipt, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))
    if stats["positional_child_instances_skipped"]:
        raise SystemExit("positional powered-child instance cannot be normalized safely")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
