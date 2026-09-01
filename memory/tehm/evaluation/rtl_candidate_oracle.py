"""Real Icarus-backed executor for P12 structured RTL candidates.

The adapter is deliberately evaluation-only.  A frozen case supplies source
and testbench paths; the adapter never opens a fixture manifest (and therefore
cannot consume ``manifest.fix``), never writes the canonical store, and never
creates a transition.  Candidate rewrites happen in a disposable temporary
file before the existing target + frozen-regression oracle is invoked.
"""
from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tehm.ids import stable_dumps
from tehm.rtl.rtl_actions import apply_rtl_action
from tehm.rtl.rtl_oracle import IcarusOracle
from tehm.retrieval.structured_candidate import StructuredRepairCandidate


RTL_CANDIDATE_ORACLE_VERSION = "rtl-candidate-oracle-v0.1"


class RtlCandidateOracleError(ValueError):
    """A frozen RTL execution case is malformed."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _required_path(case: Mapping, key: str) -> Path:
    value = case.get(key)
    if type(value) is not str or not value.strip():
        raise RtlCandidateOracleError(f"frozen RTL case requires {key}")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise RtlCandidateOracleError(f"frozen RTL case path is not a file: {key}")
    return path


def _rtl_paths(case: Mapping, source: Path) -> list[Path]:
    """Return the primary source followed by optional non-primary RTL files."""
    raw = case.get("rtl_files", ())
    if not isinstance(raw, (list, tuple)):
        raise RtlCandidateOracleError("frozen RTL case rtl_files must be a list")
    paths = [source]
    seen = {source}
    for value in raw:
        if type(value) is not str or not value.strip():
            raise RtlCandidateOracleError("frozen RTL case rtl_files contains an invalid path")
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise RtlCandidateOracleError("frozen RTL case rtl_files contains a missing file")
        if path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def _toolchain_digest(oracle: IcarusOracle, case: Mapping) -> str:
    supplied = case.get("toolchain_digest")
    if supplied is not None:
        if type(supplied) is not str or not supplied.strip():
            raise RtlCandidateOracleError("frozen RTL case toolchain_digest is invalid")
        return supplied.strip()
    if not oracle.available:
        return "UNAVAILABLE"
    # Executable content is stronger than a mutable PATH lookup and remains
    # identical across all paired arms.  The short metadata fallback keeps a
    # non-standard injected executable usable when it cannot be read.
    executable_digests: dict[str, str] = {}
    for name, executable in (("iverilog", oracle.iverilog), ("vvp", oracle.vvp)):
        if not executable:
            return "UNAVAILABLE"
        try:
            executable_digests[name] = _file_digest(Path(executable))
        except OSError:
            executable_digests[name] = str(Path(executable).resolve())
    return _digest({"toolchain": executable_digests})


def _oracle_digest(oracle: IcarusOracle, case: Mapping,
                   target: Path, regression: Path) -> str:
    supplied = case.get("oracle_digest")
    if supplied is not None:
        if type(supplied) is not str or not supplied.strip():
            raise RtlCandidateOracleError("frozen RTL case oracle_digest is invalid")
        return supplied.strip()
    return _digest({
        "adapter": RTL_CANDIDATE_ORACLE_VERSION,
        "icarus_oracle": type(oracle).__module__ + "." + type(oracle).__qualname__,
        "target_test": str(target),
        "frozen_regression": str(regression),
    })


def _compile_result(verification: Mapping) -> str:
    runs = [verification.get("target") or {}, verification.get("regression") or {}]
    values = [run.get("compile_verdict") for run in runs
              if isinstance(run, Mapping)]
    if any(value == "FAIL" for value in values):
        return "FAIL"
    if values and all(value == "PASS" for value in values):
        return "PASS"
    return "UNKNOWN"


def _status(value: object) -> str:
    return value if value in {"PASS", "FAIL", "UNKNOWN"} else "UNKNOWN"


def _obligations(verification: Mapping) -> dict[str, str]:
    target = verification.get("target") or {}
    regression = verification.get("regression") or {}
    obligations = {
        "RTL_TARGET_TEST_PASS": _status(target.get("verdict")),
        "RTL_FROZEN_REGRESSION_PASS": _status(regression.get("verdict")),
    }
    compile_values = [target.get("compile_verdict"), regression.get("compile_verdict")]
    if any(value == "FAIL" for value in compile_values):
        obligations["RTL_COMPILE_PASS"] = "FAIL"
    elif compile_values and all(value == "PASS" for value in compile_values):
        obligations["RTL_COMPILE_PASS"] = "PASS"
    else:
        obligations["RTL_COMPILE_PASS"] = "UNKNOWN"
    return obligations


def _verification_result(verification: Mapping, *, toolchain: str,
                         oracle_digest: str, source_before: str,
                         source_verified: str, candidate: StructuredRepairCandidate | None,
                         edit: Mapping | None) -> dict[str, Any]:
    verdict = _status(verification.get("verdict"))
    complete = verification.get("oracle_complete") is True
    signoff = "PASS" if verdict == "PASS" and complete else (
        "FAIL" if verdict == "FAIL" else "UNKNOWN")
    return {
        "compile_result": _compile_result(verification),
        "functional_result": verdict,
        "signoff_result": signoff,
        "outcome": verdict,
        "created_regressions": list(verification.get("created_regressions") or ()),
        "obligations": _obligations(verification),
        "toolchain_digest": toolchain,
        "oracle_digest": oracle_digest,
        "produced_transition_id": None,
        "metadata": {
            "adapter_version": RTL_CANDIDATE_ORACLE_VERSION,
            "oracle_type": verification.get("oracle_type", "UNKNOWN"),
            "oracle_complete": complete,
            "evidence_refs": list(verification.get("evidence_refs") or ()),
            "source_before_digest": "sha256:" + source_before,
            "source_verified_digest": "sha256:" + source_verified,
            "action_applied": candidate is not None,
            "action_edit": dict(edit) if isinstance(edit, Mapping) else None,
        },
    }


def execute_rtl_candidate(candidate: StructuredRepairCandidate | None,
                          frozen_case: Mapping, budget: Mapping | int,
                          oracle: IcarusOracle | None = None) -> dict[str, Any]:
    """Run one structured candidate (or the NO_MEMORY baseline) with Icarus.

    ``frozen_case`` intentionally has no manifest input.  Required keys are
    ``case_id``, ``rtl_source``, ``target_test`` and ``frozen_regression``;
    ``rtl_files`` may list additional source files.  The return shape is the
    mapping consumed by :func:`tehm.evaluation.execute_candidate`.
    """
    if not isinstance(frozen_case, Mapping):
        raise RtlCandidateOracleError("frozen RTL case must be an object")
    source = _required_path(frozen_case, "rtl_source")
    target = _required_path(frozen_case, "target_test")
    regression = _required_path(frozen_case, "frozen_regression")
    rtl_files = _rtl_paths(frozen_case, source)
    runner = oracle or IcarusOracle()
    if not isinstance(runner, IcarusOracle):
        raise RtlCandidateOracleError("RTL candidate oracle must be IcarusOracle")
    toolchain = _toolchain_digest(runner, frozen_case)
    oracle_digest = _oracle_digest(runner, frozen_case, target, regression)
    original = source.read_text()
    before_digest = _file_digest(source)
    edit: Mapping | None = None

    if candidate is None:
        verified_path = source
        verified_digest = before_digest
        verification = runner.verify(
            rtl_files, target_tb=target, regression_tb=regression)
        return _verification_result(
            verification, toolchain=toolchain, oracle_digest=oracle_digest,
            source_before=before_digest, source_verified=verified_digest,
            candidate=None, edit=None)

    if not isinstance(candidate, StructuredRepairCandidate):
        raise RtlCandidateOracleError("RTL candidate must be StructuredRepairCandidate")
    action = candidate.concrete_action
    if not isinstance(action, Mapping) or not isinstance(action.get("payload"), Mapping):
        raise RtlCandidateOracleError("structured RTL candidate action is malformed")
    payload = dict(action["payload"])
    payload["domain"] = action.get("domain")
    fixed_source, edit_value = apply_rtl_action(original, payload)
    edit = edit_value
    if edit.get("rewritten", 1) == 0:
        return {
            "compile_result": "UNKNOWN", "functional_result": "UNKNOWN",
            "signoff_result": "UNKNOWN", "outcome": "UNKNOWN",
            "created_regressions": [],
            "obligations": {name: "UNKNOWN" for name in candidate.obligations},
            "toolchain_digest": toolchain, "oracle_digest": oracle_digest,
            "produced_transition_id": None,
            "metadata": {"adapter_version": RTL_CANDIDATE_ORACLE_VERSION,
                          "action_applied": False, "action_edit": dict(edit)},
            "oracle_error": "action_not_applied",
        }

    with tempfile.TemporaryDirectory(prefix="tehm-p12-rtl-") as directory:
        fixed_path = Path(directory) / source.name
        fixed_path.write_text(fixed_source)
        verified_files = [fixed_path, *rtl_files[1:]]
        verification = runner.verify(
            verified_files, target_tb=target, regression_tb=regression)
        verified_digest = _file_digest(fixed_path)
        return _verification_result(
            verification, toolchain=toolchain, oracle_digest=oracle_digest,
            source_before=before_digest, source_verified=verified_digest,
            candidate=candidate, edit=edit)


class IcarusCandidateOracle:
    """Callable/object adapter suitable for ``execute_paired_candidates``."""

    def __init__(self, oracle: IcarusOracle | None = None):
        self.oracle = oracle or IcarusOracle()

    def execute_candidate(self, candidate, frozen_case, budget):
        return execute_rtl_candidate(candidate, frozen_case, budget,
                                     oracle=self.oracle)

    def __call__(self, candidate, frozen_case, budget):
        return self.execute_candidate(candidate, frozen_case, budget)


__all__ = [
    "RTL_CANDIDATE_ORACLE_VERSION", "RtlCandidateOracleError",
    "IcarusCandidateOracle", "execute_rtl_candidate",
]
