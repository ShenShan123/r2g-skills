#!/usr/bin/env bash
# read ONLY this design's rtl/ folder and elaborate the hierarchy from its top
d=$1; R=/home/yangao/p500_tight/rtl_out/$d/rtl
Y=/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys
top=$(sed -n 's/^top module  //p' $R/MANIFEST.txt)
srcs=$(awk '/^source files/{f=1;next} /^$/{f=0} f{print $1}' $R/MANIFEST.txt)
incs=$(awk '/^include dirs/{f=1;next} /^$/{f=0} f{print "-I" $1}' $R/MANIFEST.txt | tr '\n' ' ')
cd $R && timeout 600 $Y -q -p "verilog_defaults -add $incs; read_verilog -defer -sv $(echo $srcs); hierarchy -check -top $top" > /tmp/claude-1017/-home-yangao-r2g-skills/67dc6ad5-abbc-4074-9e7d-c49c08a66f4a/scratchpad/chk_$d.log 2>&1 \
  && echo "OK $d" || echo "FAIL $d $(grep -m1 -E 'ERROR' /tmp/claude-1017/-home-yangao-r2g-skills/67dc6ad5-abbc-4074-9e7d-c49c08a66f4a/scratchpad/chk_$d.log | cut -c1-140)"
