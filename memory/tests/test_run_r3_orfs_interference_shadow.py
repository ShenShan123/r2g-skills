from pathlib import Path

from scripts.run_r3_orfs_interference_shadow import _derive_edits, _post_route
from scripts.run_r3_orfs_interference_challenge import _candidate


def _project(root: Path, *, utilization: str) -> Path:
    project = root / f"project-{utilization}"
    constraints = project / "constraints"
    constraints.mkdir(parents=True)
    source = project / "demo.v"
    source.write_text("module demo(input wire clk); endmodule\n")
    (constraints / "config.mk").write_text(
        "export DESIGN_NAME = demo\n"
        "export PLATFORM = sky130hs\n"
        f"export VERILOG_FILES = {source}\n"
        f"export CORE_UTILIZATION = {utilization}\n")
    return project


def test_orfs_training_delta_is_explicit(tmp_path):
    before = _project(tmp_path, utilization="28")
    after = _project(tmp_path, utilization="32")
    assert _derive_edits(before, after)["CORE_UTILIZATION"] == "32"


def test_orfs_shadow_route_forces_both_gated_fallbacks():
    candidate = _candidate("demo", core_utilization="99")
    route = _post_route("case-demo", candidate)
    assert route.decision == "INAPPLICABLE"
    assert route.memory_budget == 0
    assert route.no_memory_budget == 1
