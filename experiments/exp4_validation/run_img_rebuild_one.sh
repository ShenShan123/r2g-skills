#!/bin/bash
# V2 (images): re-run the SHIPPED cnn_pilot500 dump_design.py + ir_design.tcl on memlab203 (nice 10).
# rasterize.py is then run on 208 with the original r2g-cnn venv (see v2_img_compare.py).
X=$1; W=/data/yangao/exp4_validation_20261107; O=$W/v2img/dumps/$X; mkdir -p $O $W/logs
export DESIGN_DIR=/data/r2g_n45_pilot500/nangate45_pilot500/$X OUT_DIR=$O PLATFORM_DIR=/data/yangao/orfs203/flow/platforms/nangate45
export MIN_ROUTING_LAYER=metal2 MAX_ROUTING_LAYER=metal10 MIN_CLK_ROUTING_LAYER=metal4 MAX_CLK_ROUTING_LAYER=metal10
OR=/home/yangao/r2g_toolchain/bin/openroad
{
  PYTHONHOME=/home/yangao/r2g_toolchain/oss-cad-suite nice -n 10 timeout 3600 $OR -no_init -no_splash -exit -threads 1 -python $W/img_builder_src/dump_design.py
  nice -n 10 timeout 3600 $OR -no_init -no_splash -exit -threads 1 $W/img_builder_src/ir_design.tcl
} > $W/logs/v2img_$X.log 2>&1
ls $O | tr '\n' ' '; echo $X
