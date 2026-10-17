# dev / held-out split — FROZEN 2026-10-17

Required by `EXPERIMENT_PLAN.md` E2:

> Per platform, disjoint dev / held-out by design family (repo-level, after
> dedup). Learn + validate recipes on dev only; **freeze before touching
> held-out.**

| file | designs | sha256 |
| --- | --- | --- |
| `dev.txt` | 1249 | `1e5005104eafd96e8d24bf611e43c7a92cc08df53a8751c84b75fdcd0a58b486` |
| `held_out.txt` | 1248 | `215843425abf61e3e8d28e036c0447999e002fb6907e1965334f4b26fbf5f283` |

Names are the short candidate form `C#####__<top>__<repo>`, which is what the
corpus driver's `--select` takes. Built by `make_split.py` from the 2497-design
corpus and the handoff package's `certified/*/DESIGN.json`.

## The split unit is a union-find class, not a repo

Three relations are merged, so neither repo style nor literal duplicate RTL can
leak across the boundary:

1. same `repository.revision_key` (`github:owner/name`)
2. identical set of source-file `sha256`s — catches a fork living under a
   different owner, which relation 1 would miss
3. the package's own `consolidated_duplicates` links
   (`STRUCTURAL_DUPLICATE_FINGERPRINT_V2`)

Result: 2495 designs (2 of 2497 have no `DESIGN.json`) collapse into **1245
units**; the largest holds 63 designs (`lvdc_simulation`). Relations 1 and 2 are
not redundant — 1247 repo groups vs 2454 content-hash groups.

**Self-check enforced by the builder: 0 units straddle the boundary.**

## ONE split, used on all three platforms

A per-platform split would let a design be dev on one platform and held-out on
another, so a recipe learned from its sky130hd run would be "tested" on its
gf180 run. The same unit assignment is therefore applied to nangate45, gf180 and
sky130hd.

## Balanced on SIZE, blind to OUTCOME — deliberately

Assignment is greedy over units sorted by size, each going to whichever side is
behind on design count, with ties broken on the cell-count histogram.

| post-synth cells | dev | held-out |
| --- | --- | --- |
| 0–100 | 327 | 311 |
| 100–1 000 | 388 | 391 |
| 1 000–5 000 | 237 | 247 |
| 5 000–20 000 | 158 | 160 |
| >20 000 | 139 | 139 |

The observed failure mix comes out close but not equal — the largest gaps are
nangate45 `drc=timeout` 67 vs 82, sky130hd `drc=violations` 45 vs 38, gf180
`lvs` 15 vs 18; dual-clean itself is within 0.9–5.9% on all three platforms.

**This is not rebalanced, for two reasons.** Stratifying on *which designs
failed* would use outcome information to construct the held-out set, and the
split must be blind to outcomes to test anything. More decisively, those failure
labels are about to be invalidated: the frozen contract changes the period, the
utilization and adds a hold check, so the baseline re-run will reshuffle which
designs fail. Stratifying on labels that are about to change buys nothing.

Size is balanced instead because it is an *input* property and will not move.

## Rules from here

- Recipes are developed and validated on **dev only**.
- `held_out.txt` is not read by any development, tuning or recipe-selection step
  before the recipes are frozen.
- The recipes that already exist (`dont_use_violating_master`,
  `deck_driven_antenna_diode`'s corpus-scale validation, `pin_side_rebalance`'s
  precondition survey) were developed **before this split existed**, against the
  whole corpus. They must be **re-validated on dev** and may not be reported as
  held-out results.
