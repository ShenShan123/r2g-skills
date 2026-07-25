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
import os
import subprocess
import sys
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
# ...and the PROCESS actually exits with it                                    #
# --------------------------------------------------------------------------- #
# The tests above call main() in-process and assert its RETURN VALUE. That is not
# the same thing as the exit status a shell sees, and the difference is exactly how
# the gate shipped blind on 2026-07-24: `if __name__ == '__main__': main()` dropped
# the return value on the floor, so `fix_orfs_failures.py --rtl-verify <case> ||
# revert` read 0 on a structural REJECT and kept cutting. A CLI contract is only
# provable through a subprocess (failure-patterns #56 GATE-P1-02).
def _run_cli(cases_root, argv):
    env = {**os.environ, "R2G_CASES": str(cases_root)}
    return subprocess.run([sys.executable, str(_TOOL), *argv],
                          capture_output=True, text=True, env=env)


def test_cli_process_exit_status_is_the_verdict(case):
    cases_root = case.parent

    r = _run_cli(cases_root, ["--rtl-verify", "widget"])
    assert r.returncode == 1, f"no baseline must fail closed, got {r.returncode}"

    fox._snapshot_baseline(case, "widget")
    r = _run_cli(cases_root, ["--rtl-verify", "widget"])
    assert r.returncode == 0, f"preserved structure must exit 0, got {r.returncode}"

    _gut(case)
    r = _run_cli(cases_root, ["--rtl-verify", "widget"])
    assert r.returncode == 2, (
        f"a gutted design must exit 2 from the SHELL, got {r.returncode} — "
        "the verdict is being computed and then discarded at the __main__ line")


def test_cli_process_exit_status_on_rtl_error_reject(case):
    """--rtl-error dumps context AND fails the process when the edit gutted the design."""
    cases_root = case.parent
    _write_log(case, "ERROR: syntax error in widget.v:3\n")

    r = _run_cli(cases_root, ["--rtl-error", "widget"])
    assert r.returncode == 0, "iteration 1 records the baseline and exits clean"

    _gut(case)
    r = _run_cli(cases_root, ["--rtl-error", "widget"])
    assert r.returncode == 2, (
        f"a structural reject must exit 2 from the SHELL, got {r.returncode}")
    assert "STRUCTURAL REJECT" in r.stderr, "the operator/LLM must be told to revert"


def test_cases_root_env_override_defaults_to_repo(monkeypatch):
    """The env seam must not change the default: no R2G_CASES -> <repo>/design_cases."""
    monkeypatch.delenv("R2G_CASES", raising=False)
    assert fox._cases_root() == fox.BASE / "design_cases"
    monkeypatch.setenv("R2G_CASES", "/tmp/somewhere/else")
    assert fox._cases_root() == Path("/tmp/somewhere/else")


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


# --------------------------------------------------------------------------- #
# batch mode: the DOCUMENTED invocation must actually do something            #
# --------------------------------------------------------------------------- #
# README and failure-patterns.md both say `python3 tools/fix_orfs_failures.py`
# "classifies every failure in design_cases/_batch/orfs_results.jsonl". The code only
# ever read /tmp/fail_categories.json — which nothing in this repo writes — so the
# documented command was dead on arrival (2026-07-25).
def _sweep(cases_root, records):
    batch = cases_root / "_batch"
    batch.mkdir(parents=True, exist_ok=True)
    (batch / "orfs_results.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records))
    return batch / "orfs_results.jsonl"


def test_classifies_failures_from_the_sweep_results_file(case):
    cases_root = case.parent
    _write_log(case, "ERROR: syntax error in widget.v:3\n")
    results = _sweep(cases_root, [
        {"case": "widget", "design": "widget", "orfs": "fail(2)"},
        {"case": "widget", "design": "widget", "orfs": "pass"},   # must be ignored
    ])
    cats = fox.classify_failures(results)
    assert "other" in cats, cats                  # RTL errors dispatch via apply_other
    assert [e[0] for e in cats["other"]] == ["widget"]
    assert "syntax error" in cats["other"][0][2]


def test_passing_and_skipped_cases_are_not_classified(case):
    results = _sweep(case.parent, [
        {"case": "widget", "orfs": "pass"},
        {"case": "widget", "status": "skip", "reason": "no config.mk"},
    ])
    assert fox.classify_failures(results) == {}


def test_memory_and_include_categories_are_recognised(case):
    cases_root = case.parent
    _write_log(case, "ERROR: Memory bank is too big for the target SYNTH_MEMORY_MAX_BITS\n")
    results = _sweep(cases_root, [{"case": "widget", "orfs": "fail(2)"}])
    assert "memory_inference" in fox.classify_failures(results)

    _write_log(case, "ERROR: Can't open include file `defs.vh'\n")
    assert "missing_include" in fox.classify_failures(results)


def test_batch_mode_without_a_sweep_fails_loudly_not_silently(case, monkeypatch, tmp_path):
    """No results file must be a clear error naming the fix — never a silent no-op."""
    monkeypatch.setattr(fox, "CASES", tmp_path / "empty_cases")
    assert _cli(monkeypatch, []) == 1
