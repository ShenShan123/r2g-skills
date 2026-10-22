#!/usr/bin/env python3
"""P1c D-R1: item-by-item rollback-status restoration review of the verified PASS/PARTIAL units of an export.
Read-only on raw sources and git. Usage: restoration_review.py <code root> <export dir> <out.json>"""
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

E = Path("/data1/zhangdy/RTL/RTL_testbench/_r2g_eval")
code, exp, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(code / "memory"))
from tehm import neutral_export as nx  # noqa: E402
from tehm import restoration as rs  # noqa: E402


def git(*a):
    return subprocess.run(["git", "-C", str(code), *a], capture_output=True, text=True).stdout


# writer versions: every fix_signoff.sh version that writes the _log_iter row format (env_flags since 48e1e55c)
F = "r2g-skills/signoff-loop/scripts/flow/fix_signoff.sh"
writer = []
for line in filter(None, git("log", "--reverse", "--format=%H %aI", "48e1e55c^..HEAD", "--", F).split("\n")):
    c, d = line.split()
    writer.append({"commit": c[:12], "date": d, **rs.audit_writer_version(git("show", "%s:%s" % (c, F)))})

# trial harness versions: r2g_memory_eval.py from the Phase G trial harness (efdc53f8) to the last trial file
H = "memory/scripts/r2g_memory_eval.py"
trial_mtime = max(f.stat().st_mtime for f in (E / "g-trials").glob("*.json"))
harness = []
for line in filter(None, git("log", "--reverse", "--format=%H %aI %at", "efdc53f8^..HEAD", "--", H).split("\n")):
    c, d, at = line.split()
    if int(at) > trial_mtime:
        continue
    text = git("show", "%s:%s" % (c, H))
    m = re.search(r"\ndef _g_trial\(.*?(?=\ndef )", text, re.S)
    body = m.group(0) if m else ""
    harness.append({"commit": c[:12], "date": d, "has_g_trial": bool(body),
                    "rollback_statements": re.findall(r".*(?:restore|rollback|cfg_backup|_memory_rollback).*", body)})
harness_audit = {"versions": [h["commit"] for h in harness],
                 "no_rollback": bool(harness) and all(h["has_g_trial"] and not h["rollback_statements"] for h in harness),
                 "basis": "_g_trial creates a fresh project per trial, writes the edits into its config.mk and runs it; "
                          "no restore step (kept/best only steer LATER trials)", "detail": harness}

# engineer_loop recovery writer versions (the row format with _effect_fields since 20858a1b)
EL = "r2g-skills/signoff-loop/scripts/loop/engineer_loop.py"
executor = []
for line in filter(None, git("log", "--reverse", "--format=%H %aI", "20858a1b^..HEAD", "--", EL).split("\n")):
    c, d = line.split()
    executor.append({"commit": c[:12], "date": d, **rs.audit_engineer_loop_version(git("show", "%s:%s" % (c, EL)))})
# LLM harness versions (run_llm: rounds 1-2 L arm; llm_assist: later arms)
llm_harness = []
for line in filter(None, git("log", "--reverse", "--format=%H %aI", "--", H).split("\n")):
    c, d = line.split()
    a = rs.audit_llm_harness_version(git("show", "%s:%s" % (c, H)))
    if a["llm_assist"]["present"] or a["run_llm"]["present"]:
        llm_harness.append({"commit": c[:12], "date": d, **a})

# index every run record that carries LLM iterations (run_llm: "iterations"; llm_assist: "llm_assist")
import datetime
records = []
for f in sorted(E.glob("evaluation*/**/*.json")):
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError):
        continue
    if not isinstance(d, dict):
        continue
    its = d.get("llm_assist") if isinstance(d.get("llm_assist"), list) else (
        d.get("iterations") if d.get("arm") == "L" and isinstance(d.get("iterations"), list) else None)
    if not its:
        continue
    records.append({"path": str(f), "design": d.get("design"), "variant": d.get("variant"), "its": its,
                    "final_config": d.get("final_config") or "", "judged_run": d.get("judged_run"),
                    "project": d.get("project"),
                    "date": datetime.datetime.fromtimestamp(f.stat().st_mtime).astimezone().isoformat()})


def _sets(ap):
    out = {k: str(v) for k, v in (ap.get("set") or {}).items()}
    out.update({k: None for k in ap.get("unset") or []})
    return out


def llm_matches(u, project_dir):
    r = u["raw_row"]
    delta = {k: (None if v is None else str(v)) for k, v in (r.get("config_delta") or {}).items()}
    cleared = str(r.get("after")) == "0"
    want_iter = int(r["iter"]) if r.get("llm") else None          # llm_assist rows carry the loop iteration
    out = []
    for rec in records:
        # a record belongs to this project only if its judged run (a unique RUN_<time>_<pid>_<hash> name) is in
        # this project's backend: never matched by design/variant name (several runs share a design and variant)
        proj = Path(rec["project"]) if rec["project"] else Path(project_dir)
        if not rec["judged_run"] or not (proj / "backend" / rec["judged_run"]).is_dir():
            continue
        if proj.resolve() != Path(project_dir).resolve():
            continue
        for it in rec["its"]:
            ap = it.get("applied") or {}
            if not ap or _sets(ap) != delta or bool(it.get("physical_clean")) != cleared:
                continue
            if want_iter is not None and int(it.get("iter", -1)) != want_iter:
                continue
            proj = Path(rec["project"]) if rec["project"] else Path(project_dir)
            out.append({"record": rec["path"], "iteration": it, "final_config": rec["final_config"],
                        "judged_run": str(proj / "backend" / rec["judged_run"]) if rec["judged_run"] else None,
                        "date": rec["date"]})
    return out


