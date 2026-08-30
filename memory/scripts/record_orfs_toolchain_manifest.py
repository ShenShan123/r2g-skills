#!/usr/bin/env python3
"""Record or replay the TEHM ORFS toolchain lock.

This is the one explicit hand-off between R2G discovery and TEHM campaigns:
record once from a selected ORFS tree, then pass the resulting JSON as
``toolchain_manifest`` in a campaign manifest.  It never downloads or builds
anything and therefore is safe to run during a preflight review.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MEMORY_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_orfs_diversity_campaign import preflight_orfs_toolchain  # noqa: E402
from tehm.orfs_toolchain import (  # noqa: E402
    ToolchainManifestError,
    build_toolchain_manifest,
    load_toolchain_manifest,
    validate_toolchain_manifest,
)


def _preflight_for_manifest(manifest: dict, *, orfs_root: Path | None,
                            openroad: str | None, yosys: str | None) -> dict:
    root = (orfs_root or Path(str((manifest.get("orfs") or {}).get("root"))))
    env: dict[str, str] = {}
    # Internal locks must resolve the tree-packaged candidates.  An external
    # diagnostic lock instead replays the exact explicit executable paths.
    if manifest.get("binding_status") == "bound_external":
        tools = manifest.get("tools") or {}
        env["OPENROAD_EXE"] = str(openroad or (tools.get("openroad") or {}).get("path") or "")
        env["YOSYS_EXE"] = str(yosys or (tools.get("yosys") or {}).get("path") or "")
    else:
        if manifest.get("toolchain_root"):
            env["R2G_PREFIX"] = str(manifest["toolchain_root"])
        tools = manifest.get("tools") or {}
        if (tools.get("openroad") or {}).get("source") in {
                "r2g_prefix", "orfs_explicit"}:
            env["OPENROAD_EXE"] = str((tools.get("openroad") or {}).get("path") or "")
        if (tools.get("yosys") or {}).get("source") in {
                "r2g_prefix", "orfs_explicit"}:
            env["YOSYS_EXE"] = str((tools.get("yosys") or {}).get("path") or "")
        if openroad:
            env["OPENROAD_EXE"] = openroad
        if yosys:
            env["YOSYS_EXE"] = yosys
    if not env:
        # An empty environment is intentional: no ambient PATH or host-wide
        # pin is allowed to displace a packaged candidate during replay.
        env = {}
    return preflight_orfs_toolchain({"orfs_root": str(root)}, env=env)


def _record(args: argparse.Namespace) -> int:
    env: dict[str, str] = {}
    if args.prefix:
        env["R2G_PREFIX"] = str(args.prefix.resolve())
    if args.openroad:
        env["OPENROAD_EXE"] = str(args.openroad)
    if args.yosys:
        env["YOSYS_EXE"] = str(args.yosys)
    report = preflight_orfs_toolchain(
        {"orfs_root": str(args.orfs_root.resolve())}, env=env)
    try:
        manifest = build_toolchain_manifest(
            report, pdk_root=args.pdk_root,
            require_internal=not args.allow_external,
            allow_dirty=args.allow_dirty)
    except ToolchainManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    temporary.replace(output)
    print(json.dumps({"manifest": str(output),
                      "manifest_digest": manifest["manifest_digest"],
                      "binding_status": manifest["binding_status"]},
                     indent=2, sort_keys=True))
    return 0


def _check(args: argparse.Namespace) -> int:
    try:
        manifest = load_toolchain_manifest(args.manifest)
    except ToolchainManifestError as exc:
        print(json.dumps({"valid": False, "reasons": [str(exc)]}, indent=2))
        return 1
    report = _preflight_for_manifest(
        manifest, orfs_root=args.orfs_root,
        openroad=(str(args.openroad) if args.openroad else None),
        yosys=(str(args.yosys) if args.yosys else None))
    pdk_root = (manifest.get("pdk") or {}).get("root")
    result = validate_toolchain_manifest(
        manifest, report, pdk_root=pdk_root)
    result["preflight_status"] = report.get("status")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("valid") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record", help="record a selected toolchain")
    record.add_argument("--orfs-root", type=Path, required=True)
    record.add_argument("--output", type=Path, required=True)
    record.add_argument("--openroad", type=Path)
    record.add_argument("--yosys", type=Path)
    record.add_argument("--prefix", type=Path,
                        help="user toolchain root for classifying explicit binaries")
    record.add_argument("--pdk-root", type=Path)
    record.add_argument("--allow-external", action="store_true",
                        help="record an explicitly bound external diagnostic")
    record.add_argument("--allow-dirty", action="store_true",
                        help="record a dirty ORFS tree as diagnostic evidence")
    record.set_defaults(handler=_record)

    check = sub.add_parser("check", help="replay an existing toolchain lock")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--orfs-root", type=Path)
    check.add_argument("--openroad", type=Path)
    check.add_argument("--yosys", type=Path)
    check.set_defaults(handler=_check)
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
