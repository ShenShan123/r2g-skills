"""ann-v2 per-unit annotation (spec §3-§7): identity, binding, typed targets, step effect,
new failures, rollback and the positive gate. D6 record-time run hashes are reported as an
ADVISORY field only -- never a binding, never counted.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
from pathlib import Path

from ann_v2 import effects as EF
from ann_v2 import fields as FD
from ann_v2 import rollback as RB
from ann_v2.bind_source import Binder, attempt_from_row, is_attempt, projects_of  # noqa: F401

FAMILY_SOURCE: list = []            # source refs for the family table (set by the driver)
RECORDER_PREFIXES = ("resize_", "pinperim_", "memfix_", "pdnfix_", "synthmem_")
TARGET_FIELDS = ("stage", "exit", "error_code", "timeout", "grt_overflow", "count", "rule_class", "checker", "status",
                 "mismatch_class", "metric", "unit", "slack", "tns", "violators", "max_slew", "max_cap",
                 "max_fanout", "measurement_stage")
# target field <- measurement field, per type (all other target fields are NOT_APPLICABLE)
APPLICABLE = {
    "stage_abort": {"stage": "stage", "exit": "exit", "error_code": "error_code", "timeout": "timeout",
                    "grt_overflow": "grt_overflow"},
    "route_residual": {"count": "count"},
    "drc": {"count": "count", "checker": "checker", "rule_class": None},
    "lvs": {"status": "status", "checker": "checker", "mismatch_class": "mismatch_class"},
    "setup": {"metric": "metric", "unit": "unit", "slack": "value", "tns": "tns", "violators": "violators",
              "measurement_stage": "stage"},
    "hold": {"metric": "metric", "unit": "unit", "slack": "value", "tns": "tns", "violators": "violators",
             "measurement_stage": "stage"},
    "drv": {"max_slew": "max_slew", "max_cap": "max_cap", "max_fanout": "max_fanout",
            "measurement_stage": "stage"},
}
MEAS_CHECK = {"stage_abort": "stage", "route_residual": "route_residual", "drc": "drc", "lvs": "lvs",
              "setup": "setup", "hold": "hold", "drv": "drv"}


def identity(u: dict, families: dict) -> dict:
    """design_id / ip_family / source_group (spec §6). secworks/* is ONE source group."""
    design = (u.get("identity") or {}).get("design")
    fam = families.get(design) if design else None
    out = {"design_id": (FD.known(design, list(FAMILY_SOURCE), "unit_identity") if design
                         else FD.unknown("not_recorded", "unit_identity"))}
    if fam is None:
        out["ip_family"] = FD.unknown("not_recorded", "family_table")
        out["source_group"] = FD.unknown("not_recorded", "family_table")
    else:
        out["ip_family"] = FD.known(fam, list(FAMILY_SOURCE), "family_table")
        out["source_group"] = FD.known("secworks" if fam.startswith("secworks/") else fam,
                                       list(FAMILY_SOURCE), "source_group_rule_D2")
    return out


def _target_from_run(kind: str, meas: dict) -> dict:
    chk = meas[MEAS_CHECK[kind]]
    fields = {f: FD.na() for f in TARGET_FIELDS}
    for tf, mf in APPLICABLE[kind].items():
        if mf is None:
            continue
        fields[tf] = chk["fields"].get(mf) or FD.unknown("metric_absent", "target")
    return {"type": kind, "type_status": chk["type_status"], "source_of_type": "bound_pre_run",
            "fields": fields}


def _unknown_target(reason: str) -> dict:
    return {"type": "UNKNOWN", "type_status": "UNKNOWN", "unknown_reason": reason,
            "fields": {f: FD.na() for f in TARGET_FIELDS}}


def _drc_rule_class(u: dict, target: dict) -> dict:
    """Dominant rule class from the action's recorded before_categories (on-disk row only),
    cross-checked against the bound pre-run total when one exists."""
    rec = RB.row_field(u, "before_categories", "before_categories")
    if rec["status"] != "KNOWN":
        return rec
    cats = rec["value"]
    if isinstance(cats, str):
        try:
            cats = json.loads(cats)
        except ValueError:
            cats = None
    if not isinstance(cats, dict) or not cats:
        return FD.unknown("not_recorded", "before_categories")
    counts = {k: (v.get("count") if isinstance(v, dict) else v) for k, v in cats.items()}
    if any(not isinstance(c, (int, float)) for c in counts.values()):
        return FD.unknown("metric_absent", "before_categories")
    total = target["fields"]["count"]
    if total.get("status") == "KNOWN" and sum(counts.values()) != total["value"]:
        return FD.unknown("conflicting_sources", "before_categories", rec["source"] + total["source"])
    dom = max(counts, key=lambda k: (counts[k], k))
    return FD.known(dom, rec["source"], "before_categories")


def _recorded_stage_abort(u: dict) -> dict | None:
    """A stage abort typed from the action's own recorded pre-action situation: only when an
    ORFS error code was recorded (that alone shows an abort, not a route residual)."""
    code = RB.row_field(u, "situation.error_code", "fix_log_row")
    if code["status"] != "KNOWN" or not code["value"] or code["value"] == "UNKNOWN":
        return None
    t = {"type": "stage_abort", "type_status": "KNOWN", "source_of_type": "recorded_pre_action_situation",
         "fields": {f: FD.na() for f in TARGET_FIELDS}}
    f = t["fields"]
    f["stage"] = RB.row_field(u, "violation_class", "fix_log_row")
    f["exit"] = FD.unknown("not_recorded", "fix_log_row")
    f["error_code"] = code
    timeout = RB.row_field(u, "situation.timeout", "fix_log_row")
    f["timeout"] = timeout if timeout["status"] != "KNOWN" or isinstance(timeout["value"], bool) \
        else FD.unknown("not_recorded", "fix_log_row")
    if f["stage"].get("value") == "route":
        f["grt_overflow"] = FD.unknown("not_recorded", "fix_log_row")
    return t


def targets(u: dict, pre: dict | None, pre_reason: str | None) -> list:
    check, vclass = u["raw_row"].get("check"), u["raw_row"].get("violation_class")
    if check == "orfs_stage":
        if pre is not None:
            if pre["stage"]["present"] and FD.STAGE_ORDER and \
                    (pre["stage"]["fields"]["stage"].get("value") == vclass):
                return [_target_from_run("stage_abort", pre)]
            if vclass == "route" and pre["route_residual"]["present"]:
                return [_target_from_run("route_residual", pre)]
            return [_unknown_target("conflicting_sources")]
        rec = _recorded_stage_abort(u)
        return [rec] if rec else [_unknown_target(pre_reason or "not_measured")]
    if pre is None:
        return [_unknown_target(pre_reason or "not_measured")]
    if check == "drc":
        if not pre["drc"]["present"]:
            return [_unknown_target("conflicting_sources" if pre["drc"]["measured"] else "not_measured")]
        t = _target_from_run("drc", pre)
        t["fields"]["rule_class"] = _drc_rule_class(u, t)
        return [t]
    if check == "lvs":
        if not pre["lvs"]["present"]:
            return [_unknown_target("conflicting_sources" if pre["lvs"]["measured"] else "not_measured")]
        return [_target_from_run("lvs", pre)]
    if check == "timing":
        out = [_target_from_run(k, pre) for k in ("setup", "drv") if pre[k]["present"]]
        if out:
            return out
        measured = pre["setup"]["measured"] or pre["drv"]["measured"]
        return [_unknown_target("conflicting_sources" if measured else "not_measured")]
    return [_unknown_target("not_recorded")]


def post_run_advisory(u: dict, runs: dict, projects: list) -> dict | None:
    """D6, ADVISORY ONLY: the run a recorder referenced by hash when the row was written.
    It names the run referenced at record time, not the run the action produced."""
    sid = str(u["raw_row"].get("fix_session_id") or "")
    if not sid.startswith(RECORDER_PREFIXES):
        return None
    suffix = sid.split("_", 1)[1]
    paths = set(projects)
    for p in projects:
        for m in glob.glob(p + "/backend/RUN_*/run-meta.json"):
            try:
                paths.add(os.path.dirname(os.path.dirname(json.loads(Path(m).read_text())["config_mk"])))
            except (OSError, ValueError, KeyError, TypeError):
                pass
    match = sorted({t for t in runs for pp in paths
                    if hashlib.sha1(f"{pp}:{t}".encode("utf-8")).hexdigest()[:12] == suffix})
    sharing = set()
    for p in projects:
        for r in FD._jsonl(Path(p) / "reports" / "fix_log.jsonl"):
            s = str(r.get("fix_session_id") or "")
            if s.startswith(RECORDER_PREFIXES) and s.split("_", 1)[1] == suffix:
                sharing.add((s, r.get("iter"), r.get("ts")))
    origin = None
    stage = u["raw_row"].get("violation_class")
    if len(match) == 1:
        r = runs[match[0]]
        if any(x.get("stage") == stage and x.get("status") in (0, "0") for x in r["stage_log"]):
            origin = match[0]
        else:
            entry = ((r["resume_meta"] or {}).get("parent_lineage") or {}).get(stage) or {}
            origin = entry.get("parent_run") if entry.get("verified") else None
    unresolved = ["action attribution not established",
                  "intermediate actions, retries or recoveries not excluded", "pre-action run unbound"]
    if len(sharing) > 1:
        unresolved.append("hash shared by %d recorded rows" % len(sharing))
    return {"status": "ADVISORY", "counted": False, "hash_match": match, "rows_sharing_hash": len(sharing),
            "artifact_origin": origin, "unresolved": unresolved}


