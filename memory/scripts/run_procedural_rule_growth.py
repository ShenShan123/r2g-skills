#!/usr/bin/env python3
"""Grow procedural rules on an isolated copy of a canonical TEHM snapshot.

This is an evaluation-only campaign.  It captures additional real RTL fixes
and re-runs crystallization in a writable temporary database, while the
canonical snapshot remains read-only and byte-identical.  The resulting rule
profiles are evidence for cross-lineage support and validity, not promotion
authority.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from tehm import db as tehm_db  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.crystallization.build_rules import crystallize_all  # noqa: E402
from tehm.rtl.rtl_evidence import capture_rtl_fix  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402
from tehm.parametric.shadow_campaign import canonical_counts  # noqa: E402
from tehm.sync import canonical_json  # noqa: E402


DEFAULT_FIXTURES = (
    MEMORY_ROOT / "tests/fixtures/rtl_projects/valid_ready_bug",
    MEMORY_ROOT / "tests/fixtures/rtl_projects/fifo_space_bug",
)


def _read(path: Path):
    return json.loads(path.read_text())


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def _profile(rule: dict) -> dict:
    profile = rule.get("validity_profile") or {}
    v3 = next((gate for gate in profile.get("gates", [])
               if gate.get("name") == "V3"), {})
    detail = v3.get("detail") or {}
    return {
        "rule_id": rule.get("rule_id"),
        "domain": rule.get("domain"),
        "validity_status": rule.get("validity_status"),
        "num_sources": (next((gate.get("detail", {}).get("num_sources")
                               for gate in profile.get("gates", [])
                               if gate.get("name") == "V2"), None)),
        "cross_lineage": detail.get("cross_lineage"),
        "unique_attempts": detail.get("unique_attempts"),
        "unique_lineages": detail.get("unique_lineages"),
        "unique_families": detail.get("unique_families"),
        "v2_v4_valid": all(gate.get("ok") is True
                            for gate in profile.get("gates", [])
                            if gate.get("name") in {"V2", "V1", "V3", "V4"}),
    }


def _validate_work_root(path: Path) -> Path:
    resolved = path.resolve()
    if resolved != Path("/tmp") and Path("/tmp") not in resolved.parents:
        raise ValueError("rule-growth work root must be under /tmp")
    return resolved


def run(snapshot: Path, output: Path, work: Path, fixtures: tuple[Path, ...]) -> dict:
    snapshot = snapshot.resolve()
    output = output.resolve()
    work = _validate_work_root(work)
    source_db = snapshot / "closed_loop/tehm.sqlite"
    if not source_db.is_file():
        raise FileNotFoundError(f"canonical sqlite missing: {source_db}")
    for fixture in fixtures:
        if not (fixture / "manifest.json").is_file():
            raise FileNotFoundError(f"RTL fixture manifest missing: {fixture}")

    bundle = _read(snapshot / "bundle_manifest.json")
    work.mkdir(parents=True, exist_ok=True)
    work_db = work / "tehm.sqlite"
    shutil.copy2(source_db, work_db)
    canonical_before = canonical_counts(tehm_db.connect_read_only(source_db))

    oracle = IcarusOracle()
    if not oracle.available:
        raise RuntimeError("iverilog/vvp is required for procedural rule growth")
    conn = tehm_db.connect(work_db)
    tehm_db.ensure_schema(conn)
    store = ArtifactStore(work / "artifacts")
    receipts = []
    try:
        for fixture in fixtures:
            receipt = capture_rtl_fix(conn, store, fixture, oracle=oracle)
            receipts.append({
                "fixture": str(fixture),
                "design": _read(fixture / "manifest.json").get("design"),
                "transition_id": receipt.transition_id,
                "outcome": receipt.outcome,
            })
        rules = crystallize_all(conn)
        isolated_after = canonical_counts(conn)
        summaries = [_profile(rule) for rule in rules]
    finally:
        conn.close()

    source_after = canonical_counts(tehm_db.connect_read_only(source_db))
    if source_after != canonical_before:
        raise RuntimeError("canonical snapshot counters changed during rule growth")
    validated = [row for row in summaries if row["validity_status"] == "VALIDATED"]
    cross_lineage = [row for row in validated if row["cross_lineage"] is True]
    report = {
        "version": "tehm-procedural-rule-growth-v1",
        "canonical_bundle": str(snapshot),
        "bundle_digest": bundle.get("bundle_digest"),
        "manifest_digest": bundle.get("manifest_digest"),
        "fixtures": receipts,
        "canonical_snapshot_counts_before": canonical_before,
        "canonical_snapshot_counts_after": source_after,
        "isolated_work_counts_after": isolated_after,
        "canonical_memory_unchanged": source_after == canonical_before,
        "oracle": "icarus/vvp",
        "rule_summary": {
            "rules_total": len(summaries),
            "validated_rules": len(validated),
            "cross_lineage_validated_rules": len(cross_lineage),
            "profiles": summaries,
        },
        "promotion": {
            "status": "NOT_AUTHORIZED",
            "reason": "isolated evaluation copy; lifecycle/promotion not run",
        },
    }
    _write(output / "procedural_rule_growth_report.json", report)
    lines = [
        "# Procedural rule-growth report",
        "",
        f"- Fixtures captured: {len(receipts)} (real Icarus oracle)",
        f"- Isolated rules: {len(summaries)}; VALIDATED: {len(validated)}; "
        f"cross-lineage VALIDATED: {len(cross_lineage)}",
        f"- Canonical memory unchanged: `{report['canonical_memory_unchanged']}`",
        "- Promotion: `NOT_AUTHORIZED` (evaluation copy only)",
        "",
        "| rule | domain | validity | sources | lineages | cross-lineage |",
        "|---|---|---|---:|---:|---|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['rule_id']} | {row['domain']} | {row['validity_status']} | "
            f"{row['num_sources'] or 0} | {row['unique_lineages'] or 0} | "
            f"{row['cross_lineage']} |"
        )
    (output / "procedural_rule_growth_report.md").write_text("\n".join(lines) + "\n")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--work", type=Path, default=Path("/tmp/tehm-procedural-rule-growth-v1"))
    ap.add_argument("--fixture", type=Path, action="append", dest="fixtures")
    args = ap.parse_args(argv)
    fixtures = tuple((args.fixtures or list(DEFAULT_FIXTURES)))
    report = run(args.snapshot, args.output, args.work, fixtures)
    print(json.dumps({
        "ok": True,
        "canonical_memory_unchanged": report["canonical_memory_unchanged"],
        "validated_rules": report["rule_summary"]["validated_rules"],
        "cross_lineage_validated_rules": report["rule_summary"]["cross_lineage_validated_rules"],
        "output": str(args.output.resolve()),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
