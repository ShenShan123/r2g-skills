#!/usr/bin/env python3
"""Run a real, evaluation-only RTL conformal calibration campaign.

The campaign executes source-disjoint RTL fixtures with Icarus/vvp, stores the
verified transitions in a campaign-local calibration staging database, and
emits a typed obligation-set calibration receipt.  It never writes the
repository's canonical DB, never changes a rule lifecycle status, and never
imports production memory.

Example:
    PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_rtl_conformal_calibration.py \
      --artifacts /data1/zhangdy/tehm-campaigns/tehm-r3-rtl-conformal-calibration-20260903-r1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm import db as tehm_db  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.batch_lane import write_external_observations  # noqa: E402
from tehm.ids import stable_dumps  # noqa: E402
from tehm.rtl.conformal import (  # noqa: E402
    RTL_CONFORMAL_METHOD, RTL_CONFORMAL_PREDICTION_RULE,
    RTL_CONFORMAL_VERSION, RTLConformalSample, calibrate_rtl_obligations,
)
from tehm.rtl.rtl_evidence import build_rtl_execution_record  # noqa: E402
from tehm.rtl.rtl_oracle import IcarusOracle  # noqa: E402
from tehm.canonical.capture import capture  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "rtl_projects"
DEFAULT_FIXTURES = (
    "p3_obligation_recovery", "p3_obligation_recovery_b",
    "p3_predicate_unknown", "p3_positive_credit_return",
    "p3_positive_fifo_space", "p3_positive_valid_ready",
)
DEFAULT_EXCLUDED_LINEAGES = ("req_ack_fsm", "req_ack_fsm2", "req_ack_fsm4")
MANIFEST_VERSION = "rtl-conformal-calibration-manifest-v1"
REPORT_VERSION = "rtl-conformal-calibration-report-v1"


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _fixture_digest(path: Path) -> str:
    files = []
    for item in sorted(path.rglob("*")):
        if item.is_file():
            files.append({
                "path": str(item.relative_to(path)),
                "sha256": _sha_file(item),
            })
    return "sha256:" + _sha_bytes(stable_dumps(files).encode())


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def run_campaign(*, artifacts: Path, fixture_names: tuple[str, ...] = DEFAULT_FIXTURES,
                 excluded_lineages: tuple[str, ...] = DEFAULT_EXCLUDED_LINEAGES,
                 campaign_id: str = "tehm-r3-rtl-conformal-calibration-20260903-r1") -> dict:
    """Execute and freeze one source-disjoint RTL calibration cohort."""
    artifacts = Path(artifacts).expanduser().resolve()
    if artifacts == ROOT or ROOT in artifacts.parents:
        raise ValueError("calibration artifacts must stay outside the repository")
    if not fixture_names:
        raise ValueError("fixture_names must be non-empty")
    if len(set(fixture_names)) != len(fixture_names):
        raise ValueError("fixture_names must be unique")
    if not campaign_id.strip():
        raise ValueError("campaign_id is required")
    oracle = IcarusOracle()
    if not oracle.available:
        raise RuntimeError("iverilog/vvp are required for real RTL calibration")

    staging_dir = artifacts / "staging"
    database = staging_dir / "tehm.sqlite"
    store = ArtifactStore(staging_dir / "artifacts")
    conn = tehm_db.connect(database)
    tehm_db.ensure_schema(conn)
    records = []
    samples = []
    try:
        for fixture_name in fixture_names:
            fixture = (FIXTURES / fixture_name).resolve()
            if not fixture.is_dir() or not (fixture / "manifest.json").is_file():
                raise ValueError(f"RTL fixture is missing: {fixture_name}")
            record = build_rtl_execution_record(fixture, oracle=oracle, store=store)
            # Canonical capture normalizes these optional delta fields.  Put
            # the normalized values into the external witness before capture
            # so authority replay compares the exact persisted transition,
            # rather than relying on a permissive subset comparison.
            record.observation_delta.setdefault("experiment_kind", "UNKNOWN")
            record.observation_delta.setdefault("utility_verdict", "UNKNOWN")
            sample = RTLConformalSample.from_record(
                asdict(record), case_id=fixture_name, split="calibration")
            # Bind the per-obligation Icarus labels into the persisted delta.
            # VerifierSnapshot intentionally keeps adapter-only target and
            # regression details out of its compact canonical JSON; this
            # typed witness gives authority replay an immutable comparison
            # point without changing the verifier schema.
            first_divergence = record.observation_delta.setdefault(
                "first_divergence", {})
            first_divergence["rtl_obligations"] = dict(sample.observed)
            capture(
                conn, store, record,
                dataset_campaign_id=campaign_id,
                dataset_split="calibration",
                dataset_learner_eligible=False,
            )
            records.append((fixture_name, record))
            samples.append(sample)

        manifest_payload = {
            "version": MANIFEST_VERSION,
            "campaign_id": campaign_id,
            "method": RTL_CONFORMAL_METHOD,
            "prediction_set_rule": RTL_CONFORMAL_PREDICTION_RULE,
            "oracle": {
                "type": "IcarusOracle",
                "extractor_version": "icarus-oracle-v0.2",
                "iverilog": str(oracle.iverilog),
                "vvp": str(oracle.vvp),
            },
            "excluded_authority_lineages": sorted(set(excluded_lineages)),
            "fixtures": [
                {
                    "fixture": name,
                    "lineage_id": record.lineage_id,
                    "fixture_digest": _fixture_digest(FIXTURES / name),
                    "record_id": record.record_id,
                }
                for name, record in records
            ],
            "evaluation_only": True,
            "canonical_memory_mutation": "none",
        }
        manifest_digest = "sha256:" + _sha_bytes(
            stable_dumps(manifest_payload).encode())
        manifest_path = artifacts / "calibration_manifest.json"
        _write_json(manifest_path, manifest_payload)

        calibration = calibrate_rtl_obligations(
            samples,
            calibration_digest=manifest_digest,
            training_lineages=tuple(excluded_lineages),
            target_coverage=0.80,
            min_lineages=3,
        )
        conformal = calibration.authority_payload()
        observations = []
        for fixture_name, record in records:
            external_record = asdict(record)
            external_record["verification"] = {
                **external_record["verification"],
                "conformal": conformal,
                "conformal_receipt": calibration.to_dict(),
            }
            observations.append({
                "receipt_id": f"rtl-conformal:{campaign_id}:{fixture_name}",
                "case_id": fixture_name,
                "lineage_id": record.lineage_id,
                "split": "calibration",
                "classification": "ELIGIBLE_POSITIVE",
                "learner_eligible": False,
                "before": {"complete": True},
                "after": {"complete": True},
                "record": external_record,
            })
        observation_path = artifacts / "external" / "observations.jsonl"
        observation_info = write_external_observations(observation_path, observations)
        tehm_db.checkpoint_and_close(conn)
        conn = None

        staging_digest = "sha256:" + _sha_file(database)
        report = {
            "version": REPORT_VERSION,
            "campaign_id": campaign_id,
            "calibration_manifest": {
                "path": str(manifest_path),
                "sha256": manifest_digest,
            },
            "staging_db": {
                "path": str(database),
                "sha256": staging_digest,
            },
            "observations": {
                "path": str(observation_path),
                "sha256": "sha256:" + observation_info["sha256"],
                "chain_head": observation_info["chain_head"],
                "count": observation_info["count"],
            },
            "calibration": calibration.to_dict(),
            "status": "PASS" if calibration.eligible else "FAIL",
            "canonical_memory_mutation": "none",
            "production_authority_changed": False,
            "promotion_attempted": False,
        }
        report_path = artifacts / "rtl_conformal_calibration_report.json"
        _write_json(report_path, report)
        report["report_sha256"] = "sha256:" + _sha_file(report_path)
        # The digest is intentionally not included in the receipt or source
        # manifest; it is a convenient outer report witness only.
        _write_json(report_path, report)
        return report
    except Exception:
        if conn is not None:
            conn.close()
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--fixture", dest="fixtures", action="append",
                        default=None, help="source-disjoint RTL fixture (repeatable)")
    parser.add_argument("--exclude-lineage", dest="excluded_lineages",
                        action="append", default=None,
                        help="prior authority lineage excluded from calibration")
    parser.add_argument("--campaign-id", default=
                        "tehm-r3-rtl-conformal-calibration-20260903-r1")
    args = parser.parse_args(argv)
    fixture_names = tuple(args.fixtures) if args.fixtures else DEFAULT_FIXTURES
    excluded = (tuple(args.excluded_lineages)
                if args.excluded_lineages else DEFAULT_EXCLUDED_LINEAGES)
    try:
        report = run_campaign(
            artifacts=args.artifacts,
            fixture_names=fixture_names,
            excluded_lineages=excluded,
            campaign_id=args.campaign_id,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"RTL conformal calibration failed: {exc}", file=sys.stderr)
        return 1
    calibration = report["calibration"]
    print(json.dumps({
        "status": report["status"],
        "eligible": calibration["eligible"],
        "sample_count": calibration["sample_count"],
        "lineage_group_count": calibration["lineage_group_count"],
        "coverage": calibration["coverage"],
        "calibration_digest": calibration["calibration_digest"],
        "receipt_digest": calibration["receipt_digest"],
        "staging_db": report["staging_db"],
        "observations": report["observations"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
        "promotion_attempted": report["promotion_attempted"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
