#!/usr/bin/env bash
# Steps 1-3 of the learning-loop landing, unattended.
#
#   wait for chain180.sh  ->  bridge 7,491 runs  ->  batch ingest into a COPY
#   ->  learn + mine  ->  honesty gates  ->  reconcile against frozen numbers
#
# It writes ONLY to $D/ingest_proj, $D/knowledge_campaign.sqlite and its own
# logs. The git-tracked knowledge/knowledge.sqlite is never opened for write:
# whether to merge is a decision for after the diff is read.
#
# Waits first because gf180's DRC/LVS signoff is still running -- bridging a
# half-signed-off corpus would freeze partial verdicts into the store.
set -uo pipefail
D=/proj/workarea/yangao
K=$D/r2g/signoff-loop/knowledge
L=$D/chain_ingest.log
E=$D/baseline216                     # the three scripts, scp'd beside the corpus
say() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$L"; }

say "chain_ingest: start"

# ── 0. wait for the gf180 signoff chain ──────────────────────────────────────
# bracket trick so the grep cannot match its own command line (the pgrep -f
# self-match trap: the matcher's own argv contains the pattern).
CPID=$(ps -eo pid=,args= | grep -E '[c]hain180\.sh' | awk '{print $1}' | head -1)
if [ -n "${CPID:-}" ]; then
  say "等 chain180.sh (pid $CPID) 结束"
  while kill -0 "$CPID" 2>/dev/null; do sleep 120; done
  say "chain180.sh 已退出"
else
  say "chain180.sh 不在运行，直接继续"
fi
say "chain180 尾部: $(tail -3 $D/chain180.log | tr '\n' ' | ')"

# ── 1. bridge ────────────────────────────────────────────────────────────────
say "步骤 1/5 桥: result.json -> reports/*.json"
rm -rf "$D/ingest_proj"
python3 "$D/bridge_result_json.py" --out "$D/ingest_proj" >>"$L" 2>&1 \
  || say "桥 非零退出"
say "桥完成: $(ls -d $D/ingest_proj/*/ 2>/dev/null | wc -l) 个项目"

# ── 2. re-measure the oracle (gf180 is only now complete) ────────────────────
say "步骤 2/5 重新量期望值 (gf180 签核刚完成)"
python3 "$D/reconcile_ingest.py" measure --out "$D/ingest_expected.json" \
  >>"$D/reconcile_measure.log" 2>&1 || say "measure 非零退出"

# ── 3. batch ingest into a COPY ──────────────────────────────────────────────
say "步骤 3/5 全量 ingest -> knowledge_campaign.sqlite (副本，不碰仓库那份)"
cp -f "$D/knowledge.sqlite" "$D/knowledge_campaign.sqlite"
( cd "$K" && python3 ingest_batch.py \
    --projects "$D/ingest_proj" --db "$D/knowledge_campaign.sqlite" \
    --errors "$D/ingest_errors.tsv" ) >>"$D/ingest_batch.log" 2>&1 \
  || say "ingest 非零退出"
say "ingest 完成: $(tail -1 $D/ingest_batch.log)"

# ── 4. learn + mine ──────────────────────────────────────────────────────────
say "步骤 4/5 learn + mine"
( cd "$K" && R2G_KNOWLEDGE_DB="$D/knowledge_campaign.sqlite" \
    python3 learn_heuristics.py --db "$D/knowledge_campaign.sqlite" \
      --out "$D/heuristics_campaign.json" ) \
  >>"$D/learn.log" 2>&1 || say "learn 非零退出"
( cd "$K" && R2G_KNOWLEDGE_DB="$D/knowledge_campaign.sqlite" \
    python3 mine_rules.py --db "$D/knowledge_campaign.sqlite" \
      --out "$D/failure_candidates_campaign.json" ) \
  >>"$D/mine.log" 2>&1 || say "mine 非零退出"

# ── 5. gates ─────────────────────────────────────────────────────────────────
say "步骤 5/5 诚实闸门 + 对账"
( cd "$K" && python3 honesty.py --db "$D/knowledge_campaign.sqlite" ) \
  >"$D/honesty_after.log" 2>&1 || say "honesty 非零退出"
python3 "$D/reconcile_ingest.py" verify \
  --expected "$D/ingest_expected.json" --db "$D/knowledge_campaign.sqlite" \
  >"$D/reconcile_verify.log" 2>&1 || say "对账有不一致项"

say "===== 结果摘要 ====="
say "honesty : $(tail -1 $D/honesty_after.log)"
say "对账    : $(tail -1 $D/reconcile_verify.log)"
say "ingest  : $(tail -1 $D/ingest_batch.log)"
say "新 recipe 候选: $(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(len(d) if isinstance(d,list) else len(d.get(\"candidates\",d)))' "$D/failure_candidates_campaign.json" 2>/dev/null || echo 读取失败)"
say "chain_ingest: all done"
