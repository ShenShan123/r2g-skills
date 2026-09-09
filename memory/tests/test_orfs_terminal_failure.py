import copy
import json

import pytest

from tehm.adapters.orfs_terminal_failure import (
    CONTRACT, evaluate_density_terminal_failure, register_terminal_contract,
    recheck_terminal_registration,
    terminal_run_file_bindings, replay_terminal_failure,
    FLOW_CONTRACT, replay_flow_feasibility,
    replay_flow_feasibility_pair,
)


def evidence():
    return ({"run_tag": "RUN_density", "make_status": 2},
            [{"stage": stage, "status": rc} for stage, rc in
             (("synth", 0), ("floorplan", 0), ("place", 2))],
            "[ERROR FLW-0024] Place density exceeds 1.0")


def test_density_failure_is_not_signoff_or_learner_authority():
    args = evidence()
    original = copy.deepcopy(args)
    receipt = evaluate_density_terminal_failure(*args)
    assert receipt["verdict"] == "FAIL"
    assert not receipt["learner_admission"]
    assert not receipt["full_signoff_complete"]
    assert not receipt["preregistration_verified"]
    assert set(receipt["downstream_checks"].values()) == {"NOT_EXECUTED"}
    assert evaluate_density_terminal_failure(*args) == receipt
    assert args == original
    receipt["contract"]["error_codes"].append("FAKE-1234")
    assert "FAKE-1234" not in evaluate_density_terminal_failure(*args)["contract"]["error_codes"]


@pytest.mark.parametrize("marker", ["timeout", "Killed", "Permission denied",
                                    "Segmentation fault", "not found"])
def test_infrastructure_markers_override_recognized_density_error(marker):
    meta, stages, log = evidence()
    assert evaluate_density_terminal_failure(meta, stages, log + "\n" + marker)["verdict"] == "UNKNOWN"


@pytest.mark.parametrize("case", ["missing_prefix", "later_success", "different_error",
                                 "wrong_message", "exit124", "bool_status", "no_run"])
def test_incomplete_or_contradictory_inputs_abstain(case):
    meta, stages, log = evidence()
    if case == "missing_prefix": stages.pop(0)
    if case == "later_success": stages.append({"stage": "finish", "status": 0})
    if case == "different_error": log += "\n[ERROR STA-1234] unrelated error"
    if case == "wrong_message": log = "[ERROR FLW-0024] other error"
    if case == "exit124": meta["make_status"] = 124
    if case == "bool_status": stages[0]["status"] = False
    if case == "no_run": meta.pop("run_tag")
    result = evaluate_density_terminal_failure(meta, stages, log)
    assert result["verdict"] == "UNKNOWN"
    assert set(result["downstream_checks"].values()) == {"UNKNOWN"}


def test_gpl_utilization_diagnostic_is_recognized():
    meta, stages, _ = evidence()
    result = evaluate_density_terminal_failure(meta, stages,
        "[ERROR GPL-0301] Utilization 106.495% exceeds 100%")
    assert result["verdict"] == "FAIL"


def registration_inputs(tmp_path):
    (tmp_path / "constraints").mkdir()
    rtl, sdc = tmp_path / "design.v", tmp_path / "constraints/constraint.sdc"
    rtl.write_text("module design; endmodule\n")
    sdc.write_text("create_clock -period 2.2 [get_ports clk]\n")
    (tmp_path / "constraints/config.mk").write_text(
        f"export VERILOG_FILES = {rtl}\nexport SDC_FILE = {sdc}\n")
    return dict(contract_version=CONTRACT["version"],
                toolchain={"status": "bound_internal", "manifest_validation": {"valid": True}},
                command=["bash", "run_orfs.sh", str(tmp_path)])


def test_registration_is_exclusive_and_rechecks_inputs(tmp_path):
    kwargs = registration_inputs(tmp_path)
    receipt = register_terminal_contract(tmp_path, **kwargs)
    assert recheck_terminal_registration(tmp_path, receipt)
    with pytest.raises(FileExistsError):
        register_terminal_contract(tmp_path, **kwargs)
    (tmp_path / "design.v").write_text("changed RTL")
    assert not recheck_terminal_registration(tmp_path, receipt)


