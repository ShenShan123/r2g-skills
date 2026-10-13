#!/usr/bin/env python3
"""Bridge the 2026-10 campaign's per-design `result.json` into the project shape
`knowledge/ingest_run.py` reads, so 7,491 campaign runs enter the learning loop.

The campaign drivers (chain45/chain7/chain180 -> orfs_baseline.py, signoff_pass.py,
lvs130.py) are a hand-rolled pipeline that writes one flat `result.json` per run.
ingest_run.py reads a PROJECT:

    <proj>/constraints/config.mk
    <proj>/reports/{ppa,drc,lvs,timing_check}.json
    <proj>/backend/RUN_1/stage_log.jsonl

This writes that projection (JSON only -- no DEF/GDS copies) into a parallel
tree, leaving the harvested runs untouched.

STATUS VOCABULARY is taken from what knowledge_db already recognises, NOT
invented: DRC_NOT_FAILED={None,clean,clean_beol,skipped},
LVS_NOT_FAILED={None,clean,skipped}, and the existing `stuck`/`incomplete`/
`crash`/`mismatch` labels already in run_violations. A label outside that set
is silently non-failing or silently failing, and either way the learner lies.

FOUR THINGS THIS BRIDGE REFUSES TO COPY VERBATIM, each measured on the corpus:

1. `drc_violations_signoff` is a `<value>` count, not a violation count.
   nangate45 reads item 6652 / value 46546 = exactly 7.00x because an antenna
   item carries 7 values. So violation counts and per-class counts are re-derived
   from `6_drc.lyrdb` `<item>`, and `<category>` is matched both quoted
   (`'METAL1.5'`) and bare (`METAL3_ANTENNA`).

2. nangate45's 198 `lvs_failed` are NOT LVS failures. 194 of them carry ONLY
   `No rule to make target` and a log ending in `LVS completed`: the frozen
   layout cannot rebuild the stage chain, so no reference CDL was ever produced,
   LVS extracted the layout, found nothing to compare against, and exited -- and
   the driver scored "no verdict" as a failure. 0 of 198 carry a crash signature.
   Those become `incomplete`/`no_reference_netlist` (an unverified LVS, which is
   correctly NOT a success), and only the 4 real `Pin count mismatch` runs become
   `fail`/`cdl_pin_count`. Calling 194 unverified runs "fail" would hand the
   learner 194 phantom LVS defects to invent recipes for.

3. gf180's 54 `no_log_harvested` are the same 54 designs that read `synth_error`
   on both other platforms (set intersection 54/54/54). They are relabelled
   `synth_error`, else the learner gains a fictional failure class and loses 54
   real synth failures.

4. `setup_wns` is filled from result.json's `worst_slack`, not its `wns`. `wns`
   is the violation amount (0.0 when timing is met) while the column feeds
   `wns_ns`/`finish_setup_ws` -- a worst-slack field. Using `wns` would record
   every met design as having exactly zero margin.
"""
import argparse, json, os, re, shutil, sys, glob, collections

CATEGORY_RE = re.compile(r"<category>'?([^'<]+?)'?</category>")

# campaign stage token -> the six canonical names _derive_orfs_status requires
STAGE_PREFIX = [("1_", "synth"), ("2_", "floorplan"), ("3_", "place"),
                ("4_", "cts"), ("5_", "route"), ("6_", "finish")]
CANON = ["synth", "floorplan", "place", "cts", "route", "finish"]
STAGE_ALIAS = {"synth": "synth", "final": "finish", "unknown": None, "": None}

FRONTEND_FAIL = {"parse_error", "1_1_yosys_canonicalize_error",
                 "1_2_yosys_error", "synth_error"}


def canon_stage(raw):
    """Map a campaign `stage` to one of the six canonical ORFS stage names."""
    s = (raw or "").strip()
    s = s[:-4] if s.endswith(".tmp") else s
    if s in STAGE_ALIAS:
        return STAGE_ALIAS[s]
    for pre, name in STAGE_PREFIX:
        if s.startswith(pre):
            return name
    return None


