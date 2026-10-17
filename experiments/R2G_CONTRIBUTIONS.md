# What r2g contributes — the running ledger

Two kinds of entry, kept apart because they are measured differently:

- **Part A — eligibility.** Things upstream cannot do at all, which sit INSIDE
  the baseline as declared apparatus (see `BASELINE_PARAMETERS.md`). They are
  contributions, but they are **not counted as repair credit** — they are what
  makes the platform measurable in the first place.
- **Part B — repair.** Recipes that fix a failing design. These ARE the credit,
  and each carries its validation status.

Part C records tool defects found and fixed while running the campaign; Part D
the measurement discipline. Append to Part B as repair work proceeds; **one
entry per contribution, each with the measurement that establishes it.**

Appending convention: state what upstream does, what r2g does, the number that
proves it, and the validation status. An entry with no number is not an entry.

---

## Part A — eligibility: what upstream cannot do at all

### A1. nangate45 has no LVS rule upstream

Upstream OpenROAD-flow-scripts **ships no LVS rule for nangate45**. Without one
ORFS's `make lvs` silently reports "skipped" — the installer's own note says so.
r2g bundles `FreePDK45.lylvs` (`tools/install_nangate45_lvs.sh`).

**Without it the platform produces zero LVS verdicts, so zero dual-clean
designs** — every nangate45 number is conditional on it.

It is a pure checker: it reads GDS and CDL and writes a report, and cannot alter
a layout, so it adds no repair.

### A2. nangate45's antenna model is absent, while its DRC deck checks antennas

Stock FreePDK45 carries **zero** `ANTENNAAREARATIO` rules in the tech LEF and an
`ANTENNA_X1` diode with `ANTENNADIFFAREA 0.0`, which OpenROAD rejects
(GRT-0246). Meanwhile the shipped DRC deck has `ANTENNA = true` by default and
produced **6649 `METAL*_ANTENNA` violations** across the pilot corpus.

A PDK that checks a rule and gives the tools no data to satisfy it is
incomplete, not opinionated. `tools/install_nangate45_antenna.sh` adds the
missing metadata: 532 `ANTENNAPARTIALMETALSIDEAREA`, 532
`ANTENNAPARTIALMETALAREA`, 387 `ANTENNAGATEAREA`, 146 `ANTENNADIFFAREA` lines
added, and exactly one value edited (the diode's `0.0` → `0.1`). Nothing else in
either LEF changes, and the GDS library is untouched.

**Without it all 369 antenna-dirty nangate45 designs are permanently
unfixable.**

**Measured 2026-10-17 — this one check is 99.9% of what nangate45's DRC
finds.** Across the 1044 dev designs holding a nangate45 DRC report: 858 have no
violation, 184 have *only* antenna violations, and **2 have a non-antenna
violation, one each**. 3526 of 3528 violations are antenna.

So the stock platform's DRC is not merely missing a rule — with no antenna data
it is close to vacuous on this corpus: 1042 of 1044 designs would pass a deck
that found two violations in total. The patch is what gives nangate45 a DRC with
content.

Two consequences, and the second is a cost:

- **The nangate45 baseline sits 184 designs BELOW where the stock platform would
  put it.** The patch is declared apparatus — both arms get it, so it earns no
  repair credit — and it makes the baseline harder, not easier. Recording it
  here because the opposite is the natural assumption about a contribution.
- **It is also what blocks 67 dev designs from any DRC verdict at all**: every
  one of them stalls inside the ANTENNA block (`FreePDK45.lydrc:376`), after the
  other ~370 rules have run. Splitting the deck recovers those rules' verdict,
  but on this platform that verdict is almost content-free for the same reason
  the patch matters — the other rules find nothing. See BASELINE_PARAMETERS.md,
  "One slow rule must not destroy the verdict on all the others".

### A3. ORFS does nothing on PDN-0185; r2g recovers it

`pdngen` needs a core wider than one strap set (sky130hd: 13.57 µm offset +
15.2 µm strap = 28.8 µm). A design sized by utilization below that aborts
floorplan with `[ERROR PDN-0185] Insufficient width`. **A grep of ORFS's
`flow/scripts/` and `flow/Makefile` for `PDN-0185` finds no handling at all** —
the flow aborts and the design is lost.

r2g owns this as a named recipe, `engineer_loop._relieve_pdn_strap_width`
(introduced **2026-07-01**, commit `e1ad62c`): drop `CORE_UTILIZATION`, pin an
explicit PDN-feasible square die.

Pilot scope: **1137 of 5181 dual-clean designs across the three platforms exist
only because of this** (nangate45 492, gf180 367, sky130hd 278), rescue rate
near 100%. The rescued designs are tiny — sky130hd's rescued median is **10
cells / 75 µm²** against 840 cells / 6600 µm² for the rest — and a design that
small physically cannot be narrower than the power grid allows, so the sparse
die is a property of the PDK, not a flaw in the fix.

Measured minimum: **32 µm on sky130hd; 30 µm still fails** on met5 after site
snapping.

Known improvement target: the recipe hard-codes `_PDN_DIE_FLOOR_UM = 200`
(validated on one design, cordic) against the measured 32 µm — a **39x area
difference for the same rescue**. Deriving the floor from the width PDN-0185
actually reports would be strictly better.

### A4. ORFS's gf180 PDN strategy violates GF's own rules

ORFS's gf180 PDN strategy requests multi-cut via arrays (`-max_columns 5`,
`-split_cuts`) which merge into 0.772 × 1.028 rectangles and violate GF's own
V2.1/V3.1 ("every Via2/Via3 edge exactly 0.26 µm"). Recorded effect on one
design: **824 violations → 0**, and **82% of all violations** in the platform
traced to this. r2g supplies a replacement strategy.

