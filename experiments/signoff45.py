#!/usr/bin/env python3
"""DRC + LVS signoff for nangate45, using r2g's own checkers.

LVS is attempted here and was not on sky130hd, for two reasons that are both
fixed for this run:

  - The harvest now keeps 5_route.odb and 6_final.odb. r2g's run_lvs.sh refuses
    when `make lvs` would rebuild the physical stages (RMD-P0-01) -- a signoff
    checker must not regenerate the layout it grades -- and on sky130hd only
    6_final.* survived, so it correctly refused.
  - nangate45 ships a complete deck set (FreePDK45.lylvs plus the CDL), where
    sky130hd's KLayout LVS additionally needed r2g's slash and short-resistor
    CDL fixes. run_lvs.sh applies those only for sky130*, so nangate45 takes the
    plain path.

Worker count is 24, not 12: a DRC job was measured at ~0.3 cores (four in
parallel finish in the same wall time as one, 327-332 s against 314-328 s, and
load moved 42.45 -> 43.71), so the limit here is memory, not CPU.
"""
from __future__ import annotations

import argparse, collections, json, os, re, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

IMAGE = "r2g-orfs:26Q3"
D = Path("/proj/workarea/yangao")
R2G = D / "r2g" / "signoff-loop"
PLAT = "nangate45"


def stage(run_dir: Path, proj: Path, top: str) -> bool:
    """Lay the harvested artifacts out the way r2g's restage expects."""
    shutil.rmtree(proj, ignore_errors=True)
    (proj / "constraints").mkdir(parents=True)
    res = proj / "backend" / "RUN_1" / "results"
    res.mkdir(parents=True)
    shutil.copy2(run_dir / "config.mk", proj / "constraints" / "config.mk")
    sdc = run_dir / "constraint.sdc"
    if sdc.is_file():
        shutil.copy2(sdc, proj / "constraints" / "constraint.sdc")
        p = proj / "constraints" / "config.mk"
        p.write_text(re.sub(r"(?m)^export SDC_FILE\s*=.*$",
                            f"export SDC_FILE = {proj}/constraints/constraint.sdc",
                            p.read_text()))
    got = False
    for n in ("6_final.def", "6_final.gds", "6_final.odb", "6_final.v",
              "6_final.sdc", "6_final.spef", "5_route.odb"):
        if (run_dir / n).is_file():
            shutil.copy2(run_dir / n, res); got = got or n == "6_final.gds"
    return got


# The patched deck and the in-container bounds are resolved HERE, by the
# script, and never left to whoever launches it. Twice a hand-launched sign-off
# ran without R2G_DRC_DECK_OVERRIDE and without R2G_DRC_TIMEOUT: the first time
# it cost 88 verdicts to a 2h cutoff, the second time a 17-hour "6h re-run"
# graded 76 designs with the stock deck under the same 2h cutoff and wrote
# drc_failed over their baseline result.json. Raising --timeout did not help,
# because --timeout was only the OUTER subprocess bound; run_drc.sh reads
# DRC_TIMEOUT and defaults it to 7200 when it is absent.
#
# So: the deck defaults to the patched one and a missing deck is FATAL rather
# than a silent fall-back to stock (the old `and Path(_ovr).is_file()` dropped
# a mistyped override without a word); the inner DRC bound follows --timeout;
# the inner LVS bound is never lowered below run_lvs.sh's own size-scaled value;
# and the outer bound sits above the inner ones, so the checker's own timeout
# fires and is classified, instead of the subprocess being killed from outside.
PATCHED_DECK = D / "drcfix" / "FreePDK45_tiled.lydrc"
OUTER_GRACE_S = 900


def resolve_deck() -> Path | None:
    ovr = os.environ.get("R2G_DRC_DECK_OVERRIDE", "")
    if ovr:
        if not Path(ovr).is_file():
            raise SystemExit(f"R2G_DRC_DECK_OVERRIDE={ovr} does not exist -- "
                             "refusing to fall back to the stock deck")
        return Path(ovr)
    if PATCHED_DECK.is_file():
        return PATCHED_DECK
    if os.environ.get("R2G_ALLOW_STOCK_DECK") == "1":
        return None
    raise SystemExit(f"patched deck {PATCHED_DECK} missing. The stock FreePDK45 "
                     "deck does not finish CONTACT.3 inside any usable bound. "
                     "Set R2G_ALLOW_STOCK_DECK=1 only for an equivalence check.")


