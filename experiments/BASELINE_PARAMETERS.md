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

- **Compounding.** Three independent constraints at p80 would give a joint pass
  rate of 0.8³ ≈ **51%**; at p95, **86%**. Half the corpus failing by
  construction would bury the repair signal. *(This was written when timing was
  assumed to be a pass condition. It is not — see below — so the knobs no longer
  compound directly into the pass rate. They compound INDIRECTLY, by each making
  the layout harder to build: measured, the period at p95 costs ~5% and
  utilization at 50% ~3% of designs on nangate45. The conclusion is unchanged
  and the arithmetic is now an upper bound rather than the estimate.)*
- **Unfixable-by-design.** A tight utilization produces routing congestion, and
  r2g's only lever for congestion is `density_relief` — which lowers
  utilization, i.e. undoes the constraint. With the die frozen that is
  impossible, so those failures would be permanently unfixable: cost without
  signal.

So every knob below sits at a loose percentile, and the ones that would create
unfixable failures are left near the pilot's own setting.

## Timing

### Timing is NOT a pass condition — the period is a PRESSURE knob

This has to come before the period table, because reading that table as a set
of targets to be met is wrong and the mistake is easy to make.

The pass condition is DRC-clean **and** LVS-clean. Setup timing is recorded, not
judged. Measured on the pilot:

| platform | designs that missed their period | of those, still counted dual-clean |
| --- | --- | --- |
| nangate45 | 49 | 11 |
| gf180 | 76 | 4 |
| **sky130hd** | **237** | **224 (94%)** |

So sky130hd's higher baseline (1032 against 837 and 754 on dev) is partly
because its timing failures do not count against it.

It stays that way in round 1 for the same reason hold does, below: r2g repairs
DRC violations, LVS mismatches and backend aborts, and timing closure is
explicitly outside its scope (`CLAUDE.md`: escalate before attempting
signoff-quality closure). Making timing a pass condition would manufacture
~224 sky130hd failures that r2g cannot act on — cost without signal, the
principle this whole document is built on.

**But the period is not decorative, and that is also measured.** ORFS's yosys
synthesis is not timing-driven, so `result.json`'s `cells` and `area_um2` — both
synthesis numbers — come back IDENTICAL at every period, which makes the period
look inert. The pressure lands after placement, where the resizer inserts
buffers chasing the constraint. On C00059 (nangate45), run at six periods into
the same die:

| period | real cell area | of which buffers | achieved utilization |
| --- | --- | --- | --- |
| 1 ns | 10 437.6 µm² | 2541 cells | 22.7% |
| 2 ns | 9 522.0 µm² | 2056 cells | 20.7% |
| 3 ns | 9 453.11 µm² | 2016 cells | 20.5% |

A 3x tighter period costs **+10.4% real cell area, essentially all of it
buffers** (the cell-count rise and the buffer-count rise are the same 525), in a
die that did not grow. So the period sets how hard the tool is asked to work and
therefore how dense the layout ends up — which is exactly what makes DRC and
placement harder. It is a pressure knob whose effect is real but modest, and the
p95 choice below puts that pressure deliberately at the low end.

(Figures are ORFS's own `finish__design__instance__area` over
`finish__design__core__area`. `instance__area` excludes fill — verified:
instance + fill_cell equals core area to within 0.1 µm². DEF `COMPONENTS` cannot
be used instead, because fill moves OPPOSITE to real cells and cancels the
signal.)

### Clock period — by post-synthesis cell count

The pilot gave one period per platform. The problem was never that it was too
loose *in aggregate* — it already failed 3% (nangate45), 5% (gf180) and 15%
(sky130hd). The problem is that it was **9x too loose for small designs and
roughly right for large ones**: nangate45 handed 2.0 ns to designs whose median
achievable period is 0.22 ns. The fix is fairness across sizes, not more
pressure.

### Why p95 and not p90 — tightening would make r2g measure WORSE

Asked directly whether p95 leaves too little pressure. It does not, and the
decisive reason is not that p95 is tight enough:

**Tightening dilutes the measurement in one direction.** A repair result is
`repaired / total failures`. The failures p90 adds are congestion — more
timing-repair buffers, denser layout — and congestion is **the one symptom r2g
cannot repair once the die is frozen**, since its only lever lowers utilization.
So p90 grows the denominator and not the numerator. That is not a harder exam;
it is adding unmarked questions to the paper.

**The premise that p95 produces few problems does not hold.** The pilot ran at
essentially zero parameter pressure — 20% utilization, one loose period per
platform — and still failed 412 of 1249 dev designs on nangate45 (33%), 495 on
gf180 (40%), 217 on sky130hd (17%). Failures are not scarce and the parameters
are not their source; the designs and the tools are. The parameters' job is to
make "pass" mean something, not to manufacture failures. The repairable pool is
**706 dev designs** — large enough that nothing needs to be added to it.

