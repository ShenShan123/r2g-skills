"""Regression tests for verifying exported physical inputs without a backend run."""
from pathlib import Path
import importlib.util

import pytest


spec = importlib.util.spec_from_file_location(
    "workflow_graph_verifier", Path(__file__).resolve().parents[2] / "tools/verify_graph_dataset.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


@pytest.fixture(autouse=True)
def clear_input_overrides(monkeypatch):
    for key in ("R2G_DEF", "R2G_ODB", "R2G_SPEF", "R2G_TIMING_SDC"):
        monkeypatch.delenv(key, raising=False)


def test_explicit_artifacts_override_an_unrelated_backend(tmp_path, monkeypatch):
    stale = tmp_path / "backend/RUN_old/results"
    stale.mkdir(parents=True)
    source = tmp_path / "exported"
    source.mkdir()
    for directory, period in ((stale, 3), (source, 10)):
        (directory / "6_final.def").write_text("VERSION 5.8 ;\n")
        (directory / "6_final.spef").write_text('*SPEF "IEEE 1481-1998"\n')
        (directory / "6_final.sdc").write_text(f"create_clock -period {period} [get_ports clk]\n")
    monkeypatch.setenv("R2G_DEF", str(source / "6_final.def"))
    monkeypatch.setenv("R2G_SPEF", str(source / "6_final.spef"))
    assert verifier._find_final_def(str(tmp_path)) == str(source / "6_final.def")
    assert verifier._find_spef(str(tmp_path)) == str(source / "6_final.spef")
    assert verifier._sdc_clock_period(str(tmp_path)) == 10


def test_no_backend_is_needed_for_the_timed_physical_inputs(tmp_path, monkeypatch):
    design = tmp_path / "input"
    design.mkdir()
    (design / "6_final.def").write_text("VERSION 5.8 ;\n")
    (design / "6_final.sdc").write_text("create_clock -period 7.5 [get_ports clk]\n")
    monkeypatch.setenv("R2G_DEF", str(design / "6_final.def"))
    assert verifier._sdc_clock_period(str(tmp_path / "project")) == 7.5
    explicit = tmp_path / "actual_timing.sdc"
    explicit.write_text("create_clock -period 8.0 [get_ports clk]\n")
    monkeypatch.setenv("R2G_TIMING_SDC", str(explicit))
    assert verifier._sdc_clock_period(str(tmp_path / "project")) == 8.0


def test_final_clock_still_precedes_a_newer_probe_clock(tmp_path):
    final = tmp_path / "backend/RUN_1/results"
    probe = tmp_path / "backend/RUN_2/results"
    final.mkdir(parents=True)
    probe.mkdir(parents=True)
    (final / "6_final.sdc").write_text("create_clock -period 10.0 [get_ports clk]\n")
    (probe / "updated_clks.sdc").write_text("create_clock -period 4.0 [get_ports clk]\n")
    assert verifier._sdc_clock_period(str(tmp_path)) == 10.0
