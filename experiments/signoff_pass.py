#!/usr/bin/env python3
"""DRC signoff over every flow-completed design, using r2g's own checker.

The driver is ours (parallel, writes verdicts back into result.json); the check
itself is signoff-loop/scripts/flow/run_drc.sh, because that script carries
fixes the stock ORFS path does not: it invokes KLAYOUT_CMD directly instead of
through ORFS's klayout.sh wrapper (the wrapper left orphaned processes -- the
same class of leak that cost ~15 cores on this machine before the reaper was
added), it knows about the BEOL deck variant, and it runs under
_bounded_run.sh's process-group supervisor.

LVS is NOT attempted. r2g's run_lvs.sh refuses on harvested data, correctly:
`make lvs` would rebuild the physical stages, and a signoff checker must not
regenerate the layout it grades (RMD-P0-01). The harvest kept only 6_final.*,
not the 1_synth..5_route intermediates, so LVS needs either a flow re-run or
intermediates preserved from the start. Bypassing the guard would cost a full
re-run anyway and would grade a layout that no other funnel number describes.

GDS: designs harvested after the 2026-10-05 patch carry 6_final.gds; the rest
have it rebuilt from 6_final.def, which is faithful -- verified by running DRC
on an original and a rebuilt GDS of the same design and getting the same
verdict (0 violations both).
"""
from __future__ import annotations

import argparse, collections, json, os, re, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

IMAGE = "r2g-orfs:26Q3"
D = Path("/proj/workarea/yangao")
R2G = D / "r2g" / "signoff-loop"


# gf180's foundry DRC is not an ORFS .lydrc. It is ~30 PDK rule tables that
# assemble into one main.drc with its own switch interface, so run_drc.sh finds
# no deck (platforms/gf180 carries no drc/ dir and no KLAYOUT_DRC_FILE) and its
# `-rd in_gds=` / `-rd report_file=` call would not fit the deck either -- gf180
# wants input=/topcell=/report= plus the option switches below. Run the PDK deck
# directly, with the invocation measured on the 8 curve designs, the deep/tiling
# mode comparison and the 35,583-cell curve anchor, and normalise the output so
# the verdict parsing in one() stays platform-agnostic.
# The deck is assembled from the PDK rule tables by
# tools/assemble_gf180_drc_deck.sh (density.drc deliberately excluded --
# see that script). It is NOT a hand-made directory.
GF180_DECK = D / "gf180_deck_signoff"
GF180_SWITCHES = ("metal_top=9K", "mim_option=B", "metal_level=5LM",
                  "feol=true", "beol=true", "conn_drc=true",
                  # flat, not deep or tiling: deep never reached the rules in
                  # 2h (no hierarchy to share -- 1.82x MORE polygons than flat),
                  # and tiling is 4.3x faster but breaks M3.2b's whole-shape
                  # `not_interacting` predicate, inventing 6 false violations at
                  # 500um tiles and 97 at 250um where flat reports none.
                  "run_mode=flat", "offgrid=false", "thr=4")


def _drc_gf180(proj: Path, top: str, timeout: int) -> str:
    """Run the gf180 PDK deck; return a log carrying a normalised verdict line."""
    gds = proj / "backend" / "RUN_1" / "results" / "6_final.gds"
    out = proj / "drc"
    out.mkdir(parents=True, exist_ok=True)
    rd = " ".join(f"-rd {sw}" for sw in GF180_SWITCHES)
    r = subprocess.run(
        ["podman", "run", "--rm",
         # The read-only mounts ARE the frozen-layout guard on this path:
         # run_drc.sh's digest check (RMD-P0-01) does not run here, and a deck
         # or GDS the container cannot write cannot change under signoff.
         "-v", f"{GF180_DECK}:/deck:ro,Z",
         "-v", f"{gds.parent}:/src:ro,Z",
         "-v", f"{out}:/out:Z",
         IMAGE, "bash", "-lc",
         # The INNER timeout is load-bearing. With only the outer podman
         # killed, the container kept KLayout alive: a 7200s bound took 11,232s
         # of wall clock to actually stop (measured on the deep-mode run).
         # Killing inside the container lets podman exit at once.
         f"cd /deck && timeout --kill-after=60 {timeout} "
         f"klayout -b -r main.drc -rd input=/src/{gds.name} "
         f"-rd topcell={top} -rd report=/out/6_drc.lyrdb {rd}"],
        capture_output=True, text=True, timeout=timeout + 900)
    log = (r.stdout or "") + (r.stderr or "")
    if r.returncode in (124, 137):
        # the same wording run_drc.sh:303 prints, so one() classifies it alike
        return log + f"\nERROR: DRC timed out after {timeout}s\n"
    lyrdb = out / "6_drc.lyrdb"
    if r.returncode != 0 or not lyrdb.is_file():
        return log
    # KLayout writes the report database at the END, so a present file means
    # the run completed. Count <item>: that is one violation. run_drc.sh:311
    # counts <value> instead, and on gf180 the two agree exactly (checked on
    # 11 / 43,394 / 346,797-violation databases), so this number is comparable
    # with the other platforms' -- but <item> stays correct if a future item
    # ever carries more than one value.
    n = lyrdb.read_text(errors="replace").count("<item>")
    return log + f"\nDRC completed: {n} violations\n"


