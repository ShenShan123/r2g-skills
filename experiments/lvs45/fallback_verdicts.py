#!/usr/bin/env python3
"""Collect the LVS-fallback verdicts into one file the tallies read.

Input: the "<tag> <run_dir> <out_dir>" list given to run_list.sh. For each row
the verdict is re-derived from <out_dir>/netgen.rpt with the same rule as
lvs45_fallback.sh: match ONLY on Netgen's unqualified "Circuits match
uniquely."; any other final result is a mismatch; none at all is no verdict.

Output: {resolved run_dir: {"verdict", "final", "out_dir", "tag"}} as JSON.
Usage: fallback_verdicts.py <list> <out.json>
"""
import json
import os
import sys


def verdict(out_dir: str):
    try:
        lines = open(os.path.join(out_dir, "netgen.rpt"), errors="replace").read().splitlines()
    except OSError:
        return "no_verdict", ""
    final = [l for l in lines if l.startswith("Final result")]
    if not final:
        return "no_verdict", ""
    f = final[-1].strip()
    return ("match" if f == "Final result: Circuits match uniquely." else "mismatch"), f


def main() -> int:
    res = {}
    for line in open(sys.argv[1]):
        if not line.strip():
            continue
        tag, run_dir, out_dir = line.split()
        v, f = verdict(out_dir)
        res[os.path.realpath(run_dir)] = {"verdict": v, "final": f, "out_dir": out_dir, "tag": tag}
    json.dump(res, open(sys.argv[2], "w"), indent=1, sort_keys=True)
    from collections import Counter
    print(dict(Counter(v["verdict"] for v in res.values())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
