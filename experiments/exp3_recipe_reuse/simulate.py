#!/usr/bin/env python3
"""Exp3 extension -- recipe accumulation and reuse, simulated from the recorded campaign (no OpenROAD).

Population: the LLM-FALLBACK attempts of Exp3 (R2G + model fallback arm) on in-scope designs that R2G's
own recipes did not fix. Standalone model-only attempts are never used. For each such attempt the
campaign kept R2G's final project (the fallback's start point), R2G's diagnosis context at its stop,
and the LLM's final project with its harness-verified signoff verdict.

Stages
  distill     every fixed fallback attempt -> knowledge.learned_recipes.distill (R2G's stop symptom +
              the R2G->LLM project delta), with the rejection reason for every attempt that yields none
  A  self     mechanism check: R2G, put back in the donor's own stop state with only that recipe in its
              store, must select it through its REAL selection path (diagnose_signoff_fix --next) and
              --apply it, and the applied project must be fix-equivalent to the LLM's verified fix
  B  stream   accumulation in campaign time order, per fallback model: when R2G stops on design t, the
              store holds only recipes distilled from fallbacks that FINISHED before t started, from other
              split units; a reuse that is fix-equivalent to t's own verified fix replaces t's fallback
  C  louo     leave-one-unit-out over all distilled recipes (time-agnostic transfer)

Verification ("fix-equivalent"): the project R2G produces by applying the learned recipe is compared
with the design's OWN verified LLM fix: the same set of non-plumbing config edits, equal values, and
byte-identical hook/override files. Only then is the recorded dual-clean signoff of that layout
attributed to the reuse. Anything else is reported as unverified (a real flow run would be needed) and
never counted as a success or a saving. RTL edits are never distilled; fallback fixes that edited RTL
count only with a formal-equivalence proof (Exp3 rule) and are reported apart.

Cost of a reuse: no model call (selection + apply run inside R2G's deterministic repair loop), one flow
from the recipe's rerun stage (the design's own measured stage times from its final flow run) and one
signoff (the attempt's measured EDA time per signoff). Saved: the fallback attempt's recorded model
calls, tokens, USD, flow runs and wall time.

  simulate.py --inputs DIR --tally-inputs DIR --tally-csv CSV --out DIR
"""
from __future__ import annotations

import argparse, collections, glob, json, os, re, shutil, sqlite3, subprocess, sys, tempfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "r2g-skills" / "signoff-loop"
sys.path[:0] = [str(SKILL / "knowledge"), str(SKILL / "scripts" / "reports")]
import learned_recipes as lr                                   # noqa: E402
from diagnose_signoff_fix import parse_config                  # noqa: E402

DIAG = SKILL / "scripts" / "reports" / "diagnose_signoff_fix.py"
ORFS_HOOKS = SKILL / "scripts" / "flow" / "orfs_hooks"
FX = 7.10
STAGES = ["synth", "floorplan", "place", "cts", "route", "finish"]
MODEL = {"deepseek-flash": "DeepSeek", "gpt-6.1-sol": "GPT"}


def ts(s):
    return datetime.fromisoformat(s).timestamp()


# ---------------------------------------------------------------- inputs
def load_tally(tdir, csv_path):
    import csv
    verdict = {r["id"]: r for r in csv.DictReader(open(csv_path))}
    rows = [r for f in glob.glob(tdir + "/bare_*.json") for r in json.load(open(f)) if r["arm"] == "llm_after_r2g"]
    best = {}
    for r in rows:                                             # the tally's attempt: newest per (model, design)
        k = (r["model"], r["id"])
        if k not in best or r["mtime"] > best[k]["mtime"]:
            best[k] = r
    eq = {}
    roots = {x["staged_root"]: x["host_root"] for x in json.load(open(tdir + "/roots.json"))} if os.path.exists(tdir + "/roots.json") else {}
    for f in ("equiv_review_208_20261104.json", "equiv_all_20261104.json", "equiv_20261107_final.json"):
        if os.path.exists(tdir + "/" + f):
            for r in json.load(open(tdir + "/" + f))["rows"]:
                eq[(roots.get(r.get("root"), r.get("root")), r["case_id"])] = r["verdict"]
    return verdict, best, eq


