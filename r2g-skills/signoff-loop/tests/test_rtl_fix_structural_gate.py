"""Guards the WIRING of the structural-preservation gate into the RTL auto-fix loop.

`test_check_structural_preservation.py` proves the seven B-threshold RULES are right.
This file proves they actually FIRE: until 2026-07-24 `tools/fix_orfs_failures.py`
only ever ran `snapshot` and nothing in the repo called `verify`, so the
anti-hollowing-out gate was armed and inert (failure-patterns #56 GATE-P1-01).

The subtle part is not the call — it is the BASELINE LIFETIME. `_snapshot_baseline`
used to overwrite `rtl_baseline.json` on every invocation, and the RTL-error handler
runs once per fix iteration. Wiring `verify` in without fixing that would have
compared each edited design against a fingerprint taken AFTER the edit — a gate that
verdicts 'pass' no matter how far the design is gutted. `test_baseline_is_never_
overwritten_after_an_edit` is the test that would have caught it.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_TOOL = ROOT / "tools" / "fix_orfs_failures.py"

_spec = importlib.util.spec_from_file_location("fix_orfs_failures", _TOOL)
fox = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fox)


_CLEAN_RTL = """\
module widget (
    input  wire       clk,
    input  wire [7:0] din,
    output reg  [7:0] dout
);
    wire [7:0] stage;
    assign stage = din ^ 8'hA5;
    always @(posedge clk) dout <= stage;
endmodule

module widget_helper (input wire a, output wire b);
    assign b = ~a;
endmodule
"""

# The classic cheat: keep the port list, delete everything that computes.
_GUTTED_RTL = """\
module widget (
    input  wire       clk,
    input  wire [7:0] din,
    output reg  [7:0] dout
);
endmodule
"""


@pytest.fixture
def case(tmp_path, monkeypatch):
    """A design case rooted in tmp_path, with fox.CASES pointed at it."""
    cases_root = tmp_path / "design_cases"
    case_dir = cases_root / "widget"
    (case_dir / "rtl").mkdir(parents=True)
    (case_dir / "rtl" / "widget.v").write_text(_CLEAN_RTL)
    (case_dir / "constraints").mkdir()
    (case_dir / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = widget\nexport PLATFORM = nangate45\n")
    monkeypatch.setattr(fox, "CASES", cases_root)
    return case_dir


def _gut(case_dir):
    (case_dir / "rtl" / "widget.v").write_text(_GUTTED_RTL)


def _write_log(case_dir, text):
    """A log where `_find_log` actually looks (batch_logs/orfs.log, preference 1)."""
    logs = case_dir / "batch_logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "orfs.log").write_text(text)


# --------------------------------------------------------------------------- #
# baseline lifetime — the trap that made the wiring meaningless                #
# --------------------------------------------------------------------------- #
def test_first_snapshot_records_the_original_design(case):
    result = fox._snapshot_baseline(case, "widget")
    assert result["status"] == "ok"
    recorded = json.loads(fox._baseline_path(case).read_text())
    assert recorded["module_count"] == 2
    assert recorded["always_blocks"] == 1


def test_baseline_is_never_overwritten_after_an_edit(case):
    """THE regression test: re-snapshotting would fingerprint the gutted design and
    every later verify would pass."""
    fox._snapshot_baseline(case, "widget")
    original = fox._baseline_path(case).read_text()

    _gut(case)
    second = fox._snapshot_baseline(case, "widget")

    assert second["status"] == "preserved"
    assert fox._baseline_path(case).read_text() == original, \
        "baseline was overwritten with the post-edit design — the gate is now blind"
    # and the gate still sees the damage
    assert fox._verify_structure(case)["status"] == "reject"


# --------------------------------------------------------------------------- #
# the gate fires                                                              #
# --------------------------------------------------------------------------- #
def test_no_baseline_is_not_a_pass(case):
    """First iteration: nothing to compare against. Must NOT read as 'pass'."""
    result = fox._verify_structure(case)
    assert result["status"] == "no_baseline"


def test_unchanged_rtl_verifies_pass(case):
    fox._snapshot_baseline(case, "widget")
    assert fox._verify_structure(case)["status"] == "pass"


def test_gutted_rtl_is_rejected_with_named_reasons(case):
    fox._snapshot_baseline(case, "widget")
    _gut(case)
    result = fox._verify_structure(case)
    assert result["status"] == "reject"
    assert result["reasons_reject"], "a reject must say WHY"
    joined = " ".join(result["reasons_reject"])
    assert "module count dropped" in joined or "always blocks dropped" in joined
    # the machine-readable report is written where the operator/LLM can read it
    assert Path(result["report"]).is_file()


def test_verify_survives_a_missing_rtl_dir(case):
    """A broken case must produce a verdict, not an exception."""
    fox._snapshot_baseline(case, "widget")
    for f in (case / "rtl").iterdir():
        f.unlink()
    assert fox._verify_structure(case)["status"] in {"reject", "error"}


# --------------------------------------------------------------------------- #
# CLI: the exit code IS the verdict                                           #
# --------------------------------------------------------------------------- #
def _cli(monkeypatch, argv):
    monkeypatch.setattr(fox.sys, "argv", ["fix_orfs_failures.py", *argv])
    return fox.main()


def test_rtl_verify_exit_codes(case, monkeypatch):
    # no baseline yet -> 1 (fail-closed, not a pass)
    assert _cli(monkeypatch, ["--rtl-verify", "widget"]) == 1
    fox._snapshot_baseline(case, "widget")
    # clean -> 0
    assert _cli(monkeypatch, ["--rtl-verify", "widget"]) == 0
    # gutted -> 2 (reject)
    _gut(case)
    assert _cli(monkeypatch, ["--rtl-verify", "widget"]) == 2


def test_rtl_verify_unknown_case_fails_closed(case, monkeypatch):
    assert _cli(monkeypatch, ["--rtl-verify", "no_such_design"]) == 1


# --------------------------------------------------------------------------- #
# the detector dump carries the verdict, and verifies BEFORE it snapshots      #
# --------------------------------------------------------------------------- #
def test_context_dump_reports_the_structural_verdict(case, monkeypatch):
    """Iteration 1 records the baseline; iteration 2 (after a gutting edit) must
    report 'reject' in the dump AND exit 2 — proving verify runs before snapshot."""
    _write_log(case, "[INFO] starting\nERROR: syntax error in widget.v:3\n")

    first = fox.apply_rtl_error_fix("widget")
    assert first["status"] == "context_dumped", first
    assert first["structural_check"]["status"] == "no_baseline"
    assert first["baseline_status"] == "ok"

    _gut(case)
    second = fox.apply_rtl_error_fix("widget")
    assert second["status"] == "context_dumped"
    assert second["structural_check"]["status"] == "reject"
    # 'preserved', not 'ok' — proves verify ran against the ORIGINAL fingerprint
    assert second["baseline_status"] == "preserved"

    # and the CLI surfaces it as a non-zero exit even though the dump succeeded
    assert _cli(monkeypatch, ["--rtl-error", "widget"]) == 2


def test_context_dump_still_exits_zero_when_structure_is_preserved(case, monkeypatch):
    _write_log(case, "ERROR: syntax error in widget.v:3\n")
    assert _cli(monkeypatch, ["--rtl-error", "widget"]) == 0   # iteration 1
    assert _cli(monkeypatch, ["--rtl-error", "widget"]) == 0   # iteration 2, unchanged RTL
