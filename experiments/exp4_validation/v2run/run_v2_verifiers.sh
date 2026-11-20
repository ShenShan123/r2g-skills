#!/bin/bash
# def-graph's OWN verifiers over the v2 proof projects (memlab203, nice 10).
W=/data/yangao/exp4_validation_20261107; V=$W/exp4v2; P=$V/projects; R=$W/defgraph/repo
export R2G_ENV_FILE=/data/yangao/r2g-api-repair-cost-20261028/r2g_vs_bare/tools203_data.env OPENROAD_EXE=/home/yangao/r2g_toolchain/bin/openroad
VPY=$W/venv_rebuild/bin/python; GPY=/data/yangao/r2g-api-repair-cost-20261028/bin/graph-python
mkdir -p $V/results/own
nice -n 10 $VPY $R/tools/verify_graph_dataset.py --batch $P --json $V/results/own/verify_graph_dataset.json > $V/results/own/verify_graph_dataset.log 2>&1
echo "verify_graph_dataset rc=$?"; grep -E "FAIL\]" $V/results/own/verify_graph_dataset.log | sed -E 's/ +/ /g' | cut -c1-160 | sort | uniq -c | sort -rn | head -30
for d in $P/*/; do X=$(basename $d); G=$d/dataset_4stage/generated; s=""; v=""
  if [ -d $G ]; then s=$(nice -n 10 $GPY $R/r2g-skills/def-graph/scripts/r2g2/checks/summarize_four_stage_graph_data.py --root $G 2>&1 | grep -o "structural_issues[^,}]*" | head -1)
    v=$(python3 -c "import json;print(json.load(open('$G/four_stage.validation.json'))['status'])" 2>/dev/null); fi
  i=$(python3 -c "import json;r=json.load(open('$d/reports/image_dataset.json'));print(r.get('status'), r.get('verify',{}).get('passed'), '/', r.get('verify',{}).get('total'))" 2>/dev/null)
  echo "$X | 4stage validate=$v summarize:$s | images: $i"
done
