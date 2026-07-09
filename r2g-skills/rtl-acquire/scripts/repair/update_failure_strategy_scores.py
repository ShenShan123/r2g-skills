#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


import sys

_SKILL_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))
from skill_env import (
    default_out_root,
    out_root_path,
    skill_reference_path,
    workspace_path,
)

DEFAULT_INDEX = out_root_path("index.csv")
DEFAULT_REPAIR_LOG = workspace_path("failures/repair_action_log.json")
DEFAULT_STRATEGY = skill_reference_path("failure_strategy.json")
DEFAULT_DIAGNOSIS = workspace_path("failures/failure_diagnosis.json")
DEFAULT_DESIGN_SCORES = workspace_path("quality/design_quality_scores.csv")
DEFAULT_EXTERNAL_ROOT = default_out_root()
DEFAULT_PUBLISH_ELIGIBLE = workspace_path("manifests/publish_eligible_designs.csv")


def load_index(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {row["design"]: row for row in csv.DictReader(fh)}


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Update failure_strategy.json with success stats per action.")
    parser.add_argument("--index-csv", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--repair-log-json", type=Path, default=DEFAULT_REPAIR_LOG)
    parser.add_argument("--strategy-json", type=Path, default=DEFAULT_STRATEGY)
    parser.add_argument("--diagnosis-json", type=Path, default=DEFAULT_DIAGNOSIS)
    parser.add_argument("--design-scores-csv", type=Path, default=DEFAULT_DESIGN_SCORES)
    parser.add_argument("--external-root", type=Path, default=DEFAULT_EXTERNAL_ROOT)
    parser.add_argument("--publish-eligible-csv", type=Path, default=DEFAULT_PUBLISH_ELIGIBLE)
    args = parser.parse_args()

    index_rows = load_index(args.index_csv)
    repair_log = load_json(args.repair_log_json)
    strategy = load_json(args.strategy_json)
    diagnosis_payload = load_json(args.diagnosis_json)
    diagnoses = diagnosis_payload.get("diagnoses") or []

    action_stats = defaultdict(
        lambda: {
            "attempts": 0,
            "success": 0,
            "success_good": 0,
            "success_degraded": 0,
            "success_low_value": 0,
            "reward_sum": 0.0,
            "diagnosis_recommended": 0,
            "diagnosis_followed": 0,
            "diagnosis_success_after_recommendation": 0,
        }
    )
    design_scores = {}
    if args.design_scores_csv.exists():
        with args.design_scores_csv.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                design_scores[row.get("design", "")] = row
    publish_eligible = set()
    if args.publish_eligible_csv.exists():
        with args.publish_eligible_csv.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if str(row.get("publish_eligible", "")).strip().lower() == "true" and row.get("design"):
                    publish_eligible.add(row["design"])
    for design, entry in repair_log.items():
        actions = entry.get("last_actions") or []
        status = index_rows.get(design, {}).get("status")
        score = 0.0
        try:
            score = float(design_scores.get(design, {}).get("design_quality_score", 0.0) or 0.0)
        except Exception:
            score = 0.0
        degraded = False
        stats_path = args.external_root / design / "cell_stats.json"
        if stats_path.exists():
            try:
                stats = json.loads(stats_path.read_text(encoding="utf-8"))
                degraded = bool(stats.get("degraded_quality", False))
            except Exception:
                degraded = False
        low_value = score < 0.2
        publish_gain = 1.0 if design in publish_eligible else 0.0
        repair_cost = 0.1 * int(entry.get("attempts", 0) or 0)
        fidelity_penalty = 0.5 if degraded else 0.0
        low_value_penalty = 0.3 if low_value else 0.0
        reward = publish_gain + score - fidelity_penalty - low_value_penalty - repair_cost
        for action in actions:
            action_stats[action]["attempts"] += 1
            if status == "success":
                action_stats[action]["success"] += 1
                action_stats[action]["reward_sum"] += reward
                if low_value:
                    action_stats[action]["success_low_value"] += 1
                elif degraded:
                    action_stats[action]["success_degraded"] += 1
                else:
                    action_stats[action]["success_good"] += 1

    for diagnosis in diagnoses:
        design = str(diagnosis.get("design") or "")
        action = str(diagnosis.get("next_best_action") or "")
        if not action or action in {"retry_next_best_action", "manual_or_exclude"}:
            continue
        action_stats[action]["diagnosis_recommended"] += 1
        attempted = set((repair_log.get(design, {}) or {}).get("last_actions") or [])
        if action in attempted:
            action_stats[action]["diagnosis_followed"] += 1
        if index_rows.get(design, {}).get("status") == "success":
            action_stats[action]["diagnosis_success_after_recommendation"] += 1

    for key, item in strategy.items():
        actions = item.get("actions") or []
        stats = []
        for action in actions:
            s = action_stats.get(action)
            if not s:
                continue
            attempts = int(s["attempts"])
            success = int(s["success"])
            rate = round(success / attempts, 4) if attempts else 0.0
            avg_reward = round((s["reward_sum"] / success), 4) if success else 0.0
            stats.append(
                {
                    "action": action,
                    "attempts": attempts,
                    "success": success,
                    "success_rate": rate,
                    "avg_reward": avg_reward,
                    "success_good": int(s["success_good"]),
                    "success_degraded": int(s["success_degraded"]),
                    "success_low_value": int(s["success_low_value"]),
                    "diagnosis_recommended": int(s["diagnosis_recommended"]),
                    "diagnosis_followed": int(s["diagnosis_followed"]),
                    "diagnosis_success_after_recommendation": int(s["diagnosis_success_after_recommendation"]),
                    "reward_formula": "publish_gain + design_quality_score - fidelity_penalty - low_value_penalty - repair_cost",
                }
            )
        if stats:
            item["action_stats"] = stats
        strategy[key] = item

    args.strategy_json.write_text(json.dumps(strategy, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.strategy_json}")


if __name__ == "__main__":
    main()
