#!/bin/bash
# Exp 4 (conversion), 216: build the image and lay out the read-only input trees.
#   data/platform/          the 3 Nangate45 platform files, copied out of localhost/r2g-orfs:26Q3
#   data/inputs/<design>/   hard links of ONLY 5_route.def, 6_final.spef, constraint.sdc
#   data/dev_inputs/        the 6 dev designs' input dirs (what the LLM dev container mounts)
#   data/smoke_inputs/      one small dev design (harness smoke test)
set -euo pipefail
W=/proj/workarea/yangao/exp4_conversion_20261107
C=$W/code/experiments/exp4_conversion
cp $C/harness/Containerfile $W/build/Containerfile
podman build -t localhost/exp4c-conv:1 -f $W/build/Containerfile $W/build > $W/logs/image_build.log 2>&1
podman image inspect localhost/exp4c-conv:1 --format '{{.Id}}' > $W/logs/image_id.txt
mkdir -p $W/data/platform
cid=$(podman create localhost/r2g-orfs:26Q3)
P=/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/nangate45
podman cp $cid:$P/lef/NangateOpenCellLibrary.tech.lef $W/data/platform/
podman cp $cid:$P/lef/NangateOpenCellLibrary.macro.mod.lef $W/data/platform/
podman cp $cid:$P/lib/NangateOpenCellLibrary_typical.lib $W/data/platform/
podman rm $cid > /dev/null
chmod a-w $W/data/platform/*
sha256sum $W/data/platform/* > $W/logs/platform.sha256
for d in $(ls $W/data/designs); do
  mkdir -p $W/data/inputs/$d
  for f in 5_route.def 6_final.spef constraint.sdc; do
    [[ -e $W/data/inputs/$d/$f ]] || ln $W/data/designs/$d/$f $W/data/inputs/$d/$f
  done
done
python3 - <<EOF
import json, os
W = "$W"
dev = json.load(open(f"{W}/code/experiments/exp4_validation/dev_designs.json"))["designs"] if os.path.exists(f"{W}/code/experiments/exp4_validation/dev_designs.json") else None
s = json.load(open(f"{W}/code/experiments/exp4_validation/sample.json"))
test = s["v1_all"]
assert dev and len(dev) == 6 and len(test) == 43 and not set(dev) & set(test)
open(f"{W}/data/dev_designs.txt", "w").write("\n".join(dev) + "\n")
open(f"{W}/data/test_designs.txt", "w").write("\n".join(test) + "\n")
for d in dev:
    os.makedirs(f"{W}/data/dev_inputs/{d}", exist_ok=True)
    for f in ("5_route.def", "6_final.spef", "constraint.sdc"):
        t = f"{W}/data/dev_inputs/{d}/{f}"
        if not os.path.exists(t):
            os.link(f"{W}/data/designs/{d}/{f}", t)
sm = "C03005__counter__teroshdl-examples__nangate45__p2"
os.makedirs(f"{W}/data/smoke_inputs/{sm}", exist_ok=True)
for f in ("5_route.def", "6_final.spef", "constraint.sdc"):
    t = f"{W}/data/smoke_inputs/{sm}/{f}"
    if not os.path.exists(t):
        os.link(f"{W}/data/designs/{sm}/{f}", t)
print("dev", len(dev), "test", len(test))
EOF
chmod -R a-w $W/data/inputs $W/data/dev_inputs $W/data/smoke_inputs $W/data/designs
echo PREPARE_DONE
