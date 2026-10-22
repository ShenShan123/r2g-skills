#!/usr/bin/env python3
"""Emit the frozen per-design spec from the pilot corpus.

One JSON per platform: {design: {cells, period_ns, die_side, core_side}}.
Values follow experiments/BASELINE_PARAMETERS.md exactly; this script is the
single place they are computed, so the baseline run cannot drift from the
document.

Why the PILOT's cell count. The period is a function of post-synthesis size, but
the period is read AT synthesis -- a circular dependency. The contract resolves
it by pinning each design's size class to its BASELINE synthesis and never
recomputing, and the pilot is exactly that: a synthesis measurement at a known
nominal period. Using it is traceable and costs no extra synthesis pass.

Why an explicit die rather than CORE_UTILIZATION. Deriving the die at run time
would let a repair move its own goalpost -- a fix that cuts cell count would
shrink its die and hand itself an easier job. Frozen, a repair that cuts cells
gets more room (a real benefit) and one that grows cells gets squeezed (a real
cost), which is how a tape-out behaves.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import math
import re
import os
import sys
from pathlib import Path

# The pilot's p95 achievable period per size class, rounded up.
#
# Why p95 and not p90. The period is a PRESSURE knob, not a pass condition --
# a design that misses its period still passes if it is DRC+LVS clean (measured
# on the pilot: 237 sky130hd designs missed their period and 224 of those were
# still dual-clean). So tightening the period does not create failures to
# repair; it creates timing-repair buffers, which grow cell area (measured on
# C00059: a 3x tighter period cost +10.4% real cell area, essentially all
# buffers) and therefore utilization. Against a FROZEN die, extra utilization
# turns into congestion -- the one symptom r2g cannot repair, since its only
# congestion lever lowers utilization and the die will not follow.
#
# p90 would thus have made r2g measure WORSE, not better: more cost, and the
# added failures land in the one class outside its repair envelope. p95 keeps
# the period inside what the platform demonstrably achieves while still binding
# (verified: 23/24 nangate45 and 22/23 gf180 preflight designs meet it).
PERIOD_NS = {
    "nangate45": [(100, 0.5), (1000, 1.0), (5000, 1.6), (None, 3.0)],
    "gf180":     [(100, 2.2), (1000, 5.0), (5000, 9.0), (None, 16.0)],
    "sky130hd":  [(100, 1.8), (1000, 3.6), (5000, 6.5), (None, 16.0)],
}
# "Utilization - flat 40%" and "PDN core floor - baseline policy"
# 0.50, matching BASELINE_PARAMETERS.md. The anchor is OpenLane's own sky130
# default (FP_CORE_UTIL=50 / PL_TARGET_DENSITY=0.5) rather than the 60-70% the
# literature recommends, because that advice assumes a floorplan the designer
# can still move: here the die is FROZEN, so headroom above the chosen value is
# not a working margin and every congestion failure above it is permanently
# unfixable (r2g's only congestion lever, density_relief, lowers utilization).
# CHANGING THIS REQUIRES REGENERATING THE SPEC TABLE -- it is the one place the
# die is computed, and a stale table would run designs against a die the frozen
# spec no longer describes.
UTILIZATION = 0.50
# The smallest core each platform's PDN grid can be built in. Applied
# PROACTIVELY here rather than left to ORFS's PDN-0185 run-time fallback, so the
# die is known before the flow starts and both arms get the same one. A design
# small enough to be floored runs at far below the 50% target by construction;
# that is accepted (it is a property of the design, not a spec error).
PDN_FLOOR_UM = {"nangate45": 32.0, "gf180": 110.0, "sky130hd": 32.0}

# IO positions per um of PERIMETER, for the pin floor below. NOT fitted:
# place_pins keeps two tracks between pins, so k = 1/(2 * pin-layer pitch).
# Both observed PPL-0024 failures confirm it -- nangate45 reported 1642
# positions on a 228 um core (k=1.80 -> pitch 0.278, LEF says 0.28) and gf180
# 4924 on 1377 (k=0.894 -> pitch 0.56, LEF says 0.56).
#
# sky130hd is MEASURED, not derived (2026-10-22). Its 1/(2*0.46)=1.087 came from
# the met2 pitch and was never checked against a sky130hd failure -- the two
# confirmations above are nangate45 and gf180. On sky130hd the frozen-spec smoke
# run's C01139 still hit PPL-0024 on its pin-floored die (976 positions on a
# 267 um core), and all 28 PPL-0024 messages in the pilot + smoke logs give
# positions / core perimeter = 0.912-0.923 (0.913 on every design above 1000
# pins), i.e. 16% fewer positions than the derivation, so every pin-floored die
# came out too small. 0.90 = the measured minimum with the same ~1% margin as
# nangate45's per-design correction (need_side * 1.01).
IO_POS_PER_UM = {"nangate45": 1.80, "gf180": 0.894, "sky130hd": 0.90}
CORE_INSET_UM = 2.0
PILOT_ROOT = {"nangate45": "n45_full", "gf180": "gf180_full",
              "sky130hd": "full_out"}


def period_for(platform: str, cells: int) -> float:
    for upper, ns in PERIOD_NS[platform]:
        if upper is None or cells < upper:
            return ns
    raise AssertionError("unreachable: last bin has upper=None")


# Per-platform area allowance, MEASURED by derive_area_allowance.py from the
# utilization preflight, never hand-picked. The die is sized from the PILOT's
# recorded cell area, but the pilot ran one flat period per platform while the
# frozen spec gives each size class its own -- and a tighter period makes the
# resizer insert timing-repair buffers, so the final layout's cell area exceeds
# the pilot's. Measured: on gf180's C03211 those buffers are 39% of the final
# cell area, 27% on C00496, 24% on C03488. Without the allowance the achieved
# utilization overshot the frozen 50% target by ten points on gf180 (median
# 60.0%, six of ten designs above target+5%), and a FROZEN die turns that
# overshoot into congestion -- the one symptom r2g cannot repair once the die is
# fixed, since its only lever lowers utilization. Cost without signal.
#
# The allowance is really two terms: about 1.07 for cells the synthesis area
# never counted (tap, endcap, clock buffers) plus a period-dependent buffer
# term. That is why nangate45 (fast process, comfortable periods) needs 1.03
# while gf180 (180 nm, periods tightened up to 4.5x from the pilot's flat 10 ns)
# needs 1.18.
# These are the MEASURED values (derive_area_allowance.py over the 30-design
# wide preflight: 15/18/18 binding designs after PDN-floored ones are excluded).
# They were previously the pre-measurement ESTIMATES 1.031/1.184/1.092, and the
# wide preflight found sky130hd's to be wrong by 9% -- at 1.092 the achieved
# utilization came in at 53.7% median against a 50% target, i.e. the die was
# too small on exactly the platform whose sample had been thinnest.
#
# Keeping the old estimates here would have been a silent trap: the file below
# normally supplies the real numbers, so the fallback only fires when the file
# is MISSING -- the one moment nobody is looking -- and it would then size every
# die from superseded values with no error. Same shape as every other defect in
# this project's catalog: a fallback that quietly substitutes a worse value.
# So the fallback now (a) holds the measured numbers and (b) says so on stderr.
AREA_ALLOWANCE_DEFAULT = {"nangate45": 1.021, "gf180": 1.144, "sky130hd": 1.190}
ALLOWANCE_FILE = Path("/proj/workarea/yangao/spec/area_allowance.json")
_warned: set[str] = set()


def area_allowance(platform: str) -> float:
    """The measured allowance, read from disk so the number is never copied."""
    if ALLOWANCE_FILE.is_file():
        try:
            v = json.loads(ALLOWANCE_FILE.read_text()).get(platform)
            if isinstance(v, (int, float)) and 1.0 <= v <= 2.0:
                return float(v)
        except Exception:                                        # noqa: BLE001
            pass
    if platform not in _warned:
        _warned.add(platform)
        print("警告: 读不到 %s，%s 回退到内置余量 %.3f（应与实测一致，但请确认）"
              % (ALLOWANCE_FILE, platform,
                 AREA_ALLOWANCE_DEFAULT.get(platform, 1.0)), file=sys.stderr)
    return AREA_ALLOWANCE_DEFAULT.get(platform, 1.0)


def pin_counts(pilot_dir: Path) -> dict:
    """Pin count per design, from the PINS header of whatever DEF the pilot wrote.

    Only designs that produced a DEF have one, which is roughly 60% of the
    corpus, so this cannot become a term in the general formula without
    inventing numbers for the rest. It does not need to be: the floor can only
    RAISE a core side, and pin_floor_equivalence.py shows that over all 1249
    nangate45 designs every design the floor would raise has ALREADY failed, all
    15 of them with PPL-0024, while zero passing designs would be raised and
    zero PPL failures would be left unraised. So applying it exactly where the
    count is known is equivalent to having run it from the start.
    """
    pins = {}
    for f in glob.glob(str(pilot_dir / "runs" / "*" / "6_final.def")):
        short = "__".join(os.path.basename(os.path.dirname(f)).split("__")[:-2])
        if short in pins:
            continue
        try:
            with open(f, "r", errors="replace") as fh:
                head = fh.read(200000)
        except OSError:
            continue
        m = re.search(r"^PINS (\d+)", head, re.M)
        if m:
            pins[short] = (int(m.group(1)), "pilot_def")
    # A design that failed place_pins never wrote a DEF -- which is exactly the
    # design the floor exists for. place_pins prints the count when it refuses
    # ("[ERROR PPL-0024] Number of IO pins (313) exceeds ..."), so failed runs
    # carry it in their logs. Pin count is a property of the RTL, so any run of
    # the design states the same number: where the pilot log and the frozen
    # re-run's log both have it, all 14 agree. Without this source the floor
    # reached 1 of the 25 nangate45 dev designs failing PPL-0024.
    for root in [pilot_dir / "runs", *(Path(r) for r in EXTRA_PIN_LOG_ROOTS)]:
        for rd in sorted(root.glob("*")):
            short = "__".join(rd.name.split("__")[:-2])
            if short in pins:
                continue
            for lg in rd.glob("logs/*/*/*/*.log"):
                try:
                    t = lg.read_text(errors="replace")
                except OSError:
                    continue
                m = re.search(r"\[ERROR PPL-0024\] Number of IO pins \((\d+)\)", t)
                if m:
                    pins[short] = (int(m.group(1)),
                                   "pilot_ppl_log" if root == pilot_dir / "runs" else "rerun_ppl_log")
                    break
    return pins


# Extra run roots whose PPL-0024 logs may state a pin count the pilot lacks.
EXTRA_PIN_LOG_ROOTS: list[str] = []
# {design: (cells, chip_area)} from the BASELINE re-run's synth_stat.txt, for
# designs the pilot never synthesised. Filled from --baseline-size-root.
BASELINE_SIZES: dict = {}
REPAIR_SIZES: dict = {}
# {design: (die_perimeter_um, pins, available_positions)} from the most recent
# PPL-0024 refusal under --ppl-correction-root.
PPL_CORRECTIONS: dict = {}


def load_ppl_corrections(roots: list[str]) -> None:
    pat = re.compile(r"Number of IO pins \((\d+)\) exceeds maximum number of available "
                     r"positions \((\d+)\)\. Increase the die perimeter from ([0-9.]+)um")
    for root in roots:
        for lg in sorted(Path(root).glob("*/logs/*/*/*/3_2_place_iop.log")):
            comp = next((c for c in lg.parts if re.match(r"C\d{5}__", c)), None)
            if comp is None:
                continue
            m = pat.search(lg.read_text(errors="replace"))
            if m:
                PPL_CORRECTIONS["__".join(comp.split("__")[:3])] = (
                    float(m.group(3)), int(m.group(1)), int(m.group(2)))


def top_section_size(run_dir: Path):
    """(cells, area) from the top module's section of a hierarchical
    synth_stat.txt, or None for a flat report (one section, already right)."""
    for st in run_dir.glob("reports/*/*/*/synth_stat.txt"):
        t = st.read_text(errors="replace")
        if t.count("=== ") < 2:
            return None
        top = st.parts[-3]
        i = t.find("=== %s ===" % top)
        if i < 0:
            return None
        m = re.compile(r"^\s*(\d+)\s+(\S+)\s+\d+\s+\S+\s+cells\s*$", re.M).search(t, i)
        if not m:
            return None
        try:
            return int(m.group(1)), float(m.group(2))
        except ValueError:
            return None
    return None


def load_baseline_sizes(roots: list[str], into: dict | None = None) -> None:
    """Read synth_stat.txt exactly as orfs_spec.py's harvest does, so the
    fallback is the same quantity as the pilot's, not an approximation. The
    design is identified by the first path component that looks like a run
    name (C<5 digits>__...), so any layout of work dirs under a root works."""
    into = BASELINE_SIZES if into is None else into
    pat_area = re.compile(r"^\s*(\d+)\s+(\S+)\s+\d+\s+\S+\s+cells\s*$", re.M)
    for root in roots:
        for stat in sorted(Path(root).rglob("synth_stat.txt")):
            comp = next((c for c in stat.parts if re.match(r"C\d{5}__", c)), None)
            if comp is None:
                continue
            short = "__".join(comp.split("__")[:3])
            if short in into:
                continue
            t = stat.read_text(errors="replace")
            m = pat_area.search(t)
            if not m:
                continue
            try:
                into[short] = (int(m.group(1)), float(m.group(2)))
            except ValueError:
                continue


def pin_required_core(platform: str, npins: int) -> float:
    """Smallest core side whose perimeter holds npins IO positions.

    k is derived, not fitted: place_pins keeps two tracks between pins, so
    k = 1/(2*pitch) on the pin layer -- nangate45's 1.80 matches its 0.278/LEF
    0.28 pitch, gf180's 0.894 its 0.56. Cell area scales with the square of the
    core side while IO capacity scales with its perimeter, which is why a die
    sized purely from area can be large enough for the cells and still too
    small for the pins; that asymmetry is the whole PPL-0024 failure mode.
    """
    k = IO_POS_PER_UM.get(platform)
    return 0.0 if not k or npins <= 0 else npins / (k * 4.0)


def die_for(platform: str, cell_area_um2: float,
            npins: int = 0) -> tuple[float, float, bool]:
    """(die_side, core_side, pin_floored) in um.

    core = max(area_derived, pin_required, pdn_floor). The pin term is applied
    only where a pin count is known; see pin_counts() for why that is sound.
    """
    eff = cell_area_um2 * area_allowance(platform)
    derived = math.sqrt(eff / UTILIZATION) if eff > 0 else 0.0
    need = pin_required_core(platform, npins)
    core = max(derived, need, PDN_FLOOR_UM[platform])
    core = math.ceil(core)
    return core + 2 * CORE_INSET_UM, core, need > max(derived, PDN_FLOOR_UM[platform])


def build(platform: str, pilot_dir: Path) -> tuple[dict, dict]:
    spec, skipped = {}, {}
    pins = pin_counts(pilot_dir)
    for f in glob.glob(str(pilot_dir / "runs" / "*" / "result.json")):
        name = os.path.basename(os.path.dirname(f))
        short = "__".join(name.split("__")[:-2])
        try:
            d = json.loads(Path(f).read_text())
        except Exception as exc:
            skipped[short] = f"unreadable result.json: {type(exc).__name__}"
            continue
        cells, area = d.get("cells"), d.get("area_um2")
        size_src = "pilot_synth_stat"
        # orfs_spec.py's harvest took the FIRST `<n> <area> ... cells` line of
        # synth_stat.txt. A hierarchical report lists one section per module
        # and the first is a SUBMODULE, so the die of such a design was sized
        # from part of it: the two nangate45 dev designs with hierarchical
        # reports (jsilicon tt_um_Jsilicon, FSM) got 1/1.87 and 1/1.47 of their
        # area and failed placement at 111% utilization. Read the top's own
        # section instead -- its line counts everything below it.
        top_size = top_section_size(Path(f).parent)
        if top_size and (not area or abs(top_size[1] - float(area)) > 1e-6):
            cells, area = top_size
            size_src = "pilot_synth_stat_top"
        # A design the pilot never synthesised has no pilot size. Before
        # 2026-10-21 that meant no spec at all and the platform default period
        # (2.0 ns on nangate45, unfrozen die) -- harmless while every such
        # design also failed before synthesis, but 67 nangate45 dev designs
        # only failed on driver defects that are now fixed, and they would have
        # entered the denominator under a period no peer got. The fallback is
        # the SAME quantity (synth_stat.txt's cells and chip area) measured by
        # the frozen BASELINE re-run, never by a repair arm, so the size class
        # is still fixed once and no repair can move its own target.
        if (not cells or not area) and short in BASELINE_SIZES:
            cells, area = BASELINE_SIZES[short]
            size_src = "baseline_rerun_synth_stat"
        # Last resort, for a design whose baseline cannot synthesise at all
        # (default parser rejects it, a header is listed standalone, an
        # async-load flop has no cell): its only synthesis is the repair arm's.
        # Labelled as such so the provenance is never mistaken for a baseline
        # measurement; it exists so a repair-arm win is judged under the same
        # period/die formula as every peer, not under the platform default.
        if (not cells or not area) and short in REPAIR_SIZES:
            cells, area = REPAIR_SIZES[short]
            size_src = "repair_arm_synth_stat"
        if not cells or not area:
            skipped[short] = f"no pilot size (cells={cells}, area={area})"
            continue
        npins, pins_src = pins.get(short, (0, None))
        die, core, pin_floored = die_for(platform, float(area), npins)
        # Per-design correction from the design's OWN place_pins refusal. The
        # derived k (1/(2*pitch) = 1.80 positions/um) is slightly optimistic:
        # over 68 nangate45 PPL-0024 logs the measured positions per um of die
        # perimeter run 1.666-1.783 (median 1.747; corners cost more on small
        # dies), so 6 pin-floored designs still came up 0.2-0.6% short. Lowering
        # k globally would also raise designs that already place their pins --
        # moving the baseline -- so the correction is applied only where a
        # PPL-0024 log exists: positions scale with perimeter, hence
        # needed = current * pins / available, plus 1%.
        corr = PPL_CORRECTIONS.get(short)
        if corr:
            cur_perim, n, avail = corr
            need_side = math.ceil(cur_perim * n / avail * 1.01 / 4.0)
            need_core = need_side - 2 * CORE_INSET_UM
            if need_core > core:
                core, die, pin_floored = need_core, need_core + 2 * CORE_INSET_UM, True
        spec[short] = {
            "cells": int(cells),
            "pilot_cell_area_um2": round(float(area), 3),
            "period_ns": period_for(platform, int(cells)),
            "die_side_um": die,
            "core_side_um": core,
            "core_inset_um": CORE_INSET_UM,
            "pdn_floored": core <= PDN_FLOOR_UM[platform],
            # recorded even when it does not bind, so a die can be re-derived
            # from the table alone without going back to the pilot's DEFs
            "pins": npins,
            "pins_source": pins_src,
            "size_source": size_src,
            "pin_floored": pin_floored,
        }
    return spec, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pilot-root", default="/proj/workarea/yangao",
                    help="directory holding n45_full / gf180_full / full_out")
    ap.add_argument("--out-dir", default="/proj/workarea/yangao/spec")
    ap.add_argument("--extra-pin-log-root", action="append", default=[],
                    help="a runs dir whose PPL-0024 logs may give a pin count the "
                         "pilot lacks (repeatable)")
    ap.add_argument("--baseline-size-root", action="append", default=[],
                    help="a baseline re-run's work/scratch dir whose synth_stat.txt "
                         "sizes designs the pilot never synthesised (repeatable)")
    ap.add_argument("--repair-size-root", action="append", default=[],
                    help="repair-arm work dirs whose synth_stat.txt sizes a design "
                         "the baseline cannot synthesise at all (repeatable)")
    ap.add_argument("--ppl-correction-root", action="append", default=[],
                    help="runs dir whose PPL-0024 refusals correct that design's die "
                         "from its own measured positions/perimeter (repeatable)")
    ap.add_argument("--platforms", default="nangate45,gf180,sky130hd")
    args = ap.parse_args(argv)
    EXTRA_PIN_LOG_ROOTS.extend(args.extra_pin_log_root)
    load_baseline_sizes(args.baseline_size_root)
    load_baseline_sizes(args.repair_size_root, REPAIR_SIZES)
    load_ppl_corrections(args.ppl_correction_root)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for plat in args.platforms.split(","):
        pilot = Path(args.pilot_root) / PILOT_ROOT[plat]
        if not pilot.is_dir():
            print(f"{plat}: 缺 {pilot}，跳过")
            continue
        spec, skipped = build(plat, pilot)
        floored = sum(1 for v in spec.values() if v["pdn_floored"])
        periods = sorted({v["period_ns"] for v in spec.values()})
        # Provenance, so a consumer can refuse a table that no longer describes
        # the frozen spec. The re-run chain's preflight compares `utilization`
        # against the value it is about to run with and will not start on a
        # mismatch -- without this stamp a table built at 40% would silently
        # hand out 40%-derived dies to a 50% run, and the dies are the whole
        # point of the table (they are explicit, so nothing downstream
        # recomputes them).
        spec["_meta"] = {
            "utilization": UTILIZATION,
            "core_inset_um": CORE_INSET_UM,
            "pdn_floor_um": PDN_FLOOR_UM[plat],
            "platform": plat,
            "designs": len(spec),
            "pdn_floored": floored,
            "skipped_no_pilot_size": len(skipped),
            "periods": periods,
            "pilot_root": str(pilot),
            "generated": _dt.date.today().isoformat(),
        }
        p = out / f"spec_{plat}.json"
        p.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n")
        print(f"{plat}: {len(spec) - 1} 份 -> {p}")
        print(f"   周期取值 {periods}   被 PDN 下限兜底 {floored} 份"
              f"   无试跑尺寸(跳过) {len(skipped)} 份")
        if skipped:
            (out / f"spec_{plat}.skipped.json").write_text(
                json.dumps(skipped, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
