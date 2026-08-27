#!/usr/bin/env python3
"""Read-only audit of bounded ORFS support cohorts.

The auditor consumes campaign-local staging stores and never imports or mutates
canonical memory.  It independently rechecks the persisted full-oracle receipt
on each captured transition, separates harmful observations from the selected
support cohort, and reports the six rule-promotion gates with explicit
``NOT_ESTABLISHED`` status where no independent authority evidence exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MEMORY_ROOT))

from tehm.batch_lane import canonical_snapshots  # noqa: E402
from tehm.causal.transfer import full_oracle_complete  # noqa: E402

GATES = (
    "rollback_verified", "registry_verified", "obligation_coverage",
    "cross_lineage_te", "harmful_rate", "conformal_coverage",
)


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _json(raw, fallback=None):
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {} if fallback is None else fallback
    return value


def _staging_db(root: Path) -> Path:
    candidates = (
        root / "staging" / "complete" / "tehm.sqlite",
        root / "staging" / "full" / "tehm.sqlite",
        root / "staging" / "tehm.sqlite",
    )
    ranked = []
    for order, path in enumerate(candidates):
        if not path.is_file():
            continue
        # A campaign may have a legacy partial capture beside a later
        # full-oracle recapture.  Prefer the DB with the most persisted
        # expanded receipts, then use the stable candidate order.
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            full = conn.execute(
                "SELECT COUNT(*) FROM tehm_transitions "
                "WHERE verifier_json LIKE '%full_oracle%'").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0]
            conn.close()
        except sqlite3.Error:
            continue
        ranked.append((int(full), int(total), -order, path))
    if ranked:
        return max(ranked)[-1]
    raise FileNotFoundError(f"no campaign staging DB below {root / 'staging'}")


def _campaign(root: Path, *, selected: bool) -> dict:
    root = root.resolve()
    manifest_path = root / "campaign_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    item_by_transition = {
        row.get("transition_id"): row
        for row in manifest.get("captured", [])
        if row.get("transition_id")
    }
    db = _staging_db(root)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT t.transition_id, t.observation_delta_json, t.verifier_json, "
        "t.provenance_json, p.deltas_json "
        "FROM tehm_transitions t LEFT JOIN tehm_physical_effects p "
        "ON p.transition_id=t.transition_id ORDER BY t.transition_id"
    ).fetchall()
    conn.close()
    observations = []
    for row in rows:
        transition_id = row["transition_id"]
        captured = item_by_transition.get(transition_id, {})
        delta = _json(row["observation_delta_json"])
        verifier = _json(row["verifier_json"])
        full_complete = full_oracle_complete(verifier)
        utility = str(delta.get("utility_verdict") or "UNKNOWN")
        observations.append({
            "case_id": captured.get("case_id"),
            "transition_id": transition_id,
            "lineage_id": captured.get("lineage_id"),
            "dataset_split": captured.get("dataset_split"),
            "learner_eligible": captured.get("learner_eligible"),
            "oracle_complete": verifier.get("oracle_complete") is True,
            "full_oracle_complete": full_complete,
            "utility_verdict": utility,
            "run_id": (_json(row["provenance_json"]).get("run_id")
                       or _json(row["provenance_json"]).get("record_id")),
            "deltas": _json(row["deltas_json"], fallback={}),
        })
    complete = [row for row in observations
                if row["oracle_complete"] and row["full_oracle_complete"]]
    selected_rows = [row for row in complete
                     if row["utility_verdict"] != "HARMFUL"] if selected else []
    harmful = [row for row in complete if row["utility_verdict"] == "HARMFUL"]
    return {
        "campaign_root": str(root),
        "campaign_manifest_sha256": _sha(manifest_path),
        "source_freeze_digest": manifest.get("source_freeze_digest"),
        "staging_db": str(db),
        "staging_db_sha256": _sha(db),
        "observations": observations,
        "complete_count": len(complete),
        "incomplete_count": len(observations) - len(complete),
        "selected_support_count": len(selected_rows),
        "harmful_complete_count": len(harmful),
        "selected": bool(selected),
    }


def audit(roots: list[Path], *, negative_roots: list[Path]) -> dict:
    selected_campaigns = [_campaign(path, selected=True) for path in roots]
    negative_campaigns = [_campaign(path, selected=False) for path in negative_roots]
    selected = [row for campaign in selected_campaigns
                for row in campaign["observations"]
                if row["oracle_complete"] and row["full_oracle_complete"]
                and row["utility_verdict"] != "HARMFUL"]
    lineages = sorted({row["lineage_id"] for row in selected if row.get("lineage_id")})
    harmful = [row for campaign in selected_campaigns
               for row in campaign["observations"]
               if row["oracle_complete"] and row["full_oracle_complete"]
               and row["utility_verdict"] == "HARMFUL"]
    negative_harmful = [row for campaign in negative_campaigns
                        for row in campaign["observations"]
                        if row["oracle_complete"] and row["full_oracle_complete"]
                        and row["utility_verdict"] == "HARMFUL"]
    complete_count = sum(campaign["complete_count"]
                         for campaign in selected_campaigns)
    incomplete_count = sum(campaign["incomplete_count"]
                           for campaign in selected_campaigns)
    # These are deliberately independent of the caller's desired decision.
    # Training-only observations cannot establish held-out transfer, rollback,
    # registry, or conformal evidence.
    gate_status = {
        "rollback_verified": "NOT_ESTABLISHED",
        "registry_verified": "NOT_ESTABLISHED",
        "obligation_coverage": "PASS" if selected and incomplete_count == 0 and all(
            row["full_oracle_complete"] for row in selected) else "NOT_ESTABLISHED",
        "cross_lineage_te": "NOT_ESTABLISHED",
        "harmful_rate": "PASS" if selected and not harmful else (
            "FAIL" if harmful else "NOT_ESTABLISHED"),
        "conformal_coverage": "NOT_ESTABLISHED",
    }
    report = {
        "version": "tehm-orfs-support-cohort-audit-v1",
        "campaigns": selected_campaigns,
        "negative_control_campaigns": negative_campaigns,
        "support_observation_count": len(selected),
        "complete_observation_count": complete_count,
        "incomplete_observation_count": incomplete_count,
        "unique_lineages": lineages,
        "unique_lineage_count": len(lineages),
        "harmful_complete_count": len(harmful),
        "negative_control_harmful_count": len(negative_harmful),
        "harmful_rate": (len(harmful) / len(selected) if selected else None),
        "gate_status": gate_status,
        "gates": {name: status == "PASS" for name, status in gate_status.items()},
        "all_gates_established": all(status == "PASS" for status in gate_status.values()),
        "decision": "ALLOW_AUTHORITY_REVIEW" if all(
            status == "PASS" for status in gate_status.values())
        else "DENY_CANONICAL_IMPORT",
        "promotion_attempted": False,
        "canonical_memory_mutation": "none",
        "canonical_snapshots": canonical_snapshots(),
    }
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path,
                        help="campaign roots supplying candidate support observations")
    parser.add_argument("--negative-root", action="append", type=Path, default=[],
                        help="complete campaign root retained as an excluded negative control")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = audit(args.roots, negative_roots=args.negative_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "support_observation_count": report["support_observation_count"],
        "unique_lineage_count": report["unique_lineage_count"],
        "gate_status": report["gate_status"],
        "decision": report["decision"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
