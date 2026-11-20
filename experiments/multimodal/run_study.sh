#!/usr/bin/env bash
# run_study.sh DATA OUT GPU [MODELS] -- 4 models x 3 seeds, 150 epochs, same protocol as run_main.sh, 4 jobs at a time
cd /home/yangao/r2g-skills/experiments/multimodal
P=/home/yangao/.conda/envs/gnn_blackwell/bin/python; D=$1; O=$2; G=$3; MODELS=${4:-"unet gnn graft exchange"}
mkdir -p $O
for s in 0 1 2; do for m in $MODELS; do echo "$m $s"; done; done | \
  xargs -P 4 -L 1 bash -c '[ -f '$O'/$0_s$1/metrics.json ] || '$P' train_mm.py --data '$D' --model $0 --seed $1 --epochs 150 --device cuda:'$G' --out '$O'/$0_s$1 > '$O'/$0_s$1.log 2>&1; tail -1 '$O'/$0_s$1.log'
