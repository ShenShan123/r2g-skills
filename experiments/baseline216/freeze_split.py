#!/usr/bin/env python3
"""Freeze the dev / held-out split that E2 requires, before any more recipe work.

E2: "Per platform, disjoint dev / held-out by design family (repo-level, after
dedup). Learn + validate recipes on dev only; freeze before touching held-out."

Three decisions, each made for a reason:

1. FAMILY = THE REPO, not the design. `arty_mac_test` contributes both frame_rx
   and udp_frame_rx; splitting them would validate a recipe on a sibling from the
   same source tree, which is the leakage this split exists to prevent.

2. ONE GLOBAL ASSIGNMENT, not a per-platform draw. The three corpora hold the
   SAME 2497 designs on three platforms. If a family were dev on sky130hd and
   held-out on nangate45, a recipe learned on the first could be measured on the
   same design under the second, and the cross-platform transfer claim would be
   circular. E2 says "per platform"; a single assignment satisfies that strictly
   and also covers the transfer case.

3. ASSIGNED BY HASH OF THE FAMILY NAME, never by outcome. Balancing the two
   sides on failure counts would mean consulting the labels, and re-drawing until
   the halves look even is the same peeking in slower motion. So the draw is
   deterministic and outcome-blind, and the resulting imbalance is REPORTED
   rather than corrected.

Designs already touched during development are recorded, not hidden: any that
land in held-out are marked `seen_in_dev` so a later result can say so plainly.

  freeze_split.py --write      writes split_frozen.json (refuses to overwrite)
  freeze_split.py              dry run: prints the tally only
"""
import argparse, collections, hashlib, json, os, sys

D = "/proj/workarea/yangao"
CORPORA = {"sky130hd": f"{D}/full_out/runs",
           "nangate45": f"{D}/n45_full/runs",
           "gf180": f"{D}/gf180_full/runs"}
OUT = f"{D}/split_frozen.json"
# Subject pools this campaign already developed recipes on.
USED_DIRS = [f"{D}/ratio_trial", f"{D}/_dkb", f"{D}/res_m32", f"{D}/runb_ant"]


def family(name):
    """C00001__lcd_timing__sonic-speed-parallelism__sky130hd__p5 -> the repo."""
    f = name.split("__")
    return f[2] if len(f) >= 3 else name


def design_key(name):
    """id + design, the part that is the same across platforms."""
    f = name.split("__")
    return "__".join(f[:2]) if len(f) >= 2 else name


def side(fam):
    h = hashlib.sha256(("r2g-split-v1:" + fam).encode()).hexdigest()
    return "dev" if int(h[:8], 16) % 2 == 0 else "heldout"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    per_plat = {}
    for p, root in CORPORA.items():
        if not os.path.isdir(root):
            print(f"缺语料目录: {root}", file=sys.stderr)
            return 1
        per_plat[p] = sorted(os.listdir(root))
        print(f"{p:<11s} {len(per_plat[p])} 份")

    # the three corpora must hold the same designs, or "one global assignment"
    # is not a coherent statement
    keys = {p: {design_key(n) for n in v} for p, v in per_plat.items()}
    base = keys["sky130hd"]
    for p, k in keys.items():
        if k != base:
            print(f"  注意: {p} 与 sky130hd 的设计集差 "
                  f"{len(k ^ base)} 份（分配仍按族名，不受影响）")

    fams = sorted({family(n) for v in per_plat.values() for n in v})
    assign = {f: side(f) for f in fams}
    nd = sum(1 for f in fams if assign[f] == "dev")
    print(f"\n族 {len(fams)} 个 -> dev {nd} / heldout {len(fams)-nd}")

    # designs per side, per platform
    tally = collections.Counter()
    for p, v in per_plat.items():
        for n in v:
            tally[(p, assign[family(n)])] += 1
    print(f"\n{'平台':<11s}{'dev':>8s}{'heldout':>10s}")
    for p in CORPORA:
        print(f"{p:<11s}{tally[(p,'dev')]:>8d}{tally[(p,'heldout')]:>10d}")

    # Failure counts per side -- REPORTED, never used to steer the draw.
    # Read from each run's own result.json, not from knowledge.sqlite: the DB's
    # design_name is the bare design ("cordic") and its project_path is the
    # bridge's synthetic directory, so neither carries the repo this split is
    # keyed on. The corpus directory name does.
    fail = collections.Counter()
    for p, root in CORPORA.items():
        for n in per_plat[p]:
            s_ = assign.get(family(n))
            if s_ is None:
                continue
            try:
                r = json.load(open(os.path.join(root, n, "result.json")))
            except Exception:
                fail[(p, s_)] += 1          # no verdict is not a pass
                continue
            if not (r.get("drc_status") in ("clean", "clean_beol")
                    and r.get("lvs_status") == "clean"):
                fail[(p, s_)] += 1
    print(f"\n（事后报告，未参与抽签）每侧未双清的设计数")
    print(f"{'平台':<11s}{'dev':>8s}{'heldout':>10s}")
    for p in CORPORA:
        print(f"{p:<11s}{fail[(p,'dev')]:>8d}{fail[(p,'heldout')]:>10d}")

    # what development has already touched
    used = set()
    for d in USED_DIRS:
        if os.path.isdir(d):
            for n in os.listdir(d):
                used.add(family(n.replace("__base", "")))
    seen_heldout = sorted(f for f in used if assign.get(f) == "heldout")
    print(f"\n开发期已接触的族 {len(used & set(fams))} 个，"
          f"其中落在 heldout 的 {len(seen_heldout)} 个")
    for f in seen_heldout[:8]:
        print(f"   {f}")

    if not a.write:
        print("\n（未写入；加 --write 冻结）")
        return 0
    if os.path.exists(OUT):
        print(f"\n拒绝覆盖已冻结的 {OUT}", file=sys.stderr)
        return 2
    doc = {"version": "r2g-split-v1",
           "rule": "sha256('r2g-split-v1:'+family)[:8] % 2 == 0 -> dev",
           "family_field": "the 3rd '__' field of the run dir = the repo",
           "scope": "one global assignment shared by all three platforms",
           "families": assign,
           "seen_in_dev_but_heldout": seen_heldout,
           "counts": {f"{p}/{s}": tally[(p, s)]
                      for p in CORPORA for s in ("dev", "heldout")}}
    body = json.dumps(doc, indent=1, sort_keys=True)
    doc["digest"] = hashlib.sha256(body.encode()).hexdigest()[:16]
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    print(f"\n已冻结 {OUT}  digest={doc['digest']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
