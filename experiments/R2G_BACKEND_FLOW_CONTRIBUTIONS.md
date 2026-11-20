# R2G's contributions to the backend flow: DRC and LVS across nangate45, gf180 and sky130hd

Status as of 2026-11-16. Numbers come from the frozen-spec dev baseline on memlab216 (1,249 dev designs per
platform), recomputed today with `rerun_tally.py`, `tally_n45_repair.py` and `co6a_tally.py`, unless a row says
otherwise. The running ledger with every measurement is [`R2G_CONTRIBUTIONS.md`](R2G_CONTRIBUTIONS.md); the
per-platform checking definitions are in [`signoff-contract.md`](../r2g-skills/signoff-loop/references/signoff-contract.md);
frozen parameters and their provenance are in [`BASELINE_PARAMETERS.md`](BASELINE_PARAMETERS.md) and
[`apparatus.json`](apparatus.json).

## 1. Summary

R2G's work on the backend flow falls into three layers, and they are credited differently:

| Layer | What it is | Counted as R2G repair credit? |
| --- | --- | --- |
| **Checking** | Making signoff exist, finish, and be trustworthy: rule decks, LVS comparers, execution strategies, verdict parsing. | No. Both baseline and R2G arms use it. |
| **Apparatus** | Platform configuration that upstream ships wrong, applied identically to every design (PDN strategy, tap distance, antenna data, die sizing, frontend file-list hygiene). | No. It is what makes the platform measurable. |
| **Repair** | Recipes that fix a specific failing design after diagnosing its violation. | **Yes.** This is the headline credit. |

DRC and LVS clean designs on the 1,249 dev designs, frozen spec. "Dual-clean" below means DRC clean **and** LVS
clean; timing is reported beside it, never folded in.

| Platform | Frozen baseline (with checking + apparatus) | + R2G repair | Main repair |
| --- | --- | --- | --- |
| nangate45 | 922 | **1,070 (+148)** | deck-driven antenna repair (122 designs) |
| gf180 | 829 | **≈1,133 (+304)** | ban the one std-cell master whose own layout violates CO.6a |
| sky130hd | 1,157 | — | baseline already 93% dual-clean; repair pool is small |

The baseline column already contains R2G's checking and apparatus work. Without it the numbers are not merely
lower, several are undefined. Stock nangate45 has no LVS rule (zero LVS verdicts), the stock nangate45 DRC deck
does not finish on hundreds of designs, and stock gf180's power grid violates GF's own via rules on most layouts.

## 2. Checking: signoff that exists, finishes, and can be trusted

### 2.1 nangate45

| Problem upstream | What R2G does | Measured effect |
| --- | --- | --- |
| ORFS ships **no LVS rule** for nangate45; `make lvs` silently reports "skipped". | Bundles `FreePDK45.lylvs` (`tools/install_nangate45_lvs.sh`). Pure checker: reads GDS + CDL, cannot alter a layout. | Without it: 0 LVS verdicts, hence 0 dual-clean designs on the platform. |
| The stock FreePDK45 DRC deck runs `CONTACT.3` flat over the whole die and does not finish. | `FreePDK45_tiled.lydrc`: local geometry rules tiled; antenna and net-tracing rules kept in deep mode, because tiling a net-tracing check makes it wrong, not just faster. | 167 designs: **11 h with 36 workers → 11 min.** Same layout C00006: stock timed out at 7,278 s, tiled reported its violations in 310 s. |
| KLayout 0.30.7's LVS **comparer crashes** (SIGSEGV) on ~24 dev designs and leaves no verdict. Upgrading KLayout, dropping the cross-reference and raising the search budget all failed. | Netgen fallback: extraction and normalisation stay the platform deck's own; only the comparison moves to Netgen 1.5.272 (the comparer sky130hd/gf180 use). Applied only where KLayout gave no verdict. Never overrides one. | 32 verdicts recovered, all `match`. Validated first: 11/11 KLayout-clean designs also match; every negative control is caught (pin swap, deleted cell, swapped flop D inputs, a real case-folding mismatch). |
| The crash retry sat below the frozen-layout fallback, so a crash inside it reached an unconditional `break`. | Retry moved onto the path the corpus actually uses. | 47 of 127 designs crashed at least once; **11 verdicts changed** (2 rescued to clean, 9 given a real non-clean verdict). |
| A restaged design read cell count 0, so LVS took its smallest timeout (5,400 s instead of up to 28,800 s) and most retries. | Cell count falls back to the DEF `COMPONENTS` count. | Removes a silent misconfiguration on every restaged design. |
| The 67 designs that stall in the deck's last (ANTENNA) block lose the verdict of all ~370 rules that already ran. | Run the deck per switch group (FEOL / BEOL / OFFGRID / ANTENNA), each with its own budget and verdict. A recovered design is `clean_except_antenna`, never `clean`. | Verdict recovery for the non-antenna rules on stalled designs. |

