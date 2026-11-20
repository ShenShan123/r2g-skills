#!/usr/bin/env bash
set -euo pipefail
exec python3 /r2g/generate_and_validate.py --kind graph "$@"
