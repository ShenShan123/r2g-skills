import argparse
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "tools/run_repair_family_probe.py"
SPEC = importlib.util.spec_from_file_location("repair_family_probe", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_parallel_probe_environment_bounds_workers_and_cpu_affinity(tmp_path, monkeypatch):
    monkeypatch.setenv("ORFS_MAX_CPUS", "4")
    args = argparse.Namespace(timeout_seconds=600, cores=4)

    env = MODULE.execution_environment(args, tmp_path)

    assert env["NUM_CORES"] == "4"
    assert env["ORFS_TIMEOUT"] == "600"
    assert env["ORFS_MAX_CPUS"] == "4"


def test_probe_environment_accepts_explicit_cpu_set(tmp_path):
    args = argparse.Namespace(timeout_seconds=600, cores=4, cpu_set="32-35,40")

    env = MODULE.execution_environment(args, tmp_path)

    assert env["ORFS_CPU_SET"] == "32-35,40"


def test_probe_environment_rejects_invalid_cpu_set(tmp_path):
    args = argparse.Namespace(timeout_seconds=600, cores=4, cpu_set="32-35;echo bad")

    with pytest.raises(ValueError, match="invalid --cpu-set"):
        MODULE.execution_environment(args, tmp_path)


def test_frozen_probe_input_integrity_fails_closed_on_mutated_sdc(tmp_path):
    project = tmp_path / "project"
    (project / "rtl").mkdir(parents=True)
    (project / "constraints").mkdir()
    rtl = project / "rtl/top.v"
    sdc = project / "constraints/constraint.sdc"
    config = project / "constraints/config.mk"
    rtl.write_text("module top; endmodule\n")
    sdc.write_text("create_clock -period 10 [get_ports clk]\n")
    config.write_text("export DESIGN_NAME = top\n")
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    files = [{"path": "rtl/top.v", "size": rtl.stat().st_size, "sha256": sha(rtl)}]
    source_digest = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    protected = {"sdc_sha256": sha(sdc), "source_digest": source_digest}
    manifest = {
        "files": files,
        "protected_task": protected,
        "protected_task_digest": hashlib.sha256(
            json.dumps(protected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "config_sha256": sha(config),
        "config_artifacts": [],
    }
    assert MODULE.input_integrity_failures(project, manifest) == []

    sdc.write_text("create_clock -period 20 [get_ports clk]\n")
    assert MODULE.input_integrity_failures(project, manifest) == [
        "mutated frozen SDC: constraints/constraint.sdc"
    ]


def test_frozen_probe_input_integrity_rejects_mutated_protected_task(tmp_path):
    project = tmp_path / "project"
    (project / "rtl").mkdir(parents=True)
    (project / "constraints").mkdir()
    rtl = project / "rtl/top.v"
    sdc = project / "constraints/constraint.sdc"
    config = project / "constraints/config.mk"
    rtl.write_text("module top; endmodule\n")
    sdc.write_text("create_clock -period 10 [get_ports clk]\n")
    config.write_text("export DESIGN_NAME = top\n")
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    files = [{"path": "rtl/top.v", "size": rtl.stat().st_size, "sha256": sha(rtl)}]
    source_digest = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    protected = {
        "sdc_sha256": sha(sdc),
        "source_digest": source_digest,
        "target_frequency_mhz": 100,
    }
    manifest = {
        "files": files,
        "protected_task": protected,
        "protected_task_digest": hashlib.sha256(
            json.dumps(protected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "config_sha256": sha(config),
        "config_artifacts": [],
    }
    manifest["protected_task"]["target_frequency_mhz"] = 50

    assert "protected-task digest mismatch" in MODULE.input_integrity_failures(project, manifest)


def test_failure_patterns_separate_missing_include_from_route_timeout():
    missing = "ERROR: Can't open include file `coeffs.svh'!"
    timeout = "ERROR: Stage 'route' failed (exit code 124) after 7200s\n  (timed out after 7200s, exit code 124)"

    assert MODULE.classify_flow_failures(missing) == (
        True,
        False,
        False,
        ["SYNTH_MISSING_INCLUDE"],
    )
    assert MODULE.classify_flow_failures(timeout) == (
        False,
        True,
        False,
        ["ROUTE_TIMEOUT"],
    )


def test_zero_cell_netlist_is_input_failure_but_ifp_0065_alone_is_not():
    empty = (
        "number instances in verilog is 0\n"
        "Design area 0 um^2 100% utilization.\n"
        "[ERROR IFP-0065] No rows created in the core area.\n"
    )
    undersized = "[ERROR IFP-0065] No rows created in the core area.\n"

    assert MODULE.classify_flow_failures(empty) == (
        True,
        False,
        False,
        ["SYNTH_ZERO_CELL_NETLIST"],
    )
    assert MODULE.classify_flow_failures(undersized) == (False, False, False, [])


def test_synth_memory_cap_is_explicit_capacity_infeasibility():
    memory_cap = (
        "Largest single memory instance: 180224 bits\n"
        "SYNTH_MEMORY_MAX_BITS: 4096\n"
        "Error: Synthesized memory size 4096 exceeds SYNTH_MEMORY_MAX_BITS\n"
    )

    assert MODULE.classify_flow_failures(memory_cap) == (
        False,
        False,
        True,
        ["SYNTH_MEMORY_CAPACITY"],
    )


def test_mapped_cell_count_uses_the_floorplan_elaborated_count():
    flow = (
        "number instances in verilog is 99,999\n"
        "[INFO GPL-0006] Number of instances: 105000\n"
    )

    assert MODULE.mapped_cell_count(flow) == 99999
    assert MODULE.mapped_cell_count("no floorplan cell count") is None


def test_mapped_cell_admission_has_both_minimum_and_maximum_bounds():
    assert MODULE.mapped_cell_count_out_of_bounds(99, 100, 100000) is True
    assert MODULE.mapped_cell_count_out_of_bounds(100, 100, 100000) is False
    assert MODULE.mapped_cell_count_out_of_bounds(100000, 100, 100000) is False
    assert MODULE.mapped_cell_count_out_of_bounds(100001, 100, 100000) is True


def test_constraint_coverage_requires_all_sequential_registers_to_be_clocked():
    clean = (
        "Floorplan check_setup\n"
        "Warning: There are 2 input ports missing set_input_delay.\n"
        "Warning: There are 3 output ports missing set_output_delay.\n"
        "Warning: There are 0 unclocked register/latch pins.\n"
        "Warning: There are 0 unconstrained endpoints.\n"
    )
    incomplete = (
        "Floorplan check_setup\n"
        "Warning: There are 7302 unclocked register/latch pins.\n"
        "Warning: There are 7634 unconstrained endpoints.\n"
    )

    assert MODULE.constraint_coverage(clean) == {
        "status": "complete",
        "unclocked_register_pins": 0,
        "unconstrained_endpoints": 0,
        "input_ports_missing_delay": 2,
        "output_ports_missing_delay": 3,
    }
    assert MODULE.constraint_coverage(incomplete)["status"] == "incomplete"
    assert MODULE.constraint_coverage("flow ended before floorplan")["status"] == "unknown"


def test_constraint_coverage_interprets_absent_orfs_warnings_as_zero_only_after_check_setup():
    assert MODULE.constraint_coverage("Floorplan check_setup\n")["status"] == "complete"


def test_constraint_coverage_records_unmodeled_io_without_rejecting_clocked_registers():
    io_unmodeled = (
        "Floorplan check_setup\n"
        "Warning: There are 33 output ports missing set_output_delay.\n"
        "Warning: There are 33 unconstrained endpoints.\n"
    )

    coverage = MODULE.constraint_coverage(io_unmodeled)

    assert coverage["status"] == "complete"
    assert coverage["unclocked_register_pins"] == 0
    assert coverage["unconstrained_endpoints"] == 33


def test_final_timing_must_be_finite_and_not_opensta_unconstrained_sentinel():
    assert MODULE.has_evaluable_final_timing(
        {"setup_wns_ns": 0.12, "hold_wns_ns": 0.03}
    ) is True
    assert MODULE.has_evaluable_final_timing(
        {"setup_wns_ns": 1.0e39, "hold_wns_ns": 1.0e39}
    ) is False
    assert MODULE.has_evaluable_final_timing(
        {"setup_wns_ns": float("nan"), "hold_wns_ns": 0.03}
    ) is False
    assert MODULE.has_evaluable_final_timing(
        {"setup_wns_ns": 0.12, "hold_wns_ns": None}
    ) is False


def test_synth_module_redefinition_is_an_input_closure_failure():
    duplicate = (
        "rtl/top/../cores/uart_rx.v:1: "
        "ERROR: Re-definition of module `$abstract\\\\uart_rx'!\n"
    )

    assert MODULE.classify_flow_failures(duplicate) == (
        True,
        False,
        False,
        ["SYNTH_MODULE_REDEFINITION"],
    )


def test_interrupted_orfs_exit_is_not_repair_evidence():
    commands = [{"command": ["bash", "/r2g/run_orfs.sh"], "returncode": 143}]

    assert MODULE.classify_execution_failure(commands, []) == (
        True,
        False,
        ["FLOW_INTERRUPTED"],
    )


def test_collect_only_marks_a_backend_run_without_final_metadata_interrupted(tmp_path):
    run = tmp_path / "backend/RUN_partial"
    run.mkdir(parents=True)

    assert MODULE.incomplete_collected_run(run, collect_only=True) is True
    assert MODULE.incomplete_collected_run(run, collect_only=False) is False

    (run / "run-meta.json").write_text("{}\n")
    assert MODULE.incomplete_collected_run(run, collect_only=True) is False


def test_unclassified_nonzero_orfs_exit_fails_closed():
    commands = [{"command": ["bash", "/r2g/run_orfs.sh"], "returncode": 2}]

    assert MODULE.classify_execution_failure(commands, []) == (
        False,
        True,
        ["FLOW_EXECUTION_FAILED"],
    )
    assert MODULE.classify_execution_failure(commands, ["ORD-0001"]) == (
        False,
        False,
        [],
    )


def test_materialize_copies_bound_source_provenance_to_metadata(tmp_path):
    source = tmp_path / "source"
    project = tmp_path / "project"
    (source / "rtl").mkdir(parents=True)
    (source / "rtl/top.v").write_text(
        "module top(input wire clk); endmodule\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(
        ["git", "-C", str(source), "remote", "add", "origin", "https://example.test/org/repo.git"],
        check=True,
    )
    subprocess.run(["git", "-C", str(source), "add", "rtl/top.v"], check=True)
    subprocess.run(
        [
            "git", "-C", str(source), "-c", "user.name=Test", "-c",
            "user.email=test@example.test", "commit", "-qm", "fixture",
        ],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    args = argparse.Namespace(
        source=source,
        source_repo_url="https://example.test/org/repo",
        source_commit=commit,
        rtl_file=[],
        dependency_file=[],
        project=project,
        family="test-family",
        task_id="test-task",
        variant="baseline",
        platform="sky130hd",
        top_module="top",
        clock_port="clk",
        frequency_mhz=100.0,
        set=[],
        unset=[],
        fastroute_tcl=None,
    )

    MODULE.materialize(args)

    metadata = json.loads((project / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["source_repo_url"] == "https://example.test/org/repo"
    assert metadata["source_commit"] == commit
    assert metadata["source_provenance_status"] == "repo_url_commit_and_bytes_bound"


def test_materialize_uses_the_frozen_sky130hd_baseline_defaults(tmp_path):
    source = tmp_path / "source"
    project = tmp_path / "project"
    (source / "rtl").mkdir(parents=True)
    (source / "rtl/top.v").write_text(
        "module top(input wire clk); endmodule\n", encoding="utf-8"
    )
    args = argparse.Namespace(
        source=source,
        source_repo_url=None,
        source_commit=None,
        rtl_file=[],
        dependency_file=[],
        project=project,
        family="test-family",
        task_id="test-task",
        variant="baseline",
        platform="sky130hd",
        top_module="top",
        clock_port="clk",
        frequency_mhz=100.0,
        set=[],
        unset=[],
        fastroute_tcl=None,
    )

    MODULE.materialize(args)

    config = (project / "constraints/config.mk").read_text(encoding="utf-8")
    assert "export CORE_UTILIZATION = 20" in config
    assert "export PLACE_DENSITY_LB_ADDON = 0.20" in config
    assert "export ABC_AREA = 1" in config


def test_materialize_does_not_trust_declared_commit_for_plain_snapshot(tmp_path):
    source = tmp_path / "source"
    project = tmp_path / "project"
    (source / "rtl").mkdir(parents=True)
    (source / "rtl/top.v").write_text(
        "module top(input wire clk); endmodule\n", encoding="utf-8"
    )
    args = argparse.Namespace(
        source=source,
        source_repo_url="https://example.test/org/repo",
        source_commit="a" * 40,
        rtl_file=[],
        dependency_file=[],
        project=project,
        family="test-family",
        task_id="test-task",
        variant="baseline",
        platform="sky130hd",
        top_module="top",
        clock_port="clk",
        frequency_mhz=100.0,
        set=[],
        unset=[],
        fastroute_tcl=None,
    )

    MODULE.materialize(args)

    metadata = json.loads((project / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["source_provenance_status"] == (
        "snapshot_bytes_bound_repo_commit_unverified"
    )


def test_materialize_freezes_include_dependency_without_compiling_it_twice(tmp_path):
    source = tmp_path / "source"
    project = tmp_path / "project"
    (source / "rtl/top").mkdir(parents=True)
    (source / "rtl/cores").mkdir()
    (source / "rtl/top/top.v").write_text(
        '`include "../cores/child.v"\nmodule top(input wire clk); child u(); endmodule\n',
        encoding="utf-8",
    )
    (source / "rtl/cores/child.v").write_text(
        "module child; endmodule\n", encoding="utf-8"
    )
    args = argparse.Namespace(
        source=source,
        source_repo_url=None,
        source_commit=None,
        rtl_file=["rtl/top/top.v"],
        dependency_file=["rtl/cores/child.v"],
        project=project,
        family="test-family",
        task_id="test-task",
        variant="baseline",
        platform="sky130hd",
        top_module="top",
        clock_port="clk",
        frequency_mhz=100.0,
        set=[],
        unset=[],
        fastroute_tcl=None,
    )

    MODULE.materialize(args)

    config = (project / "constraints/config.mk").read_text(encoding="utf-8")
    verilog_line = next(line for line in config.splitlines() if "VERILOG_FILES" in line)
    include_line = next(
        line for line in config.splitlines() if "VERILOG_INCLUDE_DIRS" in line
    )
    assert "rtl/top/top.v" in verilog_line
    assert "rtl/cores/child.v" not in verilog_line
    assert "rtl/top" in include_line
    assert "rtl/cores" in include_line

    manifest = json.loads(
        (project / "repair_family_probe_input.json").read_text(encoding="utf-8")
    )
    assert manifest["compilation_units"] == ["rtl/rtl/top/top.v"]
    assert manifest["dependency_inputs"] == ["rtl/rtl/cores/child.v"]
    assert len(manifest["files"]) == 2


def test_materialize_freezes_readmem_data_dependency_without_compiling_it(tmp_path):
    source = tmp_path / "source"
    project = tmp_path / "project"
    (source / "rtl").mkdir(parents=True)
    (source / "rtl/top.v").write_text(
        'module top(input wire clk); reg [7:0] mem [0:7]; initial $readmemh("init.dat", mem); endmodule\n',
        encoding="utf-8",
    )
    (source / "rtl/init.dat").write_text("00\n", encoding="utf-8")
    args = argparse.Namespace(
        source=source,
        source_repo_url=None,
        source_commit=None,
        rtl_file=["rtl/top.v"],
        dependency_file=["rtl/init.dat"],
        project=project,
        family="test-family",
        task_id="test-task",
        variant="baseline",
        platform="sky130hd",
        top_module="top",
        clock_port="clk",
        frequency_mhz=100.0,
        set=[],
        unset=[],
        fastroute_tcl=None,
    )

    MODULE.materialize(args)

    config = (project / "constraints/config.mk").read_text(encoding="utf-8")
    verilog_line = next(line for line in config.splitlines() if "VERILOG_FILES" in line)
    assert "rtl/top.v" in verilog_line
    assert "rtl/init.dat" not in verilog_line
    assert (project / "rtl/rtl/init.dat").is_file()