### 2.2 gf180

| Problem | What R2G does | Measured effect |
| --- | --- | --- |
| The 7,884-line monolithic deck assembled for the pilot judges correctly (35 CO.6a on C00006, the same as the official runner) but **times out** on 180 pilot designs. | Signoff through the PDK's **own** `run_drc.py`: 58 rule tables run separately, flat mode, variant C. | Reaches verdicts where the monolithic run produced none, e.g. C03506 (18,290 cells) in 3,891 s. |
| Running the official runner on every design is slow (2,588 s per design, mean of 4 validation designs at 644–4,512 s, `--mp 4`). | **Hybrid DRC**: the fast deck grades everything (median 94 s); the official runner only fills designs the fast deck could not grade (86 on dev). It never downgrades an existing verdict. Every design records which checker judged it. | **Estimated, not measured:** ~75 h for official-on-everything vs ~19 h for the hybrid (pre-launch estimate, 2026-10-17, from designs × 2,588 s ÷ workers; the design count and concurrency behind each figure were not recorded, and neither configuration was timed end to end on the same set). 61 dev verdicts backfilled. Agreement measured on artifacts (rule names + coordinates), not verdicts: same rules and counts on 3 dirty designs, all 54 tables empty on 20 clean ones. |
| The official runner writes one lyrdb **per table**; reading one file reports one table and drops 50+. | Reader aggregates every table's lyrdb. | A run holding 35 CO.6a violations first read as "clean" before this fix. |
| `--split_deep` looks like a speedup. | Measured and kept **off**. | It reported 0 CO.6a where the flat default reported 35 on the same design: lossy. |
| The official default leaves antenna checking **off**. | `--antenna` **on**, so all three platforms check antennas and their numbers sit in one table. | A real reliability rule is measured. It is also the rule R2G repairs. |
| LVS | Netgen with the PDK's own `gf180mcuC_setup.tcl`, driven by R2G's `run_netgen_lvs.sh`; `"Top level cell failed pin matching"` parsed as a failure. | A pin-matching failure can no longer read as "no verdict". |

### 2.3 sky130hd

| Problem | What R2G does | Measured effect |
| --- | --- | --- |
| KLayout LVS on sky130 needs CDL fixes (slashes in names, short resistors). | `run_lvs.sh` applies R2G's slash and short-resistor CDL fixes for sky130 only. | sky130hd KLayout LVS runs at all. |
| A corpus-scale LVS pass was missing. | Full **Netgen** pass with the PDK's own `sky130A_setup.tcl`, smallest designs first (runtime is near-linear: 8 s at 1 cell, 48 s at 6,921). | 1,161 LVS-clean dev designs. Verdict parsing validated with negative controls: an early grep scored two deliberate corruptions as "no verdict". |
| Aggregate JSON files were read as LVS truth. | The verdict of record is each run's own `result.json`. | `netgen_lvs.json` held 1,475 rows for 2,497 designs; reading it undercounted sky130hd by 669 designs. |

### 2.4 All platforms

- **Signoff never regenerates the layout it grades.** `run_lvs.sh` refuses when `make lvs` would rebuild the
  physical stages (RMD-P0-01). The harvest therefore keeps `5_route.odb` + `6_final.odb` and the LVS chain
  (`6_final.cdl`, `6_final_concat.cdl`, `6_lvs.lvsdb`).
- **No verdict is not a pass.** Timeouts, crashes and missing layouts count as not clean, on the same ruler as the
  pilot. Signoff checkers run under a session supervisor (group TERM, grace period, KILL, then a survivor reap), so
  a timed-out checker cannot leave an orphan process grading on.
- **Bounds:** 21,600 s per design for DRC and for LVS, declared as apparatus because a verdict earned only by the
  longer bound is not like-for-like with the pilot's 7,200 s.

## 3. Apparatus: flow configuration upstream ships wrong

These change the flow, uniformly for every design, so they improve the baseline itself and earn no repair credit.

