#!/usr/bin/env python3
"""Baseline runner for the certified-handoff corpus: RTL -> ORFS -> signoff.

One stage, not two. The corpus subset being run is pure Verilog, which the ORFS
yosys reads natively, so the design's own sources go straight into the flow.
Routing them through the package's synth.ys to get a generic netlist first would
synthesise twice and change what the flow sees; the historical baselines on this
project fed the original RTL the same way.

The flow runs inside a container because the execution host is RHEL 8 with glibc
2.28 and the ORFS binaries need 2.35. The image carries the same binaries the
bare-metal runs use, so results from either are comparable rather than merely
similar.

Disk is handled by harvesting what the analysis needs and deleting the design's
working tree straight away. A measured design costs about 47 MB while running
and a few hundred kB afterwards, so the corpus is not a disk problem -- but the
brakes stay because the volume is shared, a handful of designs are two orders of
magnitude larger than the median, and a run that fills the disk corrupts every
design still in flight rather than just the one that overflowed.

Clock period is an argument, not a constant. A single period cannot serve two
technologies: sky130hd closes comfortably at 10 ns while the same number leaves
a 45 nm library with no timing pressure at all. Sweeping it is what turns yield
from a number into a curve, and what manufactures the graded timing failures the
repair experiments need.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class Result:
    design: str
    platform: str
    period: str
    status: str = "pending"
    stage: str = ""
    failure_class: str = ""
    seconds: float = 0.0
    peak_disk_bytes: int = 0
    wns: float | None = None
    tns: float | None = None
    worst_slack: float | None = None
    drc_violations: int | None = None
    clock_kind: str = ""
    clock_port: str = ""
    cells: int | None = None
    message: str = ""
    # Which PDN policy this run used, so the corpus stays auditable: a design
    # that needed the core floor is not comparable to one that did not.
    pdn_fallback: bool = False
    core_floor_um: float | None = None
    pdn_insufficient: bool = False
    log_tail: str = ""
    area_um2: float | None = None
    timing_path_constrained: bool | None = None


def free_gb(path: Path) -> float:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 1e9


def dir_bytes(path: Path) -> int:
    total = 0
    for root, _d, files in os.walk(path, onerror=lambda e: None):
        for n in files:
            try:
                total += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return total


class DiskGuard:
    """Reads real free space each cycle rather than tracking our own writes.

    The volume is shared: 229 GB disappeared from one of these machines in a
    week with none of our work running, so a budget computed from what this
    process has written would not describe the disk it is writing to.
    """

    def __init__(self, root: Path, pause_gb: float, stop_gb: float, log) -> None:
        self.root, self.pause_gb, self.stop_gb, self.log = root, pause_gb, stop_gb, log
        self.stop = threading.Event()
        self._paused = False

    def may_dispatch(self) -> bool:
        gb = free_gb(self.root)
        if gb < self.stop_gb:
            if not self.stop.is_set():
                self.log(f"HARD STOP: {gb:.0f} GB free below --stop-gb {self.stop_gb}")
                self.stop.set()
            return False
        if gb < self.pause_gb:
            if not self._paused:
                self.log(f"paused: {gb:.0f} GB free below --pause-gb {self.pause_gb}")
                self._paused = True
            return False
        if self._paused:
            self.log(f"resumed: {gb:.0f} GB free")
            self._paused = False
        return True


# A fixed list of clock-port spellings cannot cover 2,453 unrelated
# repositories: a first pass matched none in 32% of designs, and an unmatched
# clock becomes a virtual clock, which constrains no path and reports WNS 0.
# That reads as "closed timing" in every summary while meaning "nothing was
# timed", so the SDC asks the design what its clock is called instead, and the
# result records which branch fired so the two can never be pooled.
SDC = """set cand [get_ports -quiet {{{ports}}}]
if {{[llength $cand] == 0}} {{
  foreach p [all_inputs] {{
    if {{[regexp -nocase {{(^|_)(clk|clock)([0-9_]*$|_)}} [get_name $p]]}} {{
      lappend cand $p
    }}
  }}
}}
if {{[llength $cand] > 0}} {{
  create_clock -name core_clk -period {period} [lindex $cand 0]
  puts "R2G_CLOCK real [get_name [lindex $cand 0]]"
}} else {{
  create_clock -name virtual_clk -period {period}
  puts "R2G_CLOCK virtual none"
}}
set_clock_uncertainty 0.0 [get_clocks *]
"""
CLOCK_PORTS = ("clk clock i_clk clk_i core_clk CLK CLOCK HCLK clock_i sys_clk "
               "clk_in clkin clk_sys aclk ACLK wb_clk_i clock_in pclk PCLK "
               "clock0 clk0 clkA clk_a ref_clk refclk mclk MCLK fclk sclk")


def write_config(design_dir: Path, meta: dict, work: Path, platform: str,
                 period: str, utilization: str,
                 core_floor_um: float | None = None) -> None:
    files, incs = [], []
    for f in meta.get("source_files") or []:
        files.append(str((design_dir / f["file"]).resolve()))
    for i in meta.get("include_dirs") or []:
        incs.append(str((design_dir / i).resolve()))
    defines = " ".join(meta.get("defines") or [])
    (work / "constraint.sdc").write_text(SDC.format(ports=CLOCK_PORTS, period=period))
    cfg = [f'export DESIGN_NAME = {meta["top_module"]}',
           f"export PLATFORM = {platform}",
           f"export VERILOG_FILES = {' '.join(files)}",
           "export SDC_FILE = /work/constraint.sdc",
           f"export CORE_UTILIZATION = {utilization}",
           "export ABC_AREA = 1",
           "export SYNTH_MEMORY_MAX_BITS = 131072"]
    if incs:
        cfg.append(f"export VERILOG_INCLUDE_DIRS = {' '.join(incs)}")
    if defines:
        cfg.append(f"export VERILOG_DEFINES = {defines}")
    if core_floor_um:
        # sky130hd's default PDN needs a core wide enough for one met4/met5
        # pitch plus the strap offset: 13.57 um offset + 15.2 um total strap
        # = 28.8 um (platforms/sky130hd/pdn.tcl). A design whose
        # utilization-derived core is narrower cannot host the default grid at
        # all -- PDN-0185 aborts floorplan. Replacing the derived core with an
        # explicit square of core_floor_um per side is the minimum intervention:
        # utilization becomes an outcome instead of an input, and only for the
        # designs that cannot be built otherwise.
        #
        # It never packs a design tighter than the original policy. The error
        # fires only when the derived core is under ~900 um^2, i.e. cell area
        # under 180 um^2, which is at most 19% of the 935 um^2 the floor
        # actually yields after site snapping.
        margin = 2.0
        die = core_floor_um + 2 * margin
        cfg = [c for c in cfg if not c.startswith("export CORE_UTILIZATION")]
        cfg.append(f"export DIE_AREA  = 0 0 {die:g} {die:g}")
        cfg.append(f"export CORE_AREA = {margin:g} {margin:g} "
                   f"{core_floor_um + margin:g} {core_floor_um + margin:g}")
    # A platform's PDN strategy is a `?=` default, so a design-config export
    # overrides it. gf180's stock strategy asks for multi-cut via arrays
    # (-max_columns 5, -split_cuts) which merge into 0.772 x 1.028 rectangles
    # and violate GF's V2.1/V3.1 "every Via2/Via3 edge exactly 0.26 um". Being
    # able to swap the strategy is what makes that testable. The container sees
    # only /work and the package, and config.mk already lives in /work, so the
    # cfg is staged beside it.
    _pdn_cfg = os.environ.get("R2G_PDN_CFG", "")
    if _pdn_cfg and Path(_pdn_cfg).is_file():
        shutil.copy(_pdn_cfg, work / "pdn_override.cfg")
        cfg.append("export PDN_TCL = /work/pdn_override.cfg")
    # Same mechanism for the tap strategy: gf180 ships -distance 100 while its
    # DF.13/DF.14_MV rules cap the distance to the nearest substrate tap at
    # 15 um for 5 V devices (sky130hd uses 14 against its own rule).
    _tap_tcl = os.environ.get("R2G_TAPCELL_TCL", "")
    if _tap_tcl and Path(_tap_tcl).is_file():
        shutil.copy(_tap_tcl, work / "tapcell_override.tcl")
        cfg.append("export TAPCELL_TCL = /work/tapcell_override.tcl")
    # Arbitrary extra make assignments, newline-separated. Platform defaults
    # use `?=`, so a design-config export overrides them -- which is how a
    # one-off knob (SKIP_ANTENNA_REPAIR_POST_DRT, SYNTH_HDL_FRONTEND, ...) gets
    # tested without another bespoke flag.
    for _line in os.environ.get("R2G_EXTRA_MK", "").splitlines():
        if _line.strip():
            cfg.append(_line.strip())
    (work / "config.mk").write_text("\n".join(cfg) + "\n")


FAILURE_PATTERNS = (
    (r"can't open input file|No such file or directory.*\.v\b", "source_missing"),
    (r"is not part of the design|cannot find module|referenced in module", "black_box"),
    (r"syntax error|parse error|Parser error", "parse_error"),
    (r"Can't find top module|No top module", "missing_top"),
    (r"found multiple top|Multiple modules", "ambiguous_top"),
    (r"ERROR: \[STA|no paths found|Clock .* not found", "sdc_error"),
    (r"Detailed routing failed|DRT-|routing congestion", "route_failed"),
    (r"placement .*failed|GPL-|DPL-", "place_failed"),
    (r"out of memory|std::bad_alloc|Killed", "oom"),
)


def classify(log_text: str, stage: str) -> str:
    tail = log_text[-20000:]
    for pattern, label in FAILURE_PATTERNS:
        if re.search(pattern, tail, re.IGNORECASE):
            return label
    if stage == "unknown":
        # Distinct from any real stage failure: this one is "we kept no
        # evidence", which is a harvest bug to fix, not a design defect to
        # learn a recipe for.
        return "no_log_harvested"
    return f"{stage}_error" if stage else "flow_error"


def last_stage(work: Path) -> str:
    # No log means no evidence, and "synth" would be a guess presented as a
    # fact -- which is how 54 runs came to be filed under synth_error without
    # anything to support it. Say unknown and let classify() mark it.
    logs = sorted((work / "logs").rglob("*.log"))
    return logs[-1].stem if logs else "unknown"


NUM = r"(-?[0-9]+\.?[0-9]*)"


def _kill_container(name: str) -> None:
    """Stop the container itself, not just the client that started it.

    Killing the `podman run` process leaves the container detached and running:
    the design's openroad keeps all its threads busy while the worker slot has
    already moved on. Every abort path must come through here.
    """
    subprocess.run(["podman", "rm", "-f", name], capture_output=True, check=False)


def read_metrics(work: Path, res: Result) -> None:
    """Pull the few numbers the calibration needs out of ORFS's own reports.

    clock_kind is read from the SDC's own announcement rather than inferred, so
    a design whose clock was never found is visible as such instead of appearing
    as a design that met timing.
    """
    try:
        m = re.search(r"R2G_CLOCK (\w+) (\S+)", (work / "flow.log").read_text(errors="replace"))
        if m:
            res.clock_kind, res.clock_port = m.group(1), m.group(2)
    except OSError:
        pass
    for rpt in (work / "reports").rglob("6_finish.rpt"):
        text = rpt.read_text(errors="replace")
        for key, attr in (("wns max", "wns"), ("tns max", "tns"),
                          ("worst slack max", "worst_slack")):
            m = re.search(re.escape(key) + r"\s+" + NUM, text)
            if m:
                setattr(res, attr, float(m.group(1)))
        if re.search(r"worst slack max\s+INF", text):
            res.timing_path_constrained = False
        elif res.worst_slack is not None:
            res.timing_path_constrained = True
        break
    for rpt in (work / "reports").rglob("5_route_drc.rpt"):
        try:
            res.drc_violations = sum(1 for ln in rpt.read_text(errors="replace")
                                     .splitlines() if ln.startswith("violation"))
        except OSError:
            pass
        break
    for stat in (work / "reports").rglob("synth_stat.txt"):
        stat_text = stat.read_text(errors="replace")
        m = re.search(r"^\s*(\d+)\s+(\S+)\s+\d+\s+\S+\s+cells\s*$",
                      stat_text, re.M)
        if m:
            try:
                res.area_um2 = float(m.group(2))
            except ValueError:                                 # area printed as "-"
                pass
        else:
            m = re.search(r"Number of cells:\s+(\d+)", stat_text)
        if m:
            res.cells = int(m.group(1))
        break


def harvest(work: Path, dest: Path) -> None:
    """Keep the evidence, drop the gigabytes.

    The reports and the final views are what the funnel, the QoR table and the
    graph builder read. The per-stage ODB and routing databases around them are
    reproducible by rerunning the flow and are what makes a design cost 47 MB.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for sub in ("reports", "logs"):
        src = work / sub
        if src.is_dir():
            shutil.copytree(src, dest / sub, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("*.odb", "*.gds"))
    # flow.log is the container's own stdout and the ONLY record of a failure
    # that happens before ORFS creates logs/. Leaving it out made 54 sky130hd
    # runs unattributable: no log, so last_stage() defaulted to "synth" and the
    # class became "synth_error" by default rather than by diagnosis.
    for name in ("config.mk", "constraint.sdc", "flow.log"):
        if (work / name).is_file():
            shutil.copy2(work / name, dest / name)
    # 6_final.gds and 6_final.v were dropped from this list originally, which
    # made signoff impossible to add afterwards: Magic/KLayout DRC needs the GDS
    # and LVS needs the netlist behind 6_final_concat.cdl. Both turned out to be
    # reconstructible from 6_final.def (ORFS streams the GDS out of the DEF, and
    # read_lef+read_def+write_cdl rebuilds the CDL), but regenerating costs
    # 5-28 s per design and the originals cost a few MB each -- the whole
    # harvested corpus is 2.5 GB on a 35 TB volume, so space was never the
    # constraint. Keeping them makes a later signoff pass a straight read.
    for pattern in ("6_final.def", "6_final.spef", "6_final.sdc", "6_final.v",
                    "6_final.gds", "1_synth.v",
                    # LVS needs the layout's own database, not a rebuilt one:
                    # r2g's run_lvs.sh refuses when `make lvs` would regenerate
                    # the physical stages (RMD-P0-01), which is exactly what
                    # happened on sky130hd because only 6_final.* was kept.
                    # 5_route.odb and 6_final.odb are ~90 MB each here, so the
                    # whole corpus costs about 500 GB of a 35 TB volume.
                    "5_route.odb", "6_final.odb"):
        for src in work.rglob(pattern):
            shutil.copy2(src, dest / src.name)
            break