def attempt_dir(inputs, root, cid):
    tag = root.strip("/").replace("/", "__")
    hits = glob.glob(os.path.join(inputs, "h2*", tag, cid))
    return hits[0] if hits else None


def files_of(d, sub_dirs):
    out = {}
    for sub in sub_dirs:
        for f in glob.glob(os.path.join(d, sub, "*")):
            if os.path.isfile(f):
                out[sub.split("/")[-1] + "/" + os.path.basename(f)] = open(f, "rb").read()
    for f in ORFS_HOOKS.glob("*"):                             # R2G's own hooks, referenced by absolute path
        if f.is_file():
            out.setdefault("orfs_hooks/" + f.name, f.read_bytes())
    return out


def flow_stats(d):
    """(attempt start ts, attempt end ts, per-stage seconds of the LAST complete flow run)."""
    p = os.path.join(d, "orfs_audit.jsonl")
    ev = [json.loads(l) for l in open(p)] if os.path.exists(p) else []
    start = ts(ev[0]["timestamp"]) if ev else None
    runs, cur, open_make = [], None, None
    for e in ev:
        if e["event"] == "wrapper_start":
            cur = {}
        elif e["event"] == "make_start" and cur is not None:
            open_make = (e["argv"][-1], ts(e["timestamp"]))
        elif e["event"] == "make_end" and cur is not None and open_make:
            cur[open_make[0]] = cur.get(open_make[0], 0) + ts(e["timestamp"]) - open_make[1]; open_make = None
        elif e["event"] == "wrapper_end" and cur is not None:
            if e.get("exit_code") == 0 and "finish" in cur:
                runs.append(cur)
            cur = None
    return start, (runs[-1] if runs else None), sum(sum(r.values()) for r in runs)


def units_of(case_ids, splits_dev, rtl_hashes):
    """Union-find split unit: same repo (C-number -> repo from the frozen split) or identical RTL hashes."""
    repo = {}
    for line in open(splits_dev):
        parts = line.strip().split("__")
        if len(parts) >= 3:
            repo[parts[0]] = parts[-1]
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b): parent[find(a)] = find(b)
    for cid in case_ids:
        c = cid.split("_", 1)[1]
        union(cid, "repo:" + repo.get(c, c))
        h = rtl_hashes.get(cid)
        if h:
            union(cid, "rtl:" + h)
    return {cid: find(cid) for cid in case_ids}


KNOWN = lr.known_variables(os.environ.get("ORFS_ROOT", "/home/yangao/r2g_toolchain/OpenROAD-flow-scripts"), SKILL)


