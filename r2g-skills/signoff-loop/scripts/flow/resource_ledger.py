#!/usr/bin/env python3
"""Per-attempt resource ledger with one shared total budget (P1a).

Append-only JSONL, one file per project attempt: ``$R2G_LEDGER_PATH`` or
``<project>/reports/resource_ledger.jsonl``. Every EDA flow passes through ``run_orfs.sh``,
which calls ``admit`` before launching and ``flow-run`` after it; callers tag the component
(``R2G_BUDGET_COMPONENT`` = initial | catalogue | memory | llm_repair | final_acceptance), so
memory trials and catalogue steps are charged to the SAME total.

Budget: ``$R2G_BUDGET_FILE`` (JSON) ``{"budget_id": ..., "caps": {resource: amount},
"reserve": {resource: amount}}``. Resources: ``flows``, ``wall_s`` (since the attempt's first
event), ``llm_requests``, ``tokens_in``, ``tokens_out``. A request with purpose
``final_acceptance`` may spend the reserve; any other must leave it intact. Without a budget
file the ledger only records (no caps). ``"halt_on_unknown_usage": true`` keeps an UNCAPPED budget
metered and fail-closed: once any request or token amount is UNKNOWN, every further admission is
refused (the queue pauses for review) although no balance cap exists (T2a independent ledgers,
2026-10-16).

Hard-cap rules:
- **Read, check and reserve are one critical section** (an exclusive lock on the ledger file),
  so concurrent requests of one attempt cannot both pass a cap that fits only one.
- **Reserve before use, settle after.** ``admit --hold`` books an upper bound (e.g. a request's
  maximum input and output tokens) and prints a hold id; ``settle`` replaces it with the actual
  usage. An open hold counts at its booked amount.
- **Usage that was not reported is UNKNOWN, never 0.** A capped resource with an UNKNOWN amount
  cannot be shown to be within budget, so ``admit`` refuses (fail closed).
- **Wall time.** ``remaining`` prints the seconds left before the attempt's deadline, so callers
  bound running processes (ORFS stages, LLM requests) by it, not only their launch.

Exit codes: 0 ok; 75 refused, budget exhausted; 2 usage or accounting error (a caller with a
budget configured must stop, and must treat it neither as exhaustion nor as permission).
Protocol: TEHM_Paper_Experiment_Protocol_v1 sections 7.2 and 10; spec
memory/evaluation/r2g_P0_event_resource_ledger_spec_20261008.md.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
from pathlib import Path

EXIT_BUDGET = 75
EXIT_ERROR = 2
UNKNOWN = "UNKNOWN"
# repair_attempts: LLM repair-candidate generations (T2a A1/A2: at most 3 per task, arm and repeat), distinct
# from llm_requests (every network request, retries and helpers included)
RESOURCES = ("flows", "wall_s", "llm_requests", "tokens_in", "tokens_out", "repair_attempts")
_ELAPSED = re.compile(r"Elapsed time:\s*([\d:.]+)\[h:\]min:sec\.\s*CPU time:\s*user\s*([\d.]+)\s*sys\s*([\d.]+)")


def ledger_path(project: str | os.PathLike) -> Path:
    return Path(os.environ.get("R2G_LEDGER_PATH") or Path(project) / "reports" / "resource_ledger.jsonl")


def load_budget() -> dict:
    path = os.environ.get("R2G_BUDGET_FILE")
    if not path:
        return {"budget_id": None, "caps": {}, "reserve": {}}
    b = json.loads(Path(path).read_text())
    for key in ("caps", "reserve"):
        bad = set(b.get(key) or {}) - set(RESOURCES)
        if bad:
            raise ValueError("unknown resource(s) in budget %s: %s" % (key, sorted(bad)))
    combined = b.get("combined_tokens_cap")
    if combined is not None and (isinstance(combined, bool) or not isinstance(combined, (int, float))
                                 or not math.isfinite(combined) or combined < 0):
        raise ValueError("invalid combined_tokens_cap")
    halt = b.get("halt_on_unknown_usage", False)
    if not isinstance(halt, bool):
        raise ValueError("invalid halt_on_unknown_usage (must be true or false)")
    return {"budget_id": b.get("budget_id"), "caps": dict(b.get("caps") or {}),
            "reserve": dict(b.get("reserve") or {}), "combined_tokens_cap": combined,
            "halt_on_unknown_usage": halt}


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def transact(path: Path, decide):
    """Run ``decide(events) -> (events_to_append, result)`` under an exclusive lock: read, check and
    append are one critical section. Appended events get ``event_seq`` and ``ts`` inside the lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        events = [json.loads(line) for line in fh if line.strip()]
        new, result = decide(events)
        if new:
            fh.seek(0, os.SEEK_END)
            for i, e in enumerate(new):
                fh.write(json.dumps({"event_seq": len(events) + i + 1, "ts": round(time.time(), 3), **e},
                                    sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    return result


def _add(a, b):
    return UNKNOWN if UNKNOWN in (a, b) else a + b


def totals(events: list[dict], now: float | None = None) -> dict:
    """Spent per resource: charges + settled actuals + open holds at their booked amount."""
    out: dict = {r: 0 for r in RESOURCES}
    settled = {e["hold_id"] for e in events if e.get("type") == "budget_settle"}
    for e in events:
        t = e.get("type")
        if t == "budget_charge":
            out[e["resource"]] = _add(out[e["resource"]], e["amount"])
        elif t == "budget_hold" and e["hold_id"] not in settled:
            for r, a in e["needs"].items():
                out[r] = _add(out[r], a)
        elif t == "budget_settle":
            for r, a in e["actual"].items():
                out[r] = _add(out[r], a)
    if events:
        out["wall_s"] = round((now or time.time()) - events[0]["ts"], 3)
    return out


def occupancy_target(path, sha, ledger, hold_id):
    """An explicit user decision, hash-bound and scoped to one existing settlement."""
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('UNKNOWN occupancy authorization changed')
    grant = json.loads(raw)
    if grant.get('schema') != 'r2g-unknown-occupancy-authorization-v1' or grant.get('user_approved') is not True:
        raise ValueError('UNKNOWN occupancy authorization missing')
    matches = [r for r in grant.get('targets', [])
               if r.get('ledger_path') == str(Path(ledger).resolve()) and r.get('hold_id') == hold_id]
    if len(matches) != 1:
        raise ValueError('UNKNOWN occupancy authorization does not name this hold/ledger')
    return matches[0]['upper_bound']


def validate_occupancy(events, hold_id, bounds):
    holds = [e for e in events if e.get('type') == 'budget_hold' and e.get('hold_id') == hold_id]
    settled = [e for e in events if e.get('type') == 'budget_settle' and e.get('hold_id') == hold_id]
    if len(holds) != 1 or len(settled) != 1:
        raise ValueError('UNKNOWN occupancy needs one existing hold and settlement')
    unknown = {r for r, v in settled[0]['actual'].items() if v == UNKNOWN}
    if not unknown or not unknown <= {'tokens_in', 'tokens_out'} or set(bounds) != unknown:
        raise ValueError('UNKNOWN occupancy applies only to the named UNKNOWN token fields')
    for r, value in bounds.items():
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0 or value != holds[0]['needs'].get(r)
                or value != settled[0]['booked'].get(r)):
            raise ValueError('UNKNOWN occupancy must retain the original booked bound')


def budget_totals(events, now=None):
    """Safety occupancy, NEVER reported actual usage. Unapproved UNKNOWNs stay UNKNOWN."""
    approved = {}
    for e in events:
        if e.get('type') != 'budget_unknown_occupancy':
            continue
        hold = e['hold_id']
        if hold in approved:
            raise ValueError('Duplicate UNKNOWN occupancy')
        bounds = occupancy_target(e['authorization'], e['authorization_sha256'], e['ledger_path'], hold)
        if bounds != e['upper_bound']:
            raise ValueError('UNKNOWN occupancy differs from authorization')
        validate_occupancy(events, hold, bounds)
        approved[hold] = bounds
    safety = []
    for e in events:
        if e.get('type') == 'budget_settle' and e['hold_id'] in approved:
            e = {**e, 'actual': {**e['actual'], **approved[e['hold_id']]}}
        safety.append(e)
    return totals(safety, now)


def check(events: list[dict], budget: dict, needs: dict, purpose: str, now: float | None = None) -> tuple[bool, str]:
    """May ``needs`` ({resource: amount}) be booked now? Every capped resource is checked (wall time too)."""
    spent = budget_totals(events, now)
    if budget.get("halt_on_unknown_usage") and any(spent.get(k) == UNKNOWN
                                                   for k in ("llm_requests", "tokens_in", "tokens_out")):
        return False, "usage is UNKNOWN; queue paused for review (halt_on_unknown_usage)"
    cap = budget.get("combined_tokens_cap")
    if cap is not None:
        if any(spent[k] == UNKNOWN for k in ("tokens_in", "tokens_out")):
            return False, "combined tokens have UNKNOWN usage"
        if sum(spent[k] + needs.get(k, 0) for k in ("tokens_in", "tokens_out")) > cap:
            return False, "combined token cap exceeded"
    for r, cap in budget["caps"].items():
        used = spent.get(r, 0)
        if used == UNKNOWN:
            return False, "%s has an UNKNOWN amount; cannot show it is within the cap" % r
        keep = 0 if purpose == "final_acceptance" else budget["reserve"].get(r, 0)
        need = needs.get(r, 0)
        if used + need > cap - keep:
            return False, "%s: used %s + need %s > cap %s - reserve %s" % (r, used, need, cap, keep)
    return True, "ok"


def remaining(events: list[dict], budget: dict, purpose: str, now: float | None = None) -> dict:
    """{resource: amount left before the cap (less the reserve unless final_acceptance)}; None if uncapped,
    UNKNOWN if an amount is UNKNOWN. With a wall_s cap also ``deadline_epoch``."""
    now = now or time.time()
    spent = budget_totals(events, now)
    out = {}
    for r in RESOURCES:
        cap = budget["caps"].get(r)
        if cap is None:
            out[r] = None
            continue
        keep = 0 if purpose == "final_acceptance" else budget["reserve"].get(r, 0)
        used = spent.get(r, 0)
        out[r] = UNKNOWN if used == UNKNOWN else round(cap - keep - used, 3)
    if out.get("wall_s") not in (None, UNKNOWN):
        out["deadline_epoch"] = round(now + out["wall_s"], 3)
    cap = budget.get("combined_tokens_cap")
    out["combined_tokens"] = (None if cap is None else UNKNOWN
                              if UNKNOWN in (spent["tokens_in"], spent["tokens_out"])
                              else cap - spent["tokens_in"] - spent["tokens_out"])
    return out


def stage_times(run_dir: Path) -> list[dict]:
    """Per-stage wall and CPU seconds from the ORFS stage logs; UNKNOWN when a stage has no end line
    (e.g. killed by a timeout)."""
    logs = sorted((run_dir / "logs").rglob("*.log")) if (run_dir / "logs").is_dir() else []
    out = []
    for log in logs:
        m = None
        for m in _ELAPSED.finditer(log.read_text(errors="replace")):
            pass
        if m is None:
            out.append({"stage": log.stem, "wall_s": UNKNOWN, "cpu_s": UNKNOWN})
            continue
        parts = [float(x) for x in m.group(1).split(":")]
        wall = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
        out.append({"stage": log.stem, "wall_s": round(wall, 2), "cpu_s": round(float(m.group(2)) + float(m.group(3)), 2)})
    return out


def _ids() -> dict:
    keys = ("R2G_EXPERIMENT_ID", "R2G_ATTEMPT_ID", "R2G_ARM", "R2G_TASK_ID")
    return {k[4:].lower(): os.environ[k] for k in keys if os.environ.get(k)}


def _amounts(pairs, allow_unknown: bool) -> dict:
    out = {}
    for p in pairs or []:
        r, _, a = p.partition("=")
        if r not in RESOURCES or not a:
            raise ValueError("bad resource=amount: %r" % p)
        if a == UNKNOWN:
            if not allow_unknown:
                raise ValueError("%s: an amount to book must be numeric" % r)
            out[r] = UNKNOWN
        else:
            out[r] = float(a)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("admit", "charge", "settle", "occupy-unknown", "remaining", "flow-run", "event", "summary"):
        p = sub.add_parser(name)
        p.add_argument("project")
        if name in ("admit", "charge"):
            p.add_argument("--resource", choices=RESOURCES, help="single resource (with --amount)")
            p.add_argument("--amount")
            p.add_argument("--need", action="append", metavar="RESOURCE=AMOUNT", help="repeatable")
            p.add_argument("--component", default=os.environ.get("R2G_BUDGET_COMPONENT", "unspecified"))
            p.add_argument("--ref", default="")
        if name in ("admit", "charge", "remaining"):
            p.add_argument("--purpose", default=os.environ.get("R2G_BUDGET_PURPOSE", "repair"))
        if name == "admit":
            p.add_argument("--hold", action="store_true", help="book and print a hold id; settle later")
        if name == "settle":
            p.add_argument("--hold-id", required=True)
            p.add_argument("--actual", action="append", metavar="RESOURCE=AMOUNT", default=[])
        if name == 'occupy-unknown':
            p.add_argument('--hold-id', required=True)
            p.add_argument('--authorization', required=True)
            p.add_argument('--authorization-sha256', required=True)
        if name == "flow-run":
            p.add_argument("--run-dir", required=True)
            p.add_argument("--rc", type=int, required=True)
            p.add_argument("--component", default=os.environ.get("R2G_BUDGET_COMPONENT", "unspecified"))
        if name == "event":
            p.add_argument("--type", required=True)
            p.add_argument("--json", default="{}")
        if name == "summary":
            p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    path = ledger_path(a.project)
    try:
        budget = load_budget()
        amounts = {}
        if a.cmd in ("admit", "charge"):
            pairs = list(a.need or []) + (["%s=%s" % (a.resource, a.amount)] if a.resource else [])
            if not pairs:
                raise ValueError("give --resource/--amount or --need")
            amounts = _amounts(pairs, allow_unknown=(a.cmd == "charge"))
        elif a.cmd == "settle":
            amounts = _amounts(a.actual, allow_unknown=True)
    except (OSError, ValueError) as exc:
        print("resource_ledger: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    base = {"component": getattr(a, "component", None), "budget_id": budget["budget_id"], **_ids()}

    if a.cmd == 'occupy-unknown':
        def decide(events):
            if any(e.get('type') == 'budget_unknown_occupancy' and e.get('hold_id') == a.hold_id for e in events):
                raise ValueError('UNKNOWN occupancy already recorded')
            bounds = occupancy_target(a.authorization, a.authorization_sha256, path, a.hold_id)
            validate_occupancy(events, a.hold_id, bounds)
            return [{'type': 'budget_unknown_occupancy', 'hold_id': a.hold_id,
                     'upper_bound': bounds, 'actual_remains': UNKNOWN,
                     'authorization': str(Path(a.authorization).resolve()),
                     'authorization_sha256': a.authorization_sha256,
                     'ledger_path': str(path.resolve()), **_ids()}], None
        try:
            transact(path, decide)
        except (OSError, ValueError, KeyError) as exc:
            print('resource_ledger: %s' % exc, file=sys.stderr)
            return EXIT_ERROR
        return 0

    if a.cmd == "admit":
        hold_id = uuid.uuid4().hex[:12] if a.hold else None

        def decide(events):
            ok, why = check(events, budget, amounts, a.purpose)
            if not ok:
                return [{"type": "budget_refused", "needs": amounts, "purpose": a.purpose, "ref": a.ref,
                         "reason": why, **base}], (False, why)
            if hold_id:
                return [{"type": "budget_hold", "hold_id": hold_id, "needs": amounts, "purpose": a.purpose,
                         "ref": a.ref, **base}], (True, hold_id)
            return [{"type": "budget_charge", "resource": r, "amount": v, "purpose": a.purpose, "ref": a.ref, **base}
                    for r, v in amounts.items()], (True, "")
        ok, info = transact(path, decide)
        if not ok:
            print("resource_ledger: BUDGET EXHAUSTED (%s)" % info, file=sys.stderr)
            return EXIT_BUDGET
        if hold_id:
            print(hold_id)
        return 0
    if a.cmd == "charge":
        transact(path, lambda ev: ([{"type": "budget_charge", "resource": r, "amount": v, "purpose": a.purpose,
                                     "ref": a.ref, **base} for r, v in amounts.items()], None))
        return 0
    if a.cmd == "settle":
        def decide(events):
            holds = {e["hold_id"]: e for e in events if e.get("type") == "budget_hold"}
            done = {e["hold_id"] for e in events if e.get("type") == "budget_settle"}
            if a.hold_id not in holds or a.hold_id in done:
                return [], "unknown or already settled hold %s" % a.hold_id
            booked = holds[a.hold_id]["needs"]
            actual = {r: amounts.get(r, UNKNOWN) for r in booked}       # unreported booked usage is UNKNOWN
            actual.update({r: v for r, v in amounts.items() if r not in booked})
            return [{"type": "budget_settle", "hold_id": a.hold_id, "booked": booked, "actual": actual,
                     "component": holds[a.hold_id].get("component"), **_ids()}], None
        err = transact(path, decide)
        if err:
            print("resource_ledger: %s" % err, file=sys.stderr)
            return EXIT_ERROR
        return 0
    if a.cmd == "remaining":
        print(json.dumps(remaining(read_events(path), budget, a.purpose), sort_keys=True))
        return 0
    if a.cmd == "flow-run":
        run_dir = Path(a.run_dir) if a.run_dir else None
        transact(path, lambda ev: ([{"type": "flow_run", "run_dir": str(run_dir or ""),
                                     "run_tag": run_dir.name if run_dir else "", "rc": a.rc, "timeout": a.rc == 124,
                                     "budget_stop": a.rc == EXIT_BUDGET, "component": a.component,
                                     "stages": stage_times(run_dir) if run_dir else [], **_ids()}], None))
        return 0
    if a.cmd == "event":
        transact(path, lambda ev: ([{"type": a.type, **json.loads(a.json), **_ids()}], None))
        return 0
    events = read_events(path)
    s = {"budget_id": budget["budget_id"], "caps": budget["caps"], "reserve": budget["reserve"],
         "combined_tokens_cap": budget.get("combined_tokens_cap"),
         "halt_on_unknown_usage": budget.get("halt_on_unknown_usage", False),
         "spent": totals(events), "events": len(events),
         "budget_spent": budget_totals(events),
         "unknown_occupancies": [e for e in events if e.get('type') == 'budget_unknown_occupancy'],
         "refused": sum(e.get("type") == "budget_refused" for e in events),
         "open_holds": len({e["hold_id"] for e in events if e.get("type") == "budget_hold"}
                           - {e["hold_id"] for e in events if e.get("type") == "budget_settle"}),
         "flows_by_component": {}}
    for e in events:
        if e.get("type") == "budget_charge" and e["resource"] == "flows":
            c = e.get("component") or "unspecified"
            s["flows_by_component"][c] = _add(s["flows_by_component"].get(c, 0), e["amount"])
    print(json.dumps(s, sort_keys=True) if a.json else json.dumps(s, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
