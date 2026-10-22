"""Typed current failure for TEHM-v2 decisions, from the reports the diagnosis already reads.

failures: e.g. ['setup'], ['drv'], ['setup', 'drv'], ['drc:li.3'], ['route:GRT-0116'],
['place:FLW-0024']. Unconstrained timing (WNS >= 1e30) never yields setup/hold; a DRV entry
needs the timing report's drv status 'fail'. The signature is the evidence-index key of the
primary failure.
"""
from __future__ import annotations

UNCONSTRAINED = 1e30


def _dominant_rule(drc: dict) -> str | None:
    cats = (drc or {}).get("categories") or {}
    counts = {k: (v.get("count") if isinstance(v, dict) else v) for k, v in cats.items()}
    counts = {k: c for k, c in counts.items() if isinstance(c, (int, float)) and c > 0}
    return max(counts, key=lambda k: (counts[k], k)) if counts else None


def from_reports(check: str, situation: dict | None, drc: dict | None, route: dict | None,
                 timing: dict | None, cfg: dict | None) -> dict:
    sit = situation or {}
    failures, unconstrained = [], False
    if check == "drc":
        rule = _dominant_rule(drc or {})
        if rule:
            failures.append(f"drc:{rule}")
    elif check in ("route", "orfs_stage"):
        stage = sit.get("violation_class") or ("route" if check == "route" else None)
        if stage:
            failures.append(f"{stage}:{sit.get('error_code')}")
    elif check == "timing":
        t = timing or {}
        wns = t.get("wns_ns", t.get("wns"))
        unconstrained = t.get("tier") == "unconstrained" or (isinstance(wns, (int, float)) and wns >= UNCONSTRAINED)
        if not unconstrained:
            if isinstance(wns, (int, float)) and wns < 0:
                failures.append("setup")
            hold = t.get("hold_wns")
            if isinstance(hold, (int, float)) and hold < 0:
                failures.append("hold")
        if (t.get("drv") or {}).get("status") == "fail":
            failures.append("drv")
    elif check == "lvs":
        failures.append("lvs")
    return {"failures": failures, "signature": failures[0] if failures else None, "cfg": dict(cfg or {}),
            "timing_unconstrained": unconstrained}