def build_attempts(inputs, tally_inputs, tally_csv):
    verdict, best, eq = load_tally(tally_inputs, tally_csv)
    out = []
    for (model, cid), row in sorted(best.items()):
        v = verdict.get(cid)
        if not v or v["r2g"] != "not_fixed":
            continue                                           # population: R2G's own recipes did not fix it
        d = attempt_dir(inputs, row["root"], cid)
        if d is None:
            out.append({"case_id": cid, "model": model, "missing_inputs": True}); continue
        r = json.load(open(d + "/result.json"))
        fl = d + "/llm/r2g_context/fix_log.jsonl"
        fix_rows = [json.loads(l) for l in open(fl)] if os.path.exists(fl) else []
        synth = open(d + "/r2g/logs/synth.log", errors="replace").read() if os.path.exists(d + "/r2g/logs/synth.log") else None
        sym = lr.stop_symptom(fix_rows, synth)
        rcfg_p, lcfg_p = d + "/r2g/constraints/config.mk", d + "/llm/constraints/config.mk"
        delta = lr.config_delta(parse_config(open(rcfg_p).read()) if os.path.exists(rcfg_p) else {},
                                parse_config(open(lcfg_p).read()) if os.path.exists(lcfg_p) else {},
                                files_of(d, ["r2g/constraints", "r2g/hooks"]), files_of(d, ["llm/constraints", "llm/hooks"]))
        o = r.get("outcome")
        fixed = o == "dual_clean" or (o == "pending_semantic_review" and eq.get((row["root"], cid)) == "equivalent")
        start, last_flow, flows_s = flow_stats(d)
        top = (parse_config(open(lcfg_p).read()) if os.path.exists(lcfg_p) else {}).get("DESIGN_NAME")
        a = {"case_id": cid, "platform": cid.split("_")[0], "model": model, "root": row["root"], "dir": d,
             "outcome": o, "fixed": fixed, "equiv": eq.get((row["root"], cid)),
             "rtl_changed": (r.get("rtl_changed") or []) + (r.get("rtl_added") or []),
             "symptom": sym, "fix_rows": fix_rows, "delta": delta, "summary": (r.get("finish") or {}).get("summary", ""),
             "start": start, "end": os.path.getmtime(d + "/result.json"), "last_flow": last_flow, "flows_s": flows_s,
             "known_vars": KNOWN, "design_tokens": [t for t in (top, cid, cid.split("_", 1)[1]) if t],
             "eda_s": r.get("agent_eda_seconds") or 0, "signoffs": row.get("signoffs") or 0,
             "cost": {"calls": row.get("calls") or 0, "tokens": row.get("tokens") or 0, "flows": row.get("orfs") or 0,
                      "wall_h": (row.get("wall") or 0) / 3600, "usd": usd(row)},
             "rtl_hash": "|".join(sorted((r.get("prepare") or {}).get("rtl_sha256", {}).values()))}
        a["distill"] = lr.distill(a)
        out.append(a)
    return out


def usd(row):
    if row["model"] == "deepseek-flash":
        c = row.get("cached") or 0
        u = row.get("uncached") if row.get("uncached") is not None else max((row.get("inp") or 0) - c, 0)
        return (row.get("cost") or (u * 2.0 + c * 0.04 + (row.get("outp") or 0) * 8.0) / 1e6) / FX
    c = row.get("cached") or 0
    u = row.get("uncached") if row.get("uncached") is not None else max((row.get("inp") or 0) - c, 0)
    return (u * 2.0 + c * 0.10 + (row.get("outp") or 0) * 10.0) / 1e6


if __name__ == "__main__" and "--distill-only" in sys.argv:
    ap = argparse.ArgumentParser(); ap.add_argument("--inputs"); ap.add_argument("--tally-inputs"); ap.add_argument("--tally-csv")
    ap.add_argument("--distill-only", action="store_true")
    a = ap.parse_args()
    A = build_attempts(a.inputs, a.tally_inputs, a.tally_csv)
    c = collections.Counter()
    for x in A:
        if x.get("missing_inputs"): c[("missing", x["model"])] += 1; continue
        k = "recipe" if "recipe" in x["distill"] else x["distill"]["rejected"].split(":")[0]
        c[(MODEL[x["model"]], x["fixed"], k)] += 1
    for k, v in sorted(c.items(), key=str): print(v, k)
    rec = collections.Counter((x["platform"], x["symptom"][0]["check"], x["symptom"][0]["class"]) for x in A if "recipe" in x.get("distill", {}))
    print(rec)
    ids = collections.Counter(x["distill"]["recipe"]["recipe_id"] for x in A if "recipe" in x.get("distill", {}))
    print("distinct recipes", len(ids), "donor attempts", sum(ids.values()))


# ================================================================ R2G replay of a stop state
CHECK_OF = {"lvs": "lvs", "drc": "drc", "orfs_stage": "route"}


