#!/usr/bin/env python3
"""Shared-history collection: contract checks, queue, first-episode reality check, accounting
(user scope, 2026-10-17; contract memory/evaluation/t2_history_collection_contract.json).

This module PLANS and GATES; it launches nothing. The EDA steps (exploration flow, repair
episode) are wired only after the user records an authorisation for the exact contract hash.

Billing: every ORFS flow counts toward one total -- each ladder flow (default, u80, u90) is an
EXPLORATION run; flows launched inside a repair episode (catalogue steps, recoveries, rollback
reruns) are REPAIR flows; any flow launched for final verification is FINAL. A repair episode
reuses its exploration run as its starting flow (engineer_loop add --reuse-flow-returncode),
so that flow is never launched twice. Exploration runs are counted and retained whether they
pass, fail outside the targets, or hit a target. No automatic expansion: a cap ends the queue.

  python3 t2_history_collection.py plan --contract <json>
  python3 t2_history_collection.py check-chain --project <dir> --source-v1 <dir> --t2-lock <json>
  python3 t2_history_collection.py account --root <campaign dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

LADDER = ("default", "u80", "u90")
TARGETS = ("route_abort_GRT-0116", "drc_li.3", "drc_m3.2", "drv", "place_abort_FLW-0024")
COMPONENT = {"initial": "exploration", "qualification": "exploration", "catalogue": "repair",
             "memory": "repair", "llm_repair": "repair", "final_acceptance": "final"}


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- contract

def validate(c: dict) -> list:
    """Every violated contract rule, as text (empty = valid)."""
    errs = []
    caps, split = c.get("caps") or {}, c.get("split") or {}
    learn, verify = set(split.get("learn") or []), set(split.get("verify") or [])
    if learn & verify:
        errs.append(f"clusters both learn and verify: {sorted(learn & verify)}")
    designs = c.get("designs") or []
    if len(designs) > caps.get("designs", 0):
        errs.append(f"{len(designs)} designs > cap {caps.get('designs')}")
    excluded = set(c.get("exclusions", {}).get("designs") or [])
    for d in designs:
        if d.get("cluster") not in learn:
            errs.append(f"{d.get('design')}: cluster {d.get('cluster')!r} is not a learning cluster")
        if d.get("design") in excluded:
            errs.append(f"{d.get('design')}: excluded design")
    if tuple(c.get("ladder") or ()) != LADDER:
        errs.append(f"ladder must be the fixed {LADDER}")
    if (c.get("repair") or {}).get("memory_backend") != "none" or (c.get("repair") or {}).get("llm") is not False:
        errs.append("repair must run with memory_backend none and llm false")
    if caps.get("llm_requests") != 0:
        errs.append("llm_requests cap must be 0")
    need = len(designs) * len(LADDER) + min(caps.get("repair_episodes", 0),
                                            len(designs) * c.get("stop_after_failures_per_design", 0)) \
        * caps.get("flows_per_episode", 0)
    if need > caps.get("flows_total", 0):
        errs.append(f"worst case {need} flows exceeds flows_total {caps.get('flows_total')}: "
                    "the total cap would cut an episode")
    if c.get("no_automatic_expansion") is not True:
        errs.append("no_automatic_expansion must be true")
    return errs


def worst_case(c: dict) -> dict:
    caps = c["caps"]
    eps = min(caps["repair_episodes"], len(c["designs"]) * c["stop_after_failures_per_design"])
    return {"exploration_flows_max": len(c["designs"]) * len(LADDER), "repair_episodes_max": eps,
            "repair_flows_max": eps * caps["flows_per_episode"],
            "total_max": len(c["designs"]) * len(LADDER) + eps * caps["flows_per_episode"],
            "flows_total_cap": caps["flows_total"]}


# ---------------------------------------------------------------- queue

def next_action(c: dict, state: dict) -> dict:
    """The next step given the records so far. state = {flows_spent, records: [...],
    suspended: str|None, chain_checked: bool}. Records: {op, design, step, outcome}
    with outcome for explore in {clean, target, non_target, infra} (+ target name)."""
    caps = c["caps"]
    if state.get("suspended"):
        return {"op": "stop", "reason": "suspended: " + state["suspended"]}
    recs = state.get("records") or []
    repairs = [r for r in recs if r["op"] == "repair"]
    # A separately authorized continuation keeps old records/costs, but does not
    # pretend that a failed old gate passed. Gate the first episode on NEW code.
    gate_repairs = [r for r in recs[state.get('chain_gate_record_offset', 0):] if r['op'] == 'repair']
    if gate_repairs and not state.get("chain_checked"):
        return {"op": "check_chain", "design": gate_repairs[0]["design"], "step": gate_repairs[0]["step"]}
    remaining = caps["flows_total"] - state.get("flows_spent", 0)
    for d in c["designs"]:
        name = d["design"]
        mine = [r for r in recs if r["design"] == name]
        failures = [r for r in mine if r["op"] == "explore" and r["outcome"] in ("target", "non_target")]
        if any(r["op"] == "explore" and r["outcome"] == "infra" for r in mine):
            continue
        # a target exploration without its repair episode yet
        for r in mine:
            if r["op"] == "explore" and r["outcome"] == "target" and \
                    not any(x["op"] == "repair" and x["step"] == r["step"] for x in mine):
                if len(repairs) >= caps["repair_episodes"]:
                    return {"op": "stop", "reason": "repair episode cap reached (no automatic expansion)"}
                if remaining <= 0:
                    return {"op": "stop", "reason": "flow cap reached (no automatic expansion)"}
                return {"op": "repair", "design": name, "step": r["step"], "target": r.get("target"),
                        "flows_cap": min(caps["flows_per_episode"], remaining)}
        if len(failures) >= c["stop_after_failures_per_design"]:
            continue
        done = {r["step"] for r in mine if r["op"] == "explore"}
        for step in LADDER:
            if step not in done:
                if remaining <= 0:
                    return {"op": "stop", "reason": "flow cap reached (no automatic expansion)"}
                return {"op": "explore", "design": name, "step": step, "flows_cap": 1}
    return {"op": "stop", "reason": "queue complete"}


def classify(measure: dict, drc_categories: dict | None) -> dict:
    """Exploration outcome from typed measurements of the run (ann_v2.fields.measure shape)."""
    st = measure["stage"]
    if not st.get("measured"):
        return {"outcome": "infra", "detail": "no stage log"}
    if st.get("present"):
        stage = st["fields"]["stage"].get("value")
        code = st["fields"]["error_code"].get("value")
        if stage == "synth":
            return {"outcome": "infra", "detail": "synth abort"}
        if stage == "route" and code == "GRT-0116":
            return {"outcome": "target", "target": "route_abort_GRT-0116"}
        if stage == "place" and code == "FLW-0024":
            return {"outcome": "target", "target": "place_abort_FLW-0024"}
        return {"outcome": "non_target", "detail": f"{stage} abort {code}"}
    cats = {k: (v.get("count") if isinstance(v, dict) else v) for k, v in (drc_categories or {}).items()}
    for rule in ("li.3", "m3.2"):
        if (cats.get(rule) or 0) > 0:
            return {"outcome": "target", "target": "drc_" + rule}
    if measure["drv"].get("present"):
        return {"outcome": "target", "target": "drv"}
    others = [k for k in CHECKS_FAIL if measure[k].get("present")]
    if any((v or 0) > 0 for v in cats.values()) or others:
        return {"outcome": "non_target", "detail": "failures " + ",".join(others or ["drc_other"])}
    return {"outcome": "clean"}


CHECKS_FAIL = ("route_residual", "lvs", "setup", "hold")


# ---------------------------------------------------------------- reality check (first episode)

def chain_problems(project: Path, offset: int = 0) -> list:
    """Raw chain-link checks on the episode's fix-log rows (from row `offset`)."""
    log = project / "reports" / "fix_log.jsonl"
    rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()][offset:] if log.exists() else []
    out = []
    attempts = [r for r in rows if r.get("strategy") not in (None, "none")
                and not str(r.get("verdict") or "").startswith("stop")]
    if not attempts:
        out.append("no attempt row in the episode")
    for r in attempts:
        rid = r.get("action_id") or f"{r.get('fix_session_id')}#?"
        if r.get("chain_version") != 1 or not r.get("action_id"):
            out.append(f"{rid}: no chain_version 1 / action_id")
            continue
        if not r.get("pre_run_tag"):
            out.append(f"{rid}: pre_run_tag missing")
        if r.get("from_stage") and str(r.get("verdict")) not in ("apply_failed", "budget_exhausted",
                                                                  "accounting_error"):
            tag = r.get("post_run_tag")
            if not tag:
                out.append(f"{rid}: post_run_tag missing for a rerun step")
            else:
                rec = project / "backend" / tag / "r2g_action.json"
                try:
                    if json.loads(rec.read_text()).get("action_id") != r["action_id"]:
                        out.append(f"{rid}: {tag}/r2g_action.json names another action")
                except (OSError, ValueError):
                    out.append(f"{rid}: {tag}/r2g_action.json missing")
            sc = r.get("step_check")
            if isinstance(sc, dict) and sc.get("graded_post_run") is False:
                out.append(f"{rid}: re-check graded a run other than the post run")
        if not isinstance(r.get("rolled_back"), bool):
            out.append(f"{rid}: rolled_back marker missing")
    return out


