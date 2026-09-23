"""Source-replayed flow-feasibility records, without learner admission.

The declared oracle measures flow completion, not complete physical signoff.
No legacy aggregate record is relabeled and no database is written here.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from tehm.adapters.orfs_terminal_failure import (
    FLOW_CONTRACT, _digest, replay_locked_flow_feasibility_pair,
    terminal_run_file_bindings,
)
from tehm.adapters.r2g_evidence import parse_config_mk
from tehm.canonical.capture import ExecutionRecord


def build_flow_feasibility_record(
        before: Path, after: Path, *, lineage_id: str, before_pin: str,
        after_pin: str, config_edits: dict, toolchain_manifest: str | Path,
        expected_manifest_digest: str, role: str = "treatment") -> ExecutionRecord:
    """Build an explicit scoped observation from freshly replayed raw arms.

    Pins must be supplied from the frozen acquisition record. The caller owns
    lineage and dataset partition assignment; neither follows from a PASS.
    """
    if type(lineage_id) is not str or not lineage_id.strip():
        raise ValueError("flow feasibility record requires a lineage")
    if type(role) is not str or role not in {"treatment", "control"}:
        raise ValueError("flow feasibility role must be treatment or control")
    before, after = Path(before).resolve(), Path(after).resolve()
    kwargs = dict(before_pin=before_pin, after_pin=after_pin,
                  config_edits=dict(config_edits),
                  toolchain_manifest=str(Path(toolchain_manifest).resolve()),
                  expected_manifest_digest=expected_manifest_digest)
    pair = replay_locked_flow_feasibility_pair(before, after, **kwargs)
    states, refs = [], []
    for side, project in (("before", before), ("after", after)):
        registration = json.loads((project / "terminal-preregistration.json").read_text())
        files = terminal_run_file_bindings(project)
        refs.extend(item["path"] for item in files)
        states.append({
            "config": parse_config_mk((project / "constraints/config.mk").read_text()),
            "reports": {"flow_feasibility": {
                "scope": FLOW_CONTRACT["scope"], "verdict": pair[side]["verdict"],
                "downstream_checks": pair[side]["downstream_checks"],
            }},
            "artifacts": {"flow_feasibility": {
                "project": str(project), "run_tag": pair[side]["run_tag"],
                "registration": registration, "run_files": files,
                "receipt_digest": pair[side]["receipt_digest"],
            }},
        })
    # State construction must not race source changes after initial replay.
    if replay_locked_flow_feasibility_pair(before, after, **kwargs) != pair:
        raise ValueError("flow measurement changed during record construction")
    return _record_from_replayed_flow_states(
        before, after, lineage_id=lineage_id, kwargs=kwargs, pair=pair,
        states=states, refs=refs, role=role,
    )


def _record_from_replayed_flow_states(before, after, *, lineage_id: str,
                                      kwargs: dict, pair: dict, states: list,
                                      refs: list, role: str) -> ExecutionRecord:
    """Construct canonical content without changing logical origin identities."""
    before_verdict, after_verdict = pair["before"]["verdict"], pair["after"]["verdict"]
    original = ("REMOVED" if (before_verdict, after_verdict) == ("FAIL", "PASS") else
                "PRESENT" if before_verdict == "FAIL" and after_verdict == "FAIL" else "UNKNOWN")
    family = ("DENSITY_RELIEF" if float(states[1]["config"]["CORE_UTILIZATION"]) <
              float(states[0]["config"]["CORE_UTILIZATION"]) else "DENSITY_INCREASE")
    scoped = {"version": "orfs-scoped-record-v1", "role": "after" if role == "treatment" else "before",
              "before_project": str(before), "after_project": str(after),
              **kwargs, "pair_receipt": pair}
    identity = _digest({"scoped_execution": scoped, "lineage_id": lineage_id})
    if role == "control":
        # This is the observed baseline run, not a newly executed no-op arm.
        # Both endpoints carry baseline evidence and its own measured verdict.
        states[1] = deepcopy(states[0])
        after_verdict = before_verdict
        original = "PRESENT" if before_verdict == "FAIL" else "UNKNOWN"
        refs = [item["path"] for item in states[0]["artifacts"]["flow_feasibility"]["run_files"]]
    record = ExecutionRecord(
        record_id="orfs-scoped:" + identity.removeprefix("sha256:"),
        domain="flow.signoff", project_id=lineage_id,
        design_id=states[0]["config"]["DESIGN_NAME"], lineage_id=lineage_id,
        before=states[0], after=states[1],
        action={"domain": "flow.CONFIG_DELTA" if role == "treatment" else "flow.BASELINE_CONTROL",
                "transformation_family": family,
                "payload": {"config_edits": dict(kwargs["config_edits"]) if role == "treatment" else {},
                            **({"control": True, "observation_only": True} if role == "control" else {}),
                            "recheck": "flow_feasibility",
                            "measurement_contract_digest": pair["contract_digest"]}},
        observation_delta={
            "original_failure": original,
            "failing_tests": {side: (0 if verdict == "PASS" else 1 if verdict == "FAIL" else None)
                              for side, verdict in (("before", before_verdict), ("after", after_verdict))},
            "created_regressions": (["flow_feasibility"] if
                                    (before_verdict, after_verdict) == ("PASS", "FAIL") else []),
            "newly_observed_failures": [],
            "experiment_kind": "REPAIR" if role == "treatment" and original in {"REMOVED", "PRESENT"} else "OBSERVATION",
            "utility_verdict": "UNKNOWN",
        },
        verification={"verdict": after_verdict, "oracle_type": "TARGET_TEST",
                      "scope": "flow_feasibility", "confidence_tier": "T",
                      "oracle_complete": pair["controlled_measurement_valid"],
                      "obligation_coverage": 1.0 if pair["controlled_measurement_valid"] else None,
                      "evidence_refs": refs, "scoped_execution": scoped},
    )
    record.validate()
    return record


def replay_flow_feasibility_record(record: ExecutionRecord) -> dict:
    """Rebuild action, both states, delta and verifier; reject any substitution.

    This is a pre-capture integrity check, not a learner admission gate.
    """
    record.validate()
    scoped = record.verification.get("scoped_execution")
    if isinstance(scoped, dict) and scoped.get("version") == "orfs-rc1-seed-record-v1":
        from tehm.adapters.research_seed_scoped import replay_research_seed_record
        return replay_research_seed_record(record)
    if not isinstance(scoped, dict) or scoped.get("version") != "orfs-scoped-record-v1":
        raise ValueError("unsupported scoped execution record")
    if scoped.get("role") not in {"before", "after"}:
        raise ValueError("unsupported scoped execution role")
    try:
        expected = build_flow_feasibility_record(
            Path(scoped["before_project"]), Path(scoped["after_project"]),
            lineage_id=record.lineage_id,
            role="control" if scoped["role"] == "before" else "treatment",
            **{key: scoped[key] for key in ("before_pin", "after_pin", "config_edits",
                                           "toolchain_manifest", "expected_manifest_digest")})
    except (KeyError, TypeError) as exc:
        raise ValueError("malformed scoped execution record") from exc
    if asdict(record) != asdict(expected):
        raise ValueError("flow feasibility record differs from replayed execution")
    return expected.verification["scoped_execution"]["pair_receipt"]


def replay_persisted_flow_feasibility(conn: sqlite3.Connection, transition_id: str,
                                     *, acquisition: dict) -> dict:
    """Compare persisted evidence with an independent acquisition reconstruction.

    The caller supplies the frozen acquisition (including lineage and pins),
    not fields extracted from the transition under examination. The source
    database is read only; canonical reconstruction happens in RAM. This
    proves measurement binding, not dataset eligibility or causal authority.
    """
    from tehm.causal.mechanism import load_transition_facts

    if (isinstance(acquisition, dict) and
            acquisition.get("version") == "tehm-r4-rc1-seed-acquisition-v1"):
        from tehm.adapters.research_seed_scoped import replay_persisted_research_seed
        return replay_persisted_research_seed(
            conn, transition_id, acquisition=acquisition)
    expected_keys = {"before", "after", "lineage_id", "before_pin", "after_pin",
                     "config_edits", "toolchain_manifest", "expected_manifest_digest"}
    if type(acquisition) is not dict or set(acquisition) not in (expected_keys, expected_keys | {"role"}):
        raise ValueError("scoped replay requires an explicit frozen acquisition")
    acquisition = dict(acquisition)
    for key in ("before", "after", "toolchain_manifest"):
        acquisition[key] = str(Path(acquisition[key]).resolve())
    facts = load_transition_facts(conn, transition_id)
    if not facts.verifier.get("scoped_execution"):
        raise ValueError("transition has no scoped execution")
    record = build_flow_feasibility_record(**acquisition)
    _compare_persisted_flow_record(conn, transition_id, record)
    return {"version": "orfs-persisted-scoped-replay-v1", "transition_id": transition_id,
            "acquisition_digest": _digest(acquisition), "persisted_binding_verified": True,
            "pair_receipt": record.verification["scoped_execution"]["pair_receipt"],
            "learner_admission": False, "promotion_attempted": False}


def _compare_persisted_flow_record(conn: sqlite3.Connection, transition_id: str,
                                   record: ExecutionRecord) -> None:
    """Reconstruct in RAM and compare all canonical content/provenance columns."""
    from tehm import db
    from tehm.artifact_store import ArtifactStore
    from tehm.canonical.capture import capture

    with tempfile.TemporaryDirectory(prefix="tehm-scoped-replay-") as scratch:
        replica = sqlite3.connect(":memory:")
        replica.row_factory = sqlite3.Row
        try:
            replica.execute("PRAGMA foreign_keys=ON")
            db.ensure_schema(replica)
            receipt = capture(replica, ArtifactStore(Path(scratch)), record,
                              dataset_campaign_id="scoped-replay-diagnostic",
                              dataset_learner_eligible=False)
            if receipt.transition_id != transition_id:
                raise ValueError("persisted transition differs from independent acquisition")
            # Checking the transition ID alone misses tampered state rows
            # whose IDs have not been recomputed. Compare all persisted
            # state/transition columns, including non-identity provenance.
            for table, key, ids in (
                    ("tehm_states", "state_id", receipt.state_ids.values()),
                    ("tehm_transitions", "transition_id", [transition_id])):
                for identity in ids:
                    query = f"SELECT * FROM {table} WHERE {key}=?"
                    actual = conn.execute(query, (identity,)).fetchone()
                    expected = replica.execute(query, (identity,)).fetchone()
                    if actual is None or expected is None:
                        raise ValueError("scoped replay missing canonical row")
                    actual, expected = dict(actual), dict(expected)
                    actual.pop("created_at", None)
                    expected.pop("created_at", None)
                    if actual != expected:
                        raise ValueError(f"scoped replay {table} evidence mismatch")
        finally:
            replica.close()