def stop_project(a, root):
    """Rebuild the project R2G stopped on: its final config.mk + the reports it diagnosed."""
    p = Path(root) / a["case_id"]
    if p.exists():
        shutil.rmtree(p)
    (p / "constraints").mkdir(parents=True); (p / "reports").mkdir()
    shutil.copy(a["dir"] + "/r2g/constraints/config.mk", p / "constraints" / "config.mk")
    for f in ("drc.json", "lvs.json", "route.json", "fix_log.jsonl"):
        src = a["dir"] + "/llm/r2g_context/" + f
        if os.path.exists(src):
            shutil.copy(src, p / "reports" / f)
    return p


def r2g_select_apply(a, db, work):
    """Run R2G's REAL selection + apply (diagnose_signoff_fix --next, then --apply) on a's stop state."""
    check = CHECK_OF.get(a["symptom"][0]["check"]) if a.get("symptom") else None
    if check is None:
        return {"status": "symptom_stage_not_wired", "check": (a.get("symptom") or [{}])[0].get("check")}
    p = stop_project(a, work)
    tried = sorted({r["strategy"] for r in a["fix_rows"] if r.get("strategy") not in (None, "none")})
    env = {**os.environ, "R2G_KNOWLEDGE_DB": str(db), "R2G_JOURNAL_DB": str(Path(work) / "journal.sqlite")}
    base = [sys.executable, str(DIAG), str(p), "--check", check] + (["--exclude", ",".join(tried)] if tried else [])
    nxt = subprocess.run(base + ["--next"], capture_output=True, text=True, env=env, timeout=300).stdout.strip()
    if not nxt.startswith("learned:"):
        return {"status": "no_learned_recipe" if nxt.startswith("STOP") else "builtin_selected", "next": nxt[:200]}
    sid = nxt.split("\t")[0]
    r = subprocess.run(base + ["--apply", sid], capture_output=True, text=True, env=env, timeout=300)
    if r.returncode != 0:
        return {"status": "apply_failed", "recipe": sid, "out": (r.stdout + r.stderr)[-400:]}
    return {"status": "applied", "recipe": sid, "rerun_from": nxt.split("\t")[1] or None,
            "config": (p / "constraints" / "config.mk").read_text(), "project": str(p)}


def fix_equivalent(applied, recipe, a):
    """Is R2G's applied project the SAME fix as design a's own verified LLM fix? (see module doc)"""
    if not (a["outcome"] == "dual_clean" and not a["rtl_changed"]):
        return False, "no verified non-RTL fix of this design to compare with"
    own = a["delta"]
    if own["rejected"] or not own["edits"]:
        return False, "the design's own fix is not a config/hook delta"
    want = {k: (("@" + lr._sha(own["assets"][v[7:]])) if str(v).startswith("@asset:") else v) for k, v in own["edits"].items()}
    cfg = parse_config(applied["config"])
    got = {}
    for k, v in recipe["strategy"]["config_edits"].items():
        val = cfg.get(k)
        if str(v).startswith("@asset:"):
            if not val or not os.path.exists(val):
                return False, "asset of %s not materialized" % k
            got[k] = "@" + lr._sha(open(val, "rb").read())
            if got[k] != "@" + lr._sha(recipe["assets"][v[7:]]):
                return False, "materialized asset differs from the recipe"
        else:
            got[k] = val
    return (got == want), ("fix-equivalent" if got == want else "different fix")


def reuse_cost(a, rerun_from):
    lf = a.get("last_flow") or {}
    i = STAGES.index(rerun_from) if rerun_from in STAGES else None
    flow_s = sum(v for k, v in lf.items() if i is not None and k in STAGES and STAGES.index(k) >= i)
    signoff_s = max(a["eda_s"] - a["flows_s"], 0) / max(a["signoffs"], 1)
    return {"calls": 0, "tokens": 0, "usd": 0.0, "flows": 1 if i is not None else 0,
            "wall_h": (flow_s + signoff_s) / 3600}


