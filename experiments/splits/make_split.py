#!/usr/bin/env python3
"""Build the dev / held-out split required by EXPERIMENT_PLAN E2.

  "Per platform, disjoint dev / held-out by design family (repo-level, after
   dedup). Learn + validate recipes on dev only; freeze before touching
   held-out."

The split UNIT is a union-find class over three relations, so neither repo
style nor literal duplicate RTL can leak across the boundary:
  1. same repository.revision_key  (github:owner/name)
  2. identical set of source-file sha256s  (a fork under a different owner)
  3. the package's own consolidated_duplicates links
     (STRUCTURAL_DUPLICATE_FINGERPRINT_V2)

ONE split is produced and used on ALL THREE platforms. A per-platform split
would let a design be dev on one platform and held-out on another, and a recipe
learned from its sky130 run would then be "tested" on its gf180 run.

Assignment is greedy over units sorted by size, each going to whichever side is
currently smaller -- balanced on design count AND on the cell-count histogram,
since an unbalanced difficulty mix would confound the comparison.
"""
import collections, glob, json, os, sys

PKG = "/proj/workarea/yangao/rtl-certified-handoff-0002/certified"
D = "/proj/workarea/yangao/"
BINS = [(0, 100), (100, 1000), (1000, 5000), (5000, 20000), (20000, 10**9)]

# ---- which designs are actually in the corpus (keyed by the short name) -----
corpus = {}
for f in glob.glob(D + "n45_full/runs/*/result.json"):
    n = os.path.basename(os.path.dirname(f))
    short = "__".join(n.split("__")[:-2])
    try:
        d = json.load(open(f))
    except Exception:
        d = {}
    corpus[short] = {"cells": d.get("cells") or 0,
                     "pass": d.get("status") == "pass"}
print(f"语料设计数: {len(corpus)}")

# ---- read DESIGN.json for every corpus design ------------------------------
parent = {}
def find(x):
    while parent.setdefault(x, x) != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x
def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb

by_repo = collections.defaultdict(list)
by_hash = collections.defaultdict(list)
by_did = {}
found = 0
for short in corpus:
    p = os.path.join(PKG, short, "DESIGN.json")
    if not os.path.isfile(p):
        continue
    try:
        d = json.load(open(p))
    except Exception:
        continue
    found += 1
    find(short)
    rk = (d.get("repository") or {}).get("revision_key")
    if rk:
        by_repo[rk].append(short)
    hs = tuple(sorted(s.get("sha256", "") for s in (d.get("source_files") or [])))
    if hs:
        by_hash[hs].append(short)
    did = d.get("design_id")
    if did:
        by_did[did] = short
    corpus[short]["dups"] = [x.get("design_id")
                             for x in (d.get("consolidated_duplicates") or [])]
print(f"找到 DESIGN.json 的: {found}")

for group in list(by_repo.values()) + list(by_hash.values()):
    for other in group[1:]:
        union(group[0], other)
nx = 0
for short, info in corpus.items():
    for did in info.get("dups") or []:
        peer = by_did.get(did)
        if peer:
            union(short, peer); nx += 1
print(f"合并关系: 同仓库 {len(by_repo)} 组, 同内容 {len(by_hash)} 组, "
      f"结构重复链接 {nx} 条")

units = collections.defaultdict(list)
for short in corpus:
    units[find(short)].append(short)
print(f"划分单元数: {len(units)}  (最大单元 {max(len(v) for v in units.values())} 个设计)")

# ---- greedy balanced assignment -------------------------------------------
def binof(c):
    for i, (lo, hi) in enumerate(BINS):
        if lo <= c < hi:
            return i
    return len(BINS) - 1

{"dev": [], "held": []}
side = {"dev": [], "held": []}
hist = {"dev": collections.Counter(), "held": collections.Counter()}
for u in sorted(units.values(), key=len, reverse=True):
    # pick the side that is behind on total count, breaking ties on the bin
    # this unit would most unbalance
    pick = "dev" if len(side["dev"]) < len(side["held"]) else "held"
    if len(side["dev"]) == len(side["held"]):
        b = binof(corpus[u[0]]["cells"])
        pick = "dev" if hist["dev"][b] <= hist["held"][b] else "held"
    side[pick] += u
    for s in u:
        hist[pick][binof(corpus[s]["cells"])] += 1

print(f"\ndev {len(side['dev'])} 份   held-out {len(side['held'])} 份")
print(f"{'单元数区间':>14s} {'dev':>7s} {'held-out':>9s}")
for i, (lo, hi) in enumerate(BINS):
    print(f"{f'{lo}-{hi if hi<10**9 else chr(8734)}':>14s} "
          f"{hist['dev'][i]:7d} {hist['held'][i]:9d}")
fd = sum(1 for s in side["dev"] if not corpus[s]["pass"])
fh = sum(1 for s in side["held"] if not corpus[s]["pass"])
print(f"\nnangate45 流程失败数:  dev {fd}   held-out {fh}")

for k, v in side.items():
    with open(D + f"split_{k}.txt", "w") as fh2:
        fh2.write("\n".join(sorted(v)) + "\n")
print(f"\n-> {D}split_dev.txt / split_held.txt")
# leakage self-check: no unit may straddle
bad = [u for u in units.values()
       if any(s in side["dev"] for s in u) and any(s in side["held"] for s in u)]
print(f"跨界单元(必须是 0): {len(bad)}")
