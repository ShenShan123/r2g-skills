#!/usr/bin/env bash
# Run inside a provisioned, isolated Linux environment as an ordinary user.
# No tool/PDK directories or credentials may be mounted from the host.
set -euo pipefail
repo="${1:?usage: run_clean_install.sh REPO PREFIX EVIDENCE}"
prefix="${2:?missing installation prefix}"
evidence="${3:?missing evidence directory}"
platforms="${4:-nangate45,sky130hd,gf180}"
[[ $(id -u) != 0 ]] || { echo 'Run the installation as a non-root user' >&2; exit 2; }
mkdir -p "$prefix" "$evidence"
cd "$repo"
git rev-parse HEAD > "$evidence/commit.txt"
git status --porcelain > "$evidence/worktree_before.txt"
date -u +%FT%TZ > "$evidence/started.txt"
id > "$evidence/user.txt"
df -Pk "$prefix" > "$evidence/disk_before.txt"
for tool in openroad yosys klayout magic netgen conda; do
  command -v "$tool" || true
done > "$evidence/preexisting_tools.txt"

set +e
bash r2g-skills/eda-install/bootstrap.sh --dry-run --prefix "$prefix" \
  --tiers core,frontend,klayout,sky130,pdk,platform_rules,graph \
  --strict-platforms "$platforms" > "$evidence/dry_run.log" 2>&1
preview_rc=$?
echo "$preview_rc" > "$evidence/dry_run.exit"
if [[ $preview_rc != 0 ]]; then
  echo 'Dry-run failed; see dry_run.log' >&2
  exit "$preview_rc"
fi
/usr/bin/time -v -o "$evidence/install.time" timeout --signal=TERM --kill-after=60 7200 \
  bash install.sh --yes --prefix "$prefix" --platforms "$platforms" --no-config --selftest \
  > "$evidence/install.log" 2>&1
install_rc=$?
echo "$install_rc" > "$evidence/install.exit"
set -e
date -u +%FT%TZ > "$evidence/finished.txt"
du -sk "$prefix" > "$evidence/installed_size_kib.txt"
df -Pk "$prefix" > "$evidence/disk_after.txt"
git status --porcelain > "$evidence/worktree_after.txt"
tail -35 "$evidence/install.log"
exit "$install_rc"
