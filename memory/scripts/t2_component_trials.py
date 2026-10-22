#!/usr/bin/env python3
"""GRT component trials: contract validation and per-trial classification (user review
2026-10-18). Plans and checks only; launches nothing.

* validate(contract): fixed states and components; identical component list per state; no
  component equal to the ORFS default or to the state's own value (a no-op is never a fix);
  distinct effective values; immutable inputs hash-checked on disk; flow arithmetic; 0 LLM.
* classify(...): one trial reported on SEPARATE axes -- whether the edit was actually applied
  (from the execution-bound effective-config receipts), congestion (labels v2 route effect),
  any later failure, strict acceptance -- and fix credit only for an APPLIED action whose
  congestion CLEARED. A control never earns fix credit.

  python3 t2_component_trials.py validate --contract <json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

DEFAULTS = {"GPL_ROUTABILITY_DRIVEN": "1", "ROUTING_LAYER_ADJUSTMENT": "0.5"}   # ORFS variables.yaml


def _norm(v):
    try:
        return repr(float(v))
    except (TypeError, ValueError):
        return str(v)


def validate(c: dict, check_files: bool = True) -> list:
    errs = []
    states = c.get("states") or []
    if c.get("platform") != "sky130hd":
        errs.append("platform must be sky130hd")
    if len(states) != 2:
        errs.append(f"{len(states)} states; the contract fixes exactly 2")
    ids = [tuple(x["id"] for x in s.get("components") or []) for s in states]
    if len(set(ids)) > 1:
        errs.append("component lists differ between states")
    for s in states:
        comps = s.get("components") or []
        if sum(x.get("kind") == "action" for x in comps) != 4 or sum(x.get("kind") == "control" for x in comps) != 1:
            errs.append(f"{s.get('design')}: need 4 actions and 1 control")
        state_cfg = {"CORE_UTILIZATION": str(s.get("CORE_UTILIZATION"))}
        seen = set()
        for x in comps:
            edit = x.get("edit") or {}
            if x.get("kind") == "control":
                if edit:
                    errs.append(f"{s.get('design')}/{x['id']}: a control carries no edit")
                continue
            if not edit:
                errs.append(f"{s.get('design')}/{x['id']}: an action needs an edit")
            for k, v in edit.items():
                if _norm(v) == _norm(DEFAULTS.get(k)) or _norm(v) == _norm(state_cfg.get(k)):
                    errs.append(f"{s.get('design')}/{x['id']}: {k}={v} is already in effect (no-op, never a fix)")
            key = json.dumps({k: _norm(v) for k, v in edit.items()}, sort_keys=True)
            if key in seen:
                errs.append(f"{s.get('design')}/{x['id']}: duplicates another component's effective edit")
            seen.add(key)
        if check_files:
            for group, files in (s.get("immutable") or {}).items():
                for path, want in files.items():
                    p = Path(path)
                    got = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
                    if got != want:
                        errs.append(f"{s.get('design')}: {group} {path} hash {got} != {want}")
    caps = c.get("caps") or {}
    need = sum(1 + len(s.get("components") or []) for s in states)
    if need != caps.get("flows_total") or caps.get("llm_requests") != 0 or caps.get("memory_backend") != "none":
        errs.append(f"caps: {need} flows needed vs flows_total {caps.get('flows_total')}; llm 0; memory none")
    if c.get("no_automatic_expansion") is not True:
        errs.append("no_automatic_expansion must be true")
    area = c.get("area") or {}
    if area.get("caps") != {"stdcell": 0.55, "core": 1.0, "die": 1.0}:
        errs.append("area caps must be the approved +55% stdcell / +100% core / +100% die")
    if not area.get("reference") or not area.get("manifest") or "envelope" not in str(area.get("rule")):
        errs.append("area reference, manifest and envelope rule must be explicit")
    return errs


def applied(edit: dict, baseline_eff: dict | None, trial_eff: dict | None) -> str:
    """APPLIED / NO_OP / UNVERIFIED from the two runs' execution-bound effective configs."""
    if not edit:
        return "CONTROL"
    if baseline_eff is None or trial_eff is None:
        return "UNVERIFIED"
    want = {k: _norm(v) for k, v in edit.items()}
    if any(_norm(trial_eff.get(k)) != want[k] for k in want):
        return "UNVERIFIED"                                   # the receipt does not show the edit
    return "NO_OP" if all(_norm(baseline_eff.get(k)) == want[k] for k in want) else "APPLIED"


def classify(component: dict, baseline_eff, trial_eff, congestion: str, post_stage: dict | None,
             acceptance: dict | None, envelope: dict | None = None) -> dict:
    """congestion: labels-v2 route effect (CLEARED / IMPROVED / NO_CHANGE / REGRESSED / UNKNOWN).
    post_stage: {'present', 'stage', 'error_code', 'timeout'} of the trial run's stage log."""
    app = applied(component.get("edit") or {}, baseline_eff, trial_eff)
    later = None
    if post_stage and post_stage.get("present") and congestion == "CLEARED":
        later = {k: post_stage.get(k) for k in ("stage", "error_code", "timeout")}
    acc = "UNKNOWN" if not acceptance else ("ACCEPTED" if acceptance.get("accepted") is True else "NOT_ACCEPTED")
    env_status = (envelope or {}).get("status") or "UNVERIFIED"
    fix = component.get("kind") == "action" and app == "APPLIED" and congestion == "CLEARED" and env_status == "PASS"
    return {"component": component["id"], "kind": component.get("kind"), "applied": app,
            "congestion": congestion, "later_failure": later, "acceptance": acc, "envelope": env_status,
            "area": ((envelope or {}).get("area") or {}).get("metrics"),
            "fix_credit": fix, "task_success": fix and acc == "ACCEPTED"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--contract", type=Path, required=True)
    a = ap.parse_args(argv)
    errs = validate(json.loads(a.contract.read_text()))
    print(json.dumps({"valid": not errs, "errors": errs,
                      "sha256": hashlib.sha256(a.contract.read_bytes()).hexdigest()}, indent=1))
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
