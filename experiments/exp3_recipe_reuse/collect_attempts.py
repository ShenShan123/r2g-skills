#!/usr/bin/env python3
"""Run ON a campaign host: tar (to stdout) the small artifacts of every LLM-fallback attempt under ROOT/cases/*
that the recipe-reuse simulation needs -- nothing is modified.  collect_attempts.py ROOT [ROOT ...] > out.tar

Per case (stored as <root-tag>/<case_id>/...):
  result.json, orfs_audit.jsonl              the attempt's verdict, usage, writes, flow audit
  llm/constraints/*, llm/hooks/*             the LLM's FINAL project config (what produced its verdict)
  llm/r2g_context/*                          R2G's diagnosis context handed to the LLM (what R2G saw when it stopped)
  llm/reports/*.json                         the harness signoff reports of the final layout
  r2g/constraints/*, r2g/hooks/*             R2G's final project config (prepare.from_project), the LLM's start point
"""
import io, json, os, sys, tarfile, glob

out = tarfile.open(fileobj=sys.stdout.buffer, mode="w|")
def add(path, arc):
    if os.path.isfile(path):
        out.add(path, arcname=arc, recursive=False)
for root in sys.argv[1:]:
    tag = root.strip("/").replace("/", "__")
    for res in sorted(glob.glob(root.rstrip("/") + "/cases/*/result.json")):
        case = os.path.dirname(res); cid = os.path.basename(case)
        try:
            r = json.load(open(res))
        except Exception:
            continue
        base = "%s/%s/" % (tag, cid)
        add(res, base + "result.json"); add(case + "/orfs_audit.jsonl", base + "orfs_audit.jsonl")
        proj = os.path.join(case, cid + "_bare")
        for sub in ("constraints", "hooks", "r2g_context"):
            for f in glob.glob(os.path.join(proj, sub, "*")):
                add(f, base + "llm/%s/%s" % (sub, os.path.basename(f)))
        for f in glob.glob(os.path.join(proj, "reports", "*.json")):
            add(f, base + "llm/reports/" + os.path.basename(f))
        fp = (r.get("prepare") or {}).get("from_project")
        if fp:
            for sub in ("constraints", "hooks"):
                for f in glob.glob(os.path.join(fp, sub, "*")):
                    add(f, base + "r2g/%s/%s" % (sub, os.path.basename(f)))
        meta = json.dumps({"root": root, "case": cid, "from_project": fp, "from_project_exists": bool(fp and os.path.isdir(fp))}).encode()
        ti = tarfile.TarInfo(base + "collect_meta.json"); ti.size = len(meta); out.addfile(ti, io.BytesIO(meta))
out.close()
