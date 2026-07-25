"""Guards tools/check_structural_preservation.py — the anti-hollowing-out gate.

This tool decides whether an LLM's RTL edits still describe the SAME design, or
whether the agent quietly gutted the design until the tool stopped complaining.
Its seven B-threshold rules were signed off by the researcher, and until 2026-07-24
not one of them had a test: the thresholds could drift, or a regex could stop
matching, and every edit would silently verdict 'pass'.

Every test below is a NEGATIVE CONTROL — it constructs an edit that MUST be caught
and pins the exact verdict/exit code, plus a clean control per rule so a
reject-everything regression is caught too.

Exit-code contract (relied on by any caller that gates on this):
    0 = pass, 2 = reject, 3 = flag-only (needs human attention).

SCOPE: this file covers the RULES only. The gate's production WIRING into the RTL
auto-fix loop (`fix_orfs_failures.py --rtl-error` / `--rtl-verify`, and the baseline
lifetime that makes the comparison meaningful) is covered by
`test_rtl_fix_structural_gate.py`.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_TOOL = ROOT / "tools" / "check_structural_preservation.py"

_spec = importlib.util.spec_from_file_location("check_structural_preservation", _TOOL)
csp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(csp)


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #
_CLEAN_RTL = """\
module top (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] din,
    output reg  [7:0] dout,
    output wire       valid
);
    wire [7:0] stage;
    assign stage = din + 8'd1;
    assign valid = |stage;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) dout <= 8'd0;
        else        dout <= stage;
    end
endmodule

module helper (input wire a, output wire b);
    assign b = ~a;