def lyrdb_classes(run_dir):
    """Per-class ITEM counts from 6_drc.lyrdb. Returns (total, {cls: n}).

    Counts <item>, never <value> -- see module docstring trap 1. Matches both
    the quoted and bare <category> spellings -- trap: a quoted-only regex
    collapses nangate45's nine antenna classes into one.
    """
    p = os.path.join(run_dir, "6_drc.lyrdb")
    if not os.path.isfile(p):
        return None, {}
    t = open(p, errors="replace").read()
    per = collections.Counter(CATEGORY_RE.findall(t))
    return t.count("<item>"), dict(per)


def lvs_verdict(run_dir, raw_status):
    """(status, mismatch_class) from the raw driver label plus the real log.

    See module docstring trap 2: the driver's `lvs_failed` conflates "LVS found a
    mismatch" with "LVS had no schematic to compare against".
    """
    if not raw_status:
        return None, None
    txt = ""
    for f in ("lvs_signoff.log", "netgen_lvs.log", "netgen_lvs.rpt"):
        p = os.path.join(run_dir, f)
        if os.path.isfile(p):
            txt += open(p, errors="replace").read()
    pin_count = "Pin count mismatch between circuit definition" in txt
    crash = re.search(r"Signal number|sort_circuit|net2id|Internal error.*cleanup", txt)
    unresolved = "No rule to make target" in txt
    real_cmp = re.search(r"Circuits match uniquely|Netlists don't match|"
                         r"Circuits don't match|netlist mismatch detected", txt)

    if raw_status == "clean":
        return "clean", None
    if raw_status == "lvs_failed":
        if pin_count:
            return "fail", "cdl_pin_count"
        if crash:
            return "crash", "klayout_crash"
        if unresolved and not real_cmp:
            # LVS ran, produced a layout netlist, had no reference -> no verdict
            return "incomplete", "no_reference_netlist"
        return "fail", None
    if raw_status == "mismatch":
        if crash:
            return "crash", "klayout_crash"
        return "mismatch", "netlist_mismatch" if real_cmp else None
    if raw_status == "pin_mismatch":
        return "mismatch", "top_pin_mismatch"
    if raw_status.startswith("error_portless"):
        return "incomplete", "portless_extraction"
    if raw_status == "timeout":
        return "incomplete", "lvs_timeout"
    return "unknown", None


def timing_tier(j):
    """In-vocabulary tier (clean/minor/moderate/severe/unconstrained/unknown)."""
    if j.get("timing_path_constrained") is False:
        return "unconstrained"
    ws = j.get("worst_slack")
    if not isinstance(ws, (int, float)):
        return "unknown"
    if ws >= 0:
        return "clean"
    period = float(j.get("period") or 0) or None
    frac = abs(ws) / period if period else None
    if frac is None:
        return "unknown"
    return "minor" if frac < 0.10 else ("moderate" if frac < 0.30 else "severe")


def stage_log(j, run_dir=None):
    """Faithful six-stage ledger. status 0=pass, 124=our own timeout, 1=fail.

    All wall time is attributed to the terminal stage: ingest only consumes
    sum(elapsed_s), and a fabricated per-stage split would be a worse lie than
    an honest total on one row.
    """
    secs = j.get("seconds")
    if j.get("status") == "pass":
        rows = [{"stage": s, "status": 0} for s in CANON]
        rows[-1]["elapsed_s"] = secs
        return rows
    fc = (j.get("failure_class") or "").strip()
    st = canon_stage(j.get("stage"))
    if st is None and run_dir:
        # sky130hd's driver recorded NO stage for 97 failures (96 of them
        # timeouts). All 97 still hold the ORFS per-stage logs, whose newest
        # filename names the stage the flow died at, so the stage is RECOVERED
        # from evidence rather than invented. Without it those 97 ingest as
        # orfs_status='unknown' and 97 real backend failures become invisible
        # as failures -- the "learner is blind to a whole failure class"
        # invariant, reached by omission instead of by a wrong label.
        st = canon_stage(_stage_from_logs(run_dir))
    if st is None:
        # gf180's `unknown` stage on the 54 relabelled synth failures, and the
        # 94 sky130hd rows whose driver recorded no stage at all.
        st = "synth" if fc in FRONTEND_FAIL else None
    if st is None:
        return []                       # honest: no stage evidence -> 'unknown'
    code = 124 if fc == "timeout" else 1
    rows = []
    for s in CANON:
        if s == st:
            rows.append({"stage": s, "status": code, "elapsed_s": secs})
            break
        rows.append({"stage": s, "status": 0})
    return rows


