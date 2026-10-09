# baseline216 — the corpus baseline harness

These are the drivers that produced the three-platform baseline the paper's
claims rest on: 2,497 designs each on `sky130hd`, `nangate45` and `gf180`, run
on host 216 (96 cores, 1TB RAM, podman image `r2g-orfs:26Q3`).

They live in `experiments/` and not in `tools/` because **no skill calls them**
— per the boundary in the root `CLAUDE.md`, a script belongs in `tools/` if a
skill invokes it and in `experiments/` if it only produces evidence for a claim
in `EXPERIMENT_PLAN.md`. The dependency runs one way: these call into
`r2g-skills/signoff-loop/scripts/flow/`, never the reverse.

## What each one does

| Script | Role |
| --- | --- |
| `orfs_baseline.py` | The baseline runner. Calls `make DESIGN_CONFIG=…` **directly**, with no r2g wrapper, so the numbers are a pure upstream ORFS baseline. Owns per-design config generation, the PDN core-floor retry ladder, harvest and failure classification. |
| `signoff_pass.py` | DRC+LVS signoff for `sky130hd` and `gf180` (`--platform`). |
| `signoff45.py` | The same for `nangate45`, kept separate because that platform needs the frozen-layout LVS path (`make lvs` cannot resolve the stage chain on a harvested run). |
| `chain45.sh`, `chain180.sh` | Orchestration: flow → backfill → DRC signoff → LVS signoff → funnel. |
| `funnel.py` | The loss funnel: front-end / back-end / DRC / LVS / timing, per platform. Takes the campaign root as `argv[1]`; **the default is `full_out` (sky130hd)**, which is how a `nangate45` question once got answered with `sky130hd` numbers. |
| `pdn_status.py` | PDN fallback outcomes and the failure-class histogram. |

## What these are NOT

Not a supported interface, and not an example to copy. Paths, worker counts and
the container image are hardwired to host 216. The durable knowledge they
produced lives in `EXPERIMENT_RUNBOOK_zh.md` and in the skill scripts; these
are kept so the baseline numbers can be reproduced and audited, nothing more.

The monitoring scaffolding that ran alongside them (`watchdog.sh`, `reaper.sh`)
is deliberately **not** committed: it carries no claim, is entirely specific to
one machine's core count and mount layout, and keeping it here would suggest it
is the recommended way to supervise a campaign.

## Two honesty notes worth carrying forward

`signoff45.py` diverged from `signoff_pass.py` and silently lost artifact
preservation: it kept `lvs_signoff.log` but neither `drc_signoff.log` nor
`6_drc.lyrdb`, so `sky130hd` has 2,156 violation databases to analyse and
`nangate45` had none — and a claim that "CONTACT.3 never fires in the corpus"
turned out to be unknowable rather than true. Fixed here, including on the
timeout path, since a stalled run's partial log is what identifies the rule it
stalled in.

`result.json` carries both `seconds` (the **flow's** time) and
`signoff_seconds` (the **signoff's**). Reading the first for the second
produced three wrong cost estimates in one session: one design reads 210.1
against 7262.4.
