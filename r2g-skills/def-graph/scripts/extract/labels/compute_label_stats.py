#!/usr/bin/env python3
"""Roll per-cell/per-net label CSVs into a compact per-design statistics JSON.

Pure stdlib (csv + statistics + math). Reads the four label CSVs from a labels
directory and writes reports/labels_stats.json. A label whose CSV is missing or
empty is recorded with status "skipped".
"""
import csv
import json
import math
import os
import statistics
import sys

# label name -> (csv filename, label column, raw-metric column)
SPECS = {
    "congestion": {"file": "cell_congestion.csv", "label": "label", "metric": "cell_congestion"},
    "wirelength": {"file": "wirelength.csv", "label": "label", "metric": "WireLength_um"},
    "timing": {"file": "timing_features.csv", "label": "label", "metric": "Path_Delay_ns"},
    "irdrop": {"file": "ir_drop.csv", "label": "label", "metric": "IR_Drop_mV"},
}


def _percentile(sorted_vals, q):
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    idx = q * (n - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - idx) + sorted_vals[hi] * (idx - lo)


def numeric_summary(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    return {
        "min": vals[0],
        "max": vals[-1],
        "mean": statistics.fmean(vals),
        "p50": _percentile(vals, 0.50),
        "p90": _percentile(vals, 0.90),
        "p95": _percentile(vals, 0.95),
        "p99": _percentile(vals, 0.99),
    }


def _col_floats(rows, col):
    out = []
    for row in rows:
        try:
            v = float(row[col])
        except (ValueError, KeyError, TypeError):
            continue
        # Drop NaN (NaN != NaN): float("nan") does NOT raise, so without this an
        # all-NaN label column sailed past the honesty gate below (status 'ok' with
        # NaN summary stats) AND json.dump emitted invalid-JSON `NaN` tokens. An
        # all-NaN column must read as "no numeric values" -> 'invalid' (2026-07-07).
        if v == v:
            out.append(v)
    return out


def _is_true(val):
    return str(val).strip().lower() == "true"


def summarize(labels_dir, name, spec):
    path = os.path.join(labels_dir, spec["file"])
    if not os.path.exists(path):
        return {"status": "skipped", "reason": "csv missing"}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = list(reader)
    if not rows:
        return {"status": "skipped", "reason": "csv empty"}

    # Honesty gate: rows exist but the label column is absent or never numeric
    # -> the CSV is NOT a usable label set (e.g. an interrupted extractor left
    # a raw tool dump at the canonical path — the 2026-07-05 irdrop incident).
    # Report "invalid" so ingest/dashboards see the truth instead of "ok".
    label_vals = _col_floats(rows, spec["label"])
    if not label_vals:
        reason = (f"label column '{spec['label']}' missing from header "
                  f"(raw/unprocessed csv? header: {header[:6]})"
                  if spec["label"] not in header
                  else f"label column '{spec['label']}' has no numeric values")
        return {"status": "invalid", "reason": reason, "rows": len(rows)}

    res = {
        "status": "ok",
        "rows": len(rows),
        "label": numeric_summary(label_vals),
        spec["metric"]: numeric_summary(_col_floats(rows, spec["metric"])),
    }
    if name == "wirelength":
        sig = sum(1 for r in rows if _is_true(r.get("mask_wl", "")))
        res["signal_nets"] = sig
        res["masked_nets"] = len(rows) - sig
    elif name == "timing":
        inp = sum(1 for r in rows if _is_true(r.get("in_sta_path", "")))
        res["in_path"] = inp
        res["not_in_path"] = len(rows) - inp
    elif name == "irdrop":
        res["has_irdrop"] = any(_is_true(r.get("has_irdrop", "")) for r in rows)
        p95s = _col_floats(rows, "P95_mV")
        res["p95_mV"] = p95s[0] if p95s else None
    return res


def build_report(labels_dir, out_path, design="unknown", platform="unknown"):
    report = {"design": design, "platform": platform, "labels": {}}
    for name, spec in SPECS.items():
        report["labels"][name] = summarize(labels_dir, name, spec)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    return report


def main():
    if len(sys.argv) < 3:
        print("usage: compute_label_stats.py <labels_dir> <out_json> [design] [platform]")
        sys.exit(1)
    labels_dir = sys.argv[1]
    out_path = sys.argv[2]
    design = sys.argv[3] if len(sys.argv) > 3 else "unknown"
    platform = sys.argv[4] if len(sys.argv) > 4 else "unknown"
    report = build_report(labels_dir, out_path, design, platform)
    ok = sum(1 for v in report["labels"].values() if v["status"] == "ok")
    print(f"Wrote {out_path}: {ok}/{len(report['labels'])} label sets present")
    for name, v in report["labels"].items():
        if v["status"] != "ok":
            print(f"WARNING: label '{name}' {v['status']}: {v.get('reason', '')}",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