def _stage_from_logs(run_dir):
    """Newest ORFS per-stage log filename = the stage the flow died at."""
    logs = glob.glob(os.path.join(run_dir, "logs", "*", "*", "*", "*.log"))
    if not logs:
        return None
    newest = max(logs, key=os.path.getmtime)
    return os.path.basename(newest)[:-4]          # strip '.log'; '.tmp' handled by canon_stage


def project_for(run_dir, j, out_root, platform):
    name = f"{platform}__{os.path.basename(run_dir)}"
    # absolute: SDC_FILE below and every A/B arm copytree consume this path from
    # a different working directory than the one the bridge ran in.
    proj = os.path.abspath(os.path.join(out_root, name))
    for sub in ("constraints", "reports", os.path.join("backend", "RUN_1")):
        os.makedirs(os.path.join(proj, sub), exist_ok=True)

    cfg = os.path.join(run_dir, "config.mk")
    if os.path.isfile(cfg):
        shutil.copy2(cfg, os.path.join(proj, "constraints", "config.mk"))
    sdc = os.path.join(run_dir, "constraint.sdc")
    if os.path.isfile(sdc):
        shutil.copy2(sdc, os.path.join(proj, "constraints", "constraint.sdc"))
        # The harvested config.mk points SDC_FILE at `/work/constraint.sdc` -- the
        # CONTAINER path of the flow run that produced it, long gone. Every other
        # path in it (VERILOG_FILES, VERILOG_INCLUDE_DIRS) is an absolute host
        # path that still resolves, so rewriting this one line is the whole
        # difference between a project that can re-flow and one that cannot --
        # which is what an A/B arm needs of a subject. signoff45.py:124 does the
        # same rewrite for its own project copies.
        cfg_out = os.path.join(proj, "constraints", "config.mk")
        if os.path.isfile(cfg_out):
            txt = open(cfg_out, errors="ignore").read()
            txt = re.sub(r"(?m)^(\s*(?:override\s+)?export\s+SDC_FILE\s*=).*$",
                         lambda m: f"{m.group(1)} {proj}/constraints/constraint.sdc",
                         txt)
            open(cfg_out, "w").write(txt)

    W = lambda n, o: json.dump(o, open(os.path.join(proj, "reports", n), "w"),
                               indent=1, sort_keys=True)

    W("ppa.json", {
        "design": j.get("design"), "platform": platform,
        "summary": {
            # trap 4: worst_slack, not wns
            "timing": {"setup_wns": j.get("worst_slack"), "setup_tns": j.get("tns"),
                       "clock_period_ns": _f(j.get("period"))},
            "power": {}, "area": {"design_area_um2": j.get("area_um2")}},
        "geometry": {"instance_count": j.get("cells"),
                     "die_area_um2": j.get("area_um2")},
        "provenance": {"bridged_from": "result.json",
                       "driver": j.get("signoff_checker") or j.get("drc_checker")}})

    ds = j.get("drc_status")
    items, per = lyrdb_classes(run_dir)
    if ds == "clean":
        W("drc.json", {"status": "clean", "violations": 0, "categories": {}})
    elif ds == "violations":
        W("drc.json", {"status": "fail",
                       # re-derived ITEM count; result.json's number is 7x on nangate45
                       "violations": items if items is not None else None,
                       "violations_value_count_untrusted": j.get("drc_violations_signoff"),
                       "categories": {k: {"count": v} for k, v in per.items()}})
    elif ds == "timeout":
        # 15 gf180 designs timed out with a NON-EMPTY lyrdb holding 1,078 real
        # violations (CO.6a 1061, M4.2a 15, M4.1 2). KLayout wrote the database
        # 4 s BEFORE the bound fired, so those items are observed, not stale.
        # Dropping them hid 1,078 violations from the learner; claiming the
        # verdict complete would be a lie. So: status stays `stuck` (not in
        # DRC_NOT_FAILED, so still not a success) and the partial categories
        # ride along, flagged as a lower bound.
        o = {"status": "stuck", "violations": items,
             "violations_are_lower_bound": True,
             "categories": {k: {"count": v} for k, v in per.items()},
             "detail": "signoff DRC hit its wall-clock bound; the violations "
                       "found before the kill are real but the sweep is partial"}
        W("drc.json", o)
    elif ds:
        W("drc.json", {"status": "unknown", "categories": {}, "detail": str(ds)})

    lst, mcls = lvs_verdict(run_dir, j.get("lvs_status"))
    if lst:
        o = {"status": lst, "categories": {}}
        if mcls:
            o["mismatch_class"] = mcls
        if lst == "incomplete":
            o["detail"] = ("LVS produced a layout netlist but no reference "
                           "netlist existed, so no comparison was performed")
        o["driver_label_untrusted"] = j.get("lvs_status")
        W("lvs.json", o)

    W("timing_check.json", {"tier": timing_tier(j),
                            "setup_wns": j.get("worst_slack"),
                            "constrained": j.get("timing_path_constrained")})

    with open(os.path.join(proj, "backend", "RUN_1", "stage_log.jsonl"), "w") as f:
        for r in stage_log(j, run_dir):
            f.write(json.dumps(r) + "\n")
    return proj


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", default="/proj/workarea/yangao")
    ap.add_argument("--out", required=True, help="parallel project tree to write")
    ap.add_argument("--corpora", default="nangate45:n45_full,sky130hd:full_out,gf180:gf180_full")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    # the 54 gf180 `no_log_harvested` -> `synth_error` relabel (trap 3) needs the
    # other platforms' design names, so collect them first.
    synth_fail = set()
    for spec in a.corpora.split(","):
        plat, d = spec.split(":")
        for rj in glob.glob(os.path.join(a.runs_root, d, "runs", "*", "result.json")):
            try:
                j = json.load(open(rj))
            except Exception:
                continue
            if (j.get("failure_class") or "").strip() == "synth_error":
                synth_fail.add(_key(rj, plat))

    total = collections.Counter()
    for spec in a.corpora.split(","):
        plat, d = spec.split(":")
        n = relabel = 0
        for rj in sorted(glob.glob(os.path.join(a.runs_root, d, "runs", "*", "result.json"))):
            try:
                j = json.load(open(rj))
            except Exception:
                total[f"{plat}:unreadable"] += 1
                continue
            if (j.get("failure_class") or "").strip() == "no_log_harvested" \
                    and _key(rj, plat) in synth_fail:
                j["failure_class"] = "synth_error"
                j["stage"] = "synth"
                j["bridge_relabelled_from"] = "no_log_harvested"
                relabel += 1
            project_for(os.path.dirname(rj), j, a.out, plat)
            n += 1
            if a.limit and n >= a.limit:
                break
        total[f"{plat}:projects"] = n
        total[f"{plat}:relabelled_no_log_harvested"] = relabel
        print(f"{plat}: wrote {n} projects, relabelled {relabel}")
    print(dict(total))
    return 0


def _key(rj, plat):
    return re.sub(r"__" + plat + r"__p\d+$", "", os.path.basename(os.path.dirname(rj)))


if __name__ == "__main__":
    sys.exit(main())
