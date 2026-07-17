"""_restage_for_signoff.sh must re-stage a NEWER backend run (full-pipeline Issue 7).

The .r2g_restaged marker used to be an empty boolean: once present, _restage_dir skipped
ALL copying, so a newer backend run (correctly PICKED by _restage_pick_run_dir as the
newest RUN_* with a 6_final.gds) was never staged into the ORFS workspace — signoff kept
verifying an older layout. The marker is now IDENTITY-BEARING: it records the basename of
the staged run, and a differing identity forces a clobber re-stage. A same-identity marker
stays the fast-path no-op.
"""
import os
import subprocess
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
RESTAGE = SKILL / "scripts" / "flow" / "_restage_for_signoff.sh"

PLATFORM = "nangate45"
DESIGN = "demo"
VARIANT = "proj"


def _mk_run(proj: Path, run_name: str, gds_content: str, mtime: float):
    """Create backend/<run_name>/results/6_final.{gds,def} with given content + mtime."""
    rdir = proj / "backend" / run_name / "results"
    rdir.mkdir(parents=True)
    (rdir / "6_final.gds").write_text(gds_content)
    (rdir / "6_final.def").write_text(gds_content + "_def")
    for p in (rdir / "6_final.gds", rdir / "6_final.def", rdir, rdir.parent):
        os.utime(p, (mtime, mtime))


def _restage(proj: Path, flow_dir: Path) -> subprocess.CompletedProcess:
    env = dict(
        os.environ,
        PROJECT_DIR=str(proj), PLATFORM=PLATFORM, DESIGN_NAME=DESIGN,
        FLOW_VARIANT=VARIANT, FLOW_DIR=str(flow_dir),
        CONFIG_MK=str(proj / "constraints" / "config.mk"),
    )
    return subprocess.run(
        ["bash", "-c", f'source "{RESTAGE}"'],
        env=env, capture_output=True, text=True, timeout=60,
    )


def _staged(flow_dir: Path):
    d = flow_dir / "results" / PLATFORM / DESIGN / VARIANT
    return d, (d / "6_final.gds"), (d / ".r2g_restaged")


def _setup(tmp_path):
    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(f"export DESIGN_NAME = {DESIGN}\n")
    flow_dir = tmp_path / "flow"
    (flow_dir / "designs" / PLATFORM / DESIGN / VARIANT).mkdir(parents=True)
    return proj, flow_dir


def test_newer_run_is_restaged(tmp_path):
    proj, flow_dir = _setup(tmp_path)
    now = time.time()
    # RUN_ONE is the newest at first -> it gets staged.
    _mk_run(proj, "RUN_ONE", "GDS_ONE", now)
    _mk_run(proj, "RUN_TWO", "GDS_TWO", now - 3600)   # older for stage 1
    r1 = _restage(proj, flow_dir)
    assert r1.returncode == 0, r1.stderr
    _d, gds, marker = _staged(flow_dir)
    assert gds.read_text() == "GDS_ONE", gds.read_text()
    assert marker.read_text().strip() == "RUN_ONE", marker.read_text()

    # Now RUN_TWO becomes the newest (a fresh backend run) -> must RE-stage.
    os.utime(proj / "backend" / "RUN_TWO", (now + 3600, now + 3600))
    os.utime(proj / "backend" / "RUN_TWO" / "results", (now + 3600, now + 3600))
    os.utime(proj / "backend" / "RUN_TWO" / "results" / "6_final.gds", (now + 3600, now + 3600))
    r2 = _restage(proj, flow_dir)
    assert r2.returncode == 0, r2.stderr
    assert gds.read_text() == "GDS_TWO", "newer RUN_TWO GDS must replace the stale RUN_ONE GDS"
    assert marker.read_text().strip() == "RUN_TWO", marker.read_text()
    assert (flow_dir / "results" / PLATFORM / DESIGN / VARIANT / "6_final.def").read_text() == "GDS_TWO_def"


def test_same_run_restage_is_noop(tmp_path):
    proj, flow_dir = _setup(tmp_path)
    now = time.time()
    _mk_run(proj, "RUN_ONE", "GDS_ONE", now)
    _restage(proj, flow_dir)
    _d, gds, marker = _staged(flow_dir)
    assert marker.read_text().strip() == "RUN_ONE"
    # Tamper the staged GDS with a sentinel. A genuine no-op (same pick identity) must NOT
    # re-copy the source over it. (mtime can't be used here — the script's final
    # find -exec touch bumps every staged file's mtime unconditionally.)
    gds.write_text("SENTINEL_NOT_RECOPIED")
    r = _restage(proj, flow_dir)
    assert r.returncode == 0, r.stderr
    assert gds.read_text() == "SENTINEL_NOT_RECOPIED", \
        "same-identity restage re-copied the GDS (should be a fast-path no-op)"
    assert marker.read_text().strip() == "RUN_ONE"


def test_legacy_empty_marker_triggers_restage(tmp_path):
    """A pre-existing EMPTY (legacy) marker has no identity -> treated as unknown, re-staged
    once, then stamped with the pick identity."""
    proj, flow_dir = _setup(tmp_path)
    now = time.time()
    _mk_run(proj, "RUN_ONE", "GDS_ONE", now)
    d, gds, marker = _staged(flow_dir)
    d.mkdir(parents=True, exist_ok=True)
    # Simulate the old boolean marker + a stale GDS from some prior run.
    gds.write_text("STALE_LEGACY")
    marker.write_text("")   # legacy empty marker
    r = _restage(proj, flow_dir)
    assert r.returncode == 0, r.stderr
    assert gds.read_text() == "GDS_ONE", "legacy empty marker must force a re-stage"
    assert marker.read_text().strip() == "RUN_ONE", marker.read_text()