@pytest.mark.parametrize("existing", ["run", "receipt"])
def test_historical_execution_cannot_gain_registration(tmp_path, existing):
    kwargs = registration_inputs(tmp_path)
    if existing == "run":
        (tmp_path / "backend/RUN_previous").mkdir(parents=True)
    else:
        (tmp_path / "campaign-run-receipt.json").write_text("{}")
    with pytest.raises(ValueError, match="fresh project"):
        register_terminal_contract(tmp_path, **kwargs)
    assert not (tmp_path / "terminal-preregistration.json").exists()


def test_unknown_contract_and_unlocked_tools_rejected(tmp_path):
    kwargs = registration_inputs(tmp_path)
    with pytest.raises(ValueError, match="unsupported"):
        register_terminal_contract(tmp_path, **{**kwargs, "contract_version": "manual-pass"})
    with pytest.raises(ValueError, match="toolchain lock"):
        register_terminal_contract(tmp_path, **{**kwargs, "toolchain": {}})


def test_registration_tamper_rejected(tmp_path):
    kwargs = registration_inputs(tmp_path)
    receipt = register_terminal_contract(tmp_path, **kwargs)
    receipt["command"].append("other")
    assert not recheck_terminal_registration(tmp_path, receipt)


def test_external_sdc_does_not_replace_required_wrapper_local_sdc(tmp_path):
    kwargs = registration_inputs(tmp_path)
    external = tmp_path / "external.sdc"
    external.write_text("create_clock -period 2.2 [get_ports clk]\n")
    config = tmp_path / "constraints/config.mk"
    config.write_text(config.read_text().replace(
        str(tmp_path / "constraints/constraint.sdc"), str(external)))
    (tmp_path / "constraints/constraint.sdc").unlink()
    with pytest.raises(FileNotFoundError):
        register_terminal_contract(tmp_path, **kwargs)
    assert not (tmp_path / "terminal-preregistration.json").exists()


@pytest.mark.parametrize("drift", [False, True])
def test_runner_registers_before_invocation_and_records_recheck(tmp_path, monkeypatch, drift):
    from scripts import run_orfs_diversity_campaign as runner
    from types import SimpleNamespace

    project = tmp_path / "case"
    project.mkdir()
    kwargs = registration_inputs(project)
    manifest = {"orfs_root": str(tmp_path / "orfs"),
                "terminal_failure_contract": CONTRACT["version"],
                "items": [{"platform": "sky130hs", "before_project": str(project),
                           "after_project": str(project)}]}
    binding = {**kwargs["toolchain"], "environment": {}}
    monkeypatch.setattr(runner, "_require_orfs_platform_scope", lambda *a, **k: {})
    monkeypatch.setattr(runner, "_require_orfs_toolchain", lambda *a, **k: binding)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    invoked = []

    def execute(command, log, **kwargs):
        registered = json.loads((project / "terminal-preregistration.json").read_text())
        assert registered["command"] == command
        assert recheck_terminal_registration(project, registered)
        invoked.append(command)
        log.write_text("synthetic runner failure, no EDA executed")
        if drift:
            (project / "design.v").write_text("changed during execution")
        return 2, False

    monkeypatch.setattr(runner, "_run_bounded", execute)
    runner.run_projects(tmp_path, manifest, workers=1, cpus=1, timeout=1)
    receipt = json.loads((project / "campaign-run-receipt.json").read_text())
    assert len(invoked) == 1
    assert receipt["terminal_registration_unchanged"] is (not drift)
    assert receipt["terminal_preregistration"]["contract"] == CONTRACT
    with pytest.raises(ValueError, match="fresh project"):
        runner.run_projects(tmp_path, manifest, workers=1, cpus=1, timeout=1)
    assert len(invoked) == 1


