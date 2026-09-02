#!/usr/bin/env python3
"""Replay eligible P13 triggers through the isolated shadow-update executor.

The trigger report and update manifest are separate, content-addressed inputs.
This command opens the source SQLite database read-only; ``apply_localized_update_shadow``
copies it to an in-memory staging database, applies each typed plan, verifies
the source remained unchanged, and discards staging.  It never writes
canonical memory, lifecycle authority, or production runtime state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tehm.evolution import (  # noqa: E402
    AntiForgettingWitness,
    AppliedShadowUpdateReceipt,
    LocalizedUpdatePlan,
    P12_SHADOW_TRIGGER_VERSION,
    P12ShadowUpdateTriggerReceipt,
    ShadowUpdateError,
    apply_localized_update_shadow,
)
from tehm.ids import stable_dumps  # noqa: E402


REPORT_VERSION = "p13-shadow-update-run-report-v1"
MANIFEST_VERSION = "p13-shadow-update-manifest-v1"


class P13ShadowRunError(ValueError):
    """The trigger report or isolated update manifest is unsafe."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _digest(value: Mapping) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(dict(value)).encode()).hexdigest()


def _load_json(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise P13ShadowRunError(f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise P13ShadowRunError(f"{name} must be a JSON object")
    return payload


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise P13ShadowRunError(f"{name} must be a non-empty string")
    return value.strip()


def _digest_text(value: object, name: str) -> str:
    value = _text(value, name)
    if not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
        raise P13ShadowRunError(f"{name} must be a sha256 digest")
    return value


def _trigger_map(path: Path) -> tuple[dict, dict[str, P12ShadowUpdateTriggerReceipt]]:
    report = _load_json(path, "P13 trigger report")
    if report.get("version") != "p13-shadow-trigger-report-v1":
        raise P13ShadowRunError("P13 trigger report version mismatch")
    if report.get("p13_eligible") is not True:
        raise P13ShadowRunError("P13 trigger report is not eligible")
    raw = report.get("triggers")
    if not isinstance(raw, list) or not raw:
        raise P13ShadowRunError("P13 trigger report has no triggers")
    if report.get("trigger_count") != len(raw):
        raise P13ShadowRunError("P13 trigger report trigger_count is inconsistent")
    if report.get("triggered_count") != len(raw):
        raise P13ShadowRunError("P13 trigger report triggered_count is inconsistent")
    result: dict[str, P12ShadowUpdateTriggerReceipt] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise P13ShadowRunError("P13 trigger entry must be an object")
        try:
            trigger = P12ShadowUpdateTriggerReceipt.from_dict(item)
        except (TypeError, ValueError) as exc:
            raise P13ShadowRunError(f"P13 trigger entry is invalid: {exc}") from exc
        if trigger.version != P12_SHADOW_TRIGGER_VERSION:
            raise P13ShadowRunError(
                "P13 trigger report contains a legacy trigger; current mutation "
                "requires p12-shadow-trigger-v0.2")
        if trigger.triggered is not True:
            raise P13ShadowRunError("P13 trigger report contains a non-triggering entry")
        supplied_digest = item.get("receipt_digest")
        if supplied_digest != trigger.receipt_digest:
            raise P13ShadowRunError(
                f"P13 trigger entry {trigger.case_id} receipt digest mismatch")
        if trigger.case_id in result:
            raise P13ShadowRunError("P13 trigger report contains duplicate case IDs")
        result[trigger.case_id] = trigger
    campaign_id = _text(report.get("campaign_id"), "trigger report campaign_id")
    if any(trigger.campaign_id != campaign_id for trigger in result.values()):
        raise P13ShadowRunError("P13 trigger campaign IDs are inconsistent")
    if report.get("canonical_memory_mutation") != "none":
        raise P13ShadowRunError(
            "P13 trigger report crosses canonical memory boundary")
    if report.get("production_runtime_imported") is not False:
        raise P13ShadowRunError(
            "P13 trigger report imports production runtime state")
    if report.get("production_integration") != "not_attempted":
        raise P13ShadowRunError(
            "P13 trigger report has production integration state")
    if report.get("shadow_update_policy") != "isolated_staging_only":
        raise P13ShadowRunError(
            "P13 trigger report shadow policy is invalid")
    supplied_report_digest = report.get("report_digest")
    if type(supplied_report_digest) is not str or not supplied_report_digest.startswith("sha256:"):
        raise P13ShadowRunError("P13 trigger report digest is required")
    digest_payload = dict(report)
    digest_payload.pop("report_digest", None)
    if supplied_report_digest != _digest(digest_payload):
        raise P13ShadowRunError("P13 trigger report digest mismatch")
    return report, result


def _open_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise P13ShadowRunError(f"source SQLite database is not a file: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_bound_anti_forgetting(
        raw_ref: object, *, case_id: str, campaign_id: str,
        manifest_path: Path, forbidden_paths: set[Path],
        ) -> tuple[dict, dict]:
    """Load one file-bound witness report and return payload plus audit ref."""
    if not isinstance(raw_ref, Mapping):
        raise P13ShadowRunError(
            f"P13 anti-forgetting receipt for {case_id} must be an object")
    raw_path = _text(raw_ref.get("path"),
                     f"P13 anti-forgetting receipt path for {case_id}")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    path = path.resolve()
    if path in forbidden_paths:
        raise P13ShadowRunError(
            f"P13 anti-forgetting receipt for {case_id} reuses an input/output file")
    if not path.is_file():
        raise P13ShadowRunError(
            f"P13 anti-forgetting receipt for {case_id} is not a file: {path}")
    expected = _text(raw_ref.get("sha256"),
                     f"P13 anti-forgetting receipt sha256 for {case_id}")
    actual = _sha256(path)
    if expected != actual:
        raise P13ShadowRunError(
            f"P13 anti-forgetting receipt sha256 does not match {path}")
    report = _load_json(path, f"P13 anti-forgetting receipt for {case_id}")
    if report.get("version") != "p13-anti-forgetting-witness-report-v1":
        raise P13ShadowRunError(
            f"P13 anti-forgetting receipt for {case_id} version mismatch")
    if report.get("campaign_id") != campaign_id or report.get("case_id") != case_id:
        raise P13ShadowRunError(
            f"P13 anti-forgetting receipt for {case_id} campaign/case mismatch")
    try:
        witness = AntiForgettingWitness.from_dict(report.get("witness"))
    except (TypeError, ValueError) as exc:
        raise P13ShadowRunError(
            f"P13 anti-forgetting receipt for {case_id} witness is invalid: {exc}") from exc
    if witness.eligible is not True:
        raise P13ShadowRunError(
            f"P13 anti-forgetting witness for {case_id} is not eligible")
    if report.get("eligible") is not witness.eligible:
        raise P13ShadowRunError(
            f"P13 anti-forgetting receipt for {case_id} eligible flag mismatch")
    supplied_digest = report.get("witness", {}).get("receipt_digest")
    if supplied_digest != witness.receipt_digest:
        raise P13ShadowRunError(
            f"P13 anti-forgetting receipt for {case_id} digest mismatch")
    return {
        **witness.to_dict(), "receipt_digest": witness.receipt_digest,
    }, {
        "path": str(path), "sha256": actual,
        "witness_receipt_digest": witness.receipt_digest,
    }


def _decode_updates(
        updates: Mapping,
        triggers: Mapping[str, P12ShadowUpdateTriggerReceipt],
        campaign_id: str, *, anti_forgetting_receipts: object,
        manifest_path: Path, forbidden_paths: set[Path],
        ) -> dict[str, tuple[LocalizedUpdatePlan, Mapping, dict | None]]:
    """Validate all plans and mutation witnesses before opening source state."""
    if anti_forgetting_receipts is None:
        anti_forgetting_receipts = {}
    if not isinstance(anti_forgetting_receipts, Mapping):
        raise P13ShadowRunError(
            "P13 anti_forgetting_receipts must be an object")
    if set(anti_forgetting_receipts) - set(triggers):
        raise P13ShadowRunError(
            "P13 anti_forgetting_receipts contains unknown case IDs")
    decoded: dict[str, tuple[LocalizedUpdatePlan, Mapping, dict | None]] = {}
    for case_id in sorted(triggers):
        item = updates[case_id]
        if not isinstance(item, Mapping):
            raise P13ShadowRunError(f"P13 update for {case_id} must be an object")
        raw_plan = item.get("plan")
        if not isinstance(raw_plan, Mapping):
            raise P13ShadowRunError(
                f"P13 update plan for {case_id} must be an object")
        try:
            plan = LocalizedUpdatePlan.from_dict(raw_plan)
        except (TypeError, ValueError) as exc:
            raise P13ShadowRunError(
                f"P13 update plan for {case_id} is invalid: {exc}") from exc
        if raw_plan.get("plan_digest") != plan.plan_digest:
            raise P13ShadowRunError(
                f"P13 update plan for {case_id} plan digest mismatch")
        trigger = triggers[case_id]
        if plan.campaign_id != campaign_id:
            raise P13ShadowRunError(
                f"P13 update plan for {case_id} campaign_id disagrees")
        if trigger.receipt_digest not in plan.evidence_refs:
            raise P13ShadowRunError(
                f"P13 update plan for {case_id} must witness its trigger digest")
        evidence = item.get("evidence")
        if not isinstance(evidence, Mapping):
            raise P13ShadowRunError(
                f"P13 update evidence for {case_id} must be an object")
        evidence = dict(evidence)
        raw_trigger = evidence.get("p12_shadow_trigger")
        if not isinstance(raw_trigger, Mapping):
            raise P13ShadowRunError(
                f"P13 update evidence for {case_id} requires its P12 trigger")
        if raw_trigger.get("receipt_digest") != trigger.receipt_digest:
            raise P13ShadowRunError(
                f"P13 update evidence for {case_id} trigger digest disagrees")
        try:
            evidence_trigger = P12ShadowUpdateTriggerReceipt.from_dict(raw_trigger)
        except (TypeError, ValueError) as exc:
            raise P13ShadowRunError(
                f"P13 update evidence for {case_id} trigger is invalid: {exc}") from exc
        if evidence_trigger.receipt_digest != trigger.receipt_digest:
            raise P13ShadowRunError(
                f"P13 update evidence for {case_id} trigger does not match report")
        anti_ref = None
        if plan.update_target != "UPDATE_NONE":
            if case_id not in anti_forgetting_receipts:
                raise P13ShadowRunError(
                    f"P13 update for {case_id} requires a file-bound "
                    "anti-forgetting witness receipt")
            witness_payload, anti_ref = _load_bound_anti_forgetting(
                anti_forgetting_receipts[case_id], case_id=case_id,
                campaign_id=campaign_id, manifest_path=manifest_path,
                forbidden_paths=forbidden_paths)
            witness = AntiForgettingWitness.from_dict(witness_payload)
            inline = evidence.get("anti_forgetting")
            if inline is not None:
                try:
                    inline_witness = AntiForgettingWitness.from_dict(inline)
                except (TypeError, ValueError) as exc:
                    raise P13ShadowRunError(
                        f"P13 inline anti-forgetting witness for {case_id} is invalid: {exc}") from exc
                if inline_witness.receipt_digest != witness.receipt_digest:
                    raise P13ShadowRunError(
                        f"P13 inline anti-forgetting witness for {case_id} disagrees with bound receipt")
            evidence["anti_forgetting"] = witness_payload
            if witness.receipt_digest not in plan.evidence_refs:
                raise P13ShadowRunError(
                    f"P13 update plan for {case_id} must witness its anti-forgetting digest")
        decoded[case_id] = (plan, evidence, anti_ref)
    return decoded


def run_p13_shadow_update(trigger_report: Path | str, manifest: Path | str,
                          *, output: Path | str) -> dict:
    """Apply all manifest plans in discarded staging and emit receipts."""
    trigger_path = Path(trigger_report).expanduser().resolve()
    manifest_path = Path(manifest).expanduser().resolve()
    report, triggers = _trigger_map(trigger_path)
    payload = _load_json(manifest_path, "P13 shadow update manifest")
    if payload.get("version") != MANIFEST_VERSION:
        raise P13ShadowRunError("P13 shadow update manifest version mismatch")
    declared_trigger_digest = _digest_text(
        payload.get("trigger_report_digest"), "trigger_report_digest")
    if declared_trigger_digest != report.get("report_digest"):
        raise P13ShadowRunError(
            "P13 shadow update manifest trigger report digest disagrees")
    campaign_id = _text(payload.get("campaign_id"), "manifest campaign_id")
    if campaign_id != report["campaign_id"]:
        raise P13ShadowRunError("manifest campaign_id does not match trigger report")
    source_path = Path(_text(payload.get("source_db"), "source_db")).expanduser()
    if not source_path.is_absolute():
        source_path = manifest_path.parent / source_path
    source_path = source_path.resolve()
    declared_source_digest = _digest_text(
        payload.get("source_db_sha256"), "source_db_sha256")
    output_path = Path(output).expanduser().resolve()
    if output_path in {source_path, trigger_path, manifest_path}:
        raise P13ShadowRunError(
            "P13 shadow update output must be separate from all input files")
    updates = payload.get("updates")
    if not isinstance(updates, Mapping) or set(updates) != set(triggers):
        raise P13ShadowRunError(
            "P13 shadow update manifest must cover exactly all trigger cases")
    decoded_updates = _decode_updates(
        updates, triggers, campaign_id,
        anti_forgetting_receipts=payload.get("anti_forgetting_receipts"),
        manifest_path=manifest_path,
        forbidden_paths={manifest_path, output_path, source_path})
    source_sha_before = _sha256(source_path)
    if declared_source_digest != source_sha_before:
        raise P13ShadowRunError(
            "P13 shadow update manifest source DB digest disagrees")
    conn = _open_read_only(source_path)
    receipts: dict[str, AppliedShadowUpdateReceipt] = {}
    try:
        for case_id in sorted(triggers):
            plan, evidence, _anti_ref = decoded_updates[case_id]
            try:
                receipt = apply_localized_update_shadow(plan, conn, evidence)
            except (ShadowUpdateError, TypeError, ValueError) as exc:
                raise P13ShadowRunError(
                    f"P13 shadow update for {case_id} was rejected: {exc}") from exc
            if receipt.campaign_id != campaign_id:
                raise P13ShadowRunError(
                    f"P13 shadow receipt for {case_id} campaign_id disagrees")
            receipts[case_id] = receipt
    finally:
        conn.close()
    source_sha_after = _sha256(source_path)
    if source_sha_after != source_sha_before:
        raise P13ShadowRunError("source SQLite file changed during shadow update")
    output_payload = {
        "version": REPORT_VERSION,
        "trigger_report": str(trigger_path),
        "trigger_report_sha256": _sha256(trigger_path),
        "trigger_report_digest": report.get("report_digest"),
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "manifest_digest": _digest(payload),
        "source_db": str(source_path),
        "source_db_sha256_expected": declared_source_digest,
        "source_db_sha256_before": source_sha_before,
        "source_db_sha256_after": source_sha_after,
        "campaign_id": campaign_id,
        "receipt_count": len(receipts),
        "receipts": {
            case_id: {**receipt.to_dict(), "receipt_digest": receipt.receipt_digest}
            for case_id, receipt in sorted(receipts.items())
        },
        "anti_forgetting_receipts": {
            case_id: anti_ref for case_id, (_plan, _evidence, anti_ref)
            in sorted(decoded_updates.items()) if anti_ref is not None
        },
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "production_integration": "not_attempted",
        "staging_discarded": True,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_payload, indent=2, sort_keys=True) + "\n")
    return output_payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trigger-report", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run_p13_shadow_update(
            args.trigger_report, args.manifest, output=args.output)
    except (OSError, P13ShadowRunError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "campaign_id": report["campaign_id"],
        "receipt_count": report["receipt_count"],
        "canonical_memory_mutation": report["canonical_memory_mutation"],
        "staging_discarded": report["staging_discarded"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
