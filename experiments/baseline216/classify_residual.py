#!/usr/bin/env python3
"""Unified failure classification for the 2026-10 campaign, with what the EXISTING
recipe matcher would propose for each class and what history says it achieves.

Answers "can the recipes we already have fix this corpus?" without burning a flow,
by joining three things the store already holds:

  1. the residual landscape  -- run_violations / runs, per platform, latest run only
  2. what the matcher does   -- diagnose_signoff_fix.build_plan is CALLED per class
                                with a real config from that class, so the proposal
                                is the live dispatch rather than a guess about it
  3. what it achieved before -- fix_trajectories outcomes keyed by the same class

Why (2) is called rather than reasoned about: _drc_plan's dispatch is not a lookup.
`clean/clean_beol/skipped` yields no strategy; `stuck`/`timeout` yields
`drc_<status>_tooling_out_of_v1_scope` and NO strategy -- so a DRC timeout is
explicitly outside repair scope, not an uncovered class; an all-antenna category set
routes to _antenna_strategies while any non-antenna member routes to
_routing_drc_strategies; and _antenna_precondition can drop the diode strategies
entirely when the platform's antenna model is provably unusable. Several of those
branches depend on the design's own config.mk (CORE_UTILIZATION presence, PLATFORM),
which is why a representative project per class is loaded instead of a synthetic one.

The output splits the corpus into exactly four buckets, which is the thing the next
step needs:

  covered_proven    a strategy is proposed AND history shows it resolving this class
  covered_untried   a strategy is proposed, no history for this class yet  -> run it
  proposed_failed   a strategy is proposed, history shows only abandoned/improved
                      -> run it, but expect to need a new candidate
  out_of_scope      the matcher declines by design (tooling/timeouts)
"""
import argparse, collections, json, os, sqlite3, sys

