"""Independent, append-only Parametric shadow campaign records.

The shadow proposal API is deliberately small and read-only.  This module is
the experiment boundary around it: receipts and observed outcomes live in an
external JSONL log, are chained and content addressed, and are joined only
after execution.  Nothing in this module opens a writable TEHM database or
calls capture, lifecycle, or activation code.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping

from tehm.ids import stable_dumps
from tehm.physical.effects import PHYSICAL_METRICS, extract_deltas
from tehm.parametric.shadow import proposal_digest


RECEIPT_VERSION = "parametric-shadow-receipt-v1"
OUTCOME_VERSION = "parametric-shadow-outcome-v1"
JOINED_VERSION = "parametric-shadow-joined-v1"
LOG_VERSION = "parametric-shadow-log-v1"
GENESIS = "GENESIS"
COUNT_KEYS = ("tehm_states", "tehm_transitions", "tehm_episodes",
              "tehm_views", "tehm_rules", "tehm_physical_effects")


class ShadowCampaignError(ValueError):
    """Fail-closed input, integrity, or join error."""


def validate_observation_gate(report: Mapping, prospective_manifest: Mapping) -> dict:
    """Check the preregistered observation gates before a decision round.

    A missing metric is a gate failure.  This is intentionally stricter than
    the descriptive report: decision candidates must not be prepared from an
    observation cohort with zero proposal coverage or incomplete oracle data.
    """
    if not isinstance(report, Mapping):
        raise ShadowCampaignError("observation report must be a mapping")
    metrics = report.get("metrics", report)
    if not isinstance(metrics, Mapping):
        raise ShadowCampaignError("observation report metrics must be a mapping")
    thresholds = prospective_manifest.get("pre_registered_metrics") or {}
    gate = prospective_manifest.get("decision_gate")
    if not isinstance(gate, Mapping):
        raise ShadowCampaignError("prospective manifest has no decision_gate")
    failures = []

    def number(key: str):
        value = metrics.get(key)
        if isinstance(value, bool):
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if value == value else None

    def require_at_least(metric: str, observed, threshold):
        if observed is None or observed < float(threshold):
            failures.append({"metric": metric, "observed": observed,
                             "required": float(threshold), "comparison": ">="})

    require_at_least("proposal_coverage", number("proposal_coverage"),
                     gate["min_observation_proposal_coverage"])
    require_at_least("outcome_coverage", number("outcome_coverage"),
                     gate["min_observation_outcome_coverage"])
    obligation = number("obligation_coverage_min")
    require_at_least("obligation_coverage_min", obligation,
                     gate["min_observation_obligation_coverage"])

    harmful = number("harmful_outcome_rate")
    max_harmful = float(thresholds["max_harmful_rate"])
    if harmful is None or harmful > max_harmful:
        failures.append({"metric": "harmful_outcome_rate", "observed": harmful,
                         "required": max_harmful, "comparison": "<="})
    distance = (metrics.get("ood_distance") or {}).get("max")
    hard_ood = float(thresholds["hard_ood_ceiling"])
    distance = float(distance) if distance is not None else None
    if distance is None or distance > hard_ood:
        failures.append({"metric": "ood_distance.max", "observed": distance,
                         "required": hard_ood, "comparison": "<="})

    required_interval = float(thresholds["min_interval_coverage"])
    min_evaluated = int(gate["min_metric_evaluations"])
    physical = metrics.get("physical_metrics") or {}
    for name in gate["required_physical_metrics"]:
        row = physical.get(name) or {}
        evaluated = row.get("evaluated")
        coverage = row.get("interval_coverage")
        if (not isinstance(evaluated, int) or evaluated < min_evaluated or
                coverage is None or float(coverage) < required_interval):
            failures.append({"metric": f"physical_metrics.{name}",
                             "observed": {"evaluated": evaluated,
                                           "interval_coverage": coverage},
                             "required": {"evaluated": min_evaluated,
                                          "interval_coverage": required_interval},
                             "comparison": "both"})
    return {
        "version": "parametric-shadow-decision-gate-v1",
        "passed": not failures,
        "failures": failures,
        "thresholds": {
            "min_observation_proposal_coverage": float(gate["min_observation_proposal_coverage"]),
            "min_observation_outcome_coverage": float(gate["min_observation_outcome_coverage"]),
            "min_observation_obligation_coverage": float(gate["min_observation_obligation_coverage"]),
            "min_interval_coverage": required_interval,
            "max_harmful_rate": max_harmful,
            "hard_ood_ceiling": hard_ood,
            "required_physical_metrics": sorted(gate["required_physical_metrics"]),
            "min_metric_evaluations": min_evaluated,
        },
    }


def build_observation_gate_audit(*, joined: Iterable[Mapping], join_report: Mapping,
                                 shadow_report: Mapping,
                                 prospective_manifest: Mapping) -> dict:
    """Create a deterministic external risk/quarantine record for observation.

    This is intentionally downstream of the shadow join.  It explains a failed
    observation gate at case/metric granularity, but it never creates an
    evolution event or writes canonical memory.  A failed gate makes the
    policy non-reusable for the next decision round until a new, independently
    preregistered observation is produced.
    """
    rows = [dict(row) for row in joined]
    if not isinstance(join_report, Mapping):
        raise ShadowCampaignError("join report must be a mapping")
    if not isinstance(shadow_report, Mapping):
        raise ShadowCampaignError("shadow report must be a mapping")
    if not isinstance(prospective_manifest, Mapping):
        raise ShadowCampaignError("prospective manifest must be a mapping")
    metrics = shadow_report.get("metrics", shadow_report)
    if not isinstance(metrics, Mapping):
        raise ShadowCampaignError("shadow report metrics must be a mapping")
    gate = validate_observation_gate(shadow_report, prospective_manifest)

    harmful_findings = []
    interval_findings = []
    for row in rows:
        proposal = row.get("proposal") or {}
        if proposal.get("abstained", True):
            continue
        prediction = proposal.get("prediction") or {}
        means = prediction.get("mean_deltas") or {}
        intervals = prediction.get("uncertainty_95") or {}
        observed = row.get("observed_deltas") or {}
        lineage = (row.get("provenance") or {}).get("source_lineage") or row.get("case_id")
        harmful_metrics = _harmful_metrics(observed)
        if harmful_metrics:
            harmful_findings.append({
                "case_id": row.get("case_id"),
                "source_lineage": lineage,
                "nearest_distance": _number(prediction.get("nearest_distance")),
                "metrics": harmful_metrics,
            })
        for metric in PHYSICAL_METRICS:
            predicted = _number(means.get(metric))
            actual = _number(observed.get(metric))
            interval = intervals.get(metric) or {}
            lower = _number(interval.get("lower_95"))
            upper = _number(interval.get("upper_95"))
            if predicted is None or actual is None or lower is None or upper is None:
                continue
            if not lower <= actual <= upper:
                interval_findings.append({
                    "case_id": row.get("case_id"),
                    "source_lineage": lineage,
                    "metric": metric,
                    "predicted": predicted,
                    "observed": actual,
                    "interval": {"lower_95": lower, "upper_95": upper},
                    "nearest_distance": _number(prediction.get("nearest_distance")),
                })

    failure_classes = []
    for failure in gate["failures"]:
        metric = str(failure.get("metric"))
        if metric == "harmful_outcome_rate":
            category = "HARMFUL_OUTCOME"
        elif metric.startswith("physical_metrics."):
            category = "INTERVAL_COVERAGE"
        elif metric == "ood_distance.max":
            category = "OOD_DISTANCE"
        else:
            category = "OBSERVATION_COVERAGE"
        failure_classes.append({"category": category, "metric": metric})

    source_lineages = sorted({
        str((row.get("provenance") or {}).get("source_lineage"))
        for row in rows
        if (row.get("provenance") or {}).get("source_lineage") is not None
    })
    policy_digests = sorted({
        str((row.get("provenance") or {}).get("policy_digest"))
        for row in rows
        if (row.get("provenance") or {}).get("policy_digest") is not None
    })
    action_digests = sorted({
        str(row.get("proposal", {}).get("action_digest"))
        for row in rows
        if row.get("proposal", {}).get("action_digest") is not None
    })
    body = {
        "version": "parametric-shadow-observation-gate-audit-v1",
        "disposition": "PROCEED_TO_DECISION" if gate["passed"] else "QUARANTINE",
        "shadow_policy_reusable": bool(gate["passed"]),
        "parametric_view_status": "NOT_IMPLEMENTED",
        "shadow_only": True,
        "promotion_eligible": False,
        "canonical_memory_mutation": "none",
        "gate": gate,
        "failure_classes": failure_classes,
        "risk_findings": {
            "harmful_outcomes": harmful_findings,
            "interval_misses": interval_findings,
        },
        "evidence": {
            "receipt_count": join_report.get("receipt_count"),
            "outcome_count": join_report.get("outcome_count"),
            "joined_count": join_report.get("joined_count"),
            "source_lineages": source_lineages,
            "policy_digests": policy_digests,
            "action_digests": action_digests,
            "metrics_digest": digest(metrics),
        },
    }
    body["audit_digest"] = digest(body)
    return body


def digest(value: object) -> str:
    return hashlib.sha256(stable_dumps(value).encode("utf-8")).hexdigest()


def action_digest(action: Mapping) -> str:
    if not isinstance(action, Mapping) or not action:
        raise ShadowCampaignError("action must be a non-empty mapping")
    return digest(dict(action))


def canonical_counts(conn) -> dict[str, int]:
    """Return the canonical counters used by the zero-mutation guard."""
    result = {}
    for table in COUNT_KEYS:
        try:
            row = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()
        except Exception as exc:  # pragma: no cover - schema failure is explicit
            raise ShadowCampaignError(f"cannot count canonical table {table}") from exc
        result[table] = int(row["n"] if row is not None else 0)
    return result


def assert_counts_unchanged(before: Mapping, after: Mapping) -> None:
    before_norm = _normalise_counts(before)
    after_norm = _normalise_counts(after)
    if before_norm != after_norm:
        changed = {key: (before_norm[key], after_norm[key])
                   for key in COUNT_KEYS if before_norm[key] != after_norm[key]}
        raise ShadowCampaignError(f"canonical memory mutation detected: {changed}")


def build_receipt(*, case_id: str, proposal: Mapping, target_graph_context_digest: str,
                  action: Mapping, policy_digest: str, bundle_digest: str,
                  manifest_digest: str, canonical_counts_before: Mapping,
                  candidate_rank: int | None = None,
                  mode: str = "observation", source_lineage: str | None = None,
                  memory_snapshot_digest: str | None = None) -> dict:
    """Create one deterministic receipt without adding a volatile timestamp."""
    if not isinstance(case_id, str) or not case_id.strip():
        raise ShadowCampaignError("case_id must be a non-empty string")
    if not isinstance(proposal, Mapping):
        raise ShadowCampaignError("proposal must be a mapping")
    if proposal.get("parametric_view_status") != "NOT_IMPLEMENTED":
        raise ShadowCampaignError("shadow receipt cannot represent a materialized Parametric View")
    if proposal.get("parametric_shadow_status") != "SHADOW_ONLY":
        raise ShadowCampaignError("proposal is not shadow-only")
    if proposal.get("canonical_memory_mutation") != "none":
        raise ShadowCampaignError("proposal is not read-only")
    if proposal.get("promotion_eligible") is not False:
        raise ShadowCampaignError("shadow proposal must not be promotion eligible")
    if not isinstance(target_graph_context_digest, str) or not target_graph_context_digest:
        raise ShadowCampaignError("target graph context digest is required")
    if not isinstance(policy_digest, str) or not policy_digest:
        raise ShadowCampaignError("policy digest is required")
    if not isinstance(bundle_digest, str) or not bundle_digest:
        raise ShadowCampaignError("bundle digest is required")
    if not isinstance(manifest_digest, str) or not manifest_digest:
        raise ShadowCampaignError("manifest digest is required")
    if memory_snapshot_digest is not None and (
            not isinstance(memory_snapshot_digest, str) or not memory_snapshot_digest):
        raise ShadowCampaignError("memory snapshot digest must be a non-empty string")
    if mode not in {"observation", "decision"}:
        raise ShadowCampaignError("mode must be observation or decision")
    if candidate_rank is not None and (not isinstance(candidate_rank, int) or candidate_rank < 1):
        raise ShadowCampaignError("candidate_rank must be a positive integer")
    counts = _normalise_counts(canonical_counts_before)
    a_digest = action_digest(action)
    p_digest = proposal_digest(dict(proposal))
    supplied_policy = (proposal.get("provenance") or {}).get("policy_digest")
    if supplied_policy is not None and str(supplied_policy) != policy_digest:
        raise ShadowCampaignError("policy digest does not match proposal provenance")
    supplied_bundle = (proposal.get("provenance") or {}).get("bundle_digest")
    if supplied_bundle is not None and str(supplied_bundle) != bundle_digest:
        raise ShadowCampaignError("bundle digest does not match proposal provenance")
    supplied_manifest = (proposal.get("provenance") or {}).get("manifest_digest")
    if supplied_manifest is not None and str(supplied_manifest) != manifest_digest:
        raise ShadowCampaignError("manifest digest does not match proposal provenance")
    identity = {
        "case_id": case_id,
        "target_graph_context_digest": target_graph_context_digest,
        "action_digest": a_digest,
        "policy_digest": policy_digest,
        "bundle_digest": bundle_digest,
        "manifest_digest": manifest_digest,
        "proposal_digest": p_digest,
        "mode": mode,
    }
    if memory_snapshot_digest is not None:
        identity["memory_snapshot_digest"] = memory_snapshot_digest
    join_key = digest(identity)
    receipt_id = f"shadow_receipt_{join_key[:24]}"
    provenance = {
        "policy_digest": policy_digest,
        "bundle_digest": bundle_digest,
        "manifest_digest": manifest_digest,
        "source_lineage": source_lineage,
        "target_graph_context_digest": target_graph_context_digest,
    }
    if memory_snapshot_digest is not None:
        provenance["memory_snapshot_digest"] = memory_snapshot_digest
    return {
        "record_version": RECEIPT_VERSION,
        "record_type": "shadow_receipt",
        "receipt_id": receipt_id,
        "case_id": case_id,
        "join_key": join_key,
        "proposal_digest": p_digest,
        "target_graph_context_digest": target_graph_context_digest,
        "action": dict(action),
        "action_digest": a_digest,
        "candidate_rank": candidate_rank,
        "mode": mode,
        "provenance": provenance,
        "proposal": dict(proposal),
        "canonical_counts_before": counts,
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
    }


def build_outcome(*, receipt: Mapping, before_ppa: Mapping, after_ppa: Mapping,
                  oracle: Mapping | None = None,
                  canonical_counts_after: Mapping | None = None) -> dict:
    """Build the post-execution record; missing PPA metrics remain ``None``."""
    _require_receipt(receipt)
    if not isinstance(before_ppa, Mapping) or not isinstance(after_ppa, Mapping):
        raise ShadowCampaignError("before_ppa and after_ppa must be mappings")
    counts_before = _normalise_counts(receipt["canonical_counts_before"])
    counts_after = (_normalise_counts(canonical_counts_after)
                    if canonical_counts_after is not None else counts_before)
    unchanged = counts_before == counts_after
    observed = extract_deltas(dict(before_ppa), dict(after_ppa))
    oracle_data = dict(oracle or {})
    identity = {
        "receipt_id": receipt["receipt_id"],
        "join_key": receipt["join_key"],
        "observed_deltas": observed,
        "oracle": oracle_data,
        "canonical_counts_after": counts_after,
    }
    return {
        "record_version": OUTCOME_VERSION,
        "record_type": "shadow_outcome",
        "outcome_id": f"shadow_outcome_{digest(identity)[:24]}",
        "receipt_id": receipt["receipt_id"],
        "case_id": receipt["case_id"],
        "join_key": receipt["join_key"],
        "target_graph_context_digest": receipt["target_graph_context_digest"],
        "action_digest": receipt["action_digest"],
        "provenance": dict(receipt["provenance"]),
        "observed_deltas": observed,
        "oracle": oracle_data,
        "canonical_counts_after": counts_after,
        "canonical_memory_unchanged": unchanged,
        "status": "OBSERVED" if unchanged else "INVALID_MEMORY_MUTATION",
    }


class AppendOnlyShadowLog:
    """Hash-chained JSONL log with idempotent append and crash-tail recovery."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def read(self, *, recover_partial_tail: bool = False) -> list[dict]:
        return read_log(self.path, recover_partial_tail=recover_partial_tail)

    def append(self, event: Mapping, *, recover_partial_tail: bool = True) -> dict:
        if not isinstance(event, Mapping):
            raise ShadowCampaignError("log event must be a mapping")
        events = self.read(recover_partial_tail=recover_partial_tail)
        key = _event_key(event)
        for existing in events:
            if _event_key(existing["event"]) == key:
                if existing["event"] != dict(event):
                    raise ShadowCampaignError(f"conflicting duplicate event: {key}")
                return {"duplicate": True, "sequence": existing["sequence"],
                        "event_digest": existing["event_digest"]}
        previous = events[-1]["event_digest"] if events else GENESIS
        envelope = {
            "log_version": LOG_VERSION,
            "sequence": len(events),
            "previous_digest": previous,
            "event": dict(event),
        }
        envelope["event_digest"] = digest(envelope)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as stream:
            stream.write((stable_dumps(envelope) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        return {"duplicate": False, "sequence": envelope["sequence"],
                "event_digest": envelope["event_digest"]}


def read_log(path: Path, *, recover_partial_tail: bool = False) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    if not raw:
        return []
    complete = raw.endswith(b"\n")
    lines = raw.splitlines()
    if not complete:
        if not recover_partial_tail:
            raise ShadowCampaignError("shadow log has an incomplete final record")
        last_newline = raw.rfind(b"\n")
        path.write_bytes(raw[:last_newline + 1] if last_newline >= 0 else b"")
        lines = raw[:last_newline].splitlines() if last_newline >= 0 else []
    events = []
    previous = GENESIS
    seen = set()
    for expected_sequence, line in enumerate(lines):
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ShadowCampaignError(f"invalid shadow log JSON at sequence {expected_sequence}") from exc
        if envelope.get("log_version") != LOG_VERSION:
            raise ShadowCampaignError("unsupported shadow log version")
        if envelope.get("sequence") != expected_sequence:
            raise ShadowCampaignError("shadow log sequence discontinuity")
        if envelope.get("previous_digest") != previous:
            raise ShadowCampaignError("shadow log hash chain mismatch")
        supplied = envelope.get("event_digest")
        body = dict(envelope)
        body.pop("event_digest", None)
        if supplied != digest(body):
            raise ShadowCampaignError("shadow log event digest mismatch")
        event = envelope.get("event")
        if not isinstance(event, dict):
            raise ShadowCampaignError("shadow log event must be an object")
        key = _event_key(event)
        if key in seen:
            raise ShadowCampaignError(f"duplicate event in shadow log: {key}")
        seen.add(key)
        events.append(envelope)
        previous = supplied
    return events


def join_receipts_and_outcomes(events: Iterable[Mapping]) -> tuple[list[dict], dict]:
    receipts, outcomes = {}, {}
    for envelope in events:
        event = envelope.get("event", envelope)
        if event.get("record_type") == "shadow_receipt":
            _require_receipt(event)
            _insert_unique(receipts, event["receipt_id"], event)
        elif event.get("record_type") == "shadow_outcome":
            if not event.get("outcome_id"):
                raise ShadowCampaignError("outcome_id is required")
            _insert_unique(outcomes, event["outcome_id"], event)
        else:
            raise ShadowCampaignError(f"unsupported shadow event type: {event.get('record_type')}")
    joined, missing, invalid = [], [], []
    for receipt_id, receipt in sorted(receipts.items()):
        matches = [item for item in outcomes.values()
                   if item.get("receipt_id") == receipt_id]
        if not matches:
            missing.append(receipt_id)
            continue
        if len(matches) != 1:
            raise ShadowCampaignError(f"multiple outcomes for receipt: {receipt_id}")
        outcome = matches[0]
        if outcome.get("join_key") != receipt["join_key"]:
            raise ShadowCampaignError(f"join key mismatch for receipt: {receipt_id}")
        if outcome.get("action_digest") != receipt["action_digest"]:
            raise ShadowCampaignError(f"action digest mismatch for receipt: {receipt_id}")
        if outcome.get("target_graph_context_digest") != receipt["target_graph_context_digest"]:
            raise ShadowCampaignError(f"target graph digest mismatch for receipt: {receipt_id}")
        if outcome.get("status") != "OBSERVED":
            invalid.append(receipt_id)
            continue
        joined.append({
            "record_version": JOINED_VERSION,
            "receipt_id": receipt_id,
            "case_id": receipt["case_id"],
            "join_key": receipt["join_key"],
            "mode": receipt.get("mode", "observation"),
            "candidate_rank": receipt.get("candidate_rank"),
            "proposal": receipt["proposal"],
            "observed_deltas": outcome["observed_deltas"],
            "oracle": outcome.get("oracle") or {},
            "provenance": receipt["provenance"],
        })
    report = {
        "receipt_count": len(receipts),
        "outcome_count": len(outcomes),
        "joined_count": len(joined),
        "missing_outcomes": missing,
        "invalid_outcomes": invalid,
    }
    return joined, report


def summarise(joined: list[Mapping], *, total_receipts: int | None = None) -> dict:
    """Compute honest shadow metrics; unavailable fields remain explicit."""
    total = int(total_receipts if total_receipts is not None else len(joined))
    proposed = [x for x in joined if not (x["proposal"].get("abstained", True))]
    abstained = sum(1 for x in joined if x["proposal"].get("abstained", True))
    missing_outcomes = max(0, total - len(joined))
    abstain_reason_distribution = Counter(
        str(reason)
        for row in joined
        if row["proposal"].get("abstained", True)
        for reason in (row["proposal"].get("abstain_reasons") or ["unspecified"])
    )
    distances = sorted(
        float(distance)
        for row in joined
        for distance in [((row["proposal"].get("prediction") or {}).get("nearest_distance"))]
        if _number(distance) is not None
    )
    ood_distance = {
        "evaluated": len(distances),
        "min": min(distances) if distances else None,
        "max": max(distances) if distances else None,
        "mean": sum(distances) / len(distances) if distances else None,
    }
    metric = {}
    for name in PHYSICAL_METRICS:
        rows = []
        for row in proposed:
            pred = ((row["proposal"].get("prediction") or {}).get("mean_deltas") or {}).get(name)
            interval = (((row["proposal"].get("prediction") or {}).get("uncertainty_95") or {}).get(name) or {})
            observed = (row.get("observed_deltas") or {}).get(name)
            if _number(pred) is None or _number(observed) is None:
                continue
            lower, upper = _number(interval.get("lower_95")), _number(interval.get("upper_95"))
            rows.append({"predicted": float(pred), "observed": float(observed),
                         "interval_hit": lower is not None and upper is not None and lower <= float(observed) <= upper,
                         "sign_agreement": _sign(float(pred)) == _sign(float(observed))})
        metric[name] = {
            "evaluated": len(rows),
            "interval_coverage": (sum(x["interval_hit"] for x in rows) / len(rows)
                                   if rows else None),
            "mean_absolute_error": (sum(abs(x["predicted"] - x["observed"]) for x in rows) / len(rows)
                                     if rows else None),
            "sign_agreement": (sum(x["sign_agreement"] for x in rows) / len(rows)
                                if rows else None),
        }
    harmful = [_harmful_outcome(row) for row in proposed]
    risk = []
    distances = sorted({
        float(((row["proposal"].get("prediction") or {}).get("nearest_distance")))
        for row in proposed
        if _number(((row["proposal"].get("prediction") or {}).get("nearest_distance"))) is not None
    })
    for threshold in distances:
        selected = [row for row in proposed
                    if _number(((row["proposal"].get("prediction") or {}).get("nearest_distance"))) is not None
                    and float(row["proposal"]["prediction"]["nearest_distance"]) <= threshold]
        risk.append({"max_distance": threshold, "coverage": len(selected) / total if total else 0.0,
                     "harmful_rate": sum(_harmful_outcome(row) for row in selected) / len(selected)
                     if selected else None})
    obligation = [_number((row.get("oracle") or {}).get("obligation_coverage"))
                  for row in joined]
    obligation = [x for x in obligation if x is not None]
    return {
        "version": "parametric-shadow-metrics-v1",
        "receipt_count": total,
        "joined_count": len(joined),
        "missing_outcome_count": missing_outcomes,
        "proposed_count": len(proposed),
        "abstained_count": abstained,
        "outcome_coverage": len(joined) / total if total else 0.0,
        "proposal_coverage": len(proposed) / total if total else 0.0,
        "proposal_coverage_given_outcome": len(proposed) / len(joined) if joined else None,
        "abstain_reason_distribution": dict(sorted(abstain_reason_distribution.items())),
        "ood_distance": ood_distance,
        "harmful_outcome_count": sum(harmful),
        "harmful_outcome_rate": sum(harmful) / len(proposed) if proposed else None,
        "obligation_coverage_min": min(obligation) if obligation else None,
        "obligation_coverage_mean": sum(obligation) / len(obligation) if obligation else None,
        "physical_metrics": metric,
        "selective_risk_coverage": risk,
        "ranking": _ranking_metrics(joined),
    }


def _ranking_metrics(joined: list[Mapping]) -> dict:
    groups = {}
    for row in joined:
        if row.get("candidate_rank") is None:
            continue
        utility = _number((row.get("oracle") or {}).get("oracle_utility"))
        if utility is None:
            continue
        groups.setdefault(row["case_id"], []).append((int(row["candidate_rank"]), utility))
    if not groups:
        return {"status": "not_available", "reason": "candidate_rank_and_oracle_utility_required"}
    regrets = []
    correct = 0
    for rows in groups.values():
        rows.sort()
        chosen = rows[0][1]
        best = max(value for _, value in rows)
        regrets.append(best - chosen)
        correct += int(chosen == best)
    return {"status": "available", "candidate_groups": len(groups),
            "top1_oracle_rate": correct / len(groups),
            "mean_oracle_regret": sum(regrets) / len(regrets)}


def _harmful_outcome(row: Mapping) -> bool:
    return bool(_harmful_metrics(row.get("observed_deltas") or {}))


def _harmful_metrics(deltas: Mapping) -> list[dict]:
    """Return the observed metrics that violate the shadow safety direction."""
    findings = []
    if not isinstance(deltas, Mapping):
        return findings
    for metric, value in deltas.items():
        if _number(value) is None:
            continue
        value = float(value)
        harmful = ((metric in {"wns_ns", "tns_ns"} and value < 0) or
                    (metric in {"area_um2", "power_w", "congestion", "drc_violations"}
                     and value > 0))
        if harmful:
            findings.append({"metric": str(metric), "observed": value})
    return sorted(findings, key=lambda item: item["metric"])


def _require_receipt(receipt: Mapping) -> None:
    if receipt.get("record_version") != RECEIPT_VERSION or receipt.get("record_type") != "shadow_receipt":
        raise ShadowCampaignError("unsupported or malformed shadow receipt")
    if receipt.get("canonical_memory_mutation") != "none" or receipt.get("promotion_eligible") is not False:
        raise ShadowCampaignError("receipt violates shadow-only boundary")


def _insert_unique(target: dict, key: str, value: Mapping) -> None:
    if key in target and target[key] != dict(value):
        raise ShadowCampaignError(f"conflicting duplicate record: {key}")
    target[key] = dict(value)


def _event_key(event: Mapping) -> str:
    if event.get("record_type") == "shadow_outcome":
        key = event.get("outcome_id")
    else:
        key = event.get("receipt_id") or event.get("outcome_id")
    if not key:
        raise ShadowCampaignError("shadow event has no stable record id")
    return str(key)


def _normalise_counts(counts: Mapping) -> dict[str, int]:
    if not isinstance(counts, Mapping):
        raise ShadowCampaignError("canonical counts must be a mapping")
    result = {}
    for key in COUNT_KEYS:
        value = counts.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ShadowCampaignError(f"canonical count {key} must be a non-negative integer")
        result[key] = value
    return result


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float("inf") else None


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0
