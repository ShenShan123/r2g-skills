"""Fail-closed admission for real RTL -> ORFS batch observations.

The batch lane is intentionally separate from canonical capture.  Flow attempts
first become content-addressed external receipts.  Only receipts that satisfy
the complete semantic/physical oracle may be copied into an isolated staging
store.  A second, authority-bound operation is required for canonical import.

This module contains no ORFS executor.  It grades preserved executor evidence,
which keeps model/campaign proposals outside the semantic authority boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Mapping

from tehm import db as tehm_db
from tehm.adapters.orfs_pair import build_orfs_pair_record
from tehm.artifact_store import ArtifactStore
from tehm.canonical.capture import ExecutionRecord, capture
from tehm.canonical.transition import Action, ObservationDelta, classify_outcome
from tehm.canonical.verifier import VerifierSnapshot
from tehm.dataset import normalize_stored_learner_bool, require_learner_bool
from tehm.ids import stable_dumps
from tehm.physical.graph_context import load_defgraph_context
from tehm.physical.effects import extract_deltas
from tehm.physical.memory import PhysicalEffectMemory
from tehm.physical.orfs_preflight import (
    ROUTING_LAYER_ADJUSTMENT, inspect_routing_layer_adjustment,
    parse_orfs_config, preflight_digest, validate_persisted_execution_preflight)


BATCH_LANE_VERSION = "orfs-batch-lane-v1"
EXTERNAL_RECEIPT_VERSION = "orfs-external-observation-v1"
CANONICAL_IMPORT_AUTHORITY_VERSION = "orfs-canonical-import-authority-v1"

REQUIRED_ORACLES = (
    "synthesis",
    "equivalence",
    "route",
    "finish",
    "timing",
    "drc",
    "lvs",
    "strict_signoff",
    "ppa",
    "graph",
    "artifact_digest",
    "input_binding",
    "timing_contract",
)
PROMOTION_GATES = (
    "rollback_verified",
    "registry_verified",
    "obligation_coverage",
    "cross_lineage_te",
    "harmful_rate",
    "conformal_coverage",
)
OBSERVATION_SPLITS = frozenset({"support", "calibration", "heldout", "ab"})


class BatchLaneError(RuntimeError):
    """A fail-closed batch-lane contract violation."""


def canonical_case_selection_digest(case_ids: Iterable[str]) -> str:
    """Digest the exact support-case set approved by an authority receipt.

    The observation-file digest binds all available evidence, but it does not
    bind which rows the authority selected.  A separate canonical selection
    digest prevents a valid receipt from being replayed after its ``case_ids``
    are changed to import a different support case.
    """
    if isinstance(case_ids, (str, bytes)):
        raise BatchLaneError(
            "canonical import case_ids must be a sequence, not a string")
    values = [str(case_id).strip() for case_id in case_ids]
    if (not values or any(not value for value in values) or
            len(set(values)) != len(values)):
        raise BatchLaneError(
            "canonical import case_ids must be unique and non-empty")
    return hashlib.sha256(stable_dumps(sorted(values)).encode()).hexdigest()


def staging_witness_digest(witness: Iterable[Mapping]) -> str:
    """Digest the case→canonical-transition mapping proven in staging."""
    rows = []
    for item in witness:
        if not isinstance(item, Mapping):
            raise BatchLaneError("staging witness row is malformed")
        rows.append({
            "case_id": str(item.get("case_id") or "").strip(),
            "record_id": str(item.get("record_id") or "").strip(),
            "transition_id": str(item.get("transition_id") or "").strip(),
        })
    if (not rows or any(not value for row in rows for value in row.values()) or
            len({row["case_id"] for row in rows}) != len(rows) or
            len({row["record_id"] for row in rows}) != len(rows) or
            len({row["transition_id"] for row in rows}) != len(rows)):
        raise BatchLaneError("staging witness rows must be unique and complete")
    return hashlib.sha256(stable_dumps(sorted(rows, key=lambda row: row["case_id"])).encode()).hexdigest()


def validate_staging_import_witness(
        *, rows: Iterable[Mapping], staging_db: Path,
        campaign_id: str) -> list[dict]:
    """Replay selected external records against their staging DB witnesses.

    File hashes prove that a staging snapshot is unchanged, but not that it
    contains the exact records an authority selected.  This read-only replay
    binds each selected case to one transition whose provenance, canonical
    action/delta/verifier, lineage, learner membership and physical effect all
    match the external record.  Any ambiguity or missing witness fails closed.
    """
    if not campaign_id:
        raise BatchLaneError("staging witness campaign_id is required")
    selected = [dict(row) for row in rows]
    if not selected:
        raise BatchLaneError("staging witness selection is empty")
    case_ids = [str(row.get("case_id") or "").strip() for row in selected]
    if any(not case_id for case_id in case_ids) or len(set(case_ids)) != len(case_ids):
        raise BatchLaneError("staging witness case selection is invalid")
    record_ids: set[str] = set()
    try:
        conn = tehm_db.connect_read_only(Path(staging_db))
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise BatchLaneError("staging witness DB is not a readable TEHM snapshot") from exc
    try:
        transition_by_record: dict[str, list[sqlite3.Row]] = {}
        for transition in conn.execute(
                "SELECT * FROM tehm_transitions").fetchall():
            try:
                provenance = json.loads(transition["provenance_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(provenance, Mapping) and provenance.get("record_id"):
                transition_by_record.setdefault(
                    str(provenance["record_id"]), []).append(transition)
        witness = []
        for external in selected:
            raw_record = external.get("record")
            try:
                record = ExecutionRecord.from_dict(dict(raw_record))
            except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
                raise BatchLaneError(
                    f"staging witness record is malformed: {external.get('case_id')}") from exc
            record_id = str(record.record_id or "").strip()
            if not record_id or record_id in record_ids:
                raise BatchLaneError("staging witness record IDs are ambiguous")
            record_ids.add(record_id)
            if (external.get("split") != "support" or
                    external.get("classification") != "ELIGIBLE_POSITIVE" or
                    external.get("learner_eligible") is not True):
                raise BatchLaneError(
                    "staging witness external row is not importable: " +
                    str(external.get("case_id")))
            matches = transition_by_record.get(record_id, [])
            if len(matches) != 1:
                raise BatchLaneError(
                    "staging witness transition is missing or ambiguous: " + record_id)
            transition = matches[0]
            transition_id = str(transition["transition_id"])
            action = Action.from_dict(record.action)
            delta = ObservationDelta.from_dict(record.observation_delta)
            verifier = VerifierSnapshot.from_dict(record.verification)
            try:
                validate_persisted_execution_preflight(action.to_dict(),
                                                       verifier.to_dict())
            except ValueError as exc:
                raise BatchLaneError(
                    "staging witness routing preflight is invalid: " + str(exc)) from exc
            external_lineage = str(external.get("lineage_id") or "").strip()
            if external_lineage != str(record.lineage_id or "").strip():
                raise BatchLaneError(
                    "staging witness external lineage mismatch: " + record_id)
            if str(external.get("family") or "").strip() != action.transformation_family:
                raise BatchLaneError(
                    "staging witness external family mismatch: " + record_id)
            config = record.before.get("config") or record.after.get("config") or {}
            record_platform = str(
                config.get("PLATFORM") or config.get("platform") or "").strip()
            if (not record_platform or
                    str(external.get("platform") or "").strip() != record_platform):
                raise BatchLaneError(
                    "staging witness external platform mismatch: " + record_id)
            expected_fields = {
                "action_domain": action.domain,
                "action_json": stable_dumps(action.to_dict()),
                "observation_delta_json": stable_dumps(delta.to_dict()),
                "verifier_json": stable_dumps(verifier.to_dict()),
                "outcome": classify_outcome(delta, verifier),
            }
            if any(transition[field] != value
                   for field, value in expected_fields.items()):
                raise BatchLaneError(
                    "staging witness transition content mismatch: " + record_id)
            state = conn.execute(
                "SELECT lineage_id FROM tehm_states WHERE state_id=?",
                (transition["source_state_id"],)).fetchone()
            if state is None or str(state["lineage_id"] or "") != str(
                    record.lineage_id or ""):
                raise BatchLaneError(
                    "staging witness lineage mismatch: " + record_id)
            memberships = conn.execute(
                """SELECT split, learner_eligible
                     FROM tehm_dataset_membership
                    WHERE transition_id=? AND campaign_id=?""",
                (transition_id, campaign_id)).fetchall()
            membership_eligible = None
            if len(memberships) == 1:
                try:
                    membership_eligible = normalize_stored_learner_bool(
                        memberships[0]["learner_eligible"])
                except ValueError:
                    membership_eligible = None
            if (len(memberships) != 1 or memberships[0]["split"] != "training"
                    or membership_eligible is not True):
                raise BatchLaneError(
                    "staging witness is not training learner evidence: " + record_id)
            physical = conn.execute(
                "SELECT * FROM tehm_physical_effects WHERE transition_id=?",
                (transition_id,)).fetchone()
            if physical is None:
                raise BatchLaneError(
                    "staging witness physical effect is missing: " + record_id)
            before_ppa = (record.before.get("reports") or {}).get("ppa") or {}
            after_ppa = (record.after.get("reports") or {}).get("ppa") or {}
            expected_deltas = stable_dumps(extract_deltas(before_ppa, after_ppa))
            expected_effect_fields = {
                "action_domain": action.domain,
                "transformation_family": action.transformation_family,
                "effect_key": str(transition["primary_effect_key"] or ""),
                "before_ppa_json": stable_dumps(before_ppa),
                "after_ppa_json": stable_dumps(after_ppa),
                "deltas_json": expected_deltas,
                "evidence_refs_json": stable_dumps(
                    list(verifier.evidence_refs)),
            }
            if any(physical[field] != value
                   for field, value in expected_effect_fields.items()):
                raise BatchLaneError(
                    "staging witness physical effect mismatch: " + record_id)
            witness.append({"case_id": str(external["case_id"]).strip(),
                            "record_id": record_id,
                            "transition_id": transition_id})
        staging_witness_digest(witness)
        return sorted(witness, key=lambda item: item["case_id"])
    except (IndexError, sqlite3.Error) as exc:
        raise BatchLaneError(
            "staging witness DB is not a readable TEHM snapshot") from exc
    finally:
        conn.close()


def canonical_db_candidates() -> tuple[Path, ...]:
    """Resolve known canonical stores without opening any of them.

    The live development default and the immutable bundle pointer are both
    protected.  ``TEHM_CANONICAL_DB`` may name an operator-owned canonical
    store and is protected as well.
    """
    memory_root = Path(__file__).resolve().parents[1]
    paths = {Path(memory_root / "tehm.sqlite").resolve()}
    explicit = os.environ.get("TEHM_CANONICAL_DB")
    if explicit:
        paths.add(Path(explicit).expanduser().resolve())
    pointer = memory_root / "evaluation" / "canonical_freeze_pointer_v1.json"
    try:
        payload = json.loads(pointer.read_text())
        bundle = (pointer.parent / str(payload["canonical_bundle"])).resolve()
        paths.add((bundle / "closed_loop" / "tehm.sqlite").resolve())
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        # An unavailable pointer must not make an arbitrary destination look
        # canonical.  The live default remains protected above.
        pass
    return tuple(sorted(paths, key=str))


def require_staging_destination(db_path: Path, *, campaign_root: Path) -> Path:
    """Require a DB under ``<campaign_root>/staging`` and reject canonical DBs."""
    db_path = Path(db_path).expanduser().resolve()
    campaign_root = Path(campaign_root).expanduser().resolve()
    staging_root = (campaign_root / "staging").resolve()
    try:
        db_path.relative_to(staging_root)
    except ValueError as exc:
        raise BatchLaneError(
            f"staging DB must be below {staging_root}, got {db_path}") from exc
    if db_path in canonical_db_candidates():
        raise BatchLaneError(f"refusing canonical memory as batch staging DB: {db_path}")
    return db_path


def sqlite_snapshot(path: Path) -> dict:
    """Return a read-only digest/count snapshot without creating a database."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        return {"path": str(path), "exists": False, "sha256": None,
                "transitions": None, "physical_effects": None}
    digest = _sha(path)
    counts = {"transitions": None, "physical_effects": None}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            counts["transitions"] = conn.execute(
                "SELECT COUNT(*) FROM tehm_transitions").fetchone()[0]
            counts["physical_effects"] = conn.execute(
                "SELECT COUNT(*) FROM tehm_physical_effects").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return {"path": str(path), "exists": True, "sha256": digest, **counts}


