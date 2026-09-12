#!/usr/bin/env python3
"""Build R3-8 ORFS P12 inputs from actual TEHM routing and binding.

The input is a pre-registered, source-disjoint challenge plus a frozen P13
source snapshot.  The builder replays the parent acquisitions in isolated RAM,
then requires the real knowledge router, asset selector, runtime binder and
structured-candidate builder to produce every memory candidate.  It writes a
P12 manifest and routing receipts outside the repository; it executes no EDA,
writes no canonical database and grants no lifecycle or production authority.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import MemoryQuery  # noqa: E402
from tehm.assets.receipts import RuntimeBindingReceipt  # noqa: E402
from tehm.evaluation import P12_ARMS  # noqa: E402
from tehm.evaluation.counterfactual_oracle import COUNTERFACTUAL_SCOPE  # noqa: E402
from tehm.evaluation.orfs_candidate_oracle import (  # noqa: E402
    _file_sha256, _source_binding, _source_content_binding, _source_inputs,
)
from tehm.ids import stable_dumps  # noqa: E402
from tehm.physical.utility_contracts import (  # noqa: E402
    action_contract_binding_reason, known_utility_contracts,
    utility_contract_digest,
)
from tehm.retrieval.asset_selector import (  # noqa: E402
    select_knowledge_grounded_assets,
)
from tehm.retrieval.memory_router import route_memory  # noqa: E402
from tehm.retrieval.structured_candidate import (  # noqa: E402
    build_structured_candidate,
)
from tehm.assets.flow_config_probe import probe_flow_config  # noqa: E402
from tehm.verified_execution import scoped_learning_replay  # noqa: E402


PREREGISTRATION_VERSION = "r3-8-source-bound-orfs-preregistration-v1"
REPORT_VERSION = "r3-8-source-bound-orfs-input-freeze-v1"
INPUT_AUTHORITY_VERSION = "r3-8-source-bound-orfs-input-authority-v1"
P12_MANIFEST_VERSION = "p12-orfs-cohort-manifest-v1"
_CONFIG_VALUE = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?::=|\?=|\+=|=)\s*(.*?)\s*$",
    re.MULTILINE)
_SOURCE_KEYS = ("VERILOG_FILES", "SDC_FILE")
_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


class SourceBoundInterferenceInputError(ValueError):
    """The frozen source, query or generated candidate chain is invalid."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _read(path: Path, name: str) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceBoundInterferenceInputError(
            f"cannot read {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise SourceBoundInterferenceInputError(f"{name} must be an object")
    return payload


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise SourceBoundInterferenceInputError(f"{name} is required")
    return value.strip()


def _path(value: object, name: str, *, directory: bool = False) -> Path:
    path = Path(_text(value, name)).expanduser().resolve()
    exists = path.is_dir() if directory else path.is_file()
    if not exists:
        kind = "directory" if directory else "file"
        raise SourceBoundInterferenceInputError(
            f"{name} is not a {kind}: {path}")
    return path


def _digest_pin(value: object, name: str) -> str:
    value = _text(value, name)
    if not value.startswith("sha256:") or len(value) <= len("sha256:"):
        raise SourceBoundInterferenceInputError(f"{name} must be a sha256 digest")
    return value


