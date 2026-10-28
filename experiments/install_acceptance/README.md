# Clean-install acceptance

Run a public Git revision in a fresh Linux container or VM. Do not mount host
EDA tools, PDKs, user configuration, credentials, package caches, or a Docker
socket. The installer runs as an ordinary user; preparing the base OS is a
separate, recorded prerequisite. The base OS needs Git, curl, CA certificates,
Python with venv/ensurepip, and GNU time for measurement.

Inside the isolated environment, clone the repository from its remote, check
out an exact commit, then run:

```sh
bash experiments/install_acceptance/run_clean_install.sh \
  /home/tester/repo /home/tester/toolchain /acceptance/clean
```

The fourth optional argument selects platforms, defaulting to
`nangate45,sky130hd,gf180`. The harness records the initial commit, worktree,
pre-existing tools, dry-run, complete install/selftest log, return code, elapsed
time and final installation size. Its two-hour outer bound is an installation
test limit, not an override of physical signoff limits. Inspect actual job
results and DRC/LVS/dataset reports; a successful process exit alone is not
enough. Tool versions, rule hashes, actual recipe selection, repeat installation
and new-session behavior need separate evidence. A container test does not
constitute VM/OS-reboot acceptance.

`run_namespace.sh` is an alternative host-side launcher when Docker is absent
but unprivileged user namespaces and a distribution-provided bubblewrap binary
are available. Its root filesystem must contain only the audited base OS. It
mounts that OS read-only, with separate fresh home, temporary and evidence
directories. It exposes only host DNS configuration and shared networking;
tools and experiments remain inside the new root. It selects eight available
CPUs and sets a **per-process** 32 GiB address-space limit, not an aggregate
cgroup RAM limit. Keep this distinction in the report. Set `ACCEPTANCE_PROXY`
only when a download proxy is required; never forward model credentials.

`proxy_bridge.py` can expose an existing loopback download proxy on a Docker
gateway to one explicitly allowed container address. Stop the bridge after the
experiment. It does not log request content.

Keep experiment logs, OS/package archives, workspaces and datasets outside Git.
The implementation and reproducible harness belong in Git. When a baseline
fails, preserve it before applying candidate source changes. An in-place repair
run is diagnostic; final acceptance requires a new empty home/environment and
the exact published candidate revision.