def canonical_snapshots() -> list[dict]:
    return [sqlite_snapshot(path) for path in canonical_db_candidates()]


def assert_snapshots_unchanged(before: Iterable[Mapping], after: Iterable[Mapping]) -> None:
    """Fail when any protected canonical DB changed or appeared."""
    left = {str(row.get("path")): dict(row) for row in before}
    right = {str(row.get("path")): dict(row) for row in after}
    if left != right:
        raise BatchLaneError("protected canonical memory changed during batch observation")


def _validate_toolchain_binding(binding: Mapping | None, *,
                                run_meta: Mapping | None = None,
                                expected_orfs_root: Path | None = None) -> dict:
    """Replay the content-bound toolchain receipt without launching EDA.

    A receipt's paths and hashes are evidence, not authority by themselves:
    the current files, run metadata, ORFS root and derived fingerprint must
    still agree.  Returning reasons keeps the caller's external receipt
    auditable while the boolean remains a simple fail-closed admission gate.
    """
    result = {"valid": False, "reasons": []}
    if not isinstance(binding, Mapping):
        result["reasons"].append("toolchain binding is missing")
        return result
    status = binding.get("status")
    if status not in {"bound_internal", "bound_external"}:
        result["reasons"].append("toolchain binding status is not bound")
    root_value = binding.get("orfs_root")
    root = None
    if not isinstance(root_value, str) or not root_value.strip():
        result["reasons"].append("toolchain binding ORFS root is missing")
    else:
        root = Path(root_value).expanduser().resolve()
        if expected_orfs_root is not None and root != Path(expected_orfs_root).resolve():
            result["reasons"].append("toolchain binding ORFS root mismatch")
    tools = binding.get("tools")
    if not isinstance(tools, Mapping):
        result["reasons"].append("toolchain binding tools are missing")
        tools = {}
    for name, meta_key in (("openroad", "openroad_exe"),
                           ("yosys", "yosys_exe")):
        tool = tools.get(name)
        if not isinstance(tool, Mapping):
            result["reasons"].append(f"{name} binding is missing")
            continue
        path_value, digest = tool.get("path"), tool.get("sha256")
        if not isinstance(path_value, str) or not path_value.strip():
            result["reasons"].append(f"{name} path is missing")
            continue
        path = Path(path_value).expanduser().resolve()
        if not isinstance(digest, str) or not digest:
            result["reasons"].append(f"{name} digest is missing")
        elif _sha(path) != digest:
            result["reasons"].append(f"{name} binary digest changed")
        # ``bound_internal`` means the executable is inside the locked direct
        # toolchain bundle when one is recorded; older ORFS-native receipts
        # have no bundle root and therefore retain the historical ORFS-root
        # containment check.  Treating every internal binary as if it had to
        # live below the ORFS checkout incorrectly rejects the reproducible
        # user-owned bundle (and silently downgrades otherwise valid pairs).
        containment_root = root
        toolchain_root_value = binding.get("toolchain_root")
        if isinstance(toolchain_root_value, str) and toolchain_root_value.strip():
            containment_root = Path(toolchain_root_value).expanduser().resolve()
        if status == "bound_internal" and containment_root is not None:
            try:
                path.relative_to(containment_root)
            except ValueError:
                result["reasons"].append(
                    f"{name} escapes bound toolchain root")
        if isinstance(run_meta, Mapping) and run_meta.get(meta_key) != path_value:
            result["reasons"].append(f"run metadata {meta_key} mismatch")
    # Campaign receipts include the direct bundle root in the fingerprint so
    # two otherwise identical binaries cannot be rebound to a different user
    # prefix.  Preserve the legacy two-field replay for older external rows
    # which predate ``toolchain_root``.
    fingerprint_payload = {
        "orfs_root": str(root) if root is not None else None,
        "tools": dict(tools),
    }
    toolchain_root_value = binding.get("toolchain_root")
    if isinstance(toolchain_root_value, str) and toolchain_root_value.strip():
        fingerprint_payload["toolchain_root"] = str(
            Path(toolchain_root_value).expanduser().resolve())
    expected_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if binding.get("fingerprint") != expected_fingerprint:
        result["reasons"].append("toolchain fingerprint mismatch")
    result["valid"] = not result["reasons"]
    return result


