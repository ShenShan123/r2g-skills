#!/usr/bin/env python3
"""Candidate export of recent DEV fix-log rows in the SOURCE-v1 unit format (user, 2026-10-17:
"audit existing, more recent DEV runs ... compile into a new shared historical dataset
without modifying SOURCE-v1").

Read-only over the eval tree. Writes `source_export.jsonl` + `source_manifest.json` into a
new directory, so the unchanged ann-v2 annotator and verifier run on it.

Inclusion: every fix-log row (attempts AND no-op stops, so the denominator is complete) of
every project under the named DEV campaigns, written on or after the cut date, and not
already a SOURCE-v1 row. Exclusion is recorded, never silent: T1/F1/F2 (TEST designs),
designs on the TEST list, locked T2 groups, learning-curve rosters.

  python3 -m ann_v2.dev_export --eval-root <_r2g_eval> --campaign <dir> [...] \
      --source-v1 <SOURCE-v1 dir> --t2-lock <lock.json> --test-design ibex ... \
      --since 2026-10-09 --out <dir>
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path

PRUNE = {"backend", ".orfs-work", "objects", "results", "logs", "reports_orfs", "drc", "lvs", "rcx", "dataset",
         "labels", "features", "src", "rtl", "sim", "synth", "input", "lint", "constraints"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def row_key(r: dict) -> tuple:
    return (r.get("fix_session_id"), int(r.get("iter") or 1), r.get("strategy"), r.get("check"), r.get("ts"))


def fix_logs(root: Path):
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in PRUNE)
        if "fix_log.jsonl" in fns and dp.endswith("/reports"):
            yield Path(dp) / "fix_log.jsonl"


def design_of(project: Path) -> str:
    return project.name.split("__")[0]


def build(eval_root: Path, campaigns: list, source_v1: Path, since: str, test_designs: set,
          locked_designs: set, out: Path, extra_families: dict | None = None) -> dict:
    """extra_families: {design: family} for designs outside SOURCE-v1's family table (e.g. the
    learning-curve corpus, keyed by its lineage group). A design already in the table keeps its
    SOURCE-v1 family; a disagreement raises."""
    v1_keys = set()
    for l in (source_v1 / "source_export.jsonl").read_text().splitlines():
        if l.strip():
            v1_keys.add(row_key(json.loads(l)["raw_row"]))
    v1_manifest = json.loads((source_v1 / "source_manifest.json").read_text())
    families = dict(v1_manifest["split"]["family_of_design"])
    for d, fam in (extra_families or {}).items():
        if d in families and families[d] != fam:
            raise ValueError(f"family conflict for {d}: {families[d]} vs {fam}")
        families.setdefault(d, fam)
    units: dict = {}
    excluded = collections.Counter()
    inputs = {}
    for c in campaigns:
        for log in fix_logs(eval_root / c):
            project = log.parent.parent
            design = design_of(project)
            rows = []
            for i, l in enumerate(log.read_text().splitlines(), 1):
                if l.strip():
                    try:
                        rows.append((i, json.loads(l)))
                    except ValueError:
                        excluded["unparseable_row"] += 1
            take = []
            for i, r in rows:
                if str(r.get("ts") or "") < since:
                    excluded["before_cut"] += 1
                elif row_key(r) in v1_keys:
                    excluded["already_in_SOURCE_v1"] += 1
                elif design in test_designs:
                    excluded["TEST_design"] += 1
                elif design in locked_designs:
                    excluded["T2_locked_design"] += 1
                else:
                    take.append((i, r))
            if not take:
                continue
            inputs[str(log)] = sha256(log)
            for i, r in take:
                digest = hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()
                u = units.get(digest)
                if u is None:                       # identical rows in copy roots are one unit
                    k = row_key(r)
                    uid = "devhist:%s:%s:%s:iter%s:%s" % (design, c, k[0], k[1], digest[:16])
                    u = units[digest] = {
                        "unit_id": uid, "identity": {"design": design, "platform": (r.get("situation") or {}).get("platform")},
                        "raw_row": r, "action": {"strategy": r.get("strategy"), "from_stage": r.get("from_stage")},
                        "provenance": {"sources": [], "code_version": "UNKNOWN", "toolchain": "UNKNOWN"},
                        "context": {"campaign": c, "project": project.name}, "content_digest": digest}
                u["provenance"]["sources"].append({"path": str(log), "line": i})
    out.mkdir(parents=True, exist_ok=True)
    recs = sorted(units.values(), key=lambda u: u["unit_id"])
    with (out / "source_export.jsonl").open("w") as f:
        for u in recs:
            f.write(json.dumps(u, sort_keys=True) + "\n")
    designs = sorted({u["identity"]["design"] for u in recs})
    manifest = {
        "schema": "ann-v2-devhist-candidate-v1", "status": "CANDIDATE (not frozen; SOURCE-v1 unchanged)",
        "since": since, "campaigns": campaigns, "units": len(recs),
        "split": {"family_of_design": {d: families[d] for d in designs if d in families}},
        "designs_without_family": [d for d in designs if d not in families],
        "excluded": dict(excluded), "test_designs": sorted(test_designs), "locked_designs": sorted(locked_designs),
        "source_v1_sha256": sha256(source_v1 / "source_export.jsonl"), "inputs": inputs}
    (out / "source_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--eval-root", type=Path, required=True)
    ap.add_argument("--campaign", nargs="+", required=True)
    ap.add_argument("--source-v1", type=Path, required=True)
    ap.add_argument("--t2-lock", type=Path, required=True)
    ap.add_argument("--test-design", nargs="+", required=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    lock = json.loads(a.t2_lock.read_text())
    locked = {e.get("design") for e in lock.get("entries", []) if e.get("design")}
    m = build(a.eval_root, a.campaign, a.source_v1, a.since, set(a.test_design), locked, a.out)
    print(json.dumps({k: m[k] for k in ("units", "excluded", "designs_without_family")}, indent=1))
    print(json.dumps(m["split"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
