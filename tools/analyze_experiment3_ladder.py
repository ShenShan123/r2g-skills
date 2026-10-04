#!/usr/bin/env python3
"""Score the Experiment 3 ablation ladder across campaigns.

The ladder holds the LLM constant and varies only what R2G supplies around it:

  L0  no LLM, no memory                       state/b_heldout/m0.json
  L1  native LLM, no R2G scaffolding          state/raw_llm/<model>/
  L2  LLM + R2G scaffolding, no memory        state/pure_llm/<model>/
  L3  frozen A-phase memory, no LLM           state/b_heldout/m1,m2,m3.json
  L4  LLM + R2G scaffolding + memory          state/r2g_memory/<model>/

Success rate saturates on a small held-out set with a concentrated recipe
distribution, so attempts-to-clean is reported beside it: the mechanism separating
L4 from L2 is reaching the right action sooner, not reaching one that L2 cannot.

Two honesty obligations are reported rather than left implicit. Per-arm timeout
counts, because arms ran at different concurrency and a trial that exceeds the
budget is scored a loss. And the LVS criterion in both readings, because R2G's own
knowledge layer treats a symmetric_matcher mismatch with no net or device mismatch
as a clean layout while signoff_gate does not.

Usage:
  analyze_experiment3_ladder.py collect-lvs --campaign ROOT [--campaign ROOT] \
      --out lvs_index.json                       # run where the trials live
  analyze_experiment3_ladder.py report --campaign ROOT [--campaign ROOT] \
      [--lvs-index lvs_index.json] [--out DIR]
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import statistics
from pathlib import Path
from typing import Any

LEVELS = ("L0", "L1", "L2", "L3", "L4")
LEVEL_LABEL = {
    "L0": "no LLM, no memory",
    "L1": "native LLM",
    "L2": "R2G scaffolding, no memory",
    "L3": "frozen memory, no LLM",
    "L4": "R2G full (scaffolding + memory)",
}
LLM_DIRS = {"L1": "raw_llm", "L2": "pure_llm", "L4": "r2g_memory"}

# Knob-name groups for the native arm. The point is not taxonomy for its own sake:
# a proposal that names the right mechanism with the wrong identifier is a different
# failure from one that targets the wrong mechanism, and only the first says the
# model knew what to do.
INTENT_GROUPS = (
    ("antenna_repair", ("ANTENNA_REPAIR", "REPAIR_ANTENNAS", "ANTENNA_ITER", "ANT_ITER")),
    ("antenna_diode", ("DIODE", "ANTENNA_CELL")),
    ("antenna_margin", ("ANTENNA_MARGIN", "ANTENNA_RATIO")),
    ("placement_density", ("PLACE_DENSITY", "CELL_PAD", "CORE_UTIL")),
    ("routing", ("ROUTE", "GRT_", "DRT_", "LAYER_ADJUST", "CONGESTION")),
    ("timing", ("ABC_", "SETUP_", "TNS_", "HOLD_", "REPAIR_TIMING", "SYNTH_")),
    ("pin_placement", ("PLACE_PINS", "PIN_")),
)


def read_json(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def intent_group(knob: str) -> str:
    upper = knob.upper()
    for name, needles in INTENT_GROUPS:
        if any(needle in upper for needle in needles):
            return name
    return "other"


def heldout_tasks(root: Path) -> list[str]:
    manifest = read_json(root / "data/split_manifest.json")
    return sorted(
        row["task_id"] for row in manifest["assignments"] if row["split"] == "b_heldout"
    )


def platform_of(root: Path) -> str:
    manifest = read_json(root / "data/split_manifest.json")
    if manifest.get("platform"):
        return manifest["platform"]
    return "sky130hd" if "20260906" in root.name else "nangate45"


def action_policy(root: Path) -> dict[str, Any]:
    """Resolve the frozen action policy for a campaign."""
    override = os.environ.get("R2G_EXP3_ACTION_POLICY")
    if override and Path(override).is_file():
        return read_json(override)
    for candidate in (
        root / "protocol/action_policy.json",
        root / "reports/reproducibility_snapshot/protocol"
        / "sky130hd_100mhz_fixed_task_repair_action_policy.json",
    ):
        if candidate.is_file():
            return read_json(candidate)
    return {}


def legal_knobs(policy: dict[str, Any]) -> set[str]:
    return set(policy.get("allowed_string_knobs") or {}) | set(
        policy.get("allowed_numeric_knobs") or {}
    )


# --------------------------------------------------------------------------- LVS


def collect_lvs(roots: list[Path]) -> dict[str, Any]:
    """Index every trial's lvs.json by (arm, task, candidate directory)."""
    index: dict[str, Any] = {}
    for root in roots:
        for path in glob.glob(str(root / "trials/*/*/*/reports/lvs.json")):
            arm, task, cand = path.split("/trials/")[1].split("/")[:3]
            try:
                payload = read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            index[f"{root.name}|{arm}|{task}|{cand}"] = {
                "status": payload.get("status"),
                "mismatch_count": payload.get("mismatch_count"),
                "mismatch_class": payload.get("mismatch_class"),
                "net_mismatches": payload.get("net_mismatches"),
                "device_mismatches": payload.get("device_mismatches"),
                "reason": payload.get("reason"),
            }
    return {"schema_version": "experiment3-lvs-index-1.0", "entries": index}