def assess_full_oracle(project: Path, *, rtl_files: Iterable[Path],
                       expected_input_binding: Mapping | None = None,
                       expected_timing_contract: Mapping | None = None) -> dict:
    """Grade one preserved ORFS project against the complete Batch-0 oracle."""
    project = Path(project).resolve()
    runs = sorted((project / "backend").glob("RUN_*"))
    run = runs[-1] if runs else None
    reports = project / "reports"
    stage_log = _jsonl(run / "stage_log.jsonl") if run else []
    stages = {str(row.get("stage")): row.get("status") for row in stage_log
              if isinstance(row, Mapping) and row.get("stage")}
    route = _json(reports / "route.json")
    timing = _json(reports / "timing_check.json")
    drc = _json(reports / "drc.json")
    lvs = _json(reports / "lvs.json")
    ppa = _json(reports / "ppa.json")
    equivalence = _json(reports / "equivalence.json")
    strict_signoff = _json(reports / "strict_signoff.json")
    persisted_graph = _json(reports / "batch_graph_context.json")
    campaign_receipt = _json(project / "campaign-run-receipt.json")
    toolchain_binding = campaign_receipt.get("toolchain_binding") or {}
    run_meta = _json(run / "run-meta.json") if run else {}
    actual_input_binding = _input_binding(project, rtl_files)
    input_binding_ok = (
        expected_input_binding is None or
        _input_binding_matches(actual_input_binding, expected_input_binding)
    )
    actual_timing_contract = _timing_contract(project)
    timing_contract_ok = (
        expected_timing_contract is None or
        _timing_contract_matches(actual_timing_contract, expected_timing_contract)
    )

    final_def = _newest(run / "final", "*.def") if run else None
    final_gds = _newest(run / "final", "*.gds") if run else None
    graph_detail = {"status": "missing"}
    if final_def:
        try:
            context = load_defgraph_context(project, def_path=final_def)
            graph_detail = context.to_dict()
        except (OSError, TypeError, ValueError) as exc:
            graph_detail = {"status": "invalid", "reason": str(exc)}

    ppa_metrics = _core_ppa_metrics(ppa)
    evidence_paths = [
        project / "constraints" / "config.mk",
        project / "constraints" / "constraint.sdc",
    ]
    evidence_paths.extend(Path(path).resolve() for path in rtl_files)
    if run:
        evidence_paths.extend(run / name for name in ("run-meta.json", "stage_log.jsonl"))
    evidence_paths.extend(reports / name for name in (
        "equivalence.json", "route.json", "timing_check.json", "drc.json",
            "lvs.json", "ppa.json", "strict_signoff.json", "features_stats.json"))
    evidence_paths.append(reports / "batch_graph_context.json")
    evidence_paths.append(project / "campaign-run-receipt.json")
    if final_def and final_gds:
        evidence_paths.extend((final_def, final_gds))
    evidence_refs = [_evidence_ref(path) for path in evidence_paths if path.is_file()]
    required_paths_present = all(path.is_file() for path in evidence_paths)

    checks = {
        "synthesis": _stage_passed(stages.get("synth")),
        "equivalence": equivalence.get("verdict") == "PASS",
        "route": (_stage_passed(stages.get("route")) and
                  route.get("status") in {"clean", "complete", "pass"}),
        "finish": (_stage_passed(stages.get("finish")) and
                   final_def is not None and final_gds is not None),
        "timing": (timing.get("tier") == "clean" or
                   timing.get("status") in {"clean", "met"}),
        # Batch-0 is full-oracle. Qualified BEOL-only DRC is useful external
        # evidence but is not a strict positive experience.
        "drc": drc.get("status") == "clean",
        "lvs": lvs.get("status") == "clean",
        # The individual DRC/LVS/RCX reports are necessary but not sufficient:
        # strict signoff is the aggregate receipt emitted by the same bounded
        # checker run and its absence must keep the pair external-only.  This
        # closes the gap where hand-carried component reports could otherwise
        # look complete without an executed strict gate.
        "strict_signoff": strict_signoff.get("status") == "pass",
        "ppa": all(value is not None for value in ppa_metrics.values()),
        "graph": (graph_detail.get("status") == "complete" and
                  bool(graph_detail.get("digest")) and
                  persisted_graph.get("digest") == graph_detail.get("digest")),
        "artifact_digest": required_paths_present and all(
            ref.get("sha256") for ref in evidence_refs),
        # A complete physical result must identify the exact ORFS/tool binary
        # binding used to produce it.  Historical attempts without this receipt
        # remain useful diagnostics but can never become learner evidence.
        "toolchain_binding": _validate_toolchain_binding(
            toolchain_binding, run_meta=run_meta).get("valid", False),
        # The executor binds the exact config/SDC/RTL bytes at prepare time.
        # A later mutation remains external-only even if reports still look
        # clean.
        "input_binding": input_binding_ok,
        "timing_contract": timing_contract_ok,
    }
    return {
        "version": BATCH_LANE_VERSION,
        "project": str(project),
        "run_tag": run.name if run else None,
        "checks": checks,
        "complete": all(checks.values()),
        "missing_oracles": [name for name in REQUIRED_ORACLES if not checks[name]],
        "missing_gates": [name for name, passed in checks.items() if not passed],
        "ppa_metrics": ppa_metrics,
        "strict_signoff_status": strict_signoff.get("status"),
        "graph": graph_detail,
        "evidence_refs": evidence_refs,
        "input_binding": {
            "expected": (dict(expected_input_binding)
                          if isinstance(expected_input_binding, Mapping) else None),
            "actual": actual_input_binding,
        },
        "timing_contract": {
            "expected": (dict(expected_timing_contract)
                          if isinstance(expected_timing_contract, Mapping) else None),
            "actual": actual_timing_contract,
        },
    }


