#!/usr/bin/env bash
# Host-side launcher for an unprivileged Linux namespace acceptance container.
# ROOT/rootfs must be an audited base OS without EDA tools or user data.
# ROOT/fresh_home, ROOT/tmp, ROOT/evidence must be empty on the first run.
set -euo pipefail
root="${1:?usage: run_namespace.sh ROOT BWRAP [COMMAND...]}"
bwrap="${2:?missing bubblewrap binary}"
shift 2
if [[ $# == 0 ]]; then set -- /bin/bash; fi
root="$(realpath "$root")"
bwrap="$(realpath "$bwrap")"
mkdir -p "$root/fresh_home" "$root/tmp" "$root/evidence"
cpus="$(python3 -c 'import os; print(",".join(map(str, sorted(os.sched_getaffinity(0))[:8])))')"
# This is a PER-PROCESS address-space cap, not an aggregate cgroup memory cap.
ulimit -v 33554432
network=()
if [[ -n "${ACCEPTANCE_PROXY:-}" ]]; then
  network=(--setenv http_proxy "$ACCEPTANCE_PROXY" --setenv https_proxy "$ACCEPTANCE_PROXY"
           --setenv no_proxy localhost,127.0.0.1)
fi
exec taskset -c "$cpus" "$bwrap" \
  --unshare-user --uid 1000 --gid 1000 --unshare-pid --unshare-ipc --unshare-uts \
  --die-with-parent --new-session --ro-bind "$root/rootfs" / \
  --bind "$root/fresh_home" /home/tester --bind "$root/evidence" /acceptance \
  --bind "$root/tmp" /tmp --ro-bind /etc/resolv.conf /etc/resolv.conf \
  --proc /proc --dev /dev --clearenv \
  --setenv HOME /home/tester --setenv USER tester \
  --setenv PATH /usr/local/bin:/usr/bin:/bin \
  --setenv OMP_NUM_THREADS 8 --setenv OPENBLAS_NUM_THREADS 8 --setenv MKL_NUM_THREADS 8 \
  "${network[@]}" --chdir /home/tester "$@"
