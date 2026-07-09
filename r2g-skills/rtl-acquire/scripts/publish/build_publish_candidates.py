#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
import sys


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    out_root_path,
    skill_reference_path,
    workspace_path,
)

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = SCRIPT_DIR.parent
for path in (SCRIPTS_ROOT, SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from common.io_utils import load_json, load_rows, write_json


DEFAULT_EXTERNAL_INDEX = out_root_path("index.csv")
DEFAULT_DESIGN_SCORES = workspace_path("quality/design_quality_scores.csv")
DEFAULT_PUBLISH_POLICY = skill_reference_path("publish_policy.json")
DEFAULT_OUT_CSV = workspace_path("manifests/publish_eligible_designs.csv")
DEFAULT_OUT_JSON = workspace_path("manifests/publish_eligible_designs.json")
DEFAULT_OUT_MD = workspace_path("manifests/publish_eligible_designs.md")

def parse_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build publish-eligible design set from success index + design quality scores.")
    parser.add_argument("--external-index", type=Path, default=DEFAULT_EXTERNAL_INDEX)
    parser.add_argument("--design-scores", type=Path, default=DEFAULT_DESIGN_SCORES)
    parser.add_argument("--publish-policy-json", type=Path, default=DEFAULT_PUBLISH_POLICY)
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_OUT_CSV)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    publish_policy = load_json(args.publish_policy_json)
    allowed_actions = {
        str(action).strip().lower()
        for action in publish_policy.get("allowed_design_actions", ["keep", "conditional"])
    }
    exclude_low_fidelity = bool(publish_policy.get("exclude_low_fidelity_designs", True))
    max_dominant = float(publish_policy.get("max_dominant_gate_share", 0.98) or 0.98)
    min_complexity = float(publish_policy.get("min_nontrivial_complexity_score", 0.02) or 0.02)

    index_rows = load_rows(args.external_index)
    score_rows = load_rows(args.design_scores)
    scores_by_design = {row.get("design", ""): row for row in score_rows if row.get("design")}

    fieldnames = [
        "design",
        "status",
        "design_action",
        "publish_eligible",
        "publish_reasons",
        "design_quality_score",
        "graph_complexity_score",
        "dominant_cell_share",
        "low_fidelity",
    ]
    out_rows: list[dict[str, str]] = []
    eligible_count = 0
    reasons_counter = Counter()

    for row in index_rows:
        design = row.get("design", "")
        if not design:
            continue

        reasons: list[str] = []
        score = scores_by_design.get(design)
        if row.get("status") != "success":
            reasons.append("not_success")
        if not score:
            reasons.append("missing_design_score")
        else:
            action = (score.get("design_action") or "").strip().lower()
            if action not in allowed_actions:
                reasons.append(f"design_action={action or 'missing'}")
            low_fidelity = parse_bool(score.get("low_fidelity", "False"))
            if exclude_low_fidelity and low_fidelity:
                reasons.append("low_fidelity")
            try:
                complexity = float(score.get("graph_complexity_score", 0.0) or 0.0)
            except Exception:
                complexity = 0.0
            if action == "keep" and complexity < min_complexity:
                reasons.append(f"complexity<{min_complexity}")
            try:
                dominant = float(score.get("dominant_cell_share", 1.0) or 1.0)
            except Exception:
                dominant = 1.0
            if dominant > max_dominant:
                reasons.append(f"dominant_cell_share>{max_dominant}")

        eligible = not reasons
        if eligible:
            eligible_count += 1
        for reason in reasons:
            reasons_counter[reason] += 1

        out_rows.append(
            {
                "design": design,
                "status": row.get("status", ""),
                "design_action": score.get("design_action", "") if score else "",
                "publish_eligible": str(eligible),
                "publish_reasons": ";".join(reasons),
                "design_quality_score": score.get("design_quality_score", "") if score else "",
                "graph_complexity_score": score.get("graph_complexity_score", "") if score else "",
                "dominant_cell_share": score.get("dominant_cell_share", "") if score else "",
                "low_fidelity": score.get("low_fidelity", "") if score else "",
            }
        )

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    payload = {
        "external_design_rows": len(out_rows),
        "publish_eligible_count": eligible_count,
        "publish_blocked_count": len(out_rows) - eligible_count,
        "allowed_design_actions": sorted(allowed_actions),
        "exclude_low_fidelity_designs": exclude_low_fidelity,
        "reason_counts": dict(reasons_counter),
    }
    write_json(args.out_json, payload)

    with args.out_md.open("w", encoding="utf-8") as fh:
        fh.write("# Publish Eligible Designs\n\n")
        fh.write(f"- external_design_rows: {len(out_rows)}\n")
        fh.write(f"- publish_eligible_count: {eligible_count}\n")
        fh.write(f"- publish_blocked_count: {len(out_rows) - eligible_count}\n")
        fh.write(f"- allowed_design_actions: {sorted(allowed_actions)}\n")
        fh.write(f"- exclude_low_fidelity_designs: {exclude_low_fidelity}\n\n")
        fh.write("## Reason Counts\n")
        for key, value in sorted(reasons_counter.items()):
            fh.write(f"- {key}: {value}\n")

    print(args.out_csv)


if __name__ == "__main__":
    main()
