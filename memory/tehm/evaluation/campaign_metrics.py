"""Design-doc section 13 funnel metrics over a frozen TEHM campaign."""
from __future__ import annotations

import json
import sqlite3

from contracts import RepairContext
from tehm import db as tehm_db
from tehm.activation.binding import bind_rule
from tehm.activation.instantiate import instantiate_rewrite
from tehm.retrieval.index import build_index
from tehm.retrieval.pipeline import retrieve
from tehm.retrieval.result import APPLICABLE


def evaluate_campaign(conn: sqlite3.Connection, cases: list[dict], *, limit: int = 10) -> dict:
    """Compute RCret/RCexec/AY/BSR/IVR/RU/HAR/OC/TE with raw numerators."""
    index = build_index(conn, lifecycle_statuses=frozenset({"promoted"}))
    case_rows, retrieved_n = [], 0
    applicable_n = bound_n = executable_n = 0
    ret_covered = exec_covered = 0
    for case in cases:
        ctx = RepairContext(project_dir=case.get("project_path"),
                            design_id=case.get("design_id"),
                            platform=case.get("platform"),
                            check=case.get("check", "route"),
                            reports=case.get("reports") or {},
                            cfg=case.get("cfg") or {},
                            compatibility_profile=case.get("compatibility_profile"),
                            symptom_signature=case.get("symptom_signature") or {})
        receipt = retrieve(conn, ctx, limit=limit)
        retrieved_n += receipt.candidates_retrieved
        ret_covered += int(receipt.candidates_retrieved > 0)
        row_app = row_bound = row_exec = 0
        for result in receipt.results:
            if result.applicability_status != APPLICABLE:
                continue
            row_app += 1
            rule = index.get(result.rule_id)
            if rule is None:
                continue
            binding = bind_rule(rule, ctx, provided_binding=case.get("binding") or {})
            if binding.status != "BOUND":
                continue
            row_bound += 1
            action = instantiate_rewrite(rule, binding, ctx)
            # Flow/signoff actions expose config_edits; typed RTL actions are
            # executable when their parser-backed domain has a concrete
            # payload.  Keeping this domain-neutral avoids reporting a false
            # zero IVR for the Phase-10 RTL closed loop.
            payload = action.get("payload") or {}
            if payload.get("config_edits") or (
                    str(action.get("domain") or "").startswith("rtl.") and
                    any(value is not None for value in payload.values())):
                row_exec += 1
        applicable_n += row_app
        bound_n += row_bound
        executable_n += row_exec
        exec_covered += int(row_exec > 0)
        case_rows.append({"case_id": case.get("case_id") or case.get("design_id"),
                          "retrieved": receipt.candidates_retrieved,
                          "applicable": row_app, "bound": row_bound,
                          "executable": row_exec,
                          "retrieval_latency_ms": receipt.latency_ms})

    trial_rows = conn.execute(
        "SELECT trial_uuid, rule_id, metrics_json FROM tehm_trials").fetchall()
    infrastructure_trials = {
        row["trial_uuid"] for row in trial_rows
        if _trial_infrastructure_failure(tehm_db.read_json(row["metrics_json"]))}
    all_activations = conn.execute(
        "SELECT rule_id, outcome, created_regressions_json, obligation_coverage, "
        "obligation_transfer_json, rollback_receipt_json, trial_uuid "
        "FROM tehm_activations WHERE trial_uuid IS NOT NULL").fetchall()
    activations = [row for row in all_activations
                   if row["trial_uuid"] not in infrastructure_trials]
    executed = len(activations)
    positive = sum(1 for r in activations if r["outcome"] == "PASS")
    harmful = sum(1 for r in activations
                  if tehm_db.read_json(r["created_regressions_json"], default=[]))
    checked = required = 0
    rollback_verified = 0
    for row in activations:
        transfer = tehm_db.read_json(row["obligation_transfer_json"])
        results = transfer.get("results") if isinstance(transfer, dict) else None
        if isinstance(results, list):
            required += len(results)
            checked += sum(1 for item in results
                           if item.get("status") in {"BOUND", "PASS"})
        elif row["obligation_coverage"] is not None:
            required += 1
            checked += float(row["obligation_coverage"])
        receipt = tehm_db.read_json(row["rollback_receipt_json"])
        rollback_verified += int(receipt.get("verified") is True)

    cross_total = cross_success = 0
    source_lineages = {r["rule_id"]: {x[0] for x in conn.execute(
        "SELECT lineage_id FROM tehm_rule_sources WHERE rule_id=?", (r["rule_id"],))
        if x[0]} for r in activations}
    for trial in trial_rows:
        metrics = tehm_db.read_json(trial["metrics_json"])
        if _trial_infrastructure_failure(metrics):
            continue
        sources = source_lineages.get(trial["rule_id"], set())
        for pair in metrics.get("pairs") or []:
            lineage = pair.get("subject_lineage") or pair.get("subject")
            if lineage and lineage not in sources:
                cross_total += 1
                cross_success += int((pair.get("arm_b") or {}).get("success") is True)
    registry_verified = sum(1 for r in trial_rows if
                            (tehm_db.read_json(r["metrics_json"]).get("registry_authority") or {})
                            .get("verified") is True)

    n_cases = len(cases)
    metrics = {
        "RC_ret": _ratio(ret_covered, n_cases),
        "RC_exec": _ratio(exec_covered, n_cases),
        "AY": _ratio(applicable_n, retrieved_n),
        "BSR": _ratio(bound_n, applicable_n),
        "IVR": _ratio(executable_n, bound_n),
        "RU": _ratio(positive, executed),
        "HAR": _ratio(harmful, executed),
        "OC": _ratio(checked, required),
        "TE": _ratio(cross_success, cross_total),
    }
    counts = {"cases": n_cases, "retrieval_covered": ret_covered,
              "executable_covered": exec_covered, "retrieved": retrieved_n,
              "applicable": applicable_n, "bound": bound_n,
              "well_formed_executable": executable_n,
              "executed_activations": executed, "positive_activations": positive,
              "harmful_activations": harmful, "checked_obligations": checked,
              "required_obligations": required,
              "cross_lineage_activations": cross_total,
              "cross_lineage_successes": cross_success,
              "infrastructure_trials_excluded": len(infrastructure_trials),
              "infrastructure_activations_excluded":
                  len(all_activations) - len(activations)}
    return {"metrics": metrics, "counts": counts, "cases": case_rows,
            "rollback": {"activation_verified": rollback_verified,
                         "activation_total": executed,
                         "activation_rate": _ratio(rollback_verified, executed),
                         "registry_verified": registry_verified,
                         "registry_total": len(trial_rows),
                         "registry_rate": _ratio(registry_verified, len(trial_rows))}}


