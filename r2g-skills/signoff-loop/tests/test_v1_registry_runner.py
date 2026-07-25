"""Guards tools/run_v1_validation_registry.py — the ARBITER of every gate verdict.

Until 2026-07-24 this file was the one gate surface with no tests at all: a bug in
the runner cannot be caught by the gates it runs, because the runner decides what
"caught" means. So this suite is a NEGATIVE-CONTROL suite — every assertion drives
the runner into a state where a green verdict would be a lie, and pins that it goes
red instead.

The fail-closed contract under test:

  * a failing executable condition  -> gate verdict 'fail', process exit 1
  * an UNRESOLVABLE command binary  -> 'harness_error' RECORDED, the sweep CONTINUES,
                                       the evidence report is STILL written, exit 1
  * a stage timeout                 -> 'timeout', ok=False, exit 1
  * a builtin that raises           -> 'harness_error', ok=False, exit 1
  * --dry-run                       -> nothing executed, exit 0, no false pass
  * a registry that lies about the  -> lint errors before ANY condition runs
    frozen protocol digest

The missing-binary case is the reason this file exists: a bare
`subprocess.run(["missing-tool"])` raised FileNotFoundError out of
`command_gates`, which (a) aborted the sweep so every downstream condition went
unexecuted, and (b) skipped the report write, leaving the PREVIOUS run's
gate-conditions.json on disk still claiming its verdict — a stale green surviving
a red run. Non-zero exit alone is not enough; the evidence has to be honest too.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_TOOL = ROOT / "tools" / "run_v1_validation_registry.py"
_REGISTRY = ROOT / "tools" / "v1_validation_registry.yaml"

_spec = importlib.util.spec_from_file_location("run_v1_validation_registry", _TOOL)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


# --------------------------------------------------------------------------- #
# helpers — mutate the REAL registry so the lint (spec digest, GC/VAL coverage) #
# still passes and only the probed condition differs                           #
# --------------------------------------------------------------------------- #
def _registry():
    return json.loads(_REGISTRY.read_text(encoding="utf-8"))


def _write(tmp_path, reg, name="registry.yaml"):
    path = tmp_path / name
    path.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    return path


def _set_condition(reg, gate, condition_id, **fields):
    """Replace one gate condition's execution fields, keeping id/title/evidence/
    requirements so the traceability lint still passes."""
    for condition in reg["gate_conditions"][gate]:
        if condition["id"] == condition_id:
            for key in ("builtin", "suite", "command", "cases", "procedure"):
                condition.pop(key, None)
            condition.update(fields)
            return condition
    raise AssertionError(f"{condition_id} not in {gate}")


def _run_gates(tmp_path, reg, gate="OPS-GATE"):
    """Run `gates --gate <gate>` over a mutated registry; return (exit, report)."""
    registry_path = _write(tmp_path, reg)
    out = tmp_path / "gate-conditions.json"
    code = runner.main(
        ["--registry", str(registry_path), "gates", "--gate", gate, "--out", str(out)]
    )
    report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return code, report, out


def _condition(report, condition_id):
    for record in report["conditions"]:
        if record["id"] == condition_id:
            return record
    raise AssertionError(f"{condition_id} absent from the report")


# --------------------------------------------------------------------------- #
# baseline: the shipped registry must lint, and dry-run must execute nothing    #
# --------------------------------------------------------------------------- #
def test_shipped_registry_lints_clean():
    """Regression on the frozen-protocol pin: if the spec doc is edited without
    recomputing protocol.sha256, this is where it surfaces."""
    reg = json.loads(_REGISTRY.read_text(encoding="utf-8"))
    assert runner.lint_registry(_REGISTRY, reg) == []


def test_dry_run_executes_nothing_and_does_not_pass_anything(tmp_path):
    reg = _registry()
    # a condition that would FAIL if it ran at all
    _set_condition(reg, "OPS-GATE", "GC-OPS-02",
                   kind="command", command=["false"], timeout_s=30)
    registry_path = _write(tmp_path, reg)
    out = tmp_path / "gate-conditions.json"
    code = runner.main(["--registry", str(registry_path), "gates", "--gate", "OPS-GATE",
                        "--dry-run", "--out", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0
    assert report["dry_run"] is True
    assert report["gate_summaries"]["OPS-GATE"]["verdict"] == "dry_run"
    # not_scheduled, never a fabricated pass
    assert _condition(report, "GC-OPS-02")["execution_status"] == "not_scheduled"
    assert _condition(report, "GC-OPS-02")["ok"] is None


# --------------------------------------------------------------------------- #
# fail-closed: every way an executable condition can go wrong must go red       #
# --------------------------------------------------------------------------- #
def test_failing_command_fails_the_gate(tmp_path):
    reg = _registry()
    _set_condition(reg, "OPS-GATE", "GC-OPS-02",
                   kind="command", command=["false"], timeout_s=30)
    code, report, _ = _run_gates(tmp_path, reg)
    record = _condition(report, "GC-OPS-02")
    assert (code, record["ok"], record["execution_status"]) == (1, False, "completed")
    assert report["gate_summaries"]["OPS-GATE"]["verdict"] == "fail"


def test_missing_binary_is_harness_error_not_an_abort(tmp_path):
    """The 2026-07-24 defect: an unresolvable command raised out of command_gates.

    Three things must hold, and only the first one held before the fix:
      1. the run exits non-zero,
      2. the sweep CONTINUES so downstream conditions are still executed,
      3. the evidence report is still written (else a stale green survives a red run).
    """
    reg = _registry()
    # broken condition FIRST, working builtin after it -> proves the sweep continues
    _set_condition(reg, "OPS-GATE", "GC-OPS-01",
                   kind="command", command=["r2g-no-such-binary-xyz"], timeout_s=30)
    code, report, out = _run_gates(tmp_path, reg)

    assert code == 1
    assert out.exists(), "no evidence report written — a stale prior verdict survives"

    broken = _condition(report, "GC-OPS-01")
    assert broken["execution_status"] == "harness_error"
    assert broken["ok"] is False
    assert "FileNotFoundError" in broken["error"]

    # the condition AFTER the broken one still ran
    downstream = _condition(report, "GC-OPS-02")
    assert downstream["execution_status"] == "completed"
    assert report["gate_summaries"]["OPS-GATE"]["verdict"] == "fail"


def test_timeout_is_recorded_and_fails_closed(tmp_path):
    reg = _registry()
    _set_condition(reg, "OPS-GATE", "GC-OPS-02",
                   kind="command", command=["sleep", "30"], timeout_s=1)
    code, report, _ = _run_gates(tmp_path, reg)
    record = _condition(report, "GC-OPS-02")
    assert (code, record["ok"], record["execution_status"]) == (1, False, "timeout")


def test_builtin_exception_is_harness_error_not_a_pass(tmp_path, monkeypatch):
    """A probe that crashes is a harness failure — it must never read as a pass,
    and must not take the sweep down with it."""
    def _boom(_context):
        raise RuntimeError("probe exploded")

    monkeypatch.setitem(runner.BUILTIN_CHECKS, "reports_gitignored", _boom)
    reg = _registry()
    code, report, out = _run_gates(tmp_path, reg)
    record = _condition(report, "GC-OPS-02")
    assert (code, record["ok"], record["execution_status"]) == (1, False, "harness_error")
    assert "probe exploded" in record["error"]
    assert out.exists()


# --------------------------------------------------------------------------- #
# the lint is the gate ON the gates — it must reject a registry that lies       #
# --------------------------------------------------------------------------- #
def test_unfrozen_protocol_digest_blocks_the_gates(tmp_path):
    """A registry whose protocol digest no longer matches the spec must not be
    able to run gates at all — an unfrozen protocol cannot certify anything."""
    reg = _registry()
    reg["protocol"]["sha256"] = "0" * 64
    registry_path = _write(tmp_path, reg)
    with pytest.raises(runner.RegistryError, match="lint failed before gates"):
        runner.command_gates(
            runner.build_parser().parse_args(
                ["--registry", str(registry_path), "gates", "--gate", "OPS-GATE",
                 "--out", str(tmp_path / "r.json")]
            )
        )


def test_lint_rejects_a_gate_condition_missing_from_the_spec(tmp_path):
    """GC ids are pinned to the protocol document, so a condition invented in the
    registry alone (or silently dropped) is a traceability break, not a new gate."""
    reg = _registry()
    reg["gate_conditions"]["OPS-GATE"] = [
        c for c in reg["gate_conditions"]["OPS-GATE"] if c["id"] != "GC-OPS-02"
    ]
    errors = runner.lint_registry(_write(tmp_path, reg), reg)
    assert any("GC mismatch" in e for e in errors), errors


def test_lint_rejects_an_unknown_builtin(tmp_path):
    reg = _registry()
    _set_condition(reg, "OPS-GATE", "GC-OPS-02", kind="builtin", builtin="not_a_real_check")
    errors = runner.lint_registry(_write(tmp_path, reg), reg)
    assert any("unknown builtin" in e for e in errors), errors
