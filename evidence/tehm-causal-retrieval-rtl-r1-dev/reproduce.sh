#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$ROOT/../.." && pwd)
export PYTHONPATH="$REPO/memory${PYTHONPATH:+:$PYTHONPATH}"
python3 "$REPO/memory/scripts/build_rtl_causal_retrieval_report.py" \
  --source-db "$REPO/evidence/tehm-evidence-freeze-v4-dev/closed_loop/tehm.sqlite" \
  --output-dir "$ROOT" --overwrite
