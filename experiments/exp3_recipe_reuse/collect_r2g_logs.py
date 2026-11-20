#!/usr/bin/env python3
"""Run ON a campaign host: tar (stdout) the last 400 lines of R2G's agent_logs/{synth,repair,flow}.log for every
fallback attempt under ROOT/cases/* (R2G's own logs of the run it stopped in). Read-only.
  collect_r2g_logs.py ROOT [ROOT ...] > logs.tar"""
import io, json, os, sys, tarfile, glob
out = tarfile.open(fileobj=sys.stdout.buffer, mode="w|")
for root in sys.argv[1:]:
    tag = root.strip("/").replace("/", "__")
    for res in sorted(glob.glob(root.rstrip("/") + "/cases/*/result.json")):
        cid = os.path.basename(os.path.dirname(res))
        try: fp = (json.load(open(res)).get("prepare") or {}).get("from_project")
        except Exception: continue
        for name in ("synth.log", "repair.log", "flow.log"):
            p = os.path.join(fp or "", "agent_logs", name)
            if os.path.isfile(p):
                data = "".join(open(p, errors="replace").readlines()[-400:]).encode()
                ti = tarfile.TarInfo("%s/%s/r2g/logs/%s" % (tag, cid, name)); ti.size = len(data)
                out.addfile(ti, io.BytesIO(data))
out.close()