def _run_one_once(design_dir: Path, meta: dict, platform: str, period: str,
                  args, guard: DiskGuard, log,
                  core_floor_um: float | None = None,
                  ignore_done: bool = False) -> Result:
    name = design_dir.name
    res = Result(design=name, platform=platform, period=period,
                 core_floor_um=core_floor_um,
                 pdn_fallback=core_floor_um is not None)
    tag = f"{name}__{platform}__p{period}"
    work = Path(args.scratch) / tag
    dest = Path(args.out) / "runs" / tag
    if (dest / "result.json").is_file() and not (args.force or ignore_done):
        # A record that hit PDN-0185 but carries no core floor is the first half
        # of a retry that never got its second half -- the first attempt writes
        # result.json in `finally`, before the fallback attempt overwrites it, so
        # a campaign killed between the two leaves exactly this state. Treating
        # it as done would silently keep a design in the fail bucket that the
        # declared fallback was supposed to rescue, so it counts as unfinished.
        try:
            prior = json.loads((dest / "result.json").read_text())
        except Exception:                                      # noqa: BLE001
            prior = {}
        unfinished_retry = (prior.get("pdn_insufficient")
                            and not prior.get("pdn_fallback")
                            and getattr(args, "pdn_core_floor_um", 0.0))
        if not unfinished_retry:
            return Result(design=name, platform=platform, period=period,
                          status="skipped_done")

    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        write_config(design_dir, meta, work, platform, period, args.utilization,
                     core_floor_um=core_floor_um)
        # A stale container under this name can only be a leak from an earlier
        # attempt (the tag is unique per design/platform/period), so clearing it
        # first makes the runner self-healing rather than failing on the name.
        subprocess.run(["podman", "rm", "-f", tag],
                       capture_output=True, check=False)
        cmd = ["podman", "run", "--rm", "--name", tag,
               "-v", f"{work}:/work:Z",
               "-v", f"{args.package}:{args.package}:ro,Z",
               "-e", f"NUM_CORES={args.cores_per_design}",
               "-e", f"OMP_NUM_THREADS={args.cores_per_design}",
               args.image, "bash", "-lc",
               "cd $ORFS_ROOT/flow && make DESIGN_CONFIG=/work/config.mk WORK_HOME=/work"]
        proc = subprocess.Popen(cmd, stdout=open(work / "flow.log", "w"),
                                stderr=subprocess.STDOUT, start_new_session=True)
        quota = args.max_design_gb * 1e9
        deadline = time.time() + args.timeout
        while proc.poll() is None:
            time.sleep(10)
            size = dir_bytes(work)
            res.peak_disk_bytes = max(res.peak_disk_bytes, size)
            if size > quota:
                proc.kill(); proc.wait()
                _kill_container(tag)
                res.status, res.failure_class = "fail", "oversize"
                res.message = f"work tree passed {args.max_design_gb} GB"
                res.stage = last_stage(work)
                read_metrics(work, res)
                return res
            if time.time() > deadline:
                proc.kill(); proc.wait()
                _kill_container(tag)
                res.status, res.failure_class = "fail", "timeout"
                # Which stage it was stuck in and how big the design had become
                # are the only things that make a timeout explainable later. The
                # 2026-10-04 audit found this bucket carried neither, so the 21
                # timeouts then on record could not be attributed to anything
                # without re-running them for three hours each.
                res.stage = last_stage(work)
                read_metrics(work, res)
                try:
                    res.log_tail = (work / "flow.log").read_text(
                        errors="replace")[-4000:]
                except Exception:                              # noqa: BLE001
                    pass
                return res
            if guard.stop.is_set():
                proc.kill(); proc.wait()
                _kill_container(tag)
                res.status, res.failure_class = "fail", "aborted_disk"
                return res
        text = (work / "flow.log").read_text(errors="replace")
        read_metrics(work, res)
        if proc.returncode != 0:
            res.status, res.stage = "fail", last_stage(work)
            res.failure_class = classify(text, res.stage)
            # The work tree is deleted in `finally`, and harvest() does not keep
            # flow.log, so without this a failure can only be diagnosed by
            # reproducing it. Keep enough to read the actual tool error.
            res.log_tail = text[-4000:]
            res.pdn_insufficient = "PDN-0185" in text
            return res
        res.status, res.stage = "pass", "final"
        # Closing timing is a separate question from the flow completing, and the
        # funnel needs them apart: a design that routed but missed its target is
        # a repair candidate, not a build failure.
        if res.wns is not None and res.wns < 0:
            res.failure_class = "timing_not_met"
        return res
    except Exception as exc:                                   # noqa: BLE001
        res.status, res.failure_class = "error", "harness"
        res.message = str(exc)[:300]
        return res
    finally:
        res.seconds = round(time.time() - t0, 1)
        res.peak_disk_bytes = max(res.peak_disk_bytes, dir_bytes(work))
        harvest(work, dest)
        (dest / "result.json").write_text(json.dumps(asdict(res), indent=1) + "\n")
        shutil.rmtree(work, ignore_errors=True)
        log(f"{res.status:7} {platform:10} p={period:5} "
            f"wns={('%7.3f' % res.wns) if res.wns is not None else '      -'} "
            f"{res.failure_class or '':18} {res.peak_disk_bytes/1e6:6.0f}MB "
            f"{res.seconds:6.0f}s  {name[:46]}")


