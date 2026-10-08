#!/usr/bin/env python3
"""Acceptance check v1: the frozen sign-off checklist (memory/evaluation/r2g_acceptance_checklist_v1_20261004.md).

A project is ACCEPTED only when all eleven checks pass. A missing or unreadable input is "unknown",
which never counts as a pass. Reads only the project's reports and constraints; writes
reports/acceptance.json.

usage: acceptance_check.py <project-dir> --expected-clock-period NS [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VERSION = "r2g.acceptance.v1"


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clock_period(sdc: Path):
    try:
        m = re.search(r"set\s+clk_period\s+([0-9.]+)", sdc.read_text(errors="replace"))
    except OSError:
        return None
    return float(m.group(1)) if m else None


def evaluate(project: Path, expected_clock: float | None) -> dict:
    rep = project / "reports"
    gate = _load(rep / "signoff_gate.json") or {}
    g = gate.get("checks") or {}
    drc, lvs, route, rcx = (_load(rep / n) for n in ("drc.json", "lvs.json", "route.json", "rcx.json"))
    ppa = _load(rep / "ppa.json")
    t = ((ppa or {}).get("summary") or {}).get("timing") if ppa else None
    lvs_result = _load(project / "lvs" / "netgen_lvs_result.json") or {}
    checks = {}

    def put(name, ok, detail):
        checks[name] = {"result": "unknown" if ok is None else ("pass" if ok else "fail"), "detail": detail}

    gs = lambda k: (g.get(k) or {}).get("status") if g else None
    put("A1_flow_complete", None if not g else gs("orfs") == "complete", {"orfs": gs("orfs")})
    put("A2_drc_full_deck", None if drc is None else (drc.get("status") == "clean" and not _num(drc.get("total_violations"))),
        {"status": (drc or {}).get("status"), "violations": (drc or {}).get("total_violations")})
    put("A3_lvs", None if lvs is None else lvs.get("status") == "clean",
        {"status": (lvs or {}).get("status"), "mismatch_class": (lvs or {}).get("mismatch_class"),
         "cell_override_receipt_sha256": lvs_result.get("cell_override_receipt_sha256") or None})
    put("A4_route", None if route is None else (route.get("status") == "clean" and not _num(route.get("total_violations"))
                                                 and route.get("completed") is True),
        {"status": (route or {}).get("status"), "violations": (route or {}).get("total_violations")})
    put("A5_antenna", None if not g else gs("antenna") == "clean", {"antenna": gs("antenna")})
    sw, st, hw, ht = ((_num((t or {}).get(k)) for k in ("setup_wns", "setup_tns", "hold_wns", "hold_tns")))
    put("A6_setup", None if sw is None or st is None else (sw >= 0 and st == 0), {"setup_wns": sw, "setup_tns": st})
    put("A7_hold", None if hw is None or ht is None else (hw >= 0 and ht == 0), {"hold_wns": hw, "hold_tns": ht})
    cap, slew = _num((t or {}).get("max_cap_violations")), _num((t or {}).get("max_slew_violations"))
    put("A8_drv_limits", None if cap is None or slew is None else (cap == 0 and slew == 0),
        {"max_cap_violations": cap, "max_slew_violations": slew})
    period = _clock_period(project / "constraints" / "constraint.sdc")
    put("A9_task_clock", None if period is None or expected_clock is None else abs(period - expected_clock) < 1e-9,
        {"final_period": period, "expected_period": expected_clock})
    put("A10_rcx", None if rcx is None else (rcx.get("status") == "complete" and bool(rcx.get("spef_file"))
                                             and (_num(rcx.get("net_count")) or 0) > 0),
        {"status": (rcx or {}).get("status"), "net_count": (rcx or {}).get("net_count")})
    prov = {k: gs(k) for k in ("binding", "report_binding", "artifact_digest")}
    put("A11_provenance", None if not g else all(v == "bound" for v in prov.values()), prov)
    accepted = all(c["result"] == "pass" for c in checks.values())
    return {"schema": VERSION, "project": str(project), "accepted": accepted,
            "failed": sorted(k for k, c in checks.items() if c["result"] == "fail"),
            "unknown": sorted(k for k, c in checks.items() if c["result"] == "unknown"),
            "checks": checks}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--expected-clock-period", type=float, default=None,
                    help="the task's original clock period (ns); without it A9 is unknown (not accepted)")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    res = evaluate(a.project, a.expected_clock_period)
    out = a.out or (a.project / "reports" / "acceptance.json")
    out.write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"accepted": res["accepted"], "failed": res["failed"], "unknown": res["unknown"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