def build_external_observation(item: Mapping) -> dict:
    """Create one content-addressed external pair receipt.

    The receipt is useful even when the run failed or is incomplete.  Only a
    complete support receipt carries an importable canonical record.
    """
    split = str(item.get("split") or "")
    if split not in OBSERVATION_SPLITS:
        raise BatchLaneError(f"invalid observation split: {split!r}")
    rtl_files = [Path(path).resolve() for path in item.get("rtl_files") or ()]
    if not rtl_files:
        raise BatchLaneError(f"case {item.get('case_id')} has no RTL files")
    bindings = item.get("input_bindings")
    if not isinstance(bindings, Mapping):
        bindings = {}
    expected_before = bindings.get("before")
    expected_after = bindings.get("after")
    timing_contracts = item.get("timing_contract")
    if not isinstance(timing_contracts, Mapping):
        timing_contracts = {}
    expected_timing_before = timing_contracts.get("before")
    expected_timing_after = timing_contracts.get("after")
    before = assess_full_oracle(
        Path(str(item["before_project"])), rtl_files=rtl_files,
        expected_input_binding=(expected_before
                                if isinstance(expected_before, Mapping) else {}),
        expected_timing_contract=(expected_timing_before
                                  if isinstance(expected_timing_before, Mapping) else {}))
    after = assess_full_oracle(
        Path(str(item["after_project"])), rtl_files=rtl_files,
        expected_input_binding=(expected_after
                                if isinstance(expected_after, Mapping) else {}),
        expected_timing_contract=(expected_timing_after
                                  if isinstance(expected_timing_after, Mapping) else {}))
    complete = bool(before["complete"] and after["complete"])
    config_edits = dict(item["config_edits"])
    execution_preflight = None
    if ROUTING_LAYER_ADJUSTMENT in config_edits:
        before_project = Path(str(item["before_project"]))
        execution_preflight = inspect_routing_layer_adjustment(
            item.get("platform", ""), config_edits,
            config=parse_orfs_config(
                before_project / "constraints" / "config.mk"),
            project_dir=before_project,
            orfs_root=item.get("orfs_root"))
        execution_preflight["digest"] = preflight_digest(execution_preflight)
        # A no-op or unavailable real hook is not a demonstrated intervention.
        # Preserve the row for audit, but make it ineligible before the receipt
        # is written so downstream consumers cannot mistake it for support.
        if execution_preflight["status"] in {"NO_OP", "UNKNOWN"}:
            complete = False
    record_dict = None
    record_error = None
    try:
        record = build_orfs_pair_record(
            Path(str(item["before_project"])), Path(str(item["after_project"])),
            lineage_id=str(item["lineage_id"]), target_check=str(item.get("check") or "route"),
            config_edits=config_edits,
            transformation_family=str(item["family"]),
        )
        record_dict = asdict(record)
        if execution_preflight is not None:
            record_dict["verification"]["execution_preflight"] = execution_preflight
        complete = bool(
            complete and record.verification.get("verdict") == "PASS" and
            record.verification.get("obligation_coverage") == 1.0 and
            not record.observation_delta.get("created_regressions"))
    except (OSError, TypeError, ValueError) as exc:
        record_error = str(exc)
        complete = False

    classification = "ELIGIBLE_POSITIVE" if complete else "INCOMPLETE_EXTERNAL_ONLY"
    payload = {
        "version": EXTERNAL_RECEIPT_VERSION,
        "case_id": str(item["case_id"]),
        "lineage_id": str(item["lineage_id"]),
        "platform": str(item["platform"]),
        "family": str(item["family"]),
        "split": split,
        "action": {"config_edits": config_edits},
        "before": before,
        "after": after,
        "classification": classification,
        "learner_eligible": bool(complete and split == "support"),
        "record": record_dict,
        "record_error": record_error,
        "execution_preflight": execution_preflight,
        "execution_preflight_blocked": bool(
            execution_preflight and execution_preflight.get("status")
            in {"NO_OP", "UNKNOWN"}),
        "canonical_memory_mutation": "none",
        "promotion_eligible": False,
    }
    receipt_id = "orfs-observation:" + hashlib.sha256(
        stable_dumps(payload).encode()).hexdigest()[:24]
    return {"receipt_id": receipt_id, **payload}


