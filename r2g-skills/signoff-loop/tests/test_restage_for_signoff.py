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


# --------------------------------------------------------------------------- #
# RMD-P0-01 (three-platform pilot 2026-07-22): the stamp policy must leave     #
# make NOTHING to rebuild. The 07-21 fix ordered stage results 1→6 but kept    #
# two triggers: a blanket "non-stage results newest" rule (clock_period.txt is #
# a YOSYS input, so stamping it newest made synthesis stale) and logs stamped  #
# older than their stage results (6_report.log is itself a make target).       #
# --------------------------------------------------------------------------- #

def _mk_full_run(proj: Path, run_name: str, mtime: float):
    """Backend run with the artifacts the ORFS mtime cascade walks."""
    rdir = proj / "backend" / run_name / "results"
    ldir = proj / "backend" / run_name / "logs"
    rdir.mkdir(parents=True)
    ldir.mkdir(parents=True)
    results = ["clock_period.txt", "mem.json", "1_1_yosys_canonicalize.rtlil",
               "1_2_yosys.v", "2_floorplan.odb", "3_place.odb", "4_cts.odb",
               "5_route.odb", "6_1_fill.odb", "6_final.def", "6_final.gds",
               "6_final.v", "6_final.sdc"]
    for name in results:
        (rdir / name).write_text(name)
    logs = ["1_1_yosys_canonicalize.log", "6_report.log", "6_drc.log"]
    for name in logs:
        (ldir / name).write_text(name)
    for p in list(rdir.iterdir()) + list(ldir.iterdir()) + [rdir, ldir, rdir.parent]:
        os.utime(p, (mtime, mtime))


def test_stamp_order_frozen_layout(tmp_path):
    """After restage: design inputs < non-stage results (clock_period.txt) <
    stage-1 results < … < stage-6 results, and each numbered log carries the
    SAME epoch as its matching stage results — so `make drc`/`make lvs` sees
    every target at least as new as its prerequisites (no rebuild)."""
    proj, flow_dir = _setup(tmp_path)
    _mk_full_run(proj, "RUN_ONE", time.time())
    r = _restage(proj, flow_dir)
    assert r.returncode == 0, r.stderr

    res = flow_dir / "results" / PLATFORM / DESIGN / VARIANT
    logs = flow_dir / "logs" / PLATFORM / DESIGN / VARIANT
    design = flow_dir / "designs" / PLATFORM / DESIGN / VARIANT

    def m(p: Path) -> float:
        return p.stat().st_mtime

    sdc_like = m(design / "config.mk")
    clock_period = m(res / "clock_period.txt")
    mem_json = m(res / "mem.json")
    yosys_rtlil = m(res / "1_1_yosys_canonicalize.rtlil")
    fill = m(res / "6_1_fill.odb")
    final_def = m(res / "6_final.def")
    report_log = m(logs / "6_report.log")

    # clock_period.txt: NEWER than its own prerequisite (the staged SDC/config)
    # so its make rule does not refire, but OLDER than the synthesis outputs so
    # YOSYS_DEPENDENCIES never marks synthesis stale (the 12/12 pilot rebuild).
    assert sdc_like < clock_period, "clock_period.txt must be newer than design inputs"
    assert clock_period < yosys_rtlil, \
        "clock_period.txt (a YOSYS input) must be OLDER than restored synthesis outputs"
    assert mem_json < yosys_rtlil, "non-stage results must be older than stage 1"

    # Stage monotonicity 1 → 6.
    stages = [m(res / n) for n in ("1_1_yosys_canonicalize.rtlil", "2_floorplan.odb",
                                   "3_place.odb", "4_cts.odb", "5_route.odb",
                                   "6_final.gds")]
    assert stages == sorted(stages), f"stage results must increase 1→6: {stages}"
    assert stages[0] < stages[-1], "stage epochs must strictly increase"

    # 6_report.log is a make TARGET depending on 6_1_fill.odb, and 6_final.def
    # depends on 6_report.log: equal epochs read as up-to-date, older refires.
    assert report_log >= fill, "6_report.log must not be older than 6_1_fill.odb"
    assert final_def >= report_log, "6_final.def must not be older than 6_report.log"

    # No blanket newest rule: nothing in results/ may be newer than stage 6.
    newest_stage6 = max(m(p) for p in res.iterdir() if p.name.startswith("6_"))
    for p in res.iterdir():
        assert m(p) <= newest_stage6, f"{p.name} stamped newer than stage 6 (blanket rule?)"


def test_signoff_record_written(tmp_path):
    """RMD-P0-02: restage writes backend/.r2g_signoff_run naming the picked run
    with the artifact digests — where report_io.run_provenance() reads it."""
    import hashlib
    import json as _json

    proj, flow_dir = _setup(tmp_path)
    now = time.time()
    _mk_run(proj, "RUN_ONE", "GDS_ONE", now)
    r = _restage(proj, flow_dir)
    assert r.returncode == 0, r.stderr

    rec_path = proj / "backend" / ".r2g_signoff_run"
    assert rec_path.is_file(), "restage must write the project-side signoff record"
    rec = _json.loads(rec_path.read_text())
    assert rec["run_tag"] == "RUN_ONE"
    assert rec["platform"] == PLATFORM
    assert rec["flow_variant"] == VARIANT
    assert rec["gds_sha256"] == hashlib.sha256(b"GDS_ONE").hexdigest()
    assert rec["def_sha256"] == hashlib.sha256(b"GDS_ONE_def").hexdigest()

    # A new pick refreshes the record.
    _mk_run(proj, "RUN_TWO", "GDS_TWO", now + 10)
    os.utime(proj / "backend" / "RUN_TWO", (now + 3600, now + 3600))
    r2 = _restage(proj, flow_dir)
    assert r2.returncode == 0, r2.stderr
    rec2 = _json.loads(rec_path.read_text())
    assert rec2["run_tag"] == "RUN_TWO"
    assert rec2["gds_sha256"] == hashlib.sha256(b"GDS_TWO").hexdigest()
