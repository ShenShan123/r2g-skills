#!/bin/bash
# Deploy files to memlab203 via ssh stdin (no scp) and verify sha256 on both sides.
set -e
R=/data/yangao/exp4_validation_20261107/code
ssh memlab203 "mkdir -p $R"
for f in "$@"; do
  ssh memlab203 "cat > $R/$(basename $f)" < "$f"
  a=$(sha256sum "$f" | cut -d' ' -f1); b=$(ssh memlab203 "sha256sum $R/$(basename $f)" | cut -d' ' -f1)
  [ "$a" = "$b" ] && echo "OK $f $a" || { echo "SHA MISMATCH $f"; exit 1; }
done
