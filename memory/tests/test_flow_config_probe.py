"""Real Make expansion tests with tiny synthetic, non-EDA Makefiles."""
from pathlib import Path
import shutil
import sys

import pytest

from tehm.assets import flow_config_probe as probe
from test_orfs_runtime_resources import resource_binding


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


def _version_tool(project):
    tool = project.parent / "pinned-klayout"
    marker = project.parent / "version-invocations"
    tool.write_text(f"#!/bin/sh\nprintf 'version\\n' >> '{marker}'\nprintf 'KLayout 0.29.12\\n'\n")
    tool.chmod(0o755)
    return tool, marker


def test_v2_binds_real_make_version_query_not_ambient_tool(setup, monkeypatch):
    project, root, _, args = setup
    tool, marker = _version_tool(project)
    makefile = root / "flow/Makefile"
    makefile.write_text(makefile.read_text() +
        "KLAYOUT_CMD ?= /this/host/tool/must/not/run\n"
        "KLAYOUT_VERSION := $(shell $(KLAYOUT_CMD) -v)\n")
    monkeypatch.setenv("KLAYOUT_CMD", "/ambient/must/not/run")
    result = probe.probe_flow_config(project, root, **args, klayout_exe=tool)
    assert result["version"] == "orfs-effective-config-probe-v2"
    assert result["tool_bindings"] == {"KLAYOUT_CMD": str(tool)}
    assert result["environment"]["KLAYOUT_CMD"] == str(tool)
    assert result["tool_sha256"][str(tool)] == probe._sha(tool)
    assert marker.read_text().splitlines() == ["version", "version"]
    assert "KLAYOUT_CMD" not in result["values"]
    assert result["eda_executed"] is False


def test_v1_without_pin_keeps_legacy_receipt_shape(setup):
    project, root, _, args = setup
    result = probe.probe_flow_config(project, root, **args)
    assert result["version"] == "orfs-effective-config-probe-v1"
    assert "tool_bindings" not in result
    assert "KLAYOUT_CMD" not in result["environment"]


@pytest.mark.parametrize("name", ["klayout with space", "klayout$inject", "klayout;inject"])
def test_v2_rejects_command_syntax_before_make(setup, monkeypatch, name):
    project, root, _, args = setup
    tool = project.parent / name
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: pytest.fail("Make ran"))
    with pytest.raises(ValueError, match="shell-safe"):
        probe.probe_flow_config(project, root, **args, klayout_exe=tool)


def test_v2_rejects_make_override_of_pinned_command(setup):
    project, root, _, args = setup
    tool, _ = _version_tool(project)
    makefile = root / "flow/Makefile"
    makefile.write_text(makefile.read_text() + "override KLAYOUT_CMD := /different/tool\n")
    with pytest.raises(ValueError, match="command override"):
        probe.probe_flow_config(project, root, **args, klayout_exe=tool)


def test_v2_detects_tool_bytes_changed_during_expansion(setup, monkeypatch):
    project, root, _, args = setup
    tool, _ = _version_tool(project)
    original = probe.subprocess.run
    calls = 0
    def changing(*a, **k):
        nonlocal calls
        calls += 1
        if calls == 2:
            tool.write_text(tool.read_text() + "# changed\n")
        return original(*a, **k)
    monkeypatch.setattr(probe.subprocess, "run", changing)
    with pytest.raises(ValueError, match="inputs changed"):
        probe.probe_flow_config(project, root, **args, klayout_exe=tool)


def test_v3_resources_reach_actual_make_child_environment(setup, monkeypatch):
    project, root, _, args = setup
    resources = resource_binding(project.parent / "resources")
    tool, marker = _version_tool(project)
    tool.write_text("#!/bin/sh\nprintf '%s|%s|%s|%s\\n' \"$TERM\" \"$TERMINFO\" "
                    f"\"$TERMINFO_DIRS\" \"$OPENSSL_CONF\" >> '{marker}'\n"
                    "printf 'KLayout test\\n'\n")
    makefile = root / "flow/Makefile"
    makefile.write_text(makefile.read_text() +
        "KLAYOUT_CMD ?= /host/must/not/run\n"
        "export OPENSSL_CONF := /make/default/must/not/be/used\n"
        "KLAYOUT_VERSION := $(shell $(KLAYOUT_CMD) -v)\n")
    monkeypatch.setenv("OPENSSL_CONF", "/ambient/must/not/be/used")
    result = probe.probe_flow_config(project, root, **args, klayout_exe=tool,
                                     runtime_resources=resources)
    assert result["version"] == "orfs-effective-config-probe-v3"
    assert result["runtime_resources"] == resources
    expected = "|".join(resources["environment"][key] for key in ("TERM", "TERMINFO", "TERMINFO_DIRS", "OPENSSL_CONF"))
    assert marker.read_text().splitlines() == [expected, expected]
    assert not set(resources["environment"]) & set(result["values"])
    assert result["parent_launch_binding_verified"] is False
    assert result["native_closure_proven"] is False


def test_v3_requires_klayout_pin_before_make(setup, monkeypatch):
    project, root, _, args = setup
    resources = resource_binding(project.parent / "resources")
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: pytest.fail("Make ran"))
    with pytest.raises(ValueError, match="explicit KLayout"):
        probe.probe_flow_config(project, root, **args, runtime_resources=resources)


@pytest.mark.parametrize("call", [1, 2])
def test_v3_resource_drift_rejected_during_each_expansion(setup, monkeypatch, call):
    project, root, _, args = setup
    resources = resource_binding(project.parent / "resources")
    tool, _ = _version_tool(project)
    original, calls = probe.subprocess.run, 0
    def changing(*a, **k):
        nonlocal calls
        calls += 1
        result = original(*a, **k)
        if calls == call:
            path = Path(resources["environment"]["OPENSSL_CONF"])
            path.write_text(path.read_text() + "# changed\n")
        return result
    monkeypatch.setattr(probe.subprocess, "run", changing)
    with pytest.raises(ValueError, match="resource bytes changed or SHA256"):
        probe.probe_flow_config(project, root, **args, klayout_exe=tool,
                                runtime_resources=resources)


def test_v3_rejects_make_override_of_resource_environment(setup):
    project, root, _, args = setup
    resources = resource_binding(project.parent / "resources")
    tool, _ = _version_tool(project)
    makefile = root / "flow/Makefile"
    makefile.write_text(makefile.read_text() + "override OPENSSL_CONF := /unbound/other.cnf\n")
    with pytest.raises(ValueError, match="resource environment override"):
        probe.probe_flow_config(project, root, **args, klayout_exe=tool,
                                runtime_resources=resources)
