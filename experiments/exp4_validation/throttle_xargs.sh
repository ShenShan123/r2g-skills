#!/bin/bash
# Run run_all500_one.sh over a list with <=5 workers; each worker waits while load/CPU > 1.2.
W=/data/yangao/exp4_validation_20261107; NC=$(nproc)
wait_ok() { while awk -v n=$NC '{exit !($1/n > 1.2)}' /proc/loadavg; do sleep 60; done; }
export -f wait_ok; export NC W
cat $1 | xargs -P 5 -I{} bash -c 'wait_ok; bash $W/code/run_all500_one.sh {}'
echo ALL500_DONE
