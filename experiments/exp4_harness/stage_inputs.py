#!/usr/bin/env python3
"""Copy and hash the frozen Exp4 B–F inputs, without changing source permissions.

Run on memlab216. Only copies raw EDA artifacts and flattened technology files;
no R2G converter, graph, labels, features, truth, or scoring code enters inputs.
Defaults to a plan; --apply creates immutable file copies in the campaign root.
"""
import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from select_cohort import REQUIRED, PLATFORMS, sha, validate

TECH_FILES = {
    "nangate45": ("NangateOpenCellLibrary.tech.lef", "NangateOpenCellLibrary.macro.mod.lef", "NangateOpenCellLibrary_typical.lib"),
    "sky130hd": ("sky130_fd_sc_hd.tlef", "sky130_fd_sc_hd_merged.lef", "sky130_fd_sc_hd__tt_025C_1v80.lib"),
    "gf180": ("gf180mcu_5LM_1TM_9K_9t_tech.lef", "gf180mcu_5LM_1TM_9K_9t_sc.lef", "gf180mcu_fd_sc_mcu9t5v0__ff_n40C_5v50.lib.gz"),
}


def copy_checked(src, dst):
    src, dst = Path(src), Path(dst)
    before = src.stat()
    if not src.is_file() or before.st_size == 0:
        raise ValueError(f"Missing or empty source {src}")
    shutil.copyfile(src, dst, follow_symlinks=True)
    digest = sha(dst)
    after = src.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"Source changed during copy: {src}")
    if sha(src) != digest:
        raise RuntimeError(f"Copy checksum mismatch: {src}")
    if os.stat(src).st_dev == os.stat(dst).st_dev and os.stat(src).st_ino == os.stat(dst).st_ino:
        raise RuntimeError(f"Refusing shared inode: {src}")
    dst.chmod(0o444)
    return {"origin": str(src.resolve()), "sha256": digest, "bytes": after.st_size}


def stage_bundle(sources, destination, identity):
    destination = Path(destination)
    if destination.exists():
        metadata = destination / "STAGED.json"
        if not metadata.is_file():
            raise RuntimeError(f"Destination exists without provenance: {destination}")
        rec = json.loads(metadata.read_text())
        if rec.get("identity") != identity or set(rec["files"]) != set(sources):
            raise RuntimeError(f"Existing input bundle has different identity: {destination}")
        for name, source in sources.items():
            if sha(destination / name) != rec["files"][name]["sha256"] or sha(source) != rec["files"][name]["sha256"]:
                raise RuntimeError(f"Stale or modified input: {destination / name}")
        return rec, "verified_existing"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".staging-", dir=destination.parent))
    try:
        rec = {"schema": 1, "identity": identity,
               "files": {name: copy_checked(path, temporary / name) for name, path in sources.items()}}
        metadata = temporary / "STAGED.json"
        metadata.write_text(json.dumps(rec, indent=2) + "\n")
        metadata.chmod(0o444)
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return rec, "copied"


def audit_inputs(evidence):
    total_bytes = 0
    total_files = 0
    for bundle in evidence["bundles"]:
        root = Path(bundle["destination"])
        expected = set(bundle["files"]) | {"STAGED.json"}
        if {p.name for p in root.iterdir()} != expected:
            raise RuntimeError(f"Unexpected files in raw-input bundle: {root}")
        for name, record in bundle["files"].items():
            path, source = root / name, Path(record["origin"])
            if path.is_symlink() or path.stat().st_mode & 0o222:
                raise RuntimeError(f"Input is not an independent read-only file: {path}")
            if (path.stat().st_dev, path.stat().st_ino) == (source.stat().st_dev, source.stat().st_ino):
                raise RuntimeError(f"Input is hardlinked to original: {path}")
            if sha(path) != record["sha256"] or sha(source) != record["sha256"]:
                raise RuntimeError(f"Input or source digest changed: {path}")
            total_bytes += path.stat().st_size
            total_files += 1
    return {"bundles": len(evidence["bundles"]), "raw_files": total_files,
            "raw_bytes": total_bytes, "extra_files": 0, "hardlinked_sources": 0,
            "checksum_mismatches": 0, "status": "pass"}


def plan(manifest, out, platform_root):
    tasks = []
    for split in ("dev", "test"):
        for row in manifest["splits"][split]:
            source = Path(row["source_dir"])
            for name, key in (("config.mk", "config_sha256"), ("result.json", "result_sha256")):
                if row.get(key) and sha(source / name) != row[key]:
                    raise RuntimeError(f"Source metadata changed since inventory: {source / name}")
            tasks.append({"destination": str(out / "data" / split / row["design"]),
                          "sources": {n: str(source / n) for n in REQUIRED},
                          "identity": {"design": row["design"], "platform": row["platform"], "split": split,
                                       "family": row["family"], "cells": row["cells"],
                                       "drc_status": row.get("drc_status"), "lvs_status": row.get("lvs_status"),
                                       "source_result_sha256": row.get("result_sha256")}})
    for p in PLATFORMS:
        tasks.append({"destination": str(out / "data" / "platforms" / p),
                      "sources": {n: str(platform_root / p / n) for n in TECH_FILES[p]},
                      "identity": {"platform": p, "kind": "technology_inputs"}})
    return tasks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--platform-root", type=Path, required=True,
                    help="Existing flattened raw-technology directory, containing one directory per platform")
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--audit-only", action="store_true")
    a = ap.parse_args()
    manifest = json.loads(a.manifest.read_text()); validate(manifest)
    if a.audit_only:
        evidence = json.loads((a.out / "evidence" / "staged_inputs.json").read_text())
        if evidence["manifest_sha256"] != sha(a.manifest):
            raise RuntimeError("Staging evidence belongs to another cohort manifest")
        audit = audit_inputs(evidence)
        (a.out / "evidence" / "staged_inputs_audit.json").write_text(json.dumps(audit, indent=2) + "\n")
        print(json.dumps(audit)); return
    tasks = plan(manifest, a.out, a.platform_root)
    missing = [p for t in tasks for p in t["sources"].values() if not Path(p).is_file()]
    if missing:
        raise SystemExit(json.dumps({"missing": missing}, indent=2))
    if not a.apply:
        print(json.dumps({"mode": "plan", "bundles": len(tasks), "tasks": tasks}, indent=2)); return
    evidence = {"schema": 1, "manifest_sha256": sha(a.manifest), "bundles": []}
    for task in tasks:
        rec, status = stage_bundle(task["sources"], Path(task["destination"]), task["identity"])
        evidence["bundles"].append({"destination": task["destination"], "status": status, **rec})
        print(status, task["destination"], flush=True)
    evidence_path = a.out / "evidence" / "staged_inputs.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main()