**The compounding figure does not apply here.** "Joint pass rate 86% at p95
against 73% at p90" is 0.95³ versus 0.90³, and it assumes three constraints that
each gate. Timing does not gate. Those numbers describe a pass rate that does
not exist, and they must not be used to argue p95 is loose — the same caveat is
attached to them in the design-intent section above.

How much tighter p90 actually is, measured: 10–17% on the smallest size class
and **33–37% on 5 000–20 000 cells and above**, i.e. concentrated exactly on the
designs whose routing is already hardest.

What would change this: a repairable pool too small to measure against. At 706
it is not. And a demonstration that the flow holds under harsher conditions is a
DIFFERENT experiment — it does not belong inside the control arm, whose job is
to give repair a clean comparison.

Values are the pilot's **p95 achievable period** per size class, rounded up —
pressure settings, not targets to be met (see above):

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

### Clock uncertainty — 5% of the period for SETUP, a fixed 20 ps for HOLD

```tcl
set_clock_uncertainty -setup [expr {$_per * 0.05}] [get_clocks *]
set_clock_uncertainty -hold  0.020                 [get_clocks *]
```

**`-setup` is load-bearing and its absence aborted designs.** Written without
the flag, SDC applies the value to HOLD as well — and a hold margin scaled to
the PERIOD is meaningless, because hold reflects clock skew alone and does not
grow when the clock slows down. On gf180's 16 ns designs the missing flag
demanded **0.8 ns of hold margin**, against the tens of picoseconds real skew
amounts to. The resizer answered by inserting **1955 hold buffers (+30% cells)**
and then aborting CTS at `[ERROR RSZ-0060] Max buffer count reached`.

Found 13 designs into the campaign: `C00023__qoi_compress` passed in the pilot
and failed here. Re-run with the flag, the same design passes at `final` with
8909 cells. ORFS's own 1877 shipped designs and the pilot all use
`set_clock_uncertainty 0.0`, i.e. nobody puts a fraction of the period on hold.

Hold gets a fixed 20 ps instead — independent of the period, which is what skew
is. Nearer real signoff than ORFS's zero, without being absurd.

The conformance check now REQUIRES `-setup` and a separate `-hold 0.020`, and
was verified to report 60 inconsistencies against the pre-fix artifacts. A check
that accepts both spellings cannot tell the defect from the fix.

**The defect was wider than the one design that exposed it.** Rebuilt with the
fix, gf180 and nangate45 came back **30/30 and 30/30 complete, zero failures**
across 60 designs. Before it, gf180's preflight lost one design and the campaign
lost another to CTS within its first 13. So the missing flag was not merely
aborting the occasional design at the buffer cap — it was imposing surplus hold
repair on *every* design, inflating cell counts and pushing the marginal ones
over. Removing it lowered the failure rate as a side effect, which is why the
rebuild is also the cleanest evidence that the diagnosis was right.

### (historical) Clock uncertainty — 5% of the period

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
| frozen | **50%** | OpenLane's own default for sky130 (`FP_CORE_UTIL=50`, `PL_TARGET_DENSITY=0.5`), bracketed by measurement: u40 completes 60 of 61 and **u50 completes 58 of 61** on nangate45 — no cliff, ~3% pressure. gf180 and sky130hd measuring. |

**nangate45 sweep, 61 designs, period held at the pilot's 2 ns so only area moves:**

| post-synth cells | designs | at 40% | at 50% |
| --- | --- | --- | --- |
| 0–100 | 15 | 15/15 | 14/15 |
| 100–1 000 | 15 | 15/15 | 15/15 |
| 1 000–5 000 | 15 | 14/15 | 14/15 |
| 5 000–20 000 | 10 | 10/10 | 10/10 |
| >20 000 | 6 | 6/6 | 5/6 |

The three u50 failures are real, not contention: one `2_1_floorplan_error` and
two `place_failed`.

**gf180 and sky130hd, 59 designs each** (the last two of 61 were still routing
when the sweep was closed; the period is each platform's pilot value so again
only area moves):

| post-synth cells | gf180 | sky130hd |
| --- | --- | --- |
| 0–100 | 15/15 | 15/15 |
| 100–1 000 | 15/15 | 14/15 |
| 1 000–5 000 | 15/15 | 15/15 |
| 5 000–20 000 | 10/10 | 10/10 |
| >20 000 | 4/4 | **2/4** |
| total | **59/59** | **56/59** |

So across the three platforms: **173 of 179 complete at 50%, a 3.4% failure
rate**, and on top of the period's ~5% that is about 8% total pressure. Enough
that the constraint is not a formality — the pilot's 20% failed almost nothing —
and little enough that it does not manufacture a batch of failures r2g cannot
repair.

