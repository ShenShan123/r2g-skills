#!/bin/bash
# Deploy the Exp 4 (conversion) code to memlab216 by streaming over ssh (scp is disabled on
# 216) and verify every file's sha256 on both sides.
#   deploy216.sh            -> code (exp4_conversion, exp4_validation/{gt_raw,match}.py, agent/r2g_agent)
#   deploy216.sh --skill    -> also the def-graph skill at git HEAD (git archive, no working-tree edits)
set -euo pipefail
REPO=/home/yangao/r2g-skills
W=/proj/workarea/yangao/exp4_conversion_20261107
cd "$REPO"
FILES=$(find experiments/exp4_conversion -type f ! -name "*.pyc" ! -path "*/__pycache__/*" | sort | tr "\n" " ")
FILES="$FILES experiments/exp4_validation/gt_raw.py experiments/exp4_validation/match.py experiments/exp4_validation/sample.json experiments/exp4_validation/dev_designs.json"
FILES="$FILES $(find agent/r2g_agent -type f -name "*.py" ! -path "*/__pycache__/*" | sort | tr "\n" " ")"
tar -cf - $FILES | ssh memlab216 "mkdir -p $W/code && tar -C $W/code -xf -"
sha256sum $FILES > /tmp/exp4c_local.sha
ssh memlab216 "cd $W/code && sha256sum $FILES" > /tmp/exp4c_remote.sha
diff /tmp/exp4c_local.sha /tmp/exp4c_remote.sha && echo "code: $(wc -l < /tmp/exp4c_local.sha) files sha256-identical"
if [[ "${1:-}" == "--skill" ]]; then
  C=$(git rev-parse HEAD)
  git archive "$C" r2g-skills/def-graph | ssh memlab216 "rm -rf $W/r2g_runtime && mkdir -p $W/r2g_runtime && tar -C $W/r2g_runtime -xf - && echo $C > $W/r2g_runtime/COMMIT"
  git ls-tree -r "$C" r2g-skills/def-graph | awk '{print $3, $4}' | while read -r blob path; do echo "$(git cat-file -p "$blob" | sha256sum | cut -d' ' -f1)  $path"; done | sort -k2 > /tmp/exp4c_skill_local.sha
  ssh memlab216 "cd $W/r2g_runtime && find r2g-skills -type f | sort | xargs sha256sum" | sort -k2 > /tmp/exp4c_skill_remote.sha
  diff /tmp/exp4c_skill_local.sha /tmp/exp4c_skill_remote.sha && echo "skill @ $C: $(wc -l < /tmp/exp4c_skill_local.sha) files sha256-identical"
fi
