#!/usr/bin/env python3
"""Audit procedural rule stability on isolated leave-one-lineage-out copies.

The canonical TEHM snapshot is copied to a temporary writable DB for one full
capture and one run per omitted fixture.  Rules are compared by their
content-addressed executable pattern rather than volatile rule IDs.  This is
evidence for support and stability only: no lifecycle, activation, or
promotion path is called and the source snapshot is checked byte-for-byte.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db as tehm_db  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.crystallization.build_rules import crystallize_all  # noqa: E402
from tehm.crystallization.preflight import run_preflight  # noqa: E402
from tehm.parametric.shadow_campaign import canonical_counts, digest  # noqa: E402
from tehm.rtl.rtl_evidence import capture_rtl_fix  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402
from tehm.sync import canonical_json  # noqa: E402


DEFAULT_FIXTURES = (
    ROOT / "tests/fixtures/rtl_projects/valid_ready_bug",
    ROOT / "tests/fixtures/rtl_projects/fifo_space_bug",
)
MIN_STABLE_RETENTION = 0.50


def _read(path: Path):
    return json.loads(path.read_text())


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def _validate_work_root(path: Path) -> Path:
    resolved = path.resolve()
    if resolved != Path("/tmp") and Path("/tmp") not in resolved.parents:
        raise ValueError("rule-stability work root must be under /tmp")
    return resolved


def _rule_signature(rule: dict) -> str:
    """Digest only executable rule content, excluding support/volatile fields."""
    content = {
        key: rule.get(key)
        for key in (
            "domain", "action_domain", "transformation_family",
            "before_pattern", "after_pattern", "hard_preconditions",
            "context_predicates", "obligations", "predicate_schema_version",
            "role_schema_version", "crystallizer_version",
        )
    }
    return digest(content)


def _profile(rule: dict) -> dict:
    profile = rule.get("validity_profile") or {}
    gates = {gate.get("name"): gate for gate in profile.get("gates", [])
             if isinstance(gate, dict)}
    v3 = gates.get("V3", {}).get("detail", {}) or {}
    return {
        "rule_id": rule.get("rule_id"),
        "signature": _rule_signature(rule),
        "validity_status": rule.get("validity_status"),
        "unique_attempts": v3.get("unique_attempts"),
        "unique_lineages": v3.get("unique_lineages"),
        "unique_families": v3.get("unique_families"),
        "cross_lineage": v3.get("cross_lineage"),
        "v2_v4_valid": all(
            gate.get("ok") is True
            for name, gate in gates.items()
            if name in {"V2", "V1", "V3", "V4"}),
    }


def _source_transition_coverage(conn, validated_rule_ids: set[str]) -> dict:
    if not validated_rule_ids:
        return {"covered": 0, "total": 0, "coverage": None}
    total = int(conn.execute("SELECT COUNT(*) FROM tehm_transitions").fetchone()[0])
    marks = ",".join("?" for _ in validated_rule_ids)
    covered = int(conn.execute(
        "SELECT COUNT(DISTINCT s.episode_id || ':' || s.source_substitution_json) "
        "FROM tehm_rule_sources s JOIN tehm_rules r ON r.rule_id=s.rule_id "
        f"WHERE r.rule_id IN ({marks})", tuple(sorted(validated_rule_ids))).fetchone()[0])
    return {"covered": covered, "total": total,
            "coverage": covered / total if total else None}


def _run_variant(snapshot: Path, work: Path, name: str,
                 fixtures: tuple[Path, ...], oracle: IcarusOracle) -> dict:
    variant = work / name
    variant.mkdir(parents=True, exist_ok=True)
    db_path = variant / "tehm.sqlite"
    shutil.copy2(snapshot / "closed_loop" / "tehm.sqlite", db_path)
    conn = tehm_db.connect(db_path)
    tehm_db.ensure_schema(conn)
    receipts = []
    try:
        store = ArtifactStore(variant / "artifacts")
        for fixture in fixtures:
            receipt = capture_rtl_fix(conn, store, fixture, oracle=oracle)
            receipts.append({
                "fixture": str(fixture),
                "outcome": receipt.outcome,
                "transition_id": receipt.transition_id,
            })
        rules = crystallize_all(conn)
        counts = canonical_counts(conn)
        preflight = run_preflight(conn)
        validated = [rule for rule in rules
                     if rule.get("validity_status") == "VALIDATED"]
        profiles = [_profile(rule) for rule in rules]
        source_coverage = _source_transition_coverage(
            conn, {rule["rule_id"] for rule in validated})
    finally:
        conn.close()
    return {
        "name": name,
        "fixtures": receipts,
        "counts": counts,
        "rules_total": len(rules),
        "validated_rules": len(validated),
        "cross_lineage_validated_rules": sum(
            row.get("cross_lineage") is True for row in profiles
            if row.get("validity_status") == "VALIDATED"),
        "profiles": profiles,
        "preflight": {
            "total_transitions": preflight.total_transitions,
            "effect_group_count": preflight.num_groups,
            "non_singleton_effect_groups": sum(
                group["size"] >= preflight.min_group_size
                for group in preflight.groups.values()),
            "cc_raw": preflight.cc_raw,
            "cc_lineage": preflight.cc_lineage,
        },
        "validated_source_transition_coverage": source_coverage,
        "runtime_rule_coverage": {
            "status": "not_available",
            "reason": "stability replay does not execute activation or A/B",
        },
    }


def run(snapshot: Path, output: Path, work: Path,
        fixtures: tuple[Path, ...]) -> dict:
    snapshot = snapshot.resolve()
    work = _validate_work_root(work)
    source_db = snapshot / "closed_loop" / "tehm.sqlite"
    if not source_db.is_file():
        raise FileNotFoundError(f"canonical sqlite missing: {source_db}")
    bundle = _read(snapshot / "bundle_manifest.json")
    for fixture in fixtures:
        if not (fixture / "manifest.json").is_file():
            raise FileNotFoundError(f"RTL fixture manifest missing: {fixture}")
    oracle = IcarusOracle()
    if not oracle.available:
        raise RuntimeError("iverilog/vvp is required for procedural rule stability")

    source_before = source_db.read_bytes()
    source_counts_conn = tehm_db.connect_read_only(source_db)
    source_counts = canonical_counts(source_counts_conn)
    source_counts_conn.close()
    work.mkdir(parents=True, exist_ok=True)
    names = {fixture.name: fixture for fixture in fixtures}
    variants = {}
    variants["full"] = _run_variant(snapshot, work, "full", fixtures, oracle)
    for omitted in fixtures:
        retained = tuple(fixture for fixture in fixtures if fixture != omitted)
        variants[f"loo_without_{omitted.name}"] = _run_variant(
            snapshot, work, f"loo_without_{omitted.name}", retained, oracle)

    source_after = source_db.read_bytes()
    if source_before != source_after:
        raise RuntimeError("canonical snapshot bytes changed during stability replay")
    after_counts_conn = tehm_db.connect_read_only(source_db)
    source_after_counts = canonical_counts(after_counts_conn)
    after_counts_conn.close()
    if source_counts != source_after_counts:
        raise RuntimeError("canonical snapshot counters changed during stability replay")

    full_profiles = variants["full"]["profiles"]
    full_validated = {
        row["signature"] for row in full_profiles
        if row["validity_status"] == "VALIDATED"
    }
    loo = {}
    retentions = []
    for name, variant in variants.items():
        if name == "full":
            continue
        validated = {
            row["signature"] for row in variant["profiles"]
            if row["validity_status"] == "VALIDATED"
        }
        retained = len(full_validated & validated)
        retention = retained / len(full_validated) if full_validated else None
        retentions.append(retention if retention is not None else 0.0)
        loo[name] = {
            "validated_signatures": len(validated),
            "stable_validated_rules": retained,
            "validated_rule_retention": retention,
            "cross_lineage_validated_rules": variant["cross_lineage_validated_rules"],
        }
    stability = {
        "min_validated_rule_retention": min(retentions) if retentions else None,
        "required_min_retention": MIN_STABLE_RETENTION,
        "passed": bool(retentions) and min(retentions) >= MIN_STABLE_RETENTION,
        "loo": loo,
    }
    report = {
        "version": "tehm-procedural-rule-stability-v1",
        "canonical_bundle": str(snapshot),
        "bundle_digest": bundle.get("bundle_digest"),
        "manifest_digest": bundle.get("manifest_digest"),
        "fixtures": [str(path) for path in fixtures],
        "canonical_snapshot_counts_before": source_counts,
        "canonical_snapshot_counts_after": source_after_counts,
        "canonical_memory_unchanged": source_counts == source_after_counts,
        "oracle": "icarus/vvp",
        "variants": variants,
        "stability": stability,
        "promotion": {
            "status": "NOT_AUTHORIZED",
            "reason": "isolated leave-one-lineage-out replay; no lifecycle or A/B",
        },
    }
    _write(output / "procedural_rule_stability_report.json", report)
    lines = [
        "# Procedural rule stability report", "",
        f"- Canonical memory unchanged: `{report['canonical_memory_unchanged']}`",
        f"- Full validated rules: {len(full_validated)}",
        f"- Minimum leave-one-lineage-out retention: "
        f"{stability['min_validated_rule_retention']}",
        f"- Stability gate (descriptive, no promotion): `{stability['passed']}`",
        "- Runtime Rule Coverage/VCG: `NOT_AVAILABLE` (no activation/A/B)", "",
        "| variant | rules | validated | cross-lineage validated | source coverage |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, variant in variants.items():
        lines.append(
            f"| {name} | {variant['rules_total']} | {variant['validated_rules']} | "
            f"{variant['cross_lineage_validated_rules']} | "
            f"{variant['validated_source_transition_coverage']['coverage']} |")
    (output / "procedural_rule_stability_report.md").write_text(
        "\n".join(lines) + "\n")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--work", type=Path,
                    default=Path("/tmp/tehm-procedural-rule-stability-v1"))
    ap.add_argument("--fixture", type=Path, action="append", dest="fixtures")
    args = ap.parse_args(argv)
    fixtures = tuple(args.fixtures or DEFAULT_FIXTURES)
    report = run(args.snapshot, args.output, args.work, fixtures)
    print(json.dumps({
        "ok": True,
        "canonical_memory_unchanged": report["canonical_memory_unchanged"],
        "full_validated_rules": report["variants"]["full"]["validated_rules"],
        "min_validated_rule_retention": report["stability"]["min_validated_rule_retention"],
        "stability_passed": report["stability"]["passed"],
        "output": str(args.output.resolve()),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
