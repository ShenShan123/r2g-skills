"""run_drc.sh must be a frozen-layout, checker-only operation (RMD-P0-01).

Three-platform pilot 2026-07-22: every one of the 12 DRC invocations rebuilt
synthesis→finish before KLayout, because `make drc` walked ORFS's mtime cascade
over a restage-stamped chain. The fix invokes KLayout DIRECTLY on the preserved
backend GDS — physical-stage dependency evaluation cannot run at all.

Harness: run_drc.sh is executed from a HERMETIC copy of scripts/flow/ (the real
skill dir carries references/env.local.sh, whose unconditional exports would
re-point ORFS_ROOT/KLAYOUT_CMD at the real toolchain). The fake ORFS flow dir
ships a klayout.sh wrapper mirroring upstream; KLAYOUT_CMD is a stub that logs
each invocation and writes a lyrdb with STUB_VIOLS `<value>` markers; a
booby-trapped `make` sits first in PATH and records any attempt to run it.
"""
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]

PLATFORM = "nangate45"
DESIGN = "demo"


def _make_exec(path: Path, text: str):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path):
    # Hermetic skill copy: flow scripts only + an empty knowledge/ (the journal
    # helper is invoked best-effort and must merely resolve the directory).
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    shutil.copytree(SKILL / "scripts" / "flow", skill / "scripts" / "flow")
    (skill / "knowledge").mkdir()
    ref = skill / "references"
    ref.mkdir()

    # Fake ORFS checkout.
    orfs = tmp_path / "orfs"
    flow = orfs / "flow"
    pdir = flow / "platforms" / PLATFORM
    (pdir / "drc").mkdir(parents=True)
    (flow / "scripts").mkdir(parents=True)
    (flow / "Makefile").write_text("# fake ORFS Makefile — must never be executed\n")
    (pdir / "config.mk").write_text(
        "export KLAYOUT_DRC_FILE = $(PLATFORM_DIR)/drc/FreePDK45.lydrc\n")
    deck = pdir / "drc" / "FreePDK45.lydrc"
    deck.write_text("FEOL    = true\nBEOL    = true\nANTENNA = true\nOFFGRID = true\n")
    # Upstream-shaped wrapper: logs version, then execs the tool.
    _make_exec(flow / "scripts" / "klayout.sh",
               '#!/usr/bin/env bash\nset -u -eo pipefail\n'
               '"$KLAYOUT_CMD" -v\n"$KLAYOUT_CMD" "$@"\n')

    # Stub klayout: counts invocations, honors -v, writes the report_file lyrdb.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_exec(bindir / "klayout", f"""#!/usr/bin/env bash
if [[ "${{1:-}}" == "-v" ]]; then echo "KLayout 0.0.stub"; exit 0; fi
echo x >> "{tmp_path}/klayout_invocations"
report=""
for a in "$@"; do
  case "$a" in report_file=*) report="${{a#report_file=}}" ;; esac
