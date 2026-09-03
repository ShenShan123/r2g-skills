"""Evaluation-only ORFS executor for P12 structured flow candidates.

The shared R2G scripts remain the execution authority.  This adapter only
prepares a disposable project copy, applies a typed ``flow.CONFIG_DELTA``,
invokes ``run_orfs.sh`` and ``fix_signoff.sh`` through the existing lifecycle
helper, and translates the resulting reports into the P12 receipt mapping.
No canonical database or lifecycle table is opened here.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tehm.ids import stable_dumps
from tehm.retrieval.structured_candidate import StructuredRepairCandidate
from tehm.lifecycle.orfs_trial import (
    _apply_config_edits, _execute_arm, _parse_config, _scope_success,
    _snapshot_digest, _snapshot_source,
)


ORFS_CANDIDATE_ORACLE_VERSION = "orfs-candidate-oracle-v0.1"
_GOLD_KEYS = frozenset({"fix", "gold_patch", "repaired_rtl", "heldout_answer"})
_IGNORED_PROJECT_OUTPUTS = (
    "backend", "reports", "drc", "lvs", "rcx", ".orfs-work",
    ".orfs-design", ".tehm_ab", "features", "*.gds",
)
_PINNED_ENV_KEYS = frozenset({
    "R2G_HERMETIC", "ORFS_ROOT", "OPENROAD_EXE", "YOSYS_EXE", "PDK_ROOT",
    "R2G_PREFIX", "R2G_TOOLCHAIN_ROOT", "R2G_TOOLCHAIN_MANIFEST",
})


class OrfsCandidateOracleError(ValueError):
    """A frozen ORFS execution case or candidate action is malformed."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise OrfsCandidateOracleError(f"frozen ORFS case {name} is required")
    return value.strip()


def _file(value: object, name: str) -> Path:
    path = Path(_text(value, name)).expanduser().resolve()
    if not path.is_file():
        raise OrfsCandidateOracleError(f"frozen ORFS case {name} is not a file")
    return path


def _executable_file(value: object, name: str) -> Path:
    path = _file(value, name)
    if not os.access(path, os.X_OK):
        raise OrfsCandidateOracleError(f"frozen ORFS case {name} is not executable")
    return path


def _directory(value: object, name: str) -> Path:
    path = Path(_text(value, name)).expanduser().resolve()
    if not path.is_dir():
        raise OrfsCandidateOracleError(f"frozen ORFS case {name} is not a directory")
    return path


def _digest_pin(value: object, name: str) -> str:
    text = _text(value, name)
    if not text.startswith("sha256:") or len(text) <= len("sha256:"):
        raise OrfsCandidateOracleError(f"frozen ORFS case {name} must be a sha256 digest")
    return text


def _environment(case: Mapping) -> dict[str, str]:
    """Build a hermetic R2G environment from explicit frozen pins."""
    orfs_root = _directory(case.get("orfs_root"), "orfs_root")
    openroad = _executable_file(case.get("openroad_exe"), "openroad_exe")
    yosys = _executable_file(case.get("yosys_exe"), "yosys_exe")
    pdk_root = _directory(case.get("pdk_root"), "pdk_root")
    toolchain_root = case.get("toolchain_root")
    if toolchain_root is not None:
        toolchain_root = str(_directory(toolchain_root, "toolchain_root"))
    env = {
        "R2G_HERMETIC": "1",
        "ORFS_ROOT": str(orfs_root),
        "OPENROAD_EXE": str(openroad),
        "YOSYS_EXE": str(yosys),
        "PDK_ROOT": str(pdk_root),
    }
    if toolchain_root:
        env["R2G_PREFIX"] = toolchain_root
        env["R2G_TOOLCHAIN_ROOT"] = toolchain_root
    manifest = case.get("toolchain_manifest")
    if manifest is not None:
        env["R2G_TOOLCHAIN_MANIFEST"] = str(_file(manifest, "toolchain_manifest"))
    overrides = case.get("environment") or {}
    if not isinstance(overrides, Mapping):
        raise OrfsCandidateOracleError("frozen ORFS case environment must be an object")
    for key, value in overrides.items():
        if type(key) is not str or not key or type(value) is not str:
            raise OrfsCandidateOracleError("frozen ORFS case environment entry is invalid")
        if key in _PINNED_ENV_KEYS:
            raise OrfsCandidateOracleError(
                f"frozen ORFS environment cannot override pinned key {key}")
        env[key] = value
    return env