endmodule
"""


@pytest.fixture
def rtl_dir(tmp_path):
    d = tmp_path / "rtl"
    d.mkdir()
    (d / "top.v").write_text(_CLEAN_RTL)
    return d


@pytest.fixture
def baseline(rtl_dir):
    return csp.compute_structure(rtl_dir, "top")


def _mutate(base, **overrides):
    """A 'current' structure identical to the baseline except the named fields."""
    current = json.loads(json.dumps(base))
    current.update(overrides)
    return current


# --------------------------------------------------------------------------- #
# the fingerprint itself must actually see the design                          #
# --------------------------------------------------------------------------- #
def test_snapshot_sees_the_real_structure(baseline):
    """A fingerprint of all-zeros would make every later rule vacuously pass."""
    assert baseline["module_count"] == 2
    assert baseline["always_blocks"] == 1
    assert baseline["assign_statements"] == 3   # stage, valid, helper.b
    assert baseline["code_lines"] > 10
    ports = {p["name"]: p["dir"] for p in baseline["top_ports"]}
    assert ports == {"clk": "input", "rst_n": "input", "din": "input",
                     "dout": "output", "valid": "output"}


def test_unchanged_rtl_passes(baseline, rtl_dir):
    result = csp.verify(baseline, _mutate(baseline), rtl_dir)
    assert result["verdict"] == "pass"
    assert result["reasons_reject"] == []


# --------------------------------------------------------------------------- #
# Rule 1 — top port list is EXACT (name, direction, width)                     #
# --------------------------------------------------------------------------- #
def test_rule1_rejects_a_dropped_top_port(baseline, rtl_dir):
    current = _mutate(baseline, top_ports=[p for p in baseline["top_ports"]
                                           if p["name"] != "valid"])
    result = csp.verify(baseline, current, rtl_dir)
    assert result["verdict"] == "reject"
    assert any("top port list changed" in r for r in result["reasons_reject"])


def test_rule1_rejects_a_flipped_port_direction(baseline, rtl_dir):
    ports = json.loads(json.dumps(baseline["top_ports"]))
    for p in ports:
        if p["name"] == "dout":
            p["dir"] = "input"
    result = csp.verify(baseline, _mutate(baseline, top_ports=ports), rtl_dir)
    assert result["verdict"] == "reject"


def test_rule1_rejects_a_narrowed_port(baseline, rtl_dir):
    """Width is part of the identity — narrowing a bus is a different design."""
    ports = json.loads(json.dumps(baseline["top_ports"]))
    for p in ports:
        if p["name"] == "din":
            p["width"] = 1
            p["msb"] = 0
    result = csp.verify(baseline, _mutate(baseline, top_ports=ports), rtl_dir)
    assert result["verdict"] == "reject"


# --------------------------------------------------------------------------- #
# Rule 2 — module count may rise, never fall                                   #
# --------------------------------------------------------------------------- #
def test_rule2_rejects_a_deleted_module(baseline, rtl_dir):
    current = _mutate(baseline, module_count=1, module_names=["top"])
    result = csp.verify(baseline, current, rtl_dir)
    assert result["verdict"] == "reject"
    assert any("module count dropped" in r for r in result["reasons_reject"])
    assert any("helper" in r for r in result["reasons_reject"]), "offender must be named"


def test_rule2_allows_added_modules(baseline, rtl_dir):
    current = _mutate(baseline, module_count=3,
                      module_names=sorted(baseline["module_names"] + ["extra"]))
    assert csp.verify(baseline, current, rtl_dir)["verdict"] == "pass"


# --------------------------------------------------------------------------- #
# Rule 3 — always blocks: budget is max(10%, 1)                                #
# --------------------------------------------------------------------------- #
def test_rule3_tolerates_the_budget_but_rejects_beyond_it(baseline, rtl_dir):
    base = _mutate(baseline, always_blocks=20)          # budget = ceil(2.0) = 2
    assert csp.verify(base, _mutate(base, always_blocks=18), rtl_dir)["verdict"] == "pass"
    result = csp.verify(base, _mutate(base, always_blocks=17), rtl_dir)
    assert result["verdict"] == "reject"
    assert any("always blocks dropped" in r for r in result["reasons_reject"])


def test_rule3_small_design_keeps_a_floor_of_one(baseline, rtl_dir):
    """max(1, ...) — a 1-always design may lose its only always block without
    tripping rule 3. Pinned deliberately: this is the documented threshold, and
    rules 1/2/5 are what actually catch a gutted small design."""
    base = _mutate(baseline, always_blocks=1)
    assert csp.verify(base, _mutate(base, always_blocks=0), rtl_dir)["verdict"] == "pass"


# --------------------------------------------------------------------------- #
# Rule 4 — assign statements: budget is 20%, and never trips on a drop of 1    #
# --------------------------------------------------------------------------- #
def test_rule4_rejects_a_large_assign_purge(baseline, rtl_dir):
    base = _mutate(baseline, assign_statements=100)     # budget = 20
    assert csp.verify(base, _mutate(base, assign_statements=80), rtl_dir)["verdict"] == "pass"
    result = csp.verify(base, _mutate(base, assign_statements=70), rtl_dir)
    assert result["verdict"] == "reject"
    assert any("assign statements dropped" in r for r in result["reasons_reject"])


def test_rule4_never_trips_on_a_single_assign(baseline, rtl_dir):
    """`drop > 1` guard: a 1-assign design must not reject on a 1-statement edit."""
    base = _mutate(baseline, assign_statements=1)
    assert csp.verify(base, _mutate(base, assign_statements=0), rtl_dir)["verdict"] == "pass"


# --------------------------------------------------------------------------- #
# Rule 5 — code lines: budget is max(30%, 30 lines)                            #
# --------------------------------------------------------------------------- #
def test_rule5_rejects_hollowing_the_body_out(baseline, rtl_dir):
    base = _mutate(baseline, code_lines=1000)           # budget = 300
    assert csp.verify(base, _mutate(base, code_lines=700), rtl_dir)["verdict"] == "pass"
    result = csp.verify(base, _mutate(base, code_lines=699), rtl_dir)
    assert result["verdict"] == "reject"
    assert any("code lines dropped" in r for r in result["reasons_reject"])


def test_rule5_absolute_floor_protects_small_designs(baseline, rtl_dir):
    """max(30, 30%) — on a 40-line design the 30-line floor, not the 12-line
    percentage, is the budget."""
    base = _mutate(baseline, code_lines=40)
    assert csp.verify(base, _mutate(base, code_lines=15), rtl_dir)["verdict"] == "pass"
    assert csp.verify(base, _mutate(base, code_lines=9), rtl_dir)["verdict"] == "reject"


# --------------------------------------------------------------------------- #
# Rule 6 — a new `initial` block driving a top output (the classic cheat)      #
# --------------------------------------------------------------------------- #
def test_rule6_rejects_an_initial_block_forcing_a_top_output(baseline, rtl_dir):
    """The canonical hollowing-out move: stop computing `dout`, just force it."""
    (rtl_dir / "cheat.v").write_text(
        "module cheat;\n  initial begin\n    dout = 8'd0;\n  end\nendmodule\n")
    current = csp.compute_structure(rtl_dir, "top")
    result = csp.verify(baseline, current, rtl_dir)
    assert result["verdict"] == "reject"
    assert any("initial assigns to output" in r for r in result["reasons_reject"])


def test_rule6_ignores_an_initial_on_a_non_top_signal(rtl_dir, baseline):
    """Control: initial blocks are normal in RTL — only ones driving a TOP OUTPUT
    are the cheat. A reject here would make the gate unusable."""
    (rtl_dir / "bench.v").write_text(
        "module bench;\n  reg local_sig;\n  initial begin\n    local_sig = 1'b0;\n"
        "  end\nendmodule\n")
    current = csp.compute_structure(rtl_dir, "top")
    result = csp.verify(baseline, current, rtl_dir)
    assert result["verdict"] == "pass", result["reasons_reject"]


# --------------------------------------------------------------------------- #
# Rule 7 — new translate_off is FLAGGED, never auto-rejected                   #
# --------------------------------------------------------------------------- #
def test_rule7_flags_but_does_not_reject_new_translate_off(baseline, rtl_dir):
    result = csp.verify(baseline, _mutate(baseline, translate_off=2), rtl_dir)
    assert result["verdict"] == "flag"
    assert result["reasons_reject"] == []
    assert any("translate_off" in r for r in result["reasons_flag"])


def test_reject_outranks_flag(baseline, rtl_dir):
    """A design that both hides code AND drops a module is a reject, not a flag."""
    result = csp.verify(
        baseline, _mutate(baseline, translate_off=2, module_count=1,
                          module_names=["top"]), rtl_dir)
    assert result["verdict"] == "reject"


# --------------------------------------------------------------------------- #
# CLI exit codes — what any caller wiring this gate in will actually branch on #
# --------------------------------------------------------------------------- #
def _cli(monkeypatch, argv):
    monkeypatch.setattr(csp.sys, "argv", ["check_structural_preservation.py", *argv])
    return csp.main()


def test_cli_snapshot_then_verify_roundtrip(tmp_path, rtl_dir, monkeypatch):
    snap = tmp_path / "baseline.json"
    assert _cli(monkeypatch, ["snapshot", "--rtl-dir", str(rtl_dir),
                              "--top-module", "top", "--out", str(snap)]) == 0
    assert json.loads(snap.read_text())["module_count"] == 2
    # unchanged -> exit 0
    assert _cli(monkeypatch, ["verify", "--rtl-dir", str(rtl_dir),
                              "--baseline", str(snap)]) == 0
    # gut it -> exit 2 (reject)
    (rtl_dir / "top.v").write_text(
        "module top (\n    input wire clk\n);\nendmodule\n")
    assert _cli(monkeypatch, ["verify", "--rtl-dir", str(rtl_dir),
                              "--baseline", str(snap)]) == 2


def test_cli_verify_missing_baseline_fails_closed(tmp_path, rtl_dir, monkeypatch):
    """No baseline must NOT read as a pass — the gate has nothing to compare."""
    assert _cli(monkeypatch, ["verify", "--rtl-dir", str(rtl_dir),
                              "--baseline", str(tmp_path / "absent.json")]) == 1
