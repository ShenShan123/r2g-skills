#!/usr/bin/env python3
"""Recompute `seen_in_dev_but_heldout` in the frozen split, which was wrong.

The split ASSIGNMENT is unaffected and is not touched here: it is derived from
the corpus directory names, which parse correctly. Only the record of which
families development had already touched was computed with the wrong parser, and
that record is the one thing a later held-out result has to be honest about.

Two naming schemes exist and the first version assumed one of them:

  corpus     C00016__eq_cascade__sonic-speed-parallelism__sky130hd__p5
             -> repo is field 2
  trial      ao486__nangate45__C03434__pc_dma__ao486__nangate45__p2
             -> repo is field 0 (the staging script prefixes it)

Reading field 2 of a trial directory yields `C03434`, which matches no family at
all, so touched families went silently uncounted -- the failure mode being that
an under-reported contamination list looks exactly like a clean one.
"""
import hashlib, json, os, re, sys

D = "/proj/workarea/yangao"
SPLIT = f"{D}/split_frozen.json"
USED = [f"{D}/ratio_trial", f"{D}/_dkb", f"{D}/res_m32", f"{D}/runb_ant"]
CID = re.compile(r"^C\d+$")


def family(dirname):
    n = dirname.replace("__base", "")
    f = n.split("__")
    if not f:
        return n
    if CID.match(f[0]):                      # corpus style
        return f[2] if len(f) >= 3 else n
    return f[0]                              # trial style: repo first


def main():
    doc = json.load(open(SPLIT))
    assign = doc["families"]
    touched, unknown = set(), set()
    for d in USED:
        if not os.path.isdir(d):
            continue
        for n in os.listdir(d):
            f = family(n)
            (touched if f in assign else unknown).add(f)
    seen = sorted(f for f in touched if assign[f] == "heldout")
    old = doc.get("seen_in_dev_but_heldout", [])
    print(f"开发期接触的族: {len(touched)}（旧记为 {len(old)} 条命中 heldout）")
    print(f"  其中 dev     {sum(1 for f in touched if assign[f]=='dev')}")
    print(f"  其中 heldout {len(seen)}")
    if unknown:
        print(f"  未匹配到族的目录名 {len(unknown)} 种（示例 "
              f"{sorted(unknown)[:3]}）")
    doc["seen_in_dev_but_heldout"] = seen
    doc["seen_source_dirs"] = [os.path.basename(d) for d in USED if os.path.isdir(d)]
    doc["amendments"] = doc.get("amendments", []) + [
        "2026-10-15: recomputed seen_in_dev_but_heldout with a naming-aware "
        "family parser; the trial directories put the repo in field 0 while the "
        "corpus puts it in field 2, so touched families had gone uncounted. The "
        "split assignment itself is unchanged."]
    doc.pop("digest", None)
    body = json.dumps(doc, indent=1, sort_keys=True)
    doc["digest"] = hashlib.sha256(body.encode()).hexdigest()[:16]
    json.dump(doc, open(SPLIT, "w"), indent=1, sort_keys=True)
    print(f"\n已更新 {SPLIT}  新 digest={doc['digest']}")
    print(f"落在 heldout 的已接触族（结论须排除或声明）:")
    for f in seen[:12]:
        print(f"   {f}")
    if len(seen) > 12:
        print(f"   …… 共 {len(seen)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
