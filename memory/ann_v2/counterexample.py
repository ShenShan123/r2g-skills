#!/usr/bin/env python3
"""ann-v2 counter-example run on REAL evidence (scratch copies; the eval tree is never written).

For a few annotated units whose effect was measured CLEARED, copy the files the annotator
reads into a scratch directory, re-annotate (the baseline must reproduce the formal record's
bindings and effect), then mutate the copy and re-annotate: every mutation must degrade the
answer in the expected direction (to UNKNOWN or to the correct KNOWN) and never create a
positive. The report is a receipt; nothing here is evidence of success.

  python3 -m ann_v2.counterexample --annotation <jsonl> --source <SOURCE dir> --scratch <dir> \
                                   --out <dir> [--per-kind 1]
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
from pathlib import Path

from ann_v2 import annotate as AN
from ann_v2 import fields as FD

KEEP = ("resume_meta.json", "stage_log.jsonl", "stage_artifact_manifest.jsonl", "effective-config.json",
        "run-meta.json", "drc/drc_result.json", "lvs/netgen_lvs_result.json", "reports_orfs/5_route_drc.rpt")


def copy_project(src: Path, dst: Path) -> None:
    (dst / "reports").mkdir(parents=True, exist_ok=True)
    for f in (src / "reports").glob("*"):
        if f.name == "fix_log.jsonl" or f.name.startswith("global_regression_it"):
            shutil.copy2(f, dst / "reports" / f.name)
    for run in sorted((src / "backend").glob("RUN_*")):
        d = dst / "backend" / run.name
        for rel in KEEP:
            if (run / rel).exists():
                (d / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(run / rel, d / rel)
        if (run / "logs").is_dir():
            (d / "logs").mkdir(parents=True, exist_ok=True)
            for f in (run / "logs").glob("*.json"):
                shutil.copy2(f, d / "logs" / f.name)
        if (run / "flow.log").exists():
            lines = (run / "flow.log").read_text(errors="ignore").splitlines()[-500:]
            d.mkdir(parents=True, exist_ok=True)
            (d / "flow.log").write_text("\n".join(lines) + "\n")


def relocate(u: dict, mapping: dict) -> dict:
    v = copy.deepcopy(u)
    for s in v["provenance"]["sources"]:
        for old, new in mapping.items():
            if s["path"].startswith(old + "/"):
                s["path"] = new + s["path"][len(old):]
    return v


def summary(r: dict) -> dict:
    return {"post": r["binding"]["post"]["run"], "post_reason": r["binding"]["post"].get("unknown_reason"),
            "pre": r["binding"]["pre"]["run"], "pre_reason": r["binding"]["pre"].get("unknown_reason"),
            "effect": r["step_effect"], "rolled_back": (r["rolled_back"]["status"], r["rolled_back"]["value"],
                                                        r["rolled_back"]["unknown_reason"]),
            "positive": r["positive"]}


def mutations(u: dict, rec: dict, roots: list):
    """(name, apply(), expectation(result) -> error or None)"""
    post = rec["binding"]["post"]["run"]
    pre = rec["binding"]["pre"]["run"]
    raw = u["raw_row"]
    runs = [Path(r) / "backend" / post for r in roots if (Path(r) / "backend" / post).is_dir()]
    pres = [Path(r) / "backend" / pre for r in roots if (Path(r) / "backend" / pre).is_dir()]
    logs = [Path(r) / "reports" / "fix_log.jsonl" for r in roots if (Path(r) / "reports" / "fix_log.jsonl").exists()]

    def m_delete_resume_meta():
        for d in runs:
            (d / "resume_meta.json").unlink()

    def m_duplicate_post():
        for d in runs:
            shutil.copytree(d, d.parent / (post + "_dup"))

    def m_add_claimant():
        for lg in logs:
            with lg.open("a") as f:
                f.write(json.dumps({"fix_session_id": "mut_claimant", "iter": 1, "strategy": raw["strategy"],
                                    "from_stage": raw["from_stage"], "check": raw["check"]}) + "\n")

    def m_nearer_unrelated():
        for d in runs:
            dup = d.parent / (post[:-4] + "zzzz")
            shutil.copytree(d, dup)
            meta = json.loads((dup / "resume_meta.json").read_text())
            meta["reason"] = meta["reason"].replace(f"strategy={raw['strategy']}", "strategy=mut_other")
            (dup / "resume_meta.json").write_text(json.dumps(meta))

    def m_break_anchor():
        for d in pres:
            man = d / "stage_artifact_manifest.jsonl"
            rows = [json.loads(l) for l in man.read_text().splitlines() if l.strip()]
            for r in rows:
                r["sha256"] = "0" * 64
            man.write_text("".join(json.dumps(r) + "\n" for r in rows))
            meta = d / "resume_meta.json"
            if meta.exists():
                meta.unlink()

    def m_remove_post_measurement():
        for d in runs:
            for rel in ("drc/drc_result.json", "logs/6_report.json", "stage_log.jsonl"):
                if (d / rel).exists():
                    (d / rel).unlink()

    def m_regression_file():
        it = int(raw.get("iter") or 1)
        for r in roots:
            (Path(r) / "reports" / f"global_regression_it{it}.json").write_text(json.dumps({
                "iter": it, "strategy": raw["strategy"], "config_restored": True,
                "pre": {"layout": {"run_tag": pre}}}))

    def m_row_marker_false():
        for lg in logs:
            rows = [json.loads(l) for l in lg.read_text().splitlines() if l.strip()]
            for r in rows:
                if (r.get("fix_session_id"), int(r.get("iter") or 1), r.get("strategy")) == \
                        (raw["fix_session_id"], int(raw.get("iter") or 1), raw["strategy"]):
                    r["rolled_back"] = False
            lg.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def m_copy_disagrees():
        if len(runs) < 2:
            return "skip"
        (runs[1] / "stage_log.jsonl").write_text((runs[1] / "stage_log.jsonl").read_text() +
                                                 json.dumps({"stage": "finish", "status": 1}) + "\n")

    def exp(**want):
        def check(res):
            s = summary(res)
            bad = {k: (s[k], v) for k, v in want.items() if s[k] != v}
            return None if not bad else f"expected {want}, got {bad}"
        return check

    yield "delete_post_resume_meta", m_delete_resume_meta, exp(post=None, post_reason="no_explicit_link", positive=False)
    yield "duplicate_post_run", m_duplicate_post, exp(post=None, post_reason="ambiguous_binding", positive=False)
    yield "add_claimant_row", m_add_claimant, exp(post=None, post_reason="ambiguous_binding", positive=False)
    yield "nearer_unrelated_run", m_nearer_unrelated, exp(post=post, pre=pre, positive=rec["positive"])
    yield "break_pre_anchor", m_break_anchor, exp(post=post, pre=None, positive=False)
    yield "remove_post_measurement", m_remove_post_measurement, exp(positive=False)
    yield "regression_file_bound_to_pre", m_regression_file, exp(rolled_back=("KNOWN", True, None), positive=False)
    yield "row_marker_false", m_row_marker_false, exp(rolled_back=("KNOWN", False, None))
    yield "copy_disagrees", m_copy_disagrees, exp(post=None, post_reason="copy_conflict", positive=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--annotation", type=Path, required=True)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--per-kind", type=int, default=1)
    a = ap.parse_args(argv)
    manifest = a.source / "source_manifest.json"
    families = json.loads(manifest.read_text())["split"]["family_of_design"]
    AN.FAMILY_SOURCE[:] = [FD.src(manifest, "split.family_of_design")]
    units = {}
    for l in (a.source / "source_export.jsonl").read_text().splitlines():
        if l.strip():
            u = json.loads(l)
            units[u["unit_id"]] = u
    chosen, per = [], {}
    for l in a.annotation.read_text().splitlines():
        r = json.loads(l)
        if r.get("status") != "ATTEMPT" or set(r["step_effect"].values()) != {"CLEARED"}:
            continue
        kind = r["row_kind"]
        if per.get(kind, 0) < a.per_kind:
            per[kind] = per.get(kind, 0) + 1
            chosen.append(r)
    results = []
    for rec in chosen:
        u = units[rec["unit_id"]]
        roots = AN.projects_of(u)
        base = a.scratch / rec["unit_id"].replace(":", "_")
        if base.exists():
            shutil.rmtree(base)
        mapping = {}
        for i, r in enumerate(roots):
            dst = base / "base" / f"copy{i}" / os.path.basename(r)
            copy_project(Path(r), dst)
            mapping[r] = str(dst)
        ub = relocate(u, mapping)
        baseline = AN.annotate_unit(ub, AN.Binder(), families)
        b, f = summary(baseline), summary(rec)
        entry = {"unit_id": rec["unit_id"], "row_kind": rec["row_kind"], "formal": f, "baseline": b,
                 "baseline_reproduces": {k: b[k] == f[k] for k in ("post", "pre", "effect", "positive")},
                 "mutations": []}
        names = [n for n, _, _ in mutations(ub, baseline, list(mapping.values()))]
        for name in names:
            mdir = base / name                                   # a fresh copy per mutation
            if mdir.exists():
                shutil.rmtree(mdir)
            shutil.copytree(base / "base", mdir)
            mmap = {old: new.replace(str(base / "base"), str(mdir)) for old, new in mapping.items()}
            um = relocate(u, mmap)
            apply, check = next((ap, ck) for n, ap, ck in mutations(um, baseline, list(mmap.values())) if n == name)
            if apply() == "skip":
                entry["mutations"].append({"name": name, "status": "SKIPPED"})
                continue
            res = AN.annotate_unit(um, AN.Binder(), families)
            err = check(res)
            entry["mutations"].append({"name": name, "status": "OK" if err is None else "UNEXPECTED",
                                       "result": summary(res), "note": err})
        results.append(entry)
    a.out.mkdir(parents=True, exist_ok=True)
    unexpected = sum(1 for e in results for m in e["mutations"] if m["status"] == "UNEXPECTED")
    rep = {"units": len(results), "unexpected": unexpected,
           "baseline_reproduced_all": all(all(e["baseline_reproduces"].values()) for e in results),
           "results": results, "scratch": str(a.scratch), "note": "counter-example receipt; no evidence of success"}
    (a.out / "counterexample_real_report.json").write_text(json.dumps(rep, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: rep[k] for k in ("units", "unexpected", "baseline_reproduced_all")}))
    for e in results:
        print(e["row_kind"], e["unit_id"], e["baseline_reproduces"],
              [(m["name"], m["status"]) for m in e["mutations"]])
    return 1 if unexpected or not rep["baseline_reproduced_all"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