def lvs_verdict(record: dict[str, Any] | None) -> tuple[bool | None, str]:
    """Apply R2G's own LVS rule: symmetric_matcher with no real mismatch is clean."""
    if record is None:
        return None, "no_report"
    if record["status"] == "clean":
        return True, "clean"
    if record["status"] == "crash":
        return None, record.get("reason") or "crash"
    if (
        record.get("mismatch_class") == "symmetric_matcher"
        and record.get("net_mismatches") == 0
        and record.get("device_mismatches") == 0
    ):
        return True, "symmetric_matcher_false_positive"
    return False, record.get("mismatch_class") or "fail"


def find_lvs(index: dict[str, Any], campaign: str, arm: str, task: str,
             candidate_id: str | None, candidate_hash: str | None) -> dict[str, Any] | None:
    prefix = f"{campaign}|{arm}|{task}|"
    best = None
    for key, value in index.items():
        if not key.startswith(prefix):
            continue
        cand = key[len(prefix):]
        if candidate_id and cand.startswith(candidate_id):
            if not candidate_hash or candidate_hash[:12] in cand:
                return value
            best = best or value
    return best



# ------------------------------------------------------------------------- cost

# Providers report usage under different names. Normalising to four classes keeps
# the cost model honest: input, output, cache write and cache read are priced
# differently, so a single "total tokens" number cannot be turned into money.
def normalize_usage(usage: dict[str, Any]) -> dict[str, int]:
    usage = usage or {}
    prompt = usage.get("prompt_tokens")
    if prompt is None:
        prompt = usage.get("input_tokens") or 0
    output = usage.get("completion_tokens")
    if output is None:
        output = usage.get("output_tokens") or 0
    billing = ((usage.get("billing_usage") or {}).get("claude_usage")) or {}
    cache_write = int(billing.get("cache_creation_input_tokens") or 0)
    # Cache writes are priced by TTL: 1.25x base input for the 5-minute entry,
    # 2x for the 1-hour entry, so the two cannot share one rate.
    write_1h = int(usage.get("claude_cache_creation_1_h_tokens") or 0)
    write_5m = int(usage.get("claude_cache_creation_5_m_tokens") or 0)
    if write_1h + write_5m == 0:
        write_5m = cache_write
    cache_read = int(billing.get("cache_read_input_tokens") or 0)
    if not cache_read:
        details = usage.get("prompt_tokens_details") or {}
        cache_read = int(details.get("cached_tokens") or 0)
    # A relay reports cache tokens beside the prompt count, so uncached input is
    # what is left after removing the part served from cache.
    uncached = max(int(prompt) - cache_read, 0)
    return {
        "input_uncached": uncached,
        "input_cache_read": cache_read,
        "input_cache_write": cache_write,
        "input_cache_write_1h": write_1h,
        "input_cache_write_5m": write_5m,
        "output": int(output),
        "billed_total": uncached + cache_read + cache_write + int(output),
    }


