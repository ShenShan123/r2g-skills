"""Read-only S2 deterministic-controller proposal preflight.

All three policies consume the same verified S1 development feedback.  The
original control is only a shared observation, never a No Memory Agent arm.
This module freezes candidate pools; execution and Agent outcomes are separate.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .research_s1_control import _digest, _file_sha, _load
from .research_s1_preflight import verify_s1_candidate_preflight


SCHEMA = "tehm-r4-s2-deterministic-proposal-preflight-v1"
POLICIES = ("no_persistent_memory", "legacy_memory", "tehm")
_ALLOWED_EDITS = frozenset({"CORE_UTILIZATION", "PLACE_DENSITY_LB_ADDON", "ABC_AREA"})


class ResearchS2ProposalError(ValueError):
    """An S2 proposal source, policy pool, or frozen baseline is invalid."""


def _repo() -> Path:
    return Path(__file__).resolve().parents[3]


def _legacy_files(manifest: Path) -> dict[str, Path]:
    root = _repo() / "r2g-skills/signoff-loop/knowledge"
    files = {"heuristics": root / "heuristics.json",
             "schema": root / "schema.sql",
             "database": root / "knowledge.sqlite"}
    saved = _load(manifest)
    content = {key: value for key, value in saved.items()
               if key != "manifest_digest"}
    expected = hashlib.sha256(json.dumps(content, indent=2, sort_keys=True).encode()).hexdigest()
    if saved.get("manifest_digest") != expected:
        raise ResearchS2ProposalError("Legacy baseline manifest digest changed")
    wanted = {"heuristics": saved.get("heuristics_digest"),
              "schema": saved.get("schema_digest"),
              "database": (saved.get("knowledge_db_fingerprint") or {}).get("db_sha256")}
    if any(not path.is_file() or _file_sha(path) != wanted[key]
           for key, path in files.items()):
        raise ResearchS2ProposalError("Legacy snapshot bytes differ from frozen manifest")
    wal = files["database"].with_name(files["database"].name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ResearchS2ProposalError("Legacy baseline has uncheckpointed WAL data")
    return files


def _recommend(project: Path, backend: str, script: Path,
               database_copy: Path) -> dict[str, Any]:
    if backend not in {"none", "legacy"}:
        raise ResearchS2ProposalError("unsupported R2G baseline backend")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("R2G_", "ORFS_"))}
    environment.update({"R2G_MEMORY_BACKEND": backend,
                        "PYTHONDONTWRITEBYTECODE": "1"})
    # Run the real recommender, but redirect any feature-KNN database access
    # to a verified isolated copy. The repository-default DB is never opened.
    command = [sys.executable, "-c",
               "import json,sys; from pathlib import Path; "
               "sys.path.insert(0,sys.argv[1]); import suggest_config; "
               "print(json.dumps(suggest_config.recommend("
               "Path(sys.argv[2]), use_learned=(sys.argv[3]=='legacy'), "
               "db_path=Path(sys.argv[4])), sort_keys=True))",
               str(script.parent), str(project), backend, str(database_copy)]
    try:
        process = subprocess.run(command, cwd=str(script.parent), env=environment,
                                 capture_output=True, text=True, timeout=30,
                                 check=False)
        if process.returncode:
            raise ResearchS2ProposalError(
                f"{backend} recommender failed rc={process.returncode}: "
                + process.stderr[-250:])
        report = json.loads(process.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise ResearchS2ProposalError(f"{backend} recommender unavailable") from exc
    if not isinstance(report, dict) or not isinstance(report.get("recommendations"), dict):
        raise ResearchS2ProposalError("R2G recommendation is malformed")
    if backend == "none" and (report.get("memory_backend") != "none"
                               or report.get("learned_source") is not None
                               or report.get("memory_proposal") is not None):
        raise ResearchS2ProposalError("No Memory recommender accessed memory")
    if backend == "legacy" and report.get("memory_backend", "legacy") != "legacy":
        raise ResearchS2ProposalError("Legacy recommender used a different backend")
    edits = {key: str(value) for key, value in report["recommendations"].items()}
    if (not edits or set(edits) - _ALLOWED_EDITS
            or "CORE_UTILIZATION" not in edits
            or any(not value for value in edits.values())):
        raise ResearchS2ProposalError("R2G recommendation exceeds S2 config contract")
    return {"config_edits": edits, "learned_source": report.get("learned_source"),
            "size_class": report.get("size_class"),
            "design_type": report.get("design_type"),
            "cell_count": report.get("cell_count"),
            "recommendation_digest": _digest(report)}


def _candidate(source: str, edits: dict[str, str], source_digest: str) -> dict[str, Any]:
    value = {"source": source, "action_domain": "flow.CONFIG_DELTA",
             "config_edits": dict(sorted(edits.items())),
             "source_digest": source_digest}
    value["candidate_digest"] = _digest(value)
    return value


def _pool(cold: dict[str, Any], advisor: dict[str, Any] | None,
          *, budget: int) -> dict[str, Any]:
    if type(budget) is not int or not 1 <= budget <= 3:
        raise ResearchS2ProposalError("candidate budget must be 1..3")
    if cold.get("source") != "cold_start" or not cold.get("config_edits"):
        raise ResearchS2ProposalError("every S2 policy needs a cold-start candidate")
    rows = [cold]
    duplicate = False
    if advisor is not None and budget > 1:
        if advisor.get("source") not in {"legacy_memory", "tehm"}:
            raise ResearchS2ProposalError("memory advisor source is invalid")
        duplicate = advisor["config_edits"] == cold["config_edits"]
        if not duplicate:
            rows.insert(0, advisor)
    if len(rows) > budget or sum(row["source"] != "cold_start" for row in rows) > 1:
        raise ResearchS2ProposalError("S2 candidate pool exceeded budget")
    return {"ordered_candidates": rows, "advisor_duplicate_of_cold": duplicate,
            "advisor_considered": advisor is not None and budget > 1,
            "candidate_limit": budget}


def _exact_legacy_runs(database: Path, top: str) -> int:
    connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
    try:
        return int(connection.execute(
            "SELECT COUNT(*) FROM runs WHERE design_name=?", (top,)).fetchone()[0])
    finally:
        connection.close()


def _report(*, preflight: Path, legacy_manifest: Path, controller: Path,
            budget: Path) -> dict[str, Any]:
    verified = verify_s1_candidate_preflight(preflight)
    previous = _load(preflight / "preflight.json")
    if verified.get("preflight_digest") != previous.get("preflight_digest"):
        raise ResearchS2ProposalError("S1 feedback and scoped M0 preflight drifted")
    controller_data = _load(controller)
    budget_data = _load(budget)
    if (controller_data.get("schema") != "tehm-research-controller-v1"
            or controller_data.get("controller_type") != "deterministic_skill"
            or controller_data.get("policies") != list(POLICIES)
            or controller_data.get("online_memory_update") is not False
            or controller_data.get("production_authority") is not False
            or budget_data.get("candidate_limit") != 3
            or budget_data.get("model_call_limit") != 0
            or budget_data.get("model_token_limit") != 0):
        raise ResearchS2ProposalError("frozen controller or budget differs from S2 contract")
    files = _legacy_files(legacy_manifest)
    script = files["heuristics"].parent / "suggest_config.py"
    if not script.is_file():
        raise ResearchS2ProposalError("R2G recommender is missing")
    binding = _load(Path(previous["control_binding_path"]))
    projects = {row["design_id"]: row for row in binding["control_projects"]}
    original_database_sha = _file_sha(files["database"])
    sidecar_paths = [files["database"].with_name(files["database"].name + suffix)
                     for suffix in ("-wal", "-shm")]
    original_sidecars = [(path.exists(), path.stat().st_size if path.exists() else None)
                         for path in sidecar_paths]
    rows = []
    with tempfile.TemporaryDirectory(prefix="tehm-s2-legacy-") as directory:
        database_copy = Path(directory) / "knowledge.sqlite"
        shutil.copyfile(files["database"], database_copy)
        if _file_sha(database_copy) != original_database_sha:
            raise ResearchS2ProposalError("isolated Legacy DB copy differs from baseline")
        for observed in previous["rows"]:
            design = observed["design_id"]
            if (not observed["target_failure"] or observed["candidate"] is None
                    or design not in projects):
                raise ResearchS2ProposalError("S2 development cohort lacks verified feedback")
            project = Path(projects[design]["project"])
            stage = _load(project / "stage-receipt.json")
            cold_report = _recommend(project, "none", script, database_copy)
            legacy_report = _recommend(project, "legacy", script, database_copy)
            cold = _candidate("cold_start", cold_report["config_edits"],
                              cold_report["recommendation_digest"])
            legacy = (_candidate("legacy_memory", legacy_report["config_edits"],
                                 legacy_report["recommendation_digest"])
                      if legacy_report["learned_source"] else None)
            source = observed["candidate"]
            action = source.get("concrete_action") or {}
            edits = (action.get("payload") or {}).get("config_edits")
            if (action.get("domain") != "flow.CONFIG_DELTA"
                    or not isinstance(edits, dict)
                    or set(edits) != {"CORE_UTILIZATION"}):
                raise ResearchS2ProposalError("TEHM candidate action is not scope bound")
            tehm = _candidate("tehm", edits, source["candidate_digest"])
            rows.append({
                "design_id": design, "source_group": observed["source_group"],
                "split": "pilot_development_reused_s1_feedback",
                "control_audit_digest": observed["control_audit_digest"],
                "control_project": str(project),
                "control_stage_receipt_digest": stage["receipt_digest"],
                "legacy_exact_top_run_count": _exact_legacy_runs(
                    files["database"], stage["top_module"]),
                "cold_start": cold_report, "legacy": legacy_report,
                "pools": {
                    "no_persistent_memory": _pool(cold, None, budget=3),
                    "legacy_memory": _pool(cold, legacy, budget=3),
                    "tehm": _pool(cold, tehm, budget=3),
                },
            })
        if _file_sha(database_copy) != original_database_sha:
            raise ResearchS2ProposalError("Legacy recommender mutated isolated DB bytes")
    if (_file_sha(files["database"]) != original_database_sha
            or [(path.exists(), path.stat().st_size if path.exists() else None)
                for path in sidecar_paths] != original_sidecars):
        raise ResearchS2ProposalError("Legacy baseline or sidecars changed during S2 preflight")
    if len(rows) != 2 or len({row["source_group"] for row in rows}) != 2:
        raise ResearchS2ProposalError("S2 development denominator/source groups changed")
    payload = {
        "schema": SCHEMA,
        "preflight_path": str(preflight),
        "preflight_digest": verified["preflight_digest"],
        "legacy_manifest_path": str(legacy_manifest),
        "legacy_manifest_sha256": "sha256:" + _file_sha(legacy_manifest),
        "legacy_bytes": {key: "sha256:" + _file_sha(path)
                         for key, path in files.items()},
        "legacy_database_isolated_copy": True,
        "recommender_sha256": "sha256:" + _file_sha(script),
        "controller_path": str(controller),
        "controller_sha256": "sha256:" + _file_sha(controller),
        "budget_path": str(budget),
        "budget_sha256": "sha256:" + _file_sha(budget),
        "proposal_source_sha256": "sha256:" + _file_sha(Path(__file__)),
        "policies": list(POLICIES), "candidate_limit": 3,
        "registered_designs": len(rows), "registered_policy_tasks": 3 * len(rows),
        "rows": rows,
        "cost": {"model_calls": 0, "eda_calls": 0,
                 "no_memory_recommender_calls": len(rows),
                 "legacy_recommender_calls": len(rows)},
        "authority": {"memory_update": "none", "production_authority": False,
                      "action_executed": False},
        "claim_boundary": "Pilot-development S2 proposal preflight only; controls are shared feedback, not No Memory Agent outcomes. Legacy has exact-design historical exposure; no Agent or final-heldout result is established.",
    }
    payload["proposal_digest"] = _digest(payload)
    return payload


def audit_s2_proposals(*, preflight: str | Path, legacy_manifest: str | Path,
                       controller: str | Path, budget: str | Path,
                       output: str | Path) -> dict[str, Any]:
    destination = Path(output).expanduser().resolve()
    if destination.exists():
        raise ResearchS2ProposalError("refusing to overwrite S2 proposal preflight")
    payload = _report(
        preflight=Path(preflight).expanduser().resolve(),
        legacy_manifest=Path(legacy_manifest).expanduser().resolve(),
        controller=Path(controller).expanduser().resolve(),
        budget=Path(budget).expanduser().resolve())
    destination.mkdir(parents=True)
    temporary = destination / f"proposals.json.tmp.{os.getpid()}"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True)
                         + "\n", encoding="utf-8")
    temporary.replace(destination / "proposals.json")
    return {"valid": True, "proposal_digest": payload["proposal_digest"],
            "registered_policy_tasks": payload["registered_policy_tasks"],
            "output": str(destination)}


def verify_s2_proposals(output: str | Path) -> dict[str, Any]:
    root = Path(output).expanduser().resolve()
    saved = _load(root / "proposals.json")
    if (saved.get("schema") != SCHEMA
            or saved.get("proposal_digest") != _digest({
                key: value for key, value in saved.items()
                if key != "proposal_digest"})):
        raise ResearchS2ProposalError("S2 proposal digest or schema changed")
    actual = _report(
        preflight=Path(saved["preflight_path"]),
        legacy_manifest=Path(saved["legacy_manifest_path"]),
        controller=Path(saved["controller_path"]),
        budget=Path(saved["budget_path"]))
    if actual != saved:
        raise ResearchS2ProposalError("S2 proposal replay differs from frozen report")
    return {"valid": True, "proposal_digest": saved["proposal_digest"],
            "registered_policy_tasks": saved["registered_policy_tasks"],
            "output": str(root)}