def replay_inputs(tmp_path, *, version=None, success=False, run_name="RUN_density", config_lines="",
                  toolchain=None):
    kwargs = registration_inputs(tmp_path)
    if toolchain is not None:
        kwargs["toolchain"] = toolchain
    config = tmp_path / "constraints/config.mk"
    config.write_text(config.read_text() + config_lines)
    if version is not None:
        kwargs["contract_version"] = version
    registration = register_terminal_contract(tmp_path, **kwargs)
    run = tmp_path / "backend" / run_name
    run.mkdir(parents=True)
    meta, stages, log = evidence()
    meta["run_tag"] = run_name
    if success:
        meta["make_status"] = 0
        stages = [{"stage": name, "status": 0} for name in FLOW_CONTRACT["success_stages"]]
        log = "Flow completed successfully"
        (run / "final").mkdir()
        for name in FLOW_CONTRACT["final_artifacts"]:
            (run / "final" / name).write_text("synthetic fixture artifact")
    (run / "run-meta.json").write_text(json.dumps(meta))
    (run / "stage_log.jsonl").write_text("\n".join(json.dumps(row) for row in stages))
    (run / "flow.log").write_text(log)
    files = terminal_run_file_bindings(tmp_path)
    execution = {"terminal_preregistration": registration, "toolchain_binding": kwargs["toolchain"],
                 "command": kwargs["command"], "attempt": 1, "completed": success,
                 "resume_from": None, "supervisor_timeout": False, "flow_rc": 0 if success else 2,
                 "terminal_run_files": files, "stage_log": files[1]["path"],
                 "stage_log_sha256": files[1]["sha256"], "terminal_registration_unchanged": True}
    (tmp_path / "campaign-run-receipt.json").write_text(json.dumps(execution))
    return registration["registration_digest"], kwargs["toolchain"], execution


def test_consumer_replays_raw_evidence_without_learner_authority(tmp_path):
    pin, toolchain, _ = replay_inputs(tmp_path)
    result = replay_terminal_failure(tmp_path, registration_digest=pin, current_toolchain=toolchain)
    assert result["verdict"] == "FAIL"
    assert result["registration_binding_verified"] is True
    assert result["learner_admission"] is False
    assert result["preregistration_verified"] is False


@pytest.mark.parametrize("tamper", ["input", "log", "registration", "command", "toolchain",
                                   "pin", "timeout", "attempt", "stage_ref", "extra_run"])
def test_consumer_rejects_tampering_despite_producer_true_flag(tmp_path, tamper):
    pin, toolchain, execution = replay_inputs(tmp_path)
    if tamper == "input": (tmp_path / "design.v").write_text("drift")
    if tamper == "log": (tmp_path / "backend/RUN_density/flow.log").write_text("other failure")
    if tamper == "registration": execution["terminal_preregistration"] = {}
    if tamper == "command": execution["command"] = ["different command"]
    if tamper == "toolchain": toolchain = {**toolchain, "changed": True}
    if tamper == "pin": pin = "sha256:wrong"
    if tamper == "timeout": execution["supervisor_timeout"] = True
    if tamper == "attempt": execution["attempt"] = 2
    if tamper == "stage_ref": execution["stage_log"] = "/other/run/stage_log.jsonl"
    if tamper == "extra_run": (tmp_path / "backend/RUN_other").mkdir()
    (tmp_path / "campaign-run-receipt.json").write_text(json.dumps(execution))
    with pytest.raises(ValueError):
        replay_terminal_failure(tmp_path, registration_digest=pin, current_toolchain=toolchain)


@pytest.mark.parametrize("success", [False, True])
def test_v2_measures_both_outcomes_without_signoff_claim(tmp_path, success):
    pin, tools, _ = replay_inputs(tmp_path, version=FLOW_CONTRACT["version"], success=success)
    receipt = replay_flow_feasibility(tmp_path, registration_digest=pin, current_toolchain=tools)
    assert receipt["verdict"] == ("PASS" if success else "FAIL")
    assert receipt["contract"] == FLOW_CONTRACT
    assert not receipt["full_signoff_complete"]
    assert not receipt["learner_admission"]
    if success:
        assert set(receipt["downstream_checks"].values()) == {"UNKNOWN"}
    with pytest.raises(ValueError, match="contract version"):
        replay_terminal_failure(tmp_path, registration_digest=pin, current_toolchain=tools)


@pytest.mark.parametrize("name", FLOW_CONTRACT["final_artifacts"])
def test_v2_rejects_missing_completed_artifacts(tmp_path, name):
    pin, tools, _ = replay_inputs(tmp_path, version=FLOW_CONTRACT["version"], success=True)
    (tmp_path / "backend/RUN_density/final" / name).unlink()
    with pytest.raises(ValueError, match="raw run evidence"):
        replay_flow_feasibility(tmp_path, registration_digest=pin, current_toolchain=tools)


def test_v1_failure_cannot_be_silently_reinterpreted_as_v2(tmp_path):
    pin, tools, _ = replay_inputs(tmp_path)
    with pytest.raises(ValueError, match="contract version"):
        replay_flow_feasibility(tmp_path, registration_digest=pin, current_toolchain=tools)


