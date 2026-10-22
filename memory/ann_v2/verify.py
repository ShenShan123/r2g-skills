#!/usr/bin/env python3
"""ann-v2 independent verifier: the release condition for an annotation (user decision,
2026-10-17: automated verification replaces the manual sign-off; a reviewer can still read
the raw files, but no human verdict creates evidence or turns UNKNOWN into KNOWN).

It imports nothing from the annotator. With its own readers it re-derives, per record:

  provenance   every KNOWN field names hashed sources that exist, hash-match, and resolve
               (by locator) to the recorded value; UNKNOWN never holds a value
  binding      a second implementation of spec §4 (E1 ownership, two-way uniqueness, E2
               contradiction, copy conflict, predecessor chain with raw rollback evidence)
               must give the same run for every KNOWN binding
  targets      type and defining fields re-measured from the bound pre-action run
  effects      step effect and new failures re-measured from both bound runs
  rollback     KNOWN only from raw evidence (row marker; regression file bound by iter,
               strategy and pre run tag); a raw rollback the record missed is critical
  positive     the §5 gate recomputed from the re-derived parts
  identity     family table lookup and the secworks source-group rule
  advisory     D5/D6 fields are never counted and never appear as binding evidence
  inputs       SOURCE hash, record/unit correspondence, one annotator version

Any CRITICAL finding fails the gate (exit 1). Under-binding (the record is UNKNOWN where
this verifier finds a unique link) is reported as information: conservative, never wrong.

  python3 -m ann_v2.verify --annotation <jsonl> --source <SOURCE dir> --out <dir>
                           [--expect-source-sha <hex>]
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from pathlib import Path

UNKNOWN_REASONS = ("no_explicit_link", "ambiguous_binding", "contradicted_binding", "predecessor_unbound",
                   "predecessor_rollback_unknown", "anchor_unbound", "run_missing_on_disk",
                   "row_missing_on_disk", "export_only", "metric_absent", "conflicting_sources",
                   "copy_conflict", "binary_flag_only", "not_measured", "not_recorded")
STAGES = ("synth", "floorplan", "place", "cts", "route", "finish")
CHECKS = ("stage", "route_residual", "drc", "lvs", "setup", "hold", "drv")
REASON = re.compile(r"\bstrategy=(\S+) rerun_from=(\S+)")
ERRCODE = re.compile(r"\[ERROR\s+([A-Z]{2,5}-\d{3,4})\]")
TIMEOUT_RCS = (124, 137)
CRITICAL = ("provenance", "unknown_as_known", "wrong_binding", "copy_conflict", "target_mismatch",
            "effect_mismatch", "new_failure_mismatch", "missed_rollback", "miscounted_positive",
            "identity", "advisory_counted", "input_integrity", "shape", "admission_mismatch")
INFO = ("under_bound", "under_typed")


# ---------------------------------------------------------------- raw readers (own code)

def sha256(path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def jload(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def jlines(path) -> list:
    try:
        return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def read_run(d: Path) -> dict:
    meta = jload(d / "resume_meta.json") or {}
    m = REASON.search(str(meta.get("reason") or ""))
    eff = jload(d / "effective-config.json")
    # independent of fields.finished_here: the report counts only if this run finished
    fin = any(str(x.get("stage")) == "finish" and str(x.get("status")) in ("0", "pass")
              for x in jlines(d / "stage_log.jsonl"))
    rep = jload(d / "logs" / "6_report.json") if fin else None
    unit = None
    for f in sorted((d / "logs").glob("*.json")) if (d / "logs").is_dir() else []:
        x = jload(f)
        if isinstance(x, dict) and x.get("run__flow__platform__time_units"):
            unit = x["run__flow__platform__time_units"]
            break
    try:
        tail = (d / "flow.log").read_text(errors="ignore").splitlines()[-500:]
    except OSError:
        tail = []
    code = next((mm.group(1) for ln in tail for mm in [ERRCODE.search(ln)] if mm), None)
    rpt = d / "reports_orfs" / "5_route_drc.rpt"
    route_count = None
    if rpt.exists():
        text = rpt.read_text(errors="ignore")
        route_count = len(re.findall(r"violation type", text, re.IGNORECASE)) if text.strip() else 0
        if text.strip() and route_count == 0:
            route_count = sum(1 for ln in text.splitlines() if ln.strip())
    key_files = ("resume_meta.json", "stage_log.jsonl", "stage_artifact_manifest.jsonl", "effective-config.json",
                 "drc/drc_result.json", "lvs/netgen_lvs_result.json", "logs/6_report.json")
    content = hashlib.sha256(b"".join((sha256(d / k) or "-").encode() for k in key_files)).hexdigest()
    return {"tag": d.name, "paths": [str(d)], "content": content, "copy_conflict": False,
            "stage_log": jlines(d / "stage_log.jsonl"), "manifest": jlines(d / "stage_artifact_manifest.jsonl"),
            "resume_from": meta.get("from_stage"), "reason_key": (m.group(1), m.group(2)) if m else None,
            "parent_lineage": meta.get("parent_lineage") or {},
            "effective": eff.get("effective") if isinstance(eff, dict) and eff.get("status") == "PASS" else None,
            "drc": jload(d / "drc" / "drc_result.json"), "lvs": jload(d / "lvs" / "netgen_lvs_result.json"),
            "timing": rep if isinstance(rep, dict) else None, "time_unit": unit, "error_code": code,
            "route_count": route_count, "action": (jload(d / "r2g_action.json") or {}).get("action_id")}


class Evidence:
    """Runs and on-disk rows per project set, read once."""

    def __init__(self):
        self._runs, self._rows = {}, {}

    def runs(self, projects) -> dict:
        key = tuple(projects)
        if key not in self._runs:
            out = {}
            for p in projects:
                for d in sorted(Path(p).glob("backend/RUN_*")):
                    if not d.is_dir():
                        continue
                    r = read_run(d)
                    if d.name in out:
                        out[d.name]["paths"].append(str(d))
                        if out[d.name]["content"] != r["content"]:
                            out[d.name]["copy_conflict"] = True
                    else:
                        out[d.name] = r
            self._runs[key] = out
        return self._runs[key]

    def rows(self, projects) -> list:
        key = tuple(projects)
        if key not in self._rows:
            out = []
            for p in projects:
                for i, r in enumerate(jlines(Path(p) / "reports" / "fix_log.jsonl"), 1):
                    out.append((r, str(Path(p) / "reports" / "fix_log.jsonl"), i))
            self._rows[key] = out
        return self._rows[key]


# ---------------------------------------------------------------- measurements (own code)

GRT_TOTAL = re.compile(r"^Total\s+\d+\s+\d+\s+[\d.]+%\s+\d+\s*/\s*\d+\s*/\s*(\d+)\s*$")
GRT_ERROR = re.compile(r"\[ERROR GRT-\d+\]")


def global_route(run: dict) -> dict:
    p = Path(run["paths"][0]) / "logs" / "5_1_grt.log"
    try:
        lines = p.read_text(errors="ignore").splitlines()
    except OSError:
        return {"overflow": None, "clean": None}
    tot = [int(m.group(1)) for ln in lines for m in [GRT_TOTAL.match(ln.strip())] if m]
    if any(GRT_ERROR.search(ln) for ln in lines):
        clean = False
    else:
        clean = (tot[-1] == 0) if tot else None
    return {"overflow": tot[-1] if tot else None, "clean": clean}


def stage_state(run: dict) -> dict:
    rows = run["stage_log"]
    if not rows:
        return {"measured": False}
    last = rows[-1]
    failed = last.get("status") not in (0, "0", "pass")
    try:
        timeout = int(last.get("status")) in TIMEOUT_RCS
    except (TypeError, ValueError):
        timeout = None
    gr = global_route(run) if failed and last.get("stage") == "route" else {"overflow": None, "clean": None}
    return {"measured": True, "present": failed, "stage": last.get("stage"), "exit": last.get("status"),
            "timeout": timeout, "error_code": run["error_code"] if failed else None,
            "grt_overflow": gr["overflow"], "global_route_clean": gr["clean"],
            "passed": [r.get("stage") for r in rows if r.get("status") in (0, "0", "pass")]}


def drc_state(run: dict) -> dict:
    d = run["drc"]
    try:
        n = int(str(d.get("violations"))) if isinstance(d, dict) else None
    except ValueError:
        n = None
    if n is None:
        return {"measured": False}
    return {"measured": True, "present": n > 0, "count": n, "checker": d.get("checker") or None}


def route_state(run: dict) -> dict:
    routed = any(r.get("stage") == "route" and r.get("status") in (0, "0") for r in run["stage_log"])
    if not routed or run["route_count"] is None:
        return {"measured": False}
    return {"measured": True, "present": run["route_count"] > 0, "count": run["route_count"]}


def lvs_state(run: dict) -> dict:
    d = run["lvs"]
    st = d.get("status") if isinstance(d, dict) else None
    if st in (None, "", "unknown", "error"):
        return {"measured": False}
    return {"measured": True, "present": st != "clean", "status": st, "checker": d.get("tool") or None,
            "mismatch_class": d.get("mismatch_class") or None}


def slack_state(run: dict, kind: str) -> dict:
    t = run["timing"] or {}
    ws = t.get(f"finish__timing__{kind}__ws")
    if not isinstance(ws, (int, float)):
        return {"measured": False}
    if abs(ws) >= 1e30:                      # no constrained path: never a measured "met"
        return {"measured": False, "unconstrained": True}
    return {"measured": True, "present": ws < 0, "value": ws, "unit": run["time_unit"],
            "tns": t.get(f"finish__timing__{kind}__tns"),
            "violators": t.get(f"finish__timing__drv__{kind}_violation_count")}


def timing_unconstrained(run: dict) -> bool:
    return any(slack_state(run, k).get("unconstrained") for k in ("setup", "hold"))


def drv_state(run: dict) -> dict:
    t = run["timing"] or {}
    subs = {k: t.get(f"finish__timing__drv__{k}") for k in ("max_slew", "max_cap", "max_fanout")}
    subs = {k: (v if isinstance(v, (int, float)) else None) for k, v in subs.items()}
    if all(v is None for v in subs.values()):
        return {"measured": False}
    return {"measured": True, "present": any((v or 0) > 0 for v in subs.values()), **subs}


def measure(run: dict) -> dict:
    return {"stage": stage_state(run), "route_residual": route_state(run), "drc": drc_state(run),
            "lvs": lvs_state(run), "setup": slack_state(run, "setup"), "hold": slack_state(run, "hold"),
            "drv": drv_state(run)}


def failure_recorded(run: dict, check: str, vclass):
    if check in ("orfs_stage", "route"):
        s = stage_state(run)
        return None if not s["measured"] else bool(s["present"] and vclass in (None, s["stage"]))
    if check == "drc":
        s = drc_state(run)
        return s["present"] if s["measured"] else None
    if check == "lvs":
        s = lvs_state(run)
        return s["present"] if s["measured"] else None
    if check == "timing":
        a, b = slack_state(run, "setup"), drv_state(run)
        if not a["measured"] and not b["measured"]:
            return None
        return bool(a.get("present")) or bool(b.get("present"))
    return None


def digest(run: dict, stage: str):
    for row in run["manifest"]:
        if row.get("stage") == stage and row.get("status") in (0, "0") and row.get("sha256"):
            return row["sha256"]
    e = run["parent_lineage"].get(stage) or {}
    return e.get("sha256") if e.get("verified") else None


# ---------------------------------------------------------------- second binder (own code)

def delta_after(delta) -> dict:
    if isinstance(delta, str):
        try:
            delta = json.loads(delta)
        except ValueError:
            return {}
    return {k: (v.get("after") if isinstance(v, dict) else v) for k, v in (delta or {}).items()}


def contradicted(row: dict, run: dict, chain: bool = False) -> bool:
    if isinstance(run["effective"], dict):
        want = {**(row.get("config_before") or {}), **delta_after(row.get("config_delta"))}
        for k, v in want.items():
            got = run["effective"].get(k)
            got = got.get("value") if isinstance(got, dict) else got
            if str(got) != str(v):
                return True
    logged = [r.get("stage") for r in run["stage_log"]]
    if not logged:
        return False
    if chain and logged[0] in STAGES and row.get("from_stage") in STAGES:
        return STAGES.index(logged[0]) > STAGES.index(row["from_stage"])
    return logged[0] != row.get("from_stage")


def chain_bind(row: dict, ev: "Evidence", projects) -> tuple:
    """(post, pre) for a chain-v1 row: explicit links only, checked both ways."""
    runs = ev.runs(projects)
    aid, post_tag, pre_tag = row.get("action_id"), row.get("post_run_tag"), row.get("pre_run_tag")
    rows_with_id = {json.dumps(r, sort_keys=True) for r, _, _ in ev.rows(projects) if r.get("action_id") == aid}
    naming = sorted(t for t, r in runs.items() if aid and r.get("action") == aid)
    post = None
    if post_tag and post_tag in runs and runs[post_tag].get("action") == aid and naming == [post_tag] \
            and len(rows_with_id) == 1 and not runs[post_tag]["copy_conflict"] \
            and not contradicted(row, runs[post_tag], chain=True):
        post = post_tag
    pre = None
    if pre_tag and pre_tag in runs and not runs[pre_tag]["copy_conflict"] \
            and failure_recorded(runs[pre_tag], row.get("check"), row.get("violation_class")) is not False:
        pre = pre_tag
    return post, pre


def bind_post(row: dict, ev: Evidence, projects) -> str | None:
    key = (row.get("strategy"), row.get("from_stage"))
    runs = ev.runs(projects)
    cands = [t for t, r in runs.items() if r["reason_key"] == key and r["resume_from"] == row.get("from_stage")]
    clean = [t for t in cands if not contradicted(row, runs[t])]
    claim = {(row.get("fix_session_id"), int(row.get("iter") or 1))}
    for r, _, _ in ev.rows(projects):
        if (r.get("strategy"), r.get("from_stage")) == key:
            claim.add((r.get("fix_session_id"), int(r.get("iter") or 1)))
    if len(clean) != 1 or len(claim) != 1 or runs[clean[0]]["copy_conflict"]:
        return None
    return clean[0]


def anchor(row: dict, runs: dict, post: str | None):
    if post is None or row.get("from_stage") not in STAGES or STAGES.index(row["from_stage"]) == 0:
        return None
    stage = STAGES[STAGES.index(row["from_stage"]) - 1]
    e = runs[post]["parent_lineage"].get(stage) or {}
    return (stage, e["sha256"]) if e.get("verified") and e.get("sha256") else None


SIGNAL = {"drc": "drc", "lvs": "lvs", "timing": "timing", "orfs_stage": "orfs", "route": "route"}


def _iso(t):
    import datetime
    try:
        return datetime.datetime.fromisoformat(str(t)).timestamp()
    except (TypeError, ValueError):
        return None


def owned_snapshot(row: dict, projects, ev: "Evidence"):
    """The regression snapshot of (iter) if THIS row uniquely owns it, else None."""
    it = int(row.get("iter") or 1)
    files = [Path(p) / "reports" / f"global_regression_it{it}.json" for p in projects]
    files = [f for f in files if f.exists()]
    if not files or len({sha256(f) for f in files}) != 1:
        return None
    snap = jload(files[0])
    if not isinstance(snap, dict) or snap.get("config_restored") is not True \
            or snap.get("strategy") != row.get("strategy") or int(snap.get("iter") or 0) != it:
        return None
    want = sorted(snap.get("regressions") or []) if isinstance(snap.get("regressions"), list) else []
    owners = {json.dumps(r, sort_keys=True) for r, _, _ in ev.rows(projects)
              if r.get("verdict") == "regression" and int(r.get("iter") or 1) == it
              and r.get("strategy") == snap.get("strategy")
              and (sorted(r["global_regressions"]) if isinstance(r.get("global_regressions"), list) else []) == want}
    return snap if owners == {json.dumps(row, sort_keys=True)} else None


def snapshot_pre_run(row: dict, projects, ev: "Evidence", post):
    snap = owned_snapshot(row, projects, ev)
    if snap is None:
        return None
    runs = ev.runs(projects)
    pre = snap.get("pre") or {}
    lay = pre.get("layout") or {}
    tag = lay.get("run_tag")
    if not tag or tag not in runs or runs[tag]["copy_conflict"]:
        return None
    if ((pre.get("signals") or {}).get(SIGNAL.get(row.get("check"), "")) or {}).get("fresh") is not True:
        return None
    d = Path(runs[tag]["paths"][0])
    dsha = next((sha256(d / rel) for rel in ("results/6_final.def", "final/6_final.def") if (d / rel).exists()), None)
    if not lay.get("def_digest") or dsha != lay.get("def_digest"):
        return None
    if failure_recorded(runs[tag], row.get("check"), row.get("violation_class")) is not True:
        return None
    if post is not None and ((snap.get("post") or {}).get("layout") or {}).get("run_tag") != post:
        return None
    a, b = _iso(pre.get("captured_at")), _iso(row.get("ts"))
    if a is not None and b is not None and a > b:
        return None
    return tag


def rollback_state(row: dict, projects, pre: str | None, ev: "Evidence" = None):
    if isinstance(row.get("rolled_back"), bool):
        return row["rolled_back"]
    if ev is not None and owned_snapshot(row, projects, ev) is not None:
        return True
    return None


class Binder2:
    def __init__(self, ev: Evidence):
        self.ev, self.memo = ev, {}

    def session_row(self, projects, session, it):
        """The unique attempt row of (session, it), copies deduplicated; None otherwise."""
        seen = {}
        for r, _, _ in self.ev.rows(projects):
            if r.get("fix_session_id") == session and int(r.get("iter") or 1) == it \
                    and r.get("strategy") not in (None, "none") and not str(r.get("verdict") or "").startswith("stop"):
                seen[json.dumps(r, sort_keys=True)] = r
        return next(iter(seen.values())) if len(seen) == 1 else None

    def bind(self, row: dict, projects) -> tuple:
        """(post, pre, rolled_back) for the attempt `row` (on-disk row)."""
        key = (tuple(projects), row.get("fix_session_id"), int(row.get("iter") or 1), row.get("strategy"),
               row.get("action_id"))
        if key in self.memo:
            return self.memo[key]
        runs = self.ev.runs(projects)
        if row.get("chain_version"):
            post, pre = chain_bind(row, self.ev, projects)
            snap = snapshot_pre_run(row, projects, self.ev, post)
            if pre is not None and snap is not None and snap != pre:
                pre = None                                     # cross-check only, never fills
            rb = rollback_state(row, projects, pre, self.ev)
            self.memo[key] = (post, pre, rb)
            return self.memo[key]
        post = bind_post(row, self.ev, projects)
        it = int(row.get("iter") or 1)
        pre, state, cands = None, "open", []       # state: open | known | blocked | ambiguous
        if it > 1:
            prow = self.session_row(projects, row.get("fix_session_id"), it - 1)
            if prow is not None and prow.get("strategy") not in (None, "none"):
                p_post, p_pre, p_rb = self.bind(prow, projects)
                if p_rb is not None:
                    src = p_pre if p_rb else p_post
                    if src is not None:
                        a = anchor(row, runs, post)
                        bad = failure_recorded(runs[src], row.get("check"), row.get("violation_class")) is False \
                            or (a is not None and digest(runs[src], a[0]) != a[1])
                        if runs[src]["copy_conflict"] or bad:
                            state = "blocked"
                        else:
                            pre, state = src, "known"
        else:
            a = anchor(row, runs, post)
            if a is not None:
                cands = [t for t, r in runs.items() if t != post and digest(r, a[0]) == a[1]
                         and failure_recorded(r, row.get("check"), row.get("violation_class")) is True]
                if len(cands) == 1:
                    if runs[cands[0]]["copy_conflict"]:
                        state = "blocked"
                    else:
                        pre, state = cands[0], "known"
                elif len(cands) > 1:
                    state = "ambiguous"
        snap = snapshot_pre_run(row, projects, self.ev, post)
        if snap is not None:
            if state == "known":
                pre = pre if snap == pre else None
            elif state == "ambiguous":
                pre = snap if snap in cands else None
            elif state == "open":
                pre = snap
        rb = rollback_state(row, projects, pre, self.ev)
        self.memo[key] = (post, pre, rb)
        return self.memo[key]


# ---------------------------------------------------------------- typed targets and effects (own code)

def my_targets(row: dict, pre_m: dict) -> list:
    """[(type, {field: value})] from the pre-action measurement (spec §3 typing rules)."""
    check, vclass = row.get("check"), row.get("violation_class")
    if check == "orfs_stage":
        s = pre_m["stage"]
        if s["measured"] and s["present"] and s["stage"] == vclass:
            return [("stage_abort", {"stage": s["stage"], "exit": s["exit"], "error_code": s["error_code"],
                                     "timeout": s["timeout"], "grt_overflow": s["grt_overflow"]})]
        if vclass == "route" and pre_m["route_residual"].get("present"):
            return [("route_residual", {"count": pre_m["route_residual"]["count"]})]
        return [("UNKNOWN", {})]
    if check == "drc":
        d = pre_m["drc"]
        return [("drc", {"count": d["count"], "checker": d["checker"]})] if d.get("present") else [("UNKNOWN", {})]
    if check == "lvs":
        l = pre_m["lvs"]
        return [("lvs", {"status": l["status"], "checker": l["checker"], "mismatch_class": l["mismatch_class"]})] \
            if l.get("present") else [("UNKNOWN", {})]
    if check == "timing":
        out = []
        if pre_m["setup"].get("present"):
            s = pre_m["setup"]
            out.append(("setup", {"slack": s["value"], "unit": s["unit"], "tns": s["tns"],
                                  "violators": s["violators"], "measurement_stage": "finish"}))
        if pre_m["drv"].get("present"):
            d = pre_m["drv"]
            out.append(("drv", {"max_slew": d["max_slew"], "max_cap": d["max_cap"], "max_fanout": d["max_fanout"],
                                "measurement_stage": "finish"}))
        return out or [("UNKNOWN", {})]
    return [("UNKNOWN", {})]


def compare(before, after) -> str:
    if before is None or after is None:
        return "UNKNOWN"
    if after == 0:
        return "CLEARED"
    return "IMPROVED" if after < before else "NO_CHANGE" if after == before else "REGRESSED"


def my_effect(kind: str, fields: dict, pre_m: dict, post_m: dict) -> str:
    if kind == "stage_abort":
        s = post_m["stage"]
        if not s["measured"]:
            return "UNKNOWN"
        if fields["stage"] in s["passed"]:
            return "CLEARED"
        if s["present"]:
            if s["stage"] == fields["stage"] == "route":
                c0, c1 = fields.get("error_code"), s["error_code"]
                if c0 is not None and c0 == c1:
                    o0, o1 = pre_m["stage"].get("grt_overflow"), s.get("grt_overflow")
                    if o0 is None or o1 is None or o0 == o1:
                        return "NO_CHANGE"
                    return "IMPROVED" if o1 < o0 else "REGRESSED"
                if str(c0 or "").startswith("GRT-") and s.get("global_route_clean") is True:
                    return "CLEARED"
                return "UNKNOWN" if c0 != c1 else "NO_CHANGE"
            if s["stage"] == fields["stage"]:
                return "NO_CHANGE"
            if s["stage"] in STAGES and fields["stage"] in STAGES and \
                    STAGES.index(s["stage"]) < STAGES.index(fields["stage"]):
                return "REGRESSED"
        return "UNKNOWN"
    if kind in ("drc", "route_residual"):
        if not post_m[kind]["measured"]:
            return "UNKNOWN"
        return compare(pre_m[kind].get("count"), post_m[kind].get("count"))
    if kind == "lvs":
        if not post_m["lvs"]["measured"]:
            return "UNKNOWN"
        return "CLEARED" if post_m["lvs"]["present"] is False else "NO_CHANGE"
    if kind in ("setup", "hold"):
        a, b = post_m[kind], pre_m[kind]
        if not a["measured"]:
            return "UNKNOWN"
        if b.get("unit") is None or b.get("unit") != a.get("unit") or b.get("value") is None:
            return "UNKNOWN"
        if a["value"] >= 0:
            return "CLEARED"
        return "IMPROVED" if a["value"] > b["value"] else "NO_CHANGE" if a["value"] == b["value"] else "REGRESSED"
    if kind == "drv":
        a, b = post_m["drv"], pre_m["drv"]
        if not a["measured"]:
            return "UNKNOWN"
        subs = ("max_slew", "max_cap", "max_fanout")
        if any((b.get(s) is None) != (a.get(s) is None) for s in subs) or all(a.get(s) is None for s in subs):
            return "UNKNOWN"
        return compare(sum(b[s] for s in subs if b.get(s) is not None), sum(a[s] for s in subs if a.get(s) is not None))
    return "UNKNOWN"


def my_new_failures(pre_m: dict, post_m: dict) -> dict:
    out = {}
    for c in CHECKS:
        b, a = pre_m[c], post_m[c]
        if not b["measured"]:
            out[c] = "NOT_MEASURED_BEFORE"
        elif not a["measured"]:
            out[c] = "UNKNOWN"
        elif c == "stage":
            same = b["present"] and b["stage"] == a["stage"] and b.get("error_code") == a.get("error_code")
            out[c] = "KNOWN-present" if a["present"] and not same else "KNOWN-none"
        else:
            out[c] = "KNOWN-present" if a["present"] and not b["present"] else "KNOWN-none"
    return out


# ---------------------------------------------------------------- locator resolution

def _dominant(cats):
    if isinstance(cats, str):
        try:
            cats = json.loads(cats)
        except ValueError:
            return None
    if not isinstance(cats, dict) or not cats:
        return None
    counts = {k: (v.get("count") if isinstance(v, dict) else v) for k, v in cats.items()}
    if any(not isinstance(c, (int, float)) for c in counts.values()):
        return None
    return max(counts, key=lambda k: (counts[k], k))


def resolve(path: Path, locator: str, field: str, families: dict | None, design: str | None):
    """(ok, value_at_locator) with derived-field semantics applied; ok False = unresolvable."""
    name = path.name
    if name == "5_1_grt.log" and (m := re.match(r"line (\d+): (Total congestion|GRT error)$", locator)):
        try:
            ln = path.read_text(errors="ignore").splitlines()[int(m.group(1)) - 1]
        except (OSError, IndexError):
            return False, None
        if m.group(2) == "GRT error":
            return (True, False) if GRT_ERROR.search(ln) else (False, None)
        mm = GRT_TOTAL.match(ln.strip())
        if not mm:
            return False, None
        return True, (int(mm.group(1)) == 0 if field == "global_route_clean" else int(mm.group(1)))
    m = re.match(r"(line|item) (\d+): (.+)$", locator)
    if m:
        n, key_path = int(m.group(2)), m.group(3)
        if m.group(1) == "line":
            rows = jlines(path)
            row = rows[n - 1] if 0 < n <= len(rows) else None
        else:
            data = jload(path)
            row = data[n - 1] if isinstance(data, list) and 0 < n <= len(data) else None
        if not isinstance(row, dict):
            return False, None
        cur = row
        for k in key_path.split("."):
            if not isinstance(cur, dict) or k not in cur:
                return False, None
            cur = cur[k]
        if name == "stage_log.jsonl" and key_path == "status" and field == "timeout":
            try:
                return True, int(cur) in TIMEOUT_RCS
            except (TypeError, ValueError):
                return False, None
        if key_path == "before_categories" and field == "rule_class":
            return True, _dominant(cur)
        return True, cur
    if m := re.match(r"tail-500 line (\d+)$", locator):
        try:
            tail = path.read_text(errors="ignore").splitlines()[-500:]
        except OSError:
            return False, None
        i = int(m.group(1))
        first = next((j for j, ln in enumerate(tail, 1) if ERRCODE.search(ln)), None)
        if first != i:
            return False, None
        return True, ERRCODE.search(tail[i - 1]).group(1)
    if locator == "violation type markers":
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            return False, None
        n = len(re.findall(r"violation type", text, re.IGNORECASE)) if text.strip() else 0
        if text.strip() and n == 0:
            n = sum(1 for ln in text.splitlines() if ln.strip())
        return True, n
    if locator == "split.family_of_design":
        if families is None or design is None or design not in families:
            return False, None
        fam = families[design]
        return True, {"design_id": design, "ip_family": fam,
                      "source_group": "secworks" if str(fam).startswith("secworks/") else fam}.get(field)
    if locator.startswith("config_restored"):
        d = jload(path)
        return (True, True) if isinstance(d, dict) and d.get("config_restored") is True else (False, None)
    d = jload(path)
    if not isinstance(d, dict):
        return False, None
    if locator == "finish__ prefix":
        return (True, "finish") if any(str(k).startswith("finish__") for k in d) else (False, None)
    if locator in d:
        v = d[locator]
        if field == "metric":
            return True, locator.split("__")[2] + "_ws" if locator.startswith("finish__timing__") else None
        if field in ("count",) and name == "drc_result.json":
            try:
                return True, int(str(v))
            except ValueError:
                return False, None
        return True, v
    return False, None


def same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------- record verification

class Report:
    def __init__(self):
        self.findings, self.counts = [], collections.Counter()

    def add(self, unit_id, kind, detail, severity=None):
        sev = severity or ("CRITICAL" if kind in CRITICAL else "INFO" if kind in INFO else "NON_CRITICAL")
        self.findings.append({"unit_id": unit_id, "kind": kind, "severity": sev, "detail": detail})
        self.counts[(sev, kind)] += 1


def _fields_of(rec: dict):
    for k, f in (rec.get("identity") or {}).items():
        yield f"identity.{k}", k, f
    for i, t in enumerate(rec.get("targets") or []):
        for k, f in (t.get("fields") or {}).items():
            yield f"targets[{i}].{k}", k, f
    if rec.get("rolled_back"):
        yield "rolled_back", "rolled_back", rec["rolled_back"]


def verify_fields(rec: dict, unit: dict, families: dict, rep: Report) -> None:
    uid, design = rec["unit_id"], (unit.get("identity") or {}).get("design")
    for where, field, f in _fields_of(rec):
        st = f.get("status")
        if st not in ("KNOWN", "UNKNOWN", "NOT_APPLICABLE"):
            rep.add(uid, "shape", f"{where}: status {st!r}")
            continue
        if st == "UNKNOWN":
            if f.get("value") is not None:
                rep.add(uid, "shape", f"{where}: UNKNOWN holds a value")
            if f.get("unknown_reason") not in UNKNOWN_REASONS:
                rep.add(uid, "shape", f"{where}: unknown_reason {f.get('unknown_reason')!r} not in the closed list")
            continue
        if st == "NOT_APPLICABLE":
            if f.get("value") is not None:
                rep.add(uid, "shape", f"{where}: NOT_APPLICABLE holds a value")
            continue
        if not f.get("source"):
            rep.add(uid, "unknown_as_known", f"{where}: KNOWN without a source")
            continue
        for s in f["source"]:
            path = Path(s.get("path") or "")
            if not path.exists():
                rep.add(uid, "provenance", f"{where}: source missing {path}")
                continue
            if s.get("sha256") != sha256(path):
                rep.add(uid, "provenance", f"{where}: sha256 mismatch for {path}")
                continue
            ok, val = resolve(path, s.get("locator") or "", field, families, design)
            if not ok:
                rep.add(uid, "provenance", f"{where}: locator {s.get('locator')!r} does not resolve in {path.name}")
            elif not same(val, f.get("value")):
                rep.add(uid, "provenance", f"{where}: recorded {f.get('value')!r}, source gives {val!r}")


def _bound(rec: dict, side: str):
    b = (rec.get("binding") or {}).get(side) or {}
    return b.get("run") if b.get("status") == "KNOWN" else None


def _first_path(rec: dict, side: str):
    e1 = (((rec.get("binding") or {}).get(side) or {}).get("evidence") or {}).get("e1") or {}
    return (e1.get("paths") or [None])[0]


def verify_record(rec: dict, unit: dict, families: dict, ev: Evidence, b2: Binder2, rep: Report) -> dict:
    uid = rec["unit_id"]
    verify_fields(rec, unit, families, rep)
    if rec.get("status") != "ATTEMPT":
        if rec.get("positive"):
            rep.add(uid, "miscounted_positive", "a non-attempt is positive")
        if (rec.get("admission") or {}).get("status") != "NOT_ADMITTED":
            rep.add(uid, "admission_mismatch", "a non-attempt must not be admitted")
        return {"positive": False}
    # advisory fields are never counted and never bind
    adv = rec.get("post_run_advisory")
    if adv and (adv.get("counted") is not False or adv.get("status") != "ADVISORY"):
        rep.add(uid, "advisory_counted", "post_run_advisory counted or not ADVISORY")
    for side in ("post", "pre"):
        e1 = (((rec.get("binding") or {}).get(side) or {}).get("evidence") or {}).get("e1") or {}
        if any(k in e1 for k in ("ordinal", "ordinal_pair", "ts", "first_stage_ts", "hash_match")):
            rep.add(uid, "advisory_counted", f"{side} binding evidence uses advisory or time keys")
    # projects from the unit's provenance (own derivation)
    projects = sorted({str(Path(s["path"]).parent.parent) for s in unit["provenance"]["sources"]
                       if str(s.get("path", "")).endswith("reports/fix_log.jsonl")})
    raw = unit["raw_row"]
    row = None
    own = (raw.get("fix_session_id"), int(raw.get("iter") or 1), raw.get("strategy"), raw.get("check"))
    for r, p, i in (ev.rows(projects) if projects else []):
        if (r.get("fix_session_id"), int(r.get("iter") or 1), r.get("strategy"), r.get("check")) == own:
            row = r
            break
    rec_post, rec_pre = _bound(rec, "post"), _bound(rec, "pre")
    if row is None:
        for side, run in (("post", rec_post), ("pre", rec_pre)):
            if run is not None:
                rep.add(uid, "wrong_binding", f"{side} KNOWN but the unit's own fix-log row is not on disk")
        if rec.get("rolled_back", {}).get("status") == "KNOWN":
            rep.add(uid, "unknown_as_known", "rolled_back KNOWN without an on-disk row")
        if rec.get("positive"):
            rep.add(uid, "miscounted_positive", "positive without an on-disk row")
        if (rec.get("admission") or {}).get("status") != "NOT_ADMITTED":
            rep.add(uid, "admission_mismatch", "admitted without an on-disk row")
        return {"positive": False}
    post, pre, rb = b2.bind(row, projects)
    runs = ev.runs(projects)
    for side, mine, theirs in (("post", post, rec_post), ("pre", pre, rec_pre)):
        if theirs is not None and mine != theirs:
            rep.add(uid, "wrong_binding", f"{side}: record binds {theirs}, independent binder gives {mine}")
        elif theirs is None and mine is not None:
            rep.add(uid, "under_bound", f"{side}: record UNKNOWN, independent binder finds {mine}")
        if theirs is not None and theirs in runs and runs[theirs]["copy_conflict"]:
            rep.add(uid, "copy_conflict", f"{side}: bound run {theirs} has disagreeing copies")
        if theirs is not None:
            p = _first_path(rec, side)
            if not p or not Path(p).is_dir():
                rep.add(uid, "provenance", f"{side}: bound run path missing {p}")
    # rollback
    rrb = rec.get("rolled_back") or {}
    if rrb.get("status") == "KNOWN":
        if rb is None:
            rep.add(uid, "unknown_as_known", "rolled_back KNOWN without raw evidence (row marker / bound regression file)")
        elif rb is not rrb.get("value"):
            rep.add(uid, "missed_rollback" if rb else "unknown_as_known",
                    f"rolled_back recorded {rrb.get('value')!r}, raw evidence gives {rb!r}")
    elif rb is True:
        rep.add(uid, "missed_rollback", "raw evidence shows a rollback; record is not KNOWN true")
    # targets, effects, new failures, positive -- only on agreed KNOWN bindings
    agreed = post is not None and pre is not None and post == rec_post and pre == rec_pre
    my_pos, pos_blockers = False, []
    if agreed:
        pre_m, post_m = measure(runs[pre]), measure(runs[post])
        mine = my_targets(row, pre_m)
        theirs = [(t.get("type"), t.get("type_status"), t.get("fields") or {}) for t in rec.get("targets") or []]
        if [k for k, _ in mine] != [k for k, _, _ in theirs]:
            if all(k == "UNKNOWN" for k, _, _ in theirs) and any(k != "UNKNOWN" for k, _ in mine):
                rep.add(uid, "under_typed", f"record untyped; independent typing {[k for k, _ in mine]}")
            else:
                rep.add(uid, "target_mismatch", f"types {[k for k, _, _ in theirs]} vs {[k for k, _ in mine]}")
        else:
            for (k, mf), (_, ts, tf) in zip(mine, theirs):
                for name, v in mf.items():
                    f = tf.get(name) or {}
                    if f.get("status") == "KNOWN" and not same(f.get("value"), v):
                        rep.add(uid, "target_mismatch", f"{k}.{name}: recorded {f.get('value')!r}, measured {v!r}")
                    if f.get("status") == "KNOWN" and v is None:
                        rep.add(uid, "unknown_as_known", f"{k}.{name}: KNOWN but not measurable from the pre run")
        effects = {k: my_effect(k, mf, pre_m, post_m) if k != "UNKNOWN" else "UNKNOWN" for k, mf in mine}
        for k, e in (rec.get("step_effect") or {}).items():
            if k in effects and effects[k] != e and not (effects[k] != "UNKNOWN" and e == "UNKNOWN"):
                rep.add(uid, "effect_mismatch", f"{k}: recorded {e}, re-measured {effects[k]}")
        new = my_new_failures(pre_m, post_m)
        for c, v in (rec.get("new_failures") or {}).items():
            if new.get(c) != v:
                rep.add(uid, "new_failure_mismatch", f"{c}: recorded {v}, re-measured {new.get(c)}")
        typed_ok = all(k != "UNKNOWN" for k, _ in mine) and all(ts == "KNOWN" for _, ts, _ in theirs)
        if not typed_ok:
            pos_blockers.append("target")
        pos_blockers += [f"effect:{k}={e}" for k, e in effects.items() if e != "CLEARED"]
        pos_blockers += [f"new:{c}={v}" for c, v in new.items() if v in ("KNOWN-present", "UNKNOWN")]
        if rb is not False:
            pos_blockers.append("rollback")
        my_pos = not pos_blockers
    else:
        pos_blockers.append("binding")
    unc = agreed and (timing_unconstrained(runs[pre]) or timing_unconstrained(runs[post]))
    my_adm = "ADMITTED" if my_pos and not unc else "NOT_ADMITTED"
    if (rec.get("admission") or {}).get("status") != my_adm:
        rep.add(uid, "admission_mismatch", f"record admission {(rec.get('admission') or {}).get('status')}, "
                                            f"re-derived {my_adm} (unconstrained timing: {bool(unc)})")
    if bool(rec.get("positive")) != my_pos:
        rep.add(uid, "miscounted_positive", f"record positive={rec.get('positive')}, re-derived {my_pos} "
                                             f"(blockers {pos_blockers})")
    cc = rec.get("candidate_class")
    if cc is not None and (rec.get("positive") or cc != "effect_cleared_rollback_unknown"
                           or rrb.get("status") == "KNOWN"):
        rep.add(uid, "miscounted_positive", f"candidate_class {cc!r} on a positive or a rollback-known unit")
    if rec.get("positive"):                      # the record's own parts must support it (internal gate)
        own = (rec_post is not None and rec_pre is not None
               and bool(rec.get("targets")) and all(t.get("type_status") == "KNOWN" for t in rec["targets"])
               and all(e == "CLEARED" for e in (rec.get("step_effect") or {}).values())
               and not any(v in ("KNOWN-present", "UNKNOWN") for v in (rec.get("new_failures") or {}).values())
               and rrb.get("status") == "KNOWN" and rrb.get("value") is False)
        if not own:
            rep.add(uid, "miscounted_positive", "positive is not supported by the record's own parts")
    return {"positive": my_pos}


# ---------------------------------------------------------------- driver

def verify(annotation: Path, source_dir: Path, expect_source_sha: str | None = None) -> dict:
    rep, ev = Report(), Evidence()
    b2 = Binder2(ev)
    src_file = source_dir / "source_export.jsonl"
    src_sha = sha256(src_file)
    if expect_source_sha and src_sha != expect_source_sha:
        rep.add("-", "input_integrity", f"SOURCE sha256 {src_sha} != expected {expect_source_sha}")
    units = {}
    for l in src_file.read_text().splitlines():
        if l.strip():
            u = json.loads(l)
            units[u["unit_id"]] = u
    manifest = jload(source_dir / "source_manifest.json") or {}
    families = ((manifest.get("split") or {}).get("family_of_design")) or {}
    records = [json.loads(l) for l in annotation.read_text().splitlines() if l.strip()]
    ids = [r["unit_id"] for r in records]
    if len(set(ids)) != len(ids):
        rep.add("-", "input_integrity", "duplicate unit_id in the annotation")
    if set(ids) != set(units):
        rep.add("-", "input_integrity", f"annotation covers {len(set(ids))} units, SOURCE has {len(units)}")
    versions = collections.Counter()
    for r in records:
        for _, _, f in _fields_of(r):
            if f.get("method"):
                versions[str(f["method"]).split("@")[-1]] += 1
    if len(versions) > 1:
        rep.add("-", "input_integrity", f"several annotator versions: {dict(versions)}")
    seen_ids: dict = {}
    for u in units.values():
        for sref in u["provenance"]["sources"]:
            pth = str(sref.get("path", ""))
            if pth.endswith("reports/fix_log.jsonl") and pth not in seen_ids:
                ids = [r.get("action_id") for r in jlines(pth) if r.get("action_id")]
                seen_ids[pth] = True
                dup = sorted({i for i in ids if ids.count(i) > 1})
                if dup:
                    rep.add("-", "input_integrity", f"duplicate action_id in {pth}: {dup[:3]}")
                known = set(ids)
                for r in jlines(pth):
                    if r.get("parent_action") and r["parent_action"] not in known:
                        rep.add("-", "input_integrity", f"parent_action {r['parent_action']} not in {pth}")
    post_owner: dict = {}
    my_positive = 0
    for r in records:
        u = units.get(r["unit_id"])
        if u is None:
            continue
        out = verify_record(r, u, families, ev, b2, rep)
        my_positive += out["positive"]
        p = _first_path(r, "post")
        if p:
            if p in post_owner and post_owner[p] != r["unit_id"]:
                rep.add(r["unit_id"], "wrong_binding", f"post run {p} also bound by {post_owner[p]}")
            post_owner.setdefault(p, r["unit_id"])
    crit = [f for f in rep.findings if f["severity"] == "CRITICAL"]
    return {"gate": "FAIL" if crit else "PASS", "critical": len(crit),
            "non_critical": sum(1 for f in rep.findings if f["severity"] == "NON_CRITICAL"),
            "info": sum(1 for f in rep.findings if f["severity"] == "INFO"),
            "records": len(records), "attempts": sum(1 for r in records if r.get("status") == "ATTEMPT"),
            "recorded_positive": sum(1 for r in records if r.get("positive")), "rederived_positive": my_positive,
            "by_kind": {f"{s}:{k}": n for (s, k), n in sorted(rep.counts.items())},
            "inputs": {"annotation": str(annotation), "annotation_sha256": sha256(annotation),
                       "source_export_sha256": src_sha, "annotator_versions": dict(versions)},
            "findings": rep.findings,
            "note": ("A reviewer may read the raw files; no manual verdict creates evidence or turns UNKNOWN "
                     "into KNOWN. INFO findings (under-binding) are conservative and never fail the gate.")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ann-v2 independent verifier")
    ap.add_argument("--annotation", type=Path, required=True)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--expect-source-sha", default=None)
    a = ap.parse_args(argv)
    r = verify(a.annotation, a.source, a.expect_source_sha)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "verify_report.json").write_text(json.dumps(r, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: r[k] for k in ("gate", "critical", "non_critical", "info", "records", "attempts",
                                        "recorded_positive", "rederived_positive")}, indent=1))
    print(json.dumps(r["by_kind"], indent=1))
    return 1 if r["gate"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
