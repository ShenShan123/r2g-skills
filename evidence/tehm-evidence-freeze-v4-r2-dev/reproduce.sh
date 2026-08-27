#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
export PYTHONPATH="$ROOT/source/memory${PYTHONPATH:+:$PYTHONPATH}"
python3 "$ROOT/source/memory/scripts/reproduce_v4_dev.py" --bundle "$ROOT"
