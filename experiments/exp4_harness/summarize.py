#!/usr/bin/env python3
"""Build the four-row Exp4 table only from complete, independently scored attempts.

Incomplete runs remain incomplete: absent scores are never fabricated as zeros.
Measured failures (including no converter) count once scored against every input.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics

ARMS = ("deepseek_native", "deepseek_r2g", "gpt_native", "gpt_r2g")
LABELS = {"deepseek_native": "DeepSeek × 原生", "deepseek_r2g": "DeepSeek × R2G",
          "gpt_native": "GPT × 原生", "gpt_r2g": "GPT × R2G"}


def read(path):
    if not Path(path).is_file():
        return None
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def safe_sum(values):
    return sum(values) if values and all(numeric(x) for x in values) else None


def describe(values):
    return {"mean": statistics.mean(values), "min": min(values), "max": max(values), "n": len(values)} \
        if values and all(numeric(x) for x in values) else None


def summarize_run(root, arm, repeat, expected, accuracy_key="label_accuracy"):
    out = root / "runs" / arm / f"s{repeat}"
    development = read(out / "result.json")
    rows, missing, invalid = [], [], []
    for design in expected:
        destination = out / "production" / design["design"]
        score, resource = read(destination / "score/score.json"), read(destination / "run.json")
        if score is None:
            missing.append(design["design"]); continue
        if (score.get("design") != design["design"] or score.get("platform") != design["platform"]
                or not isinstance(score.get("strict_full_pass"), bool)
                or not numeric(score.get(accuracy_key)) or not 0 <= score[accuracy_key] <= 1):
            invalid.append(design["design"]); continue
        rows.append({"design": design["design"], "platform": design["platform"],
                     "strict_full_pass": score["strict_full_pass"], accuracy_key: score[accuracy_key],
                     "labels": score.get("channels", score.get("labels", {})), "failures": score.get("failures", []),
                     "resource": resource, "score_path": str(destination / "score/score.json")})
    complete = (development is not None and not development.get("version_only", False)
                and not missing and not invalid and len(rows) == len(expected)
                and all(r["resource"] is not None for r in rows))
    dev = development or {}
    production = [r["resource"] or {} for r in rows]
    production_wall = safe_sum([r.get("wall_seconds") for r in production]) if complete else None
    total_wall = safe_sum([dev.get("wall_seconds"), production_wall]) if complete else None
    production_cpu = safe_sum([r.get("cpu_seconds") for r in production]) if complete else None
    total_cpu = safe_sum([dev.get("cpu_seconds"), production_cpu]) if complete else None
    rss = [dev.get("peak_process_tree_rss_bytes")] + [r.get("peak_process_tree_rss_bytes") for r in production]
    peak_rss = max(rss) if complete and all(numeric(x) for x in rss) else None
    tokens = (dev.get("provider_usage") or {}).get("total_tokens") if dev.get("usage_complete") else None
    if not numeric(tokens):
        tokens = None
    pdk = {}
    for platform in sorted({r["platform"] for r in expected}):
        eligible = [r for r in expected if r["platform"] == platform]
        scored = [r for r in rows if r["platform"] == platform]
        finished = len(scored) == len(eligible)
        pdk[platform] = {"expected": len(eligible), "scored": len(scored),
                         "strict_full_pass_count": sum(r["strict_full_pass"] for r in scored) if finished else None,
                         accuracy_key: statistics.mean(r[accuracy_key] for r in scored) if finished else None}
    return {"arm": arm, "repeat": repeat, "status": "complete" if complete else "incomplete",
            "expected_designs": len(expected), "scored_designs": len(rows), "missing_designs": missing,
            "invalid_designs": invalid, "strict_full_pass_count": sum(r["strict_full_pass"] for r in rows) if complete else None,
            accuracy_key: statistics.mean(r[accuracy_key] for r in rows) if complete else None,
            "total_tokens": tokens if complete else None, "usage_complete": bool(dev.get("usage_complete")),
            "provider_usage": dev.get("provider_usage"), "known_provider_usage": dev.get("known_provider_usage"),
            "model_calls": dev.get("model_calls"), "development_stop_reason": dev.get("stop_reason"),
            "development_exit_code": dev.get("exit_code"), "converter_present": dev.get("converter_present"),
            "development_wall_seconds": dev.get("wall_seconds"), "production_wall_seconds": production_wall,
            "total_wall_seconds": total_wall, "production_cpu_seconds": production_cpu,
            "total_cpu_seconds": total_cpu, "peak_process_tree_rss_bytes": peak_rss,
            "platforms": pdk, "designs": rows}


def summarize(root):
    freeze = read(root / "campaign_freeze.json") or {}
    accuracy_key = "raster_accuracy" if freeze.get("task_kind") == "image" else "label_accuracy"
    cohort = read(root / "cohort.json")
    if cohort is None:
        raise FileNotFoundError(root / "cohort.json")
    expected = cohort["splits"]["test"]
    if len(expected) != 60 or len({r["design"] for r in expected}) != 60:
        raise ValueError("Frozen main evaluation must contain 60 unique designs")
    arms = {}
    metrics = ("strict_full_pass_count", accuracy_key, "total_tokens", "total_wall_seconds",
               "production_cpu_seconds", "total_cpu_seconds", "peak_process_tree_rss_bytes")
    for arm in ARMS:
        runs = [summarize_run(root, arm, i, expected, accuracy_key) for i in (1, 2, 3)]
        complete = all(r["status"] == "complete" for r in runs)
        arms[arm] = {"status": "complete" if complete else "incomplete",
                     "complete_repeats": sum(r["status"] == "complete" for r in runs),
                     "metrics": {m: describe([r[m] for r in runs]) if complete else None for m in metrics},
                     "platforms": {}, "runs": runs}
        for p in sorted({r["platform"] for r in expected}):
            arms[arm]["platforms"][p] = {
                metric: describe([r["platforms"][p][metric] for r in runs]) if complete else None
                for metric in ("strict_full_pass_count", accuracy_key)}
    return {"schema": 1, "accuracy_key": accuracy_key, "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "complete" if all(a["status"] == "complete" for a in arms.values()) else "incomplete",
            "expected_designs_per_run": 60, "expected_repeats_per_arm": 3,
            "cost_scope": "development + production; evaluator and queue time excluded",
            "token_scope": "provider-reported total input (including cache) + output; unknown for incomplete usage",
            "arms": arms}


def value_text(stats, *, scale=1, decimals=1, suffix=""):
    if stats is None:
        return "未知"
    return (f"{stats['mean'] * scale:,.{decimals}f} "
            f"({stats['min'] * scale:,.{decimals}f}–{stats['max'] * scale:,.{decimals}f}){suffix}")


def table(summary):
    accuracy_key = summary.get("accuracy_key", "label_accuracy")
    accuracy_label = "像素准确率" if accuracy_key == "raster_accuracy" else "标签准确率"
    lines = [f"| 模型 × harness | 完全正确设计 / 60 ↑ | {accuracy_label} ↑ | 总 Token ↓ | 总耗时 ↓ |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for arm in ARMS:
        result = summary["arms"][arm]; metrics = result["metrics"]
        if result["status"] != "complete":
            cells = [f"未完成（{result['complete_repeats']}/3 次）"] * 4
        else:
            cells = [value_text(metrics["strict_full_pass_count"]),
                     value_text(metrics[accuracy_key], scale=100, suffix="%"),
                     value_text(metrics["total_tokens"], decimals=0),
                     value_text(metrics["total_wall_seconds"], scale=1/60, suffix=" min")]
        lines.append("| " + " | ".join([LABELS[arm], *cells]) + " |")
    return "\n".join(lines) + ("\n\n每格为 3 次独立开发的均值（最小值–最大值）。"
        "耗时含开发与 60 个设计的生产，排除独立评分和排队等待。"
        "缺失评分保持未完成；请求用量未完整返回时 Token 记为未知。\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, help="Defaults to ROOT/summary")
    a = ap.parse_args(); root = a.root.resolve(); out = a.out or root / "summary"
    result = summarize(root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    (out / "table.md").write_text(table(result))
    print(json.dumps({"status": result["status"], "table": str(out / "table.md"),
                      "complete_runs": sum(x["complete_repeats"] for x in result["arms"].values())}))


if __name__ == "__main__":
    main()
