# Setup

> Superseded 2026-07-09 by the r2g ingestion: rtl-acquire no longer has its own
> setup path. Provision the toolchain with the **eda-install** sub-skill —
> one command detects, installs, pins `references/env.local.sh` (for this
> skill too), and verifies:
>
> ```bash
> bash r2g-skills/bootstrap.sh --dry-run   # plan only; drop --dry-run to install
> ```
>
> Requirements this skill actually uses: ORFS checkout + yosys/openroad
> (synthesis via `signoff-loop/scripts/flow/run_orfs.sh`), and a torch venv
> for the graph stage (`R2G_GRAPH_PYTHON`; stages SKIP cleanly without it).
> The 30pt base dataset, AutoGraph `mapping.txt`, and the bundled
> `resources/orfs_util/` scripts are retired — graphs are def-graph
> `netlist_graph.pt`.
>
> Verify resolution any time with `python3 scripts/skill_env.py`, or run the
> comprehensive `../signoff-loop/scripts/flow/check_env.sh`.
