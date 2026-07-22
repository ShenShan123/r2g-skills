#!/usr/bin/env python3
"""Atomic reports/*.json writer shared by the extract scripts.

Every extractor used to `write_text` its report in place — truncate-then-write —
so a `kill -9 -<pgid>` (the documented orphan-cleanup for stuck drivers), an OOM
kill, or disk-full mid-write left a TRUNCATED reports/*.json. ingest_run then
read it as a blank/failed report and the run silently vanished from the
knowledge store, breaking the "ingest after EVERY flow" honesty invariant
(2026-07-04 robustness audit, M1). tmp + os.replace() is atomic on POSIX: a
reader sees either the old complete report or the new complete report, never a
torn one.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def write_json_atomic(path: Path | str, obj, *, indent: int = 2,
                      ensure_ascii: bool = False, sort_keys: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii,
                                  sort_keys=sort_keys) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)     # no stray tmp on a failed dump/replace


# --------------------------------------------------------------------------- #
# Report provenance envelope (P0-R7, failure-patterns.md #52).                 #
# --------------------------------------------------------------------------- #
# A signoff report lives at <project>/reports/<tool>.json — PROJECT level, not
# run level — while a project accumulates many backend RUN_* dirs. Nothing in
# drc.json or lvs.json said WHICH run's layout it judged, so the def-graph
# signoff gate could certify run Z's DEF with run A's clean reports. The audit
# reproduced it on two real wbuart32 runs with different DEF digests (R1
# d6426fae…, R2 cc2da796…): the gate returned pass_with_caveats because it only
# checked that the R2 DEF lived under the R2 dir — it never asked where the
# reports came from.
#
# This stamps the missing half. Attribution order, most authoritative first:
#   1. an explicit run_dir the caller already resolved;
#   2. <project>/backend/.r2g_signoff_run — the project-side JSON record
#      _backend_run.sh's shared resolver writes at every restage/checker run,
#      naming the RUN whose artifacts were staged (plus their sha256 digests).
#      This is the run the tool actually judged. (RMD-P0-02, 2026-07-22: the
#      old `backend/RUN_*/.r2g_restaged` glob below pointed at a marker that
#      was only ever written into the ORFS WORKSPACE, never under the project
#      backend — so this branch was dead and every report silently degraded to
#      the latest_run guess. All 12 three-platform-pilot DRC reports carried
#      source=latest_run because of it.)
#   3. <ORFS results>/.r2g_restaged legacy glob (kept for old trees).
#   4. the newest backend RUN_* — a guess, recorded as such via `source`.
#
# Absent provenance is a legacy report, not a lie: the gate treats it as an
# explicit caveat rather than a blocker, so existing projects keep passing and
# self-heal on their next signoff run. A report that DISAGREES with the selected
# run is the hard block.

def _signoff_record(project_root: Path) -> dict | None:
    """The project-side signoff run record written by _backend_run.sh."""
    rec_path = project_root / "backend" / ".r2g_signoff_run"
    if not rec_path.is_file():
        return None
    try:
        text = rec_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        doc = json.loads(text)
        if isinstance(doc, dict) and doc.get("run_tag"):
            return doc
    except ValueError:
        # Tolerate a plain-text tag (hand-written / older writer).
        tag = text.strip().splitlines()[0].strip() if text.strip() else ""
        if tag:
            return {"run_tag": tag}
    return None


def _restage_run_tag(project_root: Path) -> str:
    """The RUN basename recorded by the last restage, if we can still see it."""
    for marker in sorted(project_root.glob("backend/RUN_*/.r2g_restaged")):
        try:
            tag = marker.read_text(encoding="utf-8").strip().splitlines()[0].strip()
            if tag:
                return tag
        except (OSError, IndexError):
            continue
    return ""


def run_provenance(project_root: Path | str, run_dir: Path | str | None = None) -> dict:
    """The `provenance` envelope every signoff report carries.

    `source` records HOW the run was attributed so a consumer can weigh it:
    'explicit', 'signoff_record' and 'restage_marker' are authoritative,
    'latest_run' is a guess. When the signoff record carries artifact digests
    they ride along (gds_sha256/def_sha256) so the def-graph gate can bind the
    report to exact layout bytes, not just a directory name.
    """
    project_root = Path(project_root)
    if run_dir:
        rd = Path(run_dir)
        return {"run_tag": rd.name, "run_dir": str(rd.resolve()), "source": "explicit"}

    rec = _signoff_record(project_root)
    if rec:
        tag = str(rec["run_tag"])
        rd = project_root / "backend" / tag
        out = {"run_tag": tag,
               "run_dir": str(rd.resolve()) if rd.is_dir() else None,
               "source": "signoff_record"}
        for key in ("gds_sha256", "def_sha256"):
            if rec.get(key):
                out[key] = rec[key]
        return out

    tag = _restage_run_tag(project_root)
    if tag:
        rd = project_root / "backend" / tag
        return {"run_tag": tag,
                "run_dir": str(rd.resolve()) if rd.is_dir() else None,
                "source": "restage_marker"}

    runs = sorted(project_root.glob("backend/RUN_*"))
    if runs:
        return {"run_tag": runs[-1].name, "run_dir": str(runs[-1].resolve()),
                "source": "latest_run"}
    return {"run_tag": None, "run_dir": None, "source": "none"}


def stamp_run_provenance(result: dict, project_root: Path | str,
                         run_dir: Path | str | None = None) -> dict:
    """Attach the provenance envelope to a report dict in place; return it.

    Never raises — an unattributable report must still be written (it degrades to
    the gate's `unknown` caveat), because losing the whole verdict would be worse
    than losing its attribution.
    """
    try:
        result["provenance"] = run_provenance(project_root, run_dir)
    except Exception as e:  # noqa: BLE001
        result["provenance"] = {"run_tag": None, "run_dir": None,
                                "source": "error", "detail": str(e)[:200]}
    return result
