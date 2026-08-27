from pathlib import Path


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
