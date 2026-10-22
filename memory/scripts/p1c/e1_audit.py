#!/usr/bin/env python3
"""P1c E1: raw-availability audit of the frozen TEHM stores (read-only).

For every transition of the 12 frozen stores, locate the raw fix-log row(s) it was captured from
(record id r2g:<session>:iter<k>:<family> = the k-th row, by iter, of that session in one fix log).
Classes:
  raw_unique      the raw row exists, and every raw copy of it is byte-identical
  raw_divergent   several raw rows with DIFFERENT content map to the same record id (distinct runs that
                  reuse a deterministic session name: one record id, several physical attempts)
  record_only     no raw row found (only the TEHM transition survives)
For verified PASS/PARTIAL transitions it also records which raw evidence could show "not rolled back":
  next_step_config   the next row of the same session records config_before containing this step's edit
  log_cumulative     this row's own cumulative_config (config.mk auto block at log time) contains the edit
  trial_isolated     a component trial (isolated trial project)
Usage: e1_audit.py <code root> <out.json>
"""
import collections
import glob
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

E = Path("/data1/zhangdy/RTL/RTL_testbench/_r2g_eval")
HERE = E / "p1c-export"
STORES = {"SL-hd": "sandbox-f/tehm/SL-hd", "SL-hs": "sandbox-f/tehm/SL-hs",
          "Mp-hd": "sandbox-f/tehm/Mp-hd", "Mp-hs": "sandbox-f/tehm/Mp-hs",
          "G-hd": "sandbox-f/tehm/G-hd", "G-hs": "sandbox-f/tehm/G-hs",
          "P-hd": "evaluation-h-ablation/no_llm_evidence-hd", "P-hs": "evaluation-h-ablation/no_llm_evidence-hs",
          "L-hd": "evaluation-h-ablation/no_component_trial_evidence-hd",
          "L-hs": "evaluation-h-ablation/no_component_trial_evidence-hs",
          "J-hd": "evaluation-i/stores/IJ-hd", "J-hs": "evaluation-i/stores/IJ-hs"}
code, out = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(code / "memory"))
from tehm.adapters.r2g_evidence import _normalize_family, normalize_knob_edits  # noqa: E402

RID = re.compile(r"^r2g:(.+):iter(\d+):([^:]+)$")


def digest(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:16]


def load_raw():
    """{session: [(source, rows sorted by iter)]} from every fix log and component-trial file."""
    by_sess = collections.defaultdict(list)
    files = [l.strip() for l in open(HERE / "all_fix_logs.txt") if l.strip()]
    sources = [(f, [json.loads(l) for l in open(f, errors="ignore") if l.strip().startswith("{")]) for f in files]
    for f in sorted(glob.glob(str(E / "**" / "*trials*" / "*.json"), recursive=True)):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        if isinstance(d, list) and d and isinstance(d[0], dict) and "fix_session_id" in d[0]:
            sources.append((f, d))
    for f, rows in sources:
        sess = collections.defaultdict(list)
        for r in rows:
            if isinstance(r, dict) and r.get("fix_session_id"):
                sess[str(r["fix_session_id"])].append(r)
        for s, rs in sess.items():
            rs.sort(key=lambda r: int(r.get("iter", 0) or 0))
            by_sess[s].append((f, rs))
    return by_sess, len(sources)


def edit_in(cfg, row):
    """True when every knob the row edited holds the row's new value in cfg; None when not decidable."""
    edits = normalize_knob_edits(row.get("config_delta"), row.get("config_before"))
    if not edits or not isinstance(cfg, dict):
        return None
    return all(str(cfg.get(k)) == str(v["after"]) if v["after"] is not None else cfg.get(k) in (None, "")
               for k, v in edits.items())


def rollback_basis(rows, i):
    row = rows[i]
    if row.get("evidence_tier") == "component_trial":
        return "trial_isolated"
    nxt = rows[i + 1] if i + 1 < len(rows) else None
    if nxt is not None and edit_in(nxt.get("config_before"), row):
        return "next_step_config"
    cum = row.get("cumulative_config")
    try:
        cum = json.loads(cum) if isinstance(cum, str) else cum
    except ValueError:
        cum = None
    if edit_in(cum, row):
        return "log_cumulative"
    return "none"


raw, n_sources = load_raw()
report = {"raw_sources": n_sources, "raw_sessions": len(raw), "stores": {}}
for name, rel in STORES.items():
    c = sqlite3.connect("file:%s?mode=ro" % (E / rel / "tehm.sqlite"), uri=True)
    rows_out = []
    for tid, outcome, ver, delta, prov in c.execute(
            "SELECT transition_id, outcome, verifier_json, observation_delta_json, provenance_json FROM tehm_transitions"):
        prov, ver = json.loads(prov or "{}"), json.loads(ver or "{}")
        rid = prov.get("record_id") or ""
        m = RID.match(rid)
        matches = []
        if m:
            sess, k, fam = m.group(1), int(m.group(2)), m.group(3)
            for f, rs in raw.get(sess, []):
                if k < len(rs) and _normalize_family(str(rs[k].get("strategy") or "none")).lower() == fam:
                    matches.append((f, digest(rs[k]), rollback_basis(rs, k)))
        digests = {d for _, d, _ in matches}
        cls = "record_only" if not matches else ("raw_unique" if len(digests) == 1 else "raw_divergent")
        verified_pos = outcome in ("PASS", "PARTIAL") and ver.get("oracle_complete") is True
        bases = sorted({b for _, _, b in matches})
        rows_out.append({"transition": tid, "record_id": rid, "class": cls, "raw_files": len({f for f, _, _ in matches}),
                         "raw_contents": len(digests), "verified_positive": verified_pos,
                         "rollback_basis": bases if verified_pos else None,
                         "kind": (lambda s: "seed" if s.startswith("seed_") else "hash" if re.fullmatch(r"[0-9a-f]{16}", s)
                                  else s.split("_")[0])(m.group(1) if m else "")})
    report["stores"][name] = rows_out
out.write_text(json.dumps(report, indent=1) + "\n")

print("raw sources %d, raw sessions %d" % (n_sources, len(raw)))
for name, rows in report["stores"].items():
    cnt = collections.Counter((r["kind"], r["class"]) for r in rows)
    pos = [r for r in rows if r["verified_positive"]]
    pb = collections.Counter(("+".join(r["rollback_basis"]) or "no_raw") for r in pos)
    print("%-6s trans %3d | %s" % (name, len(rows), " ".join("%s/%s=%d" % (k, c, n) for (k, c), n in sorted(cnt.items()))))
    print("       verified PASS/PARTIAL %d, rollback basis: %s" % (len(pos), dict(pb)))
