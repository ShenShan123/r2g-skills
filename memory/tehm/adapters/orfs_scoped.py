"""Source-replayed flow-feasibility records, without learner admission.

The declared oracle measures flow completion, not complete physical signoff.
No legacy aggregate record is relabeled and no database is written here.
"""
from __future__ import annotations

import json
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
        expected_manifest_digest: str) -> ExecutionRecord:
    """Build an explicit scoped observation from freshly replayed raw arms.

    Pins must be supplied from the frozen acquisition record. The caller owns
    lineage and dataset partition assignment; neither follows from a PASS.
    """
    if type(lineage_id) is not str or not lineage_id.strip():
        raise ValueError("flow feasibility record requires a lineage")
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
    before_verdict, after_verdict = pair["before"]["verdict"], pair["after"]["verdict"]
    original = ("REMOVED" if (before_verdict, after_verdict) == ("FAIL", "PASS") else
                "PRESENT" if before_verdict == "FAIL" and after_verdict == "FAIL" else "UNKNOWN")
    family = ("DENSITY_RELIEF" if float(states[1]["config"]["CORE_UTILIZATION"]) <
              float(states[0]["config"]["CORE_UTILIZATION"]) else "DENSITY_INCREASE")
    scoped = {"version": "orfs-scoped-record-v1", "role": "after",
              "before_project": str(before), "after_project": str(after),
              **kwargs, "pair_receipt": pair}
    identity = _digest({"scoped_execution": scoped, "lineage_id": lineage_id})
    record = ExecutionRecord(
        record_id="orfs-scoped:" + identity.removeprefix("sha256:"),
        domain="flow.signoff", project_id=lineage_id,
        design_id=states[0]["config"]["DESIGN_NAME"], lineage_id=lineage_id,
        before=states[0], after=states[1],
        action={"domain": "flow.CONFIG_DELTA", "transformation_family": family,
                "payload": {"config_edits": dict(config_edits),
                            "recheck": "flow_feasibility",
                            "measurement_contract_digest": pair["contract_digest"]}},
        observation_delta={
            "original_failure": original,
            "failing_tests": {side: (0 if pair[side]["verdict"] == "PASS" else
                                     1 if pair[side]["verdict"] == "FAIL" else None)
                              for side in ("before", "after")},
            "created_regressions": (["flow_feasibility"] if
                                    (before_verdict, after_verdict) == ("PASS", "FAIL") else []),
            "newly_observed_failures": [],
            "experiment_kind": "REPAIR" if original in {"REMOVED", "PRESENT"} else "OBSERVATION",
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
    if not isinstance(scoped, dict) or scoped.get("version") != "orfs-scoped-record-v1":
        raise ValueError("unsupported scoped execution record")
    try:
        expected = build_flow_feasibility_record(
            Path(scoped["before_project"]), Path(scoped["after_project"]),
            lineage_id=record.lineage_id,
            **{key: scoped[key] for key in ("before_pin", "after_pin", "config_edits",
                                           "toolchain_manifest", "expected_manifest_digest")})
    except (KeyError, TypeError) as exc:
        raise ValueError("malformed scoped execution record") from exc
    if asdict(record) != asdict(expected):
        raise ValueError("flow feasibility record differs from replayed execution")
    return expected.verification["scoped_execution"]["pair_receipt"]