# ================================================================ stages
def stage_self(A, work):
    """A: every admitted-quality recipe, alone in the store, must be selected + applied on its donor's own
    stop state and reproduce the donor's verified fix exactly."""
    rows = []
    for a in A:
        if "recipe" not in a.get("distill", {}):
            continue
        rec = a["distill"]["recipe"]; db = Path(work) / ("self_%s.sqlite" % a["case_id"])
        db.unlink(missing_ok=True)
        c = sqlite3.connect(db); lr.record(c, rec, a["case_id"], "u1", {}); lr.record(c, rec, a["case_id"] + "#2", "u2", {}); c.close()
        r = r2g_select_apply(a, db, Path(work) / "proj_self")
        ok, why = fix_equivalent(r, rec, a) if r["status"] == "applied" else (False, r["status"])
        rows.append({"case_id": a["case_id"], "model": MODEL[a["model"]], "symptom": rec["signature"],
                     "recipe": rec["recipe_id"], "status": r["status"], "selected_own": r.get("recipe") == "learned:" + rec["recipe_id"],
                     "fix_equivalent": ok, "why": why})
    return rows


def stage_stream(A, units, work, *, models, same_unit_ok):
    """B: campaign-time replay. Donors enter the store when they FINISH; a recipe is offered once it is
    validated (>= ADMIT_UNITS independent units). At each R2G stop (attempt start) the store snapshot is
    queried through R2G's real selection path."""
    ev = sorted([a for a in A if not a.get("missing_inputs") and a["model"] in models and a["start"]], key=lambda a: a["start"])
    donors, uses, rows = [], [], []
    for t in ev:
        db = Path(work) / "stream.sqlite"; db.unlink(missing_ok=True)
        c = sqlite3.connect(db); lr.ensure_schema(c)
        for d in donors:
            if d["end"] < t["start"] and (same_unit_ok or units[d["case_id"]] != units[t["case_id"]]):
                lr.record(c, d["distill"]["recipe"], d["case_id"] + "/" + d["model"], units[d["case_id"]], {})
        for rid, design, ok in uses:
            lr.record_use(c, rid, design, None, ok, {})
        c.close()
        r = r2g_select_apply(t, db, Path(work) / "proj_stream")
        row = {"case_id": t["case_id"], "model": MODEL[t["model"]], "platform": t["platform"],
               "symptom": t["symptom"][0] if t.get("symptom") else None, "status": r["status"],
               "llm_fixed": t["fixed"], "llm_cost": t["cost"]}
        if r["status"] == "applied":
            rid = r["recipe"][len("learned:"):]
            rec = next(d["distill"]["recipe"] for d in donors if d["distill"]["recipe"]["recipe_id"] == rid)
            ok, why = fix_equivalent(r, rec, t)
            row.update(recipe=rid, verified=ok, why=why, reuse_cost=reuse_cost(t, r.get("rerun_from")))
            if ok:
                uses.append((rid, t["case_id"] + "/" + t["model"], True))
        rows.append(row)
        # the fallback ran (no verified reuse): a verified, gate-passing fix becomes donor evidence
        if not row.get("verified") and "recipe" in t.get("distill", {}):
            donors.append(t)
    return rows


def stage_second(A, units, work, *, same_unit_ok):
    """C: the same stops met AGAIN (a regression run / design re-spin), with the store as it stands at the end
    of the campaign: every verified, gate-passing fix recorded; only validated recipes are offered."""
    donors = [a for a in A if not a.get("missing_inputs") and "recipe" in a.get("distill", {})]
    rows = []
    for t in [a for a in A if not a.get("missing_inputs") and a["start"]]:
        db = Path(work) / "second.sqlite"; db.unlink(missing_ok=True)
        c = sqlite3.connect(db); lr.ensure_schema(c)
        for d in donors:
            if same_unit_ok or units[d["case_id"]] != units[t["case_id"]]:
                lr.record(c, d["distill"]["recipe"], d["case_id"] + "/" + d["model"], units[d["case_id"]], {})
        c.close()
        r = r2g_select_apply(t, db, Path(work) / "proj_second")
        row = {"case_id": t["case_id"], "model": MODEL[t["model"]], "platform": t["platform"],
               "symptom": t["symptom"][0] if t.get("symptom") else None, "status": r["status"],
               "llm_fixed": t["fixed"], "llm_cost": t["cost"]}
        if r["status"] == "applied":
            rid = r["recipe"][len("learned:"):]
            rec = next(d["distill"]["recipe"] for d in donors if d["distill"]["recipe"]["recipe_id"] == rid)
            ok, why = fix_equivalent(r, rec, t)
            row.update(recipe=rid, verified=ok, why=why, reuse_cost=reuse_cost(t, r.get("rerun_from")))
        rows.append(row)
    return rows