def test_zero_exit_and_artifacts_do_not_override_incomplete_stage_sequence(tmp_path):
    pin, tools, execution = replay_inputs(tmp_path, version=FLOW_CONTRACT["version"], success=True)
    stage = tmp_path / "backend/RUN_density/stage_log.jsonl"
    stage.write_text(json.dumps({"stage": "synth", "status": 0}))
    files = terminal_run_file_bindings(tmp_path)
    execution.update(terminal_run_files=files, stage_log_sha256=files[1]["sha256"])
    (tmp_path / "campaign-run-receipt.json").write_text(json.dumps(execution))
    receipt = replay_flow_feasibility(tmp_path, registration_digest=pin, current_toolchain=tools)
    assert receipt["verdict"] == "UNKNOWN"


@pytest.mark.parametrize("extra", ["", "export ABC_AREA = 0\n"])
def test_pair_checks_context_not_only_matching_outcome_labels(tmp_path, extra):
    projects = [tmp_path / side for side in ("before", "after")]
    pins = []
    for project, density, success in zip(projects, (95, 40), (False, True)):
        project.mkdir()
        lines = ("export DESIGN_NAME = design\nexport PLATFORM = sky130hs\n"
                 f"export CORE_UTILIZATION = {density}\n")
        if success:
            lines += extra
        pin, tools, _ = replay_inputs(project, version=FLOW_CONTRACT["version"], success=success,
                                     run_name="RUN_" + project.name, config_lines=lines)
        pins.append(pin)
    kwargs = dict(before_pin=pins[0], after_pin=pins[1], current_toolchain=tools,
                  config_edits={"CORE_UTILIZATION": "40"})
    if extra:
        with pytest.raises(ValueError, match="undeclared"):
            replay_flow_feasibility_pair(*projects, **kwargs)
    else:
        result = replay_flow_feasibility_pair(*projects, **kwargs)
        assert result["controlled_measurement_valid"]
        assert not result["learner_admission"]
        assert result["before"]["verdict"] == "FAIL"
        assert result["after"]["verdict"] == "PASS"
        with pytest.raises(ValueError, match="declared density"):
            replay_flow_feasibility_pair(*projects, **{**kwargs, "config_edits": {"CORE_UTILIZATION": "30"}})


