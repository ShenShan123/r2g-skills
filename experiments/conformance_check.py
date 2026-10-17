#!/usr/bin/env python3
"""Does what the driver EMITS match what BASELINE_PARAMETERS.md FREEZES?

Written after a silent divergence: the driver was edited to read the spec table
as `table[platform][design]` while the table is flat, so the hook became a no-op
-- the config kept CORE_UTILIZATION, the flow ran, and 30 designs of preflight
measured the utilization knob instead of the frozen spec. Nothing failed; the
numbers just answered a different question. A second edit moved the I/O delay
from the frozen 15% to 25%.

The lesson is not "be careful". It is that the document and the code can drift
apart with no error, so the agreement has to be CHECKED, cheaply, before every
run. This reads a real emitted config.mk + constraint.sdc and compares them
against the spec table and the frozen constants.

Run it on the preflight output before launching the real campaign.
"""
import argparse
import json
import re
import sys
from pathlib import Path

D = Path("/proj/workarea/yangao")
# the frozen constants, mirrored from BASELINE_PARAMETERS.md
FROZEN = {"uncertainty_pct": 5.0, "io_pct": 15.0, "utilization": 0.50,
          "core_inset_um": 2.0}


def check_run(run_dir: Path, spec: dict) -> list:
    bad = []
    name = "__".join(run_dir.name.split("__")[:-2])
    e = spec.get(name)
    cfg = (run_dir / "config.mk")
    sdc = (run_dir / "constraint.sdc")
    if not cfg.is_file() or not sdc.is_file():
        return ["%s: 缺 config.mk 或 constraint.sdc" % name[:40]]
    c, t = cfg.read_text(), sdc.read_text()

    if e is None:
        if "SPEC MISS" not in t:
            bad.append("%s: 规格表无此设计，但也没留下 SPEC MISS 记录" % name[:40])
        return bad

    # 1. the die must be the table's, and CORE_UTILIZATION must be gone
    if re.search(r"(?m)^\s*export\s+CORE_UTILIZATION", c):
        bad.append("%s: config 里还有 CORE_UTILIZATION -- 规格钩子没生效" % name[:40])
    m = re.search(r"(?m)^\s*export\s+DIE_AREA\s*=\s*0\s+0\s+(\S+)\s+(\S+)", c)
    if not m:
        bad.append("%s: config 没有显式 DIE_AREA" % name[:40])
    elif abs(float(m.group(1)) - float(e["die_side_um"])) > 1e-6:
        bad.append("%s: DIE_AREA %s != 规格表 %s"
                   % (name[:40], m.group(1), e["die_side_um"]))
    mc = re.search(r"(?m)^\s*export\s+CORE_AREA\s*=\s*(\S+)\s+(\S+)\s+(\S+)", c)
    if mc and abs(float(mc.group(1)) - FROZEN["core_inset_um"]) > 1e-6:
        bad.append("%s: core 内缩 %s != 冻结的 %s"
                   % (name[:40], mc.group(1), FROZEN["core_inset_um"]))

    # 2. the period must be the table's
    mp = re.search(r"(?m)^\s*set\s+_per\s+(\S+)", t)
    if not mp:
        bad.append("%s: SDC 里找不到周期" % name[:40])
    elif abs(float(mp.group(1)) - float(e["period_ns"])) > 1e-6:
        bad.append("%s: 周期 %s != 规格表 %s"
                   % (name[:40], mp.group(1), e["period_ns"]))

    # 3. uncertainty and I/O must be the frozen fractions
    for label, key, pat in (
            # -setup is REQUIRED in the pattern, not optional. Without the flag
            # the value lands on hold too, which demanded 0.8 ns of hold margin
            # on gf180's 16 ns designs and aborted CTS at RSZ-0060 after 1955
            # hold buffers. A check that accepts both spellings cannot tell the
            # defect from the fix.
            ("不确定度", "uncertainty_pct", r"set_clock_uncertainty\s+-setup\s+\[expr\s+\{\$_per\s*\*\s*([0-9.]+)\}\]"),
            ("I/O延迟", "io_pct", r"set\s+_io\s+\[expr\s+\{\$_per\s*\*\s*([0-9.]+)\}\]")):
        mm = re.search(pat, t)
        if not mm:
            bad.append("%s: SDC 里找不到%s" % (name[:40], label))
        elif abs(float(mm.group(1)) * 100 - FROZEN[key]) > 1e-6:
            bad.append("%s: %s %.1f%% != 冻结的 %.1f%%"
                       % (name[:40], label, float(mm.group(1)) * 100, FROZEN[key]))

    # 4. hold uncertainty must be a FIXED value, not a fraction of the period
    mh = re.search(r"set_clock_uncertainty\s+-hold\s+([0-9.]+)", t)
    if not mh:
        bad.append("%s: SDC 没有单独的 -hold 不确定度" % name[:40])
    elif abs(float(mh.group(1)) - 0.020) > 1e-9:
        bad.append("%s: hold 不确定度 %s != 冻结的 0.020" % (name[:40], mh.group(1)))

    # 5. the clock port must NOT carry an input delay
    if "set_input_delay" in t and "lsearch -all -inline -not -exact" not in t:
        bad.append("%s: 输入延迟没有排除时钟端口" % name[:40])
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, help="e.g. _preflight/nangate45")
    ap.add_argument("--platform", required=True)
    args = ap.parse_args(argv)
    spec = {k: v for k, v in
            json.loads((D / ("spec/spec_%s.json" % args.platform)).read_text()).items()
            if not k.startswith("_")}
    runs = sorted((D / args.root / "runs").iterdir()) if (D / args.root / "runs").is_dir() else []
    runs = [r for r in runs if r.is_dir()]
    if not runs:
        print("没有运行目录可查 -- 不能据此说一致")
        return 2
    allbad = []
    for r in runs:
        allbad += check_run(r, spec)
    print("查了 %d 份  (%s)" % (len(runs), args.platform))

    # Discriminate the two reasons a mismatch happens, because they call for
    # opposite responses and the message otherwise reads the same:
    #   * the DRIVER emitted the wrong thing      -> fix the driver, re-run
    #   * the TABLE changed after the run was built -> the artifacts are merely
    #     stale; re-run against the frozen table, nothing is wrong with either
    # This happened for real: the spec table was regenerated with the area
    # allowance WHILE a preflight was running against the previous one, and
    # every die then read 4.5% small -- exactly sqrt(1.092).
    spec_f = D / ("spec/spec_%s.json" % args.platform)
    try:
        t_spec = spec_f.stat().st_mtime
        # Compare against each run's own config.mk -- that is the artifact
        # carrying the spec values. A run DIRECTORY's mtime moves when results
        # land at the end, which on the first attempt made the newest run look
        # newer than the table and silenced this very message.
        t_runs = min((r / "config.mk").stat().st_mtime for r in runs
                     if (r / "config.mk").is_file())
        if allbad and t_spec > t_runs:
            import datetime as _d
            print("  注意: 规格表(%s) 比这批产物(%s) 新 -- 这些产物是用旧表建的，"
                  "属于过期而不是驱动出错；用冻结的表重跑即可"
                  % (_d.datetime.fromtimestamp(t_spec).strftime("%H:%M:%S"),
                     _d.datetime.fromtimestamp(t_runs).strftime("%H:%M:%S")))
    except Exception:                                            # noqa: BLE001
        pass
    if allbad:
        print("不一致 %d 处:" % len(allbad))
        for b in allbad:
            print("   ", b)
        return 1
    print("全部与 BASELINE_PARAMETERS.md 一致 ✓")
    print("   die/core 来自规格表，内缩 %.0fum，不确定度 %.0f%%，I/O %.0f%%，"
          "时钟端口已排除" % (FROZEN["core_inset_um"], FROZEN["uncertainty_pct"],
                              FROZEN["io_pct"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