def to_markdown(report: dict) -> str:
    labels = ("RC_ret", "RC_exec", "AY", "BSR", "IVR", "RU", "HAR", "OC", "TE")
    lines = ["# TEHM production campaign metrics", "",
             "| Metric | Value |", "|---|---:|"]
    for label in labels:
        value = report["metrics"].get(label)
        lines.append(f"| {label} | {'N/A' if value is None else f'{value:.4f}'} |")
    lines += ["", "## Raw counts", "", "```json",
              json.dumps(report["counts"], indent=2, sort_keys=True), "```", "",
              "## Rollback stability", "", "```json",
              json.dumps(report["rollback"], indent=2, sort_keys=True), "```", ""]
    return "\n".join(lines)


def _ratio(numerator, denominator):
    return None if not denominator else float(numerator) / float(denominator)


def _trial_infrastructure_failure(metrics: dict) -> bool:
    if metrics.get("infrastructure_failure"):
        return True
    # Backward-compatible classification for preserved v0.1 evidence recorded
    # before the explicit field existed.  Only unambiguous environment strings
    # are excluded; ordinary synth/place/route failures remain in quality data.
    text = json.dumps(metrics.get("pairs") or []).lower()
    return any(signature in text for signature in (
        "platform variable net set", "platform variable not set",
        "project_inputs_missing", "r2g_inputs_missing",
        "read-only file system", "permission denied"))
