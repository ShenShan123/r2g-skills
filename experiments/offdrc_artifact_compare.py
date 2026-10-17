#!/usr/bin/env python3
"""Compare the two gf180 DRC checkers by their ARTIFACTS, not their verdicts.

EXPERIMENT_RUNBOOK_zh.md records this rule and the mistake that produced it:
"'both passed' proves nothing in itself -- earlier I treated three LVS
configurations agreeing as evidence of equivalence, and all three were in fact
failing; what agreed was the failure. What settles it is the md5, not the
verdict."

The gf180 validation so far has that flaw. Five designs where both checkers
report viol=0 is consistent with both being blind, and the two dirty designs
were compared by COUNT (1=1, 2=2) -- two different violations both count 1.

So this compares what each checker actually WROTE: the rule name and the
coordinates of every violation. Agreement then means they found the same
geometry for the same reason, which a count cannot show.
"""
import argparse
import collections
import glob
import json
import re
from pathlib import Path

D = Path("/proj/workarea/yangao")
ITEM = re.compile(r"<item>(.*?)</item>", re.S)
CAT = re.compile(r"<category>\s*'?([^'<]+)'?\s*</category>")
VAL = re.compile(r"<value>(.*?)</value>", re.S)


def parse_lyrdb(path: Path) -> collections.Counter:
    """Rule name -> count, plus a set of coordinate strings per rule."""
    try:
        txt = path.read_text(errors="replace")
    except Exception:                                            # noqa: BLE001
        return collections.Counter(), {}
    cnt = collections.Counter()
    coords = collections.defaultdict(set)
    for it in ITEM.findall(txt):
        m = CAT.search(it)
        rule = (m.group(1).strip() if m else "?")
        cnt[rule] += 1
        for v in VAL.findall(it):
            s = re.sub(r"\s+", "", v)[:120]
            if s:
                coords[rule].add(s)
    return cnt, coords


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--official-work", default=str(D / "_offdrc_work"))
    ap.add_argument("--assembled-runs", default=str(D / "gf180_full/runs"))
    args = ap.parse_args(argv)

    off_root = Path(args.official_work)
    if not off_root.is_dir():
        print("找不到官方 runner 的产物目录 %s" % off_root)
        return 2

    rows = []
    for d in sorted(p for p in off_root.iterdir() if p.is_dir()):
        off_cnt = collections.Counter()
        off_coords = collections.defaultdict(set)
        n_tables = 0
        for f in sorted(glob.glob(str(d / "*.lyrdb"))):
            n_tables += 1
            c, co = parse_lyrdb(Path(f))
            off_cnt.update(c)
            for k, v in co.items():
                off_coords[k] |= v
        # the assembled deck's own report for the same design
        asm = Path(args.assembled_runs) / d.name
        asm_cnt, asm_coords = collections.Counter(), {}
        for pat in ("*.lyrdb", "drc_signoff*.xml", "reports/*.lyrdb"):
            for f in glob.glob(str(asm / pat)):
                c, co = parse_lyrdb(Path(f))
                asm_cnt.update(c)
                for k, v in co.items():
                    asm_coords.setdefault(k, set()).update(v)
        rows.append((d.name, n_tables, off_cnt, asm_cnt, off_coords, asm_coords))

    print("%-46s %6s %8s %8s  %s" %
          ("设计", "表数", "官方", "单体", "规则是否一致"))
    for name, nt, oc, ac, ocd, acd in rows:
        orules, arules = set(oc), set(ac)
        if not ac:
            verdict = "单体没留报告 -- 无法比对产物"
        elif orules == arules:
            same = all(oc[r] == ac[r] for r in orules)
            verdict = "规则集相同，计数%s" % ("也相同 ✓" if same else "不同 ✗")
        else:
            only_o = orules - arules
            only_a = arules - orules
            verdict = "不同: 仅官方有 %s / 仅单体有 %s" % (
                sorted(only_o)[:3] or "-", sorted(only_a)[:3] or "-")
        print("%-46s %6d %8d %8d  %s"
              % (name[:46], nt, sum(oc.values()), sum(ac.values()), verdict))

    have_asm = sum(1 for r in rows if r[3])
    print()
    print("能做产物比对的: %d / %d 份" % (have_asm, len(rows)))
    if have_asm == 0:
        print("  单体 deck 的运行没有保留 lyrdb，所以目前只能比判决 --")
        print("  而按 runbook 的规则，判决一致不是等价性证据。要么重跑单体并保留产物，")
        print("  要么这条等价性声明不能下。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
