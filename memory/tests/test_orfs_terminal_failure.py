import copy
import json

import pytest

from tehm.adapters.orfs_terminal_failure import (
    CONTRACT, evaluate_density_terminal_failure, register_terminal_contract,
    recheck_terminal_registration,
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