def lvs_bound(cells: int, timeout: int) -> int:
    """run_lvs.sh's own size table, floored at --timeout. Never lower it: the
    pilot's LVS ran with this table, so cutting a huge design below 28800 would
    lose verdicts the pilot could earn."""
    auto = (28800 if cells > 250000 else 21600 if cells > 100000
            else 14400 if cells > 50000 else 5400)
    return max(auto, timeout)


DECK = None  # set in main()


def check(kind: str, proj: Path, top: str, variant: str, timeout: int,
          cells: int = 0):
    """Run r2g's run_drc.sh / run_lvs.sh in the container."""
    _deck_mount = []
    if kind == "drc" and DECK is not None:
        _deck_mount = ["-v", f"{DECK}:/r2g_toolchain/OpenROAD-flow-scripts/"
                             f"flow/platforms/{PLAT}/drc/FreePDK45.lydrc:ro,Z"]
    inner = timeout if kind == "drc" else lvs_bound(cells, timeout)
    _bound = (["-e", f"DRC_TIMEOUT={os.environ.get('R2G_DRC_TIMEOUT', inner)}"]
              if kind == "drc" else
              ["-e", f"LVS_TIMEOUT={os.environ.get('R2G_LVS_TIMEOUT', inner)}"])
    outer = int(os.environ.get("R2G_DRC_TIMEOUT" if kind == "drc" else
                               "R2G_LVS_TIMEOUT", inner)) + OUTER_GRACE_S
    return subprocess.run(
        ["podman", "run", "--rm", "-v", f"{R2G}:/r2g:ro,Z",
         "-v", f"{proj.parent}:{proj.parent}:Z", "-e", "NUM_CORES=4",
         *_bound, *_deck_mount, IMAGE,
         "bash", "-lc",
         # these scripts `find` under the ORFS logs dir and abort under `set -e`
         # when it is missing, so create the tree their restage writes into
         f"mkdir -p $ORFS_ROOT/flow/logs/{PLAT}/{top}/{variant} "
         f"$ORFS_ROOT/flow/reports/{PLAT}/{top}/{variant} "
         f"$ORFS_ROOT/flow/objects/{PLAT}/{top}/{variant}; "
         f"bash /r2g/scripts/flow/run_{kind}.sh '{proj}' {PLAT}"],
        capture_output=True, text=True, timeout=outer)


