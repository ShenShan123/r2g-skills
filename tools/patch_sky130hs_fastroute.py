#!/usr/bin/env python3
"""Make the sky130hs routing-adjustment hook consume the R2G knob.

The upstream sky130hs hook historically hard-coded ``0.2``.  That makes a
``ROUTING_LAYER_ADJUSTMENT`` config edit a syntactic change with no executed
effect.  This helper applies the small, content-checked patch to a selected
ORFS checkout so a fresh machine can reproduce the source freeze used by the
TEHM campaign.  It is idempotent and keeps a ``.orig`` backup beside the file.

Usage::

    ORFS_ROOT=/path/to/OpenROAD-flow-scripts \
      python3 tools/patch_sky130hs_fastroute.py [--check]

``--check`` exits 0 only when the hook directly consumes the environment knob.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


OLD = "set_global_routing_layer_adjustment $::env(MIN_ROUTING_LAYER)-$::env(MAX_ROUTING_LAYER) 0.2"
NEW = (
    "# R2G: preserve the 0.2 control default while consuming the configured knob.\n"
    "set_global_routing_layer_adjustment "
    "$::env(MIN_ROUTING_LAYER)-$::env(MAX_ROUTING_LAYER) "
    "[expr {[info exists ::env(ROUTING_LAYER_ADJUSTMENT)] ? "
    "$::env(ROUTING_LAYER_ADJUSTMENT) : 0.2}]"
)


def _path() -> Path:
    root = os.environ.get("ORFS_ROOT") or os.environ.get("FLOW_DIR")
    if not root:
        raise SystemExit("ERROR: set ORFS_ROOT (or FLOW_DIR) to an ORFS checkout")
    flow = Path(root).expanduser().resolve()
    if flow.name == "flow":
        return flow / "platforms" / "sky130hs" / "fastroute.tcl"
    return flow / "flow" / "platforms" / "sky130hs" / "fastroute.tcl"


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    path = _path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot read {path}: {exc}", file=sys.stderr)
        return 1
    if "$::env(ROUTING_LAYER_ADJUSTMENT)" in text and "set_global_routing_layer_adjustment" in text:
        print(f"sky130hs fastroute hook already consumes ROUTING_LAYER_ADJUSTMENT: {path}")
        return 0
    if check:
        print(f"sky130hs fastroute hook is hard-coded/no-op: {path}", file=sys.stderr)
        return 2
    if OLD not in text:
        print(f"ERROR: expected hard-coded 0.2 hook not found in {path}", file=sys.stderr)
        return 1
    backup = Path(str(path) + ".orig")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print(f"patched {path} (backup: {backup})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
