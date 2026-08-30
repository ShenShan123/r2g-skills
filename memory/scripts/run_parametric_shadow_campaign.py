#!/usr/bin/env python3
"""Run the independent Parametric shadow observation/join/report pipeline.

The campaign intentionally has no action executor.  ``prepare`` emits
provenance-bound receipts from a frozen, read-only TEHM snapshot; an external
fixed-policy runner produces the JSONL outcomes; ``outcomes`` appends those
records; ``join`` and ``report`` evaluate them offline.  This makes the first
prospective cohort observational and prevents shadow predictions from changing
the action stream or feeding canonical memory.

Case JSONL format::

    {"case_id":"...", "family":"DENSITY_RELIEF",
     "graph_context": {...}, "action": {...},
     "calibration_policy": {...}, "target_graph_context_digest":"...",
     "candidate_rank": 1, "source_lineage":"future:..."}

Decision rows may instead carry ``candidate_actions``.  The prepare phase
expands those into one receipt per candidate while retaining the manifest's
base ``case_id`` and assigning deterministic ``candidate_rank`` values.  When
policies are calibrated per exact action signature, ``calibration_policies``
may map ``action_digest(action)`` to the corresponding policy; a missing
binding is refused rather than silently reusing another action's policy.

Outcome JSONL format::

    {"receipt_id":"shadow_receipt_...", "before_ppa": {...},
     "after_ppa": {...}, "oracle":{"obligation_coverage":1.0}}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from tehm import db as tehm_db  # noqa: E402
from tehm.parametric.shadow import build_shadow_proposal  # noqa: E402
from tehm.parametric.shadow_campaign import (  # noqa: E402
    AppendOnlyShadowLog,
    ShadowCampaignError,
    action_digest,
    assert_counts_unchanged,
    build_outcome,
    build_receipt,
    build_observation_gate_audit,
    canonical_counts,
    join_receipts_and_outcomes,
    summarise,
    validate_observation_gate,
)
try:  # direct script execution puts memory/scripts on sys.path
    from prepare_parametric_prospective_manifest import validate as validate_prospective_manifest  # noqa: E402
except ModuleNotFoundError:  # importing this runner from a test/module
    sys.path.insert(0, str(MEMORY_ROOT / "scripts"))
    from prepare_parametric_prospective_manifest import validate as validate_prospective_manifest  # noqa: E402
from tehm.physical.memory import PhysicalEffectMemory  # noqa: E402
from tehm.physical.utility_contracts import (  # noqa: E402
    action_contract_binding_reason,
    known_utility_contracts,
)
from tehm.sync import canonical_json  # noqa: E402


def _read(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowCampaignError(f"cannot read JSON input: {path}") from exc


def _jsonl(path: Path):
    try:
        lines = Path(path).read_text().splitlines()
    except OSError as exc:
        raise ShadowCampaignError(f"cannot read JSONL input: {path}") from exc
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ShadowCampaignError(f"invalid JSONL at {path}:{index}") from exc
        if not isinstance(value, dict):
            raise ShadowCampaignError(f"JSONL row must be an object at {path}:{index}")
        yield value


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def prepare(args) -> dict:
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    readiness = _read(args.readiness)
    replay = _read(args.replay_evidence)
    prospective = validate_prospective_manifest(_read(args.prospective_manifest))
    contract = None
    if prospective.get("require_utility_contract"):
        factory = known_utility_contracts().get(prospective.get("contract_id"))
        if factory is None:  # pragma: no cover - manifest validator guards this
            raise ShadowCampaignError("strict manifest utility contract is unknown")
        contract = factory()
    if prospective["source_freeze"]["bundle_digest"] != replay.get("bundle_digest"):
        raise ShadowCampaignError("prospective manifest bundle digest differs from replay evidence")
    if prospective["source_freeze"]["manifest_digest"] != replay.get("manifest_digest"):
        raise ShadowCampaignError("prospective manifest manifest digest differs from replay evidence")
    planned_cases = {row["case_id"]: row for row in prospective["cases"]}
    rows = list(_jsonl(args.cases))
    modes = {row.get("mode", planned_cases.get(row.get("case_id"), {}).get("phase"))
             for row in rows}
    if "decision" in modes:
        if args.observation_metrics is None:
            raise ShadowCampaignError(
                "decision cases require --observation-metrics from a completed observation join")
        gate = validate_observation_gate(_read(args.observation_metrics), prospective)
        _write(out / "decision_gate.json", gate)
        if not gate["passed"]:
            raise ShadowCampaignError(
                "observation decision gate failed: " +
                ", ".join(item["metric"] for item in gate["failures"]))
    log = AppendOnlyShadowLog(args.log)
    conn = tehm_db.connect_read_only(args.db.resolve())
    try:
        before = canonical_counts(conn)
        memory_snapshot_digest = _sha256_file(args.db.resolve())
        memory = PhysicalEffectMemory(conn)
        results = []
        expanded_rows = []
        for row in rows:
            actions = row.get("candidate_actions")
            if actions is None:
                expanded_rows.append(dict(row))
                continue
            if (not isinstance(actions, list) or not actions or
                    any(not isinstance(action, dict) or not action for action in actions)):
                raise ShadowCampaignError(
                    f"case {row.get('case_id')} has invalid candidate_actions")
            if row.get("action") is not None:
                # Observation producers may redundantly carry the selected
                # action alongside a one-element candidate list.  Accept only
                # the unambiguous, digest-identical form; a preselected action
                # must never hide a multi-candidate decision case.
                if (len(actions) != 1 or
                        action_digest(row["action"]) != action_digest(actions[0])):
                    raise ShadowCampaignError(
                        f"case {row.get('case_id')} cannot provide both action and candidate_actions")
                expanded = dict(row)
                expanded["candidate_rank"] = row.get("candidate_rank", 1)
                expanded_rows.append(expanded)
                continue
            for rank, action in enumerate(actions, start=1):
                expanded = dict(row)
                expanded["action"] = dict(action)
                expanded["candidate_rank"] = row.get("candidate_rank", rank)
                expanded_rows.append(expanded)
        for row in expanded_rows:
            case_id = row.get("case_id")
            planned = planned_cases.get(case_id)
            if planned is None:
                raise ShadowCampaignError(f"case is absent from prospective manifest: {case_id}")
            if row.get("source_lineage") != planned["lineage_id"]:
                raise ShadowCampaignError(f"case lineage differs from prospective manifest: {case_id}")
            if row.get("mode", planned["phase"]) != planned["phase"]:
                raise ShadowCampaignError(f"case phase differs from prospective manifest: {case_id}")
            if contract is not None:
                reason = action_contract_binding_reason(row.get("action"), contract)
                if reason:
                    raise ShadowCampaignError(
                        f"case action is not bound to utility contract: {case_id}: {reason}")
            context = row.get("graph_context")
            policy = _policy_for_action(row)
            expected_snapshot = row.get("memory_snapshot_digest")
            if expected_snapshot is not None and str(expected_snapshot) != memory_snapshot_digest:
                raise ShadowCampaignError(
                    f"memory snapshot digest mismatch for case {case_id}: "
                    f"expected {expected_snapshot}, got {memory_snapshot_digest}")
            if not isinstance(context, dict) or not isinstance(policy, dict):
                raise ShadowCampaignError(f"case {row.get('case_id')} lacks graph_context/calibration_policy")
            target_digest = row.get("target_graph_context_digest") or context.get("digest")
            proposal = build_shadow_proposal(
                memory,
                family=row.get("family"),
                graph_context=context,
                action=row.get("action"),
                calibration_policy=policy,
                readiness=readiness,
                replay_evidence=replay,
                policy_scope=row.get("policy_scope"),
                effect_key=row.get("effect_key"),
                k=row.get("k", 5),
                min_unique_contexts=row.get("min_unique_contexts", 3),
                max_distance=row.get("max_distance", 3.0),
            )
            receipt = build_receipt(
                case_id=case_id,
                proposal=proposal,
                target_graph_context_digest=target_digest,
                action=row.get("action"),
                policy_digest=proposal["provenance"]["policy_digest"],
                bundle_digest=proposal["provenance"].get("bundle_digest") or replay.get("bundle_digest"),
                manifest_digest=proposal["provenance"].get("manifest_digest") or replay.get("manifest_digest"),
                canonical_counts_before=before,
                candidate_rank=row.get("candidate_rank"),
                mode=row.get("mode", planned["phase"]),
                source_lineage=row.get("source_lineage"),
                memory_snapshot_digest=(str(expected_snapshot)
                                        if expected_snapshot is not None else None),
            )
            result = log.append(receipt)
            results.append({"case_id": case_id, "receipt_id": receipt["receipt_id"], **result})
        after = canonical_counts(conn)
        assert_counts_unchanged(before, after)
    finally:
        conn.close()
    snapshot = {"version": "parametric-shadow-snapshot-v1", "before": before,
                "after": after, "canonical_memory_unchanged": True,
                "memory_snapshot_digest": memory_snapshot_digest,
                "input_case_count": len(rows), "case_count": len(expanded_rows),
                "receipts": results}
    _write(out / "snapshot_counts.json", snapshot)
    return snapshot


def _policy_for_action(row: dict) -> dict:
    """Select a policy without allowing cross-action calibration reuse."""
    action = row.get("action")
    bindings = row.get("calibration_policies")
    if bindings is not None:
        if not isinstance(bindings, dict):
            raise ShadowCampaignError(
                f"case {row.get('case_id')} calibration_policies must be an object")
        if not isinstance(action, dict) or not action:
            raise ShadowCampaignError(
                f"case {row.get('case_id')} action is required for policy binding")
        key = action_digest(action)
        policy = bindings.get(key)
        if not isinstance(policy, dict):
            raise ShadowCampaignError(
                f"case {row.get('case_id')} has no calibration policy for action digest {key}")
        return policy
    policy = row.get("calibration_policy")
    if not isinstance(policy, dict):
        raise ShadowCampaignError(
            f"case {row.get('case_id')} lacks graph_context/calibration_policy")
    return policy


def append_outcomes(args) -> dict:
    log = AppendOnlyShadowLog(args.log)
    envelopes = log.read(recover_partial_tail=True)
    receipts = {env["event"]["receipt_id"]: env["event"] for env in envelopes
                if env["event"].get("record_type") == "shadow_receipt"}
    receipts_by_case = {}
    for receipt in receipts.values():
        receipts_by_case.setdefault(receipt.get("case_id"), []).append(receipt)
    results = []
    for row in _jsonl(args.outcomes):
        receipt_id = row.get("receipt_id")
        if receipt_id is None and row.get("case_id"):
            candidates = receipts_by_case.get(row["case_id"], [])
            if len(candidates) != 1:
                raise ShadowCampaignError(
                    "outcome case_id must resolve to exactly one receipt: "
                    + str(row["case_id"]))
            receipt_id = candidates[0]["receipt_id"]
        receipt = receipts.get(receipt_id)
        if receipt is None:
            raise ShadowCampaignError(f"outcome references unknown receipt: {receipt_id}")
        outcome = build_outcome(
            receipt=receipt,
            before_ppa=row.get("before_ppa"),
            after_ppa=row.get("after_ppa"),
            oracle=row.get("oracle"),
            canonical_counts_after=row.get("canonical_counts_after"),
        )
        results.append({"receipt_id": receipt_id, **log.append(outcome)})
    result = {"version": "parametric-shadow-outcomes-v1", "outcome_count": len(results),
              "outcomes": results}
    _write(args.out_dir / "outcome_append_report.json", result)
    return result


def join_and_report(args) -> dict:
    log = AppendOnlyShadowLog(args.log)
    events = log.read(recover_partial_tail=False)
    joined, join_report = join_receipts_and_outcomes(events)
    metrics = summarise(joined, total_receipts=join_report["receipt_count"])
    _write(args.out_dir / "joined_outcomes.json", {
        "version": "parametric-shadow-joined-bundle-v1",
        "join": join_report, "rows": joined,
    })
    _write(args.out_dir / "shadow_metrics.json", {
        "version": "parametric-shadow-report-v1",
        "join": join_report, "metrics": metrics,
    })
    result = {"join": join_report, "metrics": metrics}
    prospective_manifest = getattr(args, "prospective_manifest", None)
    if prospective_manifest is not None:
        prospective = validate_prospective_manifest(_read(prospective_manifest))
        audit = build_observation_gate_audit(
            joined=joined,
            join_report=join_report,
            shadow_report={"join": join_report, "metrics": metrics},
            prospective_manifest=prospective,
        )
        _write(args.out_dir / "observation_gate_audit.json", audit)
        result["observation_gate_audit"] = audit
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", choices=("prepare", "outcomes", "join", "all"), default="all")
    ap.add_argument("--out-dir", type=Path, default=Path("/tmp/tehm-parametric-shadow"))
    ap.add_argument("--log", type=Path, help="append-only JSONL log (defaults to OUT/shadow_events.jsonl)")
    ap.add_argument("--db", type=Path, help="frozen TEHM sqlite snapshot, required for prepare/all")
    ap.add_argument("--cases", type=Path, help="prospective case JSONL, required for prepare/all")
    ap.add_argument("--readiness", type=Path, help="parametric_readiness.json, required for prepare/all")
    ap.add_argument("--replay-evidence", type=Path, help="freeze verifier receipt JSON, required for prepare/all")
    ap.add_argument("--prospective-manifest", type=Path,
                    help="future-lineage manifest, required for prepare/all")
    ap.add_argument("--outcomes", type=Path, help="post-execution outcome JSONL, required for outcomes/all")
    ap.add_argument("--observation-metrics", type=Path,
                    help="completed observation shadow_metrics.json required before decision prepare")
    args = ap.parse_args(argv)
    args.out_dir = args.out_dir.resolve()
    args.log = (args.log or (args.out_dir / "shadow_events.jsonl")).resolve()
    if args.phase in {"prepare", "all"}:
        for name in ("db", "cases", "readiness", "replay_evidence", "prospective_manifest"):
            if getattr(args, name) is None:
                ap.error(f"--{name.replace('_', '-')} is required for {args.phase}")
        prepare(args)
    if args.phase in {"outcomes", "all"}:
        if args.outcomes is None:
            ap.error("--outcomes is required for outcomes/all")
        append_outcomes(args)
    if args.phase in {"join", "all"}:
        join_and_report(args)
    print(json.dumps({"ok": True, "phase": args.phase,
                      "log": str(args.log), "out_dir": str(args.out_dir)},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ShadowCampaignError as exc:
        print(f"shadow campaign refused: {exc}", file=sys.stderr)
        raise SystemExit(2)
