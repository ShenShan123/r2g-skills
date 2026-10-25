"""Per-user memory for the r2g agent.

The agent defaults to TEHM at R2G_HOME/memory; legacy and none remain explicit
alternatives. Legacy stores are seeded additively from the shipped baseline.
TEHM captures and rebuilds only its own execution evidence. No legacy rows are
imported as TEHM authority. Every skill subprocess receives the same selection.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

from .paths import r2g_home, skills_dir, repo_root

_LOCK = threading.RLock()
_BACKEND_NAME: str | None = None


def backend_name() -> str:
    """The agent uses TEHM by default; one authority is locked for its process."""
    global _BACKEND_NAME
    name = os.environ.get("R2G_MEMORY_BACKEND", "tehm").strip().lower()
    if name not in {"none", "legacy", "tehm"}:
        raise ValueError(f"invalid R2G_MEMORY_BACKEND={name!r}; refusing silent fallback")
    with _LOCK:
        if _BACKEND_NAME is not None and name != _BACKEND_NAME:
            raise RuntimeError(f"memory backend already opened as {_BACKEND_NAME!r}; refusing {name!r}")
        _BACKEND_NAME = name
    return name


def tehm_paths() -> dict:
    root = r2g_home() / "memory"
    return {"db": Path(os.environ.get("TEHM_DB", root / "tehm.sqlite")).expanduser().resolve(),
            "artifacts": Path(os.environ.get("TEHM_ARTIFACTS_ROOT", root / "artifacts")).expanduser().resolve()}



def shipped_dir() -> Path:
    return skills_dir() / "signoff-loop" / "knowledge"


def shipped_store() -> tuple[Path, Path]:
    """(knowledge.sqlite, heuristics.json) as shipped in the checkout."""
    return shipped_dir() / "knowledge.sqlite", shipped_dir() / "heuristics.json"


def store_dir() -> Path:
    d = r2g_home() / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def paths() -> dict:
    d = store_dir()
    return {"db": d / "knowledge.sqlite", "heuristics": d / "heuristics.json",
            "journal": d / "journal.sqlite", "marker": d / "shipped.sha256"}


def env() -> dict:
    """Pass the selected backend and user-local stores to every skill process."""
    name = backend_name()
    if name == "none":
        return {"R2G_MEMORY_BACKEND": name}
    if name == "tehm":
        p = tehm_paths()
        return {"R2G_MEMORY_BACKEND": name, "TEHM_DB": str(p["db"]),
                "TEHM_ARTIFACTS_ROOT": str(p["artifacts"])}
    p = paths()
    return {"R2G_MEMORY_BACKEND": name,
            "R2G_KNOWLEDGE_DB": str(p["db"]), "R2G_HEURISTICS_PATH": str(p["heuristics"]),
            "R2G_JOURNAL_DB": str(p["journal"])}


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update(env())
    return subprocess.run(["python3", str(shipped_dir() / script), *args], cwd=shipped_dir(),
                          env=e, capture_output=True, text=True, timeout=1800)


def learn() -> tuple[bool, str]:
    """Rebuild the user's heuristics.json from the user's store."""
    name = backend_name()
    if name != "legacy":
        # runtime_router.ingest_project already rebuilds TEHM from its own evidence.
        return True, "disabled" if name == "none" else "TEHM rebuilt during ingest"
    p = paths()
    with _LOCK:
        r = _run("learn_heuristics.py", "--db", str(p["db"]), "--out", str(p["heuristics"]))
    lines = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode == 0, lines[-1] if lines else ""


def ensure() -> dict:
    """Seed or update the user's store. Returns what happened, for the UI/log."""
    name = backend_name()
    if name == "none":
        return {"action": "disabled", "backend": name}
    if name == "tehm":
        p = tehm_paths()
        e = {**os.environ, **env()}
        e["PYTHONPATH"] = str(repo_root() / "memory") + os.pathsep + e.get("PYTHONPATH", "")
        read_only = os.environ.get("R2G_MEMORY_READ_ONLY_EVAL", "0") == "1"
        command = [sys.executable, "-m", "tehm.cli", "init-db"]
        if read_only:
            command = [sys.executable, "-c",
                       "from pathlib import Path; import sys; from tehm import db; "
                       "c=db.connect_read_only(Path(sys.argv[1])); c.close()", str(p["db"])]
        with _LOCK:
            r = subprocess.run(command, cwd=repo_root(), env=e,
                               capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise RuntimeError("TEHM initialization failed: " + (r.stdout + r.stderr)[-800:])
        return {"action": "ready", "backend": name, "db": str(p["db"]), "read_only": read_only}
    p = paths()
    shipped, shipped_heur = shipped_store()
    if not shipped.is_file():
        with _LOCK:
            if p["db"].is_file():
                return {"action": "up_to_date", "backend": "legacy", "db": str(p["db"])}
            command = [sys.executable, "-c",
                       "import sys, knowledge_db; "
                       "c=knowledge_db.connect(sys.argv[1]); knowledge_db.ensure_schema(c); c.close()",
                       str(p["db"])]
            r = subprocess.run(command, cwd=shipped_dir(), env={**os.environ, **env()},
                               capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise RuntimeError("Legacy initialization failed: " + (r.stdout + r.stderr)[-800:])
        return {"action": "initialized", "backend": "legacy", "db": str(p["db"])}
    digest = _sha(shipped)
    with _LOCK:
        if not p["db"].is_file():
            src = sqlite3.connect("file:%s?mode=ro" % shipped, uri=True)
            tmp = p["db"].with_suffix(".sqlite.tmp")
            tmp.unlink(missing_ok=True)
            dst = sqlite3.connect(tmp)
            src.backup(dst)          # a consistent copy even if the file is mid-write
            dst.close()
            src.close()
            os.replace(tmp, p["db"])
            if shipped_heur.is_file():
                shutil.copy2(shipped_heur, p["heuristics"])
            p["marker"].write_text(digest + "\n")
            return {"action": "seeded", "db": str(p["db"])}
        if p["marker"].is_file() and p["marker"].read_text().strip() == digest:
            return {"action": "up_to_date", "db": str(p["db"])}
        r = _run("knowledge_sync.py", "merge", "--from-db", str(shipped), "--db", str(p["db"]))
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            # marker untouched: retried next start, and the user's store is as it was
            return {"action": "merge_refused", "db": str(p["db"]), "detail": out[-800:]}
        p["marker"].write_text(digest + "\n")
    ok, msg = learn()
    return {"action": "merged", "db": str(p["db"]), "learn": msg if ok else "learn failed: " + msg}


def summary() -> dict:
    name = backend_name()
    if name == "none":
        return {"backend": name, "enabled": False, "db": None, "runs": 0, "heuristics": False}
    if name == "tehm":
        p = tehm_paths()
        return {"backend": name, "db": str(p["db"]), "initialized": p["db"].is_file(),
                "runs": None, "heuristics": False}
    p = paths()
    try:
        c = sqlite3.connect("file:%s?mode=ro" % p["db"], uri=True)
        n = c.execute("select count(*) from runs").fetchone()[0]
        c.close()
    except sqlite3.Error:
        n = None
    return {"backend": name, "db": str(p["db"]), "runs": n, "heuristics": p["heuristics"].is_file()}
