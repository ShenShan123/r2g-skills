#!/usr/bin/env python3
"""Audit the transition -> effect group -> procedural rule funnel read-only."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from tehm import db  # noqa: E402
from tehm.crystallization.preflight import run_preflight  # noqa: E402
from tehm.sync import canonical_json  # noqa: E402


def audit(conn, *, out_dir: Path | None = None) -> dict:
    preflight = run_preflight(conn, out_dir=out_dir)
    rows = conn.execute(
        "SELECT rule_id, validity_status, validity_profile_json "
        "FROM tehm_rules ORDER BY rule_id").fetchall()
    validity = Counter()
    cross_lineage = 0
    support_profiles = []
    for row in rows:
        validity[row["validity_status"]] += 1
        profile = db.read_json(row["validity_profile_json"])
        gates = {item.get("name"): item for item in profile.get("gates", [])
                 if isinstance(item, dict)}
        v3 = gates.get("V3", {}).get("detail", {})
        n_lineages = int(v3.get("unique_lineages", 0) or 0)
        if n_lineages >= 2:
            cross_lineage += 1
        support_profiles.append({
            "rule_id": row["rule_id"],
            "validity_status": row["validity_status"],
            "raw_support": v3.get("raw_support"),
            "unique_lineages": n_lineages,
            "unique_families": v3.get("unique_families"),
        })
    total = preflight.total_transitions
    non_singleton = sum(1 for group in preflight.groups.values()
                        if group["size"] >= preflight.min_group_size)
    result = {
        "version": "procedural-memory-audit-v1",
        "transition_count": total,
        "effect_group_count": preflight.num_groups,
        "non_singleton_group_count": non_singleton,
        "singleton_rate": preflight.singleton_rate,
        "cc_raw": preflight.cc_raw,
        "cc_lineage": preflight.cc_lineage,
        "rule_count": len(rows),
        "validity_distribution": dict(validity),
        "cross_lineage_rule_count": cross_lineage,
        "rule_conversion_rate": len(rows) / non_singleton if non_singleton else None,
        "support_profiles": support_profiles,
        "gaps": {
            "group_to_rule_conversion": "expand_crystallization_or_action_domain"
            if len(rows) < non_singleton else "no_observed_group_loss",
            "cross_lineage_rule_support": "add_independent_rule_sources"
            if cross_lineage == 0 else "present",
            "component_ablation": "use_procedural_ablation_plan_v1",
        },
        "preflight": preflight.to_dict(),
    }
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "procedural_memory_audit.json").write_bytes(canonical_json(result))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    conn = db.connect_read_only(args.db.resolve())
    try:
        result = audit(conn, out_dir=args.out_dir.resolve() if args.out_dir else None)
    finally:
        conn.close()
    print(json.dumps({key: result[key] for key in (
        "transition_count", "effect_group_count", "non_singleton_group_count",
        "rule_count", "cross_lineage_rule_count", "rule_conversion_rate", "gaps")},
        indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
