#!/bin/bash
# Exp 4 (conversion): once a model's development run has finished, apply its FROZEN converter
# to the 43 test designs (no model, no network).  Usage: apply_llm.sh deepseek|gpt [graph|image] [workers]
set -euo pipefail
m=$1; task=${2:-graph}; workers=${3:-2}
W=/proj/workarea/yangao/exp4_conversion_20261107
H=$W/code/experiments/exp4_conversion/harness
if [[ $task == graph ]]; then DEV=$W/dev/$m; OUT=$W/apply/$m; SPEC=$W/code/experiments/exp4_conversion/spec
else DEV=$W/dev_img/$m; OUT=$W/apply_img/$m; SPEC=$W/code/experiments/exp4_conversion/spec_image; fi
until [[ -f $DEV/result.json ]]; do sleep 120; done
if [[ ! -f $DEV/frozen/converter/run.sh ]]; then echo "$m: no converter produced"; exit 0; fi
( cd $DEV && sha256sum -c converter.tar.sha256 )      # frozen copy must still match the recorded sha256
python3 $H/apply_arm.py --arm llm --task $task --label $m --converter $DEV/frozen/converter \
  --designs $W/data/test_designs.txt --data-root $W/data/designs --platform $W/data/platform \
  --spec $SPEC --out-root $OUT --workers $workers --max-load 110