def run_one(design_dir: Path, meta: dict, platform: str, period: str,
            args, guard: DiskGuard, log) -> Result:
    """One design, retried once under the declared PDN core floor if needed.

    Rule declared 2026-10-04, before any design was re-run: a flow that aborts
    on PDN-0185 (core too narrow for the platform's default power straps) is
    retried exactly once with an explicit square core of --pdn-core-floor-um per
    side. 32 um is the smallest value on a measured ladder that satisfies both
    met4 and met5 on sky130hd -- 30 um still fails met5 -- and the full flow
    completes to 6_final.gds at that size.

    Nothing else changes, and a design that never hits PDN-0185 is untouched, so
    the fallback cannot alter runs that were already succeeding. `pdn_fallback`
    on the result records which policy produced it.
    """
    res = _run_one_once(design_dir, meta, platform, period, args, guard, log)
    floor = getattr(args, "pdn_core_floor_um", 0.0)
    if (floor and res.status == "fail" and res.pdn_insufficient
            and res.core_floor_um is None):
        log(f"{'retry':7} {platform:10} p={period:5} "
            f"PDN-0185 -> core floor {floor:g}um                 "
            f"            {design_dir.name[:46]}")
        res = _run_one_once(design_dir, meta, platform, period, args, guard, log,
                            core_floor_um=floor, ignore_done=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--package", required=True)
    ap.add_argument("--select", required=True, help="file of design names")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--image", default="r2g-orfs:26Q3")
    ap.add_argument("--platforms", default="sky130hd,nangate45")
    ap.add_argument("--periods", required=True,
                    help="per platform, e.g. sky130hd=20,10,5,3,2,1;nangate45=10,5,2,1,0.5,0.3")
    ap.add_argument("--designs", type=int, default=0)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--cores-per-design", type=int, default=8)
    ap.add_argument("--utilization", default="20")
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--pause-gb", type=float, default=2000)
    ap.add_argument("--stop-gb", type=float, default=800)
    ap.add_argument("--max-design-gb", type=float, default=30)
    ap.add_argument("--pdn-core-floor-um", type=float, default=32.0,
                    help="retry a PDN-0185 abort with an explicit square core "
                         "of this many um per side; 0 disables the fallback")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    Path(args.scratch).mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        with lock:
            print(line, flush=True)
            with (out / "run.log").open("a") as fh:
                fh.write(line + "\n")

    periods = {}
    for chunk in args.periods.split(";"):
        plat, _, vals = chunk.partition("=")
        periods[plat.strip()] = [v.strip() for v in vals.split(",") if v.strip()]

    names = [ln.strip() for ln in Path(args.select).read_text().splitlines() if ln.strip()]
    if args.designs:
        names = names[: args.designs]

    jobs = []
    pkg = Path(args.package)
    for name in names:
        ddir = pkg / "certified" / name
        try:
            meta = json.loads((ddir / "DESIGN.json").read_text())
        except Exception:                                      # noqa: BLE001
            continue
        for plat in args.platforms.split(","):
            for period in periods.get(plat.strip(), []):
                jobs.append((ddir, meta, plat.strip(), period))

    guard = DiskGuard(Path(args.scratch), args.pause_gb, args.stop_gb, log)
    ver = subprocess.run(["podman", "run", "--rm", args.image, "bash", "-lc",
                          "openroad -version; yosys -V | head -1; klayout -v"],
                         capture_output=True, text=True, timeout=300)
    (out / "toolchain.json").write_text(json.dumps(
        {"image": args.image, "versions": ver.stdout.strip().splitlines(),
         "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "config": vars(args)}, indent=1) + "\n")
    for line in ver.stdout.strip().splitlines():
        log(f"tool {line}")
    log(f"{len(names)} designs x {len(jobs)//max(1,len(names))} conditions = {len(jobs)} runs, "
        f"{args.workers} workers, free {free_gb(Path(args.scratch)):.0f} GB")

    qlock = threading.Lock()
    done: list[Result] = []

    def worker() -> None:
        while not guard.stop.is_set():
            while not guard.may_dispatch():
                if guard.stop.is_set():
                    return
                time.sleep(30)
            with qlock:
                if not jobs:
                    return
                job = jobs.pop(0)
            r = run_one(*job, args, guard, log)
            with qlock:
                done.append(r)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = [asdict(r) for r in done]
    (out / "results.json").write_text(json.dumps(rows, indent=1) + "\n")
    by_cond: dict[str, dict] = {}
    for r in done:
        key = f"{r.platform}@{r.period}"
        c = by_cond.setdefault(key, {"n": 0, "pass": 0, "timing_not_met": 0, "fail": 0})
        c["n"] += 1
        if r.status == "pass":
            c["pass"] += 1
            if r.failure_class == "timing_not_met":
                c["timing_not_met"] += 1
        elif r.status != "skipped_done":
            c["fail"] += 1
    for key, c in sorted(by_cond.items()):
        met = c["pass"] - c["timing_not_met"]
        log(f"{key:22} flow_pass {c['pass']}/{c['n']}  timing_met {met}/{c['n']}  "
            f"fail {c['fail']}")
    (out / "summary.json").write_text(json.dumps(by_cond, indent=1) + "\n")


if __name__ == "__main__":
    main()
