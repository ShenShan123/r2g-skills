# The Signoff Contract — what each platform checks, and what counts as a pass

A repair campaign measures how many designs r2g can bring to **dual-clean**
(DRC clean AND LVS clean). That number is meaningless unless the yardstick is
fixed first and never moved again. This file is that yardstick.

**The rule that makes the number mean something:** the count must grow because
designs were REPAIRED, never because the checking changed. So the contract is
frozen per platform before a campaign, and a change to it invalidates every
number measured under the old one.

## The three layers

Every ingredient of a run falls into exactly one layer, and the layer decides
whether it may contribute to the repair credit.

| Layer | Rule | May it count as repair? |
| --- | --- | --- |
| **Checking** — decks, checkers, their switches | Must be the OFFICIAL deck with official defaults. A deck we modified is never the yardstick. | No. It IS the yardstick. |
| **Apparatus** — anything non-official applied IDENTICALLY to both arms | Allowed, but must be declared here with the reason it is unavoidable. | No. |
| **Repair** — applied only to the repair arm | This is the thing being measured. | Yes. |

The test for an ingredient is not who wrote it, it is **whether both arms get
it**. A checker that only reads (a `.lydrc` / `.lylvs` reads GDS and CDL and
writes a report; it cannot alter a layout) is apparatus even when r2g supplies
it. Anything that changes what the implementation tools PRODUCE is repair, and
belongs to the repair arm alone — including a patched platform library, because
the official tool then runs on an unofficial input.

**No verdict is not a pass.** A timeout, a crashed checker, a flow that never
produced a layout: all count as NOT clean. That is how the corpus has always
recorded them, so the frozen number stays on the same ruler as its history.

## nangate45 — FROZEN 2026-10-17

| Item | Definition | Layer |
| --- | --- | --- |
| DRC deck | `platforms/nangate45/drc/FreePDK45.lydrc`, unmodified — verified byte-identical to both `.bak` copies in the tree | official |
| DRC scope | the deck's own defaults: `FEOL = true`, `BEOL = true`, `ANTENNA = true`, OFFGRID on | official |
| DRC budget | 7200 s. Timeout ⇒ no verdict ⇒ **not clean** | official |
| GDS library | `NangateOpenCellLibrary.gds`, untouched | official |
| LVS rule | r2g's `FreePDK45.lylvs` | **apparatus** |
| Antenna LEF model | stock LEF in the BASELINE; the r2g patch belongs to the repair arm | **repair** |
| Flow | stock ORFS, 2 ns, `--pdn-core-floor-um 32`, 8 cores/design | apparatus (identical both arms) |

### Why the LVS rule is apparatus and not repair

Upstream OpenROAD-flow-scripts **ships no LVS rule for nangate45** — `make lvs`
silently reports "skipped" without one, so the platform cannot be signed off at
all. r2g installs one (`tools/install_nangate45_lvs.sh`). It is a pure checker:
it reads the GDS and the CDL and writes a report, and cannot alter a layout, so
it adds no repair. Both arms use it. **Every nangate45 dual-clean number is
conditional on an r2g-supplied LVS rule, and saying so is part of the number.**

### Why the antenna LEF patch is repair and had to leave the baseline

FreePDK45's stock tech LEF carries **zero** antenna ratio rules and its
`ANTENNA_X1` diode has `ANTENNADIFFAREA 0.0`, which OpenROAD rejects (GRT-0246).
`tools/install_nangate45_antenna.sh` patches both LEFs in place. It adds no
flow step — but ORFS's **default** `repair_antennas` then has a working model
and inserts diodes during routing. Measured on the 2026-10 corpus: **347 of 2497
baseline layouts carry OpenROAD-inserted diodes, 50 of them among the 1661
dual-clean** (a 3.0% upper bound on the inflation). That is repair happening
inside the baseline, so the baseline is re-measured on the stock LEF.

Note the asymmetry that makes this clean: the patch touches only the LEF, while
the deck reads the **GDS**, which is untouched. So the CHECKER was never
contaminated — only the flow was. Of the 75 files in the platform directory, 67
are untouched, three are backups, the deck's content is byte-identical to its
backups, and exactly **two** files differ: `NangateOpenCellLibrary.tech.lef` and
`NangateOpenCellLibrary.macro.mod.lef`.

Antenna checking itself is official here and on by default — the corpus's 6649
`METAL*_ANTENNA` violations are the official deck's own output, not something
r2g added. (Contrast gf180, whose `run_drc.py` leaves `--antenna` off by
default; see that section when it is frozen.)

### Honest limits of this contract

- `FreePDK45.lydrc` is community-maintained (header: `klayoutmatthias/
  FreePDK45_for_KLayout`, GPLv3), not a foundry signoff deck, and nangate45 is
  an academic PDK. "DRC clean" here means clean against that deck.
- The deck's FEOL booleans hang pathologically on some designs (the documented
  `or` stall). Those designs get no verdict and count as not clean. A deck
  variant that skips the FEOL checks was built and measured — 38/38 agreement on
  non-FEOL categories, ~40x faster — and is **deliberately excluded** from the
  contract: it changes the yardstick, and a number produced with it could not be
  compared to one produced without it.

## gf180 — NOT YET FROZEN

Open items: the official entry point is the PDK's own `run_drc.py` + 58
`rule_decks/` (variant **C** = metal_top 9K / mim_option B / metal_level 5LM,
the combination the corpus used), which runs per table and writes one lyrdb per
table. Decisions still to make: whether `--antenna` is on (official default is
OFF), and the per-table budget. `--split_deep` is **measured lossy** — it runs
CO.6/6a/6b in deep mode and reported 0 CO.6a on a design where the official flat
default reported 35 — so it stays off.

An assembled 7884-line monolithic deck was used for the 2026-10 corpus. It is
NOT the official execution, but it is not wrong either: on C00006 it reported
exactly the same 35 CO.6a as the official runner's flat default. Its defect is
execution, not judgment — a monolithic run times out where per-table does not.

## sky130hd — NOT YET FROZEN

Open item: ORFS ships an official `sky130hd.lylvs` (9056 B) while r2g supplies
and uses `sky130hd_r2g.lylvs` (22759 B, 2.5x larger) via `R2G_LVS_RULE`. Unlike
nangate45, here an official deck EXISTS, so substituting it is a change to the
yardstick and must be justified by measuring both on the same designs.