def _config_action(candidate: StructuredRepairCandidate) -> dict:
    action = candidate.concrete_action
    if not isinstance(action, Mapping):
        raise OrfsCandidateOracleError("structured ORFS candidate action is malformed")
    if action.get("domain") != "flow.CONFIG_DELTA":
        raise OrfsCandidateOracleError("ORFS candidate action must be flow.CONFIG_DELTA")
    payload = action.get("payload")
    if not isinstance(payload, Mapping):
        raise OrfsCandidateOracleError("ORFS candidate payload is malformed")
    edits = payload.get("config_edits")
    if not isinstance(edits, Mapping) or not edits:
        raise OrfsCandidateOracleError("ORFS candidate requires non-empty config_edits")
    if any(key in _GOLD_KEYS or not isinstance(key, str) or not key.strip()
           for key in edits):
        raise OrfsCandidateOracleError("ORFS candidate config_edits contain forbidden fields")
    if any(isinstance(value, (Mapping, list, tuple)) for value in edits.values()):
        raise OrfsCandidateOracleError("ORFS candidate config_edits values must be scalar")
    return {str(key): str(value) for key, value in edits.items()}


def _source_inputs(value: object) -> tuple[dict[str, str], ...]:
    """Validate immutable external inputs referenced by an ORFS project.

    The lifecycle snapshot covers ``constraints`` and ``rtl`` below the project,
    but real ORFS configs commonly point at RTL/SDC files elsewhere.  A frozen
    case must list those files with their expected SHA256 so source-disjoint
    cohorts cannot accidentally share an unbound external input.
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not value:
        raise OrfsCandidateOracleError("frozen ORFS source_inputs must be a non-empty array")
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise OrfsCandidateOracleError("frozen ORFS source input must be an object")
        path = _file(item.get("path"), "source_inputs.path")
        key = str(path)
        if key in seen:
            raise OrfsCandidateOracleError("frozen ORFS source_inputs contain duplicates")
        seen.add(key)
        expected = _text(item.get("sha256"), "source_inputs.sha256")
        if expected.startswith("sha256:"):
            expected = expected[len("sha256:"):]
        if len(expected) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in expected):
            raise OrfsCandidateOracleError("frozen ORFS source input sha256 is invalid")
        actual = _file_sha256(path)
        if actual.lower() != expected.lower():
            raise OrfsCandidateOracleError("frozen ORFS source input digest mismatch")
        entries.append({"path": key, "sha256": actual})
    return tuple(entries)


def _source_binding(project: Path, source_inputs: tuple[dict[str, str], ...]) -> str:
    snapshot = _snapshot_source(project)
    if not source_inputs:
        # Preserve the lifecycle helper's established digest for compact cases
        # whose complete source is contained under constraints/rtl.
        return _snapshot_digest(snapshot)
    project_files = {
        rel: hashlib.sha256(data).hexdigest()
        for rel, data in sorted(snapshot.items())
    }
    return _digest({
        "project_source": project_files,
        "external_source": list(source_inputs),
    })


def _source_content_binding(project: Path,
                            source_inputs: tuple[dict[str, str], ...]) -> str:
    """Return a path-independent fingerprint for source-disjoint checks."""
    project_hashes = [
        hashlib.sha256(data).hexdigest()
        for rel, data in sorted(_snapshot_source(project).items())
        if rel != "constraints/config.mk"
    ]
    external_hashes = [item["sha256"] for item in source_inputs]
    # Config knobs are an intervention, not design source.  Deduplicating a
    # copied SDC/RTL pair avoids treating the same bytes as two sources.
    return _digest({"content_sha256": sorted(set(project_hashes + external_hashes))})


def _verify_external_source_inputs(source_inputs: tuple[dict[str, str], ...]) -> None:
    for item in source_inputs:
        path = Path(item["path"])
        if not path.is_file() or _file_sha256(path) != item["sha256"]:
            raise OrfsCandidateOracleError(
                "ORFS external source input changed during execution")


def _copy_project(project: Path, destination: Path) -> None:
    shutil.copytree(project, destination,
                    ignore=shutil.ignore_patterns(*_IGNORED_PROJECT_OUTPUTS))


def _sandbox_name(case: Mapping, candidate: StructuredRepairCandidate | None) -> str:
    """Choose a collision-resistant ORFS ``FLOW_VARIANT`` directory name.

    ``run_orfs.sh`` derives its workspace variant from the temporary project
    basename when no explicit third argument is supplied.  Using the old
    constant basename ``project`` made unrelated P12 arms contend on the same
    global ORFS lock (and, worse, could race ``clean_all`` against another
    execution).  The case/candidate identity is immutable evidence, so a
    short digest gives each arm a deterministic variant without exposing
    caller-controlled path characters to the shell script.
    """
    case_id = case.get("case_id")
    candidate_id = "no-memory" if candidate is None else candidate.candidate_id
    digest = hashlib.sha256(stable_dumps({
        "case_id": case_id, "candidate_id": candidate_id,
    }).encode()).hexdigest()[:16]
    return "project_tehm_" + digest


def _result_from_arm(arm: Mapping, *, scope: str, action_applied: bool,
                     source_digest: str, config_before: str,
                     source_content_digest: str, config_after: str,
                     toolchain_digest: str,
                     oracle_digest: str, edits: Mapping | None) -> dict[str, Any]:
    flow_rc = arm.get("flow_rc")
    reports = arm.get("reports") if isinstance(arm.get("reports"), Mapping) else {}
    target = _scope_success(scope, reports)
    if flow_rc is None:
        verdict = "UNKNOWN"
        compile_result = functional_result = signoff_result = "UNKNOWN"
    else:
        compile_result = "PASS" if flow_rc == 0 else "FAIL"
        functional_result = "PASS" if target else "FAIL"
        signoff_result = functional_result
        verdict = "PASS" if arm.get("success") is True else "FAIL"
    obligations = {
        "ORFS_FLOW_PASS": compile_result,
        f"ORFS_{scope.upper()}_PASS": functional_result,
        "ORFS_SIGNOFF_PASS": signoff_result,
    }
    return {
        "compile_result": compile_result,
        "functional_result": functional_result,
        "signoff_result": signoff_result,
        "outcome": verdict,
        "created_regressions": [],
        "obligations": obligations,
        "toolchain_digest": toolchain_digest,
        "oracle_digest": oracle_digest,
        "produced_transition_id": None,
        "metadata": {
            "adapter_version": ORFS_CANDIDATE_ORACLE_VERSION,
            "scope": scope,
            "action_applied": action_applied,
            "config_before_digest": config_before,
            "config_after_digest": config_after,
            "source_digest": source_digest,
            "source_content_digest": source_content_digest,
            "config_edits": dict(edits) if isinstance(edits, Mapping) else {},
            "run_digest": _digest({
                "flow_rc": flow_rc, "fix_rc": arm.get("fix_rc"),
                "reports": reports, "success": arm.get("success") is True,
            }),
            "infrastructure_failure": flow_rc in {124, 127, 137},
        },
    }


def execute_orfs_candidate(candidate: StructuredRepairCandidate | None,
                           frozen_case: Mapping, budget: Mapping | int,
                           *, environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Execute one ORFS candidate or the NO_MEMORY baseline in a temp copy.

    Required frozen case keys are ``project_dir``, ``platform``,
    ``target_check``, ``run_flow_script``, ``fix_signoff_script``,
    ``orfs_root``, ``openroad_exe``, ``yosys_exe``, ``pdk_root``,
    ``toolchain_digest`` and ``oracle_digest``.  No fixture manifest is read.
    """
    if not isinstance(frozen_case, Mapping):
        raise OrfsCandidateOracleError("frozen ORFS case must be an object")
    project = _directory(frozen_case.get("project_dir"), "project_dir")
    platform = _text(frozen_case.get("platform"), "platform")
    scope = _text(frozen_case.get("target_check"), "target_check")
    if scope not in {"route", "drc", "lvs", "timing"}:
        raise OrfsCandidateOracleError("frozen ORFS target_check is invalid")
    run_flow = _file(frozen_case.get("run_flow_script"), "run_flow_script")
    fix_signoff = _file(frozen_case.get("fix_signoff_script"), "fix_signoff_script")
    toolchain_digest = _digest_pin(frozen_case.get("toolchain_digest"), "toolchain_digest")
    oracle_digest = _digest_pin(frozen_case.get("oracle_digest"), "oracle_digest")
    env = _environment(frozen_case)
    if environment is not None:
        if not isinstance(environment, Mapping):
            raise OrfsCandidateOracleError("ORFS environment override must be an object")
        for key, value in environment.items():
            if type(key) is not str or type(value) is not str:
                raise OrfsCandidateOracleError("ORFS environment override is invalid")
            if key in _PINNED_ENV_KEYS:
                raise OrfsCandidateOracleError(
                    f"ORFS environment override cannot replace pinned key {key}")
            env[key] = value
    source_inputs = _source_inputs(frozen_case.get("source_inputs"))
    source_digest = _source_binding(project, source_inputs)
    source_content_digest = _source_content_binding(project, source_inputs)
    expected_source_digest = frozen_case.get("source_digest")
    if expected_source_digest is None or type(expected_source_digest) is not str:
        raise OrfsCandidateOracleError("frozen ORFS case source_digest is required")
    if expected_source_digest != source_digest:
        raise OrfsCandidateOracleError("ORFS source freeze digest mismatch")
    config_path = project / "constraints" / "config.mk"
    if not config_path.is_file():
        raise OrfsCandidateOracleError("ORFS project constraints/config.mk is missing")
    config_before = _digest(_parse_config(config_path))
    edits = None
    with tempfile.TemporaryDirectory(prefix="tehm-p12-orfs-") as temp:
        # The basename becomes ORFS FLOW_VARIANT in run_orfs.sh.  Keep it
        # distinct per immutable case/arm so concurrent arms cannot share the
        # same ORFS workspace lock.
        sandbox = Path(temp) / _sandbox_name(frozen_case, candidate)
        _copy_project(project, sandbox)
        if candidate is not None:
            if not isinstance(candidate, StructuredRepairCandidate):
                raise OrfsCandidateOracleError(
                    "ORFS candidate must be StructuredRepairCandidate")
            edits = _config_action(candidate)
            _apply_config_edits(sandbox / "constraints" / "config.mk", edits)
        config_after = _digest(_parse_config(sandbox / "constraints" / "config.mk"))
        arm = _execute_arm(sandbox, platform, scope, run_flow, fix_signoff, env)
        result = _result_from_arm(
            arm, scope=scope, action_applied=candidate is not None,
            source_digest=source_digest, config_before=config_before,
            source_content_digest=source_content_digest,
            config_after=config_after, toolchain_digest=toolchain_digest,
            oracle_digest=oracle_digest, edits=edits)
    # The source project was never passed to the R2G command, but this check
    # catches accidental future changes that do mutate it before returning.
    if (_source_binding(project, source_inputs) != source_digest or
            _source_content_binding(project, source_inputs) != source_content_digest):
        raise OrfsCandidateOracleError("ORFS source project changed during execution")
    _verify_external_source_inputs(source_inputs)
    return result


class OrfsCandidateOracle:
    """Object adapter suitable for ``execute_paired_candidates``."""

    def __init__(self, environment: Mapping[str, str] | None = None):
        self.environment = dict(environment or {})

    def execute_candidate(self, candidate, frozen_case, budget):
        return execute_orfs_candidate(
            candidate, frozen_case, budget, environment=self.environment)

    def __call__(self, candidate, frozen_case, budget):
        return self.execute_candidate(candidate, frozen_case, budget)


__all__ = [
    "ORFS_CANDIDATE_ORACLE_VERSION", "OrfsCandidateOracleError",
    "OrfsCandidateOracle", "execute_orfs_candidate",
]
