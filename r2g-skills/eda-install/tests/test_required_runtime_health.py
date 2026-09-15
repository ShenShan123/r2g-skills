"""Real standalone checkers, fake tool processes: no EDA install or network."""
import os
from pathlib import Path
import subprocess

import pytest

SKILLS = Path(__file__).resolve().parents[2]
ALL = ('eda-install', 'signoff-loop', 'def-graph')
EDA = ALL[:2]
TOOLS = ('OPENROAD_EXE', 'YOSYS_EXE', 'IVERILOG_EXE', 'VVP_EXE')


def fixture_tree(tmp_path, skill):
    root = tmp_path / skill
    flow = root / 'scripts/flow'
    flow.mkdir(parents=True)
    (flow / 'check_env.sh').write_text((SKILLS / skill / 'scripts/flow/check_env.sh').read_text())
    orfs = root / 'orfs'
    (orfs / 'flow').mkdir(parents=True)
    (orfs / 'flow/Makefile').write_text('# fixture\n')
    tools = {}
    for name in TOOLS:
        path = root / name
        path.write_text('#!/bin/sh\nprintf "tool fixture 1.0\\n"\n')
        path.chmod(0o755)
        tools[name] = path
    (flow / '_env.sh').write_text(
        f'export ORFS_ROOT="{orfs}" FLOW_DIR="{orfs}/flow"\n' +
        ''.join(f'export {name}="{path}"\n' for name, path in tools.items()) +
        'export R2G_GRAPH_PYTHON="/usr/bin/python3"\n')
    return flow, orfs, tools


def run_checker(flow, *, python_broken=False):
    env = dict(os.environ, PATH='/usr/bin:/bin', R2G_STRICT_PLATFORMS='', R2G_TARGET_PLATFORM='')
    env.pop('PYTHONHOME', None)
    env.pop('PYTHONPATH', None)
    if python_broken:
        bindir = flow.parent / 'broken-python'
        bindir.mkdir()
        executable = bindir / 'python3'
        executable.write_text('#!/bin/sh\nexit 127\n')
        executable.chmod(0o755)
        env['PATH'] = str(bindir) + ':/usr/bin:/bin'
    return subprocess.run(['bash', str(flow / 'check_env.sh')], env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize('skill', ALL)
def test_healthy_required_processes_pass(tmp_path, skill):
    flow, _, _ = fixture_tree(tmp_path, skill)
    result = run_checker(flow)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('skill', EDA)
@pytest.mark.parametrize('tool', TOOLS)
def test_executable_but_broken_required_tool_fails(tmp_path, skill, tool):
    flow, _, tools = fixture_tree(tmp_path, skill)
    tools[tool].write_text('#!/bin/sh\nexit 127\n')
    result = run_checker(flow)
    assert result.returncode != 0
    assert 'MISS runtime: ' + tool in result.stdout


@pytest.mark.parametrize('skill', ALL)
def test_required_python_runtime_failure_fails(tmp_path, skill):
    flow, _, _ = fixture_tree(tmp_path, skill)
    result = run_checker(flow, python_broken=True)
    assert result.returncode != 0
    assert 'MISS runtime: python3' in result.stdout


@pytest.mark.parametrize('skill', ALL)
def test_missing_required_orfs_makefile_fails(tmp_path, skill):
    flow, orfs, _ = fixture_tree(tmp_path, skill)
    (orfs / 'flow/Makefile').unlink()
    assert run_checker(flow).returncode != 0


@pytest.mark.parametrize('skill', EDA)
def test_empty_successful_version_probe_is_not_health_evidence(tmp_path, skill):
    flow, _, tools = fixture_tree(tmp_path, skill)
    tools['OPENROAD_EXE'].write_text('#!/bin/sh\nexit 0\n')
    assert run_checker(flow).returncode != 0


@pytest.mark.parametrize('skill', EDA)
def test_nonexecutable_required_file_fails(tmp_path, skill):
    flow, _, tools = fixture_tree(tmp_path, skill)
    tools['OPENROAD_EXE'].chmod(0o644)
    assert run_checker(flow).returncode != 0


def test_def_graph_does_not_require_eda_executables(tmp_path):
    flow, _, tools = fixture_tree(tmp_path, 'def-graph')
    for path in tools.values():
        path.write_text('#!/bin/sh\nexit 127\n')
    assert run_checker(flow).returncode == 0


def test_def_graph_requires_flow_data_directory(tmp_path):
    flow, _, _ = fixture_tree(tmp_path, 'def-graph')
    with (flow / '_env.sh').open('a') as stream:
        stream.write('export FLOW_DIR="/nonexistent-tehm-flow-data"\n')
    assert run_checker(flow).returncode != 0