**The one exception, and why it is not acted on.** sky130hd loses 2 of 4 designs
above 20 000 cells (one `place_failed`, two `route_failed`). Three reasons to
leave the frozen value alone:

- **4 designs.** 2 of 4 carries no statistical weight and is not grounds for
  moving a parameter that applies to 2330.
- **It matches the platform, rather than contradicting it.** sky130hd has 5–6
  routing layers, so `route_failed` on its largest designs at 50% is the
  expected symptom and not an anomaly. gf180 has 5 layers and lost none, which
  rules out "5 layers cannot do 50%".
- **The trade is wrong even if the effect is real.** Dropping sky130hd to 40%
  enlarges the die for all 2330 designs to recover 2 — and those 2 failed with
  `route_failed`, which **r2g cannot repair anyway**, since relieving congestion
  means enlarging the die and the die is frozen. Cost without signal, from the
  other direction.

(The sweep varies utilization at run time and lets the driver derive the die from
the ACTUAL synthesis area, so it measures whether 50% is ROUTABLE on the
platform. It cannot catch a mis-sized frozen die — that is the preflight's job,
and it is what found the area allowance.)

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

**50% does not bind on the whole corpus, and the number must be stated with
that.** The die is `max(sqrt(cell_area / 0.50), platform PDN minimum)`, so for a
design small enough that the derived side falls under the PDN floor, the floor
sets the die and the achieved utilization comes out *below* 50% no matter what
the spec says. Raising the frozen value from 40% to 50% shrinks every derived
die by sqrt(0.4/0.5) = 0.894, which pushes more designs under the floor:

| platform | designs | PDN-floored | utilization actually binds on |
| --- | --- | --- | --- |
| nangate45 | 2330 | 837 | **1493 (64%)** |
| gf180 | 2330 | 583 | **1747 (74%)** |
| sky130hd | 2330 | 426 | **1904 (81%)** |

This is not a defect to fix — a die below the platform's minimum PDN pitch
cannot be built — but it does mean "a 50% utilization baseline" is shorthand for
"50% on the two thirds to four fifths of the corpus where the cell area is the
binding constraint, and PDN-floor-limited below that". Any claim about
utilization pressure applies to that subset, and the floored designs face no
area pressure at all.

### The die carries a measured area allowance, because the pilot's area is not the final area

The die is sized from the pilot's recorded cell area, and that number is
**smaller than what the frozen spec actually builds**. The pilot ran one flat
period per platform (2 / 10 / 5 ns); the frozen spec gives each size class its
own, so wherever the period tightened, the resizer inserts timing-repair buffers
and the final cell area exceeds the pilot's.