def reality_check(project: Path, source_v1: Path, t2_lock: Path, out: Path, offset: int = 0) -> dict:
    """Raw link checks, then the ann-v2 gate on the episode alone: every rerun attempt must
    have both runs KNOWN through its explicit links and the verifier must pass."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ann_v2 import annotate as AN, dev_export as DX, verify as VF
    problems = chain_problems(project, offset)
    src = out / "episode-source"
    lock = json.loads(Path(t2_lock).read_text())
    DX.build(project.parent, [project.name], source_v1, "", set(),
             {e.get("design") for e in lock.get("entries", []) if e.get("design")}, src)
    AN.main(["--source", str(src), "--out", str(out / "ann")])
    rep = VF.verify(out / "ann" / "ann_v2_dev.jsonl", src)
    if rep["gate"] != "PASS":
        problems.append(f"verifier gate {rep['gate']} ({rep['critical']} critical)")
    for l in (out / "ann" / "ann_v2_dev.jsonl").read_text().splitlines():
        r = json.loads(l)
        if r.get("status") != "ATTEMPT":
            continue
        rows = [json.loads(x) for x in (src / "source_export.jsonl").read_text().splitlines()]
        raw = next(u["raw_row"] for u in rows if u["unit_id"] == r["unit_id"])
        if raw.get("from_stage") and raw.get("post_run_tag"):
            for side in ("post", "pre"):
                if r["binding"][side]["status"] != "KNOWN":
                    problems.append(f"{raw.get('action_id')}: {side} run not bound "
                                    f"({r['binding'][side]['unknown_reason']})")
    result = {"ok": not problems, "problems": problems, "verifier_gate": rep["gate"]}
    (out / "reality_check.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


# ---------------------------------------------------------------- accounting and history view

def account(ledgers) -> dict:
    """Flows by billing class over every per-run ledger of the campaign (UNKNOWN stays UNKNOWN)."""
    out = {"exploration": 0, "repair": 0, "final": 0, "unclassified": 0, "unknown_amounts": 0}
    for path in ledgers:
        for l in Path(path).read_text().splitlines():
            try:
                e = json.loads(l)
            except ValueError:
                continue
            if e.get("type") != "budget_charge" or e.get("resource") != "flows":
                continue
            amt = e.get("amount")
            if not isinstance(amt, (int, float)):
                out["unknown_amounts"] += 1
                continue
            out[COMPONENT.get(e.get("component"), "unclassified")] += amt
    out["total_known"] = out["exploration"] + out["repair"] + out["final"] + out["unclassified"]
    return out


def history_view(records: list, split: dict, purpose: str) -> list:
    """Units usable as experience for `purpose`. For DEV verification, units of a VERIFY
    cluster are removed: other configurations of the same IP may serve technical link checks,
    never evidence of transfer. T2 (independent TEST designs) may use every cluster."""
    if purpose == "t2":
        return list(records)
    if purpose != "dev_verification":
        raise ValueError(purpose)
    verify = set(split.get("verify") or []) | set(split.get("verify_source_groups") or [])
    return [r for r in records
            if ((r.get("identity") or {}).get("ip_family") or {}).get("value") is not None
            and (r["identity"].get("source_group") or {}).get("value") not in verify
            and (r["identity"].get("ip_family") or {}).get("value") not in verify]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--contract", type=Path, required=True)
    k = sub.add_parser("check-chain")
    k.add_argument("--project", type=Path, required=True)
    k.add_argument("--source-v1", type=Path, required=True)
    k.add_argument("--t2-lock", type=Path, required=True)
    k.add_argument("--out", type=Path, required=True)
    k.add_argument("--offset", type=int, default=0)
    a = sub.add_parser("account")
    a.add_argument("--ledger", type=Path, nargs="+", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "plan":
        c = json.loads(args.contract.read_text())
        errs = validate(c)
        print(json.dumps({"contract_sha256": sha256(args.contract), "valid": not errs, "errors": errs,
                          "worst_case": worst_case(c) if not errs else None,
                          "first_action": next_action(c, {"flows_spent": 0, "records": []}) if not errs else None},
                         indent=1))
        return 1 if errs else 0
    if args.cmd == "check-chain":
        args.out.mkdir(parents=True, exist_ok=True)
        r = reality_check(args.project, args.source_v1, args.t2_lock, args.out, args.offset)
        print(json.dumps(r, indent=1))
        return 0 if r["ok"] else 1
    print(json.dumps(account(args.ledger), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
