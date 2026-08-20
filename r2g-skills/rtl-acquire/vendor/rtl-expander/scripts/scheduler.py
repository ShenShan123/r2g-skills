#!/usr/bin/env python3
"""Deterministic diversity/yield/cost scheduler for RTL discovery queries."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from frontier import FrontierDB, utc_now


SCHEDULER_SCHEMA = "rtl_discovery_scheduler_v4"
SCHEDULER_WEIGHTS = {
    "family_yield": 3.0, "gold_yield": 2.5, "synthesis_valid_yield": 2.0,
    "functional_novelty": 2.5, "size_novelty": 4.0, "source_quality": 1.5,
    "design_likelihood": 1.0, "no_rtl_rate": -2.0, "duplicate_rate": -1.5,
    "acquisition_success_rate": 2.0, "revision_resolution_failure_rate": -2.5,
    "archive_too_large_rate": -1.25, "transport_extraction_failure_rate": -1.0,
    "cost": -1.0,
}
SIZE_WEIGHTS = {"TINY": 0.25, "SMALL": 0.50, "MEDIUM": 1.0, "LARGE": 2.0, "XLARGE": 3.0}
BATCH_C_SIZE_OBJECTIVE = {
    "metric": "marginal_large_xlarge_design_instance_share",
    "target": 0.08,
    "stretch_target": 0.10,
    "hard_completion_gate": False,
}
DEFAULT_GAPS = {
    "xlarge": 1.0, "large": 0.95, "multi-clock": 1.0, "noc": 1.0,
    "ddr": 1.0, "memory-controller": 1.0, "pcie": 1.0, "cache": 0.95,
    "accelerator": 0.95, "soc": 0.9, "cpu": 0.85, "vhdl": 0.75,
    "mixed-language": 0.3, "systemverilog": 0.7, "axi": 0.45, "dma": 0.5,
    "interconnect": 1.0, "crossbar": 1.0, "multicore": 1.0,
    "video": 0.9, "signal-processing": 0.9, "crypto": 0.9,
    "uart": 0.05, "fifo": 0.02, "counter": 0.01, "simple-peripheral": 0.05,
}
DEFAULT_QUERIES = [
    "large hierarchical noc systemverilog rtl", "network on chip multi-clock verilog",
    "ddr memory-controller vhdl", "pcie endpoint multi-clock systemverilog",
    "xlarge systemverilog soc", "mixed-language vhdl verilog soc",
    "large riscv cpu cache systemverilog", "complex accelerator systemverilog",
    "axi dma subsystem rtl", "ethernet packet accelerator verilog",
]
BATCH_C_QUERIES = [
    "synthesizable soc systemverilog rtl", "riscv processor core pipeline cache rtl",
    "multicore cpu systemverilog rtl", "noc network on chip systemverilog rtl",
    "axi interconnect crossbar systemverilog", "wishbone interconnect crossbar verilog",
    "memory controller ddr controller rtl", "cache controller systemverilog rtl",
    "dma controller axi rtl", "ethernet mac synthesizable rtl", "pcie endpoint rtl",
    "network processor systemverilog", "crypto accelerator synthesizable rtl",
    "dsp accelerator systemverilog rtl", "matrix accelerator verilog rtl",
    "video pipeline fpga rtl", "image processing fpga verilog",
    "signal processing vhdl fpga", "full systemverilog project synthesizable",
    "fpga soc complete rtl", "asic rtl subsystem systemverilog",
]
FRESH_DISCOVERY_QUERIES = [
    "open source synthesizable cpu core verilog", "production riscv core systemverilog",
    "complete soc rtl source systemverilog", "manycore noc router synthesizable rtl",
    "axi4 crossbar interconnect rtl source", "wishbone fabric interconnect verilog",
    "sdram ddr memory controller synthesizable", "l2 cache controller rtl",
    "scatter gather dma controller rtl", "10g ethernet mac pcs rtl",
    "pcie dma endpoint fpga verilog", "packet processing pipeline systemverilog",
    "aes sha crypto accelerator rtl", "fft dsp accelerator fpga vhdl",
    "systolic array matrix accelerator rtl", "video image processing pipeline fpga",
    "open source asic subsystem rtl", "complete fpga reference design verilog",
    "bender.yml systemverilog soc", "fusesoc core synthesizable processor",
    "chipyard peripheral rtl", "pulp platform accelerator rtl",
    "lowrisc systemverilog peripheral", "openhw group processor rtl",
]

STRATEGY_PRIORS = {
    "dependency": 0.65, "readme_reference": 0.50, "organization": 0.35,
    "organization_sibling": 0.45, "submodule": 0.35, "ecosystem": 0.30,
    "keyword": 0.0,
}
PROVIDER_STRATEGY_PRIORS = {("gitlab", "keyword"): -0.55, ("github", "keyword"): 0.1}
DISCOVERY_PRECISION_POLICY_SCHEMA = "rtl_discovery_precision_policy_v1"
PRODUCTION_ANCHORS = {
    "DIRECT_HDL_LANGUAGE", "DIRECT_HDL_FILE", "HDL_MANIFEST",
    "MULTI_EVIDENCE", "VERIFIED_RTL_GRAPH_NEIGHBOR",
}


def _family_batch_sequence(round_id: str) -> int | None:
    if not round_id.startswith("p2f_"):
        return None
    match = re.search(r"_batch(\d+)$", round_id)
    return int(match.group(1)) if match else None


def discovery_precision_recalibration(
    db: FrontierDB, current_report: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build the Batch-4 policy from three immutable FINAL family batches."""
    corpus = db.path.parent.parent
    reports: dict[str, dict[str, Any]] = {}
    for path in sorted((corpus / "quality/phase2/rounds").glob("p2f_*/phase2_round_delta_summary.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if value.get("yield_status") == "FINAL" and _family_batch_sequence(str(value.get("factory_round_id") or "")):
            reports[str(value["factory_round_id"])] = value
    if current_report and current_report.get("yield_status") == "FINAL":
        round_id = str(current_report.get("factory_round_id") or "")
        if _family_batch_sequence(round_id):
            reports[round_id] = current_report
    sequences = sorted(filter(None, (_family_batch_sequence(key) for key in reports)))
    if len(reports) < 3 or not sequences or max(sequences) < 3:
        return None

    totals: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for report in reports.values():
        for row in report.get("admission_anchor_yield", []):
            anchor = "RTL_QUERY_ORIGIN" if row.get("admission_anchor") == "RTL_QUERY" else str(row.get("admission_anchor") or "UNANCHORED")
            for key in (
                "new_acquired_revisions", "processed_revisions", "no_rtl_revisions",
                "new_design_instances", "new_design_families", "new_gold_families",
            ):
                totals[anchor][key] += int(row.get(key) or 0)

    cells: dict[str, dict[str, Any]] = {}
    for anchor, counts in sorted(totals.items()):
        support = counts["processed_revisions"] or counts["new_acquired_revisions"]
        no_rtl_rate = counts["no_rtl_revisions"] / max(1, support)
        family_yield = counts["new_design_families"] / max(1, counts["new_acquired_revisions"])
        if support > 100 and no_rtl_rate > 0.95 and family_yield < 0.02:
            tier = "DORMANT"
        elif anchor in PRODUCTION_ANCHORS:
            tier = "PRODUCTION"
        else:
            tier = "EXPLORATION"
        cells[anchor] = {
            "support": support, "tier": tier,
            "no_rtl_rate": round(no_rtl_rate, 6),
            "design_families_per_revision": round(family_yield, 6),
            "new_design_instances": counts["new_design_instances"],
            "new_gold_families": counts["new_gold_families"],
        }
    for anchor in sorted(PRODUCTION_ANCHORS):
        cells.setdefault(anchor, {
            "support": 0, "tier": "PRODUCTION", "no_rtl_rate": 0.0,
            "design_families_per_revision": 0.25,
            "new_design_instances": 0, "new_gold_families": 0,
        })
    policy = {
        "schema": DISCOVERY_PRECISION_POLICY_SCHEMA,
        "status": "ACTIVE",
        "activation_boundary": "p2f_batch0004_and_later",
        "calibration_round_ids": sorted(reports),
        "calibration_round_count": len(reports),
        "admission_anchor_cells": cells,
        "production_sources": sorted(PRODUCTION_ANCHORS),
        "exploration_sources": [
            "RTL_QUERY_ORIGIN", "QUERY_ONLY", "ORGANIZATION_ONLY", "GRAPH_ONLY",
        ],
        "dormancy_rule": {"support_gt": 100, "no_rtl_rate_gt": 0.95, "family_yield_lt": 0.02},
        "discovery_mix_guidance": {
            "direct_language_file_manifest": [0.40, 0.60],
            "verified_graph_ecosystem": [0.20, 0.30],
            "targeted_domain_x_hdl": [0.10, 0.20],
            "bounded_exploration": [0.10, 0.15],
            "enforcement": "ADAPT_FROM_FAMILY_YIELD_NOT_FIXED_QUOTA",
        },
        "primary_utility": "P_RTL_X_P_NEW_FORMAL_FAMILY_GIVEN_RTL_X_VALUE_DIV_COST",
        "no_rtl_stage_targets": [0.60, 0.50],
        "family_yield_target": 0.25,
        "updated_at": utc_now(),
    }
    db.connection.execute(
        """INSERT INTO scheduler_state(key,value_json,updated_at) VALUES('discovery_precision_policy',?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
        (json.dumps(policy, sort_keys=True), utc_now()),
    )
    db.connection.commit()
    db.reprioritize_hardware_likelihood(policy)
    return policy


def query_features(query: str) -> set[str]:
    text = query.lower()
    features = {name for name in DEFAULT_GAPS if name in text}
    if "soc" in text:
        features.add("large")
    return features


def query_family(query: str, strategy: str) -> str:
    features = sorted(query_features(query))
    if features:
        return "+".join(features)
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    return normalized[:120] if normalized else f"{strategy}:unattributed"


def deterministic_priority(
    query: str,
    historical_yield: float = 0,
    synthesis_yield: float = 0,
    gold_yield: float = 0,
    size_novelty: float = 0,
    source_quality: float = 0,
    no_rtl_rate: float = 0,
    duplicate_rate: float = 0,
    acquisition_success_rate: float = 0,
    revision_resolution_failure_rate: float = 0,
    archive_too_large_rate: float = 0,
    transport_extraction_failure_rate: float = 0,
    design_likelihood: float = 0.7,
    cost: float = 0.2,
    gaps: dict[str, float] | None = None,
    provider: str = "",
    strategy: str = "keyword",
) -> float:
    active_gaps = gaps or DEFAULT_GAPS
    features = query_features(query)
    diversity = max((active_gaps.get(feature, 0) for feature in features), default=0.3)
    scale = max(active_gaps.get("large", 0), diversity) if ({"large", "noc", "soc"} & features) else 0.2
    semantic_size_bonus = 1.0 if ({"xlarge", "large", "soc", "noc", "interconnect", "crossbar", "multicore"} & features) else 0.0
    observed_or_semantic_size = max(size_novelty, semantic_size_bonus)
    return round(
        SCHEDULER_WEIGHTS["family_yield"] * historical_yield
        + SCHEDULER_WEIGHTS["gold_yield"] * gold_yield
        + SCHEDULER_WEIGHTS["synthesis_valid_yield"] * synthesis_yield
        + SCHEDULER_WEIGHTS["functional_novelty"] * diversity
        + SCHEDULER_WEIGHTS["size_novelty"] * max(scale, observed_or_semantic_size)
        + SCHEDULER_WEIGHTS["source_quality"] * source_quality
        + SCHEDULER_WEIGHTS["design_likelihood"] * design_likelihood
        + SCHEDULER_WEIGHTS["no_rtl_rate"] * no_rtl_rate
        + SCHEDULER_WEIGHTS["duplicate_rate"] * duplicate_rate
        + SCHEDULER_WEIGHTS["acquisition_success_rate"] * acquisition_success_rate
        + SCHEDULER_WEIGHTS["revision_resolution_failure_rate"] * revision_resolution_failure_rate
        + SCHEDULER_WEIGHTS["archive_too_large_rate"] * archive_too_large_rate
        + SCHEDULER_WEIGHTS["transport_extraction_failure_rate"] * transport_extraction_failure_rate
        + SCHEDULER_WEIGHTS["cost"] * cost
        + STRATEGY_PRIORS.get(strategy, 0.0)
        + PROVIDER_STRATEGY_PRIORS.get((provider, strategy), 0.0),
        6,
    )


def seed_queries(db: FrontierDB, providers: list[str], budget: int, queries: list[str] | None = None) -> int:
    values = queries or DEFAULT_QUERIES
    per_query = max(1, budget // max(1, len(values) * len(providers)))
    count = 0
    for provider in providers:
        for query in values:
            db.add_query(provider, "keyword", query, deterministic_priority(query, provider=provider), per_query)
            count += 1
    db.connection.execute(
        """INSERT INTO scheduler_state(key,value_json,updated_at) VALUES('diversity_gaps',?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
        (json.dumps(DEFAULT_GAPS, sort_keys=True), utc_now()),
    )
    db.connection.execute(
        """INSERT INTO scheduler_state(key,value_json,updated_at) VALUES('scheduler_config',?,?)
           ON CONFLICT(key) DO NOTHING""",
        (json.dumps({"schema": SCHEDULER_SCHEMA, "weights": SCHEDULER_WEIGHTS}, sort_keys=True), utc_now()),
    )
    db.connection.commit()
    return count


def reprioritize(db: FrontierDB) -> int:
    gap_row = db.connection.execute("SELECT value_json FROM scheduler_state WHERE key='diversity_gaps'").fetchone()
    gaps = json.loads(gap_row[0]) if gap_row else DEFAULT_GAPS
    rows = db.connection.execute("SELECT query_id,provider,strategy,query_text FROM queries").fetchall()
    for row in rows:
        source_key = f"{row['provider']}:{row['strategy']}:{row['query_text']}"
        family_key = f"{row['provider']}:{row['strategy']}:family:{query_family(row['query_text'], row['strategy'])}"
        yield_row = db.connection.execute("SELECT * FROM source_yield WHERE source_key=?", (source_key,)).fetchone()
        if not yield_row or not yield_row["acquired"]:
            family_row = db.connection.execute("SELECT * FROM source_yield WHERE source_key=?", (family_key,)).fetchone()
            if family_row and family_row["acquired"]:
                yield_row = family_row
        historical = (yield_row["new_families"] / yield_row["candidates"]) if yield_row and yield_row["candidates"] else 0
        synthesis_yield = (yield_row["synthesis_valid_families"] / yield_row["candidates"]) if yield_row and yield_row["candidates"] else 0
        cost = (yield_row["cpu_hours"] / max(1, yield_row["new_families"])) if yield_row else 0.2
        observation_row = db.connection.execute(
            "SELECT value_json FROM scheduler_state WHERE key=?", (f"phase2_source_observation:{family_key}",)
        ).fetchone()
        observation = json.loads(observation_row[0]) if observation_row else {}
        feedback_row = db.connection.execute(
            "SELECT value_json FROM scheduler_state WHERE key=?",
            (f"phase2_acquisition_feedback:{family_key}",),
        ).fetchone()
        feedback = json.loads(feedback_row[0]) if feedback_row else {}
        feedback_attempts = max(1, int(feedback.get("attempts", 0)))
        observed_candidates = max(1, int(observation.get("candidates", 0)))
        observed_designs = max(1, int(observation.get("new_design_instances", 0)))
        weighted_size = sum(
            SIZE_WEIGHTS[name] * int(observation.get("resource_classes", {}).get(name, 0))
            for name in SIZE_WEIGHTS
        ) / observed_designs / max(SIZE_WEIGHTS.values())
        priority = deterministic_priority(
            row["query_text"], historical_yield=historical, synthesis_yield=synthesis_yield,
            gold_yield=int(observation.get("new_gold_families", 0)) / observed_candidates,
            size_novelty=weighted_size,
            source_quality=int(observation.get("new_design_instances", 0)) / observed_candidates,
            no_rtl_rate=int(observation.get("no_rtl", 0)) / observed_candidates,
            duplicate_rate=int(observation.get("duplicate_existing_family_design_instances", 0)) / observed_designs,
            acquisition_success_rate=int(feedback.get("acquired", 0)) / feedback_attempts,
            revision_resolution_failure_rate=int(feedback.get("revision_resolution_failures", 0)) / feedback_attempts,
            archive_too_large_rate=int(feedback.get("archive_too_large_failures", 0)) / feedback_attempts,
            transport_extraction_failure_rate=(
                int(feedback.get("http_failures", 0)) + int(feedback.get("extraction_failures", 0))
            ) / feedback_attempts,
            cost=min(2.0, cost), gaps=gaps, provider=row["provider"], strategy=row["strategy"],
        )
        db.connection.execute("UPDATE queries SET priority=?,updated_at=? WHERE query_id=?", (priority, utc_now(), row["query_id"]))
    db.connection.commit()
    return len(rows)


def calibrate_phase1_5(db: FrontierDB) -> int:
    for key, value in {
        "diversity_gaps": DEFAULT_GAPS,
        "scheduler_config": {
            "schema": SCHEDULER_SCHEMA, "weights": SCHEDULER_WEIGHTS,
            "strategy_priors": STRATEGY_PRIORS,
            "provider_strategy_priors": {f"{provider}:{strategy}": score for (provider, strategy), score in PROVIDER_STRATEGY_PRIORS.items()},
            "calibration_basis": "phase1_1047_revisions_yield",
        },
    }.items():
        db.connection.execute(
            """INSERT INTO scheduler_state(key,value_json,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
            (key, json.dumps(value, sort_keys=True), utc_now()),
        )
    db.connection.commit()
    return reprioritize(db)


def calibrate_phase2_round(
    db: FrontierDB,
    report: dict[str, Any],
    minimum_revisions: int = 400,
    final_delta_sha256: str | None = None,
) -> tuple[int, str]:
    round_id = str(report.get("factory_round_id") or "")
    acquired = int(report.get("acquisition_cohort", {}).get("new_acquired_revisions") or 0)
    if report.get("yield_status") != "FINAL":
        return 0, "SKIPPED_PROVISIONAL_ROUND"
    if acquired < minimum_revisions:
        return 0, "SKIPPED_BELOW_CALIBRATION_BATCH_MINIMUM"
    if final_delta_sha256 is None:
        material = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        final_delta_sha256 = hashlib.sha256(material).hexdigest()
    calibration_id = hashlib.sha256(f"{round_id}\n{final_delta_sha256}\n".encode()).hexdigest()
    state_key = f"phase2_round_calibration:{calibration_id}"
    round_key = f"phase2_round_calibration_round:{round_id}"
    prior = db.connection.execute(
        "SELECT value_json FROM scheduler_state WHERE key=?", (round_key,)
    ).fetchone()
    if prior:
        prior_id = json.loads(prior[0]).get("calibration_id")
        if prior_id != calibration_id:
            raise ValueError("factory round is already bound to a different FINAL delta")
        discovery_precision_recalibration(db, report)
        return 0, "IDEMPOTENT_CACHE_HIT"
    if db.connection.execute("SELECT 1 FROM scheduler_state WHERE key=?", (state_key,)).fetchone():
        discovery_precision_recalibration(db, report)
        return 0, "IDEMPOTENT_CACHE_HIT"
    for row in report.get("provider_strategy_query_family", []):
        provider, strategy = str(row["provider"]), str(row["strategy"])
        source_key = f"{provider}:{strategy}:family:{row['query_family']}"
        candidates = int(row.get("new_acquired_revisions") or 0)
        new_instances = int(row.get("new_design_instances") or 0)
        new_families = int(row.get("new_design_families") or 0)
        db.connection.execute(
            """INSERT INTO source_yield(source_key,provider,strategy,candidates,acquired,new_design_instances,new_families,synthesis_valid_families,cpu_hours,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_key) DO UPDATE SET
                 candidates=candidates+excluded.candidates,acquired=acquired+excluded.acquired,
                 new_design_instances=new_design_instances+excluded.new_design_instances,
                 new_families=new_families+excluded.new_families,
                 synthesis_valid_families=synthesis_valid_families+excluded.synthesis_valid_families,
                 cpu_hours=cpu_hours+excluded.cpu_hours,updated_at=excluded.updated_at""",
            (source_key, provider, strategy, candidates, candidates, new_instances, new_families, new_families, float(row.get("cpu_seconds") or 0.0) / 3600.0, utc_now()),
        )
        observation_key = f"phase2_source_observation:{source_key}"
        prior_observation_row = db.connection.execute(
            "SELECT value_json FROM scheduler_state WHERE key=?", (observation_key,)
        ).fetchone()
        observation = json.loads(prior_observation_row[0]) if prior_observation_row else {
            "schema": "rtl_phase2_source_observation_v1", "source_key": source_key,
            "candidates": 0, "new_design_instances": 0, "new_gold_families": 0,
            "no_rtl": 0, "duplicate_existing_family_design_instances": 0,
            "resource_classes": {name: 0 for name in SIZE_WEIGHTS},
        }
        observation["candidates"] += candidates
        observation["new_design_instances"] += new_instances
        observation["new_gold_families"] += int(row.get("new_gold_families") or 0)
        observation["no_rtl"] += int(row.get("classification:NO_RTL") or 0)
        observation["duplicate_existing_family_design_instances"] += int(row.get("duplicate_existing_family_design_instances") or 0)
        for name in SIZE_WEIGHTS:
            observation["resource_classes"][name] += int(row.get(f"resource:{name}") or 0)
        db.connection.execute(
            """INSERT INTO scheduler_state(key,value_json,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
            (observation_key, json.dumps(observation, sort_keys=True), utc_now()),
        )
    db.connection.execute(
        "INSERT INTO scheduler_state(key,value_json,updated_at) VALUES(?,?,?)",
        (state_key, json.dumps({"schema": "rtl_phase2_round_calibration_v2", "factory_round_id": round_id, "calibration_id": calibration_id, "final_delta_sha256": final_delta_sha256, "new_acquired_revisions": acquired}, sort_keys=True), utc_now()),
    )
    db.connection.execute(
        "INSERT INTO scheduler_state(key,value_json,updated_at) VALUES(?,?,?)",
        (round_key, json.dumps({"schema": "rtl_phase2_round_calibration_binding_v1", "factory_round_id": round_id, "calibration_id": calibration_id, "final_delta_sha256": final_delta_sha256}, sort_keys=True), utc_now()),
    )
    db.connection.commit()
    changed = reprioritize(db)
    discovery_precision_recalibration(db, report)
    return changed, "CALIBRATED"


def acquisition_failure_category(detail: str) -> str:
    value = detail.upper()
    if "PROVIDER_RATE_LIMIT" in value or "RATE_LIMITED" in value:
        return "PROVIDER_RATE_LIMIT"
    if "REVISION_RESOLUTION_FAILED" in value:
        return "REVISION_RESOLUTION"
    if "ARCHIVE_TOO_LARGE" in value:
        return "ARCHIVE_TOO_LARGE"
    if "EXTRACT_LIMIT_EXCEEDED" in value:
        return "EXTRACTION_LIMIT"
    if "HTTP" in value or "URL" in value or "TIMEOUT" in value:
        return "HTTP"
    return "OTHER"


def strong_rtl_repository(row: Any) -> bool:
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        metadata = {}
    text = " ".join(str(value) for value in (
        row["repo_name"], metadata.get("description", ""),
        metadata.get("primary_language", ""), metadata.get("ecosystem", ""),
    ))
    return float(row["design_likelihood"] or 0.0) >= 0.9 and bool(
        re.search(r"(?:^|[-_\s])(rtl|hdl|verilog|systemverilog|vhdl|fpga|asic)(?:$|[-_\s])", text, re.I)
    )


def calibrate_mid_round_acquisition(
    db: FrontierDB, round_id: str, round_started_at: str, providers: list[str], seed_budget: int
) -> dict[str, Any]:
    """Reweight a live acquisition round without creating a cohort boundary."""
    active = int(db.connection.execute(
        "SELECT COUNT(*) FROM repositories WHERE claimed_by IS NOT NULL OR acquisition_status='ACQUIRING'"
    ).fetchone()[0])
    if active:
        raise RuntimeError(f"mid-round recalibration requires zero active claims, found {active}")
    reconciled = db.reconcile_abandoned_attempts()
    attempts = db.connection.execute(
        """SELECT a.repository_key,a.state AS attempt_state,
                  COALESCE(a.error_detail,'') AS error_detail,
                  r.state AS repository_state,r.provider,r.repo_name,r.metadata_json,r.design_likelihood
           FROM acquisition_attempts a JOIN repositories r USING(repository_key)
           WHERE a.started_at>=? AND a.state!='RUNNING' ORDER BY a.started_at,a.attempt_id""",
        (round_started_at,),
    ).fetchall()
    event_cache: dict[str, tuple[str, str, str]] = {}
    feedback: defaultdict[str, Counter[str]] = defaultdict(Counter)
    per_repository: defaultdict[str, Counter[str]] = defaultdict(Counter)
    repository_rows: dict[str, Any] = {}
    for row in attempts:
        if row["attempt_state"] == "RATE_LIMITED":
            # Quota exhaustion is provider state, not candidate evidence.  It
            # must not lower source yield or consume bounded retry/suppression.
            continue
        repository_key = str(row["repository_key"])
        repository_rows[repository_key] = row
        if repository_key not in event_cache:
            event = db.connection.execute(
                """SELECT de.provider,de.strategy,COALESCE(q.query_text,'')
                   FROM discovery_events de LEFT JOIN queries q USING(query_id)
                   WHERE de.repository_key=? ORDER BY de.discovered_at,de.event_id LIMIT 1""",
                (repository_key,),
            ).fetchone()
            if event:
                event_cache[repository_key] = (str(event[0]), str(event[1]), query_family(str(event[2]), str(event[1])))
            else:
                event_cache[repository_key] = (str(row["provider"]), "UNATTRIBUTED", "UNATTRIBUTED")
        provider, strategy, family = event_cache[repository_key]
        source_key = f"{provider}:{strategy}:family:{family}"
        feedback[source_key]["attempts"] += 1
        if row["attempt_state"] == "ACQUIRED":
            feedback[source_key]["acquired"] += 1
            per_repository[repository_key]["acquired"] += 1
            continue
        category = acquisition_failure_category(str(row["error_detail"]))
        feedback[source_key][f"{category.lower()}_failures"] += 1
        per_repository[repository_key][category] += 1
    for source_key, counts in feedback.items():
        value = {
            "schema": "rtl_phase2_acquisition_feedback_v1", "source_key": source_key,
            "attempts": counts["attempts"], "acquired": counts["acquired"],
            "revision_resolution_failures": counts["revision_resolution_failures"],
            "archive_too_large_failures": counts["archive_too_large_failures"],
            "http_failures": counts["http_failures"],
            "extraction_failures": counts["extraction_limit_failures"],
        }
        db.connection.execute(
            """INSERT INTO scheduler_state(key,value_json,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
            (f"phase2_acquisition_feedback:{source_key}", json.dumps(value, sort_keys=True), utc_now()),
        )
    suppressed = Counter()
    for repository_key, counts in per_repository.items():
        row = repository_rows[repository_key]
        strong = strong_rtl_repository(row)
        reason = None
        if counts["REVISION_RESOLUTION"] >= 2:
            reason = "REVISION_RESOLUTION_RETRY_EXHAUSTED"
        elif counts["ARCHIVE_TOO_LARGE"] >= (2 if strong else 1):
            reason = "LARGE_REPOSITORY_LANE_EXHAUSTED" if strong else "OVERSIZE_LOW_RTL_CONFIDENCE"
        elif counts["EXTRACTION_LIMIT"] >= (2 if strong else 1):
            reason = "EXTRACTION_RETRY_EXHAUSTED"
        elif counts["HTTP"] >= 3:
            reason = "HTTP_RETRY_EXHAUSTED"
        if reason and row["repository_state"] == "FRONTIER":
            changed = db.connection.execute(
                """UPDATE repositories SET acquisition_status='EXCLUDED',claimed_by=NULL,
                   claim_started_at=NULL,next_retry_at=NULL WHERE repository_key=?
                   AND acquisition_status IN ('NOT_ACQUIRED','RETRY')""",
                (repository_key,),
            ).rowcount
            if changed:
                suppressed[reason] += 1
    prior_events = int(db.connection.execute(
        "SELECT COUNT(*) FROM scheduler_state WHERE key LIKE ?",
        (f"phase2_mid_round_recalibration:{round_id}:%",),
    ).fetchone()[0])
    seed_queries(db, providers, seed_budget, FRESH_DISCOVERY_QUERIES)
    evidence = {
        "schema": "rtl_phase2_mid_round_acquisition_recalibration_v1",
        "factory_round_id": round_id, "round_started_at": round_started_at,
        "sequence": prior_events + 1, "attempts_observed": len(attempts),
        "failure_taxonomy": dict(Counter(
            acquisition_failure_category(str(row["error_detail"]))
            for row in attempts if row["attempt_state"] == "FAILED"
        )),
        "provider_rate_limit_events": sum(
            row["attempt_state"] == "RATE_LIMITED" for row in attempts
        ),
        "suppressed_candidates": dict(suppressed), "abandoned_attempts_reconciled": reconciled,
        "size_aware_bonus_preserved": True, "cohort_lock_created": False,
    }
    db.connection.execute(
        "INSERT INTO scheduler_state(key,value_json,updated_at) VALUES(?,?,?)",
        (f"phase2_mid_round_recalibration:{round_id}:{prior_events + 1:04d}", json.dumps(evidence, sort_keys=True), utc_now()),
    )
    db.connection.commit()
    evidence["queries_reprioritized"] = reprioritize(db)
    return evidence


def activate_phase2(db: FrontierDB) -> int:
    """Freeze Phase-2 objectives while continuing live yield recalibration."""
    for key, value in {
        "diversity_gaps": DEFAULT_GAPS,
        "scheduler_config": {
            "schema": SCHEDULER_SCHEMA,
            "phase": "PHASE_2",
            "target_unique_immutable_repository_revisions": 10_000,
            "primary_kpis": ["synthesis_valid_design_families", "gold_design_families"],
            "weights": SCHEDULER_WEIGHTS,
            "size_weights": SIZE_WEIGHTS,
            "functional_confidence_weights": {"HIGH": 1.0, "MEDIUM": 0.625, "LOW": 0.125},
            "strategy_priors": STRATEGY_PRIORS,
            "provider_strategy_priors": {f"{provider}:{strategy}": score for (provider, strategy), score in PROVIDER_STRATEGY_PRIORS.items()},
            "calibration_basis": "phase2_live_from_1047_revision_baseline",
            "mapping_policy": "GOLD_CANDIDATE_HIGH_VALUE_OR_STRATIFIED_COHORT",
            "r1_policy": "BOUNDED_EVIDENCE_STRONG_ONLINE",
            "priority_order": ["DISCOVERY_QUALITY", "R0_PROCESSING", "EVIDENCE_STRONG_R1", "TARGETED_FRONTEND_RECOVERY", "R2", "R3"],
            "mixed_language_policy": "SUBTYPE_ADJUDICATION_THEN_TARGETED_BINDING_RECOVERY",
            "r3_priority": "LOW",
        },
    }.items():
        db.connection.execute(
            """INSERT INTO scheduler_state(key,value_json,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
            (key, json.dumps(value, sort_keys=True), utc_now()),
        )
    db.connection.commit()
    return reprioritize(db)


def activate_batch_c(db: FrontierDB, providers: list[str], budget: int) -> int:
    """Activate size-aware Phase-2 discovery without weakening publication gates."""
    seed_queries(db, providers, budget, BATCH_C_QUERIES)
    config = {
        "schema": SCHEDULER_SCHEMA, "phase": "PHASE_2", "batch": "C",
        "target_new_unique_immutable_repository_revisions": 7_000,
        "global_repository_revision_milestone": 10_000,
        "size_objective": BATCH_C_SIZE_OBJECTIVE, "size_weights": SIZE_WEIGHTS,
        "weights": SCHEDULER_WEIGHTS, "strategy_priors": STRATEGY_PRIORS,
        "provider_strategy_priors": {f"{p}:{s}": v for (p, s), v in PROVIDER_STRATEGY_PRIORS.items()},
        "small_design_policy": "RETAIN_AND_DEPRIORITIZE_ONLY_ON_EQUAL_YIELD",
        "quality_gate_policy": "UNCHANGED_PHASE2_COMPLETION_INVARIANTS",
    }
    db.connection.execute(
        """INSERT INTO scheduler_state(key,value_json,updated_at) VALUES('scheduler_config',?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
        (json.dumps(config, sort_keys=True), utc_now()),
    )
    db.connection.commit()
    return reprioritize(db)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--calibrate-phase1-5", action="store_true")
    parser.add_argument("--activate-phase2", action="store_true")
    parser.add_argument("--activate-batch-c", action="store_true")
    parser.add_argument("--mid-round-recalibrate", action="store_true")
    parser.add_argument("--rescore-frontier", action="store_true")
    parser.add_argument("--factory-round-id", default="")
    parser.add_argument("--round-started-at", default="")
    parser.add_argument("--providers", default="github,gitlab,codeberg,fusesoc")
    parser.add_argument("--seed-budget", type=int, default=20_000)
    parser.add_argument("--phase2-round-report", type=Path)
    parser.add_argument("--minimum-calibration-revisions", type=int, default=400)
    args = parser.parse_args()
    db = FrontierDB(args.corpus_root / "state/frontier.sqlite")
    try:
        calibration_state = None
        if args.rescore_frontier:
            changed = db.reprioritize_hardware_likelihood()
            calibration_state = "FRONTIER_EVIDENCE_RESCORED"
        elif args.mid_round_recalibrate:
            if not args.factory_round_id or not args.round_started_at:
                parser.error("--mid-round-recalibrate requires --factory-round-id and --round-started-at")
            evidence = calibrate_mid_round_acquisition(
                db, args.factory_round_id, args.round_started_at,
                [v for v in args.providers.split(",") if v], args.seed_budget,
            )
            changed = int(evidence["queries_reprioritized"])
            calibration_state = "MID_ROUND_RECALIBRATED"
        elif args.phase2_round_report:
            report = json.loads(args.phase2_round_report.read_text(encoding="utf-8"))
            report_hash = hashlib.sha256(args.phase2_round_report.read_bytes()).hexdigest()
            changed, calibration_state = calibrate_phase2_round(
                db, report, args.minimum_calibration_revisions, report_hash
            )
        elif args.activate_batch_c:
            changed = activate_batch_c(db, [v for v in args.providers.split(",") if v], args.seed_budget)
        elif args.activate_phase2:
            changed = activate_phase2(db)
        elif args.calibrate_phase1_5:
            changed = calibrate_phase1_5(db)
        else:
            changed = reprioritize(db)
        output = {"schema": SCHEDULER_SCHEMA, "queries_reprioritized": changed, "calibration_state": calibration_state}
        if args.mid_round_recalibrate:
            output["evidence"] = evidence
        print(json.dumps(output, indent=2))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
