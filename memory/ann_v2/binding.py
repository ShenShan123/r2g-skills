"""ann-v2 run binding: ownership, not proximity (spec §4).

Evidence tiers:
  E1  explicit ownership -- decisive only when unique.
      post-action run: resume_meta.json reason names "strategy=<s> rerun_from=<stage>" and
      its from_stage equals the attempt's.
      pre-action run: iteration n>1 -> the bound post run of n-1 (its pre, recursively, when
      n-1 was rolled back; UNKNOWN when n-1's rollback is not recorded by raw evidence --
      rollback.py); iteration 1 -> the unique run that ran or reused the stage before
      from_stage with exactly the digest the post run reused AND recorded the target failure
      (parent_run alone names the artifact's PRODUCER, possibly an ancestor).
  E2  corroboration -- may drop or confirm candidates, never decides alone; an unavailable
      check is recorded as 'unavailable', never as a pass.
  E3  auxiliary (times) -- recorded for the audit; no code path reads it for a status.
Ordinal pairing is advisory only (ordinal_advisory): never a binding, never counted.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

STAGES = ("synth", "floorplan", "place", "cts", "route", "finish")
_REASON = re.compile(r"\bstrategy=(\S+) rerun_from=(\S+)")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path) -> list:
    try:
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def _finished(d: Path) -> bool:
    """The finish report is this run's evidence only when this run's stage log shows finish
    passing (failure-patterns #61: an early abort carried the previous flow's report)."""
    return any(r.get("stage") == "finish" and r.get("status") in (0, "0", "pass")
               for r in _read_jsonl(d / "stage_log.jsonl"))


def _read_run(d: Path) -> dict:
    eff = _read_json(d / "effective-config.json")
    rec = {
        "run": d.name,
        "stage_log": _read_jsonl(d / "stage_log.jsonl"),
        "manifest": _read_jsonl(d / "stage_artifact_manifest.jsonl"),
        "resume_meta": _read_json(d / "resume_meta.json"),
        "drc": _read_json(d / "drc" / "drc_result.json"),
        "lvs": _read_json(d / "lvs" / "netgen_lvs_result.json"),
        "timing": _read_json(d / "logs" / "6_report.json") if _finished(d) else None,
        "effective": eff.get("effective") if isinstance(eff, dict) and eff.get("status") == "PASS" else None,
        "action": (_read_json(d / "r2g_action.json") or {}).get("action_id"),
    }
    rec["fingerprint"] = hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()
    rec["paths"] = [str(d)]
    rec["copy_conflict"] = False
    return rec


def load_runs(projects) -> dict:
    """{run_tag: run} over every copy root of the attempt's project; copies of one run_tag
    whose content differs are marked copy_conflict (never silently merged)."""
    runs: dict = {}
    for proj in projects:
        for d in sorted(Path(proj).glob("backend/RUN_*")):
            if not d.is_dir():
                continue
            rec = _read_run(d)
            have = runs.get(d.name)
            if have is None:
                runs[d.name] = rec
            else:
                have["paths"].append(str(d))
                if have["fingerprint"] != rec["fingerprint"]:
                    have["copy_conflict"] = True
    return runs


def _e3(run: dict) -> dict:
    starts = [r.get("ts_start") for r in run["stage_log"] if r.get("ts_start") is not None]
    return {"first_stage_ts": min(starts) if starts else None, "note": "auxiliary only; never decisive"}


def _effective_value(v):
    return v.get("value") if isinstance(v, dict) else v


def _e2_post(attempt: dict, run: dict, chain: bool = False) -> dict:
    out = {}
    eff = run["effective"]
    if not isinstance(eff, dict):
        out["config"] = "unavailable"
    else:
        want = {**(attempt.get("config_before") or {}), **(attempt.get("config_delta") or {})}
        got = {k: _effective_value(eff.get(k)) for k in want}
        out["config"] = "consistent" if all(str(got[k]) == str(v) for k, v in want.items()) else "contradicted"
    logged = [r.get("stage") for r in run["stage_log"]]
    if not logged:
        out["stages"] = "unavailable"
    else:
        want = attempt.get("from_stage")
        if chain and logged[0] in STAGES and want in STAGES:
            # a chain row's run may be a full reflow (engineer-loop retry): it must START at or
            # before the stage the action reruns from, never after it
            ok = STAGES.index(logged[0]) <= STAGES.index(want)
        else:
            ok = logged[0] == want
        out["stages"] = "consistent" if ok else "contradicted"
    return out


def _decide(candidates: list, e1_key: str) -> dict:
    """Shared decision over E1 candidates (each with an 'e2' dict)."""
    if not candidates:
        return {"status": "UNKNOWN", "run": None, "unknown_reason": "no_explicit_link",
                "candidates": [], "evidence": None}
    clean = [c for c in candidates if "contradicted" not in c["e2"].values()]
    if len(clean) == 1:
        c = clean[0]
        if c.get("copy_conflict"):
            return {"status": "UNKNOWN", "run": None, "unknown_reason": "copy_conflict",
                    "candidates": candidates, "evidence": None}
        return {"status": "KNOWN", "run": c["run"], "unknown_reason": None, "candidates": candidates,
                "evidence": {"e1": c[e1_key], "e2": c["e2"]}}
    reason = "ambiguous_binding" if len(clean) > 1 else "contradicted_binding"
    return {"status": "UNKNOWN", "run": None, "unknown_reason": reason, "candidates": candidates,
            "evidence": None}


def claimants(attempt: dict) -> list:
    """Every recorded attempt (session, iter) in the project's fix logs (all copy roots,
    deduplicated) that could have produced a run named for (strategy, from_stage), plus the
    attempt itself. A strategy-named run is only attributable when this set has ONE member:
    with a run missing, the survivor is the unique candidate for every claimant."""
    key = (attempt["strategy"], attempt["from_stage"])
    out = {(attempt.get("session"), int(attempt.get("iter") or 1))}
    for proj in attempt.get("projects") or []:
        for row in _read_jsonl(Path(proj) / "reports" / "fix_log.jsonl"):
            if (row.get("strategy"), row.get("from_stage")) == key:
                out.add((row.get("fix_session_id"), int(row.get("iter") or 1)))
    return sorted([list(c) for c in out], key=lambda c: (str(c[0]), c[1]))


def chain_claimants(attempt: dict) -> int:
    """Distinct recorded rows (copies deduplicated) carrying the attempt's action_id."""
    seen = set()
    for proj in attempt.get("projects") or []:
        for row in _read_jsonl(Path(proj) / "reports" / "fix_log.jsonl"):
            if row.get("action_id") == attempt.get("action_id"):
                seen.add(json.dumps(row, sort_keys=True))
    return len(seen)


def _chain_post(attempt: dict, runs: dict) -> dict:
    """Chain log v1 (spec §4.1 E1-chain): the row names its post run, and the run names the
    action (r2g_action.json, written by run_orfs at creation). Both directions must agree and
    be unique; E2 may still contradict."""
    aid, tag = attempt.get("action_id"), attempt.get("post_run_tag")
    naming = sorted(t for t, r in runs.items() if r.get("action") == aid) if aid else []
    base = {"status": "UNKNOWN", "run": None, "candidates": [{"run": t} for t in naming], "evidence": None}
    if not tag:
        return {**base, "unknown_reason": "ambiguous_binding" if len(naming) > 1 else "no_explicit_link"}
    if tag not in runs:
        return {**base, "unknown_reason": "run_missing_on_disk"}
    run = runs[tag]
    if run.get("action") != aid:
        return {**base, "unknown_reason": "contradicted_binding"}
    if naming != [tag] or chain_claimants(attempt) != 1:
        return {**base, "unknown_reason": "ambiguous_binding"}
    cand = {"run": tag, "e1": {"chain_action_id": aid, "row_post_run_tag": tag, "run_action_record": aid,
                               "paths": run["paths"]},
            "e2": _e2_post(attempt, run, chain=True), "e3": _e3(run), "copy_conflict": run["copy_conflict"]}
    out = _decide([cand], "e1")
    out["claimants"] = [[attempt.get("session"), int(attempt.get("iter") or 1)]]
    return out


def bind_post(attempt: dict, runs: dict) -> dict:
    """The run the attempt's action produced (spec §4.1 E1 post, §4.2). Uniqueness must
    hold both ways: one strategy-named run for the attempt, and one attempt that can claim
    it (claimants). A chain-v1 row binds only through its explicit links."""
    if attempt.get("chain_version"):
        return _chain_post(attempt, runs)
    claim = claimants(attempt)
    out = _post_candidates(attempt, runs)
    out["claimants"] = claim
    if out["status"] == "KNOWN" and len(claim) > 1:
        out.update(status="UNKNOWN", run=None, unknown_reason="ambiguous_binding", evidence=None)
    return out


def _post_candidates(attempt: dict, runs: dict) -> dict:
    cands = []
    for tag, run in sorted(runs.items()):
        meta = run["resume_meta"] or {}
        m = _REASON.search(str(meta.get("reason") or ""))
        if not m or m.group(1) != attempt["strategy"] or m.group(2) != attempt["from_stage"]:
            continue
        if meta.get("from_stage") != attempt["from_stage"]:
            continue
        cands.append({"run": tag, "e1": {"resume_meta_reason": meta.get("reason"),
                                         "from_stage": meta.get("from_stage"), "paths": run["paths"]},
                      "e2": _e2_post(attempt, run), "e3": _e3(run),
                      "copy_conflict": run["copy_conflict"]})
    return _decide(cands, "e1")


def _digest(run: dict, stage: str):
    """The artifact digest a run holds for ``stage``: produced (manifest, status 0) or reused
    with a verified recorded lineage. None when the run holds no verified artifact."""
    for row in run["manifest"]:
        if row.get("stage") == stage and row.get("status") in (0, "0") and row.get("sha256"):
            return row["sha256"]
    entry = ((run["resume_meta"] or {}).get("parent_lineage") or {}).get(stage) or {}
    return entry.get("sha256") if entry.get("verified") else None


def failure_recorded(run: dict, check: str, violation_class) -> bool | None:
    """Did this run record the target failure? True / False / None (not measured)."""
    if check in ("orfs_stage", "route"):
        if not run["stage_log"]:
            return None
        last = run["stage_log"][-1]
        failed = last.get("status") not in (0, "0", "pass")
        return bool(failed and (violation_class in (None, last.get("stage"))))
    if check == "drc":
        d = run["drc"]
        if not isinstance(d, dict):
            return None
        try:
            return int(str(d.get("violations"))) > 0
        except ValueError:
            return None
    if check == "lvs":
        d = run["lvs"]
        if not isinstance(d, dict) or d.get("status") in (None, "", "unknown"):
            return None
        return d.get("status") != "clean"
    if check == "timing":
        t = run["timing"]
        if not isinstance(t, dict):
            return None
        ws = t.get("finish__timing__setup__ws")
        drv = [t.get("finish__timing__drv__" + k) for k in ("max_slew", "max_cap", "max_fanout")]
        if ws is None and all(v is None for v in drv):
            return None
        return (ws is not None and ws < 0) or any((v or 0) > 0 for v in drv)
    return None


def bind_pre(attempt: dict, runs: dict, post: dict, prev=None) -> dict:
    """The run that showed the failure this attempt responded to (spec §4.1 E1 pre).

    prev = (attempt_{n-1}, post_binding_{n-1}, pre_binding_{n-1}) for iteration n > 1.
    A chain-v1 row names its pre run (pre_run_tag, the active run when the step began)."""
    if attempt.get("chain_version"):
        tag = attempt.get("pre_run_tag")
        if not tag:
            return {"status": "UNKNOWN", "run": None, "unknown_reason": "no_explicit_link",
                    "candidates": [], "evidence": None}
        if tag not in runs:
            return {"status": "UNKNOWN", "run": None, "unknown_reason": "run_missing_on_disk",
                    "candidates": [{"run": tag}], "evidence": None}
        run = runs[tag]
        e2 = {"failure": {True: "consistent", False: "contradicted", None: "unavailable"}[
            failure_recorded(run, attempt["check"], attempt.get("violation_class"))]}
        return _decide([{"run": tag, "e1": {"row_pre_run_tag": tag, "chain_action_id": attempt.get("action_id"),
                                            "paths": run["paths"]},
                         "e2": e2, "e3": _e3(run), "copy_conflict": run["copy_conflict"]}], "e1")
    if int(attempt.get("iter") or 1) > 1:
        if prev is None:
            return {"status": "UNKNOWN", "run": None, "unknown_reason": "predecessor_unbound",
                    "candidates": [], "evidence": None}
        p_att, p_post, p_pre = prev
        rb = p_att.get("rolled_back")
        if rb is None:                       # D2: a legacy row without a marker is UNKNOWN, never "kept"
            return {"status": "UNKNOWN", "run": None, "unknown_reason": "predecessor_rollback_unknown",
                    "candidates": [{"run": x["run"], "via": v} for x, v in ((p_post, "post"), (p_pre, "pre"))
                                   if x.get("status") == "KNOWN"], "evidence": None}
        src = p_pre if rb else p_post
        if src["status"] != "KNOWN":
            return {"status": "UNKNOWN", "run": None, "unknown_reason": "predecessor_unbound",
                    "candidates": [], "evidence": None}
        run = runs[src["run"]]
        e2 = {"failure": {True: "consistent", False: "contradicted", None: "unavailable"}[
            failure_recorded(run, attempt["check"], attempt.get("violation_class"))]}
        anchor = _anchor(attempt, runs, post)
        if anchor is not None:
            e2["digest"] = "consistent" if _digest(run, anchor[0]) == anchor[1] else "contradicted"
        return _decide([{"run": src["run"], "e1": {"predecessor": {
            "iter": p_att.get("iter"), "rolled_back": bool(p_att.get("rolled_back")),
            "via": "pre" if p_att.get("rolled_back") else "post"}, "paths": run["paths"]}, "e2": e2, "e3": _e3(run),
            "copy_conflict": run["copy_conflict"]}], "e1")
    anchor = _anchor(attempt, runs, post)
    if anchor is None:
        return {"status": "UNKNOWN", "run": None, "unknown_reason": "anchor_unbound",
                "candidates": [], "evidence": None}
    stage, sha = anchor
    cands = []
    for tag, run in sorted(runs.items()):
        if tag == post["run"] or _digest(run, stage) != sha:
            continue
        if failure_recorded(run, attempt["check"], attempt.get("violation_class")) is not True:
            continue
        cands.append({"run": tag, "e1": {"anchor_stage": stage, "anchor_sha256": sha,
                                         "target_failure_recorded": True, "paths": run["paths"]},
                      "e2": {}, "e3": _e3(run), "copy_conflict": run["copy_conflict"]})
    return _decide(cands, "e1")


def _anchor(attempt: dict, runs: dict, post: dict):
    """(stage before from_stage, digest the post run reused) or None when unavailable."""
    if post.get("status") != "KNOWN":
        return None
    i = STAGES.index(attempt["from_stage"]) if attempt.get("from_stage") in STAGES else 0
    if i == 0:
        return None
    stage = STAGES[i - 1]
    entry = ((runs[post["run"]]["resume_meta"] or {}).get("parent_lineage") or {}).get(stage) or {}
    return (stage, entry["sha256"]) if entry.get("verified") and entry.get("sha256") else None


def ordinal_advisory(attempts: list, runs: dict) -> dict:
    """Order-based pairing of attempts with strategy-named runs, for MANUAL review only.
    Equal counts are not attribution evidence (a missing run plus a duplicate keeps them
    equal), so nothing here is a binding, positive evidence, coverage or rule admission."""
    out = {"advisory_only": True, "counted": False, "pairs": []}
    if not attempts:
        return out
    key = (attempts[0]["strategy"], attempts[0]["from_stage"])
    named = sorted((_e3(r)["first_stage_ts"] or 0, tag) for tag, r in runs.items()
                   if (m := _REASON.search(str((r["resume_meta"] or {}).get("reason") or "")))
                   and (m.group(1), m.group(2)) == key)
    if len(named) != len(attempts):
        return out
    out["pairs"] = [{"session": a.get("session"), "iter": a.get("iter"), "run": tag, "status": "ADVISORY"}
                    for a, (_, tag) in zip(attempts, named)]
    return out
