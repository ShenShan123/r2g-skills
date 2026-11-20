#!/usr/bin/env python3
"""Inventory existing physical results and freeze a family-disjoint Exp4 cohort.

Inventory mode runs on the source host and never launches an EDA tool. Selection
uses only provenance, source-file presence, signoff and synthesized cell counts;
no graph conversion or graph-checking result participates in the selection.
"""
import argparse
import collections
import hashlib
import json
import random
import re
import socket
from pathlib import Path

PLATFORMS = ("nangate45", "sky130hd", "gf180")
QUOTAS = {"dev": {"small": 2, "medium": 2, "large": 2},
          "test": {"small": 6, "medium": 8, "large": 6}}
REQUIRED = ("config.mk", "constraint.sdc", "6_final.def", "6_final.spef", "6_final.sdc", "6_final.v")
DEFAULT_ROOTS = {
    "nangate45": "/proj/workarea/yangao/rerun_nangate45_20261018/runs",
    "sky130hd": "/proj/workarea/yangao/rerun_sky130hd_20261022/runs",
    "gf180": "/proj/workarea/yangao/rerun_gf180_20261026/runs",
}
ID_RE = re.compile(r"C\d{5}__[^\s\"']+?__(?:nangate45|sky130hd|gf180)__p[\d.]+")
REPO_RE = re.compile(r"(?:^|/)sources/(R\d+__[^/\s]+)")


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def candidate_id(design):
    return design.split("__", 1)[0]


def repo_slug(design):
    return design.rsplit("__", 3)[1].lower()


def inventory(roots):
    rows = []
    for platform, root in sorted(roots.items()):
        for result in sorted(Path(root).glob("*/result.json")):
            run = result.parent
            try:
                data = json.loads(result.read_text())
            except (OSError, ValueError):
                continue
            cfg = run / "config.mk"
            text = cfg.read_text() if cfg.is_file() else ""
            repos = sorted(set(REPO_RE.findall(text)))
            drc = data.get("drc_status")
            recovery = None
            report, gds = run / "6_drc.lyrdb", run / "6_final.gds"
            # Match rerun_tally's verdict of record; never override a recorded violation.
            if drc not in ("clean", "violations") and report.is_file() and gds.is_file():
                if report.stat().st_mtime >= gds.stat().st_mtime:
                    n = len(re.findall(r"<item>", report.read_text(errors="replace")))
                    recovery = "clean" if n == 0 else "violations"
                    drc = recovery
            rows.append({
                "platform": platform, "design": run.name,
                "candidate_id": candidate_id(run.name), "cells": data.get("cells"),
                "source_host": socket.gethostname(), "source_dir": str(run.resolve()),
                "source_repositories": repos, "repository_slug": repo_slug(run.name),
                "drc_status": drc, "drc_recovery": recovery,
                "lvs_status": data.get("lvs_status"),
                "missing": [n for n in REQUIRED if not (run / n).is_file()],
                "result_sha256": sha(result), "config_sha256": sha(cfg) if cfg.is_file() else None,
            })
    return {"schema": 1, "roots": roots, "required": list(REQUIRED), "rows": rows}


def history(paths):
    designs, sources = set(), {}
    for path in paths:
        p = Path(path)
        designs.update(ID_RE.findall(p.read_text()))
        sources[str(p)] = sha(p)
    return {"designs": sorted(designs),
            "candidate_ids": sorted({candidate_id(n) for n in designs}),
            "repository_slugs": sorted({repo_slug(n) for n in designs}), "sources": sources}


def family_components(rows):
    """Union same candidate, same repository slug, or any shared source repository.

    Slug aliases deliberately over-group repositories with the same short name.
    Reused IP from unrelated repositories cannot be inferred from these records.
    """
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    def union(a, b):
        a, b = find(a), find(b)
        parent[max(a, b)] = min(a, b)
    for row in rows:
        keys = ["candidate:" + candidate_id(row["design"]), "slug:" + repo_slug(row["design"])]
        keys += ["repo:" + n for n in row.get("source_repositories", [])]
        for k in keys:
            union(keys[0], k)
    return {r["design"]: find("candidate:" + candidate_id(r["design"])) for r in rows}


def eligible(row):
    return (row.get("drc_status") == "clean" and row.get("lvs_status") == "clean"
            and not row.get("missing") and isinstance(row.get("cells"), int)
            and not isinstance(row["cells"], bool) and 300 <= row["cells"] <= 30000
            and bool(row.get("source_repositories")))


