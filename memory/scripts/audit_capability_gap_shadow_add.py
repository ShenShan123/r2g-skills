"""Independent P13 materialization replay; no producer imports or hardware credit.

Replay the actual frozen core ADD against its immutable source, then compare
the complete receipt and full SQL state, not success labels. Original source
IO is required here; this is not the relocated P16 oracle audit.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

from tehm import db
from tehm.ids import stable_dumps
from tehm.evolution import LocalizedUpdatePlan, apply_localized_update_shadow, ShadowUpdateError
from tehm.evolution.anti_forgetting import raw_evidence_digest
from tehm.evolution.gap_source import verify_capability_gap_source
from tehm.capability.delta import memory_delta_from_shadow_update


def digest(value):
    return byte_digest(stable_dumps(value).encode())


def byte_digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def reference(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": byte_digest(path.read_bytes())}


def verify_reference(ref):
    if reference(ref["path"])["sha256"] != ref["sha256"]:
        raise ValueError("input reference bytes changed")


def load_report(path):
    value = json.loads(Path(path).read_text())
    if value.get("report_digest") != digest({k: v for k, v in value.items() if k != "report_digest"}):
        raise ValueError("input report content digest mismatch")
    return value


def logical(conn):
    return digest("\n".join(conn.iterdump()))


def replay(freeze_path, report_path, output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("independent audit output must be absent")
    freeze, warm = load_report(freeze_path), load_report(report_path)
    if freeze["version"] != "r3-core-gap-shadow-add-prospective-freeze-v1":
        raise ValueError("unexpected prospective input contract")
    if warm["prospective_freeze"] != reference(freeze_path):
        raise ValueError("warm result did not consume this prospective freeze")
    if warm["source_snapshot"] != freeze["source_snapshot"]:
        raise ValueError("source origin mismatch")
    refs = [reference(freeze_path), reference(report_path), reference(__file__),
            freeze["source_snapshot"], warm["shadow_snapshot"],
            *freeze["current_runtime_binding"]["files"]]
    for item in freeze["origin_resolved_inputs"]:
        # Archived aliases preserve old input identity, never silently rebind
        # an old digest to a changed current file.
        if item["original_ref"]["sha256"] != item["verified_ref"]["sha256"]:
            raise ValueError("resolved origin differs from frozen input bytes")
        refs.append(item["verified_ref"])
    for item in refs:
        verify_reference(item)
    source = db.connect_read_only(freeze["source_snapshot"]["path"])
    prior_clock = db.now_local
    try:
        before, raw_before = logical(source), raw_evidence_digest(source)
        if before != freeze["source_snapshot"]["logical_digest"]:
            raise ValueError("actual source SQL differs from frozen Mt")
        witness = verify_capability_gap_source(source, freeze["source_witness"])
        if not witness.get("verified") or not witness.get("eligible"):
            raise ValueError("independent source reason DB replay rejected")
        plan = LocalizedUpdatePlan.from_dict(freeze["plan"])
        evidence = {"transition_ids": freeze["transition_ids"], "knowledge_path_id": freeze["knowledge_path_id"],
            "capability_gap_source": freeze["source_witness"], "asset": freeze["asset_proposal"],
            "anti_forgetting": freeze["anti_forgetting"], "scope": freeze["scope"],
            "created_at": freeze["materialized_at"]}
        # This clock reproduces derived projection metadata only. No oracle
        # timestamps or new independent sample identities are synthesized.
        db.now_local = lambda: freeze["materialized_at"]
        captured = []
        receipt = apply_localized_update_shadow(plan, source, evidence, staging_artifact_sink=captured.append)
        if receipt.to_dict() != warm["applied_shadow_update"]:
            raise ValueError("complete cold P13 receipt differs from actual warm execution")
        delta = memory_delta_from_shadow_update(receipt)
        if not delta.eligible or delta.to_dict() != warm["memory_delta"]:
            raise ValueError("typed P13 to C1 seam did not independently replay")
        if len(captured) != 1 or byte_digest(captured[0]) != warm["shadow_snapshot"]["sha256"]:
            raise ValueError("cold serialized artifact differs from warm file")
        cold = sqlite3.connect(":memory:")
        cold.row_factory = sqlite3.Row
        try:
            cold.deserialize(captured[0])
            if cold.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("cold snapshot integrity failed")
            if logical(cold) != warm["shadow_snapshot"]["logical_digest"]:
                raise ValueError("cold full SQL state differs from actual warm file")
            if raw_evidence_digest(cold) != raw_before:
                raise ValueError("canonical raw rows changed")
        finally:
            cold.close()
        negatives = {}
        for kind in ("missing_anti_forgetting", "missing_path", "asset_payload_tamper",
                     "foreign_campaign", "nonlearner", "source_digest_relabel"):
            candidate, altered = plan, copy.deepcopy(evidence)
            if kind == "missing_anti_forgetting": altered.pop("anti_forgetting")
            elif kind == "missing_path": altered["knowledge_path_id"] = "missing-causal-path"
            elif kind == "asset_payload_tamper":
                altered["asset"]["definition"]["action"]["payload"]["add_condition"] = "unverified_input"
            elif kind == "foreign_campaign": candidate = replace(plan, campaign_id="foreign-campaign")
            elif kind == "nonlearner":
                # The typed plan itself rejects this before the executor.
                try:
                    replace(plan, learner_eligible=False)
                except ValueError as exc:
                    if "audit-only evidence" not in str(exc):
                        raise
                    negatives[kind] = True
                    continue
                raise ValueError("nonlearner mutating plan was constructible")
            else:
                altered["capability_gap_source"].pop("receipt_digest", None)
                altered["capability_gap_source"].pop("receipt_id", None)
                altered["capability_gap_source"]["source_memory_digest"] = "sha256:" + "0" * 64
            try:
                apply_localized_update_shadow(candidate, source, altered)
            except (ShadowUpdateError, ValueError):
                negatives[kind] = True
            else:
                raise ValueError("negative materialization was accepted: " + kind)
        if logical(source) != before or raw_evidence_digest(source) != raw_before:
            raise ValueError("independent replay changed source state")
        for item in refs:
            verify_reference(item)
        result = {"version": "r3-independent-core-gap-shadow-add-replay-v1", "status": "PASS",
            "inputs": refs, "full_receipt_equal": True, "serialized_artifact_byte_equal": True,
            "full_sql_state_equal": True, "source_reason_db_replayed": True,
            "memory_delta_equal": True, "source_full_state_unchanged": True,
            "negative_rejections": negatives, "core_receipt_digest": receipt.receipt_digest,
            "new_hardware_measurements": 0, "provider_calls": 0, "learner_ingestion": False,
            "fresh_post_add_hardware_trials_established": False, "production_authority_established": False}
        result["report_digest"] = digest(result)
        # Terminal audit artifacts never overwrite previous failed/successful
        # generations. Only this new external result is persisted.
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
        return result
    finally:
        db.now_local = prior_clock
        source.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = replay(args.freeze, args.report, args.output)
    print(json.dumps({"status": result["status"], "report_digest": result["report_digest"],
                      "negative_rejections": result["negative_rejections"]}), flush=True)


if __name__ == "__main__":
    main()
