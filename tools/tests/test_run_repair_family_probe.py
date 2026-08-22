import argparse
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "tools/run_repair_family_probe.py"
SPEC = importlib.util.spec_from_file_location("repair_family_probe", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_parallel_probe_environment_uses_workers_without_cpu_index_pinning(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ORFS_MAX_CPUS", "4")
    args = argparse.Namespace(timeout_seconds=600, cores=4)

    env = MODULE.execution_environment(args, tmp_path)

    assert env["NUM_CORES"] == "4"
    assert env["ORFS_TIMEOUT"] == "600"
    assert "ORFS_MAX_CPUS" not in env


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
        ["SYNTH_MISSING_INCLUDE"],
    )
    assert MODULE.classify_flow_failures(timeout) == (
        False,
        True,
        ["ROUTE_TIMEOUT"],
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
