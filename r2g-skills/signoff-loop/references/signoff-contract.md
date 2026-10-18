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

### Period target — a formula, not bins

```
period_target(cells) = clamp( coefficient x cells^0.25 , floor , ceiling )
```

| platform | coefficient | floor | ceiling |
| --- | --- | --- | --- |
| nangate45 | 0.142 | 0.30 ns | 1.95 ns |
| gf180 | 0.818 | 1.58 ns | 10.30 ns |
| sky130hd | 0.534 | 1.22 ns | 8.80 ns |

**Why a formula and not the bins it replaces.** Measured on the pilot, the
spread WITHIN a size class (p20..p80 of 1.4x–3.0x) is larger than the step
BETWEEN adjacent classes (1.05x–1.64x) — cell count explains only part of a
critical path, since a 1000-cell serial adder chain and a 1000-cell parallel
datapath differ sharply. Finer bins are therefore false precision, and bins of
any width introduce a cliff: at a 1000-cell boundary two designs two cells apart
would get targets 1.8x apart. A power law fits as well as bins do (max deviation
1.4–1.6x, i.e. the same as the within-class spread) with no edge to argue about.

The exponent is shared because the pilot fitted 0.243 / 0.240 / 0.262 on the
three platforms independently — close enough to call 0.25, with only the
coefficient carrying the technology.

**Why BOTH ends are clamped to the measured range.** The fit was anchored on bin
midpoints and was never validated at the extremes. Unclamped it asks a 1-cell
nangate45 design for 0.142 ns while its 0–100 class demonstrably reaches 0.30 —
twice as fast as its peers — and above ~5k cells it keeps rising while the
measurement saturates, handing a 50k design 2.12 ns where peers reach 1.96. The
floor is the smallest measured class's p80; the ceiling is the largest p80
observed. A target outside the measured range is not the measurement it claims
to be.

**Why p80 at all.** It preserves the pilot's own calibration intent (gf180's
10 ns was picked to clear ~80% of a 24-design study), now applied per size
rather than globally. About 20% of each class is expected to miss it, and that
20% is the headroom a repair campaign is measured against.

### Where the per-design value comes from

Computed **offline** into a per-design table from the pilot's cell count, by
`experiments/make_spec_table.py`. Offline because the formula takes
post-synthesis cell count as input while synthesis reads the period from the SDC
(ABC constrains against it) — a circular dependency a two-pass flow would move
rather than remove. It also matches the rule above: a design's class is pinned at
baseline synthesis so a repair cannot shrink its own target.

Honest limit: the pilot count was synthesised at the pilot period, and a tighter
period buffers more, so the count will move (~±20%). Class boundaries are a
decade apart, so it does not cross them.

A design with **no** pilot cell count (no `synth_stat`; 166 per platform) gets
**no spec** and keeps the platform default. A design with no spec can neither
pass nor fail one, and inventing a value would place it in the denominator.

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
| clock uncertainty | **0.0** | **5% of the period** | zero assumes a perfect clock and makes setup artificially easy; real signoff budgets jitter + skew |
| input/output delay | **none** | **25% of the period**, on all ports | without it only reg-to-reg paths are constrained, and **27–28% of designs (≈1814) have no constrained path at all** — they report `worst slack max INF` while `wns` reads 0.00, which looks like a pass |
| hold | **unchecked** | required (condition 4) | see above |

**Validated end to end on a design the old SDC let through.**
`C01203__array_multiplier__ignitertl` (200 cells, combinational, no reg-to-reg
path) recorded `timing_path_constrained=False, worst_slack=None, wns=0.00` in the
pilot — indistinguishable from a pass. Under the contract's SDC it reports
`timing_path_constrained=True, worst_slack=-1.5` against its assigned 0.534 ns:
it needs ~2.03 ns, so it genuinely cannot reach its size class's target and is
now a measurable failure. The flow echoes
`R2G_SDC period=0.534 uncertainty=0.0267 io=0.1335`, confirming the derived
values reach the tool. Note the mechanism: this design has no clock port and
gets a virtual clock, and **a virtual clock still constrains input-to-output
paths once I/O delays exist** — which is why the previously-vacuous class comes
into scope.

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
| stage timeout | 21600 s (6 h; was 10800 in the pilot) |
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
| `--antenna` | **ON** (the official default is OFF) | official check, non-default switch |
| `R2G_PDN_CFG`, `R2G_TAPCELL_TCL` | **apparatus**, declared below | apparatus |

