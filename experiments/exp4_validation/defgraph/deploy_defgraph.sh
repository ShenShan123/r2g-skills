#!/bin/bash
# Deploy the working-tree def-graph skill + tools/verify_graph_dataset.py to memlab203 (ssh stdin,
# no scp), excluding caches and the 208-specific env.local.sh; verifies a content digest both sides.
set -e
R=/home/yangao/r2g-skills; DST=/data/yangao/exp4_validation_20261107/defgraph/repo
cd $R
LIST=$(find r2g-skills/def-graph r2g-skills/signoff-loop/scripts/flow r2g-skills/signoff-loop/scripts/extract tools/verify_graph_dataset.py -type f ! -path '*__pycache__*' ! -name 'env.local.sh*' ! -name '*.pyc' | sort)
a=$(for f in $LIST; do sha256sum "$f"; done | sha256sum | cut -c1-16)
ssh memlab203 "rm -rf $DST && mkdir -p $DST"
tar cf - $LIST | ssh memlab203 "cd $DST && tar xf -"
b=$(ssh memlab203 "cd $DST && for f in \$(find r2g-skills/def-graph r2g-skills/signoff-loop/scripts/flow r2g-skills/signoff-loop/scripts/extract tools/verify_graph_dataset.py -type f ! -path \*__pycache__\* | sort); do sha256sum \"\$f\"; done | sha256sum | cut -c1-16")
[ "$a" = "$b" ] && echo "DEPLOY_OK $a ($(echo $LIST | wc -w) files)" || { echo "DIGEST MISMATCH $a $b"; exit 1; }