def summarize(rows):
    n = len(rows); offered = [r for r in rows if r["status"] == "applied"]; ver = [r for r in offered if r["verified"]]
    def tot(L, key, k): return sum((x[key] or {}).get(k, 0) for x in L)
    keys = ("calls", "tokens", "usd", "flows", "wall_h")
    base = {k: tot(rows, "llm_cost", k) for k in keys}
    saved = {k: tot(ver, "llm_cost", k) for k in keys}
    spent = {k: tot(offered, "reuse_cost", k) for k in keys}         # every offered reuse costs its flow
    # upper bound: every unverified offer on a design the LLM did fix would have worked (labelled as such)
    upper = [r for r in offered if r["verified"] or r["llm_fixed"]]
    return {"upper_bound_reuse": len(upper), "upper_bound_saved": {k: tot(upper, "llm_cost", k) for k in keys},
            "stops": n, "offered": len(offered), "verified_reuse": len(ver),
            "unverified_offers": len(offered) - len(ver), "llm_fixed_stops": sum(r["llm_fixed"] for r in rows),
            "baseline": base, "saved": saved, "reuse_spent": spent,
            "net": {k: saved[k] - spent[k] for k in keys}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", required=True); ap.add_argument("--tally-inputs", required=True)
    ap.add_argument("--tally-csv", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--splits-dev", default=str(REPO / "experiments" / "splits" / "dev.txt"))
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); work = Path(tempfile.mkdtemp(prefix="reuse_sim_"))
    A = build_attempts(a.inputs, a.tally_inputs, a.tally_csv)
    units = units_of([x["case_id"] for x in A], a.splits_dev, {x["case_id"]: x.get("rtl_hash") for x in A if x.get("rtl_hash")})
    # ---- distillation + admission ledger
    ledger = []
    for x in A:
        dd = x.get("distill", {})
        ledger.append({"case_id": x["case_id"], "model": MODEL.get(x["model"], x["model"]), "unit": units.get(x["case_id"]),
                       "llm_fixed": x.get("fixed"), "outcome": x.get("outcome"), "equiv": x.get("equiv"),
                       "symptom": (x.get("symptom") or [None])[0], "recipe": (dd.get("recipe") or {}).get("recipe_id"),
                       "rejected": dd.get("rejected"), "issues": dd.get("issues"),
                       "edits": (dd.get("recipe") or {}).get("strategy", {}).get("config_edits")})
    pools = collections.defaultdict(set)
    for l in ledger:
        if l["recipe"]:
            pools[l["recipe"]].add(l["unit"])
    for l in ledger:
        l["validated_final"] = bool(l["recipe"]) and len(pools[l["recipe"]]) >= lr.ADMIT_UNITS
    res = {"ledger": ledger, "self": stage_self(A, work)}
    for name, models in (("deepseek", {"deepseek-flash"}), ("gpt", {"gpt-6.1-sol"}), ("both", {"deepseek-flash", "gpt-6.1-sol"})):
        for scen, same in (("similar", False), ("same_or_similar", True)):
            rows = stage_stream(A, units, work, models=models, same_unit_ok=same)
            res["stream_%s_%s" % (name, scen)] = {"rows": rows, "summary": summarize(rows)}
    for scen, same in (("similar", False), ("same_or_similar", True)):
        rows = stage_second(A, units, work, same_unit_ok=same)
        res["second_both_%s" % scen] = {"rows": rows, "summary": summarize(rows)}
    json.dump(res, open(out / "results.json", "w"), indent=1, default=str)
    write_md(res, out / "RESULTS.md")
    print(open(out / "RESULTS.md").read())


def write_md(res, path):
    L = ["# Exp3 recipe accumulation and reuse (simulation, no OpenROAD)", ""]
    led = res["ledger"]; fixed = [l for l in led if l["llm_fixed"]]
    c = collections.Counter((l["rejected"] or "recipe").split(":")[0] for l in fixed)
    L += ["## Distillation and admission (fixed fallback attempts only)", "",
          "| | count |", "|---|---|", "| verified fallback fixes (R2G had failed) | %d |" % len(fixed)]
    L += ["| -> %s | %d |" % (k, v) for k, v in sorted(c.items(), key=lambda kv: -kv[1])]
    nrec = len({l["recipe"] for l in led if l["recipe"]}); nval = len({l["recipe"] for l in led if l["validated_final"]})
    L += ["| distinct candidate recipes | %d |" % nrec, "| validated (>= %d independent units) at the end | %d |" % (lr.ADMIT_UNITS, nval), ""]
    s = res["self"]
    L += ["## A. Mechanism check (each recipe alone in the store, on its donor's own stop state)", "",
          "%d recipes: selected by R2G %d, applied %d, fix-equivalent to the donor's verified fix %d" % (
              len(s), sum(r["selected_own"] for r in s), sum(r["status"] == "applied" for r in s), sum(r["fix_equivalent"] for r in s)), ""]
    bad = [r for r in s if not r["fix_equivalent"]]
    L += ["  - %s %s: %s / %s" % (r["case_id"], r["model"], r["status"], r["why"]) for r in bad[:20]]
    L += ["", "## B. Campaign-time replay", "",
          "| stream | scenario | R2G stops | validated recipe offered | verified reuse | unverified offers | calls saved | tokens saved | USD saved | flows saved | wall h saved | reuse flow h |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, v in res.items():
        if not (k.startswith("stream_") or k.startswith("second_")):
            continue
        sm = v["summary"]; kind, name, *scen = k.split("_"); name = ("B " if kind == "stream" else "C second encounter, ") + name
        L.append("| %s | %s | %d | %d | %d | %d | %d (of %d) | %d (of %d) | %.3f (of %.3f) | %d (of %d) | %.1f (of %.1f) | %.1f |" % (
            name, "_".join(scen), sm["stops"], sm["offered"], sm["verified_reuse"], sm["unverified_offers"],
            sm["saved"]["calls"], sm["baseline"]["calls"], sm["saved"]["tokens"], sm["baseline"]["tokens"],
            sm["saved"]["usd"], sm["baseline"]["usd"], sm["saved"]["flows"], sm["baseline"]["flows"],
            sm["saved"]["wall_h"], sm["baseline"]["wall_h"], sm["reuse_spent"]["wall_h"]))
    L += ["", "Upper bound (NOT a result: assumes every validated-recipe offer on a design the LLM fixed would also have worked):", "",
          "| run | reuses | calls | tokens | USD | flows | wall h |", "|---|---|---|---|---|---|---|"]
    for k, v in res.items():
        if k.startswith(("stream_", "second_")):
            u = v["summary"]["upper_bound_saved"]
            L.append("| %s | %d | %d | %d | %.3f | %d | %.1f |" % (k, v["summary"]["upper_bound_reuse"], u["calls"], u["tokens"], u["usd"], u["flows"], u["wall_h"]))
    open(path, "w").write("\n".join(L) + "\n")


if __name__ == "__main__" and "--distill-only" not in sys.argv:
    main()
