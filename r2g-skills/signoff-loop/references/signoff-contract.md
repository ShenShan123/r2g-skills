# The Signoff Contract — the frozen specification a design must meet

A repair campaign counts designs brought to a clean, signed-off result. That
count means nothing unless the yardstick is fixed first and never moved again.
This file is the yardstick.

**The rule that makes the number mean something:** the count must grow because
designs were REPAIRED, never because the checking changed. The contract is
frozen per platform before a campaign; changing it invalidates every number
measured under the old one.

## STATUS: DRAFT — the 2026-10 corpus is a PILOT, not the baseline

The three 2497-design corpora (`n45_full`, `gf180_full`, `full_out`) were run
with global constants for period and utilization. Measurement showed both are
so loose they are **not binding**, so neither timing nor area discriminated
anything:

| dimension | what was set | what the pilot shows |
| --- | --- | --- |
| period | one constant per platform | nangate45 gave 2.0 ns to designs whose median achievable period is **0.22 ns** at <100 cells — 9x loose. Only 3% of nangate45, 5% of gf180, 15% of sky130hd designs missed it. |
| utilization | 20% for every design | achieved utilization is **19.x% for every design above 100 cells** — the flow is simply handed the answer. A die 5x the cell area never congests. Real tape-outs run 60-80%. |

A criterion 97% of designs pass measures nothing, and worse: if r2g improves
timing, the number cannot move, so the work is invisible. The pilot's value is
as **calibration data** for the spec below.

## The three layers

Every ingredient of a run falls into exactly one layer, and the layer decides
whether it may contribute to the repair credit.

| Layer | Rule | May it count as repair? |
| --- | --- | --- |
| **Checking** — decks, checkers, their switches | Must be the OFFICIAL deck with official defaults. A deck we modified is never the yardstick. | No. It IS the yardstick. |
| **Apparatus** — anything non-official applied IDENTICALLY to both arms | Allowed, but declared here with the reason it is unavoidable. | No. |
| **Repair** — applied only to the repair arm | This is the thing being measured. | Yes. |

The test for an ingredient is not who wrote it, it is **whether both arms get
it**. A checker that only reads (a `.lydrc` / `.lylvs` reads GDS and CDL and
writes a report; it cannot alter a layout) is apparatus even when r2g supplies
it. Anything that changes what the implementation tools PRODUCE is repair, and
belongs to the repair arm alone — including a patched platform library, because
the official tool then runs on an unofficial input.

**No verdict is not a pass.** A timeout, a crashed checker, a flow that never
produced a layout: all count as NOT clean.

## Pass = four conditions, all of them

1. **DRC clean** under the platform's official deck
2. **LVS clean** under the platform's checker
3. **Setup timing met** — WNS >= 0 at the period the spec assigns
4. **Hold timing met** — worst hold slack >= 0

A design that fails any one of them has not passed. Conditions 3 and 4 are new
relative to the pilot, which recorded setup only (`wns`/`tns`/`worst_slack`) and
never checked hold at all.

### Why hold is not optional

A **setup** violation means the data arrives too late: the chip still works, run
it slower. A **hold** violation means data arrives too early and overwrites the
previous cycle's value — the chip is broken **at every frequency**. The pilot
checked setup on 73% of designs and hold on none, so the more fatal of the two
was entirely unmeasured.

## The scale-dependent specification

Both period and die size are set from the design's **post-synthesis cell count**,
not from a global constant.

### Why per-size, and why frozen from the pilot

A single period across four orders of magnitude of design size conflates "easy
design" with "good flow". Binning turns the criterion into *"designs of this
size demonstrably reach this, under this flow"* — so a failure means the design
is behind its own size peers, which is a meaningful and improvable statement.

The targets below come from the pilot, computed per design as
`achievable_period = constrained_period - worst_slack`. **`worst_slack` is the
unclamped quantity**: `wns` clamps to 0 whenever timing is met, so
`period - wns` is identically the period for every passing design and carries no
information. Using `wns` here produced a table whose every entry equalled the
input period; the error was caught only because that result was implausible.

### Period target — p80 of the pilot's achievable period, by cell count

p80 is chosen because it preserves the pilot's own calibration intent (gf180's
10 ns was picked to clear ~80% of a 24-design study), now applied per size class
instead of globally. Roughly 20% of each class is expected to miss it; that
20% is the headroom a repair campaign is measured against.

| post-synth cells | nangate45 | gf180 | sky130hd |
| --- | --- | --- | --- |
| 0–100 | 0.30 ns | 1.58 ns | 1.22 ns |
| 100–1 000 | 0.55 ns | 3.39 ns | 2.55 ns |
| 1 000–10 000 | 1.39 ns | 7.79 ns | 5.23 ns |
| 10 000–100 000 | 1.91 ns | 9.95 ns | 7.97 ns |
| >100 000 | 1.80 ns | 10.36 ns | 5.65 ns |