def one(run_dir: Path, projroot: Path, timeout: int,
        platform: str = "sky130hd") -> dict:
    out = {"design": run_dir.name, "drc_status": "skipped",
           "violations": None, "seconds": 0.0, "gds_rebuilt": False}
    try:
        res = json.loads((run_dir / "result.json").read_text())
    except Exception as exc:                                    # noqa: BLE001
        out.update(drc_status="unreadable_result", message=str(exc)[:120])
        return out
    if res.get("status") != "pass":
        out["drc_status"] = "not_a_pass"; return out
    if not (run_dir / "6_final.def").is_file():
        out["drc_status"] = "no_def"; return out
    try:
        cfg = (run_dir / "config.mk").read_text()
        top = re.search(r"DESIGN_NAME\s*=\s*(\S+)", cfg).group(1)
    except Exception:                                           # noqa: BLE001
        out["drc_status"] = "no_top"; return out

    t0 = time.time()
    proj = projroot / run_dir.name
    shutil.rmtree(proj, ignore_errors=True)
    (proj / "constraints").mkdir(parents=True)
    (proj / "backend" / "RUN_1" / "results").mkdir(parents=True)
    shutil.copy2(run_dir / "config.mk", proj / "constraints" / "config.mk")
    sdc = run_dir / "constraint.sdc"
    if sdc.is_file():
        shutil.copy2(sdc, proj / "constraints" / "constraint.sdc")
        # the harvested config.mk points SDC_FILE at the long-gone scratch tree
        p = proj / "constraints" / "config.mk"
        p.write_text(re.sub(r"(?m)^export SDC_FILE\s*=.*$",
                            f"export SDC_FILE = {proj}/constraints/constraint.sdc",
                            p.read_text()))
    shutil.copy2(run_dir / "6_final.def", proj / "backend" / "RUN_1" / "results")

    gds = run_dir / "6_final.gds"
    if gds.is_file():
        shutil.copy2(gds, proj / "backend" / "RUN_1" / "results")
    else:
        out["gds_rebuilt"] = True
        w = proj / "_rb"
        for sub in ("results", "logs", "objects", "reports"):
            (w / sub / platform / top / "base").mkdir(parents=True, exist_ok=True)
        shutil.copy2(run_dir / "6_final.def",
                     w / "results" / platform / top / "base")
        for n in ("config.mk", "constraint.sdc"):
            if (run_dir / n).is_file():
                shutil.copy2(run_dir / n, w / n)
        r = subprocess.run(
            ["podman", "run", "--rm", "-v", f"{w}:/work:Z", "-e", "NUM_CORES=4",
             IMAGE, "bash", "-lc",
             "cd $ORFS_ROOT/flow && make DESIGN_CONFIG=/work/config.mk "
             "WORK_HOME=/work do-gds"],
            capture_output=True, text=True, timeout=timeout)
        built = w / "results" / platform / top / "base" / "6_final.gds"
        if r.returncode != 0 or not built.is_file():
            out.update(drc_status="gds_failed",
                       message=(r.stderr or r.stdout)[-300:],
                       seconds=round(time.time() - t0, 1))
            shutil.rmtree(proj, ignore_errors=True)
            return out
        shutil.copy2(built, proj / "backend" / "RUN_1" / "results")
        shutil.rmtree(w, ignore_errors=True)

    variant = run_dir.name
    r = None
    try:
        if platform == "gf180":
            log = _drc_gf180(proj, top, timeout)
        else:
            log = None
            r = subprocess.run(
                ["podman", "run", "--rm",
                 "-v", f"{R2G}:/r2g:ro,Z", "-v", f"{projroot}:{projroot}:Z",
                 # run_drc.sh:200 reads DRC_TIMEOUT and defaults it to 7200, and
                 # this script never passed it. Raising only the subprocess bound
                 # below changes NOTHING: the inner r2g_bounded_run still kills
                 # KLayout at 7200s and the design is recorded drc_failed rather
                 # than running longer. Both layers move together, and the outer
                 # one gets headroom so the INNER bound fires first -- that path
                 # writes a log and a verdict; the outer one kills blind.
                 "-e", f"DRC_TIMEOUT={timeout}",
                 "-e", "NUM_CORES=4", IMAGE, "bash", "-lc",
                 # run_drc.sh's find over the ORFS logs dir aborts under `set -e`
                 # when the dir is absent, so create the tree it restages into.
                 f"mkdir -p $ORFS_ROOT/flow/logs/{platform}/{top}/{variant} "
                 f"$ORFS_ROOT/flow/reports/{platform}/{top}/{variant} "
                 f"$ORFS_ROOT/flow/objects/{platform}/{top}/{variant}; "
                 f"bash /r2g/scripts/flow/run_drc.sh '{proj}' {platform}"],
                capture_output=True, text=True, timeout=timeout + 900)
    except subprocess.TimeoutExpired:
        out.update(drc_status="timeout", seconds=round(time.time() - t0, 1))
        # proj is removed per design, so without this the log of a timed-out
        # design is the only copy and it goes with it -- which is what made
        # nangate45's timeout categories unknowable. The designs that still
        # time out at a raised bound are exactly the ones that need the
        # "too big" vs "stuck" classification, so keep their evidence.
        for _d in proj.rglob("6_drc.log"):
            try:
                (run_dir / "drc_signoff.log").write_text(
                    _d.read_text(errors="replace")[-200000:])
            except OSError:
                pass
            break
        for _lyrdb in proj.rglob("6_drc.lyrdb"):
            try:
                shutil.copy2(_lyrdb, run_dir / "6_drc.lyrdb")
            except OSError:
                pass
            break
        shutil.rmtree(proj, ignore_errors=True)
        return out

    if log is None:
        log = (r.stdout or "") + (r.stderr or "")
    (run_dir / "drc_signoff.log").write_text(log[-20000:])
    m = re.search(r"DRC completed:\s*(\d+)\s*violations", log)
    if m:
        out["violations"] = int(m.group(1))
        out["drc_status"] = "clean" if out["violations"] == 0 else "violations"
    elif ("no_drc_deck_for_platform" in log
          or "no KLayout DRC deck found" in log):
        # run_drc.sh writes the machine-readable reason into drc_result.json but
        # prints a DIFFERENT sentence to stderr, which is all this script sees,
        # so the old single-string test never matched and an unsupported
        # platform was reported as a checker failure. Latent until gf180 --
        # every other platform here ships a deck.
        out["drc_status"] = "no_deck"
    elif "DRC timed out after" in log:
        # run_drc.sh:303 on DRC_STATUS==124. Now that the inner bound fires
        # before the subprocess bound, a timeout RETURNS normally instead of
        # raising TimeoutExpired, so without this branch every timed-out
        # design would silently move from the `timeout` bucket into
        # `drc_failed` -- the funnel would show the bound change as a wave of
        # checker failures. run_drc.sh deliberately derives no violation
        # count from a killed run, so there is nothing else to salvage.
        out["drc_status"] = "timeout"
    else:
        out.update(drc_status="drc_failed", message=log[-300:])
    for lyrdb in proj.rglob("6_drc.lyrdb"):
        shutil.copy2(lyrdb, run_dir / "6_drc.lyrdb"); break
    out["seconds"] = round(time.time() - t0, 1)

    shutil.rmtree(proj, ignore_errors=True)
    return out


