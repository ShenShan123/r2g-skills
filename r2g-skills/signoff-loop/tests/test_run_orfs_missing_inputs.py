"""run_orfs.sh must refuse to launch ORFS when config.mk's inputs are not on disk.

failure-patterns #57 RENAME-P0-03 (2026-07-25): the repo was renamed agent-r2g ->
r2g-skills, and all 848 config.mk files kept ABSOLUTE VERILOG_FILES/SDC_FILE paths
under the dead root. run_orfs.sh's only input check was `grep -q VERILOG_FILES` —
presence of the KEY, not of the FILES — so it passed, ORFS launched, and make died
with "No rule to make target '<dead>/rtl/foo.v'". The loop ingested that as 24
`fail`/synth runs carrying orfs-fail-synth events: infrastructure absence entering the
learner as a design symptom, indistinguishable in the DB from a real synth abort.

The guard exits 66 (R2G_INPUTS_MISSING — the same infra code engineer_loop maps to
'project_inputs_missing') BEFORE any ORFS work, so no run row can ever be created.
"""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUN_ORFS = REPO / "r2g-skills" / "signoff-loop" / "scripts" / "flow" / "run_orfs.sh"
INPUTS_MISSING_RC = 66


def _project(tmp_path: Path, verilog: str, *, make_rtl: bool = True) -> Path:
    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "rtl").mkdir()
    if make_rtl:
        (proj / "rtl" / "widget.v").write_text("module widget(); endmodule\n")
    (proj / "constraints" / "constraint.sdc").write_text("create_clock -period 10\n")
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = widget\n"
        "export PLATFORM    = nangate45\n"
        f"export VERILOG_FILES = {verilog}\n"
        f"export SDC_FILE      = {proj / 'constraints' / 'constraint.sdc'}\n"
    )
    return proj


def _run(proj: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # Never touch the shared ORFS workspace from a test.
    env["R2G_SKIP_WORKSPACE_LOCK"] = "1"
    return subprocess.run(
        ["bash", str(RUN_ORFS), str(proj), "nangate45"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120,
    )


def test_dead_verilog_path_refuses_with_the_infra_code(tmp_path):
    """The exact 2026-07-25 shape: a config.mk pointing at the pre-rename root."""
    proj = _project(tmp_path, "/proj/workarea/user5/agent-r2g/design_cases/d/rtl/d.v")
    r = _run(proj)
    assert r.returncode == INPUTS_MISSING_RC, (
        f"expected {INPUTS_MISSING_RC} (infra), got {r.returncode}: {r.stderr[-800:]}")
    assert "do not exist" in r.stderr
    # the message must name the repair, not just complain
    assert "reroot_project_paths.py" in r.stderr
    # and must say plainly that this is NOT a design failure
    assert "NOT a design failure" in r.stderr


def test_message_names_the_offending_file(tmp_path):
    dead = "/nonexistent/root/design_cases/d/rtl/d.v"
    r = _run(_project(tmp_path, dead))
    assert r.returncode == INPUTS_MISSING_RC
    assert dead in r.stderr, "the operator must be told WHICH input is missing"


def test_live_inputs_are_not_blocked(tmp_path):
    """A project whose inputs all exist must get PAST the guard.

    It will fail later for want of a real ORFS platform build — that is fine. The
    assertion is only that it does not fail with the infra code, i.e. the guard is a
    gate on missing inputs and not a blanket refusal.
    """
    proj = _project(tmp_path, str(tmp_path / "proj" / "rtl" / "widget.v"))
    r = _run(proj)
    assert r.returncode != INPUTS_MISSING_RC, (
        f"guard false-positived on a project whose inputs all exist: {r.stderr[-800:]}")


def test_multi_file_verilog_list_checks_every_entry(tmp_path):
    """VERILOG_FILES is a make list — one dead entry among live ones must still fail."""
    proj = _project(tmp_path, "PLACEHOLDER")
    cfg = proj / "constraints" / "config.mk"
    live = proj / "rtl" / "widget.v"
    dead = "/nonexistent/root/rtl/second.v"
    cfg.write_text(cfg.read_text().replace(
        "export VERILOG_FILES = PLACEHOLDER",
        f"export VERILOG_FILES = {live} {dead}"))
    r = _run(proj)
    assert r.returncode == INPUTS_MISSING_RC
    assert dead in r.stderr
