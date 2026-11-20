#!/usr/bin/env bash
# partest.sh: R2G_JOBS=1 vs 8 on three dev designs; timing + exact equality with the original converter's output
GP=/home/yangao/r2g_toolchain/bin/r2g_graph_python
cd /home/yangao/exp4_r2g_tune
for d in C00016__eq_cascade__sonic-speed-parallelism__sky130hd__p5 C00833__alarm_clock_top__alarm_clock__nangate45__p2 C03397__a_axi_write_broadcastor_1_to_4__tapa__gf180__p10; do
  for j in 1 8; do
    R2G_JOBS=$j /usr/bin/time -f "%e" -o t.txt ./convert_local.sh dev/$d par_$j > par_$j.log 2>&1
    echo "$d jobs=$j wall=$(cat t.txt)s ok=$(grep -c ': ok' par_$j.log) failed=$(grep -c FAILED par_$j.log) load=$(cut -d' ' -f1 /proc/loadavg)"
  done
  echo "  dataset vs original: $($GP cmp_ds.py runs/orig/$d/project/dataset par_8/project/dataset | tail -1)"
  for f in features labels; do echo "  $f diff vs original: $(diff -rq runs/orig/$d/project/$f par_8/project/$f --exclude='*.log' | wc -l) files"; done
done
echo DONE