def record(run_dir: Path, out: dict) -> dict:
    """Write the verdict into result.json. Called for EVERY outcome.

    one() returns early on not_a_pass, gds_failed and timeout, and each of those
    used to bypass the write that lived at the end of it -- so a design that
    timed out in signoff was indistinguishable from one never attempted. Three
    sky130hd designs of 94K-107K cells hit the 7200 s cap that way: signoff.log
    said `timeout 7229s`, result.json said nothing, and the funnel counted 2156
    of 2159. Recording from the wrapper means no branch can skip it.
    """
    if out.get("drc_status") in (None, "", "not_a_pass", "unreadable_result"):
        return out          # nothing was graded; leave the record untouched
    try:
        cur = json.loads((run_dir / "result.json").read_text())
        cur["drc_status"] = out["drc_status"]
        cur["drc_violations_signoff"] = out.get("violations")
        cur["drc_seconds"] = out.get("seconds")
        cur["drc_checker"] = "r2g_run_drc.sh"
        fd, tmp = tempfile.mkstemp(dir=str(run_dir))
        with os.fdopen(fd, "w") as fh:
            json.dump(cur, fh, indent=1); fh.write("\n")
        os.replace(tmp, run_dir / "result.json")
    except Exception as exc:                                    # noqa: BLE001
        out["message"] = f"result.json update failed: {exc}"[:200]
    return out