def write_external_observations(path: Path, observations: Iterable[Mapping]) -> dict:
    """Write deterministic hash-chained JSONL receipts."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    validated = []
    for raw in observations:
        observation = dict(raw)
        try:
            learner_eligible = require_learner_bool(
                observation.get("learner_eligible"),
                field="external learner_eligible")
        except ValueError as exc:
            raise BatchLaneError(
                "external observation learner_eligible must be boolean") from exc
        split = observation.get("split")
        if split is not None and (type(split) is not str or
                                  split not in OBSERVATION_SPLITS):
            raise BatchLaneError("external observation split is invalid")
        # Keep the writer permissive about a contradictory split so the
        # resulting hash-bound receipt can be audited and rejected by the
        # staging/authority reader; only the typed flag is normalized here.
        observation["learner_eligible"] = learner_eligible
        validated.append(observation)
    rows = []
    previous = None
    for index, observation in enumerate(sorted(
            # Re-chaining a previously persisted observation set is a normal
            # authority operation.  The prior top-level receipt belongs to
            # the old chain and must not remain inside the new digest body.
            ({key: value for key, value in observation.items()
              if key != "receipt_sha256"} for observation in validated),
            key=lambda row: row["case_id"])):
        body = {**observation, "sequence": index, "previous_sha256": previous}
        digest = hashlib.sha256(stable_dumps(body).encode()).hexdigest()
        rows.append({**body, "receipt_sha256": digest})
        previous = digest
    data = "".join(stable_dumps(row) + "\n" for row in rows).encode()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return {
        "version": EXTERNAL_RECEIPT_VERSION,
        "path": str(path),
        "count": len(rows),
        "sha256": hashlib.sha256(data).hexdigest(),
        "chain_head": previous,
        "eligible_positive": sum(row["classification"] == "ELIGIBLE_POSITIVE" for row in rows),
        "learner_eligible": sum(bool(row["learner_eligible"]) for row in rows),
    }


def read_external_observations(path: Path) -> list[dict]:
    rows = _jsonl(Path(path))
    previous = None
    seen_cases: set[str] = set()
    for index, row in enumerate(rows):
        digest = row.get("receipt_sha256")
        body = {key: value for key, value in row.items() if key != "receipt_sha256"}
        if row.get("sequence") != index or row.get("previous_sha256") != previous:
            raise BatchLaneError("external observation chain order is invalid")
        actual = hashlib.sha256(stable_dumps(body).encode()).hexdigest()
        if digest != actual:
            raise BatchLaneError("external observation receipt digest mismatch")
        case_id = str(row.get("case_id") or "").strip()
        if not case_id:
            raise BatchLaneError("external observation case_id is required")
        try:
            learner_eligible = require_learner_bool(
                row.get("learner_eligible"), field="external learner_eligible")
        except ValueError as exc:
            raise BatchLaneError(
                "external observation learner_eligible must be boolean") from exc
        split = row.get("split")
        # Legacy diagnostic receipts may omit split when they are explicitly
        # non-learner.  Any present split is still typed, and a learner row
        # must identify the support partition so it cannot enter staging via a
        # truthy string or a contradictory held-out label.
        if split is not None and (type(split) is not str or
                                  split not in OBSERVATION_SPLITS):
            raise BatchLaneError("external observation split is invalid")
        if learner_eligible and split != "support":
            raise BatchLaneError(
                "external observation learner firewall: learner evidence must use support split")
        if case_id in seen_cases:
            raise BatchLaneError(
                "external observation chain contains duplicate case_id: " + case_id)
        seen_cases.add(case_id)
        previous = digest
    return rows


def import_support_to_staging(*, observations_path: Path, staging_db: Path,
                              staging_artifacts: Path, campaign_root: Path,
                              campaign_id: str) -> dict:
    """Import only complete support receipts into an isolated staging store."""
    staging_db = require_staging_destination(staging_db, campaign_root=campaign_root)
    staging_artifacts = Path(staging_artifacts).resolve()
    staging_root = (Path(campaign_root).resolve() / "staging")
    try:
        staging_artifacts.relative_to(staging_root)
    except ValueError as exc:
        raise BatchLaneError("staging artifacts must remain under campaign staging root") from exc
    before_canonical = canonical_snapshots()
    rows = read_external_observations(observations_path)
    conn = tehm_db.connect(staging_db)
    tehm_db.ensure_schema(conn)
    store = ArtifactStore(staging_artifacts)
    physical = PhysicalEffectMemory(conn)
    imported = []
    savepoint = "tehm_batch_staging_import_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    savepoint_active = True
    try:
        for row in rows:
            if not row.get("learner_eligible"):
                continue
            if row.get("classification") != "ELIGIBLE_POSITIVE" or not row.get("record"):
                raise BatchLaneError("learner-eligible receipt lacks complete positive evidence")
            record = ExecutionRecord.from_dict(dict(row["record"]))
            try:
                validate_persisted_execution_preflight(
                    record.action, record.verification)
            except ValueError as exc:
                raise BatchLaneError(
                    "learner support routing preflight is invalid: " + str(exc)) from exc
            receipt = capture(
                conn, store, record, dataset_campaign_id=campaign_id,
                dataset_split="training", dataset_learner_eligible=True)
            physical.record(
                transition_id=receipt.transition_id,
                action_domain=record.action["domain"],
                transformation_family=record.action["transformation_family"],
                before_ppa=record.before.get("reports", {}).get("ppa") or {},
                after_ppa=record.after.get("reports", {}).get("ppa") or {},
                effect_key=receipt.primary_effect_key,
                evidence_refs=record.verification.get("evidence_refs"),
                # The source-arm context is part of the already hash-bound
                # full-oracle observation.  Persist it with the empirical
                # effect so graph support cannot be fabricated from an empty
                # placeholder or require an out-of-band backfill.
                graph_context=(row.get("before") or {}).get("graph"),
                commit=False,
            )
            imported.append({"case_id": row["case_id"],
                             "transition_id": receipt.transition_id})
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        savepoint_active = False
        conn.commit()
    except Exception:
        if savepoint_active:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    finally:
        conn.close()
    after_canonical = canonical_snapshots()
    assert_snapshots_unchanged(before_canonical, after_canonical)
    return {
        "version": BATCH_LANE_VERSION,
        "destination": "staging",
        "staging_db": sqlite_snapshot(staging_db),
        "imported": imported,
        "excluded_external_only": len(rows) - len(imported),
        "canonical_before": before_canonical,
        "canonical_after": after_canonical,
        "canonical_memory_mutation": "none",
    }


def import_audit_to_staging(*, observations_path: Path, staging_db: Path,
                            staging_artifacts: Path, campaign_root: Path,
                            campaign_id: str) -> dict:
    """Import calibration/held-out/A-B observations into audit-only staging.

    This is deliberately separate from :func:`import_support_to_staging`:
    non-training evidence may be retained as an immutable transition witness,
    but it must be captured with its declared ``calibration``/``heldout``/``ab``
    split and ``learner_eligible=0``.  The function never imports support rows,
    writes canonical memory, or changes rule lifecycle.  Incomplete external
    rows without a valid record remain excluded; complete rows can later be
    selected by the DB-bound authority projector.
    """
    staging_db = require_staging_destination(staging_db, campaign_root=campaign_root)
    staging_artifacts = Path(staging_artifacts).resolve()
    staging_root = (Path(campaign_root).resolve() / "staging")
    try:
        staging_artifacts.relative_to(staging_root)
    except ValueError as exc:
        raise BatchLaneError(
            "staging artifacts must remain under campaign staging root") from exc
    before_canonical = canonical_snapshots()
    rows = read_external_observations(observations_path)
    conn = tehm_db.connect(staging_db)
    tehm_db.ensure_schema(conn)
    store = ArtifactStore(staging_artifacts)
    physical = PhysicalEffectMemory(conn)
    imported = []
    excluded = []
    skipped_support = []
    savepoint = "tehm_batch_audit_staging_import_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    savepoint_active = True
    try:
        for row in rows:
            split = str(row.get("split") or "")
            if split == "support":
                skipped_support.append(str(row.get("case_id") or ""))
                continue
            if split not in {"calibration", "heldout", "ab"}:
                raise BatchLaneError("audit staging received an invalid split")
            if row.get("learner_eligible") is not False:
                raise BatchLaneError(
                    "audit staging observation violates learner firewall")
            if row.get("classification") not in {
                    "ELIGIBLE_POSITIVE", "INCOMPLETE_EXTERNAL_ONLY"}:
                raise BatchLaneError("audit staging observation classification is invalid")
            if not row.get("record"):
                excluded.append({"case_id": row.get("case_id"),
                                 "reason": "record_missing"})
                continue
            try:
                record = ExecutionRecord.from_dict(dict(row["record"]))
            except (TypeError, ValueError) as exc:
                raise BatchLaneError(
                    f"audit staging record is malformed: {row.get('case_id')}") from exc
            receipt = capture(
                conn, store, record, dataset_campaign_id=campaign_id,
                dataset_split=split, dataset_learner_eligible=False)
            physical.record(
                transition_id=receipt.transition_id,
                action_domain=record.action["domain"],
                transformation_family=record.action["transformation_family"],
                before_ppa=record.before.get("reports", {}).get("ppa") or {},
                after_ppa=record.after.get("reports", {}).get("ppa") or {},
                effect_key=receipt.primary_effect_key,
                evidence_refs=record.verification.get("evidence_refs"),
                graph_context=(row.get("before") or {}).get("graph"),
                commit=False,
            )
            imported.append({
                "case_id": row["case_id"], "transition_id": receipt.transition_id,
                "split": split, "classification": row["classification"],
                "learner_eligible": False,
            })
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        savepoint_active = False
        conn.commit()
    except Exception:
        if savepoint_active:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    finally:
        conn.close()
    after_canonical = canonical_snapshots()
    assert_snapshots_unchanged(before_canonical, after_canonical)
    return {
        "version": BATCH_LANE_VERSION,
        "destination": "staging",
        "staging_db": sqlite_snapshot(staging_db),
        "imported": imported,
        "excluded_external_only": excluded,
        "skipped_support_case_ids": sorted(skipped_support),
        "canonical_before": before_canonical,
        "canonical_after": after_canonical,
        "canonical_memory_mutation": "none",
    }


def validate_canonical_import_authority(
        authority: Mapping, *, observations_path: Path, staging_db: Path,
        canonical_db: Path, campaign_id: str | None = None) -> None:
    """Validate the independent authority receipt required for canonical import."""
    if not isinstance(authority, Mapping):
        raise BatchLaneError("canonical import authority must be a mapping")
    if authority.get("version") != CANONICAL_IMPORT_AUTHORITY_VERSION:
        raise BatchLaneError("canonical import authority version mismatch")
    if authority.get("decision") != "ALLOW_CANONICAL_IMPORT":
        raise BatchLaneError("canonical import is not authorized")
    gates = authority.get("promotion_gates") or {}
    if any(gates.get(name) is not True for name in PROMOTION_GATES):
        raise BatchLaneError("canonical import promotion gates are incomplete")
    gate_evaluation = authority.get("gate_evaluation")
    evaluated_checks = (gate_evaluation.get("checks")
                        if isinstance(gate_evaluation, Mapping) else None)
    if (not isinstance(gate_evaluation, Mapping) or
            not isinstance(evaluated_checks, Mapping) or
            any(evaluated_checks.get(name) is not gates.get(name)
                for name in PROMOTION_GATES) or
            gate_evaluation.get("eligible") is not True or
            gate_evaluation.get("all_gates_established") is not True):
        raise BatchLaneError(
            "canonical import authority gate evaluation is incomplete")
    if authority.get("promotion_attempted") is not False:
        raise BatchLaneError("canonical import authority must be pre-promotion")
    if authority.get("canonical_memory_mutation", "none") != "none":
        raise BatchLaneError(
            "canonical import authority has prior canonical mutation")
    if campaign_id is not None and authority.get("campaign_id") != campaign_id:
        raise BatchLaneError("canonical import authority campaign mismatch")
    try:
        selection_digest = canonical_case_selection_digest(authority["case_ids"])
    except (KeyError, TypeError, BatchLaneError) as exc:
        raise BatchLaneError(
            "canonical import authority case selection is invalid") from exc
    bindings = authority.get("bindings") or {}
    if not isinstance(bindings, Mapping):
        raise BatchLaneError("canonical import authority bindings are malformed")
    expected = {
        "observations_sha256": _sha(Path(observations_path)),
        "staging_db_sha256": _sha(Path(staging_db)),
        "canonical_db_sha256_before": _sha(Path(canonical_db)),
        "case_selection_sha256": selection_digest,
    }
    if any(bindings.get(key) != value for key, value in expected.items()):
        raise BatchLaneError("canonical import authority is not bound to current evidence")
    rows = read_external_observations(Path(observations_path))
    if ("observation_count" in authority and
            authority.get("observation_count") != len(rows)):
        raise BatchLaneError("canonical import authority observation count mismatch")
    selected_ids = {str(case_id).strip() for case_id in authority["case_ids"]}
    selected = [row for row in rows
                if str(row.get("case_id") or "").strip() in selected_ids]
    if len(selected) != len(selected_ids):
        raise BatchLaneError("canonical import authority selects unknown case_ids")
    bound_campaign = str(campaign_id or authority.get("campaign_id") or "").strip()
    if not bound_campaign:
        raise BatchLaneError("canonical import authority campaign is required")
    try:
        witness = validate_staging_import_witness(
            rows=selected, staging_db=Path(staging_db), campaign_id=bound_campaign)
        witness_digest = staging_witness_digest(witness)
    except BatchLaneError as exc:
        raise BatchLaneError(
            "canonical import staging witness is invalid") from exc
    if bindings.get("staging_witness_sha256") != witness_digest:
        raise BatchLaneError("canonical import authority is not bound to staging witnesses")


def import_support_to_canonical(*, observations_path: Path, staging_db: Path,
                                canonical_db: Path, canonical_artifacts: Path,
                                campaign_id: str, authority: Mapping) -> dict:
    """Authority-gated canonical import; never called by the batch runner."""
    observations_path = Path(observations_path).resolve()
    staging_db = Path(staging_db).resolve()
    canonical_db = Path(canonical_db).resolve()
    validate_canonical_import_authority(
        authority, observations_path=observations_path,
        staging_db=staging_db, canonical_db=canonical_db,
        campaign_id=campaign_id)
    rows = read_external_observations(observations_path)
    allowed = set(authority.get("case_ids") or ())
    if not allowed:
        raise BatchLaneError("canonical import authority contains no case_ids")
    known_case_ids = {str(row.get("case_id")) for row in rows}
    unknown = sorted(allowed - known_case_ids)
    if unknown:
        raise BatchLaneError(
            "canonical import authority selects unknown case_ids: " + ",".join(unknown))
    selected = [row for row in rows if row.get("case_id") in allowed]
    if not selected:
        raise BatchLaneError("canonical import authority selected no observations")
    conn = tehm_db.connect(canonical_db)
    tehm_db.ensure_schema(conn)
    store = ArtifactStore(Path(canonical_artifacts).resolve())
    physical = PhysicalEffectMemory(conn)
    imported = []
    savepoint = "tehm_batch_canonical_import_v1"
    conn.execute(f"SAVEPOINT {savepoint}")
    savepoint_active = True
    try:
        for row in rows:
            if row["case_id"] not in allowed:
                continue
            if (row.get("split") != "support" or
                    row.get("classification") != "ELIGIBLE_POSITIVE" or
                    row.get("learner_eligible") is not True or
                    not row.get("record")):
                raise BatchLaneError("authority selected a non-importable observation")
            record = ExecutionRecord.from_dict(dict(row["record"]))
            try:
                validate_persisted_execution_preflight(
                    record.action, record.verification)
            except ValueError as exc:
                raise BatchLaneError(
                    "canonical import routing preflight is invalid: " + str(exc)) from exc
            receipt = capture(
                conn, store, record, dataset_campaign_id=campaign_id,
                dataset_split="training", dataset_learner_eligible=True)
            physical.record(
                transition_id=receipt.transition_id,
                action_domain=record.action["domain"],
                transformation_family=record.action["transformation_family"],
                before_ppa=record.before.get("reports", {}).get("ppa") or {},
                after_ppa=record.after.get("reports", {}).get("ppa") or {},
                effect_key=receipt.primary_effect_key,
                evidence_refs=record.verification.get("evidence_refs"),
                graph_context=(row.get("before") or {}).get("graph"),
                commit=False,
            )
            imported.append({"case_id": row["case_id"],
                             "transition_id": receipt.transition_id})
        # The authority validates immutable snapshots before opening the
        # destination.  Recheck both source files while the canonical
        # savepoint is still uncommitted, so a concurrent rewrite cannot leave
        # a partially authorized import behind.
        bindings = authority.get("bindings")
        if not isinstance(bindings, Mapping):
            raise BatchLaneError(
                "canonical import authority bindings are required for TOCTOU recheck")
        bound_observation_sha = bindings.get("observations_sha256")
        bound_staging_sha = bindings.get("staging_db_sha256")
        if not bound_observation_sha or not bound_staging_sha:
            raise BatchLaneError(
                "canonical import authority snapshot bindings are incomplete")
        if _sha(observations_path) != bound_observation_sha:
            raise BatchLaneError(
                "external observation chain changed during canonical import")
        if _sha(staging_db) != bound_staging_sha:
            raise BatchLaneError("staging DB changed during canonical import")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        savepoint_active = False
        conn.commit()
    except Exception:
        if savepoint_active:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    finally:
        conn.close()
    return {"version": BATCH_LANE_VERSION, "destination": "canonical",
            "imported": imported, "authority_digest": hashlib.sha256(
                stable_dumps(dict(authority)).encode()).hexdigest()}


def _stage_passed(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 0
    return str(value).lower() in {"0", "pass", "passed", "ok", "success", "complete"}


def _core_ppa_metrics(payload: Mapping) -> dict:
    aliases = {
        "wns_ns": ("wns_ns", "setup_wns", "finish__timing__setup__ws"),
        "tns_ns": ("tns_ns", "setup_tns", "finish__timing__setup__tns"),
        "area_um2": ("area_um2", "design_area_um2", "die_area_um2",
                      "finish__design__instance__area"),
        "power_w": ("power_w", "total_power_w", "total", "finish__power__total"),
    }
    leaves = {}
    stack = [payload]
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            for key, child in value.items():
                if isinstance(child, Mapping):
                    stack.append(child)
                else:
                    leaves.setdefault(str(key), child)
    result = {}
    for metric, names in aliases.items():
        value = next((leaves.get(name) for name in names if leaves.get(name) is not None), None)
        try:
            result[metric] = float(value) if value is not None else None
        except (TypeError, ValueError):
            result[metric] = None
    return result


def _json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _jsonl(path: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in Path(path).read_text().splitlines()
                if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _rtl_source_digest(rtl_files: Iterable[Path]) -> str | None:
    """Hash the exact ordered RTL byte stream used by the campaign."""
    try:
        paths = sorted((Path(path).resolve() for path in rtl_files), key=str)
        return hashlib.sha256(b"".join(path.read_bytes() for path in paths)).hexdigest()
    except OSError:
        return None


def _input_binding(project: Path, rtl_files: Iterable[Path]) -> dict:
    """Return the prepare-time identity of all flow inputs."""
    project = Path(project).resolve()
    return {
        "config_sha256": _sha(project / "constraints" / "config.mk"),
        "sdc_sha256": _sha(project / "constraints" / "constraint.sdc"),
        "source_digest": _rtl_source_digest(rtl_files),
    }


def _timing_contract(project: Path) -> dict:
    """Extract the fixed clock target bound to a prepared SDC."""
    path = Path(project).resolve() / "constraints" / "constraint.sdc"
    period = None
    try:
        text = path.read_text()
    except OSError:
        text = ""
    matches = re.findall(
        r"(?m)^\s*set\s+clk_period\s+([0-9]+(?:\.[0-9]+)?)\s*$", text)
    if len(matches) == 1:
        try:
            period = float(matches[0])
        except ValueError:
            period = None
    return {"clock_period_ns": period, "sdc_sha256": _sha(path)}


def _timing_contract_matches(actual: Mapping, expected: Mapping) -> bool:
    return (
        expected.get("clock_period_ns") is not None
        and actual.get("clock_period_ns") == expected.get("clock_period_ns")
        and expected.get("sdc_sha256") is not None
        and actual.get("sdc_sha256") == expected.get("sdc_sha256")
    )


def _input_binding_matches(actual: Mapping, expected: Mapping) -> bool:
    required = ("config_sha256", "sdc_sha256", "source_digest")
    return all(
        expected.get(name) is not None and actual.get(name) == expected.get(name)
        for name in required
    )


def _newest(root: Path, pattern: str) -> Path | None:
    paths = sorted(Path(root).glob(pattern)) if Path(root).is_dir() else []
    return paths[-1] if paths else None


def _evidence_ref(path: Path) -> dict:
    data = Path(path).read_bytes()
    return {"path": str(Path(path).resolve()),
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