(sky130hd's >100 000 row rests on 3 designs and is the weakest cell in the table.)

### Die size — fixed explicitly, never derived at repair time

```
DIE_AREA(design) = max( cell_area_at_baseline_synthesis / target_utilization(size class),
                        platform PDN minimum )
```

written as an explicit `DIE_AREA` / `CORE_AREA` and **frozen for that design in
both arms**.

Deriving the die from utilization at run time would let a repair move its own
goalpost: a fix that reduces cell count would shrink the die and hand itself an
easier job. Freezing the die means a repair that reduces cells gets more room
(a real benefit) and one that grows cells gets squeezed (a real cost) — which is
how a tape-out actually works, since the die is fixed by cost and packaging and
the flow must fit inside it.

**No separate area check is needed.** A design that cannot fit fails placement
or routing, which conditions 1–4 already catch.

**`target_utilization(size class)` is NOT YET DETERMINED.** The pilot cannot
supply it: every design was handed 20% and the flow returned 19.x%, so nothing
was ever pushed to failure. It requires a utilization sweep (a sample per size
class at 40/50/60/70/80%, taking the p80-achievable value), run and frozen
before the baseline.

### The size class is pinned at BASELINE synthesis

A repair that changes cell count must not move the design into a different size
class and collect a different target. Each design's class is fixed by its
baseline synthesis and never recomputed.

## Timing constraints (SDC) — the full set

The pilot's SDC was `create_clock` plus `set_clock_uncertainty 0.0`, and nothing
else. Three consequences, all of which the contract fixes:

| item | pilot | contract | why |
| --- | --- | --- | --- |
| clock uncertainty | **0.0** | a realistic fraction of the period | zero assumes a perfect clock and makes setup artificially easy; real signoff budgets jitter + skew |
| input/output delay | **none** | set on all ports | without it only reg-to-reg paths are constrained, and **27–28% of designs (≈1814) have no constrained path at all** — they report `worst slack max INF` while `wns` reads 0.00, which looks like a pass |
| hold | **unchecked** | required (condition 4) | see above |

Clock identification stays as the pilot's heuristic and is part of the contract:
a 29-name candidate port list, a `(^|_)(clk|clock)([0-9_]*$|_)` regex fallback
over `all_inputs`, and a virtual clock when neither matches. Which port becomes
the clock — and whether a real clock exists at all — is decided here, so a
different heuristic is a different benchmark.

## Pinned environment (not pass/fail, but a change invalidates the baseline)

| item | value |
| --- | --- |
| container image | `r2g-orfs:26Q3` |
| OpenROAD | `26Q3-318-g6b9d7fb806` |
| Yosys | `0.64` |
| KLayout | `0.30.7` |
| synthesis target | `ABC_AREA = 1` (area-optimised, not speed) |
| `SYNTH_MEMORY_MAX_BITS` | 131072 |
| cores per design | 8 — a different thread count can perturb routing and makes runs incomparable |
| stage timeout | 10800 s |
| max work tree per design | 30 GB — a **hidden size ceiling**: exceeding it is recorded as `oversize`, so it must be stated rather than discovered |
| DRC budget | 7200 s; no verdict counts as not clean |
| PDN core floor | sky130hd 32 µm (measured minimum; 30 µm still fails after site snapping), gf180 110 µm |

## Per-platform signoff

### nangate45

| Item | Definition | Layer |
| --- | --- | --- |
| DRC deck | `platforms/nangate45/drc/FreePDK45.lydrc`, unmodified — verified byte-identical to both `.bak` copies | official |
| DRC scope | the deck's own defaults: `FEOL`, `BEOL`, `ANTENNA`, OFFGRID all on | official |
| GDS library | `NangateOpenCellLibrary.gds`, untouched | official |
| LVS rule | r2g's `FreePDK45.lylvs` | **apparatus** |
| Antenna LEF model | stock LEF in the BASELINE; the r2g patch belongs to the repair arm | **repair** |

**Why the LVS rule is apparatus:** upstream ORFS ships **no LVS rule for
nangate45** — `make lvs` silently reports "skipped" — so the platform cannot be
signed off at all without one. It only reads GDS and CDL and writes a report, so
it adds no repair. Every nangate45 number is conditional on it, and saying so is
part of the number.

**Why the antenna LEF patch is repair:** stock FreePDK45 carries zero antenna
ratio rules and an `ANTENNA_X1` diode with `ANTENNADIFFAREA 0.0`, which OpenROAD
rejects (GRT-0246). `tools/install_nangate45_antenna.sh` adds the ratios and
raises the diode to 0.1 — a purely additive antenna change (532+532+387+146
added attribute lines, one value edited, nothing else). It adds no flow step,
but ORFS's **default** `repair_antennas` then has a working model: **347 of 2497
pilot layouts carry OpenROAD-inserted diodes, 50 of them among the dual-clean.**

Measured scope of the effect, which is why nangate45 needs a full re-run rather
than a partial one: 14 small and medium designs produce **byte-identical** DEFs
with and without the patch, but **all 4 large designs tested (540 k–671 k
routing segments) differ even with zero diodes inserted** — jumper repair alters
routing without adding instances, so a diode count cannot identify the affected
set.

Note the asymmetry that keeps this clean: the patch touches only the LEF, while
the deck reads the **GDS**, which is untouched. The CHECKER was never
contaminated — only the flow. Of 75 files in the platform directory, 67 are
untouched, three are backups, the deck is byte-identical to its backups, and
exactly **two** differ.

### gf180

| Item | Definition | Layer |
| --- | --- | --- |
| ORFS platform dir | clean — 603 files, no backups, nothing newer than the install | official |
| PDK | clean — `libs.tech/klayout/drc` 191 files and `libs.ref` 4784 files all original | official |
| DRC entry point | the PDK's own `run_drc.py` + 58 `rule_decks/`, variant **C** (metal_top 9K / mim_option B / metal_level 5LM), default `run_mode=flat`, per table with `--mp` | official |
| `--split_deep` | **OFF — measured lossy.** It runs CO.6/6a/6b in deep mode and reported **0** CO.6a where the official flat default reported **35** on the same design | official |
| LVS | netgen with the PDK's own `gf180mcuC_setup.tcl` (2024-03, original), driven by r2g's `run_netgen_lvs.sh` | apparatus (rules official) |
| `--antenna` | **NOT YET DECIDED** — the official default is OFF | — |
| `R2G_PDN_CFG`, `R2G_TAPCELL_TCL` | **NOT YET DECIDED** — platform strategy overrides present in all 2497 pilot configs | — |

An assembled 7884-line monolithic deck was used for the pilot. Its judgment is
sound — on C00006 it reported exactly the same 35 CO.6a as the official runner's
flat default — but its **execution** is not: a monolithic run times out where
per-table does not, which is what left 180 designs without a verdict.

### sky130hd

| Item | Definition | Layer |
| --- | --- | --- |
| ORFS platform dir | clean — 21 files, nothing modified | official |
| PDK | clean — `libs.tech/klayout` 397 files and `libs.ref` 10271 files all original | official |
| DRC deck | official `sky130hd.lydrc` | official |
| LVS | netgen with the PDK's own `sky130A_setup.tcl` (2024-03, original), driven by r2g's `run_netgen_lvs.sh` | apparatus (rules official) |

The 21 PDK files carrying a recent mtime are install-time path substitutions
(magicrc / qflow / xcircuit, containing the conda install path), not content
changes.

## The PDN core floor is REPAIR, not apparatus

`--pdn-core-floor-um` retries a PDN-0185 floorplan abort with an explicit square
core. Upstream ORFS does **nothing** on PDN-0185 — a grep of `flow/scripts/` and
the Makefile finds no handling; it simply aborts. And r2g already owns this as a
named recipe: `engineer_loop._relieve_pdn_strap_width` (introduced 2026-07-01,
commit `e1ad62c`) floors the die to an explicit PDN-feasible square. The corpus
driver implements the same idea a second time and runs it first, so r2g never
gets the chance to earn the credit.

Pilot scope: **1137 of 5181 dual-clean designs across the three platforms exist
only because of this fallback** (nangate45 492, gf180 367, sky130hd 278), with a
rescue rate near 100%. The designs are tiny — sky130hd's rescued median is **10
cells / 75 µm²** against 840 cells / 6600 µm² for the rest — and a design that
small physically cannot be made narrower than the power grid allows, so the
"wasted" die is a property of the PDK, not a flaw in the fix.

**Open decision.** Under the die-size rule above the PDN minimum is already a
hard lower bound, so these designs would never fail and r2g would earn nothing
for them. Either reading is defensible and the choice must be explicit:

- **floor as baseline policy** — the failure is an artifact of handing every
  design one utilization, which no careful input spec would do; r2g is then
  measured on the harder remaining problems.
- **floor as repair** — r2g demonstrably automates it, and gets credit.

Reporting both, separately labelled, loses nothing.

Note for whichever is chosen: r2g's recipe hard-codes `_PDN_DIE_FLOOR_UM = 200`
(validated on one design, cordic) while the measured minimum is 32 µm on
sky130hd — a **39x area difference** for the same rescue. Deriving the floor
from the width PDN-0185 actually reports, instead of a constant, is a concrete
improvement target.

## Open items before this draft can be frozen

1. `target_utilization(size class)` — requires the utilization sweep.
2. Clock uncertainty and I/O delay — pick the fractions of period.
3. gf180 `--antenna` on or off.
4. gf180 `R2G_PDN_CFG` / `R2G_TAPCELL_TCL` — apparatus or repair.
5. PDN core floor — baseline policy or repair.
