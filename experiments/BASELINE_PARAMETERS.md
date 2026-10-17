# Baseline parameters — FROZEN 2026-10-17

The parameters the three-platform baseline re-run uses. Every value below is
traceable to a measurement on the 2026-10 pilot corpus, named at the end of its
row. **Changing any of them invalidates every number measured under this file.**

## Design intent: pressure, but not much

The spec exists so the pass criterion is not vacuous. It is NOT meant to be
hard. r2g repairs *failures* — it has no lever for "this layout could have been
10% faster or 10% smaller", so a target that fails a large fraction of designs
adds no measurable signal and only lowers the baseline.

Two measurements set the ceiling on how tight to go:

- **Compounding.** Three independent constraints at p80 give a joint pass rate
  of 0.8³ ≈ **51%**; at p95, **86%**. Half the corpus failing by construction
  would bury the repair signal.
- **Unfixable-by-design.** A tight utilization produces routing congestion, and
  r2g's only lever for congestion is `density_relief` — which lowers
  utilization, i.e. undoes the constraint. With the die frozen that is
  impossible, so those failures would be permanently unfixable: cost without
  signal.

So every knob below sits at a loose percentile, and the ones that would create
unfixable failures are left near the pilot's own setting.

## Timing

### Clock period — by post-synthesis cell count

The pilot gave one period per platform. The problem was never that it was too
loose *in aggregate* — it already failed 3% (nangate45), 5% (gf180) and 15%
(sky130hd). The problem is that it was **9x too loose for small designs and
roughly right for large ones**: nangate45 handed 2.0 ns to designs whose median
achievable period is 0.22 ns. The fix is fairness across sizes, not more
pressure.

Targets are the pilot's **p95 achievable period** per size class, rounded up:

| post-synth cells | nangate45 | gf180 | sky130hd |
| --- | --- | --- | --- |
| 0–100 | 0.5 ns | 2.2 ns | 1.8 ns |
| 100–1 000 | 1.0 ns | 5.0 ns | 3.6 ns |
| 1 000–5 000 | 1.6 ns | 9.0 ns | 6.5 ns |
| >5 000 | 3.0 ns | 16.0 ns | 16.0 ns |

Measured as `achievable = constrained_period - worst_slack` per design, over the
1622 / 1617 / 1547 pilot designs that have a constrained path.

**`worst_slack`, not `wns`.** `wns` clamps to 0 whenever timing is met, so
`period - wns` is identically the period for every passing design and carries no
information — a first attempt using it produced a table whose every entry
equalled the input period, and the error was caught only because that was
implausible. `worst_slack` is unclamped (positive on a passing design).

A table, not the fitted power law (`~ coefficient x cells^0.25`, consistent
across all three platforms), because a frozen spec others must reproduce should
not depend on reproducing a fit. The cost is a cliff at a bin edge; at p95 the
targets are generous enough that it does not matter.

**Size class is pinned to the PILOT's cell count** and never recomputed. A
repair that changes cell count must not move its design into another class and
collect a different target. The pilot is a synthesis measurement at a known
nominal period, so it is a legitimate and traceable source; using it also avoids
the circularity of needing the cell count before synthesis in order to set the
period synthesis reads.

### Clock uncertainty — 5% of the period

The pilot set `set_clock_uncertainty 0.0`, which assumes a perfect clock. Real
signoff budgets jitter and skew. 5% is modest: p95 is 25–100% looser than p80,
so 5% is inside that slack.

### I/O delay — 15% of the period, inputs and outputs

The pilot set none, so only reg-to-reg paths were constrained and **27–28% of
designs (~1814) had no constrained path at all** — they report
`worst slack max INF` while `wns` reads 0.00, which looks like a pass.

15%, not the 25% first considered: the goal is **coverage** (no design escapes
the timing criterion), not pressure. A larger value would make I/O the binding
constraint on designs that already had reg-to-reg paths.

### Hold — RECORDED, not a pass condition in round 1

A hold violation is more fatal than a setup violation: setup means the chip
works when clocked slower, hold means it works at **no** frequency. The pilot
checked setup on 73% of designs and hold on none.

It is nonetheless not a pass condition in round 1, because **its failure rate is
unknown** — if 30% of designs fail hold it would silently become the dominant
failure mode and bury everything else. Round 1 records it; it is promoted to a
pass condition once the rate is known.

## Area

### Utilization — flat 50%, not binned, not swept to the knee

| | value | evidence |
| --- | --- | --- |
| pilot | 20% | achieved utilization came back **19.x% on every design above 100 cells** — the flow was handed the answer and never pushed. A die 5x the cell area never congests. |
| frozen | **50%** | OpenLane's own default for sky130 (`FP_CORE_UTIL=50`, `PL_TARGET_DENSITY=0.5`), bracketed by measurement: u40 completes 57 of 58 (~2% pressure, too loose to be a constraint) and u50 is being measured per platform. |