def admission(positive: bool, *measurements) -> dict:
    """History admission (user decision 2026-10-18): a positive whose bound pre or post run has
    unconstrained timing (no constrained path) is not admitted -- the task's acceptance cannot
    vouch for timing. The single-step evidence is kept; the unit stays in the denominator."""
    reasons = [] if positive else ["not_positive"]
    if any(m and (m.get("constraint") or {}).get("timing_unconstrained") for m in measurements):
        reasons.append("timing_unconstrained")
    return {"status": "ADMITTED" if not reasons else "NOT_ADMITTED", "reasons": reasons}


def annotate_unit(u: dict, binder: Binder, families: dict) -> dict:
    rec = {"unit_id": u["unit_id"], "identity": identity(u, families)}
    if not is_attempt(u):
        return {**rec, "status": "NOT_AN_ATTEMPT", "positive": False, "positive_blockers": ["not_an_attempt"],
                "admission": {"status": "NOT_ADMITTED", "reasons": ["not_an_attempt"]}}
    rec["status"] = "ATTEMPT"
    projects = projects_of(u)
    rows = RB.on_disk_rows(u)
    if projects:
        # bind from the on-disk row when it exists (the export copy carries enriched fields)
        post, pre = binder.bind(attempt_from_row(rows[0][0] if rows else u["raw_row"], projects))
        runs = binder._runs(projects)
    else:
        post = pre = {"status": "UNKNOWN", "run": None, "unknown_reason": "run_missing_on_disk",
                      "candidates": [], "evidence": None}
        runs = {}
    rec["binding"] = {"post": post, "pre": pre}
    rec["post_run_advisory"] = post_run_advisory(u, runs, projects) if post["status"] != "KNOWN" else None
    pre_m = FD.measure(runs[pre["run"]]["paths"][0]) if pre["status"] == "KNOWN" else None
    post_m = FD.measure(runs[post["run"]]["paths"][0]) if post["status"] == "KNOWN" else None
    rec["targets"] = targets(u, pre_m, pre.get("unknown_reason"))
    rec["step_effect"] = {t["type"]: EF.step_effect(t, pre_m, post_m) for t in rec["targets"]}
    rec["new_failures"] = EF.new_failures(pre_m, post_m)
    rec["rolled_back"] = RB.raw_rollback(u, projects, pre["run"] if pre["status"] == "KNOWN" else None)
    rb = rec["rolled_back"]["value"] if rec["rolled_back"]["status"] == "KNOWN" else None
    rec["positive"], rec["positive_blockers"] = EF.positive(
        rec["binding"], rec["targets"], rec["step_effect"], rec["new_failures"], rb)
    rec["candidate_class"] = EF.candidate_class(rec["binding"], rec["targets"], rec["step_effect"],
                                                rec["new_failures"], rb)
    rec["admission"] = admission(rec["positive"], pre_m, post_m)
    return rec


