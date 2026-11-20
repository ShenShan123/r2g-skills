#!/bin/bash
# def-graph's OWN verifiers over every project of a tag (memlab203, nice 10).
TAG=$1; W=/data/yangao/exp4_validation_20261107; P=$W/defgraph/projects/$TAG; R=$W/defgraph/repo
export R2G_ENV_FILE=/data/yangao/r2g-api-repair-cost-20261028/r2g_vs_bare/tools203_data.env OPENROAD_EXE=/home/yangao/r2g_toolchain/bin/openroad
VPY=$W/venv_rebuild/bin/python; GPY=/data/yangao/r2g-api-repair-cost-20261028/bin/graph-python
mkdir -p $W/dgv1/$TAG
nice -n 10 $VPY $R/tools/verify_graph_dataset.py --batch $P --json $W/dgv1/$TAG/verify_graph_dataset.json --signoff-recheck > $W/dgv1/$TAG/verify_graph_dataset.log 2>&1
echo "verify_graph_dataset rc=$?"; grep -E "^\s+\[(PASS|FAIL)\] C|FAIL\]" $W/dgv1/$TAG/verify_graph_dataset.log | grep -v "signoff.gate verdict pass" | sort | uniq -c | head -20
for d in $P/*/; do X=$(basename $d); G=$d/dataset_4stage/generated
  s=$(nice -n 10 $GPY $R/r2g-skills/def-graph/scripts/r2g2/checks/summarize_four_stage_graph_data.py --root $G 2>&1 | grep -o "structural_issues[^,}]*" | head -1)
  v=$(python3 -c "import json;print(json.load(open('$G/four_stage.validation.json'))['status'])" 2>/dev/null)
  i=$(python3 -c "import json;r=json.load(open('$d/reports/image_dataset.json'));print(r.get('status'), r.get('verify',{}).get('passed'), '/', r.get('verify',{}).get('total'))" 2>/dev/null)
  echo "$X | 4stage validate=$v summarize:$s | images: $i"
done
