"""Host inventory protocol fixtures; no EDA execution or production state."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import Mock

import pytest

from r2g_agent import capabilities as cap


@pytest.fixture
def host(tmp_path, monkeypatch):
    flow = tmp_path / "orfs/flow"
    pdir = flow / "platforms/nangate45"
    pdir.mkdir(parents=True)
    (flow / "scripts").mkdir()
    (flow / "Makefile").write_text("# synthetic fixture, never execute make\n")
    (flow / "settings.mk").write_text("# fixture settings\n")
    helper = flow / "scripts/defaults.py"
    helper.write_text("#!/usr/bin/env python3\n")
    helper.chmod(0o755)
    files = {
        "tech.lef": "LAYER metal1\n ANTENNAAREARATIO 400 ;\nEND metal1\n",
        "cells.lef": "MACRO ANT\n CLASS CORE ANTENNACELL ;\n ANTENNADIFFAREA 1.2 ;\nEND ANT\n",
        "cells.lib": "library (fixture_typical) { nom_process : 1; nom_voltage : 1.1; nom_temperature : 25; "
                     "operating_conditions (typical) {} cell (INV_X1) {} }\n",
        "rules.lydrc": "# synthetic deck\n", "rules.lylvs": "# synthetic LVS\n",
        "rcx.rules": "synthetic extraction rules\n", "tech.lyt": "<technology/>\n",
        "cells.cdl": ".SUBCKT INV_X1 A Z VDD VSS\n.ENDS\n", "gds/cells.gds": "synthetic GDS fixture\n",
    }
    for name, content in files.items():
        path = pdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    mapping = {"TECH_LEF": "tech.lef", "SC_LEF": "cells.lef", "LIB_FILES": "cells.lib",
               "KLAYOUT_DRC_FILE": "rules.lydrc", "KLAYOUT_LVS_FILE": "rules.lylvs",
               "RCX_RULES": "rcx.rules", "KLAYOUT_TECH_FILE": "tech.lyt", "CDL_FILE": "cells.cdl"}
    config = "\n".join("export %s = $(PLATFORM_DIR)/%s" % item for item in mapping.items())
    config += "\nexport GDS_FILES = $(sort $(wildcard $(PLATFORM_DIR)/gds/*.gds)) $(ADDITIONAL_GDS)\n"
    (pdir / "config.mk").write_text(config)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    env = {"FLOW_DIR": str(flow), "ORFS_ROOT": str(flow.parent), "PATH": str(bindir)}
    for name, (variable, _, _) in cap.TOOLS.items():
        tool = bindir / ("python3" if name == "orfs_python" else name)
        tool.write_text("#!/bin/sh\nprintf 'fixture version 1.0\\n'\n")
        tool.chmod(0o755)
        env[variable] = str(tool)
    probe = Mock(return_value={"status": "ok", "text": "fixture version 1.0\n", "exit_code": 0})
    # Return a fresh dict: version observations must not mutate shared fixtures.
    probe.side_effect = lambda *a, **kw: {"status": "ok", "text": "fixture version 1.0\n", "exit_code": 0}
    monkeypatch.setattr(cap, "_probe", probe)
    return {"env": env, "flow": flow, "platform": pdir, "probe": probe}


def test_inventory_ready_is_not_a_verified_toolchain_or_manufacturing_claim(host):
    value = cap.inventory(resolved_env=host["env"])
    verdict = cap.check_requirements(value)
    assert verdict["verdict"] == "READY"
    assert verdict["combination_verification"] == "UNVERIFIED"
    assert value["verified_combinations"] == []
    assert value["combinations"][0]["status"] == "unverified"
    assert value["platform"]["role"] == "predictive_research"
    assert value["platform"]["manufacturing_qualified"] is False
    metadata = value["assets"]["timing_libraries"]["library_metadata"][0]
    assert metadata["libraries"] == ["fixture_typical"]
    assert metadata["operating_conditions"] == ["typical"]
    assert metadata["nominal"]["nom_voltage"] == "1.1"
    assert metadata["cell_count"] == 1
    assert value["assets"]["antenna"]["details"]["usable_diodes"] == ["ANT"]
    for item in value["assets"]["timing_libraries"]["files"]:
        assert item["sha256"] == hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()


def test_selected_tool_path_hash_and_fixed_probe_args_are_preserved(host):
    value = cap.inventory(resolved_env=host["env"])
    tool = value["tools"]["yosys"]
    assert tool["path"] == host["env"]["YOSYS_EXE"]
    assert tool["sha256"] == hashlib.sha256(Path(tool["path"]).read_bytes()).hexdigest()
    assert tool["usable"] is True
    assert [tool["path"], "-V"] in [c.args[0] for c in host["probe"].call_args_list]
    assert tool["format_validation"] == "unverified"
    assert value["tools"]["netgen"]["status"] == "present"
    assert value["tools"]["netgen"]["usable"] is False
    assert not any(c.args[0][0] == host["env"]["NETGEN_EXE"] for c in host["probe"].call_args_list)


def test_no_version_probe_means_present_only_and_cannot_admit_execution(host):
    value = cap.inventory(resolved_env=host["env"], probe_versions=False)
    host["probe"].assert_not_called()
    assert value["tools"]["yosys"]["status"] == "present"
    assert value["tools"]["yosys"]["usable"] is False
    assert cap.check_requirements(value)["verdict"] == "UNVERIFIED"


def test_selected_tool_inventory_never_reads_platform_assets(host, monkeypatch):
    original = cap._Reader.read

    def guarded_read(self, path, **kwargs):
        assert not Path(path).is_relative_to(host["flow"])
        return original(self, path, **kwargs)

    monkeypatch.setattr(cap._Reader, "read", guarded_read)
    result = cap.tool_inventory(("iverilog",), resolved_env=host["env"])
    assert set(result["tools"]) == {"iverilog"}
    assert result["tools"]["iverilog"]["usable"]
    assert "assets" not in result
    assert result["observed_bytes"] == Path(host["env"]["IVERILOG_EXE"]).stat().st_size
    host["probe"].assert_called_once()


def test_loader_environment_changes_tool_inventory_identity(host):
    first = cap.tool_inventory(("iverilog",), resolved_env=host["env"])
    changed = cap.tool_inventory(("iverilog",), resolved_env={**host["env"], "LD_LIBRARY_PATH": "/other/host/libs"})
    assert first["tools"]["iverilog"]["sha256"] == changed["tools"]["iverilog"]["sha256"]
    assert first["environment"]["sha256"] != changed["environment"]["sha256"]
    assert first["inventory_id"] != changed["inventory_id"]


@pytest.mark.parametrize("names", [[], ["shell"], "iverilog", ["iverilog; echo injected"]])
def test_selected_tool_inventory_requires_registered_fixed_tools(host, names):
    with pytest.raises(ValueError):
        cap.tool_inventory(names, resolved_env=host["env"])
    host["probe"].assert_not_called()


@pytest.mark.parametrize("name", ["../nangate45", "/etc", "nangate45; true", "nangate45\n", ""])
def test_platform_cannot_be_a_path_or_command(host, name):
    with pytest.raises(ValueError):
        cap.inventory(name, resolved_env=host["env"])
    host["probe"].assert_not_called()


@pytest.mark.parametrize("platform", ["asap7", "sky130hd", "sky130hs", "unknown"])
def test_nonagent_platform_never_inherits_installed_tool_support(host, platform):
    value = cap.inventory(platform, resolved_env=host["env"])
    assert value["platform"]["agent_supported"] is False
    assert value["verified_combinations"] == []
    assert cap.check_requirements(value)["verdict"] == "BLOCKED"
    assert value["assets"]["lvs"]["engine"] == "unverified"


@pytest.mark.parametrize("asset", ["rules.lydrc", "rules.lylvs", "rcx.rules", "cells.lib", "gds/cells.gds", "cells.cdl"])
def test_missing_required_platform_assets_block_even_when_binaries_exist(host, asset):
    (host["platform"] / asset).unlink()
    value = cap.inventory(resolved_env=host["env"])
    assert cap.check_requirements(value)["verdict"] == "BLOCKED"


def test_zero_area_antenna_diode_is_not_ready(host):
    path = host["platform"] / "cells.lef"
    path.write_text(path.read_text().replace("1.2", "0.0"))
    value = cap.inventory(resolved_env=host["env"])
    assert value["assets"]["antenna"]["status"] == "unverified"
    assert cap.check_requirements(value)["verdict"] == "UNVERIFIED"


def test_unknown_make_expression_is_not_silently_accepted(host):
    path = host["platform"] / "config.mk"
    path.write_text(path.read_text().replace("$(PLATFORM_DIR)/cells.lib", "$(PLATFORM_DIR)/$(UNKNOWN_CORNER).lib"))
    value = cap.inventory(resolved_env=host["env"])
    assert value["assets"]["timing_libraries"]["status"] == "unverified"
    assert value["assets"]["timing_libraries"]["unresolved"]
    assert cap.check_requirements(value)["verdict"] == "UNVERIFIED"


def test_asset_escape_is_not_read_or_hashed(host, tmp_path):
    outside = tmp_path / "outside.lib"
    outside.write_text("PRIVATE_FILE_SENTINEL")
    path = host["platform"] / "cells.lib"
    path.unlink()
    path.symlink_to(outside)
    value = cap.inventory(resolved_env=host["env"])
    asset = value["assets"]["timing_libraries"]
    assert asset["status"] == "unverified"
    assert asset["files"][0]["sha256"] is None
    assert "PRIVATE_FILE_SENTINEL" not in json.dumps(value)


@pytest.mark.parametrize("assignment", ["export YOSYS_EXE :=", "override export YOSYS_EXE =", "YOSYS_EXE ::="])
def test_orfs_settings_cannot_silently_replace_pinned_tool(host, assignment):
    (host["flow"] / "settings.mk").write_text(assignment + " /some/other/yosys\n")
    value = cap.inventory(resolved_env=host["env"])
    assert value["tool_pin_overrides"][0]["variable"] == "YOSYS_EXE"
    assert cap.check_requirements(value)["verdict"] == "UNVERIFIED"


def test_nonexecutable_orfs_helper_blocks_admission(host):
    (host["flow"] / "scripts/defaults.py").chmod(0o644)
    assert "orfs:defaults_helper_executable" in cap.check_requirements(cap.inventory(resolved_env=host["env"]))["missing"]


def test_tool_failure_is_unverified_and_environment_secrets_are_not_returned(host):
    host["env"]["SECRET_API_KEY"] = "MUST_NOT_APPEAR"
    host["probe"].side_effect = lambda *a, **k: {"status": "timeout", "text": "", "exit_code": -9}
    value = cap.inventory(resolved_env=host["env"])
    assert cap.check_requirements(value)["verdict"] == "UNVERIFIED"
    assert "MUST_NOT_APPEAR" not in json.dumps(value)
    assert not any("SECRET_API_KEY" in c.args[1] for c in host["probe"].call_args_list)


def test_image_requirements_do_not_require_graph_package_imports(host):
    value = cap.inventory(resolved_env=host["env"])
    value["tools"]["graph_python"]["usable"] = False
    assert cap.check_requirements(value, output="image")["verdict"] == "READY"
    assert cap.check_requirements(value, output="graph")["verdict"] == "UNVERIFIED"


def test_file_and_asset_count_limits_do_not_silently_truncate(host, monkeypatch):
    monkeypatch.setattr(cap, "MAX_FILE_BYTES", 32)
    value = cap.inventory(resolved_env=host["env"], probe_versions=False)
    assert value["assets"]["platform_config"]["status"] == "unverified"
    monkeypatch.setattr(cap, "MAX_ASSETS", 2)
    paths, unresolved = cap._asset_paths("a.lib b.lib c.lib", host["platform"], "nangate45", {})
    assert len(paths) == 2
    assert "asset list limit exceeded" in unresolved


def test_fixed_probe_enforces_output_and_time_bounds(tmp_path, monkeypatch):
    monkeypatch.setattr(cap, "repo_root", lambda: tmp_path)
    env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}
    flood = cap._probe([sys.executable, "-B", "-c", "print('x'*20000)"], env, 2)
    assert flood["status"] == "output_limit"
    assert len(flood["text"]) <= cap.MAX_OUTPUT_BYTES
    start = time.monotonic()
    waiting = cap._probe([sys.executable, "-B", "-c", "import time; time.sleep(10)"], env, 0.1)
    assert waiting["status"] == "timeout"
    assert time.monotonic() - start < 2
    assert list(tmp_path.iterdir()) == []


def test_canonical_resolver_honors_host_pin_over_orfs_env(tmp_path, monkeypatch):
    flow = tmp_path / "orfs/flow"
    flow.mkdir(parents=True)
    (flow / "Makefile").write_text("# fixture\n")
    pinned, overridden = tmp_path / "pinned-yosys", tmp_path / "overridden-yosys"
    for path in (pinned, overridden):
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    (flow.parent / "env.sh").write_text("export YOSYS_EXE=" + str(overridden) + "\n")
    for key in cap.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", os.defpath)
    monkeypatch.setenv("R2G_IGNORE_ENV_LOCAL", "1")
    monkeypatch.setenv("ORFS_ROOT", str(flow.parent))
    monkeypatch.setenv("YOSYS_EXE", str(pinned))
    monkeypatch.setenv("SECRET_API_KEY", "SHOULD_NOT_RETURN")
    env, report = cap._resolve_env(time.monotonic() + 8)
    assert report["status"] == "resolved"
    assert env["YOSYS_EXE"] == str(pinned)
    assert env["FLOW_DIR"] == str(flow)
    assert "SECRET_API_KEY" not in env


def test_differing_skill_pins_preserve_signoff_identity_and_expose_conflict(tmp_path, monkeypatch):
    scripts = []
    for name, yosys in (("signoff", "/host/pinned/yosys"), ("graph", "/host/other/yosys")):
        root = tmp_path / name
        (root / "flow").mkdir(parents=True)
        (root / "flow/_env.sh").write_text(
            "export YOSYS_EXE=" + yosys + "\nexport R2G_GRAPH_PYTHON=/host/graph/python\n")
        scripts.append(root)
    monkeypatch.setattr(cap, "signoff_scripts", lambda: scripts[0])
    monkeypatch.setattr(cap, "defgraph_scripts", lambda: scripts[1])
    monkeypatch.setenv("R2G_GRAPH_PYTHON", "/caller/pinned/python")
    env, report = cap._resolve_env(time.monotonic() + 8)
    assert env["YOSYS_EXE"] == "/host/pinned/yosys"
    assert env["R2G_GRAPH_PYTHON"] == "/caller/pinned/python"
    assert report["conflicting_skill_pins"] == ["YOSYS_EXE"]


def test_identical_skill_path_prefixes_resolve_from_the_same_original_environment(tmp_path, monkeypatch):
    roots = []
    content = ('export LD_LIBRARY_PATH="/shared/lib:${LD_LIBRARY_PATH-}"\n'
               'export PYTHONPATH="/shared/python:${PYTHONPATH-}"\n'
               'export QT_PLUGIN_PATH="/shared/qt:${QT_PLUGIN_PATH-}"\n'
               'export PATH="/shared/bin:$PATH"\n')
    for name in ("signoff", "graph"):
        root = tmp_path / name
        (root / "flow").mkdir(parents=True)
        (root / "flow/_env.sh").write_text(content)
        roots.append(root)
    monkeypatch.setattr(cap, "signoff_scripts", lambda: roots[0])
    monkeypatch.setattr(cap, "defgraph_scripts", lambda: roots[1])
    base = {"LD_LIBRARY_PATH": "/caller/lib", "PYTHONPATH": "/caller/python",
            "QT_PLUGIN_PATH": "/caller/qt", "PATH": os.defpath}
    for key, value in base.items():
        monkeypatch.setenv(key, value)

    env, report = cap._resolve_env(time.monotonic() + 8)

    assert report["status"] == "resolved"
    assert report["conflicting_skill_pins"] == []
    for key, prefix in (("LD_LIBRARY_PATH", "/shared/lib"), ("PYTHONPATH", "/shared/python"),
                        ("QT_PLUGIN_PATH", "/shared/qt"), ("PATH", "/shared/bin")):
        assert env[key] == prefix + ":" + base[key]
        assert os.environ[key] == base[key]


@pytest.mark.parametrize("variable", ["LD_LIBRARY_PATH", "PYTHONPATH", "QT_PLUGIN_PATH"])
def test_independent_resolution_still_detects_different_loader_search_order(tmp_path, monkeypatch, variable):
    roots = []
    for name, prefix in (("signoff", "/host/first:/host/second"), ("graph", "/host/second:/host/first")):
        root = tmp_path / name
        (root / "flow").mkdir(parents=True)
        (root / "flow/_env.sh").write_text('export ' + variable + '="' + prefix + ':${' + variable + '-}"\n')
        roots.append(root)
    monkeypatch.setattr(cap, "signoff_scripts", lambda: roots[0])
    monkeypatch.setattr(cap, "defgraph_scripts", lambda: roots[1])
    monkeypatch.setenv(variable, "/caller/base")

    env, report = cap._resolve_env(time.monotonic() + 8)

    assert report["status"] == "resolved"
    assert report["conflicting_skill_pins"] == [variable]
    assert env[variable] == "/host/first:/host/second:/caller/base"


def test_signoff_pin_cannot_leak_into_graph_resolution(tmp_path, monkeypatch):
    roots = []
    for name, content in (("signoff", "export YOSYS_EXE=/host/signoff/yosys\n"
                                    "export R2G_GRAPH_PYTHON=/host/signoff/python\n"),
                          ("graph", "export R2G_GRAPH_PYTHON=/host/graph/python\n")):
        root = tmp_path / name
        (root / "flow").mkdir(parents=True)
        (root / "flow/_env.sh").write_text(content)
        roots.append(root)
    monkeypatch.setattr(cap, "signoff_scripts", lambda: roots[0])
    monkeypatch.setattr(cap, "defgraph_scripts", lambda: roots[1])
    monkeypatch.delenv("YOSYS_EXE", raising=False)
    monkeypatch.delenv("R2G_GRAPH_PYTHON", raising=False)

    env, report = cap._resolve_env(time.monotonic() + 8)

    assert report["conflicting_skill_pins"] == ["YOSYS_EXE"]
    assert env["YOSYS_EXE"] == "/host/signoff/yosys"
    assert env["R2G_GRAPH_PYTHON"] == "/host/graph/python"


def test_second_environment_failure_cannot_return_partial_resolution(tmp_path, monkeypatch):
    roots = []
    for name, content in (("signoff", "export YOSYS_EXE=/host/signoff/yosys\n"), ("graph", "return 3\n")):
        root = tmp_path / name
        (root / "flow").mkdir(parents=True)
        (root / "flow/_env.sh").write_text(content)
        roots.append(root)
    monkeypatch.setattr(cap, "signoff_scripts", lambda: roots[0])
    monkeypatch.setattr(cap, "defgraph_scripts", lambda: roots[1])

    env, report = cap._resolve_env(time.monotonic() + 8)

    assert env == {}
    assert report["status"] == "unverified" and report["probe_status"] == "failed"


def test_conflicting_skill_pins_cannot_be_ready(host):
    value = cap.inventory(resolved_env=host["env"])
    value["environment"]["conflicting_skill_pins"] = ["OPENROAD_EXE"]
    verdict = cap.check_requirements(value)
    assert verdict["verdict"] == "UNVERIFIED"
    assert "conflicting_skill_pins" in verdict["unverified"]