def one_and_record(run_dir: Path, projroot: Path, timeout: int,
                   platform: str = "sky130hd") -> dict:
    return record(run_dir, one(run_dir, projroot, timeout, platform))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", default=str(D / "full_out" / "runs"))
    ap.add_argument("--projroot", default=str(D / "signoff_proj"))
    ap.add_argument("--out", default=str(D / "full_out" / "signoff.json"))
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--timeout", type=int, default=7200)
    ap.add_argument("--force", action="store_true")
    # Was hardcoded in seven places; sky130hd stays the default so every
    # existing invocation behaves identically.
    ap.add_argument("--platform", default="sky130hd")
    args = ap.parse_args()

    if not (R2G / "scripts" / "flow" / "run_drc.sh").is_file():
        sys.exit(f"r2g signoff-loop not found at {R2G}")
    args.runs = str(Path(args.runs).resolve())
    projroot = Path(args.projroot).resolve(); projroot.mkdir(parents=True, exist_ok=True)
    print(f"{time.strftime('%H:%M:%S')} 生效配置: platform={args.platform}  "
          f"DRC_TIMEOUT(内层)={args.timeout}s  外层={args.timeout + 900}s", flush=True)

    todo = []
    for d in sorted(Path(args.runs).iterdir()):
        f = d / "result.json"
        if not f.is_file():
            continue
        try: res = json.loads(f.read_text())
        except Exception: continue                              # noqa: BLE001
        if res.get("status") != "pass":
            continue
        if res.get("drc_status") and not args.force:
            continue
        todo.append(d)
    print(f"{time.strftime('%H:%M:%S')} r2g DRC signoff: {len(todo)} designs, "
          f"{args.workers} workers", flush=True)

    done = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(lambda d: one_and_record(d, projroot, args.timeout,
                                                 args.platform), todo):
            done.append(r)
            print(f"{time.strftime('%H:%M:%S')} {r['drc_status']:<14} "
                  f"viol={str(r['violations']):<6} {r['seconds']:>7.0f}s "
                  f"{'(gds rebuilt)' if r['gds_rebuilt'] else '':<14}"
                  f"{r['design'][:44]}", flush=True)
            Path(args.out).write_text(json.dumps(done, indent=1) + "\n")

    c = collections.Counter(r["drc_status"] for r in done)
    print(f"\n{time.strftime('%H:%M:%S')} done: {dict(c)}")
    v = [r["violations"] for r in done if r["violations"] is not None]
    if v:
        print(f"  DRC clean {sum(1 for x in v if x == 0)}/{len(v)}  "
              f"violations total {sum(v)}  worst {max(v)}")


if __name__ == "__main__":
    main()
