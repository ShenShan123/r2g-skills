#!/usr/bin/env bash
set -euo pipefail
# Shared by exploration and every engineer-loop/fix_signoff flow caller.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$here/t2_history_collect_run.py" guard --root "${R2G_HISTORY_ROOT:?}" --project "${1:?}"
exec bash "$here/../../r2g-skills/signoff-loop/scripts/flow/run_orfs.sh" "$@"