def token_spend(root: Path) -> dict[str, dict[str, Any]]:
    """Per arm: API calls and tokens by price class, from the per-task ledgers."""
    out: dict[str, dict[str, Any]] = {}
    for level, directory in LLM_DIRS.items():
        for model_dir in sorted(glob.glob(str(root / f"state/{directory}/*"))):
            model = Path(model_dir).name
            totals = collections.Counter()
            calls = 0
            estimated = 0
            ledger_total = 0
            for path in glob.glob(f"{model_dir}/*/token_ledger.json"):
                try:
                    ledger = read_json(path)
                except (OSError, json.JSONDecodeError):
                    continue
                ledger_total += int(ledger.get("consumed_tokens") or 0)
                for call in (ledger.get("calls") or {}).values():
                    if call.get("status") != "completed":
                        continue
                    calls += 1
                    if call.get("accounting") != "provider_usage":
                        # No provider usage came back, so the conservative reservation
                        # was charged. It overstates spend and is flagged, not hidden.
                        estimated += 1
                        totals["reserved_estimate"] += int(call.get("charged_tokens") or 0)
                        continue
                    for key, value in normalize_usage(call.get("usage")).items():
                        totals[key] += value
            if not calls:
                continue
            out[f"{level}:{model}"] = {
                "level": level,
                "model": model,
                "api_calls": calls,
                "calls_estimated_not_metered": estimated,
                "ledger_consumed_tokens": ledger_total,
                **{k: int(v) for k, v in totals.items()},
            }
    return out


def eda_spend(rows: list[dict[str, Any]], cores: int = 4) -> dict[str, Any]:
    seconds = sum(float(row.get("elapsed_seconds") or 0) for row in rows)
    return {
        "trials": len(rows),
        "wall_seconds": round(seconds),
        "core_hours": round(seconds * cores / 3600, 2),
    }


def price_tokens_paper(spend: dict[str, Any], prices: dict[str, Any]) -> float | None:
    """Cost under the paper's stated convention (Appendix: API Cost Accounting).

        C = (T_in * p_in + T_out * p_out) / 1e6

    It is an uncached, base-context reference price: cache reads and writes are
    billed as ordinary input, and no discount or premium is applied. Mixing this
    with a cache-aware figure inside one table would compare two conventions, so
    the analyzer keeps them as separate columns.
    """
    rate = prices.get(spend["model"])
    if not rate:
        return None
    t_in = (spend.get("input_uncached", 0) + spend.get("input_cache_read", 0)
            + spend.get("input_cache_write", 0) + spend.get("reserved_estimate", 0))
    t_out = spend.get("output", 0)
    return round((t_in * float(rate["input"]) + t_out * float(rate["output"])) / 1_000_000, 4)


def price_tokens(spend: dict[str, Any], prices: dict[str, Any]) -> float | None:
    """Money for one arm, given per-million-token prices for its model."""
    rate = prices.get(spend["model"])
    if not rate:
        return None
    total = 0.0
    for key, per_million in (
        ("input_uncached", "input"),
        ("input_cache_read", "cache_read"),
        ("input_cache_write_1h", "cache_write_1h"),
        ("input_cache_write_5m", "cache_write_5m"),
        ("output", "output"),
        ("reserved_estimate", "input"),
    ):
        total += spend.get(key, 0) / 1_000_000 * float(rate.get(per_million) or 0)
    return round(total, 4)


# ----------------------------------------------------------------------- scoring