def assign_strata(rows, relative=False):
    cuts = {}
    for p in PLATFORMS:
        values = sorted(r["cells"] for r in rows if r["platform"] == p)
        if not values:
            raise ValueError("No eligible rows for " + p)
        lo, hi = (values[(len(values) - 1) // 3], values[2 * (len(values) - 1) // 3]) if relative else (999, 10000)
        if lo >= hi:
            raise ValueError("Collapsed size thresholds for " + p)
        cuts[p] = {"small_max": lo, "medium_max": hi, "size_basis": "relative_tertiles" if relative else "absolute_cells"}
        for row in rows:
            if row["platform"] == p:
                row["stratum"] = "small" if row["cells"] <= lo else "medium" if row["cells"] <= hi else "large"
    return cuts


def select(inv, old, seed=20261115, relative=False, exclude_history_families=False):
    families = family_components(inv["rows"])
    rows = [dict(r, family=families[r["design"]]) for r in inv["rows"] if eligible(r)]
    old_ids = set(old["candidate_ids"])
    old_families = {families[r["design"]] for r in inv["rows"]
                    if candidate_id(r["design"]) in old_ids or repo_slug(r["design"]) in old["repository_slugs"]}
    # Fresh identity on every platform; old repository lineage is separately recorded.
    rows = [r for r in rows if candidate_id(r["design"]) not in old_ids
            and not (exclude_history_families and r["family"] in old_families)]
    cuts = assign_strata(rows, relative)
    buckets = {(p, s): [r for r in rows if r["platform"] == p and r["stratum"] == s]
               for p in PLATFORMS for s in QUOTAS["dev"]}
    counts = {p: {s: len(buckets[p, s]) for s in QUOTAS["dev"]} for p in PLATFORMS}
    for (p, s), pool in buckets.items():
        if len(pool) < QUOTAS["dev"][s] + QUOTAS["test"][s]:
            raise ValueError(f"Insufficient {p}/{s}: {len(pool)}; pool counts={counts}")
    # Retry only provenance-based allocations. Each trial is deterministic and never
    # observes downstream scores. Preserve full selected records in the manifest.
    selected = None
    for attempt in range(2000):
        rng = random.Random(f"{seed}:{attempt}")
        picked, split_family, used = {"dev": [], "test": []}, {}, set()
        ok = True
        for split in ("dev", "test"):
            order = sorted(buckets, key=lambda k: (len(buckets[k]), rng.random()))
            for p, s in order:
                pool = list(buckets[p, s]); rng.shuffle(pool)
                # Prefer distinct repositories within a platform, while permitting
                # matching source families across technologies inside one split.
                pool.sort(key=lambda r: (r["family"] in split_family,))
                take = []
                for row in pool:
                    if row["design"] in used or split_family.get(row["family"], split) != split:
                        continue
                    if any(x["platform"] == p and x["family"] == row["family"] for x in picked[split] + take):
                        continue
                    take.append(row)
                    if len(take) == QUOTAS[split][s]:
                        break
                if len(take) != QUOTAS[split][s]:
                    ok = False; break
                for row in take:
                    used.add(row["design"]); split_family[row["family"]] = split
                picked[split].extend(take)
            if not ok:
                break
        if ok:
            selected = picked; break
    if selected is None:
        raise ValueError("Could not satisfy family-disjoint quotas after 2000 deterministic attempts")
    for split in selected:
        selected[split].sort(key=lambda r: (r["platform"], r["stratum"], r["design"]))
        for row in selected[split]:
            row["historical_repository_exposure"] = row["family"] in old_families
    out = {"schema": 1, "seed": seed, "allocation_attempt": attempt, "quotas": QUOTAS,
           "size_thresholds": cuts, "eligible_counts": counts,
           "historical_exclusion": "repository_families" if exclude_history_families else "candidate_id_across_platforms",
           "history": old, "family_rule": "connected components of candidate ID, repository slug and full source repository IDs",
           "family_limitation": "Duplicated IP across unrelated source repositories is not detectable from config metadata.",
           "splits": selected}
    validate(out)
    return out


def validate(manifest):
    rows = manifest["splits"]
    all_rows = rows["dev"] + rows["test"]
    assert len({r["design"] for r in all_rows}) == len(all_rows), "Duplicate design"
    assert not {r["family"] for r in rows["dev"]} & {r["family"] for r in rows["test"]}, "Family leakage"
    old = set(manifest["history"]["candidate_ids"])
    assert not {candidate_id(r["design"]) for r in all_rows} & old, "Historical identity leakage"
    for split, quotas in QUOTAS.items():
        for p in PLATFORMS:
            for s, n in quotas.items():
                assert sum(r["platform"] == p and r["stratum"] == s for r in rows[split]) == n, (split, p, s)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("--root", action="append", help="platform=/existing/run/root; defaults to all three 216 baseline reruns")
    inv.add_argument("--out", type=Path, required=True)
    sel = sub.add_parser("select")
    sel.add_argument("--inventory", type=Path, required=True)
    sel.add_argument("--history", type=Path, action="append", required=True)
    sel.add_argument("--relative-tertiles", action="store_true")
    sel.add_argument("--exclude-history-families", action="store_true")
    sel.add_argument("--seed", type=int, default=20261115)
    sel.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.command == "inventory":
        roots = dict(x.split("=", 1) for x in a.root) if a.root else DEFAULT_ROOTS
        out = inventory(roots)
        print(json.dumps(dict(collections.Counter(r["platform"] for r in out["rows"]))))
    else:
        out = select(json.loads(a.inventory.read_text()), history(a.history), a.seed,
                     a.relative_tertiles, a.exclude_history_families)
        out["inventory_sha256"] = sha(a.inventory)
        print(json.dumps({"eligible_counts": out["eligible_counts"], "selected": {s: len(v) for s, v in out["splits"].items()}}))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
