#!/usr/bin/env python3
"""Situation signature (sit-v2): the failure context a fix was chosen in.

The symptom signature (symptom.py) is deliberately coarse — {check, class, true
predicates} — so repair experience transfers across designs. It is too coarse to
separate situations that need DIFFERENT fixes: every backend route abort shares one
symptom whether it is a GRT-0116 congestion abort on an auto-sized die at 8%
utilisation or a detailed-route timeout on a fixed die, and the learner's counts
then average over fixes that only work in one of them (R2G memory diagnosis
2026-10-01, root cause 3).

The situation is stored ALONGSIDE symptom_id (fix_events.situation_id /
situation_json / situation_source + the `situations` catalog), never instead of
it: symptom_id and every consumer keyed on it are untouched.

Fields (all optional except v/check; None = not applicable or unknown):
  check            drc | lvs | timing | orfs_stage   (route fixes -> orfs_stage)
  violation_class  dominant DRC rule | LVS mismatch class | timing tier | aborted stage
  error_code       tool error code of a backend abort (GRT-0116, FLW-0024, PPL-0024, ...)
  timeout          True when the aborted stage was killed by ORFS_TIMEOUT (rc 124/137)
  platform         ORFS platform
  die_mode         fixed (DIE_AREA) | auto (CORE_UTILIZATION)
  util_band        CORE_UTILIZATION band on an auto die: le12 | 13-30 | 31-60 | gt60
  count_band       DRC/LVS before-count band: 1-5 | 6-20 | 21-100 | gt100
  perimeter_band   PPL-0024 pin pressure: required/available die perimeter, from the tool's
                   own message — le1.25 | 1.25-1.75 | 1.75-2.5 | gt2.5 (sit-v2, Phase D
                   amendment D-A2: separates a mildly from a severely pin-bound design that
                   share every other field)

Writers: fix_signoff.sh snapshots it BEFORE the fix edit (`snapshot` CLI ->
R2G_LOG_SITUATION -> fix_log row `situation`, source 'snapshot'); engineer_loop's
backend recoveries capture it before their config edit; ingest derives it for a row
that carries none (source 'ingest' — config facts then come from the project's
CURRENT config.mk, i.e. possibly post-fix, so it is a weaker key).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

VERSION = "sit-v2"
READABLE_VERSIONS = ("sit-v1", "sit-v2")   # sit-v1 snapshots stay readable
ORFS_ERRCODE_RE = re.compile(r"\[ERROR\s+([A-Z]{2,5}-\d{3,4})\]")
_PERIMETER_RE = re.compile(r"die perimeter from ([\d.]+)um to ([\d.]+)um")
TIMEOUT_RCS = (124, 137)        # run_orfs.sh: `timeout` TERM / KILL exit codes
_EXPORT_RE = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)\s*[?:]?=\s*(.*?)\s*$")


def util_band(util: Any) -> str | None:
    try:
        u = float(util)
    except (TypeError, ValueError):
        return None
    if u <= 12:
        return "le12"
    if u <= 30:
        return "13-30"
    if u <= 60:
        return "31-60"
    return "gt60"


def count_band(n: Any) -> str | None:
    try:
        c = float(n)
    except (TypeError, ValueError):
        return None
    if c <= 0:
        return None
    if c <= 5:
        return "1-5"
    if c <= 20:
        return "6-20"
    if c <= 100:
        return "21-100"
    return "gt100"


def perimeter_band(ratio: Any) -> str | None:
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return None
    if r <= 1.25:
        return "le1.25"
    if r <= 1.75:
        return "1.25-1.75"
    if r <= 2.5:
        return "1.75-2.5"
    return "gt2.5"


def canonical(check: str, *, violation_class: str | None = None,
              error_code: str | None = None, timeout: bool = False,
              platform: str | None = None, die_mode: str | None = None,
              util: Any = None, before_count: Any = None,
              perimeter_ratio: Any = None) -> dict:
    """The canonical sit-v1 dict. Route fixes are keyed like symptom.py keys a
    backend abort: check orfs_stage, class = the stage."""
    if check == "route":
        check, violation_class = "orfs_stage", (violation_class or "route")
    return {
        "v": VERSION,
        "check": check,
        "violation_class": violation_class or None,
        "error_code": error_code if check == "orfs_stage" else None,
        "timeout": bool(timeout) if check == "orfs_stage" else False,
        "platform": platform or None,
        "die_mode": die_mode,
        "util_band": util_band(util) if die_mode == "auto" else None,
        "count_band": count_band(before_count) if check in ("drc", "lvs") else None,
        "perimeter_band": (perimeter_band(perimeter_ratio)
                           if check == "orfs_stage" and error_code == "PPL-0024" else None),
    }


def situation_id(sit: dict) -> str:
    payload = json.dumps(sit, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def config_facts(project: Path | str) -> dict:
    """{platform, die_mode, util} from constraints/config.mk (last assignment wins,
    so the signoff-fix auto block overrides the base value)."""
    cfg: dict[str, str] = {}
    p = Path(project) / "constraints" / "config.mk"
    try:
        for ln in p.read_text(errors="ignore").splitlines():
            m = _EXPORT_RE.match(ln)
            if m:
                cfg[m.group(1)] = m.group(2)
    except OSError:
        pass
    if cfg.get("DIE_AREA"):
        die_mode = "fixed"
    elif cfg.get("CORE_UTILIZATION"):
        die_mode = "auto"
    else:
        die_mode = None
    return {"platform": cfg.get("PLATFORM") or None, "die_mode": die_mode,
            "util": cfg.get("CORE_UTILIZATION")}


def _newest_run(project: Path) -> Path | None:
    # Same selection as engineer_loop._newest_stage_log / ingest: newest RUN dir by mtime.
    runs = [d for d in project.glob("backend/RUN_*") if d.is_dir()]
    return max(runs, key=lambda d: d.stat().st_mtime) if runs else None


def backend_facts(project: Path | str) -> dict:
    """{stage, error_code, timeout} of the newest backend run's abort (all None/False
    when the newest run did not abort)."""
    run = _newest_run(Path(project))
    out: dict[str, Any] = {"stage": None, "error_code": None, "timeout": False,
                           "perimeter_ratio": None}
    if run is None:
        return out
    try:
        rows = [json.loads(ln) for ln in (run / "stage_log.jsonl").read_text().splitlines()
                if ln.strip()]
    except (OSError, ValueError):
        rows = []
    if rows and rows[-1].get("status") not in (0, "0", "pass"):
        out["stage"] = rows[-1].get("stage")
        try:
            out["timeout"] = int(rows[-1].get("status")) in TIMEOUT_RCS
        except (TypeError, ValueError):
            pass
    try:
        tail = (run / "flow.log").read_text(errors="ignore").splitlines()[-500:]
    except OSError:
        tail = []
    for ln in tail:
        m = ORFS_ERRCODE_RE.search(ln)
        if m:
            out["error_code"] = m.group(1)
            p = _PERIMETER_RE.search(ln)
            if p and float(p.group(1)) > 0:
                out["perimeter_ratio"] = float(p.group(2)) / float(p.group(1))
            break
    return out


def from_project(project: Path | str, check: str, violation_class: str | None = None,
                 before_count: Any = None, platform: str | None = None) -> dict:
    """Situation of `project` as it stands now — call BEFORE the fix edit."""
    cf = config_facts(project)
    bf = backend_facts(project) if check in ("route", "orfs_stage") else {}
    return canonical(check, violation_class=violation_class,
                     error_code=bf.get("error_code"), timeout=bf.get("timeout", False),
                     perimeter_ratio=bf.get("perimeter_ratio"),
                     platform=platform or cf["platform"], die_mode=cf["die_mode"],
                     util=cf["util"], before_count=before_count)


def from_fix_log_row(row: dict, project: Path | str | None = None,
                     platform: str | None = None) -> tuple[dict | None, str | None]:
    """(situation, source) for one fix_log row: the writer's pre-fix snapshot when
    present ('snapshot'), else derived from the row + project at ingest ('ingest').
    Returns (None, None) when neither is possible."""
    snap = row.get("situation")
    if isinstance(snap, dict) and snap.get("v") in READABLE_VERSIONS and snap.get("check"):
        return snap, "snapshot"
    check = row.get("check")
    if not check or project is None:
        return None, None
    return (from_project(project, check, row.get("violation_class"),
                         row.get("before"), platform=platform), "ingest")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="print the project's current situation as JSON")
    s.add_argument("project")
    s.add_argument("--check", required=True)
    s.add_argument("--vclass", default="")
    s.add_argument("--before", default="")
    a = ap.parse_args(argv)
    try:
        sit = from_project(a.project, a.check, a.vclass or None, a.before or None)
    except Exception:           # best-effort: a snapshot failure never breaks a fix loop
        print("")
        return 0
    print(json.dumps(sit, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
