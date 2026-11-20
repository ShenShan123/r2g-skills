#!/usr/bin/env bash
set -euo pipefail
cd /home/yangao/r2g-skills
YOSYS=/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys
FORMAL=experiments/asap7_qualification_20261116/reference_diagnosis/formal
"$YOSYS" -Q -T -l "$FORMAL/equiv.log" "$FORMAL/equiv.ys"
"$YOSYS" -Q -T -l "$FORMAL/reset_base.log" "$FORMAL/reset_base.ys"
"$YOSYS" -Q -T -l "$FORMAL/mutant_prepare.log" "$FORMAL/mutant_prepare.ys"
if "$YOSYS" -Q -T -l "$FORMAL/mutant_base.log" "$FORMAL/mutant_base.ys"; then
    echo 'ERROR: output-inversion negative control unexpectedly passed' >&2
    exit 1
fi
rg -q 'SAT proof finished - model found: FAIL!' "$FORMAL/mutant_base.log"
echo 'PASS: induction, reset/request base case, and output-inversion negative control'
