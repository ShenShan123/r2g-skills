#!/usr/bin/env bash
# run_main.sh GPU -- the main study: 4 models x 3 seeds, 150 epochs, design-disjoint split, 4 jobs at a time on one GPU
cd /home/yangao/r2g-skills/experiments/multimodal
P=/home/yangao/.conda/envs/gnn_blackwell/bin/python; G=${1:-4}; O=/home/yangao/mm_runs/main
mkdir -p $O
for s in 0 1 2; do for m in unet gnn graft exchange; do echo "$m $s"; done; done | \
  xargs -P 4 -L 1 bash -c '[ -f '$O'/$0_s$1/metrics.json ] || '$P' train_mm.py --data /home/yangao/mm_data --model $0 --seed $1 --epochs 150 --device cuda:'$G' --out '$O'/$0_s$1 > '$O'/$0_s$1.log 2>&1; tail -1 '$O'/$0_s$1.log'