def _load_matcher(skill_root):
    sys.path.insert(0, os.path.join(skill_root, "scripts", "reports"))
    sys.path.insert(0, os.path.join(skill_root, "knowledge"))
    import diagnose_signoff_fix as dsf
    return dsf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--skill-root", default="/proj/workarea/yangao/r2g/signoff-loop")
    ap.add_argument("--since", default="2026-10-01")
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()
    dsf = _load_matcher(a.skill_root)
    c = sqlite3.connect(a.db)
    q = lambda s, p=(): c.execute(s, p).fetchall()

    LATEST = ("r.run_id = (SELECT r2.run_id FROM runs r2 "
              "WHERE r2.project_path = r.project_path "
              "ORDER BY r2.ingested_at DESC, r2.run_id DESC LIMIT 1)")

    # ── 1. residual landscape: latest run per project, campaign only ──────────
    rows = q(f"SELECT r.platform, rv.drc_status, rv.drc_categories_json, rv.lvs_status, "
             f"rv.lvs_mismatch_class, r.orfs_status, r.orfs_fail_stage, r.project_path, "
             f"r.design_family, r.cell_count "
             f"FROM run_violations rv JOIN runs r USING(run_id) "
             f"WHERE r.ingested_at>? AND {LATEST} "
             f"AND r.project_path NOT LIKE '%_abA_%' AND r.project_path NOT LIKE '%_abB_%'",
             (a.since,))

    # class -> {platform: [designs]}, plus a representative project per class
    cls = collections.defaultdict(lambda: collections.Counter())
    rep = {}
    status_of = {}
    for (plat, ds, cj, ls, lmc, orfs, fstage, path, fam, cells) in rows:
        if orfs in ("fail", "tool_crash"):
            k = ("orfs_stage", fstage or "unknown")
        elif ls and ls not in ("clean", "skipped", None):
            k = ("lvs", lmc or ls)
        elif ds in ("fail", "failed"):
            cats = json.loads(cj or "{}") or {}
            dom = max(cats, key=lambda x: (cats[x] or {}).get("count") or 0) if cats else "unknown"
            k = ("drc", dom)
        elif ds in ("stuck", "timeout"):
            k = ("drc", f"__{ds}__")
        else:
            continue                                  # clean: not a residual
        cls[k][plat] += 1
        rep.setdefault((k, plat), (path, cj, ds, ls, lmc))
        status_of[k] = (ds, ls)

    # ── 2. what history achieved, keyed by the same class ────────────────────
    hist = collections.defaultdict(lambda: collections.Counter())
    for ck, vc, outcome, n in q(
            "SELECT check_type, violation_class, outcome, COUNT(*) FROM fix_trajectories "
            "WHERE initial_count>0 GROUP BY 1,2,3"):
        hist[(ck, vc)][outcome] += n

    # ── 3. ask the LIVE matcher what it would propose, per class+platform ────
    def propose(k, plat):
        path, cj, ds, ls, lmc = rep[(k, plat)]
        cfgp = os.path.join(path, "constraints", "config.mk")
        try:
            cfg = dsf.parse_config(open(cfgp, errors="ignore").read())
        except OSError:
            cfg = {"PLATFORM": plat}
        cfg.setdefault("PLATFORM", plat)
        drc = {"status": ds or "unknown", "categories": json.loads(cj or "{}") or {}}
        lvs = {"status": ls or "skipped", "mismatch_class": lmc}
        check = "drc" if k[0] == "drc" else ("lvs" if k[0] == "lvs" else "both")
        try:
            plan = dsf.build_plan(drc, lvs, cfg, check=check)
        except Exception as e:                                    # noqa: BLE001
            return [], f"matcher_error:{type(e).__name__}"
        plans = plan if isinstance(plan, list) else [plan]
        ids, why = [], None
        for p in plans:
            for s in (p.get("strategies") or []):
                if s.get("id") not in ids:
                    ids.append(s.get("id"))
            why = why or p.get("residual_reason")
        return ids, why

    out = []
    for k in sorted(cls, key=lambda x: -sum(cls[x].values())):
        total = sum(cls[k].values())
        strategies, why = set(), None
        for plat in cls[k]:
            ids, w = propose(k, plat)
            strategies.update(ids); why = why or w
        h = hist.get(k, collections.Counter())
        if not strategies:
            bucket = "out_of_scope"
        elif h.get("resolved"):
            bucket = "covered_proven"
        elif h.get("abandoned") or h.get("improved"):
            bucket = "proposed_failed"
        else:
            bucket = "covered_untried"
        out.append({"check": k[0], "class": k[1], "designs": total,
                    "per_platform": dict(cls[k]), "strategies": sorted(strategies),
                    "history": dict(h), "bucket": bucket, "residual_reason": why})

    buckets = collections.Counter()
    for r in out:
        buckets[r["bucket"]] += r["designs"]
    print(f"{'bucket':17s} {'designs':>8s}   占比")
    tot = sum(buckets.values()) or 1
    for b in ("covered_proven", "covered_untried", "proposed_failed", "out_of_scope"):
        print(f"  {b:15s} {buckets[b]:8d}   {100*buckets[b]/tot:5.1f}%")
    print(f"  {'合计':15s} {tot:8d}")
    print()
    print(f"{'bucket':16s} {'check':5s} {'class':22s} {'设计':>5s}  策略 / 历史")
    for r in out:
        if r["designs"] < 2:
            continue
        s = ",".join(r["strategies"])[:46] or (r["residual_reason"] or "")[:46]
        h = ",".join(f"{k[:4]}={v}" for k, v in r["history"].items()) or "无历史"
        print(f"  {r['bucket']:14s} {r['check']:5s} {r['class'][:22]:22s} {r['designs']:5d}  {s}")
        print(f"  {'':14s} {'':5s} {'':22s} {'':5s}  └ {h}")
    if a.json_out:
        json.dump(out, open(a.json_out, "w"), indent=1, ensure_ascii=False)
        print(f"\nwrote {a.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
