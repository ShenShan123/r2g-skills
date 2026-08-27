#!/usr/bin/env python3
"""Run an isolated P3 rule-growth plus runtime ablation campaign.

The campaign first captures two fresh, executable ``RESET_RESTORE`` RTL
lineages into a temporary copy of the canonical v3 bundle, crystallizes the
copy, and enrolls the resulting rule only as a candidate in that copy.  The
existing component-ablation runner then measures candidate-visible Rule
Coverage/VCG on the fresh tasks.  The canonical bundle is never opened writable
and no lifecycle result is promoted as production authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db  # noqa: E402
from evaluation.freeze_pointer import resolve_bundle  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.crystallization.build_rules import crystallize_all  # noqa: E402
from tehm.lifecycle.rule_status import enter_shadow, set_status  # noqa: E402
from tehm.parametric.shadow_campaign import canonical_counts  # noqa: E402
from tehm.rtl.rtl_evidence import capture_rtl_fix  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402
from tehm.sync import canonical_json  # noqa: E402
from run_procedural_ablation import run as run_ablation  # noqa: E402
from prepare_procedural_ablation_manifest import validate  # noqa: E402


GROWTH_TRAINING_FIXTURE = ROOT / "tests/fixtures/rtl_projects/p3_reset_restore_c"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def _reset_rule(conn) -> str:
    row = conn.execute(
        "SELECT rule_id FROM tehm_rules WHERE domain='rtl' "
        "AND validity_status IN ('PROVISIONAL_VALID','VALIDATED') "
        "AND json_extract(before_pattern_json, '$.type')='RESET_RESTORE' "
        "ORDER BY rule_id LIMIT 1").fetchone()
    if row is None:
        raise RuntimeError("crystallization did not produce an admissible RESET_RESTORE rule")
    return str(row["rule_id"])


def _enroll_local_rule(conn, rule_id: str) -> dict:
    """Give the isolated rule candidate visibility without promotion authority."""
    enter_shadow(conn, rule_id=rule_id, target_scope="rtl",
                 provenance={"authority": "p3_growth_ablation_staging"})
    set_status(conn, rule_id=rule_id, target_scope="rtl", status="candidate",
               provenance={"authority": "p3_growth_ablation_staging"})
    row = conn.execute(
        "SELECT rule_id, target_scope, status, status_version "
        "FROM tehm_rule_status WHERE rule_id=? AND target_scope='rtl'",
        (rule_id,)).fetchone()
    result = dict(row)
    result["production_authority"] = False
    result["promotion_gates"] = {
        "status": "not_evaluated",
        "reason": "isolated_growth_is_candidate_only",
    }
    return result


def run(*, snapshot: Path, manifest_path: Path, output: Path,
        work: Path) -> dict:
    snapshot = snapshot.resolve()
    output = output.resolve()
    work = work.resolve()
    if Path("/tmp") not in work.parents and work != Path("/tmp"):
        raise ValueError("staging work must be under /tmp")
    if not (snapshot / "bundle_manifest.json").is_file():
        raise FileNotFoundError("canonical bundle manifest missing")
    normalized = validate(_read(manifest_path), repo_root=REPO_ROOT)
    if not normalized["validation"]["fixtures_materialized"]:
        raise RuntimeError("growth manifest has pending fixtures")
    expected_digest = normalized["source_snapshot"]["bundle_digest"]
    supplied_manifest = _read(snapshot / "bundle_manifest.json")
    if supplied_manifest.get("bundle_digest") != expected_digest:
        raise RuntimeError("growth manifest does not bind supplied canonical bundle")

    if work.exists():
        shutil.rmtree(work)
    work.parent.mkdir(parents=True, exist_ok=True)
    staging = work / "staging_snapshot"
    shutil.copytree(snapshot, staging)
    source_db = snapshot / "closed_loop" / "tehm.sqlite"
    staging_db = staging / "closed_loop" / "tehm.sqlite"
    before_sha = _sha256(source_db)
    source_counts = canonical_counts(db.connect_read_only(source_db))

    oracle = IcarusOracle()
    if not oracle.available:
        raise RuntimeError("iverilog/vvp is required for P3 growth ablation")
    conn = db.connect(staging_db)
    db.ensure_schema(conn)
    store = ArtifactStore(staging / "closed_loop" / "artifacts")
    receipts = []
    try:
        # A third independent source is captured before crystallization so the
        # RESET_RESTORE rule can be evaluated through V4 instead of remaining
        # permanently provisional at n=2.  It is a training-growth lineage,
        # not one of the prospective ablation targets.
        growth_receipt = capture_rtl_fix(
            conn, store, GROWTH_TRAINING_FIXTURE, oracle=oracle)
        receipts.append({
            "task_id": "p3-growth:reset-restore:training-c",
            "fixture": str(GROWTH_TRAINING_FIXTURE.relative_to(REPO_ROOT)),
            "lineage_id": "reset_restore_c",
            "transition_id": growth_receipt.transition_id,
            "outcome": growth_receipt.outcome,
            "role": "rule_growth_training",
        })
        for task in normalized["tasks"]:
            fixture = (REPO_ROOT / task["fixture"]).resolve()
            receipt = capture_rtl_fix(conn, store, fixture, oracle=oracle)
            receipts.append({
                "task_id": task["task_id"],
                "fixture": task["fixture"],
                "lineage_id": task["lineage_id"],
                "transition_id": receipt.transition_id,
                "outcome": receipt.outcome,
            })
        rules = crystallize_all(conn)
        reset_rule_id = _reset_rule(conn)
        local_status = _enroll_local_rule(conn, reset_rule_id)
        staging_counts = canonical_counts(conn)
        profiles = []
        for rule in rules:
            if rule.get("rule_id") == reset_rule_id or rule.get("domain") == "rtl":
                profile = rule.get("validity_profile") or {}
                profiles.append({
                    "rule_id": rule.get("rule_id"),
                    "domain": rule.get("domain"),
                    "validity_status": rule.get("validity_status"),
                    "validity_profile": profile,
                })
    finally:
        conn.close()

    # Run the real per-arm Icarus ablation against the prepared staging copy.
    ablation_output = work / "ablation"
    ablation = run_ablation(snapshot=staging, manifest_path=manifest_path,
                            output=ablation_output,
                            lifecycle_statuses=frozenset({"candidate"}))
    after_sha = _sha256(source_db)
    source_after = canonical_counts(db.connect_read_only(source_db))
    unchanged = before_sha == after_sha and source_counts == source_after
    if not unchanged:
        raise RuntimeError("canonical bundle changed during isolated growth ablation")

    report = {
        "version": "tehm-procedural-growth-ablation-v2",
        "canonical_bundle": str(snapshot),
        "bundle_digest": expected_digest,
        "manifest_digest": ablation["manifest_digest"],
        "staging_snapshot": str(staging),
        "fresh_rtl_receipts": receipts,
        "local_rule_status": local_status,
        "rtl_rule_profiles": profiles,
        "canonical_counts_before": source_counts,
        "canonical_counts_after": source_after,
        "staging_counts_after": staging_counts,
        "canonical_sha256_before": before_sha,
        "canonical_sha256_after": after_sha,
        "canonical_memory_unchanged": unchanged,
        "ablation": ablation,
        "promotion": {
            "status": "NOT_AUTHORIZED",
            "reason": "runtime status exists only in an isolated staging copy; no canonical lifecycle mutation",
        },
    }
    _write(output / "procedural_growth_ablation_report.json", report)
    observed = ablation["observed"]
    markdown = "\n".join([
        "# Procedural rule-growth + runtime ablation (v2)", "",
        f"- Fresh executable RTL lineages: **{len(receipts)}**",
        f"- RESET_RESTORE rule enrolled in staging only: `{reset_rule_id}`",
        f"- Validated rules in staging: **{observed['validated_rules']}**",
        f"- Cross-lineage rule support: **{observed['cross_lineage_rule_support']}**",
        f"- Runtime Rule Coverage: **{observed['rule_coverage']:.4f}**",
        f"- Runtime VCG: **{observed['vcg']:.4f}**",
        f"- Harmful activation rate: **{observed['harmful_activation_rate']:.4f}**",
        f"- Acceptance passed: **{ablation['acceptance_passed']}**",
        f"- Canonical memory unchanged: **{unchanged}**",
        "- Promotion: **NOT_AUTHORIZED** (isolated staging authority only)", "",
        "## Fresh receipts", "",
        "| task | lineage | transition | outcome |",
        "|---|---|---|---|",
    ] + [
        f"| {row['task_id']} | {row['lineage_id']} | {row['transition_id']} | {row['outcome']} |"
        for row in receipts
    ] + ["", "## Acceptance checks", "", "```json",
          json.dumps(ablation["acceptance_checks"], indent=2, sort_keys=True),
          "```", ""])
    (output / "procedural_growth_ablation_report.md").write_text(markdown)
    _write(output / "procedural_ablation_report.json", ablation)
    _write(output / "procedural_ablation_manifest.normalized.json",
           _read(ablation_output / "procedural_ablation_manifest.normalized.json"))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=resolve_bundle(require_exists=False))
    ap.add_argument("--manifest", type=Path,
                    default=ROOT / "evaluation" /
                    "procedural_growth_ablation_task_manifest_v2.json")
    ap.add_argument("--output", type=Path,
                    default=Path("/tmp/tehm-procedural-growth-ablation-v2"))
    ap.add_argument("--work", type=Path,
                    default=Path("/tmp/tehm-procedural-growth-ablation-v2/work"))
    args = ap.parse_args(argv)
    try:
        report = run(snapshot=args.snapshot, manifest_path=args.manifest,
                     output=args.output, work=args.work)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"procedural growth ablation refused: {exc}", file=sys.stderr)
        return 2
    observed = report["ablation"]["observed"]
    print(json.dumps({
        "ok": True,
        "canonical_memory_unchanged": report["canonical_memory_unchanged"],
        "validated_rules": observed["validated_rules"],
        "rule_coverage": observed["rule_coverage"],
        "vcg": observed["vcg"],
        "acceptance_passed": report["ablation"]["acceptance_passed"],
        "output": str(args.output.resolve()),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
