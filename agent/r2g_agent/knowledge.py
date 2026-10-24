"""The user's own knowledge store, seeded from the shipped one.

signoff-loop ships knowledge.sqlite + heuristics.json as git-tracked evidence and
refuses to write them (knowledge_db.SHIPPED_WRITE_OPT_IN). Before this module the
agent pointed every ingest at that shipped file, the refusal was swallowed, and
no agent run was ever recorded: the learning loop was open for every user.

Now each user has a copy under R2G_HOME/knowledge/:

  knowledge.sqlite   seeded from the shipped store on first start; every run of
                     every job is ingested here
  heuristics.json    re-learned from it after each ingest; what repair reads
  journal.sqlite     the action journal (machine-local by design)
  shipped.sha256     digest of the shipped store last merged in

When the shipped store changes (a `git pull` brought new experience), it is
merged in with knowledge_sync's additive, honesty-gated merge: new rows are
added, the user's own rows are never overwritten, and a merge that would fail an
honesty gate is rolled back and reported. Nothing is ever sent anywhere; a user
who wants to contribute exports a bundle (`knowledge_sync.py export`) by hand.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import threading
from pathlib import Path

from .paths import r2g_home, skills_dir

_LOCK = threading.Lock()


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
    """What every skill script needs to read and write the user's copy."""
    p = paths()
    return {"R2G_KNOWLEDGE_DB": str(p["db"]), "R2G_HEURISTICS_PATH": str(p["heuristics"]),
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
    p = paths()
    with _LOCK:
        r = _run("learn_heuristics.py", "--db", str(p["db"]), "--out", str(p["heuristics"]))
    lines = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode == 0, lines[-1] if lines else ""


def ensure() -> dict:
    """Seed or update the user's store. Returns what happened, for the UI/log."""
    p = paths()
    shipped, shipped_heur = shipped_store()
    if not shipped.is_file():
        return {"action": "no_shipped_store", "db": str(p["db"])}
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
    p = paths()
    try:
        c = sqlite3.connect("file:%s?mode=ro" % p["db"], uri=True)
        n = c.execute("select count(*) from runs").fetchone()[0]
        c.close()
    except sqlite3.Error:
        n = None
    return {"db": str(p["db"]), "runs": n, "heuristics": p["heuristics"].is_file()}