| Platform | Upstream defect | R2G apparatus | Measured effect |
| --- | --- | --- | --- |
| gf180 | ORFS's PDN strategy requests multi-cut via arrays that merge into 0.772 × 1.028 µm rectangles, violating GF's V2.1/V3.1 ("every Via2/Via3 edge exactly 0.26 µm"). | PDN config with exactly one dropped clause (`-split_cuts`). | One design 824 → 0 violations. **82%** of all gf180 violations traced to this. A stock-PDN baseline would be near-zero dual-clean. |
| gf180 | Tapcell `-distance 100`, while DF.13/DF.14_MV cap the distance to a substrate tap at 15 µm for 5 V devices. | `-distance 30`. | Removes a systematic rule violation from every layout. |
| nangate45 | Tech LEF has **zero** antenna ratio rules; the only diode has `ANTENNADIFFAREA 0.0` (OpenROAD rejects it, GRT-0246). Meanwhile the DRC deck checks antennas. | `install_nangate45_antenna.sh` adds 532 + 532 + 387 + 146 antenna attribute lines and edits one value (diode area 0.0 → 0.1). GDS untouched. | Gives nangate45 a DRC with content: 3,526 of 3,528 nangate45 violations are antenna. Without the data, 369 antenna-dirty designs are permanently unfixable. It also makes the baseline **harder**: 184 designs fewer pass than on a deck with no antenna data. |
| all | Utilization-derived cores narrower than one PDN strap set abort floorplan with PDN-0185; ORFS has no handling. | Explicit PDN-feasible square core (32 µm sky130hd/nangate45, 110 µm gf180) applied in the frozen die. | Pilot: 1,137 of 5,181 dual-clean designs exist only because of this, rescue rate near 100%. |
| all | A die sized from cell area alone can be too small for the IO pins (PPL-0024). | Die = max(area term, **pin floor** `pins / (4k)`, PDN floor), with k measured per platform from real PPL-0024 refusals (nangate45 1.80, gf180 0.885, sky130hd 0.90). | PPL-0024 designs: 0 of 39 → 38 of 39 pass when the die is relieved. |
| sky130hd, gf180 | `buffer_ports` adds one buffer per port, which the synthesis area never counted, so small many-port designs overflow (GPL-0301). | Port-buffer area term in the die (10.0096 µm² sky130hd, 42.336 µm² gf180). | Recovers the designs lost to GPL-0301 at 104–113% utilization. |
| all | The driver's config generation broke designs before synthesis: paths with spaces (34), files both listed and `include`d (15), `$readmem` images parsed as HDL (9). | Symlink make-hostile paths, drop a listed file another file includes, drop memory-image suffixes. | 58 designs reach synthesis. Regression gate: 0 of 1,191 passing designs change. |

## 4. Repair: the measured credit

### 4.1 nangate45: `deck_driven_antenna_diode` and antenna ratio over-constraint (+122 dev designs)

OpenROAD's antenna model and the signoff deck disagree by about 200×. On a layout the deck flags 16 times,
`check_antennas` reports 0 violations, so `repair_antennas` has nothing to act on.

R2G attacks this from two sides:
- **Take the violating nets from the deck itself.** They are mapped from the deck's lyrdb back through the DEF,
  and diodes are inserted exactly on those nets. Pilot scale: 314 of 315 dirty designs mapped; 6,275 diodes
  inserted with 0 errors. The 315th maps to no net and is refused rather than guessed.
- **Make the router stricter than the rule.** The router's ratio is set to 10:1 against the grader's 300:1, so
  repair continues until the grader is satisfied with margin. The grader is untouched. On C00315: 208 → 0
  violations, LVS still matching, slack unchanged, +3.4% cell area.

Rejected alternatives were measured, not assumed:
- `ANTENNACUMAREARATIO`: byte-identical layout.
- More repair iterations: byte-identical layout.
- Ratio 50: reduced violations on 5 of 5 designs but cleared none.

Dev result, nangate45 repair arm: **+148 dual-clean**.

| Repair | Designs |
| --- | --- |
| Antenna repair, ratio 10:1, round 1 | 106 |
| Antenna repair, rounds 2–3 | 16 |
| Ban a violating master | 2 |
| Skip incremental repair | 2 |
| Case rename | 1 |
| Skip RCX | 1 |
| **Frontend (synthesis) repairs** | **20** |

The frontend repairs are counted on their own line: SystemVerilog frontend 6, data-file fetch 5, header drop 2,
`aldff` 2, attribute stripping 1, absolute `readmem` path 1, plus 3 combined with antenna.

### 4.2 gf180: `dont_use_violating_master` (+304 dev designs)

