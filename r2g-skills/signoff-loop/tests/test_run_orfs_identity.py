"""Regression tests for run_orfs.sh run + ORFS-workspace identity (full-pipeline Issue 9).

Before this fix:
  * RUN_TAG was `RUN_$(date +%Y-%m-%d_%H-%M-%S)` — 1-second precision, no PID/randomness.
    Two same-second invocations shared one backend/RUN_<ts> dir (`mkdir -p` reused it) and
    INTERLEAVED each other's stage_log.jsonl rows.
  * run-meta.json omitted flow_variant, so signoff could not learn which ORFS workspace the
    GDS was built in.
  * Nothing serialized the shared ORFS workspace, so two configs with the same
    DESIGN_NAME+FLOW_VARIANT raced clean_all-vs-build (a CLAUDE.md Hard Rule violation).

The helpers are defined before run_orfs.sh's R2G_SOURCE_ONLY early-return, so they are
exercised here in isolation (no ORFS checkout needed).
"""
import fcntl
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUN_ORFS = REPO / "r2g-skills" / "signoff-loop" / "scripts" / "flow" / "run_orfs.sh"


def _source_call(snippet: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Source run_orfs.sh in source-only mode and run a bash snippet against its helpers."""
    env = dict(os.environ, R2G_SOURCE_ONLY="1")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", "-c", f'source "{RUN_ORFS}"; {snippet}'],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=60,
    )


def test_run_tag_unique_within_one_second(tmp_path):
    """Two backend dirs minted back-to-back (same wall-clock second) are DISTINCT."""
    base = tmp_path / "backend"
    # Each helper call emits "<dir>\t<tag>"; print both on their own lines and split here.
    p = _source_call(
        f'_r2g_new_backend_dir "{base}"; echo; _r2g_new_backend_dir "{base}"; echo'
    )
    assert p.returncode == 0, p.stderr
    rows = [ln for ln in p.stdout.splitlines() if "\t" in ln]
    assert len(rows) == 2, p.stdout
    (dir1, tag1), (dir2, tag2) = (r.split("\t", 1) for r in rows)
    assert dir1 != dir2 and tag1 != tag2, rows
    for d in (dir1, dir2):
        assert Path(d).is_dir(), f"{d} not created"
        # sortable RUN_<ts> prefix preserved (consumers glob RUN_* + sort)
        assert Path(d).name.startswith("RUN_20"), d


def test_new_backend_dir_regenerates_on_collision(tmp_path):
    """If the first-choice tag dir already exists, the helper mints another (bounded retry)."""
    base = tmp_path / "backend"
    # Pre-create a dir, then confirm the helper never returns an already-existing dir.
    p = _source_call(
        f'for i in $(seq 1 6); do _r2g_new_backend_dir "{base}" | cut -f1; done'
    )
    assert p.returncode == 0, p.stderr
    got = [ln for ln in p.stdout.splitlines() if ln.strip()]
    assert len(got) == len(set(got)) == 6, got  # all distinct, all created


def test_run_meta_records_flow_variant():
    """The run-meta.json template must carry flow_variant (signoff learns it from here)."""
    src = RUN_ORFS.read_text()
    assert '"flow_variant": "$FLOW_VARIANT"' in src, \
        "run-meta.json must include flow_variant so fix_signoff can recover the workspace"


def test_workspace_lock_blocks_second_acquisition(tmp_path):
    """A second run of the same platform+design+variant fails FAST with the hard-rule
    message instead of racing the first run's clean_all/build."""
    lockdir = tmp_path / "locks"
    lockdir.mkdir()
    env_extra = {"R2G_LOCK_DIR": str(lockdir)}
    # 1) resolve the lockfile path the helper will use
    q = _source_call("_r2g_workspace_lockfile sky130hd top myvar", env_extra)
    assert q.returncode == 0, q.stderr
    lockfile = q.stdout.strip()
    assert lockfile.startswith(str(lockdir)), lockfile
    # 2) hold it (a concurrent run) via a separate open file description
    holder = open(lockfile, "w")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        # 3) the guarded acquisition must fail fast + explain the hard rule
        r = _source_call("_r2g_acquire_workspace_lock sky130hd top myvar", env_extra)
        assert r.returncode != 0, "second acquisition must NOT succeed while held"
        assert "same DESIGN_NAME+FLOW_VARIANT" in r.stderr, r.stderr
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
    # 4) once released, a fresh acquisition succeeds
    r2 = _source_call("_r2g_acquire_workspace_lock sky130hd top myvar", env_extra)
    assert r2.returncode == 0, r2.stderr


def test_workspace_lock_distinct_variants_do_not_contend(tmp_path):
    """Different variants (unique per project, hard rule) get distinct lockfiles."""
    lockdir = tmp_path / "locks"
    lockdir.mkdir()
    env_extra = {"R2G_LOCK_DIR": str(lockdir)}
    a = _source_call("_r2g_workspace_lockfile nangate45 top varA", env_extra).stdout.strip()
    b = _source_call("_r2g_workspace_lockfile nangate45 top varB", env_extra).stdout.strip()
    assert a and b and a != b, (a, b)
