#!/usr/bin/env python3
"""gf180's second DRC pass: the PDK's own run_drc.py, on what the fast deck
could not grade.

Why two checkers in one baseline. The assembled monolithic deck grades dev's
gf180 corpus at a 94 s median but times out on 86 designs; the PDK's own
run_drc.py executes 58 rule tables separately and reaches a verdict where the
single pass cannot -- it graded C03506 (18 290 cells) in 3891 s where the
monolithic pass produced nothing. Running the official one on everything costs
75 h against 19 h for the hybrid.

That is only legitimate because the two were measured to AGREE, in both
directions and on artifacts rather than verdicts:

  * on three designs the fast deck graded dirty, the official runner reports the
    same RULE NAMES with the same per-rule counts (1 on C00277, 2 on C03746, 10
    on C03506)
  * on 20 designs the fast deck called clean, the official runner finds nothing,
    all 54 tables empty

Each design records WHICH checker produced its verdict. A baseline graded by two
checkers has to be auditable per design, not just described in prose.

This pass NEVER downgrades: it only fills a verdict the fast deck did not
produce. A design the fast deck graded keeps that verdict, so the hybrid cannot
launder a violation into a pass by re-checking it with a different tool.
"""
import argparse
import glob
import json
import subprocess
import sys
from pathlib import Path

D = Path("/proj/workarea/yangao")
NO_VERDICT = (None, "", "timeout", "drc_failed", "skipped")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default=str(D / "_offdrc_hybrid"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--mp", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=21600)
    # A script that both SELECTS and ACTS needs a way to show the selection
    # without acting. Without this I ran it at the real corpus to see which
    # designs it would pick, and it went straight on to launch the official
    # checker on them -- hours of machine time and a write-back into the pilot
    # corpus, from a command meant to print a list.
    ap.add_argument("--dry-run", action="store_true",
                    help="print the selection and the cost estimate, run nothing")
    args = ap.parse_args(argv)

    runs = Path(args.runs).resolve()
    todo = []
    for f in sorted(glob.glob(str(runs / "*" / "result.json"))):
        try:
            d = json.load(open(f))
        except Exception:                                         # noqa: BLE001
            continue
        if d.get("drc_status") not in NO_VERDICT:
            continue
        # A design with NO LAYOUT cannot be DRC'd by any checker -- there is
        # nothing to check. Running the official runner on it burns an hour to
        # learn what the absent GDS already says.
        #
        # This filter is why --dry-run exists: the selection without it was 452
        # designs and 40.6 h, because `drc_status is None` also covers every
        # design that never reached a layout. The timeouts -- the designs a
        # second checker can actually help -- are a fraction of that.
        if not (Path(f).parent / "6_final.gds").is_file():
            continue
        todo.append(Path(f).parent.name)

    print("快 deck 未判出的 gf180 设计: %d 份" % len(todo), flush=True)
    if not todo:
        print("没有需要补判的，官方 runner 不启动")
        return 0
    if args.dry_run:
        # measured on the four validation designs: 644 / 1305 / 3891 / 4512 s
        est = len(todo) * 2588 / max(args.workers, 1) / 3600
        print("  前 8 份: %s" % ", ".join(t[:34] for t in todo[:8]))
        print("  估算: %d 份 x 2588s 中位 / %d 并发 = %.1f 小时"
              % (len(todo), args.workers, est))
        print("--dry-run：什么都没跑，也没有写回任何判决")
        return 0

    sel = Path(args.work + ".select")
    sel.parent.mkdir(parents=True, exist_ok=True)
    sel.write_text("\n".join(todo) + "\n")

    rc = subprocess.run(
        [sys.executable, "-I", str(D / "gf180_official_drc.py"),
         "--runs", str(runs), "--select", str(sel),
         "--out", args.out, "--work", args.work,
         "--workers", str(args.workers), "--mp", str(args.mp),
         "--timeout", str(args.timeout)]).returncode
    print("官方 runner rc=%d" % rc, flush=True)

    if not Path(args.out).is_file():
        print("官方 runner 没有产出 json -- 不写回任何判决")
        return 1

    rows = json.loads(Path(args.out).read_text())
    filled = kept = 0
    for r in rows:
        rd = runs / r["design"]
        rj = rd / "result.json"
        if not rj.is_file():
            continue
        try:
            cur = json.loads(rj.read_text())
        except Exception:                                         # noqa: BLE001
            continue
        # never downgrade a verdict the fast deck already produced
        if cur.get("drc_status") not in NO_VERDICT:
            kept += 1
            continue
        st = r.get("drc_status")
        if st not in ("clean", "violations"):
            continue                 # still no verdict; leave it absent
        cur["drc_status"] = st
        cur["drc_violations_official"] = r.get("violations")
        cur["drc_checker"] = "pdk_run_drc.py"          # per-design provenance
        cur["drc_tables_total"] = r.get("tables_total")
        try:
            rj.write_text(json.dumps(cur, indent=1) + "\n")
            filled += 1
        except OSError:
            pass

    print("补判并写回 %d 份；%d 份已有判决未动" % (filled, kept))
    # a design the fast deck graded must also carry its own provenance, so the
    # two populations are separable later
    tagged = 0
    for f in glob.glob(str(runs / "*" / "result.json")):
        try:
            cur = json.loads(Path(f).read_text())
        except Exception:                                         # noqa: BLE001
            continue
        if cur.get("drc_status") in ("clean", "violations") and \
           not cur.get("drc_checker"):
            cur["drc_checker"] = "r2g_assembled_deck"
            try:
                Path(f).write_text(json.dumps(cur, indent=1) + "\n")
                tagged += 1
            except OSError:
                pass
    print("标注为快 deck 判决的: %d 份" % tagged)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