gf180's CO.6a is a contact rule. The router never draws a contact, so the violating geometry belongs to a
**standard cell the PDK ships**. R2G attributes each violation to the instance containing it, in that cell's
orientation-normalised frame. It offers the ban only when one master owns at least 80% of the dominant rule's
violations at one cell-frame offset **and** a same-function sibling cell survives the ban. The decision is
geometric, not a rule or platform allowlist.

| Measurement | Value |
| --- | --- |
| Pilot designs attributed to `gf180mcu_fd_sc_mcu9t5v0__aoi221_2` at cell-frame (6.6675, 4.2) | 545 of 583, same master, rule and offset in all |
| Pilot full corpus, 530 subjects | dual-clean **1,465 → 1,956 (+491)**; violations 5,920 → 118 |
| Frozen dev, 313 re-run | **304 dual-clean**; 7 DRC timeouts, 2 still violating |
| Cost | ≤1.3% cells, ≤2.3% area; on md5 timing improved (WNS −0.22 → −0.16) |
| Negative control | 0 of 83 sky130hd and 1 of 364 nangate45 dirty designs qualify. Their violations are router geometry, and the gate declines them. |

One caveat to report with it: in the pilot-scale run, 3 designs went from LVS clean to not clean. The audit target
for that number is 0.

### 4.3 sky130hd

The frozen dev baseline is already 1,157 / 1,249 dual-clean. Only 4 designs have DRC violations; the rest of the
gap is 81 designs with no verdict (flow failures and timeouts) and 7 LVS pin mismatches. The pilot-era repair pool
(70 m3.2 designs, 69 of them on the same die edge, precondition for `pin_side_rebalance`) did not reappear at this
scale under the frozen die sizing. Its effect was never measured, and it is not claimed.

### 4.4 Cross-platform repair machinery

- **PPL-0024 die relief** with a monotone target `max(current, demanded)`. The placer's demand is not monotone:
  across 39 designs the demanded/current ratio spans 0.98–3.70, and on 2 it asks for less perimeter while still
  refusing.
- **`override export` only for platform-assigned keys.** Every platform config is read after the design config.
  A plain design-side `export DONT_USE_CELLS` is therefore silently discarded on gf180 and nangate45, which made
  the master-ban recipe inert until fixed. Two follow-on defects were also fixed: the override dropped the
  platform's own ban list, and the post-apply check rejected the spelling it had just written.

## 5. How the credit is kept honest

- **Layers are declared before the run.** Every checking or apparatus ingredient is in `apparatus.json` with
  `why`, `equivalence` and `fail_if_missing`. Both arms get it, so it cannot inflate the repair gap.
- **Equivalence is shown on artifacts, not verdicts.** Rule names and coordinates are compared, and negative
  controls must fail. Three configurations once "agreed" because all three were failing.
- **dev / held-out split.** The recipes above were developed on the whole corpus before the split existed. They are
  re-validated on dev and may not be reported as held-out results.
- **Two rulers on gf180** are reported separately: the fast deck only (comparable with the pilot), and with the
  official-runner backfill. The backfill can only add verdicts; it is measurement, not repair.

## 6. Fourth platform, in progress: ASAP7 (signoff with Calibre on memlab216)

Not part of the three-platform numbers above. Recorded here because it uses the same three layers.

- **Checking.** The official r1p7 Calibre decks run unchanged.
  - The DRC deck is partitioned into group A (333 checks, minutes) and group B (`ACTIVE.AUX.3`, one slow
    encrypted operation, up to ~45 min). The two run concurrently, with early exit when A finds violations.
  - Verified identical record-for-record to the full deck on GCD, UART and AES-128. AES: 2,441 s full vs 189 s for
    a design with violations.
  - Signoff uses flat mode, because hierarchical mode adds false FIN.S.1 results (83–496 per design).
- **LVS source-netlist fix.** The official transistor library spells `DFFASRHQNx1`'s pins `RESETn`/`SETn` while the
  LEF says `RESETN`/`SETN`. As a result every async set/reset connection was dropped from the source netlist and
  such designs read LVS INCORRECT. The port names are normalised in that one subcircuit; all 212 cells are audited.
  Three false INCORRECTs became CORRECT.
- **Router.** The qualification's pin-access repair build of OpenROAD (same version, 4-line change enforcing
  via-in-pin enclosure at cell side edges) is being adopted for the ASAP7 baseline. With the stock router,
  essentially every non-trivial design carries `V0.M1.AUX.3` / `M1.S.6`.