Measured on the 30-design preflight, counting only designs where utilization
binds (a PDN-floored design's ratio says nothing — its die came from the floor):

| platform | final / pilot cell area | buffer share | binding designs |
| --- | --- | --- | --- |
| nangate45 | 1.031 → **1.021** | 3.5% | 5 → **15** |
| gf180 | 1.184 → **1.144** | 10.0% | 7 → **18** |
| sky130hd | 1.092 → **1.190** | 12.3% | 5 → **18** |

The second column of each pair is the re-derivation from the 30-designs-per-
platform preflight, and **it is the one in use**. The first, from 5/7/5 binding
designs, was labelled here as "a first-order correction, not a calibration" —
and that caveat cashed out exactly where it was pointed:

**sky130hd's allowance was wrong by 9%.** At 1.092 the wide preflight measured a
**53.7% median against a 50% target, 9 of 27 designs over target+5, maximum
79.1%** — the same shape gf180 had before ITS correction. 18 binding designs put
it at 1.190; the die grows 4.4% on a side. nangate45 barely moved (1.031 →
1.021, 0 designs over target) and gf180 was slightly over-corrected (1.184 →
1.144, median 46.9%, i.e. marginally loose).

sky130hd was re-verified after the change rather than trusted. The reasoning for
skipping a re-run would have been "the direction is clear and the mechanism is
understood" — which is precisely the reasoning that produced the wrong number
the first time, on this platform. Thirty minutes against the 37 hours sky130hd
takes in the campaign.

The extra area IS the buffers where the period tightened: on gf180's C03211 they
are 39% of the final cell area, 27% on C00496, 24% on C03488. The allowance is
really two terms — about 1.07 for cells the synthesis area never counted (tap,
endcap, clock buffers) plus a period-dependent buffer term — which is why
nangate45, a fast process given comfortable periods, needs 1.03 while gf180 at
180 nm, with periods up to 4.5x tighter than its pilot's flat 10 ns, needs 1.18.

Why it must be corrected rather than noted: a frozen die turns a 10-point
overshoot into congestion, and congestion is the one symptom r2g cannot repair
once the die is fixed, because its only lever lowers utilization. The same test
every other parameter here was set by.

`derive_area_allowance.py` computes it from the preflight and writes
`spec/area_allowance.json`, which `make_spec_table.py` reads — so the number is
measured, not hand-picked, and re-running the preflight updates it. The defaults
compiled into the script are the same values, for a checkout without that file.

**Sample size for the derivation was thin: 5, 7 and 5 binding designs.** A wider
preflight (30 per platform) was run against the corrected table to check the
correction actually lands.

**nangate45, confirmed.** 30 designs, conformance green (so the run used the
allowance-corrected table), 29 measured, 1 flow failure (3.3%, matching the u50
sweep's rate). Of the 29, **14 are PDN-floored** — their die comes from the
floor, so their achieved utilization (1.3–40%) says nothing about the target and
drags the overall median to 48.5%. The 15 designs where utilization BINDS land
at:

```
48.5  48.9  48.9  49.0  49.0  49.1  49.3  49.7  50.8  51.1  52.0  52.5  52.7  53.1  54.1
```

centred near 50–51% against a 50% target, with the **maximum down from 57.4% to
54.1%** — which is what the allowance is for: pulling in the overshooting tail
rather than shifting the whole distribution. No change needed on this platform.

(Report the binding subset, not the overall median. Mixing in the floored
designs produces a median that is neither the target nor a measurement of
anything — it lands wherever the floored/binding split happens to put the
midpoint.)

**gf180, confirmed — this was the platform the allowance existed for.** 30
designs, conformance green, 11 PDN-floored, 18 binding:

| | before the allowance | after |
| --- | --- | --- |
| median | **60.0%** | **50.1%** |
| maximum | 71.9% | 68.2% |
| over target+5 | 6 of 10 | 5 of 18 |

The median lands on target. **The tail does not, and the reason is structural:**
the five still above 60% are the SMALL designs — 216, 324, 352, 627 and 2773
cells — not the large ones.

That follows from how the allowance is built. It is one multiplier per platform
(1.184), and the overshoot it corrects is driven by timing-repair buffers, whose
share is largest where the period tightened most. gf180's small-design period
went from the pilot's flat 10 ns to 2.2 ns, **4.5x tighter**, while its large
designs went to 16 ns, *looser*. One flat multiplier cannot level that: it moves
the distribution down and centres the median, leaving the small end high.

**Not corrected, deliberately.** All five completed — 68% is routable on gf180,
and the whole 30-design preflight lost one design (3.3%). A per-size-class
allowance would add a second table of constants to calibrate and maintain, to
pull five designs from 68% to 50% that already pass. The frozen standard is "not
a formality", not "exactly 50% on every design".

Recorded rather than smoothed over, because it is the obvious place to start if
the allowance is ever refined: **the residual is a small-design effect, and it
is the period change that drives it.**

**Timing: the p95 table delivers what it claims.** Under the full frozen spec
(binned periods and the allowance-corrected die), 23 of 24 nangate45 designs and
22 of 23 gf180 designs meet their period — **96% against the 95% the table was
built for**. The concern that p95 would under-deliver, because achievable
periods were measured at the pilot's 20% utilization where wires are shorter,
does not show at this sample size; the area allowance appears to absorb it.

Two details worth carrying: gf180 has one design at `worst_slack = -15.67 ns`
against a 2.2–16 ns period — an order out, not a near miss, and isolated (the
median is +1.49 ns). And 5–6 designs per platform report NO slack at all, having
no constrained path even with the I/O delays added; they are not failures and
must not be counted as any — a design with no timing path has no timing.

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

### "DRC clean AND LVS clean" is not the same condition on the three platforms

Measured, and it changes how the numbers may be used:

| | what DRC actually finds | LVS checker | LVS failures + mismatches (dev) |
| --- | --- | --- | --- |
| nangate45 | **99.9% antenna** (3526 of 3528) — effectively one check | **r2g's `FreePDK45.lylvs` (klayout)** | **88 + 23 = 111** |
| gf180 | 54 rule tables, CO.6a 95% of violations, **zero** antenna | netgen + the PDK's own `gf180mcuC_setup.tcl` | 0 + 15 |
| sky130hd | **unknown** — 2156 reports exist but the corpus kept 5 of 2497 GDS, so none can be re-derived | netgen + the PDK's own `sky130A_setup.tcl` | 0 + 7 |

Two consequences:

- **The three platforms' counts must not be summed.** A total like "5344
  dual-clean" is arithmetic over three different conditions, not a measurement of
  one thing. Each platform is compared against ITSELF (its own pilot, its own
  control arm); cross-platform totals are dropped.
- **nangate45's LVS failure rate is not a strictness difference — RESOLVED.**
  Reading the 88 `lvs_failed` logs: **72 are timeout/killed, 16 have no log at
  all, and ZERO say the netlists do not match.** Median cell count 61 821. The
  genuine mismatches are the separate `mismatch` count, and those three numbers
  ARE comparable:

  | | genuine LVS mismatch | no verdict (timeout / no log) |
  | --- | --- | --- |
  | nangate45 | **23** | 88 |
  | gf180 | **15** | 0 |
  | sky130hd | **7** | 0 |

  So the 7–15x gap was entirely no-verdict designs. nangate45's checker is not
  stricter and its judgement is not suspect; it does not SCALE. Which is the
  same shape as its DRC: 67 designs stall in the ANTENNA block at a median of
  89 111 cells, 72 time out in LVS at a median of 61 821. **Both of nangate45's
  checkers fail to reach a verdict on large designs, while gf180's and
  sky130hd's do.**

  "No verdict is not a pass" still counts those 88 as failures, correctly. But
  they belong in the taxonomy's **checker** bucket, not the repairable one: what
  would have to change is the checker, not the design, so r2g's repair arm
  cannot win them back and must not be credited with them.

  **"Both checkers fail on large designs" was an over-generalisation.** Checked
  properly, the two sets overlap but are not one problem:

  | | designs | cause | explained by size? |
  | --- | --- | --- | --- |
  | DRC and LVS both | **61** | timeout | yes — median 95 686 cells |
  | DRC only | 7 | ANTENNA stall | yes — median 41 010 |
  | LVS only | **27** | **11 klayout Ruby exceptions + 16 produced no log** | **no** — median 7 657 |

  Two explanations for those 27 were checked and rejected: the netlist is NOT
  missing (their artifact set is identical to a design whose LVS passes), and
  `cell count: 0` is NOT the discriminator (it appears on 1084 designs of which
  992 are clean — the count read always fails and the timeout always falls back
  to 5400 s). The `make: No rule to make target` line appears in CLEAN designs'
  logs too: it is the designed frozen-layout fallback, which the log annotates
  itself, so classifying on it only says the fallback ran.

  The cause of the 11 is **undetermined** — the backtrace carries addresses but
  not the Ruby exception message. The wrapper prints "LVS completed" after the
  crash, which is its own defect: a crashed checker reporting completion is how
  a tool failure gets read as a verdict.

  None of this changes where the 95 designs sit (checker bucket, not
  repairable). It changes what fixing them would take: 68 need a checker that
  scales, 27 need a tool bug found.

```
pass = DRC clean  AND  LVS clean
setup slack: RECORDED, not judged in round 1   (see "Timing is NOT a pass condition")
hold slack:  RECORDED, not judged in round 1
no verdict (timeout / crashed checker / no layout) = NOT pass
```

An earlier draft of this block read `AND setup WNS >= 0`. That was wrong and
would have invalidated the comparison: the pilot did NOT enforce it — 237
sky130hd designs missed their period and 224 of them were recorded dual-clean —
so a re-run that enforced it would be scored on a different ruler from the
baseline it is measured against. The tally code was already correct (it counts
`drc_status == clean and lvs_status == clean`); the document has been corrected
to match both it and the pilot.

### Designs that never reach a layout are reported apart, not folded in

Synthesis failures are not backend failures. In dev, 55 designs fail synthesis
on **all three platforms** (parse errors, synthesis errors — the same designs
each time, since the cause is platform-independent RTL), and 91 fail on at least
one. They can never be dual-clean anywhere, under any parameters, and
signoff-loop has no lever on them — they belong to the RTL-acquisition side.

So the backend baseline's denominator is **1194** (1249 − 55), with the 55
reported on their own line. This is not hiding them: folding them in makes the
backend look worse than it is AND hands the repair arm a pool it cannot touch,
so neither the baseline nor the improvement would mean what it says.

The 85 dev designs with no spec entry need no special handling: every one of
them is a frontend failure on at least one platform (verified — the overlap is
85 of 85), so the `SPEC MISS` fallback period only ever applies to designs that
never reach a floorplan.

Two denominators exist and they answer different questions, so the tally prints
the per-platform one and this document states the corpus one:

- **per-platform** — designs that reached the backend on *that* platform
  (dev: 1159 nangate45, 1193 gf180, 1160 sky130hd). The right denominator for
  that platform's pass rate.
- **corpus-wide 1194** — designs that reach the backend on at least one
  platform, i.e. 1249 minus the 55 that fail synthesis everywhere. The right
  denominator for "how many designs are even eligible".

One known blind spot in the per-platform figure: gf180's frontend count (56 on
dev, against 90 and 89) is probably low, because 34 of its no-layout designs
carry `failure_class = no_log_harvested` — the log was not captured, so they
cannot be classified as frontend or backend. Do not read gf180's larger backend
denominator as a stronger frontend until those 34 are classified.

### The stage timeout was 3 h in the pilot and is 6 h here — that alone raises the number

Measured: the pilot ran a 10 800 s stage timeout, and it bound on **114 dev
designs** (27 nangate45, 34 gf180, 53 sky130hd). No design exceeded 6 h because
none was given 6 h — the flow-time maximum is ~10 833 s on every platform, i.e.
the timeout itself.

So some of those 114 will complete under the frozen 21 600 s, and the re-run's
count will be higher than the pilot's **partly for that reason alone**. That is a
looser ruler, not a repair. The pilot's numbers are context for the re-run, never
a baseline the re-run improves on, and any claim of the form "the re-run is N
designs better" is invalid: the only valid comparison is repair-arm against
control-arm WITHIN the re-run, where both arms get 6 h.

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

### One slow rule must not destroy the verdict on all the others

Both platforms whose checkers lose verdicts lose them the same way, and the fix
is the same shape on both: **run the deck in parts, each with its own budget and
its own recorded outcome.**

- **gf180** — a single monolithic pass over the assembled deck timed out on 86
  dev designs. The PDK's own `run_drc.py` executes 58 rule tables separately and
  graded C03506 (18 290 cells) in 3891 s where the monolithic pass produced
  nothing. Its verdicts match the assembled deck where both finish (1 = 1 on
  C00277, 2 = 2 on C03746).
- **nangate45** — 67 dev designs have no DRC verdict, and **all 67 stall at the
  same place**: `FreePDK45.lydrc:376`, inside the ANTENNA block. That block is
  the deck's LAST section, so roughly 370 other rules had already run and their
  result was discarded, because klayout writes its report only at the end. The
  designs that stall have a median of 89 111 cells against 350 for the designs
  that grade, and the smallest is 39 782 — it is a scale limit in one check, not
  a property of those designs. The deck already exposes four switches (`FEOL`,
  `BEOL`, `OFFGRID`, `ANTENNA`), so the split needs no deck surgery.

  **But splitting buys much less here than the same move buys on gf180, and the
  measurement says so.** Across the 1044 dev designs with a nangate45 report:
  858 hold no violation, 184 hold *only* antenna violations, and **2 hold a
  non-antenna violation — one each**. Of 3528 violations in total, 3526 are
  antenna: **99.9%**. So the ~370 rules that would be recovered by splitting are
  the rules that find nothing on this platform, and the check that did not
  finish is the only one that finds anything. The recovered verdict is real but
  nearly uninformative — "clean on every check that was never going to fire".
  On gf180 the same split recovers a verdict that can and does say `violations`
  (CO.6a is 3920 of its 4113), which is why it is worth the wall time there and
  marginal here.

  **Status: the nangate45 split is NOT validated, and is not being validated.**
  A split is only a decomposition if the groups' verdicts are unchanged by
  running them apart, and there is reason to doubt it here: with `ANTENNA=false`
  klayout attributes FEOL's first operation to `FreePDK45.lydrc:91` (inside the
  `classify_by_width` helper) where the full-deck run attributes it to line 114
  and never reports line 91 at all. The decks are byte-identical but for that
  one switch, so the execution path really does differ — klayout's deep mode is
  lazy, and the antenna block's `connect()` statements materialize layers the
  rest of the deck then reuses.
  Two attempts to settle it failed on measurement design, not on the tool: the
  first picked the smallest design with any violations and they were all
  antenna, making the comparison 0 against 0 — which this project already
  records as no evidence; the second gave the DRC a 1800 s budget on a design
  the corpus had needed longer for, and it returned `drc_failed`.
  It is not worth a third attempt: only **2 of 1044** dev designs carry a
  non-antenna violation, one each, so even a clean run would be a two-sample
  test of something already measured to be marginal. If nangate45's 67 designs
  ever need their verdicts, this has to be settled first.

