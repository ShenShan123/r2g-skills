"""Real Make expansion tests with tiny synthetic, non-EDA Makefiles."""
from pathlib import Path
import shutil
import sys

import pytest

from tehm.assets import flow_config_probe as probe


@pytest.fixture
def setup(tmp_path):
    make = shutil.which("make")
    if not make:
        pytest.skip("GNU Make is unavailable")
    project, root = tmp_path / "project", tmp_path / "orfs"
    (project / "constraints").mkdir(parents=True)
    scripts = root / "flow" / "scripts"
    scripts.mkdir(parents=True)
    config = project / "constraints" / "config.mk"
    config.write_text("PLATFORM = unit\nDESIGN_NAME = example\n")
    (scripts / "defaults.py").write_text("# fixture; no Python execution\n")
    (scripts / "variables.json").write_text("{}\n")
    (root / "flow" / "Makefile").write_text(
        "include $(DESIGN_CONFIG)\nROUTING_LAYER_ADJUSTMENT ?= 0.5\n"
        f"SCRIPTS_DIR := {scripts}\n")
    args = {"keys": ("ROUTING_LAYER_ADJUSTMENT",), "make_exe": Path(make),
            "python_exe": Path(sys.executable), "openroad_exe": Path(sys.executable),
            "yosys_exe": Path(sys.executable)}
    return project, root, config, args


def test_probe_uses_make_defaults_and_hashes_all_loaded_inputs(setup):
    project, root, config, args = setup
    result = probe.probe_flow_config(project, root, **args)
    assert result["values"]["ROUTING_LAYER_ADJUSTMENT"] == "0.5"
    assert str(config) in result["input_sha256"]
    assert str(root / "flow/scripts/variables.json") in result["input_sha256"]
    assert result["eda_executed"] is False


def test_design_assignment_overrides_default_and_ambient_environment(setup, monkeypatch):
    project, root, config, args = setup
    config.write_text(config.read_text() + "ROUTING_LAYER_ADJUSTMENT := 0.3\n")
    monkeypatch.setenv("ROUTING_LAYER_ADJUSTMENT", "0.9")
    assert probe.probe_flow_config(project, root, **args)["values"]["ROUTING_LAYER_ADJUSTMENT"] == "0.3"


def test_probe_detects_change_between_expansions(setup, monkeypatch):
    project, root, config, args = setup
    original = probe.subprocess.run
    calls = 0

    def changing(*pos, **kw):
        nonlocal calls
        calls += 1
        if calls == 2:
            config.write_text(config.read_text() + "ROUTING_LAYER_ADJUSTMENT = 0.2\n")
        return original(*pos, **kw)

    monkeypatch.setattr(probe.subprocess, "run", changing)
    with pytest.raises(ValueError, match="changed during probe"):
        probe.probe_flow_config(project, root, **args)


def test_probe_rejects_non_numeric_effective_value(setup):
    project, root, config, args = setup
    config.write_text(config.read_text() + "ROUTING_LAYER_ADJUSTMENT = unknown\n")
    with pytest.raises(ValueError, match="numeric"):
        probe.probe_flow_config(project, root, **args)


def test_probe_rejects_unknown_keys_before_make_execution(setup):
    project, root, _, args = setup
    with pytest.raises(ValueError, match="supported unique keys"):
        probe.probe_flow_config(project, root, **{**args, "keys": ("SDC_FILE",)})