def dialogue_files(project_dir, delta):
    out = []
    for f in sorted(Path(project_dir).glob("llm_*.response.json")):
        try:
            c = json.loads(f.read_text())["choices"][0]["message"]["content"]
            got = _sets(json.loads(c[c.index("{"):c.rindex("}") + 1]))
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
        if got == delta:
            req = Path(str(f).replace(".response.json", ".request.json"))
            out.append({"response": str(f), "request": str(req) if req.is_file() else None})
    return out


units = nx.read_export(exp)
rows_cache = {}


def project_rows(path):
    if path not in rows_cache:
        rows_cache[path] = [(i, json.loads(l)) for i, l in enumerate(Path(path).read_text(errors="ignore").splitlines(), 1)
                            if l.strip().startswith("{")]
    return rows_cache[path]


def find_trial_project(name):
    for d in E.glob("*/design_cases/%s" % name):
        return d
    return None


reviews, summary = [], collections.Counter()
for u in units:
    if not (u["source_eligible"] and u["outcome"]["verified"] and u["outcome"]["canonical"] in ("PASS", "PARTIAL")):
        continue
    r = u["raw_row"]
    if all(k in r for k in ("env_flags", "predicates")):
        per_copy = [rs.review_fix_signoff_unit(u, project_rows=project_rows(ref["path"]),
                                               project_dir=Path(ref["path"]).parent.parent, source_ref=ref,
                                               writer_audits=writer)
                    for ref in u["provenance"]["sources"] if ref["path"].endswith("fix_log.jsonl")]
        ok = [x for x in per_copy if x["restored"]]
        rev = dict(ok[0] if ok else per_copy[0])
        rev["copies_reviewed"] = len(per_copy)
        rev["copies_restored"] = len(ok)
        rev["restored"] = bool(ok)
        rev["restored_status"] = "false" if ok else "UNKNOWN"
    elif r.get("evidence_tier") == "component_trial":
        rev = rs.review_trial_unit(u, trial_project=find_trial_project(r.get("trial_project")),
                                   harness_audit={k: harness_audit[k] for k in ("versions", "no_rollback", "basis")},
                                   source_ref=u["provenance"]["sources"][0])
    elif all(k in r for k in ("before_status", "effect_fingerprint", "cumulative_config")):
        per_copy = [rs.review_engineer_loop_unit(u, project_rows=project_rows(ref["path"]),
                                                 project_dir=Path(ref["path"]).parent.parent, source_ref=ref,
                                                 executor_audits=executor)
                    for ref in u["provenance"]["sources"] if ref["path"].endswith("fix_log.jsonl")]
        ok = [x for x in per_copy if x["restored"]]
        rev = dict(ok[0] if ok else per_copy[0])
        rev.update(copies_reviewed=len(per_copy), copies_restored=len(ok), restored=bool(ok),
                   restored_status="false" if ok else "UNKNOWN")
    elif u["action"]["origin"] == "llm":
        ref = u["provenance"]["sources"][0]
        pdir = Path(ref["path"]).parent.parent
        seeds = u["lineage"]["seed_origin"]
        if seeds:                                              # seed copy: the original arm record and project
            rnd, kind, src = seeds[0].split(":")
            design, variant = pdir.name.split("__")[1:3]
            f = E / src / ("%s__%s__L.json" % (design, variant))
            recs = [x for x in records if x["path"] == str(f)]
            pdir = Path(recs[0]["project"]) if recs and recs[0]["project"] else pdir
        m = llm_matches(u, pdir)
        if seeds:
            m = [x for x in m if x["record"] == str(f)]
        rev = rs.review_llm_unit(u, matches=m, harness_audits=llm_harness, source_ref=ref)
        delta = {k: (None if v is None else str(v)) for k, v in (r.get("config_delta") or {}).items()}
        rev["dialogue"] = dialogue_files(pdir, delta)
    else:
        rev = {"review": rs.REVIEW_VERSION, "unit_id": u["unit_id"], "kind": "not_reviewed_yet",
               "restored": False, "restored_status": "UNKNOWN", "origin": u["action"]["origin"],
               "pending": "engineer_loop / LLM audit (task 14)"}
    reviews.append(rev)
    failed = [c for c in rev.get("required", []) if not rev["checks"].get(c)]
    summary[(rev["kind"], "restored" if rev["restored"] else "not:" + (",".join(failed[:2]) or "pending"))] += 1
out.write_text(json.dumps({"writer_audit": writer, "harness_audit": harness_audit, "executor_audit": executor,
                           "llm_harness_audit": llm_harness, "reviews": reviews},
                          indent=1, default=str) + "\n")
print("writer versions audited %d, all log after rollback: %s, all cumulative at log time: %s" % (
    len(writer), all(w["log_after_rollback"] for w in writer), all(w["cumulative_at_log_time"] for w in writer)))
print("trial harness versions %s, no rollback: %s" % (harness_audit["versions"], harness_audit["no_rollback"]))
for k, n in sorted(summary.items(), key=lambda kv: -kv[1]):
    print(n, k)
later = collections.Counter(rv["later_step_review"]["status"] + (" (window)" if rv["later_step_review"]["in_stale_backup_window"] else "")
                            for rv in reviews if rv.get("restored") and rv["kind"].startswith("fix_signoff"))
print("restored fix_signoff items, later-step review:", dict(later))
print("engineer_loop versions %d all ok: %s; LLM harness versions %d all ok: %s" % (
    len(executor), all(a["ok"] for a in executor), len(llm_harness), all(a["ok"] for a in llm_harness)))
print("LLM dialogue linked:", collections.Counter(bool(rv.get("dialogue")) for rv in reviews if rv["kind"] == "llm_run_record"))
