#!/usr/bin/env python3
"""Did the agent reproduce the nangate45 baseline -> r2g gain?

  python3 compare.py INDEX.json JOB_ID [--url http://127.0.0.1:8770]

INDEX.json is export_corpus.py's _index.json ({short: group}); the job is the
agent batch that ran those folders. Per group and per recipe the repair arm
used: how many the agent took dual-clean (passed = sign-off clean AND dataset
verified), and which agent repairs it applied. Recipes deliberately not ported
to the agent (2026-10-23: too large a change for what they win) are reported
as their own line, expected to fail, so they never hide inside a percentage.
"""
import collections
import json
import re
import sys
import urllib.request

NOT_PORTED = {"sv_frontend", "sv_frontend+antenna", "datafile_fetch_spec", "skip_incr",
              "dontuse_oai221x4", "case_rename", "no_rcx", "dontuse_ant"}


def main() -> int:
    idx = json.load(open(sys.argv[1]))["designs"]
    url = sys.argv[sys.argv.index("--url") + 1] if "--url" in sys.argv else "http://127.0.0.1:8770"
    job = json.load(urllib.request.urlopen("%s/api/jobs/%s" % (url, sys.argv[2])))
    # the agent names a design after its folder with every non-[A-Za-z0-9_] run
    # turned into "_" (pipeline.py), so "sonic-speed" is "sonic_speed" there
    by = {d["name"]: d for d in job["designs"]}
    for short in list(idx):
        alias = re.sub(r"[^A-Za-z0-9_]+", "_", short)
        if alias in by and short not in by:
            by[short] = by[alias]
    rows = collections.defaultdict(lambda: collections.Counter())
    fails = collections.defaultdict(list)
    used = collections.defaultdict(collections.Counter)
    for short, group in idx.items():
        g = group.split(":", 1)
        key = ("r2g 修复臂赢下的（未移植）" if g[0] == "r2g" and g[1] in NOT_PORTED else
               "r2g 修复臂赢下的" if g[0] == "r2g" else {"control": "对照：基线已双清", "apparatus": "器具修复救回"}[g[0]],
               g[1] if len(g) > 1 else "")
        d = by.get(short)
        det0 = (d or {}).get("detail") or {}
        # the experiment's criterion is sign-off dual-clean; the agent's "passed"
        # also needs the dataset verifier, so the two are counted apart
        dual = bool((det0.get("signoff") or {}).get("dual_clean"))
        st = d["status"] if d else "missing"
        rows[key]["dual"] += dual
        rows[key][st] += 1
        if d:
            det = d.get("detail") or {}
            for f in (det.get("frontend_fixes") or []) + (det.get("flow_fixes") or []):
                used[key][f["recipe"]] += 1
            for r in det.get("repair") or []:
                used[key]["signoff:" + str(r.get("strategy") or r.get("strategy_id") or "?")] += 1
        if not dual or st != "passed":
            fails[key].append((short, "双清但数据集未过" if dual else st, (det0.get("reason") or "")[:70]))
    print("job %s  %s" % (job["id"], job["status"]))
    print("%-28s %-22s %5s %5s %5s %s" % ("组", "repair-arm recipe", "双清", "+数据集", "总数", "agent 用到的修复"))
    tot = collections.Counter()
    for key in sorted(rows):
        c = rows[key]
        n = sum(v for k, v in c.items() if k != "dual")
        print("%-28s %-22s %5d %5d %5d %s" % (key[0], key[1], c["dual"], c["passed"], n,
                                          ", ".join("%s×%d" % kv for kv in used[key].most_common(4))))
    for key in sorted(fails):
        for short, st, why in fails[key]:
            print("  未通过  %-24s %-40s %-8s %s" % (key[1] or key[0], short, st, why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
