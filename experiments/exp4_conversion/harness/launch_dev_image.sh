#!/bin/bash
# Exp 4 (conversion): start the two IMAGE development runs once 216 has headroom
# (1-min load < MAX_LOAD, MemAvailable > 200 GB, re-checked every minute).
set -u
W=/proj/workarea/yangao/exp4_conversion_20261107
H=$W/code/experiments/exp4_conversion/harness
MAX_LOAD=${MAX_LOAD:-110}
while :; do
  l=$(cut -d' ' -f1 /proc/loadavg); m=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
  if awk -v l="$l" -v m="$m" -v L="$MAX_LOAD" 'BEGIN{exit !(l < L && m > 200)}'; then break; fi
  echo "$(date +%T) waiting: load $l mem ${m}G"; sleep 60
done
echo "$(date +%T) launching (load $l mem ${m}G)"
mkdir -p $W/dev_img
for m in deepseek gpt; do
  if [ $m = deepseek ]; then A="--base-url http://127.0.0.1:18774/v1 --model deepseek-flash --protocol openai"
  else A="--base-url http://127.0.0.1:18775/v1 --model gpt-6.1-sol --protocol openai-responses"; fi
  PYTHONPATH=/proj/workarea/yangao/bare216/pydeps nohup setsid python3 $H/run_dev_agent.py --task image --out $W/dev_img/$m \
    --dev-root $W/data/dev_inputs_img --platform $W/data/platform --spec $W/code/experiments/exp4_conversion/spec_image \
    $A --max-calls 100 --wall-h 12 --agent-src $W/code/agent > $W/dev_img/$m.log 2>&1 < /dev/null &
  echo "$m pid $!"
done
