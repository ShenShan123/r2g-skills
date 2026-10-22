#!/usr/bin/env bash
# Reference critical-path delay of a pilot design under the NEW constraint style,
# measured on the pilot's own final layout: STA on 6_final.odb + 6_final.spef
# with the tight-run SDC at the pilot's 2 ns. ref_delay = 2 - worst_slack.
# Usage (inside the r2g-orfs image): measure_ref.sh <run_dir> <sdc_template>
set -u
RUN=$1; TPL=$2
T=$(mktemp -d)
sed 's/PER_NS/2.0/' "$TPL" > $T/m.sdc
cat > $T/m.tcl <<TCL
read_liberty \$::env(ORFS_ROOT)/flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib
read_db $RUN/6_final.odb
read_sdc $T/m.sdc
read_spef $RUN/6_final.spef
puts "R2G_WS [sta::worst_slack_cmd max]"
puts "R2G_TNS [sta::total_negative_slack_cmd max]"
TCL
openroad -no_init -exit $T/m.tcl 2>&1 | grep -E "^R2G_|ERROR" | tr '\n' ' '
rm -rf $T
