"""Compare graph values exactly, retaining tensor dtypes and NaN positions.

Usage: cmp_ds.py BASE CANDIDATE [--report FILE] [--batch]
Batch layout: ROOT/DESIGN/project/{dataset,features,labels}.
PT bytes and manifests contain run-specific serialization/provenance; graph
attributes and feature/label CSV bytes are compared instead.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import torch


def differences(a, b, path="root"):
    """Return (leaf_count, mismatch_paths); never coerce a tensor's dtype."""
    if type(a) is not type(b):
        return 1, [path + ":type"]
    if torch.is_tensor(a):
        if a.dtype != b.dtype or a.shape != b.shape or a.layout != b.layout:
            return 1, [path + ":tensor_metadata"]
        if torch.equal(a, b):
            return 1, []
        if a.is_complex():
            return differences(torch.view_as_real(a), torch.view_as_real(b), path)
        if a.is_floating_point():
            equal = (a == b) | (torch.isnan(a) & torch.isnan(b))
            if bool(equal.all()):
                return 1, []
        return 1, [path + ":tensor_values"]
    if hasattr(a, "to_dict"):
        return differences(a.to_dict(), b.to_dict(), path)
    if isinstance(a, dict):
        count, bad = 0, []
        if a.keys() != b.keys():
            bad.append(path + ":keys")
        for key in a:
            if key in b:
                n, mismatches = differences(a[key], b[key], path + "/" + repr(key))
                count += n
                bad.extend(mismatches)
        return count, bad
    if isinstance(a, (list, tuple)):
        count, bad = 0, []
        if len(a) != len(b):
            bad.append(path + ":length")
        for i, (aa, bb) in enumerate(zip(a, b)):
            n, mismatches = differences(aa, bb, path + f"[{i}]")
            count += n
            bad.extend(mismatches)
        return count, bad
    if isinstance(a, float) and math.isnan(a) and math.isnan(b):
        return 1, []
    return 1, [] if a == b else [path + ":value"]


def compare_dataset(base, candidate):
    left = {x.name: x for x in base.glob("[b-f]_graph*.pt")}
    right = {x.name: x for x in candidate.glob("[b-f]_graph*.pt")}
    files = []
    for name in sorted(left.keys() | right.keys()):
        if name not in left or name not in right:
            files.append({"file": name, "passed": False, "differences": ["missing_file"]})
            continue
        a = torch.load(left[name], map_location="cpu", weights_only=False)
        b = torch.load(right[name], map_location="cpu", weights_only=False)
        count, bad = differences(a, b)
        files.append({"file": name, "fields": count, "passed": not bad, "differences": bad})
        del a, b
    return {"passed": bool(files) and all(x["passed"] for x in files), "files": files}


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compare_csvs(base, candidate):
    result = []
    for stage in ("features", "labels"):
        a = {x.name: x for x in (base / stage).glob("*.csv")}
        b = {x.name: x for x in (candidate / stage).glob("*.csv")}
        for name in sorted(a.keys() | b.keys()):
            ah = sha256(a[name]) if name in a else None
            bh = sha256(b[name]) if name in b else None
            result.append({"file": f"{stage}/{name}", "baseline_sha256": ah,
                           "candidate_sha256": bh, "passed": ah is not None and ah == bh})
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("base", type=Path)
    ap.add_argument("candidate", type=Path)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--batch", action="store_true")
    args = ap.parse_args()
    torch.set_num_threads(1)
    if args.batch:
        names = sorted({p.parent.parent.parent.name for root in (args.base, args.candidate)
                        for p in root.glob("*/project/dataset/graph_manifest.json")})
    else:
        names = [""]
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "baseline": str(args.base),
              "candidate": str(args.candidate), "comparator_sha256": sha256(Path(__file__)),
              "comparison": "same type/dtype/shape; exact values; same-position NaNs equal; no dtype coercion",
              "designs": []}
    for name in names:
        if args.batch:
            a, b = (root / name / "project" for root in (args.base, args.candidate))
            row = compare_dataset(a / "dataset", b / "dataset")
            row["csvs"] = compare_csvs(a, b)
            row["passed"] &= all(x["passed"] for x in row["csvs"])
        else:
            row = compare_dataset(args.base, args.candidate)
        row["design"] = name or args.base.name
        report["designs"].append(row)
        print(row["design"], "PASS" if row["passed"] else "FAIL", "files", len(row["files"]), flush=True)
    report["passed"] = bool(names) and all(x["passed"] for x in report["designs"])
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("designs", len(names), "passed", report["passed"])
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