The same holds for LVS: 88 dev designs on nangate45 have no LVS verdict, median
61 821 cells against 533 for those that do.

**A recovered design is not clean.** Splitting turns "no verdict at all" into "a
verdict on every group that finished, and an explicit unknown on the one that
did not" — so a design passing FEOL, BEOL and OFFGRID with ANTENNA unfinished is
`clean_except_antenna`, a third value, never folded into `clean`. Unknown is not
a pass; that is the same rule that makes a timeout a failure, applied one level
down.

There is an irony worth recording with it, and it now has a number: nangate45
can check antennas at all only because r2g supplied the missing LEF antenna data
(contribution A2) — and that one check accounts for **99.9% of everything the
platform's DRC finds**, while also being what costs 67 designs their verdict.

Without the patch, 184 of the 1044 dev designs that currently read
`drc_status=violations` would read clean, and nangate45's DRC would be
essentially vacuous: 1042 of 1044 designs passing a deck that found two
violations in total. The patch is declared APPARATUS — both arms get it, so it
earns no repair credit — but it does mean **the nangate45 baseline sits 184
designs below where the stock platform would put it**, because the stock
platform cannot see what it is checking for. That is the case the frozen
contract describes as "point out where the official data is internally
inconsistent, and still run the baseline with the apparatus in it".

