"""Crystallizability preflight (design doc 6.3, 26 Phase 4).

Answers the question: *does the captured episode corpus contain repeatable
structure worth anti-unifying, or is it instance-dominated?*

Metrics (design doc 6.3, 29.3):
    singleton_rate  = #{g : |g| = 1} / #groups
    cc_raw          = sum_{|g| >= min_group_size} |g| / N
    cc_lineage      = #episodes in a group with >= 2 distinct lineages / N
    key_precision   = group outcome homogeneity (design doc 6.3 "key precision")
    key_recall      = cc_raw

Outputs (design doc Phase 4 acceptance):
    groups.json              full group support profile
    group_report.md          metrics + verdict + which groups to crystallize
    group_size.csv           per-group size / lineage / family table
    lineage_support.csv      per-lineage crystallizability contribution
    manual_audit_sample.json largest non-trivial groups for human audit

The verdict is advisory: it says whether to proceed to joint anti-unification,
never fabricates groups (a corpus of singletons honestly reports instance-dominated).
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from tehm import db as tehm_db
from tehm.crystallization.effects import effect_key_from_transition_dict

DEFAULT_MIN_GROUP_SIZE = 2


@dataclass
class PreflightReport:
    total_transitions: int
    num_groups: int
    singleton_rate: float
    cc_raw: float
    cc_lineage: float
    key_precision: float
    key_recall: float
    groups: dict = field(default_factory=dict)   # effect_key -> group info
    verdict: str = "empty"
    detail: str = ""
    min_group_size: int = DEFAULT_MIN_GROUP_SIZE

    def to_dict(self) -> dict:
        return {
            "total_transitions": self.total_transitions,
            "num_groups": self.num_groups,
            "singleton_rate": self.singleton_rate,
            "cc_raw": self.cc_raw,
            "cc_lineage": self.cc_lineage,
            "key_precision": self.key_precision,
            "key_recall": self.key_recall,
            "verdict": self.verdict,
            "detail": self.detail,
            "min_group_size": self.min_group_size,
            "groups": self.groups,
        }


def run_preflight(conn, *, out_dir: Path | str | None = None,
                  min_group_size: int = DEFAULT_MIN_GROUP_SIZE,
                  top_groups: int = 10, campaign_id: str = "live") -> PreflightReport:
    """Group all captured transitions by primary effect key and score the corpus.

    ``out_dir`` (optional): write the five preflight outputs there.
    """
    if not campaign_id:
        raise ValueError("campaign_id is required for crystallization preflight")
    transitions, lineage_of, design_of = _load_transitions(
        conn, campaign_id=campaign_id)
    total = len(transitions)
    report = PreflightReport(
        total_transitions=total, num_groups=0, singleton_rate=0.0,
        cc_raw=0.0, cc_lineage=0.0, key_precision=0.0, key_recall=0.0,
        min_group_size=min_group_size)

    if total == 0:
        report.verdict = "empty"
        report.detail = "no transitions captured; nothing to preflight"
        if out_dir:
            _write_outputs(report, Path(out_dir), top_groups)
        return report

    # group by primary effect key (recomputed from the row — one canon).
    groups: dict[str, list[dict]] = {}
    for t in transitions:
        key = effect_key_from_transition_dict(t)
        groups.setdefault(key, []).append(t)

    group_infos: dict[str, dict] = {}
    for key, members in groups.items():
        outcomes = Counter(m["outcome"] for m in members)
        lineages = sorted({lineage_of.get(m["source_state_id"]) or "?"
                           for m in members})
        designs = sorted({design_of.get(m["source_state_id"]) or "?"
                          for m in members})
        families = sorted({(m["action"] or {}).get("transformation_family") or "?"
                           for m in members})
        group_infos[key] = {
            "effect_key": key,
            "size": len(members),
            "outcomes": dict(outcomes),
            "dominant_outcome": outcomes.most_common(1)[0][0] if outcomes else "?",
            "unique_lineages": len(lineages),
            "lineages": lineages,
            "designs": designs,
            "families": families,
            "transition_ids": sorted(m["transition_id"] for m in members),
        }
    report.groups = group_infos
    report.num_groups = len(groups)

    n_singletons = sum(1 for g in group_infos.values() if g["size"] == 1)
    report.singleton_rate = n_singletons / report.num_groups if report.num_groups else 0.0

    non_trivial = [g for g in group_infos.values() if g["size"] >= min_group_size]
    report.cc_raw = sum(g["size"] for g in non_trivial) / total
    report.cc_lineage = sum(
        g["size"] for g in non_trivial if g["unique_lineages"] >= 2) / total

    # key_precision: outcome homogeneity (within-group majority share).
    report.key_precision = sum(
        g["outcomes"].get(g["dominant_outcome"], 0) for g in group_infos.values()) / total
    report.key_recall = report.cc_raw

    report.verdict, report.detail = _verdict(report)

    if out_dir:
        _write_outputs(report, Path(out_dir), top_groups)
    return report


def _verdict(report: PreflightReport) -> tuple[str, str]:
    n = report.total_transitions
    if n == 0:
        return "empty", "no transitions"
    multi_lineage_groups = [g for g in report.groups.values()
                            if g["size"] >= report.min_group_size
                            and g["unique_lineages"] >= 2]
    any_repeat = any(g["size"] >= report.min_group_size
                     for g in report.groups.values())

    if report.cc_lineage >= 0.3:
        detail = (f"cc_lineage={report.cc_lineage:.2f}: {len(multi_lineage_groups)} "
                  f"lineage-diverse repeat group(s); proceed to joint anti-unification")
        return "crystallizable", detail
    if report.cc_raw >= 0.4:
        detail = (f"cc_raw={report.cc_raw:.2f}: raw repeat structure present but "
                  f"lineage diversity weak (cc_lineage={report.cc_lineage:.2f}); "
                  f"crystallize cautiously or gather cross-lineage episodes")
        return "crystallizable_raw_only", detail
    if any_repeat:
        detail = (f"singleton_rate={report.singleton_rate:.2f}: repeats exist but are "
                  f"too sparse/lineage-poor; gather more episodes before anti-unification")
        return "marginal", detail
    detail = (f"singleton_rate={report.singleton_rate:.2f}, cc_raw={report.cc_raw:.2f}: "
              f"instance-dominated corpus; refine the effect canon or gather more episodes")
    return "instance_dominated", detail


def _load_transitions(conn, *, campaign_id: str = "live") -> tuple[list[dict], dict, dict]:
    rows = conn.execute(
        "SELECT transition_id, source_state_id, target_state_id, action_json, "
        "observation_delta_json, verifier_json, outcome "
        "FROM tehm_transitions t "
        "WHERE EXISTS (SELECT 1 FROM tehm_dataset_membership dm "
        "WHERE dm.transition_id=t.transition_id AND dm.campaign_id=? "
        "AND dm.split='training' AND dm.learner_eligible=1)", (campaign_id,)).fetchall()
    transitions = [
        {
            "transition_id": r["transition_id"],
            "source_state_id": r["source_state_id"],
            "target_state_id": r["target_state_id"],
            "action": tehm_db.read_json(r["action_json"]),
            "observation_delta": tehm_db.read_json(r["observation_delta_json"]),
            "verifier": tehm_db.read_json(r["verifier_json"]),
            "outcome": r["outcome"],
        }
        for r in rows
    ]
    lineage_of: dict[str, str] = {}
    design_of: dict[str, str] = {}
    for r in conn.execute("SELECT state_id, lineage_id, design_id FROM tehm_states"):
        lineage_of[r["state_id"]] = r["lineage_id"] or "?"
        design_of[r["state_id"]] = r["design_id"] or "?"
    return transitions, lineage_of, design_of


# ---------------------------------------------------------------------------
# Outputs (design doc Phase 4 acceptance files)
# ---------------------------------------------------------------------------

def _write_outputs(report: PreflightReport, out_dir: Path, top_groups: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    groups = sorted(report.groups.values(), key=lambda g: g["size"], reverse=True)

    (out_dir / "groups.json").write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")

    # group_size.csv
    with open(out_dir / "group_size.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["effect_key", "size", "unique_lineages",
                         "families", "dominant_outcome"])
        for g in groups:
            writer.writerow([g["effect_key"], g["size"], g["unique_lineages"],
                             ";".join(g["families"]), g["dominant_outcome"]])

    # lineage_support.csv
    lineage_stats: dict[str, Counter] = {}
    for g in groups:
        for lineage in g["lineages"]:
            stats = lineage_stats.setdefault(lineage, Counter())
            stats["episodes"] += g["size"]
            stats["groups"] += 1
            if g["size"] >= report.min_group_size:
                stats["repeat_groups"] += 1
    with open(out_dir / "lineage_support.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["lineage_id", "episodes", "groups", "repeat_groups",
                         "crystallizable_contribution"])
        for lineage, stats in sorted(lineage_stats.items()):
            writer.writerow([lineage, stats["episodes"], stats["groups"],
                             stats["repeat_groups"],
                             stats["repeat_groups"] > 0])

    # manual_audit_sample.json — largest non-trivial groups for human audit.
    sample_groups = [g for g in groups if g["size"] >= report.min_group_size][:top_groups]
    audit = {
        "metrics": {k: getattr(report, k) for k in
                    ("total_transitions", "num_groups", "singleton_rate",
                     "cc_raw", "cc_lineage", "key_precision", "key_recall")},
        "verdict": report.verdict,
        "audited_groups": [
            {
                "effect_key": g["effect_key"],
                "size": g["size"],
                "lineages": g["lineages"],
                "families": g["families"],
                "transition_ids": g["transition_ids"],
            }
            for g in sample_groups
        ],
    }
    (out_dir / "manual_audit_sample.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n")

    # group_report.md — human-readable verdict.
    lines = [
        "# TEHM Crystallizability Preflight",
        "",
        f"- total transitions : {report.total_transitions}",
        f"- effect groups     : {report.num_groups}",
        f"- singleton rate    : {report.singleton_rate:.3f}",
        f"- CC_raw            : {report.cc_raw:.3f}",
        f"- CC_lineage        : {report.cc_lineage:.3f}",
        f"- key precision     : {report.key_precision:.3f}",
        f"- key recall        : {report.key_recall:.3f}",
        "",
        f"## Verdict: {report.verdict.upper()}",
        "",
        report.detail,
        "",
        "## Group support profile",
        "",
        "| Group | Raw episodes | Unique lineages | Families | Dominant outcome |",
        "|---|---:|---:|---|---|",
    ]
    for g in groups:
        lines.append(
            f"| {g['effect_key']} | {g['size']} | {g['unique_lineages']} | "
            f"{';'.join(g['families'])} | {g['dominant_outcome']} |")
    lines.append("")
    (out_dir / "group_report.md").write_text("\n".join(lines) + "\n")