Flat, not per-size-class: the PDN floor already protects tiny designs and 50% is
not tight for large ones, so a table would be one more thing to maintain for no
gain.

**Why 50 and not the 60–70% the literature recommends.** The published advice is
consistent — an arXiv physical-design survey says "most industries target 60-70%
initial core utilization to generate margin for timing optimization at a later
stage", and floorplanning references give the same band. That advice is written
for a designer who can still move the floorplan: the 30-40% headroom exists so
that upsizing and buffer insertion have somewhere to go. **This baseline freezes
the die**, so that headroom is not a working margin — and every congestion
failure above the chosen utilization becomes permanently unfixable, because
r2g's only lever for congestion is `density_relief`, which lowers utilization.
Pressure that r2g cannot act on is cost without signal.

The honest anchor is therefore the **official open-source flow default for a
comparable metal stack**, not the industrial figure:

| source | value |
| --- | --- |
| OpenLane (`configuration/placement.tcl`, efabless) | `FP_CORE_UTIL` **50**, `PL_TARGET_DENSITY` **0.5** |
| OpenLane sky130A 1440-run parameter sweep (IEEE DataPort) | swept core utilization **35–55%** only |
| sky130 MPW-002 slot-020 | dropped to **20** explicitly "to fix the routing congestion due to FPU" |
| ORFS `FlowVariables.md` | `CORE_UTILIZATION` has **no default and no recommendation** |
| industry / literature | 60–70% (assumes 8–12 metal layers and a movable floorplan) |

The 60–70%-vs-50% gap is explained by **routing layers**: nangate45 has 10,
sky130hd 5–6, gf180 5 (variant C = 5LM). The industrial figure assumes a stack
roughly twice as deep as two of our three platforms, which is also why OpenLane
— the reference sky130 flow — ships 50 rather than 70.

Two cautions when comparing against any cited number:

- **The definition varies.** Some sources count macros and pads against total
  core area; others take standard-cell area over placeable area. ORFS's
  `CORE_UTILIZATION` is cell area over core area.
- **Utilization alone does not predict congestion.** Pin-dense cells and
  highly-interconnected netlists produce local hot spots at any global number,
  which is why the value is confirmed by measurement per platform rather than
  adopted from a table.

Not swept to the breaking point, for the unfixable-by-design reason above: the
sweep's job is to confirm 50% has no cliff, not to find the ceiling. The
u60/u70/u80 points were planned and then dropped once the anchor was settled —
12 points of machine time with no decision attached to them.

### Die — explicit, computed once, frozen for both arms

```
side     = max( sqrt(pilot_cell_area / 0.50), platform PDN minimum )
DIE_AREA = 0 0 (side+4) (side+4)
CORE_AREA = 2 2 (side+2) (side+2)
```

Written as an explicit `DIE_AREA`/`CORE_AREA`, with no `CORE_UTILIZATION` in the
config. Deriving the die from utilization at run time would let a repair move its
own goalpost — a fix that reduces cell count would shrink its die and hand itself
an easier job. Frozen, a repair that reduces cells gets more room (a real
benefit) and one that grows cells gets squeezed (a real cost), which is how a
tape-out behaves: the die is fixed by cost and packaging and the flow must fit.

**No separate area check.** A design that cannot fit fails placement or routing,
which the pass conditions already catch.

### PDN core floor — baseline policy

sky130hd 32 µm, gf180 110 µm, nangate45 32 µm. Measured: 32 is the smallest
viable step on sky130hd — 30 µm still fails on met5 after site snapping.

This is `--pdn-core-floor-um`, and it IS an r2g capability that upstream ORFS
lacks entirely (see `R2G_CONTRIBUTIONS.md`). It is in the baseline anyway: a
10-cell design physically cannot be narrower than the power grid allows, so
without the floor those designs are unbuildable and the baseline is a degenerate
~0, which measures nothing. The contribution is declared, not counted.

## Pass conditions

```
pass = DRC clean  AND  LVS clean  AND  setup WNS >= 0
hold slack: recorded only in round 1
no verdict (timeout / crashed checker / no layout) = NOT pass
```

## Execution

