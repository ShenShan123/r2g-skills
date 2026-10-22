from pathlib import Path
import os
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[3]
FLOW = REPO / "r2g-skills" / "signoff-loop" / "scripts" / "flow"


def test_slang_bridge_is_project_local_and_fail_closed():
    runner = (FLOW / "run_orfs.sh").read_text()
    bridge = (FLOW / "synth_preamble_slang_bridge.tcl").read_text()

    assert "R2G_ORFS_SCRIPT_OVERLAY" in runner
    assert "Slang-based SystemVerilog frontend" in runner
    assert "exit 69" in runner
    assert 'frontend_bridge_sha256=${R2G_FRONTEND_BRIDGE_SHA:-none}' in runner
    assert "read_slang" in bridge
    assert "-D SYNTHESIS" in bridge
    assert "write_rtlil" in bridge
    assert "if {!$needs_slang}" in bridge
    assert "R2G_ORFS_CANONICAL_SCRIPTS_DIR" in bridge


def test_slang_bridge_does_not_mutate_frozen_rtl_or_shared_orfs():
    runner = (FLOW / "run_orfs.sh").read_text()
    bridge = (FLOW / "synth_preamble_slang_bridge.tcl").read_text()

    assert 'cp -a "$FLOW_DIR/scripts/." "$R2G_ORFS_SCRIPT_OVERLAY/"' in runner
    assert 'cp "$R2G_SLANG_BRIDGE" "$R2G_ORFS_SCRIPT_OVERLAY/synth_preamble.tcl"' in runner
    assert "write_verilog" not in bridge
    assert "VERILOG_FILES) $bridge_rtlil" in bridge


@pytest.mark.parametrize("help_text,returncode,accepted", [
    ("OVERVIEW: Slang-based SystemVerilog frontend", 0, True),
    ("    read_slang [options] [filename]", 0, True),
    ("No such command or cell type: read_slang", 0, False),
    ("    read_slang [options] [filename]", 1, False),
])
def test_slang_probe_checks_command_and_exit_status(tmp_path, help_text, returncode, accepted):
    runner = (FLOW / "run_orfs.sh").read_text()
    block = runner[runner.index('  if ! ', runner.index('R2G_SLANG_BRIDGE=')):]
    block = block[:block.index('\n  fi') + len('\n  fi')]
    fake = tmp_path / "yosys"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$FAKE_HELP"\nexit "$FAKE_RC"\n')
    fake.chmod(0o700)
    result = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + block],
                            env={**os.environ, 'YOSYS_EXE': str(fake),
                                 'FAKE_HELP': help_text, 'FAKE_RC': str(returncode)},
                            capture_output=True, text=True)
    assert result.returncode == (0 if accepted else 69), result.stderr
