"""Machine-wide design slots and disk housekeeping.

Slots. The web UI, `r2g run` and the MCP server are separate processes, each
with its own thread pool; before this, two of them running at once each took
`workers` designs and oversubscribed the machine (CLAUDE.md "Parallel ORFS":
thrashing is invisible in load and only shows as nothing finishing). A design
now holds one of `workers` lock files under R2G_HOME/slots while it runs. The
locks are fcntl locks, so a crashed process releases its slot with it.

Disk. run_orfs.sh copies every ORFS output (results, logs, reports, objects)
into the project's backend/RUN_*, so the per-variant directories left in the
ORFS checkout are duplicates; _restage_for_signoff.sh restores them from the
project copy if a later signoff needs them. They are removed when a design
ends. Whole job workspaces are only removed on request (`r2g clean`), never
while a job runs, and the packaged dataset is kept.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

from .paths import r2g_home


@contextmanager
def design_slot(n: int, should_stop=lambda: False, on_wait=lambda: None, poll: float = 2.0):
    """Hold one of `n` machine-wide slots for the body. `on_wait` is called once
    if no slot is free; `should_stop()` aborts the wait (raises whatever it raises)."""
    d = r2g_home() / "slots"
    d.mkdir(parents=True, exist_ok=True)
    waited = False
    while True:
        should_stop()
        # n may be a callable: the limit can change while designs wait
        for i in range(max(1, n() if callable(n) else n)):
            f = open(d / ("slot%d.lock" % i), "a+")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                f.close()
                continue
            try:
                should_stop()
                f.seek(0)
                f.truncate()
                f.write("%d %s\n" % (os.getpid(), time.strftime("%F %T")))
                f.flush()
                yield i
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
                f.close()
            return
        if not waited:
            on_wait()
            waited = True
        should_stop()
        time.sleep(poll)


def slots_in_use(n: int) -> int:
    d = r2g_home() / "slots"
    used = 0
    for i in range(max(1, n)):
        p = d / ("slot%d.lock" % i)
        if not p.exists():
            continue
        with open(p, "a+") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
            except OSError:
                used += 1
    return used


def orfs_variant_dirs(project: Path) -> list[Path]:
    """The ORFS-checkout directories this project's runs wrote, from run-meta.json.

    Only a directory named exactly the run's FLOW_VARIANT under
    <flow>/{results,logs,reports,objects}/<platform>/<design>/ is returned -- never
    run_orfs.sh's no-variant fallback, which other designs share."""
    out = set()
    for meta in (project / "backend").glob("RUN_*/run-meta.json"):
        try:
            m = json.loads(meta.read_text())
        except (OSError, ValueError):
            continue
        res, var = m.get("orfs_results"), m.get("flow_variant")
        if not res or not var or Path(res).name != var or var != project.name:
            continue
        if Path(res).parents[2].name != "results":
            continue
        flow = Path(res).parents[3]
        for kind in ("results", "logs", "reports", "objects"):
            p = flow / kind / m["platform"] / m["design_name"] / var
            if p.is_dir():
                out.add(p)
    return sorted(out)


def drop_orfs_copies(project: Path) -> int:
    """Remove the ORFS-checkout duplicates of a finished design. Returns bytes freed."""
    freed = 0
    for p in orfs_variant_dirs(project):
        freed += dir_size(p)
        shutil.rmtree(p, ignore_errors=True)
    return freed


def dir_size(p: Path) -> int:
    n = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                n += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return n


def disk_report(store, limit: int = 500) -> dict:
    """Workspace size per job, finished or not, plus totals."""
    ws = r2g_home() / "workspace"
    rows = []
    for j in store.jobs(limit):
        p = ws / j["id"]
        if p.is_dir():
            rows.append({"job_id": j["id"], "status": j["status"], "created": j.get("created"),
                         "bytes": dir_size(p)})
    ds = r2g_home() / "datasets"
    return {"workspace_bytes": sum(r["bytes"] for r in rows), "jobs": rows,
            "datasets_bytes": dir_size(ds) if ds.is_dir() else 0,
            "free_bytes": shutil.disk_usage(r2g_home()).free}


FINISHED = ("done", "failed", "cancelled")


def clean_candidates(store, older_than_days: float) -> list[dict]:
    """Finished jobs older than the cutoff whose workspace still exists."""
    cutoff = time.time() - older_than_days * 86400
    out = []
    for r in disk_report(store)["jobs"]:
        p = r2g_home() / "workspace" / r["job_id"]
        if r["status"] in FINISHED and p.stat().st_mtime < cutoff:
            out.append(dict(r, path=str(p)))
    return out


def orfs_flow_dir() -> Path | None:
    """The ORFS flow directory the skills use (signoff-loop's pinned env.local.sh)."""
    import re
    from .paths import skills_dir
    pin = skills_dir() / "signoff-loop" / "references" / "env.local.sh"
    root = os.environ.get("ORFS_ROOT")
    if not root and pin.is_file():
        m = re.search(r'^export ORFS_ROOT="?([^"\n]+)"?', pin.read_text(), re.M)
        root = m.group(1) if m else None
    return Path(root) / "flow" if root and (Path(root) / "flow").is_dir() else None


def free_gb() -> float:
    """Free space where designs write: R2G_HOME and the ORFS checkout, whichever is less."""
    paths = [r2g_home()] + [p for p in [orfs_flow_dir()] if p]
    return min(shutil.disk_usage(p).free for p in paths) / 1e9


def wait_for_disk(min_gb, should_stop=lambda: None, on_wait=lambda free: None, poll: float = 60.0):
    """Block while free space is under `min_gb` (0 disables; a callable is re-read
    every poll, so lowering the limit in Settings releases the waiters)."""
    told = False
    while True:
        need = min_gb() if callable(min_gb) else min_gb
        if not need:
            return
        free = free_gb()
        if free >= need:
            return
        if not told:
            on_wait(free)
            told = True
        should_stop()
        time.sleep(poll)


# per-run copies run_orfs.sh makes (every stage's ODB/DEF/GDS, merged libs);
# a passed design's dataset, reports, logs and RTL do not need them
_SLIM = ("results", "objects", "final")


def slim_passed(project: Path) -> int:
    """Delete a PASSED design's backend binaries. Returns bytes freed."""
    freed = 0
    for run in (project / "backend").glob("RUN_*"):
        for k in _SLIM:
            p = run / k
            if p.is_dir():
                freed += dir_size(p)
                shutil.rmtree(p, ignore_errors=True)
    if freed:
        (project / "backend" / "SLIMMED").write_text(
            "results/objects/final of every run were deleted after the design passed;\n"
            "its dataset, reports and logs are kept. Re-submit the design to rebuild them.\n")
    return freed