def attempt_rows(state_glob: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(glob.glob(state_glob)):
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        for result in payload.get("results") or []:
            for order, attempt in enumerate(result.get("attempts") or []):
                rows.append({"task_id": result.get("task_id"), "order": order, **attempt})
    return rows


def score_arm(rows: list[dict[str, Any]], tasks: list[str], campaign: str, arm: str,
              index: dict[str, Any] | None, attempted: int | None = None) -> dict[str, Any]:
    """Score one arm under both LVS readings, plus effort and timeouts."""
    strict: set[str] = set()
    corrected: set[str] = set()
    inconclusive: set[str] = set()
    attempts_to_clean: dict[str, int] = {}
    effort: dict[str, int] = collections.Counter()
    timeouts = 0
    elapsed: list[float] = []

    for row in rows:
        task = row.get("task_id")
        if task not in tasks:
            continue
        effort[task] += 1
        seconds = row.get("elapsed_seconds")
        if seconds:
            elapsed.append(float(seconds))
            if float(seconds) > 7200:
                timeouts += 1
        if row.get("strict_clean_after_repair") is True:
            strict.add(task)
            corrected.add(task)
            attempts_to_clean.setdefault(task, row["order"] + 1)
            continue
        if index is None:
            continue
        metrics = row.get("action_metrics") or {}
        if metrics.get("drc_violations") or metrics.get("route_violations"):
            continue
        if (metrics.get("setup_wns_ns") or 0) < 0 or (metrics.get("hold_wns_ns") or 0) < 0:
            continue
        verdict, why = lvs_verdict(
            find_lvs(index, campaign, arm, task, row.get("candidate_id"), row.get("candidate_hash"))
        )
        if verdict is True:
            corrected.add(task)
            attempts_to_clean.setdefault(task, row["order"] + 1)
        elif verdict is None and why != "no_report":
            inconclusive.add(task)

    reached = len({row["task_id"] for row in rows if row.get("task_id") in tasks})
    return {
        # An arm can attempt a task without any trial running: the native arm's
        # out-of-policy proposals are scored without reaching the EDA flow, so
        # "attempted" and "reached EDA" are different denominators.
        "tasks_attempted": attempted if attempted is not None else reached,
        "tasks_reached_eda": reached,
        "tasks_touched": reached,
        "clean_strict": len(strict),
        "clean_corrected_lvs": len(corrected),
        "clean_task_ids": sorted(corrected),
        "lvs_inconclusive_tasks": sorted(inconclusive - corrected),
        "mean_attempts_to_clean": (
            round(statistics.mean(attempts_to_clean.values()), 2) if attempts_to_clean else None
        ),
        "mean_attempts_per_task": round(statistics.mean(effort.values()), 2) if effort else None,
        "eda_trials": sum(effort.values()),
        "timeouts_over_budget": timeouts,
        "median_trial_seconds": round(statistics.median(elapsed)) if elapsed else None,
        "max_trial_seconds": round(max(elapsed)) if elapsed else None,
    }


def gate_analysis(root: Path, model: str, policy: dict[str, Any]) -> dict[str, Any]:
    """Native-arm policy gate: how often, and how, the proposal left the action space."""
    legal = legal_knobs(policy)
    gates = sorted(glob.glob(str(root / f"state/raw_llm/{model}/*/policy_gate-*.json")))
    if not gates:
        return {}
    by_attempt: dict[int, list[int]] = collections.defaultdict(lambda: [0, 0])
    proposed: collections.Counter = collections.Counter()
    illegal: collections.Counter = collections.Counter()
    groups_illegal: collections.Counter = collections.Counter()
    executed_edits: collections.Counter = collections.Counter()
    blocked = 0
    for path in gates:
        payload = read_json(path)
        order = int(path.rsplit("-", 1)[1].split(".")[0])
        by_attempt[order][1] += 1
        if not payload["executed"]:
            blocked += 1
            by_attempt[order][0] += 1
        for knob in payload.get("config_edits") or {}:
            proposed[knob] += 1
            if knob not in legal:
                illegal[knob] += 1
                groups_illegal[intent_group(knob)] += 1
            if payload["executed"]:
                executed_edits[knob] += 1
    return {
        "gate_records": len(gates),
        "blocked": blocked,
        "executed": len(gates) - blocked,
        "out_of_policy_rate": round(blocked / len(gates), 3),
        "distinct_knobs_proposed": len(proposed),
        "distinct_knobs_out_of_policy": len(illegal),
        "legal_knob_count": len(legal),
        "out_of_policy_rate_by_attempt": {
            str(order + 1): round(bad / total, 3)
            for order, (bad, total) in sorted(by_attempt.items())
        },
        "out_of_policy_by_intent": dict(groups_illegal.most_common()),
        "most_proposed_out_of_policy": dict(illegal.most_common(12)),
        "legal_edits_ever_executed": dict(executed_edits.most_common()),
    }


def learning_curve(root: Path, level: str, model: str, tasks: list[str]) -> list[int]:
    """Clean (1) or not (0) in the arm's own task order, to expose accumulation."""
    directory = LLM_DIRS.get(level)
    if not directory:
        return []
    curve = []
    for task in tasks:
        paths = glob.glob(str(root / f"state/{directory}/{model}/{task}/execution-*.json"))
        clean = any(
            any(r.get("strict_clean") is True for r in read_json(p).get("results") or [])
            for p in paths
        )
        curve.append(1 if clean else 0)
    return curve


def campaign_report(root: Path, index: dict[str, Any] | None) -> dict[str, Any]:
    tasks = heldout_tasks(root)
    policy = action_policy(root)
    report: dict[str, Any] = {
        "campaign_root": str(root),
        "platform": platform_of(root),
        "heldout_tasks": len(tasks),
        "legal_knobs": sorted(legal_knobs(policy)),
        "levels": {},
        "token_spend": token_spend(root),
    }

    for level, arms in (("L0", ["m0"]), ("L3", ["m1", "m2", "m3"])):
        for arm in arms:
            path = root / f"state/b_heldout/{arm}.json"
            if not path.is_file():
                continue
            rows = attempt_rows(str(path))
            scored = score_arm(rows, tasks, root.name, arm, index)
            scored["eda_spend"] = eda_spend(rows)
            scored["token_spend"] = None  # no model is called at these levels
            report["levels"].setdefault(level, {})[arm] = scored

    for level, directory in LLM_DIRS.items():
        for model_dir in sorted(glob.glob(str(root / f"state/{directory}/*"))):
            model = Path(model_dir).name
            rows = attempt_rows(f"{model_dir}/*/execution-*.json")
            attempted = len([d for d in glob.glob(f"{model_dir}/*") if Path(d).is_dir()])
            if not rows and not attempted:
                continue
            arm = f"{directory}_{model}" if level != "L2" else f"pure_llm_{model}"
            entry = score_arm(rows, tasks, root.name, arm, index, attempted)
            entry["learning_curve"] = learning_curve(root, level, model, tasks)
            entry["eda_spend"] = eda_spend(rows)
            if level == "L1":
                entry["policy_gate"] = gate_analysis(root, model, policy)
            report["levels"].setdefault(level, {})[model] = entry
    return report


def markdown(reports: list[dict[str, Any]], prices: dict[str, Any] | None = None) -> str:
    lines: list[str] = ["# Experiment 3 ablation ladder", ""]
    for report in reports:
        lines += [
            f"## {report['platform']} ({report['heldout_tasks']} held-out tasks)",
            "",
            "| Level | What it has | Arm | Attempted | Reached EDA | Clean (strict) "
            "| Clean (R2G LVS rule) | Mean attempts to clean | Trials | Timeouts |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for level in LEVELS:
            for arm, entry in sorted((report["levels"].get(level) or {}).items()):
                lines.append(
                    f"| {level} | {LEVEL_LABEL[level]} | {arm} | {entry['tasks_attempted']} "
                    f"| {entry['tasks_reached_eda']} "
                    f"| {entry['clean_strict']} | {entry['clean_corrected_lvs']} "
                    f"| {entry['mean_attempts_to_clean'] or '-'} | {entry['eda_trials']} "
                    f"| {entry['timeouts_over_budget']} |"
                )
        lines.append("")
        for arm, entry in sorted((report["levels"].get("L1") or {}).items()):
            gate = entry.get("policy_gate") or {}
            if not gate:
                continue
            lines += [
                f"### Native-arm action space, model `{arm}`",
                "",
                f"- legal knobs in the frozen policy: **{gate['legal_knob_count']}**",
                f"- distinct knobs proposed: **{gate['distinct_knobs_proposed']}**, "
                f"of which **{gate['distinct_knobs_out_of_policy']}** are outside it",
                f"- out-of-policy rate: **{gate['out_of_policy_rate']:.0%}** "
                f"({gate['blocked']}/{gate['gate_records']} attempts)",
                f"- by attempt index: {gate['out_of_policy_rate_by_attempt']}",
                f"- out-of-policy proposals by intended mechanism: {gate['out_of_policy_by_intent']}",
                f"- legal edits it ever reached: {gate['legal_edits_ever_executed'] or 'none'}",
                "",
            ]
        money = (" | Token cost (paper convention) | Token cost (cache-aware) "
                 "| Cost per repair (paper) |") if prices else " |"
        rule = " | ---: | ---: | ---: |" if prices else " |"
        lines += ["### Cost inputs", "",
                  "| Arm | API calls | Input (uncached) | Cache read | Cache write | Output "
                  "| Unmetered calls | EDA core-hours" + money,
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---:" + rule]
        for key, spend in sorted(report.get("token_spend", {}).items()):
            level, model = spend["level"], spend["model"]
            hours = ((report["levels"].get(level) or {}).get(model) or {}).get("eda_spend") or {}
            tail = " |"
            if prices:
                paper = price_tokens_paper(spend, prices)
                cached = price_tokens(spend, prices)
                scored = ((report["levels"].get(level) or {}).get(model) or {})
                clean = scored.get("clean_corrected_lvs") or 0
                per = f"{paper/clean:.4f}" if (paper is not None and clean) else "-"
                tail = (f" | {paper if paper is not None else '-'}"
                        f" | {cached if cached is not None else '-'} | {per} |")
            lines.append(
                f"| {level} {model} | {spend['api_calls']} | {spend.get('input_uncached',0):,} "
                f"| {spend.get('input_cache_read',0):,} | {spend.get('input_cache_write',0):,} "
                f"| {spend.get('output',0):,} | {spend['calls_estimated_not_metered']} "
                f"| {hours.get('core_hours','-')}" + tail
            )
        for level in ("L0", "L3"):
            for arm, entry in sorted((report["levels"].get(level) or {}).items()):
                hours = (entry.get("eda_spend") or {}).get("core_hours", "-")
                tail = " | 0 | 0 | 0 |" if prices else " |"
                lines.append(f"| {level} {arm} | 0 | 0 | 0 | 0 | 0 | 0 | {hours}" + tail)
        lines += ["", "No model is called at L0 or L3, so their token cost is zero by "
                  "construction and only EDA compute is spent.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect-lvs")
    collect.add_argument("--campaign", type=Path, action="append", required=True)
    collect.add_argument("--out", type=Path, required=True)

    report = sub.add_parser("report")
    report.add_argument("--campaign", type=Path, action="append", required=True)
    report.add_argument("--lvs-index", type=Path)
    report.add_argument(
        "--prices",
        type=Path,
        help='per-model USD per million tokens, e.g. '
             '{"claude": {"input": 5, "output": 25, "cache_write": 6.25, "cache_read": 0.5}}',
    )
    report.add_argument("--out", type=Path)

    args = parser.parse_args()

    if args.command == "collect-lvs":
        payload = collect_lvs([root.resolve() for root in args.campaign])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"indexed {len(payload['entries'])} trial LVS reports -> {args.out}")
        return 0

    index = None
    if args.lvs_index and args.lvs_index.is_file():
        index = read_json(args.lvs_index)["entries"]
    prices = read_json(args.prices) if args.prices and args.prices.is_file() else None
    reports = [campaign_report(root.resolve(), index) for root in args.campaign]
    text = markdown(reports, prices)
    print(text)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "ladder.json").write_text(
            json.dumps(reports, indent=2, sort_keys=True) + "\n"
        )
        (args.out / "ladder.md").write_text(text + "\n")
        print(f"\nwritten to {args.out}/ladder.json and {args.out}/ladder.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