*(Evidence is the campaign record in `chain180.sh`'s header, not a measurement
re-made here.)*

### A5. ORFS's gf180 tapcell distance violates the 5 V rules

ORFS ships `tapcell -distance 100` while GF's DF.13/DF.14_MV cap the distance to
the nearest substrate tap at **15 µm for 5 V devices**. r2g supplies
`-distance 30`.

*(Same provenance as A4.)*

---

## Part B — repair: the measured credit

**Every recipe below was developed against the WHOLE corpus, before
`experiments/splits/` existed.** They must be re-validated on **dev** and may
not be reported as held-out results. Status is tracked per entry.

### B1. `dont_use_violating_master` — ban the cell master whose own layout fails the deck

gf180's CO.6a is the poly/diffusion-to-M1 contact rule and the router never
makes a CO, so the violating geometry belongs to a standard cell shipped by the
PDK. Every other DRC lever (density relief, pin rebalance, route effort) re-runs
the flow to move geometry that is not the problem.

The decision is geometric, not a platform or rule allowlist
(`extract/attribute_drc_to_cells.py`): each violation is mapped into the
orientation-normalised frame of the instance containing it, and the ban is
offered only when one master owns ≥80% of the dominant rule's violations at ONE
cell-frame offset AND a same-function sibling survives the ban.

| measurement | value |
| --- | --- |
| gf180 dirty designs carrying coordinates | 583 |
| attributed to `gf180mcu_fd_sc_mcu9t5v0__aoi221_2` at cell-frame (6.6675, 4.2) | **545**, same master / rule / offset in all |
| violations covered | 7163 |
| designs where CO.6a is the only rule | 532 |
| A/B on 12 subjects | **47 violations → 0** |
| QoR cost | ≤1.3% cells, ≤2.3% area; md5's timing IMPROVED (WNS −0.22 → −0.16) |
| negative control | **0 of 83** sky130hd and **1 of 364** nangate45 dirty designs qualify — m3.2 and METAL\*_ANTENNA are router geometry and the gate declines them |

End-to-end verified through r2g's own CLI: diagnose ranks it first, `--apply`
writes `override export DONT_USE_CELLS = *_1 <master>`, and a probe makefile
reproducing ORFS's include order reads it back with `origin=override`.

**Status: needs dev re-validation.**

### B2. `deck_driven_antenna_diode` — insert on the DECK's nets, not OpenROAD's

On nangate45 OpenROAD's antenna model and the signoff deck do not compute the
same quantity — they disagree by **~200x**, and `check_antennas -verbose`
reports 0 net / 0 pin violations on a layout the deck flags 16 times. So
OpenROAD's violating set is EMPTY: no threshold reaches those nets, and
`repair_antennas` has no `-net` option. The recipe takes the net list from the
deck's own lyrdb, mapped back through the DEF.

| measurement | value |
| --- | --- |
| designs DRC-dirty + LVS-clean | 315 |
| whose lyrdb polygons map to ≥1 DEF net | **314** (the 315th maps to zero and the recipe REFUSES it rather than guessing) |
| diodes the mapping requires | 6997 (median 6/design) |
| flow outcome on 314 subjects | **314/314 pass**, hook fired 314/314, **6275 diodes inserted, 4 skipped, 0 errors** |

**Status: needs dev re-validation.** Its DRC verdict is also blocked by a
platform limitation, not by the recipe: the FreePDK45 deck's FEOL booleans stall
(`"or" in: FreePDK45.lydrc:131`, 2473 s for one op) on 107 of 145 graded
repaired designs. Of those that completed, **35 of 36 are clean**.

### B3. PPL-0024 die relief, and the non-monotone target it rested on

