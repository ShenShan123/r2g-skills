#!/usr/bin/env python3
"""Expand physical calibration support and evaluate a fresh prospective cohort.

This is an evidence-only campaign.  It keeps all ORFS regeneration under
``/tmp``, copies the canonical v3 SQLite snapshot to a staging database, and
loads prior external observations as action-provenance-bound calibration
support in that staging copy.  The v14-v38 lineages are materialized for
calibration and future prospective evaluation; held-out rows are never
written to TEHM before policy evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MEMORY_ROOT.parent
sys.path.insert(0, str(MEMORY_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_orfs_diversity_campaign import (  # noqa: E402
    _latest_successful_final_def,
    _load,
    _materialize,
    _write,
    run_projects,
)
from tehm import db  # noqa: E402
from evaluation.freeze_pointer import resolve_bundle  # noqa: E402
from tehm.adapters.orfs_pair import build_orfs_pair_record  # noqa: E402
from tehm.artifact_store import ArtifactStore  # noqa: E402
from tehm.physical.calibration import calibrate_retrieval  # noqa: E402
from tehm.physical.effects import extract_deltas  # noqa: E402
from tehm.physical.graph_context import load_defgraph_context  # noqa: E402
from tehm.physical.memory import PhysicalEffectMemory  # noqa: E402
from tehm.sync import canonical_json  # noqa: E402
from orfs_storage import enforce_work_root, storage_policy  # noqa: E402


VERSION = "calibration-expansion-v1"
SCRATCH_DEFAULT = Path("/tmp/tehm-p2-calibration-expansion-v14v20")
EVIDENCE_DEFAULT = Path(
    "/data1/zhangdy/tehm-campaigns/tehm-p2-calibration-expansion-v14v20"
)
ORFS_DEFAULT = Path(
    os.environ.get("ORFS_ROOT", "/opt/EDA4AI/OpenROAD-flow-scripts")
)
LINEAGES = (
    {"suffix": "v14", "design": "future_prospective_logic_v14", "base": "34"},
    {"suffix": "v15", "design": "future_prospective_logic_v15", "base": "32"},
    {"suffix": "v16", "design": "future_prospective_logic_v16", "base": "32"},
    {"suffix": "v17", "design": "future_prospective_logic_v17", "base": "30"},
    {"suffix": "v18", "design": "future_prospective_logic_v18", "base": "32"},
    {"suffix": "v19", "design": "future_prospective_logic_v19", "base": "32"},
    {"suffix": "v20", "design": "future_prospective_logic_v20", "base": "32"},
    {"suffix": "v21", "design": "future_prospective_logic_v21", "base": "30"},
    {"suffix": "v22", "design": "future_prospective_logic_v22", "base": "32"},
    {"suffix": "v23", "design": "future_prospective_logic_v23", "base": "34"},
    {"suffix": "v24", "design": "future_prospective_logic_v24", "base": "30"},
    {"suffix": "v25", "design": "future_prospective_logic_v25", "base": "32"},
    {"suffix": "v26", "design": "future_prospective_logic_v26", "base": "34"},
    {"suffix": "v27", "design": "future_prospective_logic_v27", "base": "30"},
    {"suffix": "v28", "design": "future_prospective_logic_v28", "base": "32"},
    {"suffix": "v29", "design": "future_prospective_logic_v29", "base": "34"},
    {"suffix": "v30", "design": "future_prospective_logic_v30", "base": "30"},
    {"suffix": "v31", "design": "future_prospective_logic_v31", "base": "32"},
    {"suffix": "v32", "design": "future_prospective_logic_v32", "base": "34"},
    {"suffix": "v33", "design": "future_prospective_logic_v33", "base": "30", "action": "40"},
    {"suffix": "v34", "design": "future_prospective_logic_v34", "base": "32", "action": "40"},
    {"suffix": "v35", "design": "future_prospective_logic_v35", "base": "34", "action": "40"},
    {"suffix": "v36", "design": "future_prospective_logic_v36", "base": "30", "action": "40"},
    {"suffix": "v37", "design": "future_prospective_logic_v37", "base": "32", "action": "40"},
    {"suffix": "v38", "design": "future_prospective_logic_v38", "base": "34", "action": "40"},
    {"suffix": "v39", "design": "future_prospective_logic_v39", "base": "30", "action": "40"},
    {"suffix": "v40", "design": "future_prospective_logic_v40", "base": "32", "action": "40"},
    {"suffix": "v41", "design": "future_prospective_logic_v41", "base": "34", "action": "40"},
    {"suffix": "v42", "design": "future_prospective_logic_v42", "base": "30", "action": "40"},
    {"suffix": "v43", "design": "future_prospective_logic_v43", "base": "32", "action": "40"},
    {"suffix": "v44", "design": "future_prospective_logic_v44", "base": "34", "action": "40"},
    {"suffix": "v45", "design": "future_prospective_logic_v45", "base": "30", "action": "40"},
    {"suffix": "v46", "design": "future_prospective_logic_v46", "base": "32", "action": "40"},
    {"suffix": "v47", "design": "future_prospective_logic_v47", "base": "34", "action": "40"},
    {"suffix": "v48", "design": "future_prospective_logic_v48", "base": "30", "action": "40"},
    {"suffix": "v49", "design": "future_prospective_logic_v49", "base": "32", "action": "40"},
    {"suffix": "v50", "design": "future_prospective_logic_v50", "base": "34", "action": "40"},
    {"suffix": "v51", "design": "future_prospective_logic_v51", "base": "30", "action": "40"},
    {"suffix": "v52", "design": "future_prospective_logic_v52", "base": "32", "action": "40"},
    {"suffix": "v53", "design": "future_prospective_logic_v53", "base": "34", "action": "40"},
    {"suffix": "v54", "design": "future_prospective_logic_v54", "base": "30", "action": "40"},
    {"suffix": "v55", "design": "future_prospective_logic_v55", "base": "32", "action": "40"},
    {"suffix": "v56", "design": "future_prospective_logic_v56", "base": "34", "action": "40"},
    {"suffix": "v57", "design": "future_prospective_logic_v57", "base": "30", "action": "40"},
    {"suffix": "v58", "design": "future_prospective_logic_v58", "base": "32", "action": "40"},
    {"suffix": "v59", "design": "future_prospective_logic_v59", "base": "34", "action": "40"},
    {"suffix": "v60", "design": "future_prospective_logic_v60", "base": "30", "action": "40"},
    {"suffix": "v61", "design": "future_prospective_logic_v61", "base": "32", "action": "40"},
    {"suffix": "v62", "design": "future_prospective_logic_v62", "base": "34", "action": "40"},
    {"suffix": "v63", "design": "future_prospective_logic_v63", "base": "30", "action": "40"},
    {"suffix": "v64", "design": "future_prospective_logic_v64", "base": "32", "action": "40"},
    {"suffix": "v65", "design": "future_prospective_logic_v65", "base": "34", "action": "40"},
    {"suffix": "v66", "design": "future_prospective_logic_v66", "base": "30", "action": "40"},
    {"suffix": "v67", "design": "future_prospective_logic_v67", "base": "32", "action": "40"},
    {"suffix": "v68", "design": "future_prospective_logic_v68", "base": "34", "action": "40"},
    {"suffix": "v69", "design": "future_prospective_logic_v69", "base": "30", "action": "40"},
    {"suffix": "v70", "design": "future_prospective_logic_v70", "base": "32", "action": "40"},
    {"suffix": "v71", "design": "future_prospective_logic_v71", "base": "34", "action": "40"},
    {"suffix": "v72", "design": "future_prospective_logic_v72", "base": "30", "action": "40"},
    {"suffix": "v73", "design": "future_prospective_logic_v73", "base": "32", "action": "40"},
    {"suffix": "v74", "design": "future_prospective_logic_v74", "base": "34", "action": "40"},
    {"suffix": "v75", "design": "future_prospective_logic_v75", "base": "30", "action": "40"},
    {"suffix": "v76", "design": "future_prospective_logic_v76", "base": "32", "action": "40"},
    {"suffix": "v77", "design": "future_prospective_logic_v77", "base": "34", "action": "40"},
    {"suffix": "v78", "design": "future_prospective_logic_v78", "base": "30", "action": "40"},
    {"suffix": "v79", "design": "future_prospective_logic_v79", "base": "32", "action": "40"},
    {"suffix": "v80", "design": "future_prospective_logic_v80", "base": "34", "action": "40"},
    {"suffix": "v81", "design": "future_prospective_logic_v81", "base": "30", "action": "40"},
    {"suffix": "v82", "design": "future_prospective_logic_v82", "base": "32", "action": "40"},
    {"suffix": "v83", "design": "future_prospective_logic_v83", "base": "34", "action": "40"},
    {"suffix": "v84", "design": "future_prospective_logic_v84", "base": "30", "action": "40"},
    {"suffix": "v85", "design": "future_prospective_logic_v85", "base": "32", "action": "40"},
    {"suffix": "v86", "design": "future_prospective_logic_v86", "base": "34", "action": "40"},
)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def _subset_manifest(manifest: dict, suffixes: set[str] | None) -> dict:
    """Select run/sample lineages without changing the manifest on disk."""
    if not suffixes:
        return manifest
    wanted = {str(value) for value in suffixes if str(value)}
    items = [item for item in manifest.get("items", [])
             if any(str(item.get("lineage_id", "")).startswith(
                 f"future-prospective-{suffix}:") for suffix in wanted)]
    if not items:
        raise RuntimeError(f"no campaign items match requested suffixes: {sorted(wanted)}")
    return {**manifest, "items": items,
            "selected_suffixes": sorted(wanted)}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(root: Path, orfs_root: Path) -> dict:
    template = orfs_root / "flow" / "designs" / "sky130hs" / "gcd"
    cfg, _template_sdc = template / "config.mk", template / "constraint.sdc"
    if not cfg.is_file() or not _template_sdc.is_file():
        raise FileNotFoundError(f"ORFS template incomplete: {template}")
    items = []
    for spec in LINEAGES:
        fixture = MEMORY_ROOT / "fixtures" / "physical_rtl" / f"{spec['design']}.v"
        sdc = MEMORY_ROOT / "fixtures" / "physical_rtl" / f"{spec['design']}.sdc"
        if not fixture.is_file() or not sdc.is_file():
            raise FileNotFoundError(f"prospective fixture incomplete: {fixture}")
        lineage = f"future-prospective-{spec['suffix']}:sky130hs:{spec['design']}:base0"
        common = {
            "DESIGN_NAME": spec["design"],
            "VERILOG_FILES": str(fixture.resolve()),
            "PLACE_DENSITY_LB_ADDON": "0.25",
            "EQUIVALENCE_CHECK": "0",
            "REMOVE_CELLS_FOR_EQY": "",
        }
        slug = f"sky130hs_{spec['suffix']}_base0"
        before = _materialize(root / "cases" / f"{slug}_before", cfg, sdc,
                              {**common, "CORE_UTILIZATION": spec["base"]})
        action_value = str(spec.get("action", "22"))
        after = _materialize(root / "cases" / f"{slug}_action{action_value}", cfg, sdc,
                             {**common, "CORE_UTILIZATION": action_value})
        items.append({
            "case_id": f"{lineage}:DENSITY_RELIEF:{spec['base']}->{action_value}",
            "lineage_id": lineage, "platform": "sky130hs",
            "design": spec["design"], "family": "DENSITY_RELIEF", "check": "route",
            "before_project": str(before), "after_project": str(after),
            "config_edits": {"CORE_UTILIZATION": action_value},
            "role": "prospective_observation", "capturable": False,
        })
    manifest = {
        "version": VERSION, "orfs_root": str(orfs_root.resolve()), "items": items,
        "storage_policy": storage_policy(root),
        "firewall": {
            "fresh_lineages": sorted(item["lineage_id"] for item in items),
            "disjoint": True,
        },
        "mutation_policy": "new samples remain external until policy evaluation completes",
    }
    _write_json(root / "campaign_manifest.json", manifest)
    return manifest


def _run_features(project: Path) -> int:
    runner = REPO_ROOT / "r2g-skills/def-graph/scripts/flow/run_features.sh"
    with (project / "def_graph_features.log").open("w") as out:
        proc = subprocess.run(
            ["bash", str(runner), str(project), "sky130hs", project.name],
            stdout=out, stderr=subprocess.STDOUT,
            env=dict(os.environ, R2G_SIGNOFF_GATE="warn"))
    return proc.returncode


def make_samples(root: Path, manifest: dict) -> dict:
    samples, evidence = [], []
    for item in manifest["items"]:
        before, after = Path(item["before_project"]), Path(item["after_project"])
        final_def = _latest_successful_final_def(before)
        if final_def is None:
            evidence.append({"case_id": item["case_id"], "status": "missing_successful_def"})
            continue
        feature_rc = _run_features(before)
        try:
            context = load_defgraph_context(before, def_path=final_def).to_dict()
            record = build_orfs_pair_record(
                before, after, lineage_id=item["lineage_id"], target_check="route",
                config_edits=item["config_edits"], transformation_family="DENSITY_RELIEF")
            observed = extract_deltas(record.before["reports"]["ppa"],
                                       record.after["reports"]["ppa"])
        except (OSError, ValueError, RuntimeError) as exc:
            evidence.append({"case_id": item["case_id"], "status": "pair_unavailable",
                             "feature_rc": feature_rc, "error": str(exc)})
            continue
        samples.append({
            "case_id": item["case_id"], "lineage_id": item["lineage_id"],
            "platform": item["platform"], "family": item["family"],
            "expected_tier": context.get("dataset_tier"), "graph_context": context,
            "action": record.action, "before_ppa": record.before["reports"]["ppa"],
            "after_ppa": record.after["reports"]["ppa"],
            "observed_deltas": observed,
        })
        evidence.append({"case_id": item["case_id"], "status": "evaluatable",
                         "lineage_id": item["lineage_id"],
                         "context_digest": context.get("digest"),
                         "observed_deltas": observed, "feature_rc": feature_rc})
    result = {
        "version": VERSION, "samples": samples, "evidence": evidence,
        "source_lineages": sorted({row["lineage_id"] for row in samples}),
        "mutation": "none; prospective samples were not recorded in TEHM",
    }
    _write_json(root / "prospective_samples.json", result)
    return result


def _strict_oracle_projects(manifest: dict) -> list[tuple[Path, str]]:
    """Return each unique before/after project once for full-oracle checks."""
    projects = {
        (Path(item[side]), str(item["platform"]))
        for item in manifest.get("items", [])
        for side in ("before_project", "after_project")
    }
    return sorted(projects, key=lambda value: (str(value[0]), value[1]))


def _strict_oracle_one(project: Path, platform: str, *, timeout: int) -> dict:
    """Run strict signoff plus the timing oracle for one completed ORFS project.

    The strict script is allowed to return non-zero: its reports are evidence
    even when a design is dirty or an infrastructure leg fails.  We therefore
    record both process return codes and normalized report status, never
    converting an unavailable obligation into a pass.
    """
    strict = REPO_ROOT / "r2g-skills/signoff-loop/scripts/flow/run_strict_signoff.sh"
    timing = REPO_ROOT / "r2g-skills/signoff-loop/scripts/reports/check_timing.py"
    strict_log = project / "strict_signoff.log"
    timing_log = project / "timing_check.log"
    reports = project / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    # Reuse only a receipt bound to the newest backend run and a timing report
    # from that same project.  A stale strict report must not satisfy a fresh
    # prospective observation.
    runs = sorted((project / "backend").glob("RUN_*"))
    run_tag = runs[-1].name if runs else None
    existing = _load(reports / "strict_signoff.json")
    reusable = bool(run_tag and existing.get("run_tag") == run_tag and
                    (reports / "timing_check.json").is_file())
    strict_rc = timing_rc = None
    timed_out = False
    if not reusable:
        env = dict(os.environ, NETGEN_TIMEOUT=str(max(1, timeout)))
        try:
            with strict_log.open("w") as output:
                proc = subprocess.run(
                    ["bash", str(strict), str(project), platform, project.name],
                    stdout=output, stderr=subprocess.STDOUT, env=env,
                    timeout=max(1, timeout), check=False)
            strict_rc = int(proc.returncode)
        except subprocess.TimeoutExpired:
            strict_rc = 124
            timed_out = True
        # check_timing is a separate oracle because run_strict_signoff's strict
        # gate does not emit the canonical timing_check.json consumed by the
        # TEHM ORFS pair adapter.
        try:
            with timing_log.open("w") as output:
                proc = subprocess.run(
                    [sys.executable, str(timing), str(project)],
                    stdout=output, stderr=subprocess.STDOUT,
                    env=env, timeout=max(1, timeout), check=False)
            timing_rc = int(proc.returncode)
        except subprocess.TimeoutExpired:
            timing_rc = 124
            timed_out = True

    strict_report = _load(reports / "strict_signoff.json")
    timing_report = _load(reports / "timing_check.json")
    timing_status = timing_report.get("status") or timing_report.get("tier")
    return {
        "project": str(project.resolve()),
        "platform": platform,
        "run_tag": run_tag,
        "reused": reusable,
        "strict_rc": strict_rc,
        "timing_rc": timing_rc,
        "timed_out": timed_out,
        "strict_status": strict_report.get("status"),
        "timing_status": timing_status,
        "timing_tier": timing_report.get("tier"),
        "strict_log": str(strict_log.resolve()) if strict_log.is_file() else None,
        "timing_log": str(timing_log.resolve()) if timing_log.is_file() else None,
        "strict_report_sha256": _sha256(strict_report_path)
        if (strict_report_path := reports / "strict_signoff.json").is_file()
        else None,
        "timing_report_sha256": _sha256(timing_report_path)
        if (timing_report_path := reports / "timing_check.json").is_file()
        else None,
    }


def run_strict_oracle(root: Path, manifest: dict, *, workers: int,
                      timeout: int) -> dict:
    """Materialize complete signoff obligations without touching TEHM memory."""
    projects = _strict_oracle_projects(manifest)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_strict_oracle_one, project, platform,
                               timeout=max(1, timeout))
                   for project, platform in projects]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: row["project"])
    state = {
        "version": "calibration-strict-oracle-v1",
        "requested": True,
        "required_reports": ["strict_signoff.json", "timing_check.json"],
        "projects": results,
        "all_reports_present": all(
            row["strict_status"] is not None and row["timing_status"] is not None
            for row in results),
        "mutation_policy": "oracle reports only; no TEHM capture or lifecycle mutation",
    }
    _write_json(root / "strict_oracle_state.json", state)
    return state


def _external_transition_id(sample: dict) -> str:
    payload = canonical_json({
        "lineage_id": sample["lineage_id"], "case_id": sample.get("case_id"),
        "action": sample.get("action"), "graph_context": sample.get("graph_context"),
    })
    return "external-calibration:" + hashlib.sha256(payload).hexdigest()[:24]


def _persist_external_transition(conn, *, transition_id: str, sample: dict,
                                 action: dict, action_json: str) -> None:
    """Insert one staging transition without overwriting immutable evidence."""
    values = (
        transition_id, "external_before:" + transition_id,
        "external_after:" + transition_id,
        str(action.get("domain") or "flow.CONFIG_DELTA"), action_json,
        canonical_json({"original_failure": "REMOVED"}).decode(),
        canonical_json({"verdict": "PASS", "oracle_type": "TARGET_TEST"}).decode(),
        "", "PASS", "[]", "[]",
        canonical_json({"source": "external_calibration_sample",
                        "lineage_id": sample["lineage_id"]}).decode(),
        "tehm-canonical-v0.1")
    columns = (
        "transition_id", "source_state_id", "target_state_id", "action_domain",
        "action_json", "observation_delta_json", "verifier_json",
        "primary_effect_key", "outcome", "created_regressions_json",
        "newly_observed_json", "provenance_json", "schema_version")
    existing = conn.execute(
        "SELECT * FROM tehm_transitions WHERE transition_id=?",
        (transition_id,)).fetchone()
    if existing is not None:
        conflicts = [column for column, value in zip(columns, values)
                     if str(existing[column] or "") != str(value or "")]
        if conflicts:
            raise ValueError(
                "external calibration transition is immutable and conflicts: "
                + ",".join(conflicts))
        return
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO tehm_transitions ({','.join(columns)}) "
        f"VALUES ({placeholders})", values)


def _load_external_training(root: Path, conn, store: ArtifactStore,
                            samples: list[dict]) -> list[str]:
    """Bind external samples to staging-only transitions for action filtering."""
    physical = PhysicalEffectMemory(conn)
    lineages = []
    for sample in samples:
        transition_id = _external_transition_id(sample)
        action = sample.get("action") or {}
        action_json = canonical_json(action).decode()
        # This minimal transition is deliberately staging-only.  It carries the
        # action provenance required by action-conditioned physical retrieval;
        # the actual PPA evidence remains in the physical row and report.
        _persist_external_transition(
            conn, transition_id=transition_id, sample=sample,
            action=action, action_json=action_json)
        conn.commit()
        physical.record(
            transition_id=transition_id,
            action_domain=str(action.get("domain") or "flow.CONFIG_DELTA"),
            transformation_family=str(sample.get("family") or "DENSITY_RELIEF"),
            before_ppa=sample["before_ppa"], after_ppa=sample["after_ppa"],
            effect_key="", evidence_refs=[], graph_context=sample["graph_context"])
        lineages.append(str(sample["lineage_id"]))
    return sorted(set(lineages))


def _sample_from_pair(pair: dict) -> dict:
    return {
        "case_id": pair.get("case_id"), "lineage_id": pair["lineage_id"],
        "family": "DENSITY_RELIEF", "graph_context": pair["graph_context"],
        "action": pair["action"], "before_ppa": pair["before_ppa"],
        "after_ppa": pair["after_ppa"],
        "observed_deltas": extract_deltas(pair["before_ppa"], pair["after_ppa"]),
    }


def _augment_sample_ppa(sample: dict, samples_path: Path) -> dict:
    """Recover compact PPA sides from the promoted calibration case receipts."""
    if sample.get("before_ppa") and sample.get("after_ppa"):
        return dict(sample)
    root = samples_path.parent.parent if samples_path.parent.name == "calibration" \
        else samples_path.parent
    case = root / "cases" / str(sample.get("case_id", "")).replace(":", "_")
    before_path, after_path = case / "before_ppa.json", case / "after_ppa.json"
    if not before_path.is_file() or not after_path.is_file():
        raise RuntimeError(f"promoted calibration PPA evidence missing for {sample.get('case_id')}")
    return {**sample, "before_ppa": _read(before_path), "after_ppa": _read(after_path)}


def evaluate(root: Path, *, canonical_snapshot: Path,
             v10v11_samples: Path, v12v13_pairs: Path,
             training_manifest: Path,
             prior_samples: Path | tuple[Path, ...] | list[Path] | None = None,
             fresh_suffixes: set[str] | None = None) -> dict:
    stage = root / "staging"
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(canonical_snapshot, stage)
    stage_db = stage / "closed_loop" / "tehm.sqlite"
    store = ArtifactStore(stage / "closed_loop" / "artifacts")
    v10v11 = [_augment_sample_ppa(sample, v10v11_samples)
              for sample in (_read(v10v11_samples).get("samples") or [])
              if sample.get("platform") == "sky130hs"]
    pairs = _read(v12v13_pairs).get("pairs") or []
    prior = list(v10v11) + [_sample_from_pair(pair) for pair in pairs]
    prior_paths = []
    if prior_samples is not None:
        prior_paths = ([prior_samples] if isinstance(prior_samples, Path)
                       else list(prior_samples))
    for prior_path in prior_paths:
        prior_data = _read(prior_path).get("samples") or []
        prior.extend(_augment_sample_ppa(sample, prior_path)
                     for sample in prior_data)
    conn = db.connect(stage_db)
    db.ensure_schema(conn)
    added_lineages = _load_external_training(root, conn, store, prior)
    fresh = _read(root / "prospective_samples.json").get("samples") or []
    if fresh_suffixes:
        fresh = [sample for sample in fresh
                 if any(str(sample.get("lineage_id", "")).startswith(
                     f"future-prospective-{suffix}:") for suffix in fresh_suffixes)]
    canonical_training = (_read(training_manifest).get("training_lineages") or
                          ((_read(training_manifest).get("firewall") or {}).get(
                              "training_lineages") or []))
    training = sorted(set(canonical_training) | set(added_lineages))
    physical = PhysicalEffectMemory(conn)
    policy = calibrate_retrieval(
        physical, family="DENSITY_RELIEF", heldout_samples=fresh,
        training_lineages=training, min_samples=3, min_unique_contexts=3,
        target_coverage=0.80, distance_ceiling=3.0)
    count_after = physical.count()
    conn.close()
    report = {
        "version": VERSION, "policy": policy,
        "training_lineages": training,
        "added_external_lineages": added_lineages,
        "fresh_lineages": sorted({row["lineage_id"] for row in fresh}),
        "canonical_snapshot": str(canonical_snapshot.resolve()),
        "staging_snapshot": str(stage.resolve()),
        "staging_snapshot_digest": _sha256(stage_db),
        "staging_physical_count": count_after,
        "canonical_mutation": "none; only a staging copy was opened writable",
        "promotion_eligible": False,
        "parametric_view_status": "NOT_IMPLEMENTED",
    }
    _write_json(root / "calibration_expansion_report.json", report)
    return report


def promote(root: Path, evidence_root: Path) -> dict:
    evidence_root.mkdir(parents=True, exist_ok=True)
    for name in ("campaign_manifest.json", "campaign_recovery_report.json",
                 "strict_oracle_state.json", "prospective_samples.json",
                 "calibration_expansion_report.json"):
        src = root / name
        if src.is_file():
            shutil.copy2(src, evidence_root / name)
    samples = _read(root / "prospective_samples.json") \
        if (root / "prospective_samples.json").is_file() else {}
    evaluated_lineages = sorted({str(row.get("lineage_id"))
                                 for row in samples.get("samples", [])
                                 if row.get("lineage_id")})
    selected_suffixes = sorted({
        lineage.split(":", 1)[0].removeprefix("future-prospective-")
        for lineage in evaluated_lineages
        if lineage.startswith("future-prospective-")
    })
    report = {
        "version": VERSION, "scratch_root": str(root),
        "evidence_root": str(evidence_root), "promotion_eligible": False,
        "selected_suffixes": selected_suffixes,
        "evaluated_lineages": evaluated_lineages,
        "mutation": "none; canonical v3 was not opened writable",
    }
    _write_json(evidence_root / "promotion_report.json", report)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", choices=("all", "prepare", "run", "samples",
                                         "evaluate", "promote"), default="all")
    ap.add_argument("--root", type=Path, default=SCRATCH_DEFAULT)
    ap.add_argument("--evidence-root", type=Path, default=EVIDENCE_DEFAULT)
    ap.add_argument("--orfs-root", type=Path, default=ORFS_DEFAULT)
    ap.add_argument("--canonical-snapshot", type=Path,
                    default=resolve_bundle(require_exists=False))
    ap.add_argument("--v10v11-samples", type=Path, required=True)
    ap.add_argument("--v12v13-pairs", type=Path, required=True)
    ap.add_argument("--training-manifest", type=Path, required=True)
    ap.add_argument("--prior-samples", type=Path, action="append", default=[],
                    help="previous cohort promoted to staging-only training support (repeatable)")
    ap.add_argument("--fresh-suffix", action="append", default=[],
                    help="lineage suffixes to hold out for evaluation (repeatable)")
    ap.add_argument("--run-suffix", action="append", default=[],
                    help="lineage suffixes to run/sample (repeatable; scratch-only subset)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--cpus-per-run", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--strict-oracle", action="store_true",
                    help="run strict signoff and timing reports before sampling")
    ap.add_argument("--strict-timeout", type=int, default=3600,
                    help="per-project timeout for strict signoff/timing")
    args = ap.parse_args(argv)
    root = enforce_work_root(args.root.resolve())
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "campaign_manifest.json"
    manifest = (prepare(root, args.orfs_root.resolve())
                if args.phase in {"all", "prepare"}
                else _load(manifest_path))
    if not manifest:
        raise RuntimeError(f"campaign manifest missing: {manifest_path}")
    if args.phase == "prepare":
        return 0
    run_manifest = _subset_manifest(manifest, set(args.run_suffix) or None)
    if args.phase in {"all", "run"}:
        run_projects(root, run_manifest, workers=max(1, args.workers),
                     cpus=max(1, args.cpus_per_run), timeout=max(1, args.timeout))
    if args.strict_oracle and args.phase in {"all", "run", "samples"}:
        run_strict_oracle(root, run_manifest, workers=max(1, args.workers),
                          timeout=max(1, args.strict_timeout))
    if args.phase == "run":
        return 0
    if args.phase in {"all", "samples"}:
        make_samples(root, run_manifest)
    if args.phase == "samples":
        return 0
    if args.phase in {"all", "evaluate"}:
        report = evaluate(root, canonical_snapshot=args.canonical_snapshot.resolve(),
                          v10v11_samples=args.v10v11_samples.resolve(),
                          v12v13_pairs=args.v12v13_pairs.resolve(),
                          training_manifest=args.training_manifest.resolve(),
                          prior_samples=tuple(path.resolve() for path in args.prior_samples),
                          fresh_suffixes=set(args.fresh_suffix) or None)
    else:
        report = _read(root / "calibration_expansion_report.json")
    if args.phase in {"all", "evaluate"}:
        print(json.dumps({"ok": True, "policy_status": report["policy"]["status"],
                          "coverage": report["policy"]["calibration"].get("empirical_coverage"),
                          "fresh_lineages": report["fresh_lineages"],
                          "promotion_eligible": False}, indent=2, sort_keys=True))
    if args.phase in {"all", "promote"}:
        promote(root, args.evidence_root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
