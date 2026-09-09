"""Pre-execution ORFS context registration for StateShift challenges."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from tehm.adapters.r2g_evidence import parse_config_mk
from tehm.ids import stable_dumps
from tehm.orfs_toolchain import load_toolchain_manifest
from tehm.orfs_toolchain_preflight import preflight_orfs_toolchain


REGISTRATION_VERSION = "orfs-state-shift-context-registration-v1"
CHALLENGE_MANIFEST_VERSION = "tehm-state-shift-challenge-selection-v1"
_REQUIRED_POLICIES = frozenset({"NO_MEMORY", "ALWAYS_MEMORY", "CAUSAL_NO_SKILL"})


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _file(path: Path) -> dict:
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("state-shift context input is not a file")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _measurement(knowledge) -> dict:
    value = getattr(knowledge, "intervention", {}).get("measurement_contract")
    if (not isinstance(value, Mapping) or type(value.get("scope")) is not str
            or type(value.get("contract_digest")) is not str):
        raise ValueError("state-shift context requires scoped Knowledge")
    return json.loads(stable_dumps(dict(value)))


def _challenge_membership(
        manifest_path: Path, *, project: Path, knowledge, envelope,
        challenge_id: str, lineage_id: str,
        toolchain_manifest_path: Path,
        expected_toolchain_manifest_digest: str) -> tuple[str, dict]:
    """Re-derive exact prospective cohort membership from immutable bytes."""
    manifest_path = Path(manifest_path).resolve(strict=True)
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("state-shift challenge manifest is unreadable") from exc
    if not isinstance(manifest, Mapping):
        raise ValueError("state-shift challenge manifest must be an object")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) < 2:
        raise ValueError("state-shift challenge requires two distinct lineages")
    if any(not isinstance(item, Mapping) for item in cases):
        raise ValueError("state-shift challenge cases are malformed")
    lineages = [item.get("lineage_id") for item in cases]
    if (any(type(item) is not str or not item.strip() for item in lineages)
            or len(set(lineages)) < 2):
        raise ValueError("state-shift challenge requires two distinct lineages")
    policies = manifest.get("planned_policies")
    try:
        bound_toolchain = Path(manifest["toolchain_manifest"]).resolve(strict=True)
    except (KeyError, OSError, TypeError) as exc:
        raise ValueError("state-shift challenge toolchain binding is invalid") from exc
    if (manifest.get("version") != CHALLENGE_MANIFEST_VERSION
            or manifest.get("role") != "prospective_challenge_selection"
            or not isinstance(policies, list)
            or set(policies) != _REQUIRED_POLICIES
            or bound_toolchain != toolchain_manifest_path):
        raise ValueError("state-shift challenge manifest contract mismatch")
    matches = [item for item in cases if isinstance(item, Mapping)
               and item.get("case_id") == challenge_id]
    if len(matches) != 1:
        raise ValueError("state-shift challenge case membership is not unique")
    member = matches[0]
    try:
        member_project = Path(member["current_project"]).resolve(strict=True)
    except (KeyError, OSError, TypeError) as exc:
        raise ValueError("state-shift challenge project binding is invalid") from exc
    if (member.get("lineage_id") != lineage_id or member_project != project
            or manifest.get("knowledge_parent") != knowledge.object_id
            or manifest.get("support_envelope_digest") != envelope.envelope_digest
            or manifest.get("toolchain_manifest_digest")
            != expected_toolchain_manifest_digest
            or manifest.get("reason_label") is not None
            or manifest.get("detector_status") != "pending"
            or manifest.get("execution_started") is not False):
        raise ValueError("state-shift challenge membership or boundary mismatch")
    digest = "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return digest, json.loads(stable_dumps(dict(member)))


def build_flow_state_shift_context(
        project: Path, knowledge, *, expected_toolchain_manifest_digest: str) -> dict:
    """Build typed pre-action facts from source bytes, not expected outcomes."""
    project = Path(project).resolve(strict=True)
    config_path = project / "constraints/config.mk"
    local_sdc = project / "constraints/constraint.sdc"
    config = parse_config_mk(config_path.read_text())
    try:
        rtl = tuple(Path(value).resolve(strict=True)
                    for value in config["VERILOG_FILES"].split())
        configured_sdc = Path(config["SDC_FILE"]).resolve(strict=True)
        design = config["DESIGN_NAME"]
        platform = config["PLATFORM"]
        utilization = config["CORE_UTILIZATION"]
    except (KeyError, OSError) as exc:
        raise ValueError("state-shift context requires explicit ORFS inputs") from exc
    if (not rtl or any(not path.is_file() for path in rtl) or not configured_sdc.is_file()
            or not local_sdc.is_file() or not expected_toolchain_manifest_digest):
        raise ValueError("state-shift context requires bound RTL/SDC/toolchain inputs")
    measurement = _measurement(knowledge)
    bindings = [_file(path) for path in
                sorted({config_path.resolve(), local_sdc.resolve(), configured_sdc, *rtl})]
    return {
        "mechanism_family": knowledge.mechanism_family,
        "compatibility_profile": knowledge.compatibility_profile,
        "structural_signature": {
            "design_name": design,
            "rtl_sha256": [hashlib.sha256(path.read_bytes()).hexdigest() for path in rtl],
        },
        "flow_regime": {"platform": platform,
                        "toolchain_manifest_digest": expected_toolchain_manifest_digest},
        "constraint_regime": {
            "core_utilization": utilization,
            "sdc_sha256": hashlib.sha256(configured_sdc.read_bytes()).hexdigest(),
            "wrapper_sdc_sha256": hashlib.sha256(local_sdc.read_bytes()).hexdigest(),
        },
        "oracle_regime": {"scope": measurement["scope"],
                          "contract_digest": measurement["contract_digest"]},
        "source_bindings": bindings,
    }


def _live_toolchain(manifest_path: Path, expected_digest: str) -> dict:
    locked = load_toolchain_manifest(manifest_path)
    if locked.get("manifest_digest") != expected_digest:
        raise ValueError("state-shift toolchain manifest pin mismatch")
    current = preflight_orfs_toolchain({
        "orfs_root": locked["orfs"]["root"],
        "toolchain_manifest": str(manifest_path.resolve()),
    })
    if (current.get("status") != "bound_internal"
            or (current.get("manifest_validation") or {}).get("valid") is not True
            or current["manifest_validation"].get("manifest_digest") != expected_digest):
        raise ValueError("state-shift live toolchain replay failed")
    return current


def register_flow_state_shift_context(
        project: Path, knowledge, envelope, *, challenge_id: str,
        lineage_id: str, challenge_manifest: str | Path,
        toolchain_manifest: str | Path,
        expected_toolchain_manifest_digest: str) -> dict:
    """Exclusively register context before any backend execution exists."""
    project = Path(project).resolve(strict=True)
    for name, value in (("challenge_id", challenge_id), ("lineage_id", lineage_id)):
        if type(value) is not str or not value.strip():
            raise ValueError(f"state-shift {name} is required")
    if (any((project / "backend").glob("RUN_*"))
            or (project / "campaign-run-receipt.json").exists()
            or (project / "terminal-preregistration.json").exists()):
        raise ValueError("state-shift context registration must precede execution")
    if getattr(envelope, "knowledge_object_id", None) != knowledge.object_id:
        raise ValueError("state-shift context Knowledge/envelope mismatch")
    challenge_manifest_path = Path(challenge_manifest).resolve(strict=True)
    manifest_path = Path(toolchain_manifest).resolve(strict=True)
    challenge_manifest_digest, challenge_member = _challenge_membership(
        challenge_manifest_path, project=project, knowledge=knowledge,
        envelope=envelope, challenge_id=challenge_id, lineage_id=lineage_id,
        toolchain_manifest_path=manifest_path,
        expected_toolchain_manifest_digest=expected_toolchain_manifest_digest)
    context = build_flow_state_shift_context(
        project, knowledge,
        expected_toolchain_manifest_digest=expected_toolchain_manifest_digest)
    registration = {
        "version": REGISTRATION_VERSION, "challenge_id": challenge_id,
        "lineage_id": lineage_id, "project": str(project),
        "knowledge_object_id": knowledge.object_id,
        "knowledge_content_digest": knowledge.content_digest,
        "support_envelope_digest": envelope.envelope_digest,
        "challenge_manifest": str(challenge_manifest_path),
        "challenge_manifest_digest": challenge_manifest_digest,
        "challenge_member": challenge_member,
        "toolchain_manifest": str(manifest_path),
        "toolchain_manifest_digest": expected_toolchain_manifest_digest,
        "toolchain_binding": _live_toolchain(manifest_path, expected_toolchain_manifest_digest),
        "current_context": context,
        "current_context_digest": _digest({"current_context": context}),
        "detector_result": "pending",
        "reason_label": None,
        "execution_started": False,
    }
    registration["registration_digest"] = _digest(registration)
    with (project / "state-shift-preregistration.json").open("x") as stream:
        stream.write(json.dumps(registration, indent=2, sort_keys=True) + "\n")
    return registration


def recheck_flow_state_shift_context(
        project: Path, knowledge, envelope, registration: Mapping, *,
        challenge_id: str, lineage_id: str,
        challenge_manifest: str | Path, toolchain_manifest: str | Path,
        expected_toolchain_manifest_digest: str) -> bool:
    """Recheck source and toolchain bytes; backend runs may now exist."""
    try:
        project = Path(project).resolve(strict=True)
        stored = json.loads((project / "state-shift-preregistration.json").read_text())
        supplied = dict(registration)
        unsigned = {key: value for key, value in supplied.items() if key != "registration_digest"}
        challenge_manifest_path = Path(challenge_manifest).resolve(strict=True)
        toolchain_manifest_path = Path(toolchain_manifest).resolve(strict=True)
        challenge_digest, challenge_member = _challenge_membership(
            challenge_manifest_path, project=project, knowledge=knowledge,
            envelope=envelope, challenge_id=challenge_id, lineage_id=lineage_id,
            toolchain_manifest_path=toolchain_manifest_path,
            expected_toolchain_manifest_digest=expected_toolchain_manifest_digest)
        context = build_flow_state_shift_context(
            project, knowledge,
            expected_toolchain_manifest_digest=expected_toolchain_manifest_digest)
        return (stored == supplied and supplied.get("version") == REGISTRATION_VERSION
                and supplied.get("project") == str(project)
                and supplied.get("challenge_id") == challenge_id
                and supplied.get("lineage_id") == lineage_id
                and supplied.get("knowledge_object_id") == knowledge.object_id
                and supplied.get("knowledge_content_digest") == knowledge.content_digest
                and supplied.get("support_envelope_digest") == envelope.envelope_digest
                and supplied.get("challenge_manifest") == str(challenge_manifest_path)
                and supplied.get("challenge_manifest_digest") == challenge_digest
                and supplied.get("challenge_member") == challenge_member
                and supplied.get("toolchain_manifest") == str(toolchain_manifest_path)
                and supplied.get("toolchain_manifest_digest")
                == expected_toolchain_manifest_digest
                and supplied.get("current_context") == context
                and supplied.get("current_context_digest") == _digest({"current_context": context})
                and supplied.get("registration_digest") == _digest(unsigned)
                and supplied.get("reason_label") is None
                and supplied.get("detector_result") == "pending"
                and supplied.get("execution_started") is False
                and supplied.get("toolchain_binding") == _live_toolchain(
                    toolchain_manifest_path, expected_toolchain_manifest_digest))
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


__all__ = ["REGISTRATION_VERSION", "CHALLENGE_MANIFEST_VERSION",
           "build_flow_state_shift_context",
           "register_flow_state_shift_context", "recheck_flow_state_shift_context"]