def one(run_dir: Path, projroot: Path, timeout: int) -> dict:
    out = {"design": run_dir.name, "drc_status": "skipped", "drc_violations": None,
           "lvs_status": "skipped", "seconds": 0.0}
    try:
        res = json.loads((run_dir / "result.json").read_text())
    except Exception as exc:                                     # noqa: BLE001
        out["drc_status"] = "unreadable_result"; out["message"] = str(exc)[:120]
        return out
    if res.get("status") != "pass":
        out["drc_status"] = out["lvs_status"] = "not_a_pass"; return out
    try:
        top = re.search(r"DESIGN_NAME\s*=\s*(\S+)",
                        (run_dir / "config.mk").read_text()).group(1)
    except Exception:                                            # noqa: BLE001
        out["drc_status"] = "no_top"; return out

    t0 = time.time()
    proj = projroot / run_dir.name
    if not stage(run_dir, proj, top):
        out["drc_status"] = out["lvs_status"] = "no_gds"
        shutil.rmtree(proj, ignore_errors=True)
        return out

    try:
        r = check("drc", proj, top, run_dir.name, timeout)
        log = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"DRC completed:\s*(\d+)\s*violations", log)
        if m:
            out["drc_violations"] = int(m.group(1))
            out["drc_status"] = "clean" if out["drc_violations"] == 0 else "violations"
        elif re.search(r"DRC timed out after \d+s", log):
            # run_drc.sh's own bound fired. With the outer bound now above the
            # inner one this is the normal way a slow design ends, and filing it
            # as drc_failed made 76 timeouts read as checker errors.
            out["drc_status"] = "timeout"; out["drc_log"] = log[-300:]
        else:
            out["drc_status"] = "drc_failed"; out["drc_log"] = log[-300:]
    except subprocess.TimeoutExpired:
        out["drc_status"] = "timeout"

    # Keep what signoff_pass.py keeps (its lines 118, 127-128). Without these
    # a violating design yields a count and nothing else: the per-category
    # analysis that traced sky130hd's m3.2 back to the PDN reads the lyrdb, and
    # 2,156 of them exist on that platform against 0 here. Preserve on timeout
    # as well -- the partial run-local log names the rule it stalled in, which
    # is how CONTACT.3 was found, and the project dir is deleted below.
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

    try:
        r = check("lvs", proj, top, run_dir.name, timeout,
                  int(res.get("cells") or 0))
        log = (r.stdout or "") + (r.stderr or "")
        (run_dir / "lvs_signoff.log").write_text(log[-20000:])
        # Order matters: the refusal cases first, so a run r2g DECLINED to grade
        # never reads as a verdict about the layout.
        if "would rebuild physical stages" in log:
            out["lvs_status"] = "needs_intermediates"
        elif re.search(r"CONGRATULATIONS! Netlists match|LVS CLEAN . netlists match",
                       log):
            out["lvs_status"] = "clean"
        elif re.search(r"Netlists don.t match", log):
            out["lvs_status"] = "mismatch"
        elif re.search(r"LVS timed out after \d+s", log):
            out["lvs_status"] = "timeout"
        else:
            # run_lvs.sh records why it declined in its own json; that is more
            # trustworthy than guessing from the log tail.
            reason = ""
            try:
                j = json.loads((proj / "lvs" / "lvs_result.json").read_text())
                reason = j.get("reason") or j.get("status") or ""
            except Exception:                                    # noqa: BLE001
                pass
            out["lvs_status"] = f"error_{reason}" if reason else "lvs_failed"
            out["lvs_log"] = log[-300:]
    except subprocess.TimeoutExpired:
        out["lvs_status"] = "timeout"

    out["seconds"] = round(time.time() - t0, 1)

    # Keep LVS's input chain and its result BEFORE the project dir is deleted.
    # Added 2026-10-17: three separate forensics attempts had to reconstruct or
    # abandon, because `make lvs` builds
    #     6_final.v -> 6_final.cdl -> 6_final_concat.cdl (+ platform std-cell
    #     CDL) -> 6_lvs.lvsdb
    # inside this project dir and none of it survived the rmtree. 6_lvs.lvsdb is
    # LVS's equivalent of DRC's 6_drc.lyrdb -- the per-device result -- so
    # without it an LVS verdict cannot be re-examined at all: the layout is
    # frozen, so `make lvs` refuses to regenerate (RMD-P0-01), and re-deriving
    # is exactly what this is meant to make unnecessary.
    # EXPERIMENT_RUNBOOK_zh.md settles LVS equivalence by the md5 of 6_final.cdl
    # and the extracted netlist, which needs both to exist.
    for _pat in ("6_final.cdl", "6_final_concat.cdl", "6_lvs.lvsdb",
                 "6_cdl.log", "6_lvs.log"):
        for _src in proj.rglob(_pat):
            try:
                shutil.copy2(_src, run_dir / _src.name)
            except OSError:
                pass
            break

    shutil.rmtree(proj, ignore_errors=True)
    return out