### What each platform's DRC deck actually checks — measured, not assumed

A deck's rule count is not its coverage. Counted from the decks themselves:

| | rule outputs | commented out | self-disclaimed | what it actually finds |
| --- | --- | --- | --- | --- |
| **nangate45** | **136** | **3** | **3 places** | **99.9% antenna** (3526 of 3528) |
| **sky130hd** | **452** | 7 | 0 | unknown — no GDS retained |
| **gf180** | **791** (58 PDK tables) | 0 | 0 | CO.6a is 95% of 4113 |

nangate45 checks **one sixth** of gf180's rules, and its own deck disclaims part
of what remains:

- `POLY.2` (poly-to-active spacing, 140 nm) and `POLY.3` (poly extension beyond
  active, 55 nm) are **commented out**, above the line `# TODO(rovinski) This
  section errors out for some reason`.
- the via-enclosure families `METALx.3` / `METALx.4`, up to 20 outputs across 10
  metal layers, carry `# TODO(rovinski) Via enclosure rules do not appear to
  correctly identify violations`.

That resolves something measured earlier and left unexplained: **0 FEOL
violations across 364 dirty nangate45 designs.** Not clean silicon — two of the
FEOL rules are switched off and the layer is barely exercised. A measurement of
zero on a check that is not running is not a measurement.