done
n="${{STUB_VIOLS:-0}}"
{{
  echo '<report-database>'
  for ((i=0; i<n; i++)); do echo "  <value>polygon: (0,0;1,1)</value>"; done
  echo '</report-database>'
}} > "$report"
exit 0
""")
    # Booby-trapped make: any invocation is the RMD-P0-01 regression.
    _make_exec(bindir / "make",
               f'#!/usr/bin/env bash\necho x >> "{tmp_path}/make_invocations"\nexit 2\n')

    # Project with a preserved backend run.
    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(f"export DESIGN_NAME = {DESIGN}\n")
    run = proj / "backend" / "RUN_A"
    (run / "results").mkdir(parents=True)
    for name in ("6_final.gds", "6_final.def", "6_final.v", "6_final.sdc", "5_route.odb"):
        (run / "results" / name).write_text(f"content-of-{name}")
    return skill, orfs, proj, bindir, deck


def _run_drc(tmp_path, skill, orfs, proj, bindir, extra_env=None):
    env = dict(
        os.environ,
        ORFS_ROOT=str(orfs),
        KLAYOUT_CMD=str(bindir / "klayout"),
        PATH=f"{bindir}:{os.environ['PATH']}",
        R2G_JOURNAL_DB=str(tmp_path / "journal.sqlite"),
    )
    env.pop("R2G_ENV_FILE", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(skill / "scripts" / "flow" / "run_drc.sh"), str(proj), PLATFORM],
        env=env, capture_output=True, text=True, timeout=120)


def test_checker_only_clean(tmp_path):
    skill, orfs, proj, bindir, deck = _setup(tmp_path)
    r = _run_drc(tmp_path, skill, orfs, proj, bindir)
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"

    # Zero physical-stage commands: make must never have run.
    assert not (tmp_path / "make_invocations").exists(), \
        "run_drc.sh invoked make — the checker-only invariant is broken (RMD-P0-01)"
    assert (tmp_path / "klayout_invocations").read_text().count("x\n") == 1

    result = json.loads((proj / "drc" / "drc_result.json").read_text())
    assert result["status"] == "clean"
    assert result["checker"] == "klayout_direct"
    assert result["run_tag"] == "RUN_A"
    gds_bytes = (proj / "backend" / "RUN_A" / "results" / "6_final.gds").read_bytes()
    assert result["gds_sha256"] == hashlib.sha256(gds_bytes).hexdigest()
    assert result["deck_sha256"] == hashlib.sha256(deck.read_bytes()).hexdigest()
    assert result["klayout_version"] == "KLayout 0.0.stub"

    # Output born run-local, mirrored to the project dir (RMD-P0-02).
    run_drc = proj / "backend" / "RUN_A" / "drc"
    for name in ("6_drc.lyrdb", "6_drc_count.rpt", "drc_result.json"):
        assert (run_drc / name).is_file(), f"missing run-local {name}"
        assert (proj / "drc" / name).is_file(), f"missing project mirror {name}"

    # The signoff record names the same run.
    rec = json.loads((proj / "backend" / ".r2g_signoff_run").read_text())
    assert rec["run_tag"] == "RUN_A"

    # Frozen layout: preserved artifacts byte-identical after the checker.
    assert (proj / "backend" / "RUN_A" / "results" / "6_final.gds").read_bytes() == gds_bytes


def test_two_consecutive_runs_both_fresh(tmp_path):
    """Acceptance test 4: consecutive invocations each run a fresh checker and
    neither runs a physical stage."""
    skill, orfs, proj, bindir, _deck = _setup(tmp_path)
    assert _run_drc(tmp_path, skill, orfs, proj, bindir).returncode == 0
    assert _run_drc(tmp_path, skill, orfs, proj, bindir).returncode == 0
    assert (tmp_path / "klayout_invocations").read_text().count("x\n") == 2
    assert not (tmp_path / "make_invocations").exists()


def test_violations_counted(tmp_path):
    skill, orfs, proj, bindir, _deck = _setup(tmp_path)
    r = _run_drc(tmp_path, skill, orfs, proj, bindir, extra_env={"STUB_VIOLS": "7"})
    # Violations are a nonzero-exit condition for callers? No: klayout exits 0;
    # run_drc reports the count and exits with the checker status (0).
    assert r.returncode == 0, r.stderr
    result = json.loads((proj / "drc" / "drc_result.json").read_text())
    assert result["status"] == "violations"
    assert result["violations"] == 7
    assert result["run_tag"] == "RUN_A"


def test_no_deck_is_explicit_skip(tmp_path):
    """A platform without any deck records an honest skip, never a phantom fail
    (failure-patterns #32) — and still never touches make."""
    skill, orfs, proj, bindir, deck = _setup(tmp_path)
    deck.unlink()
    (orfs / "flow" / "platforms" / PLATFORM / "config.mk").write_text("# no deck\n")
    r = _run_drc(tmp_path, skill, orfs, proj, bindir)
    assert r.returncode == 0, r.stderr
    result = json.loads((proj / "drc" / "drc_result.json").read_text())
    assert result["status"] == "skipped"
    assert result["reason"] == "no_drc_deck_for_platform"
    assert not (tmp_path / "make_invocations").exists()
    assert not (tmp_path / "klayout_invocations").exists()


def test_missing_gds_fails_closed(tmp_path):
    """Missing physical artifacts stop the wrapper; they are never regenerated."""
    skill, orfs, proj, bindir, _deck = _setup(tmp_path)
    shutil.rmtree(proj / "backend")
    r = _run_drc(tmp_path, skill, orfs, proj, bindir)
    assert r.returncode != 0
    assert "No 6_final.gds" in (r.stdout + r.stderr)
    assert not (tmp_path / "make_invocations").exists()
