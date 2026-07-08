#!/usr/bin/env python3
"""Roll the eight graph-feature CSVs into a compact per-design statistics JSON.

Pure stdlib (csv + statistics + math + json). Reads the feature CSVs from a features
directory and writes reports/features_stats.json. A CSV that is missing or empty is
recorded with status "skipped". Mirrors compute_label_stats.py.
"""
import csv
import json
import math
import os
import statistics
import sys

# csv name -> numeric columns to summarize (min/mean/p50/p90/p95/p99/max).
SUMMARY_COLS = {
    "nodes_gate": ["cell_area", "cell_power"],
    "nodes_net": ["fanout", "pin_count", "num_layer", "hpwl_um"],
    "nodes_iopin": ["nearest_tap_distance_um"],
    "nodes_pin": ["sum_pin_cap_fF"],
    "edges_gate_pin": [],
    "edges_pin_net": [],
    "edges_iopin_net": [],
}
# graph-level metadata scalars surfaced directly (single-row CSV).
METADATA_SCALARS = ["num_cells", "num_nets", "num_ios", "avg_fanout", "C_total"]

# Honesty gate: the identity/key columns each feature CSV MUST carry. A CSV that
# has rows but is missing these is NOT a usable feature set (a raw/wrong-schema
# dump at the canonical path, or a stage killed mid-write) and is reported
# "invalid" instead of "ok" — the feature-side mirror of compute_label_stats.py's
# gate (the 2026-07-05 irdrop raw-dump incident, but for the X side). Without
# this, a truncated nodes_gate.csv silently summarized as "ok" and the graph
# built on it lost features with no signal (2026-07-06 nangate45 audit).
REQUIRED_COLS = {
    "metadata": ["graph_id", "num_cells", "num_nets", "num_ios", "dbu_unit"],
    "nodes_gate": ["graph_id", "inst_name", "master", "cell_type_id", "cell_area", "cell_power"],
    "nodes_net": ["graph_id", "net_name", "net_type_id", "fanout", "pin_count", "num_layer"],
    "nodes_iopin": ["graph_id", "iopin_name", "net_name", "pin_direction_id"],
    "nodes_pin": ["graph_id", "inst_name", "pin_name", "pin_type_id", "sum_pin_cap_fF"],
    "edges_gate_pin": ["graph_id", "inst_name", "pin_name", "cell_type_id", "pin_type_id"],
    "edges_pin_net": ["graph_id", "inst_name", "pin_name", "net_name", "net_type_id"],
    "edges_iopin_net": ["graph_id", "iopin_name", "net_name", "net_type_id"],
}

ORDER = ["metadata", "nodes_gate", "nodes_net", "nodes_iopin", "nodes_pin",
         "edges_gate_pin", "edges_pin_net", "edges_iopin_net"]


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
        # Drop NaN (NaN != NaN): float("nan") does NOT raise, so an all-NaN numeric
        # column would otherwise summarize to NaN stats and json.dump would emit
        # invalid-JSON `NaN` tokens. Empty -> numeric_summary returns None (2026-07-07).
        if v == v:
            out.append(v)
    return out


def _read_rows(path):
    if not os.path.exists(path):
        return None, [], "csv missing"
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = list(reader)
    if not rows:
        return None, header, "csv empty"
    return rows, header, None


def summarize(features_dir, name):
    rows, header, reason = _read_rows(os.path.join(features_dir, f"{name}.csv"))
    if rows is None:
        return {"status": "skipped", "reason": reason}

    # Honesty gate (mirrors compute_label_stats.py): rows exist, but is this a
    # real feature set? A missing identity column == raw/wrong-schema dump; a
    # required column left None on some row == a truncated/interrupted write.
    required = REQUIRED_COLS.get(name, [])
    missing = [c for c in required if c not in header]
    if missing:
        return {"status": "invalid", "rows": len(rows),
                "reason": (f"missing required column(s) {missing} — raw/unprocessed "
                           f"or wrong-schema csv (header: {list(header)[:6]})")}
    truncated = sum(1 for r in rows if any(r.get(c) in (None, "") for c in required))
    if truncated:
        return {"status": "invalid", "rows": len(rows),
                "reason": (f"{truncated} row(s) missing a required column value — "
                           f"interrupted/partial write")}

    res = {"status": "ok", "rows": len(rows)}
    if name == "metadata":
        first = rows[0]
        for col in METADATA_SCALARS:
            try:
                res[col] = float(first[col])
            except (ValueError, KeyError, TypeError):
                res[col] = first.get(col)
    for col in SUMMARY_COLS.get(name, []):
        res[col] = numeric_summary(_col_floats(rows, col))
    return res


def build_report(features_dir, out_path, design="unknown", platform="unknown",
                 spef_present=None):
    report = {"design": design, "platform": platform, "features": {}}
    if spef_present is not None:
        report["spef_present"] = bool(spef_present)
    for name in ORDER:
        report["features"][name] = summarize(features_dir, name)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    return report


def _parse_spef_arg(val):
    if val is None:
        return None
    return str(val).strip().lower() in {"1", "true", "yes"}


def main():
    if len(sys.argv) < 3:
        print("usage: compute_feature_stats.py <features_dir> <out_json> [design] [platform] [spef_present]")
        sys.exit(1)
    features_dir = sys.argv[1]
    out_path = sys.argv[2]
    design = sys.argv[3] if len(sys.argv) > 3 else "unknown"
    platform = sys.argv[4] if len(sys.argv) > 4 else "unknown"
    spef_present = _parse_spef_arg(sys.argv[5]) if len(sys.argv) > 5 else None
    report = build_report(features_dir, out_path, design, platform, spef_present)
    ok = sum(1 for v in report["features"].values() if v["status"] == "ok")
    for fname, v in report["features"].items():
        if v["status"] == "invalid":
            sys.stderr.write(f"WARNING: feature set '{fname}' is INVALID: {v['reason']}\n")
    print(f"Wrote {out_path}: {ok}/{len(report['features'])} feature sets present")


if __name__ == "__main__":
    main()
