#!/bin/bash
# CNN label decomposition for one design (memlab203, nice 10): odb read only, no re-route.
X=$1; W=/data/yangao/exp4_validation_20261107; cd $W; mkdir -p cong/$X cong_alt_npz logs
{ [ -s cong/$X/cong_decomp.json ] || DESIGN_DIR=/data/r2g_n45_pilot500/nangate45_pilot500/$X OUT_DIR=cong/$X PYTHONHOME=/home/yangao/r2g_toolchain/oss-cad-suite \
    nice -n 10 timeout 3600 /home/yangao/r2g_toolchain/bin/openroad -no_init -no_splash -exit -threads 1 -python code/cong_decomp_dump.py
  nice -n 10 /data/yangao/r2g-api-repair-cost-20261028/bin/graph-python -I code/cong_alt.py cong/$X/cong_decomp.json img_npz_208/npz/$X.npz cong_alt_npz/$X.npz cong/$X/stats.json
} > logs/cong_$X.log 2>&1
tail -1 logs/cong_$X.log