**This is the strongest reason the three platforms' counts are not summed.** A
design that is "DRC clean" on nangate45 passed 136 rules of which 3 are off and
up to 20 are unreliable, with essentially one rule family doing all the finding.
On gf180 it passed 791. Those are not the same claim.

### What is deliberately NOT checked, per platform

Separating "measured and passed" from "not measured" matters more than the pass
rate, because an unmeasured check reads as a pass.

| check | nangate45 | gf180 | sky130hd | why |
| --- | --- | --- | --- | --- |
| DRC | ✅ 136 rules (3 off) | ✅ 791 | ✅ 452 | the pass condition |
| LVS | ✅ klayout `.lylvs` (r2g-supplied) | ✅ netgen | ✅ netgen | the pass condition |
| antenna | ✅ (needs r2g's LEF data) | ✅ `--antenna` on | ✅ deck default | — |
| metal density | — | **OFF by decision** | — | satisfied by fill insertion, which the tool does automatically: r2g has no lever, so it would be cost without signal |
| setup timing | recorded | recorded | recorded | r2g does not do timing closure; judging it would manufacture ~224 sky130hd failures it cannot act on |
| hold timing | recorded | recorded | recorded | failure rate unknown; promoted to a pass condition once it is known |
| IR drop / EM | ✗ | ✗ | ✗ | never in scope for this baseline, and not claimed |
| power | ✗ | ✗ | ✗ | same |
| netlist ≡ RTL | ✗ | ✗ | ✗ | **LVS compares the layout against `6_final.cdl`, which descends from `6_final.v` — the POST-layout netlist.** So LVS proves the layout matches what the tools built, NOT that it matches the design intent: it cannot catch a synthesis error. Standard practice, stated because "LVS clean" is easily read as the stronger claim. Equivalence against RTL is E3's job, not the baseline's. |

### Three per-platform decisions, made rather than defaulted

Each of these was previously "whatever the tool does by default", which is not a
decision. Recorded as decisions so they can be argued with.

**gf180's metal-density rules stay OFF.** The PDK ships `density.drc` and the
official `run_drc.py` exposes `--density`, off by default. Keeping it off, for
three reasons in order of weight: density is satisfied by FILL INSERTION, which
the tool does automatically, so **r2g has no lever on it** — enabling it would
add a class of failure that is cost without signal, which is the test every
parameter here was set by; the official runner's default is off, and the
baseline is defined as the official flow; and the pilot ran with it off, so
leaving it off keeps the two comparable. If a reviewer wants density in scope it
belongs in a separate arm, not folded into this baseline.

**sky130hd's violation composition stays unknown for the pilot.** 2156 DRC
reports exist but that corpus retained 5 of 2497 GDS files, so no report can be
re-derived from the layout it judges and the composition cannot be established
to the standard the other two platforms were. Not worth regenerating GDS from
the 2167 retained DEFs: the re-run keeps GDS on all three platforms and will
answer it as a side effect. Recorded as a known limitation of the pilot rather
than quietly left as a gap.

**Both checkers keep a time cap, and the LVS one is a fiction.** A checker that
does not finish must become a verdict (failure) rather than hang, so the caps
stay: DRC 7200 s, LVS 5400 s. But the LVS cap is NOT adaptive the way it claims:
`run_lvs.sh` prints "Auto-scaled LVS timeout to 5400s (cell count: N)" and N
reads **0 on 1081 of 1084 designs**, so the scaling input is dead and every
design gets the same 5400 s. Left unchanged — altering it would change the pass
condition mid-spec — but recorded, because a mechanism that appears to adapt and
does not is the same defect class as the rest of this document: it looks like it
is working.

### What the gf180 checker comparison does and does not establish

The assembled deck is r2g-supplied and the PDK's `run_drc.py` is official, so
using the fast one where it finishes and the official one where it cannot needs
the two to agree. "Both said clean" is not that evidence, and
`EXPERIMENT_RUNBOOK_zh.md` records why — three LVS configurations once agreed
and all three were failing; what agreed was the failure.

The evidence has two halves and only works as a pair:

- **Positive control** — on three designs the assembled deck graded dirty, the
  official runner reports the same RULE NAMES with the same per-rule counts: 1
  on C00277, 2 on C03746, and 10 on C03506 (18 290 cells, which the monolithic
  pass could not grade at all). Rule names, not counts: two different
  violations both count 1. This is what shows the official runner is actually
  looking.
- **Negative test** — on designs the assembled deck called clean, the official
  runner finds nothing: **20 of 20**, weighted toward the large end where a deck
  is likeliest to miss something, every one of them with all 54 tables empty.

Given the control, the negative test is evidence that the deck did not miss
anything **on those designs**. It is not evidence that it never misses: twenty
designs is a sample, and the claim stops there.

Cost, measured on the same 20: median 239 s, min 122 s, max 3262 s. That is far
below the 2598 s averaged over the four deliberately-hard validation designs, so
the hybrid budget should be priced from the designs it will actually run the
official checker on -- the 86 the assembled deck cannot grade, which are the
large ones.

One provenance trap found while assembling this. `6_drc.lyrdb` in a run
directory is not necessarily the verdict `result.json` describes — on C03506 the
report is a day newer than the timed-out attempt recorded beside it. It judges
the SAME unmodified GDS, so the comparison holds, but the check that it does
has to be made rather than assumed.

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

### Reading the re-run against the pilot: the change is TWO-directional

At the 190-design checkpoint on gf180, the frozen spec and the pilot agree on
**97%** of the same designs. The 3% that differ are not all losses:

| | designs |
| --- | --- |
| pilot pass → now fail | 4 (three `3_3_place_gp`, one `3_5_place_dp`) |
| **pilot fail → now pass** | **1** |

The reverse case matters more than its count. It refutes the natural reading of
any headline difference — "the frozen spec is harder, so it can only cost
designs". A 20% utilization die is not merely *loose*, it is **wrong in both
directions**: on a tiny design it produces a core so sparse that the placer
diverges (`GPL-0307`), which is exactly how two 7-cell `lvdc_simulation`
designs fail here. A spec that sizes the die properly can therefore RECOVER a
design the pilot lost.

So a sentence of the form "the re-run has N fewer dual-clean than the pilot"
describes a net of two opposing movements and attributes it entirely to one.
Report both directions, or report neither.

(Also recorded because the 97% is itself the load-bearing number: if the frozen
spec agreed with the pilot on only, say, 70% of designs, it would be measuring
something else and the pilot would stop being usable as context at all.)

## What the re-run is for

Every contamination question resolved to "apparatus, declared", so **the re-run
is needed because the SPEC changed** — per-design period, explicit die, three
SDC additions — not because the pilot was contaminated. The nangate45 stock-LEF
re-run that was planned for contamination is cancelled.

**Round 1 builds dev only** (1249 designs). Held-out is where repairs developed
on dev get validated, so building its baseline before there is anything to
validate buys nothing and costs half the machine time — `SET=held_out` switches
`rerun_baseline.sh` over when the time comes. Its baseline has to exist before
any held-out claim; it just does not have to exist yet.

Cost, priced from the pilot's own per-design wall times rather than from a
throughput figure: at `--workers 12`, dev is 16.2 h of flow for nangate45, 50.7 h
for gf180 (28.2 of it DRC) and 37.0 h for sky130hd — **104 h = 4.3 days
sequentially**, and that excludes nangate45's signoff, which that corpus does not
record. Including the 67 designs that stall for their full 7200 s brings it to
roughly 4.8 days. Three platforms cannot be run concurrently to compress it;
that is the state where load read 130 and nothing completed for 30 minutes.
