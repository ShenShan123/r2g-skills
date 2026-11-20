#!/bin/bash
# V3 images: build counterfactual design dirs and run the SHIPPED dump + IR on them (memlab203, nice 10).
X=$1; DONOR=$2; W=/data/yangao/exp4_validation_20261107; RAW=/data/r2g_n45_pilot500/nangate45_pilot500
OR=/home/yangao/r2g_toolchain/bin/openroad; P=/data/yangao/orfs203/flow/platforms/nangate45
for VAR in perturbed donor; do
  C=$W/v3img/raw_$VAR/$X; rm -rf $C; mkdir -p $C
  for f in 3_place.def 4_cts.def constraint.sdc config.mk; do ln -s $RAW/$X/$f $C/$f; done
  if [ $VAR = perturbed ]; then
    SRC_ODB=$RAW/$X/5_1_grt.odb DST_ODB=$C/5_1_grt.odb nice -n 10 timeout 3600 $OR -no_init -no_splash -exit -threads 1 $W/code/v3_img_regr.tcl > $W/logs/v3img_regr_$X.log 2>&1
    cp $RAW/$DONOR/5_route.def $C/5_route.def; cp $RAW/$DONOR/6_final.spef $C/6_final.spef   # IR-label inputs from donor
  else
    cp $RAW/$DONOR/5_1_grt.odb $C/5_1_grt.odb; cp $RAW/$DONOR/5_route.def $C/5_route.def; cp $RAW/$DONOR/6_final.spef $C/6_final.spef
  fi
  O=$W/v3img/dumps_$VAR/$X; mkdir -p $O
  export DESIGN_DIR=$C OUT_DIR=$O PLATFORM_DIR=$P MIN_ROUTING_LAYER=metal2 MAX_ROUTING_LAYER=metal10 MIN_CLK_ROUTING_LAYER=metal4 MAX_CLK_ROUTING_LAYER=metal10
  TR=""; [ "$3" = trace ] && [ $VAR = perturbed ] && TR="strace -f -qq -s 4096 -e trace=openat -e signal=none -o $W/v3img/strace_dump_$X.txt"
  { PYTHONHOME=/home/yangao/r2g_toolchain/oss-cad-suite nice -n 10 $TR timeout 3600 $OR -no_init -no_splash -exit -threads 1 -python $W/img_builder_src/dump_design.py
    nice -n 10 timeout 3600 $OR -no_init -no_splash -exit -threads 1 $W/img_builder_src/ir_design.tcl; } > $W/logs/v3img_${VAR}_$X.log 2>&1
  echo "$VAR $X: $(ls $O | tr '\n' ' ') $(grep -c REGR_OK $W/logs/v3img_regr_$X.log 2>/dev/null)"
done