| | value | evidence |
| --- | --- | --- |
| container image | `r2g-orfs:26Q3` | OpenROAD `26Q3-318-g6b9d7fb806`, Yosys `0.64`, KLayout `0.30.7` |
| workers x cores | **12 x 8** | the pilot's own proven setting, which sustained ~57 designs/hour. `nproc` is 96 here (48 physical x 2 threads) but 2x nominal oversubscription is empirically fine, because a design is only in its ~7-core routing phase part of the time. |
| ONE campaign at a time | — | the load-160/zero-completion failure was **three** campaigns (~45 containers), not 12 flows. That is the real rule; budgeting against 48 physical cores was an over-correction from a misattributed failure. |
| stage timeout | **21600 s** (6 h) | raised from 10800 |
| reaper `GRACE_MIN` | **365 min** | MUST move with the stage timeout. It was 185 (= 180 + 5); left there it would kill legitimately-running long designs at 3h05m, surfacing as `Terminated` and misfiling as a stage failure. |
| max work tree / design | 30 GB | a hidden size ceiling: exceeding it records `oversize`, so it is stated rather than discovered |
| `ABC_AREA` | 1 (area-optimised) | pilot setting, kept |
| `SYNTH_MEMORY_MAX_BITS` | 131072 | pilot setting, kept |
| clock identification | 29-name candidate list, `(^\|_)(clk\|clock)([0-9_]*$\|_)` regex fallback over `all_inputs`, virtual clock when neither matches | pilot setting, kept — which port becomes the clock, and whether a real clock exists, is decided here, so a different heuristic is a different benchmark |

## Per-platform signoff

| | nangate45 | gf180 | sky130hd |
| --- | --- | --- | --- |
| DRC deck | official `FreePDK45.lydrc`, unmodified | the PDK's own **`run_drc.py`**, 58 `rule_decks/`, variant **C** (metal_top 9K / mim_option B / metal_level 5LM), `run_mode=flat`, per table with `--mp` | official `sky130hd.lydrc` |
| DRC scope | deck defaults: FEOL, BEOL, ANTENNA, OFFGRID all on | `--antenna` **ON** — the pilot ran with antenna checks included (the assembled deck's own note: "antenna.drc (33 rules) IS included", and the corpus reports `ANT.16_ii_ANT.4`), and both other platforms check antennas by default | deck defaults |
| `--split_deep` | n/a | **OFF — measured lossy**: it runs CO.6/6a/6b in deep mode and reported **0** CO.6a where the official flat default reported **35** on the same design | n/a |
| DRC budget | 7200 s | per-table | 7200 s |
| LVS | r2g's `FreePDK45.lylvs` (ORFS ships none) | netgen with the PDK's own `gf180mcuC_setup.tcl` (2024-03, original) | netgen with the PDK's own `sky130A_setup.tcl` (2024-03, original) |
| antenna LEF model | **r2g patch IN the baseline** — see below | n/a | n/a |
| PDN / tapcell | ORFS defaults | **r2g overrides IN the baseline** — see below | ORFS defaults |

### Why two r2g changes sit inside the baseline

Both are cases of **official data that is internally inconsistent**, where
excluding the fix produces a degenerate baseline that measures nothing. Both are
declared in `R2G_CONTRIBUTIONS.md` and neither is counted as repair.

**nangate45 antenna LEF.** FreePDK45's DRC deck checks antennas by default — the
corpus's 6649 `METAL*_ANTENNA` violations are the official deck's own output —
while its LEF provides no data to satisfy them: zero antenna ratio rules in the
tech LEF and an `ANTENNA_X1` diode with `ANTENNADIFFAREA 0.0`, which OpenROAD
rejects (GRT-0246). A PDK that checks a rule but gives the tools no way to meet
it is incomplete, not opinionated. Without the patch all 369 antenna-dirty
designs are permanently unfixable. The patch is purely additive antenna metadata
(532 + 532 + 387 + 146 added attribute lines, one value edited from 0.0 to 0.1,
nothing else), and this is the same judgement already made for the nangate45 LVS
rule, which upstream also omits entirely.

r2g's actual antenna contribution is unaffected:
`deck_driven_antenna_diode` exists precisely because ORFS's *default* repair
inserts jumpers that the deck does not credit — the two models disagree by ~200x
and OpenROAD's violating set reads empty on a layout the deck flags 16 times. The
patch grants the platform eligibility; the recipe is the capability.

**gf180 PDN and tapcell overrides.** ORFS's gf180 PDN strategy asks for multi-cut
via arrays (`-max_columns 5`, `-split_cuts`) that merge into 0.772 x 1.028
rectangles and violate GF's own V2.1/V3.1 ("every Via2/Via3 edge exactly
0.26 um") — **824 violations on one design, 82% of all violations**. Its tapcell
default is `-distance 100` while DF.13/DF.14_MV cap the distance to the nearest
substrate tap at 15 um for 5 V devices. These are the ORFS platform
configuration disagreeing with the foundry's own rule manual. A stock-PDN gf180
baseline would be ~0 dual-clean, against which no repair is measurable.

## What the re-run is for

Every contamination question resolved to "apparatus, declared", so **the re-run
is needed because the SPEC changed** — per-design period, explicit die, three
SDC additions — not because the pilot was contaminated. The nangate45 stock-LEF
re-run that was planned for contamination is cancelled.

Both halves of `experiments/splits/` (dev 1249, held-out 1248) are run in the
same pass; held-out results are not read before the recipes are frozen.
