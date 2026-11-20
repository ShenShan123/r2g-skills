#!/usr/bin/env python3
"""Verify the 78 transferred image inputs and freeze their file permissions."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def def_geometry(path):
    text = Path(path).read_text()
    units = re.search(r"\bUNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;", text)
    area = re.search(r"\bDIEAREA\s+(.*?);", text, re.S)
    nets = re.search(r"^\s*NETS\s+\d+\s*;(.*?)^\s*END\s+NETS\b", text, re.M | re.S)
    if units is None or area is None or nets is None:
        raise ValueError("DEF is missing UNITS, DIEAREA, or regular NETS section")
    dbu = int(units.group(1))
    points = [(int(x), int(y)) for x, y in re.findall(r"\(\s*(-?\d+)\s+(-?\d+)\s*\)", area.group(1))]
    if dbu <= 0 or len(points) < 2:
        raise ValueError("Invalid DEF units or die coordinates")
    xs, ys = zip(*points)
    die = [min(xs), min(ys), max(xs), max(ys)]
    routing = re.findall(r"\+\s+(ROUTED|FIXED|COVER)\b", nets.group(1))
    return {"dbu_per_micron": dbu, "die_dbu": die,
            "die_um": [float(Fraction(v, dbu)) for v in die],
            "die_origin_zero": die[:2] == [0, 0],
            "regular_net_routing_statements": len(routing)}


def same_physical_die(left, right):
    return all(Fraction(a, left["dbu_per_micron"]) == Fraction(b, right["dbu_per_micron"])
               for a, b in zip(left["die_dbu"], right["die_dbu"]))


def audit(root):
    root = Path(root).resolve()
    cohort = json.loads((root / "cohort.json").read_text())
    issues, bundles, chmod_files = [], [], []
    verified = 0

    def issue(path, reason):
        issues.append({"path": str(path.relative_to(root)), "reason": reason})

    def bundle(directory, expected_identity=None):
        nonlocal verified
        staged = directory / "STAGED.json"
        record = json.loads(staged.read_text())
        if expected_identity:
            for key, value in expected_identity.items():
                if record.get(key) != value:
                    issue(staged, f"identity {key}: expected {value}, found {record.get(key)}")
        expected_files = set(record["files"]) | {"STAGED.json"}
        actual_files = {p.name for p in directory.iterdir()}
        if actual_files != expected_files:
            issue(directory, f"file set mismatch: missing {sorted(expected_files - actual_files)}, extra {sorted(actual_files - expected_files)}")
        checked = {}
        for filename, metadata in record["files"].items():
            path = directory / filename
            if not path.is_file():
                issue(path, "missing input file")
                continue
            digest = sha(path)
            size = path.stat().st_size
            checked[filename] = {"sha256": digest, "bytes": size}
            verified += 1
            if digest != metadata["sha256"] or size != metadata["bytes"]:
                issue(path, "SHA-256 or byte count differs from STAGED.json")
            chmod_files.append(path)
        chmod_files.append(staged)
        return {"directory": str(directory.relative_to(root)),
                "staged_sha256": sha(staged), "files": checked}

    expected_total = sum(len(cohort["splits"][s]) for s in ("dev", "test"))
    if expected_total != 78:
        issue(root / "cohort.json", f"expected 78 cohort designs, found {expected_total}")
    for split, count in (("dev", 18), ("test", 60)):
        rows = cohort["splits"][split]
        expected = {r["design"] for r in rows}
        base = root / "data" / split
        actual = {p.name for p in base.iterdir()} if base.exists() else set()
        if len(rows) != count or len(expected) != count:
            issue(root / "cohort.json", f"{split} must contain {count} distinct designs")
        if actual != expected:
            issue(base, f"design set mismatch: missing {sorted(expected - actual)}, extra {sorted(actual - expected)}")
        for row in rows:
            directory = base / row["design"]
            try:
                record = bundle(directory, {"design": row["design"], "platform": row["platform"], "split": split})
                place = def_geometry(directory / "3_place.def")
                final = def_geometry(directory / "6_final.def")
                if place["regular_net_routing_statements"]:
                    issue(directory / "3_place.def", "placement contains regular-net routing")
                if not same_physical_die(place, final):
                    issue(directory, "placement/final physical die mismatch")
                record.update(place=place, final=final, physical_die_matches=same_physical_die(place, final))
                bundles.append(record)
            except (OSError, ValueError, KeyError) as exc:
                issue(directory, f"{type(exc).__name__}: {exc}")
    platforms = []
    base = root / "data/platforms"
    expected_platforms = {"nangate45", "sky130hd", "gf180"}
    actual_platforms = {p.name for p in base.iterdir()} if base.exists() else set()
    if actual_platforms != expected_platforms:
        issue(base, "platform directory set differs from the three cohort technologies")
    for name in sorted(expected_platforms):
        try:
            platforms.append(bundle(base / name))
        except (OSError, ValueError, KeyError) as exc:
            issue(base / name, f"{type(exc).__name__}: {exc}")
    platform_files = sum(len(b["files"]) for b in platforms)
    if platform_files != 9:
        issue(base, f"expected 9 flattened technology files, found {platform_files}")
    # Permission changes happen only after hashes have been recorded. They do
    # not edit bytes or rewrite the source staging/provenance records.
    for path in chmod_files:
        path.chmod(0o444)
    result = {"schema": 1, "completed_at_utc": datetime.now(timezone.utc).isoformat(),
              "root": str(root), "cohort_sha256": sha(root / "cohort.json"),
              "expected_designs": expected_total, "audited_designs": len(bundles),
              "verified_input_files": verified, "platform_files": platform_files,
              "readonly_files": len(chmod_files), "readonly_mode": "0444",
              "nonzero_die_origin_designs": [b["directory"] for b in bundles
                  if not b["place"]["die_origin_zero"] or not b["final"]["die_origin_zero"]],
              "bundles": bundles, "platforms": platforms, "issues": issues, "passed": not issues}
    destination = root / "evidence/input_audit_203.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()
    result = audit(args.root)
    print(json.dumps({k: result[k] for k in ("passed", "audited_designs", "verified_input_files",
                     "platform_files", "nonzero_die_origin_designs", "issues")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