`_set_explicit_die` sizes the die to the perimeter the IO placer demands.
Baseline was 0 of 39 PPL-0024 designs passing; with the recipe **38 of 39**
pass, the one failure an honest transfer (PPL-0024 solved, then a detailed-route
timeout at 159 k cells).

The fix landed this round: the placer's message is **not monotone**. Across those
39 designs the demanded/current perimeter ratio spans **0.9839 to 3.7027**, and
on 2 of them the placer demands LESS perimeter than the die already has while
still aborting (`C00599 udma_apb_if`: 391 pins into 380 slots at 221.90 µm,
demands 218.96 µm). Taking the demand alone asks the relief to SHRINK exactly
the designs that are already too small; only `_PIN_PERIMETER_MARGIN = 1.15`
being larger than the contradiction kept it working. The target is now
`max(current, demanded)` — monotone by construction. Effect today: `core_side`
changes on those 2 designs only.

### B4. `pin_side_rebalance` — precondition surveyed

sky130hd's m3.2 pool: 70 designs DRC-dirty with LVS already clean, 63 with m3.2
as their only rule. Running the REAL gate (not a reimplementation) over all 70:
`density_relief` applies to **70/70**, `pin_side_rebalance` to **69/70 — and all
69 localise on the SAME die edge (`right`)**. That is one systematic cause, not
69 design properties.

**Status: precondition only; effect not yet measured. Needs dev re-validation.**

---

## Part C — tool defects found and fixed while running

Not repair credit; they are the reason the measurements above can be trusted.

### C1. The LVS crash retry was dead on the only path the corpus uses

KLayout 0.30.7's comparer SIGSEGVs. The retry existed but sat BELOW the
frozen-layout fallback, so a crash inside the fallback reached an unconditional
`break` with no retry. Of 127 designs, 47 crashed at least once; the fix
measurably changed **11 of 47 verdicts** — 2 rescued to clean, 9 given a real
non-clean verdict after crashing. (`eddb7df`)

### C2. A restaged design got LVS's smallest timeout and most retries

`run_lvs.sh` read the cell count from `6_report.json`, which a restaged harvest
does not reproduce, so the count read 0 and BOTH consumers silently took their
smallest setting: a flat 5400 s timeout instead of up to 28800 s, and 4 crash
retries on a design big enough to warrant 1. Fixed with a DEF `COMPONENTS`
fallback. (`768e0c1`)

### C3. A plain `export` is a silent no-op for a platform-assigned key

`scripts/variables.mk` includes the platform config AFTER the design config, so
a design-side `export DONT_USE_CELLS` is discarded on gf180 and nangate45, which
both assign it with a plain `=`. Verified with a two-line makefile: plain →
`[*_1]` (origin=file), `override export` → `[*_1 MYCELL]` (origin=override).
`platform_assigned_keys()` now reads which keys a platform actually clobbers
(`?=` defers, `+=` appends, so neither needs `override`) and `apply_edits` writes
`override export` for exactly those — targeted, never blanket, because
`override` also defeats make's command-line variables and `run_orfs.sh` passes
`DESIGN_CONFIG`/`FLOW_VARIANT` that way. (`3399a40`)

### C4. Two defects that `override` itself introduced

Both invisible to the unit suite, which only ever wrote plain `export`:
the edit DISCARDED the platform's own ban list (on gf180 that un-bans every
`*_1` cell while fixing one master), and the post-apply landing check rejected
the very spelling it had just written — reporting `no_effect` while make read the
value back with `origin=override`, which would have left the recipe
**permanently inert in the autonomous driver**. (`0c081c6`)

---

## Part D — measurement discipline

Not contributions to the tool; they are what makes the numbers above mean
something.

- **`references/signoff-contract.md`** — what each platform checks, which
  ingredient belongs to checking / apparatus / repair, and what counts as a
  pass. Required by `EXPERIMENT_PLAN.md` P0 ("define strict-clean per
  platform").
- **`experiments/splits/`** — the repo-level, post-dedup dev/held-out split
  required by E2, frozen with 0 units straddling the boundary.
- **`experiments/BASELINE_PARAMETERS.md`** — every frozen parameter traced to
  the measurement that set it.
- **Positive controls over absence of error.** Three defects this round were
  caught only because a probe demanded a non-zero answer: `grep -c FILE || echo
  0` printing 0 for a MISSING file turned four runs that produced nothing into a
  "pass"; a host path invisible inside the container surfaced as
  `1_2_yosys_error`; and a `setsid`'d driver believed dead ran 70 more minutes
  and killed a concurrent test's containers via a name collision. The rule now
  recorded: when a probe's good answer is zero or empty, give the error path its
  own loud value and carry a control that must come back non-zero.