def test_locked_pair_uses_live_tools_not_saved_valid_flags(tmp_path, tmp_tehm):
    from test_orfs_toolchain_manifest import _fake_orfs
    from tehm.orfs_toolchain import build_toolchain_manifest
    from tehm.orfs_toolchain_preflight import preflight_orfs_toolchain
    from tehm.adapters.orfs_terminal_failure import replay_locked_flow_feasibility_pair

    root, openroad, _ = _fake_orfs(tmp_path / "orfs")
    unlocked = preflight_orfs_toolchain({"orfs_root": str(root)}, env={})
    lock = build_toolchain_manifest(unlocked)
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(lock))
    tools = preflight_orfs_toolchain({"orfs_root": str(root), "toolchain_manifest": str(path)})
    projects = [tmp_path / side for side in ("before", "after")]
    pins = []
    for project, density, success in zip(projects, (95, 40), (False, True)):
        project.mkdir()
        pin, _, _ = replay_inputs(
            project, version=FLOW_CONTRACT["version"], success=success,
            run_name="RUN_" + project.name, toolchain=tools,
            config_lines=("export DESIGN_NAME = design\nexport PLATFORM = sky130hs\n"
                          f"export CORE_UTILIZATION = {density}\n"))
        pins.append(pin)
    kwargs = dict(before_pin=pins[0], after_pin=pins[1], config_edits={"CORE_UTILIZATION": "40"},
                  toolchain_manifest=path, expected_manifest_digest=lock["manifest_digest"])
    result = replay_locked_flow_feasibility_pair(*projects, **kwargs)
    assert result["controlled_measurement_valid"]
    assert not result["learner_admission"]
    assert result == replay_flow_feasibility_pair(
        *projects, before_pin=pins[0], after_pin=pins[1],
        config_edits=kwargs["config_edits"], current_toolchain=tools)
    with pytest.raises(ValueError, match="lock pin mismatch"):
        replay_locked_flow_feasibility_pair(*projects, **{**kwargs, "expected_manifest_digest": "other"})
    from tehm.adapters.orfs_scoped import build_flow_feasibility_record, replay_flow_feasibility_record
    from tehm.canonical.capture import capture
    from tehm.causal.mechanism import load_transition_facts
    from tehm.verified_execution import require_verified_execution

    record = build_flow_feasibility_record(*projects, lineage_id="fixture-density", **kwargs)
    assert replay_flow_feasibility_record(record) == result
    assert record.verification["scope"] == "flow_feasibility"
    assert record.verification["oracle_complete"] is True
    assert record.observation_delta["utility_verdict"] == "UNKNOWN"
    assert record.observation_delta["original_failure"] == "REMOVED"
    for section, key, value in (
            ("action", "payload", {"config_edits": {"CORE_UTILIZATION": "30"}}),
            ("before", "config", {"CORE_UTILIZATION": "10"}),
            ("after", "reports", {"drc": {"status": "clean"}}),
            ("verification", "scope", "full_signoff"),
            ("observation_delta", "utility_verdict", "PARETO_SAFE")):
        changed = copy.deepcopy(record)
        getattr(changed, section)[key] = value
        with pytest.raises(ValueError, match="differs from replayed execution"):
            replay_flow_feasibility_record(changed)
    conn, store, _ = tmp_tehm
    captured = capture(conn, store, record, dataset_learner_eligible=False)
    assert capture(conn, store, record, dataset_learner_eligible=False).transition_id == captured.transition_id
    facts = load_transition_facts(conn, captured.transition_id)
    assert facts.verifier["scoped_execution"]["pair_receipt"] == result
    from tehm.adapters.orfs_scoped import replay_persisted_flow_feasibility
    acquisition = dict(before=projects[0], after=projects[1], lineage_id="fixture-density", **kwargs)
    changes = conn.total_changes
    persisted = replay_persisted_flow_feasibility(conn, captured.transition_id, acquisition=acquisition)
    assert conn.total_changes == changes
    assert persisted["persisted_binding_verified"] and not persisted["learner_admission"]
    assert persisted["pair_receipt"] == result
    control = build_flow_feasibility_record(*projects, lineage_id="fixture-density", role="control", **kwargs)
    assert control.before == control.after
    assert control.before is not control.after
    assert control.verification["verdict"] == "FAIL"
    assert control.verification["scope"] == "flow_feasibility"
    assert control.observation_delta["original_failure"] == "PRESENT"
    assert control.observation_delta["experiment_kind"] == "OBSERVATION"
    assert control.observation_delta["failing_tests"] == {"before": 1, "after": 1}
    assert control.action["payload"]["config_edits"] == {}
    assert control.action["payload"]["observation_only"] is True
    assert all(str(projects[0]) in ref for ref in control.verification["evidence_refs"])
    assert replay_flow_feasibility_record(control) == result
    control_capture = capture(conn, store, control, dataset_learner_eligible=False)
    assert control_capture.transition_id != captured.transition_id
    assert control_capture.state_ids["before"] == captured.state_ids["before"]
    assert replay_persisted_flow_feasibility(
        conn, control_capture.transition_id, acquisition={**acquisition, "role": "control"})["persisted_binding_verified"]
    with pytest.raises(ValueError, match="independent acquisition"):
        replay_persisted_flow_feasibility(conn, control_capture.transition_id, acquisition=acquisition)
    with pytest.raises(ValueError, match="scoped_execution_replay_required"):
        require_verified_execution(load_transition_facts(conn, control_capture.transition_id))
    import hashlib
    import sqlite3
    from tehm.ids import stable_dumps
    from tehm.dataset import assign_transition
    from tehm.verified_execution import scoped_learning_replay, require_verified_transition
    from tehm.causal.intervention import build_intervention_pair
    acquisitions = json.loads(json.dumps({
        captured.transition_id: acquisition,
        control_capture.transition_id: {**acquisition, "role": "control"}}, default=str))
    digest = "sha256:" + hashlib.sha256(stable_dumps(acquisitions).encode()).hexdigest()
    context_args = dict(campaign_id="scoped-training", acquisitions=acquisitions, expected_digest=digest)
    with pytest.raises(ValueError, match="RAM database"):
        with scoped_learning_replay(conn, **context_args):
            pass
    conn.commit()
    ram = sqlite3.connect(":memory:")
    ram.row_factory = sqlite3.Row
    conn.backup(ram)
    try:
        with pytest.raises(ValueError, match="freeze digest mismatch"):
            with scoped_learning_replay(ram, **{**context_args, "expected_digest": "wrong"}):
                pass
        with scoped_learning_replay(ram, **context_args):
            with pytest.raises(ValueError, match="explicit_training_membership"):
                require_verified_transition(ram, captured.transition_id)
        for tid in acquisitions:
            assign_transition(ram, transition_id=tid, campaign_id="scoped-training", learner_eligible=True)
        with scoped_learning_replay(ram, **context_args):
            for tid in acquisitions:
                require_verified_transition(ram, tid)
            with pytest.raises(ValueError, match="connection_mismatch"):
                require_verified_transition(conn, captured.transition_id)
            controlled = build_intervention_pair(
                ram, control_capture.transition_id, captured.transition_id,
                campaign_id="scoped-training", target_scope="flow_feasibility")
            assert controlled.validity_status == "VALID_CONTROLLED_PAIR"
            from tehm.causal.path_builder import build_transition_causal_fragment, consolidate_causal_path
            from tehm.knowledge import build_knowledge_from_path
            from tehm.state import build_support_envelope_from_transitions, evaluate_state_shift
            fragments = [build_transition_causal_fragment(ram, tid, campaign_id="scoped-training")
                         for tid in acquisitions]
            path = consolidate_causal_path(ram, fragments, campaign_id="scoped-training")
            claim = build_knowledge_from_path(ram, path.path_id)
            envelope = build_support_envelope_from_transitions(
                ram, claim, [captured.transition_id], campaign_id="scoped-training")
            assert envelope.source_transition_ids == (captured.transition_id,)
            for dimension in ("structural", "flow", "constraint", "oracle", "history"):
                assert envelope.dimensions[dimension]["values"]
            assert envelope.dimensions["constraint"]["values"][0]["core_utilization"] == "95"
            current = {
                "structural_signature": envelope.dimensions["structural"]["values"][0],
                "mechanism_signature": envelope.dimensions["mechanism"]["values"][0],
                "flow_regime": envelope.dimensions["flow"]["values"][0],
                "constraint_regime": envelope.dimensions["constraint"]["values"][0],
                "oracle_regime": envelope.dimensions["oracle"]["values"][0],
                "action_history": envelope.dimensions["history"]["values"][0],
            }
            assert evaluate_state_shift(
                current, {"resolution_id": "same"}, claim, envelope).transferable
            shifted = copy.deepcopy(current)
            shifted["constraint_regime"]["core_utilization"] = "30"
            shift = evaluate_state_shift(
                shifted, {"resolution_id": "challenge"}, claim, envelope)
            assert not shift.transferable and shift.reason == "STATE_SHIFT"
            assert shift.shifted_dimensions == ("constraint_shift",)
        with pytest.raises(ValueError, match="scoped_execution_replay_required"):
            require_verified_transition(ram, captured.transition_id)
    finally:
        ram.close()
    with pytest.raises(ValueError, match="tehm_states evidence mismatch"):
        replay_persisted_flow_feasibility(conn, captured.transition_id,
                                         acquisition={**acquisition, "lineage_id": "wrong-lineage"})
    for column, value in (("source_digest", "replaced"), ("lineage_id", "wrong-lineage"),
                          ("artifact_manifest_json", "{}")):
        state_id = captured.state_ids["after"]
        original = conn.execute(f"SELECT {column} FROM tehm_states WHERE state_id=?", (state_id,)).fetchone()[0]
        conn.execute(f"UPDATE tehm_states SET {column}=? WHERE state_id=?", (value, state_id))
        with pytest.raises(ValueError, match="tehm_states evidence mismatch"):
            replay_persisted_flow_feasibility(conn, captured.transition_id, acquisition=acquisition)
        conn.execute(f"UPDATE tehm_states SET {column}=? WHERE state_id=?", (original, state_id))
    with pytest.raises(ValueError, match="scoped_execution_replay_required"):
        require_verified_execution(facts)
    openroad.write_text("#!/bin/sh\necho 'replacement'\n")
    # All producer receipts and their valid=True flags remain untouched.
    with pytest.raises(ValueError, match="live toolchain replay failed"):
        replay_locked_flow_feasibility_pair(*projects, **kwargs)
