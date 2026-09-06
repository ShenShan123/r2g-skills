"""Capture one ordinary production ORFS before/after pair as a TEHM transition.

This adapter is deliberately separate from the A/B executor.  Training runs may
enter canonical memory; A/B arm outcomes may not (honesty H9).  Both sides must
carry a real ``backend/RUN_*/run-meta.json`` and stage log.  Missing evidence is
preserved as UNKNOWN instead of being promoted to a pass.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from tehm.adapters.r2g_evidence import parse_config_mk
from tehm.adapters.semantic_oracle import evaluate_pair, normalize_spec
from tehm.canonical.capture import ExecutionRecord
from tehm.physical.effects import extract_deltas

PAIR_ADAPTER_VERSION = "orfs-pair-v0.1"
_REPORT_FILES = {
    "ppa": "ppa.json", "route": "route.json", "drc": "drc.json",
    "lvs": "lvs.json", "timing": "timing_check.json", "rcx": "rcx.json",
}


def build_orfs_pair_record(before_project: Path, after_project: Path, *,
                           lineage_id: str, target_check: str = "route",
                           config_edits: dict | None = None,
                           transformation_family: str = "DENSITY_RELIEF",
                           rerun_from: str = "floorplan",
                           utility_contract_id: str | None = None,
                           semantic_oracle: Mapping | None = None) -> ExecutionRecord:
    """Build a replayable record from two completed production-run attempts."""
    before_project, after_project = Path(before_project), Path(after_project)
    before_run, after_run = _run_evidence(before_project), _run_evidence(after_project)
    before_toolchain = _campaign_toolchain_binding(before_project)
    after_toolchain = _campaign_toolchain_binding(after_project)
    # Validate a caller-supplied semantic contract before checking the action
    # payload.  A malformed oracle is a provenance error in its own right and
    # must not be hidden behind an unrelated missing-config-edit error.
    normalized_semantic_oracle = (
        normalize_spec(semantic_oracle)
        if semantic_oracle is not None else None)
    config_edits = {str(k): str(v) for k, v in (config_edits or {}).items()}
    if not config_edits:
        raise ValueError("an ORFS pair needs the concrete config edit that was executed")
    if utility_contract_id is not None and (
            not isinstance(utility_contract_id, str) or not utility_contract_id):
        raise ValueError("utility_contract_id must be a non-empty string")
    _require_real_run(before_run, "before")
    _require_real_run(after_run, "after")

    before_ok = _scope_success(target_check, before_run["reports"])
    after_ok = _scope_success(target_check, after_run["reports"])
    before_definitive = _scope_definitive(target_check, before_run)
    after_definitive = _scope_definitive(target_check, after_run)
    semantic_receipt = (
        evaluate_pair(before_project, after_project, normalized_semantic_oracle)
        if normalized_semantic_oracle is not None else None)
    if semantic_receipt is None:
        original = ("REMOVED" if not before_ok and after_ok else
                    "PRESENT" if before_definitive and not before_ok else "UNKNOWN")
        verdict = "PASS" if after_ok else ("FAIL" if after_definitive else "UNKNOWN")
        before_failure = 0 if before_ok else (1 if before_definitive else None)
        after_failure = 0 if after_ok else (1 if after_definitive else None)
    else:
        # Keep physical target success/failure separate from the semantic
        # contract.  A complete physical run may still be a semantic failure
        # before the action; only the source-bound receipt can set REMOVED.
        original = str(semantic_receipt["original_failure"])
        semantic_after = str(semantic_receipt["after"]["verdict"])
        verdict = ("PASS" if after_ok and semantic_after == "PASS" else
                   "FAIL" if after_definitive or semantic_after == "FAIL"
                   else "UNKNOWN")
        before_failure = (0 if semantic_receipt["before"]["verdict"] == "PASS"
                          else 1 if semantic_receipt["before"]["verdict"] == "FAIL"
                          else None)
        after_failure = (0 if semantic_after == "PASS" else
                         1 if semantic_after == "FAIL" else None)
    created = _created_regressions(before_run["reports"], after_run["reports"],
                                   target_check)
    checked, required = _obligation_counts(after_run, target_check)
    coverage = checked / required if required else None
    experiment_kind = "REPAIR" if original in {"REMOVED", "PRESENT"} else "OBSERVATION"
    # Summary PPA from a partial flow is not comparable to final PPA.  Keep
    # route recovery independent of utility; a failed baseline must not gain
    # either a harmful or a Pareto-safe label from cross-stage area changes.
    utility_verdict = (
        _utility_verdict(before_run["reports"].get("ppa") or {},
                         after_run["reports"].get("ppa") or {})
        if all(_completed_for_utility(run) for run in (before_run, after_run))
        else "UNKNOWN")
    oracle_complete = bool(before_definitive and after_definitive and coverage == 1.0)

    before_cfg = _config(before_project)
    after_cfg = _config(after_project)
    action_payload = {
        "config_edits": config_edits,
        "rerun_from": rerun_from, "recheck": target_check,
        "dependency_cone_changed": True,
        "register_boundary_changed": False,
    }
    if utility_contract_id is not None:
        # The contract binding is part of the immutable action identity.  It
        # must be present before record_id is derived, otherwise a later
        # calibration-time annotation could make two semantically different
        # actions share one transition identity.
        action_payload["utility_contract_id"] = utility_contract_id

    record_identity = {
        "before": before_run["run_meta"], "after": after_run["run_meta"],
        "lineage": lineage_id, "edit": config_edits, "check": target_check,
        "semantic_oracle": semantic_receipt,
    }
    # Keep legacy record IDs stable while making a contract-bound pair
    # unambiguously distinct from its unbound counterpart.
    if utility_contract_id is not None:
        record_identity["utility_contract_id"] = utility_contract_id
    record_id = "orfs-pair:" + hashlib.sha1(
        _stable(record_identity).encode()).hexdigest()[:24]
    episode_id = "episode:" + record_id.split(":", 1)[1]
    refs = _evidence_refs(before_project, before_run, "before") + \
        _evidence_refs(after_project, after_run, "after")
    repository_ref = _repository_ref(before_cfg, before_run)

    record = ExecutionRecord(
        record_id=record_id, domain="flow.signoff",
        project_id=lineage_id, design_id=lineage_id, lineage_id=lineage_id,
        repository_ref=repository_ref,
        before={
            "repository_ref": repository_ref, "config": before_cfg,
            "reports": before_run["reports"],
            "failure_signature": {
                "check": target_check,
                "class": before_run.get("failed_stage") or target_check,
                "predicates": {"production_run": True,
                               "flow_returncode": before_run["returncode"]},
            },
            "artifacts": {"orfs_pair_before": before_run["artifact_manifest"]},
        },
        action={
            "domain": "flow.CONFIG_DELTA",
            "transformation_family": transformation_family,
            "payload": action_payload,
        },
        after={
            "repository_ref": repository_ref, "config": after_cfg,
            "reports": after_run["reports"],
            "artifacts": {"orfs_pair_after": after_run["artifact_manifest"]},
        },
        observation_delta={
            "original_failure": original,
            "first_divergence": {"before": before_failure, "after": after_failure},
            "failing_tests": {"before": before_failure, "after": after_failure},
            "created_regressions": created, "newly_observed_failures": [],
            "experiment_kind": experiment_kind,
            "utility_verdict": utility_verdict,
        },
        verification={
            "verdict": verdict,
            "oracle_type": "REGRESSION" if target_check == "drc" else "TARGET_TEST",
            "scope": f"signoff:{target_check}", "confidence_tier": "R" if target_check == "drc" else "T",
            "obligation_coverage": coverage,
            "evidence_refs": refs,
            "tool_versions": after_run["tool_versions"],
            "required_obligations": required, "checked_obligations": checked,
            "oracle_complete": oracle_complete,
            "adapter_version": PAIR_ADAPTER_VERSION,
            "semantic_oracle": semantic_receipt,
            "toolchain_binding": {
                "before": before_toolchain,
                "after": after_toolchain,
            },
        },
        episode={"episode_id": episode_id,
                 "mechanism_family": transformation_family,
                 "lineage_id": lineage_id, "step_index": 0,
                 "terminal_status": (
                     "VERIFIED_REPAIR" if verdict == "PASS" and experiment_kind == "REPAIR"
                     else "VERIFIED_OBSERVATION" if verdict == "PASS"
                     else "PARTIAL")},
    )
    record.validate()
    return record


def _run_evidence(project: Path) -> dict:
    runs = sorted((project / "backend").glob("RUN_*"))
    if not runs:
        return {"run_meta": {}, "stage_log": [], "reports": {}, "returncode": None,
                "failed_stage": None, "artifact_manifest": {}, "tool_versions": {}}
    run = runs[-1]
    meta = _json(run / "run-meta.json")
    campaign_receipt = _json(project / "campaign-run-receipt.json")
    if not meta and campaign_receipt:
        # v0.2 diversity receipts use the explicit ``flow_rc`` name; v0.1
        # campaign receipts used ``returncode``.  Both are preserved production
        # executor evidence, including non-zero design failures.
        receipt_rc = campaign_receipt.get("returncode")
        if receipt_rc is None:
            receipt_rc = campaign_receipt.get("flow_rc")
        meta = {"run_tag": run.name,
                "make_status": receipt_rc,
                "config_mk": str(project / "constraints" / "config.mk"),
                "campaign_receipt": campaign_receipt}
    stages = _jsonl(run / "stage_log.jsonl")
    reports = {name: _json(project / "reports" / filename)
               for name, filename in _REPORT_FILES.items()}
    # A successful run always establishes route even if extraction was skipped.
    if not reports["route"] and int(meta.get("make_status", 1)) == 0:
        reports["route"] = {"status": "complete", "completed": True,
                            "provenance": {"run_tag": meta.get("run_tag")}}
    failed = next((str(s.get("stage")) for s in stages if int(s.get("status", 1)) != 0), None)
    rc = meta.get("make_status")
    manifest = {"run_dir": str(run.resolve()), "run_tag": meta.get("run_tag"),
                "run_meta_sha256": _sha(run / "run-meta.json"),
                "campaign_receipt_sha256": _sha(project / "campaign-run-receipt.json"),
                "stage_log_sha256": _sha(run / "stage_log.jsonl"),
                "final_def_sha256": _newest_sha(run / "final", "*.def"),
                "final_gds_sha256": _newest_sha(run / "final", "*.gds")}
    return {"run_meta": meta, "stage_log": stages, "reports": reports,
            "returncode": rc, "failed_stage": failed,
            "artifact_manifest": manifest,
            "tool_versions": {"orfs": meta.get("orfs_commit", "unknown"),
                              "adapter": PAIR_ADAPTER_VERSION}}


def _require_real_run(evidence: dict, side: str) -> None:
    meta = evidence.get("run_meta") or {}
    if not meta.get("run_tag") or meta.get("make_status") is None:
        raise ValueError(f"{side} project has no production ORFS run-meta evidence")
    if not evidence.get("stage_log"):
        raise ValueError(f"{side} project has no production ORFS stage log")


def _config(project: Path) -> dict:
    path = project / "constraints" / "config.mk"
    return parse_config_mk(path.read_text() if path.is_file() else "")


def _scope_success(scope: str, reports: dict) -> bool:
    r = reports.get(scope) or {}
    if scope == "timing":
        return r.get("tier") in {"clean", "met"} or r.get("status") == "clean"
    if scope == "route":
        return r.get("status") in {"clean", "complete", "pass"}
    return r.get("status") in {"clean", "clean_beol", "pass"}


def _scope_definitive(scope: str, run: dict) -> bool:
    if _scope_success(scope, run["reports"]):
        return True
    if run.get("returncode") not in (None, 0):
        return True
    status = (run["reports"].get(scope) or {}).get("status")
    return status not in (None, "", "unknown", "error")


def _created_regressions(before: dict, after: dict, target: str) -> list[str]:
    return sorted(scope for scope in ("route", "drc", "lvs", "timing")
                  if scope != target and _scope_success(scope, before)
                  and not _scope_success(scope, after))


def _obligation_counts(after: dict, target: str) -> tuple[int, int]:
    required = (target, "drc", "timing")
    checked = 0
    for scope in required:
        if scope == target and _scope_definitive(scope, after):
            checked += 1
            continue
        status = (after["reports"].get(scope) or {}).get("status")
        if scope == "timing":
            status = (after["reports"].get(scope) or {}).get("tier") or status
        if status not in (None, "", "unknown", "error"):
            checked += 1
    return checked, len(required)


def _completed_for_utility(run: dict) -> bool:
    """Require executor success and a completed finish stage on both arms.

    A zero exit from a synth-only invocation is not final PPA evidence.
    Report-level status strings cannot replace these execution witnesses.
    """
    return (run.get("returncode") == 0 and
            any(stage.get("stage") == "finish" and stage.get("status") == 0
                for stage in run.get("stage_log", ())))


def _utility_verdict(before_ppa: dict, after_ppa: dict) -> str:
    """Classify physical utility without conflating it with oracle PASS.

    Missing metrics remain UNKNOWN.  A pair is Pareto-safe only when at least
    one lower-is-better/higher-is-better metric improves and no observed metric
    moves in the harmful direction; otherwise a clean neutral pair is NEUTRAL.
    """
    deltas = extract_deltas(before_ppa, after_ppa)
    observed = {k: v for k, v in deltas.items() if v is not None}
    if not observed:
        return "UNKNOWN"
    harmful = (
        any(k in {"area_um2", "power_w", "congestion", "drc_violations", "tns_ns"}
            and v > 0 for k, v in observed.items()) or
        any(k == "wns_ns" and v < 0 for k, v in observed.items())
    )
    improved = (
        any(k in {"area_um2", "power_w", "congestion", "drc_violations", "tns_ns"}
            and v < 0 for k, v in observed.items()) or
        any(k == "wns_ns" and v > 0 for k, v in observed.items())
    )
    if harmful:
        return "HARMFUL"
    return "PARETO_SAFE" if improved else "NEUTRAL"


def _evidence_refs(project: Path, run: dict, side: str) -> list[dict]:
    refs = []
    for rel in ("run-meta.json", "stage_log.jsonl"):
        runs = sorted((project / "backend").glob("RUN_*"))
        path = runs[-1] / rel
        refs.append({"side": side, "path": str(path.resolve()), "sha256": _sha(path)})
    campaign_receipt = project / "campaign-run-receipt.json"
    if campaign_receipt.is_file():
        refs.append({"side": side, "kind": "campaign_receipt",
                     "path": str(campaign_receipt.resolve()),
                     "sha256": _sha(campaign_receipt)})
    for name, filename in _REPORT_FILES.items():
        path = project / "reports" / filename
        if path.is_file():
            refs.append({"side": side, "oracle": name,
                         "path": str(path.resolve()), "sha256": _sha(path)})
    return refs


def _repository_ref(config: dict, run: dict) -> str:
    payload = {"verilog_files": config.get("VERILOG_FILES"),
               "platform": config.get("PLATFORM"),
               "run_config": run.get("run_meta", {}).get("config_mk")}
    return "orfs-source:" + hashlib.sha256(_stable(payload).encode()).hexdigest()


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _campaign_toolchain_binding(project: Path) -> dict | None:
    """Load the executor binding emitted beside a production ORFS run."""
    receipt = _json(Path(project) / "campaign-run-receipt.json")
    binding = receipt.get("toolchain_binding")
    return dict(binding) if isinstance(binding, Mapping) else None


def _jsonl(path: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _newest_sha(root: Path, pattern: str) -> str | None:
    paths = sorted(root.glob(pattern)) if root.is_dir() else []
    return _sha(paths[-1]) if paths else None


def _stable(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
