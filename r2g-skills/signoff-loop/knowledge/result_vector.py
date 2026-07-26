#!/usr/bin/env python3
"""The ONE global signoff result-vector + non-regression comparator (RMD3-P0-01,
failure-patterns.md #58).

Why this module exists: on sky130hs SHA-256 (2026-07-24 pilot) a `density_relief`
repair improved its TARGET check (DRC 10->8) while regressing route 0->32 and
breaking LVS — and the live fix loop recorded `applied`, which ingest mapped to
`win`. The A/B judge already had a global-regression veto
(engineer_loop._ab_global_regression), but the live path used a target-only
comparison, so the two learning paths disagreed about what "win" means. This
module is the single comparator BOTH paths use:

  * capture(project)          — versioned result vector from reports/*.json,
                                each signal BOUND to the layout it graded
                                (run_tag / gds_sha256), so a stale report can
                                never masquerade as post-repair evidence.
  * compare(pre, post, target)— live comparator: `gate="regression"` iff a
                                measured, fresh signal flipped good->bad (or
                                counts materially worsened). Unknown/stale
                                post-signals are REPORTED, never guessed.
  * compare_status_rows(a, b) — the ingested-run-row comparator used by the A/B
                                judge (exact behavior of the former
                                engineer_loop._ab_global_regression).
  * new_class_regression(...) — the shared new-DRC-class materiality policy
                                (formerly only in _ab_new_drc_regression).

Doctrine (matches the A/B veto code): the comparator only fires on a POSITIVE
measured good->bad flip; anything unreadable/unknown/stale carries NO signal and
never fabricates a regression. Callers therefore fail SAFE on comparator errors
(behave as before) — but a *measured* regression is a hard verdict override.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

VECTOR_VERSION = 1

# Cross-check severity vocabularies (moved here from engineer_loop so the live
# loop and the A/B judge share ONE definition; engineer_loop re-imports them).
# Values are the ingested ones observed in runs/run_violations; anything outside
# good/bad (skipped/unknown/''/None) carries NO signal and never drives a veto.
LVS_GOOD, LVS_BAD = {"clean"}, {"fail", "crash", "mismatch", "incomplete", "stale"}
DRC_GOOD, DRC_BAD = {"clean", "clean_beol"}, {"fail", "failed", "stuck"}
TIER_RANK = {"clean": 0, "minor": 1, "moderate": 2, "severe": 3, "unconstrained": 3}
# ppa.json vocabulary (extract_ppa): complete|partial|fail. The runs table
# stores pass|fail|partial (ingest maps complete->pass) — accept both spellings.
ORFS_GOOD = {"complete", "pass"}
ORFS_BAD = {"partial", "fail"}


def _now() -> str:
    try:
        from knowledge_db import now_local     # invariant 32: the ONE stamp
        return now_local()
    except Exception:
        import datetime
        return datetime.datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _newest_run(project: Path) -> Path | None:
    backend = project / "backend"
    try:
        runs = [d for d in backend.iterdir()
                if d.name.startswith("RUN_") and d.is_dir()]
    except OSError:
        return None
    return max(runs, key=lambda d: d.stat().st_mtime, default=None)


def _layout_identity(project: Path) -> dict:
    """{run_tag, def_digest} of the newest backend RUN ('' / None when absent).
    The DEF digest matches fix_signoff.sh _layout_digest (pilot P1-1)."""
    run = _newest_run(project)
    ident: dict = {"run_tag": run.name if run else None, "def_digest": None}
    if run is None:
        return ident
    for rel in ("results/6_final.def", "final/6_final.def"):
        p = run / rel
        if p.exists():
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            ident["def_digest"] = h.hexdigest()
            break
    return ident


def _norm_classes(categories: dict | None) -> dict[str, int]:
    """Fold a drc.json categories vector to {normalized_class: count>0}, using
    the SAME normalizer as the symptom index / A/B veto."""
    out: dict[str, int] = {}
    if not categories:
        return out
    try:
        import symptom as _symptom
        norm = _symptom.normalize_class
    except Exception:
        norm = lambda k: k                                  # noqa: E731
    for k, v in categories.items():
        try:
            c = int((v or {}).get("count") or 0)
        except Exception:
            c = 0
        if c > 0:
            nk = norm(k) or k
            out[nk] = out.get(nk, 0) + c
    return out


def capture(project_dir: str | os.PathLike) -> dict:
    """Versioned global result vector for a project's CURRENT reports/, with each
    signal's freshness bound to the newest backend RUN (run_tag match; drc/lvs
    also accept a gds_sha256 match so a re-extracted report over the same GDS
    stays fresh). NEVER raises — absent reports read as unmeasured signals."""
    project = Path(project_dir)
    reports = project / "reports"
    layout = _layout_identity(project)
    tag = layout.get("run_tag")

    def _bound(rep: dict | None, *keys: str) -> bool:
        """Report binds to the current layout via run_tag/backend_run."""
        if rep is None or tag is None:
            return False
        return any(rep.get(k) == tag for k in keys)

    drc = _read_json(reports / "drc.json")
    lvs = _read_json(reports / "lvs.json")
    route = _read_json(reports / "route.json")
    rcx = _read_json(reports / "rcx.json")
    ppa = _read_json(reports / "ppa.json")
    timing = _read_json(reports / "timing_check.json")

    ppa_fresh = False
    if ppa is not None and tag:
        run_dir = ppa.get("run_dir") or ""
        ppa_fresh = os.path.basename(str(run_dir).rstrip("/")) == tag
    timing_fresh = False
    if timing is not None and ppa_fresh:
        try:                       # check_timing re-measures ppa.json: fresh iff
            timing_fresh = ((reports / "timing_check.json").stat().st_mtime
                            >= (reports / "ppa.json").stat().st_mtime)
        except OSError:
            timing_fresh = False
    rcx_fresh = bool(rcx and tag and f"/{tag}/" in str(rcx.get("spef_file") or ""))

    signals = {
        "orfs": {
            "status": (ppa or {}).get("orfs_status"),
            "fail_stage": (ppa or {}).get("orfs_fail_stage"),
            "last_stage": (ppa or {}).get("orfs_last_stage"),
            "fresh": ppa_fresh,
        },
        "route": {
            "status": (route or {}).get("status"),
            "total_violations": (route or {}).get("total_violations"),
            "fresh": _bound(route, "backend_run", "run_tag"),
        },
        "drc": {
            "status": (drc or {}).get("status"),
            "total_violations": (drc or {}).get("total_violations"),
            "classes": _norm_classes((drc or {}).get("categories")),
            "fresh": _bound(drc, "run_tag", "backend_run"),
            "gds_sha256": (drc or {}).get("gds_sha256"),
        },
        "lvs": {
            "status": (lvs or {}).get("status"),
            "mismatch_count": (lvs or {}).get("mismatch_count"),
            "mismatch_class": (lvs or {}).get("mismatch_class"),
            "fresh": _bound(lvs, "run_tag", "backend_run"),
            "gds_sha256": (lvs or {}).get("gds_sha256"),
        },
        "timing": {
            "tier": (timing or {}).get("tier"),
            "wns_ns": (timing or {}).get("wns_ns", (timing or {}).get("wns")),
            "clock_period_ns": (timing or {}).get(
                "clock_period_ns", (timing or {}).get("clock_period")),
            "fresh": timing_fresh,
        },
        "rcx": {
            "status": (rcx or {}).get("status"),
            "fresh": rcx_fresh,
        },
    }
    # drc/lvs fallback freshness: same GDS bytes re-graded (report carries the
    # gds_sha256 but no run_tag — e.g. an extract re-run over an older report).
    # Only hash the GDS when a report NEEDS the fallback (hashing is not free).
    need_gds = any(signals[c]["gds_sha256"] and not signals[c]["fresh"]
                   for c in ("drc", "lvs"))
    if need_gds and tag:
        run = project / "backend" / tag
        for rel in ("results/6_final.gds", "final/6_final.gds"):
            p = run / rel
            if p.exists():
                h = hashlib.sha256()
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                gds_sha = h.hexdigest()
                for c in ("drc", "lvs"):
                    if signals[c]["gds_sha256"] == gds_sha:
                        signals[c]["fresh"] = True
                break
    for c in ("drc", "lvs"):
        signals[c].pop("gds_sha256", None)

    return {
        "vector_version": VECTOR_VERSION,
        "captured_at": _now(),
        "project": str(project),
        "layout": layout,
        "signals": signals,
    }


def _measured(sig: dict, value_key: str) -> bool:
    return bool(sig.get("fresh")) and sig.get(value_key) is not None


def new_class_regression(pre_classes: dict[str, int],
                         post_classes: dict[str, int]) -> str | None:
    """Shared new-DRC-class materiality policy (P0-13): classes present post but
    not pre veto ONLY when their combined count exceeds the pre TOTAL residual
    (post is materially worse overall, not merely a benign residual that became
    visible). Returns 'new_drc_class:<classes>' or None."""
    new = {k: c for k, c in (post_classes or {}).items()
           if c > 0 and k not in (pre_classes or {})}
    if new and sum(new.values()) > sum((pre_classes or {}).values()):
        return "new_drc_class:" + ",".join(sorted(new))
    return None


def compare(pre: dict, post: dict, target: str) -> dict:
    """Global non-regression comparator over two capture() vectors.

    gate="regression" iff any HARD regression is measured on BOTH sides with
    fresh bindings; unknown/stale post-signals land in `unknowns` (reported,
    never guessed — and never a regression). `target` names the check the
    repair aimed at (drc|lvs|route|timing): a timing target legitimately edits
    the clock period, so the constraint-relaxation test is skipped for it."""
    regressions: list[str] = []
    unknowns: list[str] = []
    ps, qs = pre.get("signals") or {}, post.get("signals") or {}

    def sig(vec: dict, name: str) -> dict:
        return vec.get(name) or {}

    # ORFS completion: complete -> partial/fail is a hard regression.
    a, b = sig(ps, "orfs"), sig(qs, "orfs")
    if _measured(a, "status") and a["status"] in ORFS_GOOD:
        if _measured(b, "status") and b["status"] in ORFS_BAD:
            regressions.append(f"orfs_regression:{a['status']}->{b['status']}")
        elif not _measured(b, "status"):
            unknowns.append("orfs:unmeasured_post")

    # Route: violation-count increase, or status good->bad when counts absent.
    a, b = sig(ps, "route"), sig(qs, "route")
    if _measured(a, "total_violations"):
        if _measured(b, "total_violations"):
            if b["total_violations"] > a["total_violations"]:
                regressions.append(
                    f"route_regression:{a['total_violations']}->{b['total_violations']}")
        elif _measured(b, "status") and a.get("status") == "clean" \
                and b.get("status") not in (None, "clean"):
            regressions.append(f"route_regression:{a.get('status')}->{b.get('status')}")
        else:
            unknowns.append("route:unmeasured_post")

    # DRC: status flip, count increase, and the shared new-class materiality veto.
    a, b = sig(ps, "drc"), sig(qs, "drc")
    if _measured(a, "status") and a["status"] in DRC_GOOD:
        if _measured(b, "status") and b["status"] in DRC_BAD:
            regressions.append(f"drc_regression:{a['status']}->{b['status']}")
        elif not _measured(b, "status"):
            unknowns.append("drc:unmeasured_post")
    if _measured(a, "total_violations") and _measured(b, "total_violations"):
        if b["total_violations"] > a["total_violations"]:
            regressions.append(
                f"drc_regression:{a['total_violations']}->{b['total_violations']}")
        nc = new_class_regression(a.get("classes") or {}, b.get("classes") or {})
        if nc:
            regressions.append(nc)

    # LVS: good->bad status flip, or mismatch-count increase.
    a, b = sig(ps, "lvs"), sig(qs, "lvs")
    if _measured(a, "status") and a["status"] in LVS_GOOD:
        if _measured(b, "status") and b["status"] in LVS_BAD:
            regressions.append(f"lvs_regression:{a['status']}->{b['status']}")
        elif not _measured(b, "status"):
            unknowns.append("lvs:unmeasured_post")
    if (_measured(a, "mismatch_count") and _measured(b, "mismatch_count")
            and b["mismatch_count"] > a["mismatch_count"]):
        regressions.append(
            f"lvs_regression:{a['mismatch_count']}->{b['mismatch_count']}")

    # Timing: tier worsening across the good(<=minor)/bad(>=moderate) boundary
    # ('unconstrained' ranks severe: losing the clock constraint disables the check).
    a, b = sig(ps, "timing"), sig(qs, "timing")
    ra = TIER_RANK.get(a.get("tier") or "") if _measured(a, "tier") else None
    rb = TIER_RANK.get(b.get("tier") or "") if _measured(b, "tier") else None
    if ra is not None and ra <= 1:
        if rb is not None and rb >= 2:
            regressions.append(f"timing_regression:{a['tier']}->{b['tier']}")
        elif rb is None:
            unknowns.append("timing:unmeasured_post")
    # Protected constraint: a NON-timing repair must not buy its win by relaxing
    # the clock (timing strategies legitimately edit the period and are judged
    # on tier). 0.1% tolerance absorbs float formatting noise.
    if target != "timing":
        pa, pb = a.get("clock_period_ns"), b.get("clock_period_ns")
        if (_measured(a, "clock_period_ns") and _measured(b, "clock_period_ns")
                and isinstance(pa, (int, float)) and isinstance(pb, (int, float))
                and pb > pa * 1.001):
            regressions.append(f"constraint_relaxed:clock_period_ns:{pa}->{pb}")

    # RCX: completed extraction must not turn into a failed one.
    a, b = sig(ps, "rcx"), sig(qs, "rcx")
    if (_measured(a, "status") and str(a["status"]) in ("complete", "ok")
            and _measured(b, "status")
            and str(b["status"]) in ("fail", "failed", "error", "crash")):
        regressions.append(f"rcx_regression:{a['status']}->{b['status']}")

    return {
        "vector_version": VECTOR_VERSION,
        "target": target,
        "gate": "regression" if regressions else "ok",
        "regressions": regressions,
        "unknowns": unknowns,
    }


def compare_status_rows(a: dict, b: dict) -> str | None:
    """Ingested-run-row comparator for the A/B judge — the exact policy of the
    former engineer_loop._ab_global_regression. `a`/`b` are dicts with keys
    orfs/drc/lvs/tier (values from runs.orfs_status/drc_status/lvs_status/
    timing_tier). Returns a comma-joined veto string, or None."""
    vetoes = []
    if a.get("orfs") == "pass" and b.get("orfs") == "fail":
        vetoes.append("orfs_regression:pass->fail")
    if a.get("lvs") in LVS_GOOD and b.get("lvs") in LVS_BAD:
        vetoes.append(f"lvs_regression:{a['lvs']}->{b['lvs']}")
    elif a.get("lvs") in LVS_GOOD and not b.get("lvs"):
        vetoes.append("check_missing:lvs")
    if a.get("drc") in DRC_GOOD and b.get("drc") in DRC_BAD:
        vetoes.append(f"drc_regression:{a['drc']}->{b['drc']}")
    ra, rb = TIER_RANK.get(a.get("tier") or ""), TIER_RANK.get(b.get("tier") or "")
    if ra is not None and rb is not None and ra <= 1 and rb >= 2:
        vetoes.append(f"timing_regression:{a['tier']}->{b['tier']}")
    return ",".join(vetoes) if vetoes else None


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Global signoff result vector: capture / compare (RMD3-P0-01)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture", help="capture a project's result vector")
    cap.add_argument("project")
    cap.add_argument("--out", help="write vector JSON here (default stdout)")
    cmp_ = sub.add_parser("compare", help="compare two captured vectors")
    cmp_.add_argument("pre")
    cmp_.add_argument("post")
    cmp_.add_argument("--target", required=True,
                      choices=["drc", "lvs", "route", "timing", "both"])
    cmp_.add_argument("--out", help="write comparison JSON here (default stdout)")
    args = ap.parse_args(argv)
    if args.cmd == "capture":
        vec = capture(args.project)
        text = json.dumps(vec, indent=1, sort_keys=True)
    else:
        pre, post = _read_json(Path(args.pre)), _read_json(Path(args.post))
        if pre is None or post is None:
            print("ERROR: unreadable vector file", file=sys.stderr)
            return 1
        text = json.dumps(compare(pre, post, args.target), indent=1, sort_keys=True)
    if getattr(args, "out", None):
        tmp = Path(args.out + ".tmp")
        tmp.write_text(text + "\n")
        tmp.replace(args.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