def report(records: list) -> dict:
    """Coverage report: every unit stays in the denominator (spec §8.6)."""
    import collections
    rep = {"units": len(records), "not_an_attempt": 0, "attempts": 0, "by_row_kind": {}, "by_target_type": {},
           "blockers": collections.Counter(), "advisory_d6": collections.Counter()}
    for r in records:
        if r["status"] == "NOT_AN_ATTEMPT":
            rep["not_an_attempt"] += 1
            continue
        rep["attempts"] += 1
        kind = r["row_kind"]
        k = rep["by_row_kind"].setdefault(kind, {"attempts": 0, "both_bound": 0, "type_known": 0,
                                                 "effects": collections.Counter(), "positive": 0,
                                                 "positive_source_groups": collections.Counter(),
                                                 "positive_ip_families": collections.Counter()})
        k["attempts"] += 1
        k["both_bound"] += r["binding"]["post"]["status"] == "KNOWN" and r["binding"]["pre"]["status"] == "KNOWN"
        k["type_known"] += all(t["type_status"] == "KNOWN" for t in r["targets"])
        for t, e in r["step_effect"].items():
            k["effects"][f"{t}:{e}"] += 1
            tt = rep["by_target_type"].setdefault(t, collections.Counter())
            tt[e] += 1
            tt["positive"] += r["positive"]
        k.setdefault("admitted", 0)
        k["admitted"] += (r.get("admission") or {}).get("status") == "ADMITTED"
        if r["positive"]:
            k["positive"] += 1
            sg, fam = r["identity"]["source_group"], r["identity"]["ip_family"]
            k["positive_source_groups"][sg["value"] if sg["status"] == "KNOWN" else "UNKNOWN"] += 1
            k["positive_ip_families"][fam["value"] if fam["status"] == "KNOWN" else "UNKNOWN"] += 1
        for b in r["positive_blockers"]:
            rep["blockers"][b] += 1
        if r.get("candidate_class"):
            cc = rep.setdefault("candidate_class", {}).setdefault(r["candidate_class"], collections.Counter())
            cc[r["row_kind"]] += 1
        adv = r.get("post_run_advisory")
        if adv:
            rep["advisory_d6"]["rows_with_recorder_hash"] += 1
            if len(adv["hash_match"]) == 1 and adv["rows_sharing_hash"] <= 1:
                rep["advisory_d6"]["unique_unshared_hash_match (to verify; not counted)"] += 1
    return json.loads(json.dumps(rep, default=dict))


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ann-v2 DEV annotation (scratch output; not the formal ann-v2)")
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    manifest = a.source / "source_manifest.json"
    families = json.loads(manifest.read_text())["split"]["family_of_design"]
    FAMILY_SOURCE[:] = [FD.src(manifest, "split.family_of_design")]
    binder, records = Binder(), []
    with (a.out / "ann_v2_dev.jsonl").open("w") as f:
        for line in (a.source / "source_export.jsonl").read_text().splitlines():
            u = json.loads(line)
            r = annotate_unit(u, binder, families)
            r["row_kind"] = "%s/%s" % (u["raw_row"].get("check"), u["raw_row"].get("violation_class"))
            records.append(r)
            f.write(json.dumps(r, sort_keys=True, default=str) + "\n")
    rep = report(records)
    rep["dev_only"] = True
    rep["source_sha256"] = FD.sha256_of(a.source / "source_export.jsonl")
    (a.out / "ann_v2_dev_report.json").write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