**Why `--antenna` is turned on despite the official default being off.** Three
reasons, in order of weight. An antenna violation is plasma-etch charge damaging
gate oxide — a real yield and reliability failure, not a cosmetic one.
nangate45's official deck has `ANTENNA = true` by default, so leaving it off
here would mean the three platforms measure different things and their numbers
could not sit in one table. And r2g owns an antenna repair recipe
(`deck_driven_antenna_diode`) whose value simply cannot be measured on a
platform that does not check antennas. The cost is runtime — antenna checking
needs full-layer connectivity extraction, which is why the PDK leaves it opt-in
— and the official per-table runner absorbs that.

**Why the PDN and tapcell overrides are apparatus rather than repair.** They are
platform CONFIGURATION applied uniformly to every design, not a response to any
design's own failure: `gf180_pdnfix/a.cfg` drops `-split_cuts {Metal3 0.128}`
because the stock strategy merges via arrays into 0.772 x 1.028 rectangles that
violate V2.1/V3.1, and `d30.tcl` takes tapcell `-distance` from 100 to 30
because DF.13/14_MV cap the distance to the nearest tap at 15 um for 5 V
devices. Both are the platform being configured correctly, the same way for
everyone, which is the apparatus test.

The alternative was measured and rejected on its consequences: the stock PDN
produced 824 violations on a single design against 0 with the override, so a
stock-PDN gf180 baseline would be near-zero dual-clean, and every later repair
would be compared against zero — a baseline that cannot distinguish a good
repair from a bad one is not worth the days of machine time it costs.

**The honest consequence, which must travel with every gf180 number: this
baseline is NOT "stock ORFS gf180".** Its PDN strategy and tapcell distance are
corrected. A reader comparing against an unmodified ORFS gf180 flow is comparing
against something else.

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

**Decision: the floor is BASELINE POLICY, not repair.** It is already the lower
bound in the die-size rule above (`max(cell_area / utilization, PDN minimum)`),
so under this contract these designs never fail in the first place and there is
nothing for a repair to rescue.

The reasoning, since the opposite reading is defensible and was held earlier in
this file's history: the failure is an artifact of handing every design one
utilization, which no careful input specification would do — a competent
engineer sizing a 9-cell design does not pick 20% and then discover the power
grid will not fit. Crediting r2g for fixing it would dilute its measured
capability with a trivial win, and the designs are tiny (sky130hd's rescued
median is 10 cells / 75 um^2 against 840 cells / 6600 um^2 for the rest).

**What must still be reported, so the information is not lost:** 1137 of the
pilot's 5181 dual-clean designs reached that state only through the fallback
(nangate45 492, gf180 367, sky130hd 278). Under this contract they are ordinary
in-policy completions. Any comparison against a baseline WITHOUT a die floor —
including the pilot as originally recorded — must say so.

The improvement target stands either way: r2g's own recipe hard-codes
`_PDN_DIE_FLOOR_UM = 200` (validated on one design, cordic) where the measured
minimum is 32 um on sky130hd — a 39x area difference for the same rescue.
Deriving the floor from the width PDN-0185 actually reports would fix that.

Note for whichever is chosen: r2g's recipe hard-codes `_PDN_DIE_FLOOR_UM = 200`
(validated on one design, cordic) while the measured minimum is 32 µm on
sky130hd — a **39x area difference** for the same rescue. Deriving the floor
from the width PDN-0185 actually reports, instead of a constant, is a concrete
improvement target.

## Open items before this draft can be frozen

1. **`target_utilization(size class)` — the only one left.** The utilization
   sweep is running: 61 designs per platform at 40/50/60/70/80%, taking the
   highest utilization at which >=80% of a size class still completes.

Settled since the first draft:
- ~~Clock uncertainty and I/O delay~~ — 5% and 25%, validated end to end.
- ~~Period spec~~ — formula with both ends clamped to the measured range.
- ~~gf180 `--antenna`~~ — ON.
- ~~gf180 PDN / tapcell~~ — apparatus, declared, with the consequence stated.
- ~~PDN core floor~~ — baseline policy, with the 1137 affected designs reported.