def _logical_digest(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return _digest("\n".join(conn.iterdump()))
    finally:
        conn.close()


def _closed(payload: Mapping, name: str) -> None:
    if (payload.get("evaluation_only") is not True or
            payload.get("canonical_memory_mutation") != "none" or
            payload.get("production_runtime_imported") is not False or
            payload.get("memory_docs_submitted") is not False):
        raise SourceBoundInterferenceInputError(
            f"{name} crosses the evaluation authority boundary")


def _verify_self_digest(payload: Mapping, field: str, name: str) -> str:
    supplied = payload.get(field)
    unsigned = dict(payload)
    unsigned.pop(field, None)
    if supplied != _digest(unsigned):
        raise SourceBoundInterferenceInputError(f"{name} {field} mismatch")
    return supplied


def _config_source_map(project: Path) -> dict[str, tuple[Path, ...]]:
    config = project / "constraints" / "config.mk"
    if not config.is_file():
        raise SourceBoundInterferenceInputError(
            f"project lacks constraints/config.mk: {project}")
    values = dict(_CONFIG_VALUE.findall(config.read_text()))
    sources: dict[str, tuple[Path, ...]] = {}
    for key in _SOURCE_KEYS:
        raw = values.get(key)
        if not raw:
            raise SourceBoundInterferenceInputError(
                f"project config lacks explicit {key}: {config}")
        try:
            tokens = shlex.split(raw)
        except ValueError as exc:
            raise SourceBoundInterferenceInputError(
                f"project {key} cannot be parsed: {config}") from exc
        if not tokens or any("$" in item or "`" in item for item in tokens):
            raise SourceBoundInterferenceInputError(
                f"project {key} must contain explicit paths: {config}")
        paths: list[Path] = []
        for token in tokens:
            path = Path(token).expanduser().resolve()
            if not path.is_file():
                raise SourceBoundInterferenceInputError(
                    f"project source input is missing: {path}")
            if path not in paths:
                paths.append(path)
        sources[key] = tuple(paths)
    return sources


def _config_sources(project: Path) -> tuple[Path, ...]:
    sources = _config_source_map(project)
    return tuple(dict.fromkeys(
        path for key in _SOURCE_KEYS for path in sources[key]))


def _training_rtl_hashes(acquisitions: Mapping) -> set[str]:
    hashes: set[str] = set()
    for item in acquisitions.values():
        if not isinstance(item, Mapping):
            raise SourceBoundInterferenceInputError(
                "parent acquisition entry is malformed")
        for arm in ("before", "after"):
            project = _path(item.get(arm), f"parent acquisition {arm}", directory=True)
            for source in _config_source_map(project)["VERILOG_FILES"]:
                hashes.add(_file_sha256(source))
    return hashes


def _runtime_binding(payload: Mapping) -> RuntimeBindingReceipt:
    required = {
        "asset_id", "knowledge_id", "target_design", "candidate_entities",
        "selected_binding", "structural_evidence", "failure_evidence",
        "ambiguity_count", "eligible", "reason", "binding_digest",
    }
    if not required <= set(payload):
        raise SourceBoundInterferenceInputError(
            "runtime binding receipt is incomplete")
    return RuntimeBindingReceipt(
        asset_id=payload["asset_id"], knowledge_id=payload["knowledge_id"],
        target_design=payload["target_design"],
        candidate_entities=tuple(payload["candidate_entities"]),
        selected_binding=dict(payload["selected_binding"]),
        structural_evidence=tuple(payload["structural_evidence"]),
        failure_evidence=tuple(payload["failure_evidence"]),
        ambiguity_count=payload["ambiguity_count"], eligible=payload["eligible"],
        reason=payload["reason"], binding_digest=payload["binding_digest"])


def _contract(preregistration: Mapping) -> tuple[dict, str]:
    contract_id = _text(
        preregistration.get("utility_contract_id"), "utility_contract_id")
    catalog = known_utility_contracts()
    if contract_id not in catalog:
        raise SourceBoundInterferenceInputError(
            "utility contract is not registered")
    contract = catalog[contract_id]()
    digest = "sha256:" + utility_contract_digest(contract)
    if _digest_pin(preregistration.get("utility_contract_digest"),
                   "utility_contract_digest") != digest:
        raise SourceBoundInterferenceInputError("utility contract digest mismatch")
    return contract, digest


def _evaluation_action(candidate, contract: Mapping) -> dict:
    action = copy.deepcopy(candidate.concrete_action)
    payload = action.get("payload")
    if not isinstance(payload, dict):
        raise SourceBoundInterferenceInputError(
            "generated candidate payload is malformed")
    payload["utility_contract_id"] = contract["contract_id"]
    reason = action_contract_binding_reason(action, contract)
    if reason is not None:
        raise SourceBoundInterferenceInputError(
            f"generated candidate does not match utility contract: {reason}")
    return action


def _source_snapshot(preregistration: Mapping):
    ref = preregistration.get("source_snapshot_report")
    if not isinstance(ref, Mapping):
        raise SourceBoundInterferenceInputError(
            "source_snapshot_report reference is required")
    report_path = _path(ref.get("path"), "source_snapshot_report.path")
    if _sha256(report_path) != _digest_pin(
            ref.get("sha256"), "source_snapshot_report.sha256"):
        raise SourceBoundInterferenceInputError(
            "source snapshot report file digest mismatch")
    report = _read(report_path, "source snapshot report")
    _closed(report, "source snapshot report")
    _verify_self_digest(report, "report_digest", "source snapshot report")
    if report.get("report_digest") != ref.get("report_digest"):
        raise SourceBoundInterferenceInputError(
            "source snapshot report content digest mismatch")
    db_ref = report.get("source_database")
    acquisition_ref = report.get("parent_acquisitions")
    if not isinstance(db_ref, Mapping) or not isinstance(acquisition_ref, Mapping):
        raise SourceBoundInterferenceInputError(
            "source snapshot lacks database/acquisition references")
    source_db = _path(db_ref.get("path"), "source database")
    if any(Path(str(source_db) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise SourceBoundInterferenceInputError("source database has live sidecars")
    if (_sha256(source_db) != db_ref.get("sha256") or
            _logical_digest(source_db) != db_ref.get("logical_digest")):
        raise SourceBoundInterferenceInputError("source database digest mismatch")
    acquisition_path = _path(
        acquisition_ref.get("path"), "parent acquisitions")
    if _sha256(acquisition_path) != acquisition_ref.get("sha256"):
        raise SourceBoundInterferenceInputError(
            "parent acquisition file digest mismatch")
    acquisition_payload = _read(acquisition_path, "parent acquisitions")
    acquisitions = acquisition_payload.get("acquisitions")
    if (not isinstance(acquisitions, dict) or not acquisitions or
            acquisition_payload.get("digest") !=
            acquisition_ref.get("acquisition_digest") or
            _digest(acquisitions) != acquisition_payload.get("digest")):
        raise SourceBoundInterferenceInputError(
            "parent acquisition content digest mismatch")
    replay = report.get("replay") or {}
    return (report_path, report, source_db, acquisition_path,
            acquisitions, acquisition_payload["digest"],
            _text(replay.get("campaign_id"), "parent replay campaign_id"))


def _toolchain(preregistration: Mapping) -> dict:
    raw = preregistration.get("toolchain")
    if not isinstance(raw, Mapping):
        raise SourceBoundInterferenceInputError("toolchain object is required")
    checked = {
        "orfs_root": str(_path(raw.get("orfs_root"), "orfs_root", directory=True)),
        "openroad_exe": str(_path(raw.get("openroad_exe"), "openroad_exe")),
        "yosys_exe": str(_path(raw.get("yosys_exe"), "yosys_exe")),
        "pdk_root": str(_path(raw.get("pdk_root"), "pdk_root", directory=True)),
        "toolchain_root": str(_path(
            raw.get("toolchain_root"), "toolchain_root", directory=True)),
        "toolchain_manifest": str(_path(
            raw.get("toolchain_manifest"), "toolchain_manifest")),
        "make_exe": str(_path(raw.get("make_exe"), "make_exe")),
        "python_exe": str(_path(raw.get("python_exe"), "python_exe")),
        "run_flow_script": str(_path(
            raw.get("run_flow_script"), "run_flow_script")),
        "fix_signoff_script": str(_path(
            raw.get("fix_signoff_script"), "fix_signoff_script")),
    }
    for name in ("openroad_exe", "yosys_exe", "make_exe", "python_exe",
                 "run_flow_script", "fix_signoff_script"):
        if not os.access(checked[name], os.X_OK):
            raise SourceBoundInterferenceInputError(
                f"{name} is not executable: {checked[name]}")
    for name in ("toolchain_digest", "oracle_digest", "platform_digest",
                 "pdk_digest"):
        checked[name] = _digest_pin(raw.get(name), name)
    checked["platform"] = _text(raw.get("platform"), "platform")
    return checked


def build_inputs(preregistration_path: Path | str,
                 *, output_dir: Path | str) -> dict:
    """Build a routed, source-bound P12 input freeze without executing EDA."""
    preregistration_path = Path(preregistration_path).expanduser().resolve()
    preregistration = _read(preregistration_path, "R3-8 preregistration")
    if preregistration.get("version") != PREREGISTRATION_VERSION:
        raise SourceBoundInterferenceInputError(
            "R3-8 preregistration version mismatch")
    _closed(preregistration, "R3-8 preregistration")
    campaign_id = _text(preregistration.get("campaign_id"), "campaign_id")
    cases = preregistration.get("cases")
    if (not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)) or
            len(cases) < 2):
        raise SourceBoundInterferenceInputError(
            "R3-8 preregistration requires at least two cases")
    contract, contract_digest = _contract(preregistration)
    toolchain = _toolchain(preregistration)
    (snapshot_path, snapshot, source_db, acquisition_path, acquisitions,
     acquisition_digest, replay_campaign_id) = _source_snapshot(preregistration)
    source_db_sha_before = _sha256(source_db)
    training_rtl_hashes = _training_rtl_hashes(acquisitions)
    output_dir = Path(output_dir).expanduser().resolve()
    if output_dir.exists():
        raise SourceBoundInterferenceInputError(
            f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_dir = output_dir / "candidates"
    candidate_dir.mkdir()
    execution_root = output_dir / "p12-execution"

    frozen = sqlite3.connect(source_db)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        frozen.backup(conn)
    finally:
        frozen.close()
    conn.execute("PRAGMA foreign_keys=ON")
    generated_cases = []
    routes = {}
    candidate_freeze = {}
    audit_cases = {}
    lineages: set[str] = set()
    content_digests: set[str] = set()
    challenge_rtl_hashes: set[str] = set()
    environment = preregistration.get("environment") or {}
    if not isinstance(environment, Mapping) or any(
            type(key) is not str or type(value) is not str
            for key, value in environment.items()):
        raise SourceBoundInterferenceInputError(
            "preregistration environment must be a string mapping")

    with scoped_learning_replay(
            conn, campaign_id=replay_campaign_id,
            acquisitions=acquisitions, expected_digest=acquisition_digest):
        for index, raw_case in enumerate(cases):
            if not isinstance(raw_case, Mapping):
                raise SourceBoundInterferenceInputError(
                    "R3-8 preregistration case is malformed")
            case_id = _text(raw_case.get("case_id"), "case_id")
            if not _SAFE_CASE_ID.fullmatch(case_id):
                raise SourceBoundInterferenceInputError(
                    f"unsafe challenge case ID: {case_id}")
            lineage_id = _text(raw_case.get("lineage_id"), f"{case_id}.lineage_id")
            if lineage_id in lineages:
                raise SourceBoundInterferenceInputError(
                    "challenge lineage IDs must be distinct")
            lineages.add(lineage_id)
            project = _path(
                raw_case.get("project_dir"), f"{case_id}.project_dir",
                directory=True)
            declared_inputs = raw_case.get("source_inputs")
            if not isinstance(declared_inputs, list) or not declared_inputs:
                raise SourceBoundInterferenceInputError(
                    f"{case_id} requires explicit source_inputs")
            source_inputs = _source_inputs(declared_inputs)
            config_sources = _config_source_map(project)
            actual_paths = {
                str(path) for paths in config_sources.values() for path in paths}
            if {item["path"] for item in source_inputs} != actual_paths:
                raise SourceBoundInterferenceInputError(
                    f"{case_id} source_inputs do not match config.mk")
            input_by_path = {item["path"]: item["sha256"]
                             for item in source_inputs}
            case_rtl_hashes = {
                input_by_path[str(path)]
                for path in config_sources["VERILOG_FILES"]}
            if case_rtl_hashes & training_rtl_hashes:
                raise SourceBoundInterferenceInputError(
                    f"{case_id} overlaps parent training RTL content")
            if case_rtl_hashes & challenge_rtl_hashes:
                raise SourceBoundInterferenceInputError(
                    f"{case_id} overlaps another challenge RTL input")
            challenge_rtl_hashes.update(case_rtl_hashes)
            source_digest = _source_binding(project, source_inputs)
            content_digest = _source_content_binding(project, source_inputs)
            if content_digest in content_digests:
                raise SourceBoundInterferenceInputError(
                    "challenge source content digests must be distinct")
            content_digests.add(content_digest)
            observation = probe_flow_config(
                project, Path(toolchain["orfs_root"]),
                keys=("CORE_UTILIZATION",),
                make_exe=Path(toolchain["make_exe"]),
                python_exe=Path(toolchain["python_exe"]),
                openroad_exe=Path(toolchain["openroad_exe"]),
                yosys_exe=Path(toolchain["yosys_exe"]))
            values = observation.get("values") or {}
            expected = raw_case.get("expected_flow")
            if not isinstance(expected, Mapping):
                raise SourceBoundInterferenceInputError(
                    f"{case_id} expected_flow is required")
            if (values.get("DESIGN_NAME") != expected.get("design_name") or
                    values.get("PLATFORM") != toolchain["platform"] or
                    values.get("CORE_UTILIZATION") !=
                    str(expected.get("core_utilization"))):
                raise SourceBoundInterferenceInputError(
                    f"{case_id} effective flow configuration drift")
            query = MemoryQuery(query_plan={
                "mechanism_family": contract["action_signature"][
                    "transformation_family"],
                "transformation_family": contract["action_signature"][
                    "transformation_family"],
                "target_scope": "flow_feasibility",
                "measurement_contract_digest": _text(
                    preregistration.get("measurement_contract_digest"),
                    "measurement_contract_digest"),
                "flow_design_id": values["DESIGN_NAME"],
                "flow_config": {"CORE_UTILIZATION": values["CORE_UTILIZATION"]},
            })
            route = route_memory(
                conn, query, no_memory_budget=2, memory_budget=1,
                persist_state=False, commit=False)
            if route.decision not in {"CONSIDER", "APPLY"}:
                raise SourceBoundInterferenceInputError(
                    f"{case_id} actual router did not select memory: "
                    f"{route.decision}/{route.no_skill_reason}")
            selection = select_knowledge_grounded_assets(
                conn, query, routing=route, candidate_budget=1)
            if len(selection.assets) != 1:
                raise SourceBoundInterferenceInputError(
                    f"{case_id} selector did not produce exactly one asset")
            raw_binding = selection.metadata.get("runtime_binding")
            if not isinstance(raw_binding, Mapping):
                raise SourceBoundInterferenceInputError(
                    f"{case_id} selector did not produce runtime binding")
            binding = _runtime_binding(raw_binding)
            candidate = build_structured_candidate(
                query, route, selection, binding)
            evaluation_action = _evaluation_action(candidate, contract)
            candidate_path = candidate_dir / f"{index:02d}-{case_id.replace(':', '_')}.json"
            _write(candidate_path, candidate.to_dict())
            candidate_ref = {
                "path": str(candidate_path), "sha256": _sha256(candidate_path),
                "candidate_id": candidate.candidate_id,
                "candidate_digest": candidate.candidate_digest,
            }
            candidate_freeze[case_id] = candidate_ref
            routes[case_id] = {
                **route.to_dict(), "decision_digest": route.decision_digest,
            }
            runtime_case = {
                "case_id": case_id, "lineage_id": lineage_id,
                "project_dir": str(project), "platform": toolchain["platform"],
                "target_check": COUNTERFACTUAL_SCOPE,
                **{name: toolchain[name] for name in (
                    "run_flow_script", "fix_signoff_script", "orfs_root",
                    "openroad_exe", "yosys_exe", "pdk_root", "toolchain_root",
                    "toolchain_manifest", "make_exe", "python_exe",
                    "toolchain_digest", "oracle_digest", "platform_digest",
                    "pdk_digest")},
                "environment": dict(environment),
                "source_inputs": [dict(item) for item in source_inputs],
                "source_digest": source_digest,
                "flow_config_observation": observation,
                "routing_decision": route.decision,
                "routing_receipt_id": route.routing_receipt_id,
                "execution_artifacts_root": str(execution_root / f"{index:02d}"),
                "candidate_paths": {
                    "NO_MEMORY": None,
                    **{arm: str(candidate_path.relative_to(output_dir))
                       for arm in P12_ARMS[1:]},
                },
            }
            generated_cases.append(runtime_case)
            audit_cases[case_id] = {
                "lineage_id": lineage_id,
                "source_digest": source_digest,
                "source_content_digest": content_digest,
                "rtl_sha256": sorted(case_rtl_hashes),
                "query": query.to_dict(),
                "route": routes[case_id],
                "asset_selection": selection.to_dict(),
                "runtime_binding": binding.to_dict(),
                "candidate": candidate_ref,
                "evaluation_action": evaluation_action,
                "flow_config_observation_digest": observation["receipt_digest"],
            }

    routes_path = output_dir / "routing-decisions.json"
    routes_payload = {"routes": routes}
    _write(routes_path, routes_payload)
    input_authority = {
        "version": INPUT_AUTHORITY_VERSION,
        "campaign_id": campaign_id,
        "preregistration": {
            "path": str(preregistration_path),
            "sha256": _sha256(preregistration_path),
            "digest": _digest(preregistration),
        },
        "source_snapshot_report": {
            "path": str(snapshot_path), "sha256": _sha256(snapshot_path),
            "report_digest": snapshot["report_digest"],
        },
        "source_database": {
            "path": str(source_db), "sha256": source_db_sha_before,
            "logical_digest": snapshot["source_database"]["logical_digest"],
        },
        "parent_acquisitions": {
            "path": str(acquisition_path), "sha256": _sha256(acquisition_path),
            "digest": acquisition_digest,
        },
        "utility_contract_id": contract["contract_id"],
        "utility_contract_digest": contract_digest,
        "source_disjoint_scope": "verilog_content_sha256",
        "cases": audit_cases,
        "candidate_freeze": candidate_freeze,
        "routing_decisions": {
            "path": str(routes_path), "sha256": _sha256(routes_path),
            "digest": _digest(routes_payload),
        },
        "actual_router_used": True,
        "actual_selector_used": True,
        "actual_runtime_binding_used": True,
        "actual_candidate_builder_used": True,
        "eda_executed": False,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "memory_docs_submitted": False,
    }
    input_authority["authority_digest"] = _digest(input_authority)
    authority_path = output_dir / "input-authority.json"
    _write(authority_path, input_authority)
    authority_ref = {
        "path": str(authority_path), "sha256": _sha256(authority_path),
        "authority_digest": input_authority["authority_digest"],
    }
    p12_manifest = {
        "version": P12_MANIFEST_VERSION,
        "campaign_id": campaign_id,
        "candidate_budget": 3,
        "min_lineages": len(lineages),
        "platform_digest": toolchain["platform_digest"],
        "pdk_digest": toolchain["pdk_digest"],
        "toolchain_digest": toolchain["toolchain_digest"],
        "oracle_digest": toolchain["oracle_digest"],
        "utility_contract_id": contract["contract_id"],
        "utility_contract_digest": contract_digest,
        "input_authority": authority_ref,
        "cases": generated_cases,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "memory_docs_submitted": False,
    }
    p12_path = output_dir / "p12-manifest.json"
    _write(p12_path, p12_manifest)
    source_db_unchanged = _sha256(source_db) == source_db_sha_before
    if not source_db_unchanged:
        raise SourceBoundInterferenceInputError(
            "source database changed during input construction")
    report = {
        "version": REPORT_VERSION,
        "campaign_id": campaign_id,
        "preregistration": {
            "path": str(preregistration_path),
            "sha256": _sha256(preregistration_path),
            "digest": _digest(preregistration),
        },
        "source_snapshot_report": {
            "path": str(snapshot_path), "sha256": _sha256(snapshot_path),
            "report_digest": snapshot["report_digest"],
        },
        "source_database": {
            "path": str(source_db), "sha256": source_db_sha_before,
            "unchanged": source_db_unchanged,
        },
        "parent_acquisitions": {
            "path": str(acquisition_path), "sha256": _sha256(acquisition_path),
            "digest": acquisition_digest,
            "training_lineages": sorted({
                item["lineage_id"] for item in acquisitions.values()}),
        },
        "utility_contract_id": contract["contract_id"],
        "utility_contract_digest": contract_digest,
        "case_count": len(generated_cases),
        "lineage_count": len(lineages),
        "source_disjoint": True,
        "source_disjoint_scope": "verilog_content_sha256",
        "training_rtl_overlap": [],
        "challenge_rtl_overlap": [],
        "cases": audit_cases,
        "p12_manifest": {
            "path": str(p12_path), "sha256": _sha256(p12_path),
            "digest": _digest(p12_manifest),
        },
        "routing_decisions": {
            "path": str(routes_path), "sha256": _sha256(routes_path),
            "digest": _digest(routes_payload),
        },
        "input_authority": authority_ref,
        "candidate_freeze": candidate_freeze,
        "actual_router_used": True,
        "actual_selector_used": True,
        "actual_runtime_binding_used": True,
        "actual_candidate_builder_used": True,
        "eda_executed": False,
        "evaluation_only": True,
        "canonical_memory_mutation": "none",
        "production_runtime_imported": False,
        "memory_docs_submitted": False,
    }
    report["report_digest"] = _digest(report)
    report_path = output_dir / "input-freeze-report.json"
    _write(report_path, report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_inputs(
            args.preregistration, output_dir=args.output_dir)
    except (OSError, SourceBoundInterferenceInputError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "report": str((args.output_dir / "input-freeze-report.json").resolve()),
        "report_digest": report["report_digest"],
        "case_count": report["case_count"],
        "actual_router_used": report["actual_router_used"],
        "eda_executed": report["eda_executed"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