def record(run_dir: Path, out: dict) -> dict:
    """Write the verdict into result.json. Called for EVERY outcome.

    one() returns early on unreadable_result, not_a_pass, no_top and no_gds, and
    the write used to sit at the end of it -- so `no_gds` and `no_top`, which
    ARE verdicts about a design, were dropped and those designs looked
    unattempted. Three sky130hd designs vanished from the funnel's denominator
    exactly that way (the log said `timeout 7229s`, result.json said nothing).
    Recording from the wrapper means no branch can skip it.

    The DRC and LVS timeouts here are caught inside one() and fall through, so
    they were already being recorded; this closes the remaining four paths.
    """
    if out.get("drc_status") in (None, "", "not_a_pass", "unreadable_result",
                                 "skipped"):
        return out          # nothing was graded; leave the record untouched
    try:
        cur = json.loads((run_dir / "result.json").read_text())
        cur.update(drc_status=out["drc_status"],
                   drc_violations_signoff=out.get("drc_violations"),
                   lvs_status=out.get("lvs_status"),
                   signoff_seconds=out.get("seconds"),
                   signoff_checker="r2g_run_drc.sh+run_lvs.sh")
        fd, tmp = tempfile.mkstemp(dir=str(run_dir))
        with os.fdopen(fd, "w") as fh:
            json.dump(cur, fh, indent=1); fh.write("\n")
        os.replace(tmp, run_dir / "result.json")
    except Exception as exc:                                     # noqa: BLE001
        out["message"] = f"result.json update failed: {exc}"[:200]
    return out


def one_and_record(run_dir: Path, projroot: Path, timeout: int) -> dict:
    return record(run_dir, one(run_dir, projroot, timeout))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", default=str(D / "n45_full" / "runs"))
    ap.add_argument("--projroot", default=str(D / "n45_signoff_proj"))
    ap.add_argument("--out", default=str(D / "n45_full" / "signoff.json"))
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--timeout", type=int, default=21600,
                    help="per-design sign-off bound in seconds. 6h, matching "
                         "STAGE_TIMEOUT. The old 7200 default cost 88 nangate45 "
                         "verdicts on a hand-launched run, so the right value "
                         "lives here rather than in whoever remembers to pass it.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    # podman rejects a relative bind-mount source ("invalid container path ...,
    # must be an absolute path"), and that failure surfaced as drc_failed /
    # lvs_failed on every design of a batch -- a launch mistake recorded as a
    # verdict. Resolve both paths here so a relative argument cannot do that.
    args.projroot = str(Path(args.projroot).resolve())
    args.runs = str(Path(args.runs).resolve())
    global DECK
    DECK = resolve_deck()
    import hashlib
    _sha = (hashlib.sha256(DECK.read_bytes()).hexdigest()[:12]
            if DECK else "STOCK")
    print(f"{time.strftime('%H:%M:%S')} 生效配置: deck={DECK.name if DECK else 'STOCK'} "
          f"sha={_sha}  DRC内层={os.environ.get('R2G_DRC_TIMEOUT', args.timeout)}s  "
          f"LVS内层=max(按规模,{args.timeout})s  外层=内层+{OUTER_GRACE_S}s", flush=True)

    if not (R2G / "scripts" / "flow" / "run_lvs.sh").is_file():
        sys.exit(f"r2g signoff-loop not found at {R2G}")
    projroot = Path(args.projroot); projroot.mkdir(parents=True, exist_ok=True)

    todo = []
    for d in sorted(Path(args.runs).iterdir()):
        f = d / "result.json"
        if not f.is_file():
            continue
        try: res = json.loads(f.read_text())
        except Exception: continue                               # noqa: BLE001
        if res.get("status") != "pass":
            continue
        if res.get("drc_status") and not args.force:
            continue
        todo.append(d)
    print(f"{time.strftime('%H:%M:%S')} nangate45 signoff: {len(todo)} designs, "
          f"{args.workers} workers", flush=True)

    done = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(lambda d: one_and_record(d, projroot, args.timeout), todo):
            done.append(r)
            print(f"{time.strftime('%H:%M:%S')} drc={r['drc_status']:<12} "
                  f"lvs={r['lvs_status']:<18} {r['seconds']:>7.0f}s "
                  f"{r['design'][:42]}", flush=True)
            Path(args.out).write_text(json.dumps(done, indent=1) + "\n")

    print(f"\n  DRC {dict(collections.Counter(r['drc_status'] for r in done))}")
    print(f"  LVS {dict(collections.Counter(r['lvs_status'] for r in done))}")


if __name__ == "__main__":
    main()
