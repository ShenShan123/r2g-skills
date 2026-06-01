# Signoff-Fixer Campaign — 2026-06-01 (on-the-fly log)

Validating the new DRC/LVS violation-fixing ability (`fix_signoff.sh` +
`diagnose_signoff_fix.py`) on the corpus, and improving the skill from what the runs reveal.
Policy: **real layout fixes only** (no rule-deck relaxation). Spec/plan:
`docs/superpowers/{specs,plans}/2026-05-31-drc-lvs-fixer*.md`.

## Ability (shipped, branch `all_tech_feat_label_extract`)

| commit | what |
|--------|------|
| `e9166d2` | honest 300:1 nangate45 antenna deck + `tools/install_nangate45_drc.sh` |
| `37439c5`→`26d133e` | `diagnose_signoff_fix.py` (pure plan + CLI), hardened |
| `b51312d`→`d76daed` | `fix_signoff.sh` iterative driver, hardened (run_orfs-fail detect, extract, tab-parse) |
| `42d0e0b` | antenna catalog corrected to real ORFS knobs (2 strategies) |
| `ce35f0a` | docs: `references/signoff-fixing.md`, SKILL.md, failure-patterns |

Antenna catalog (real fixes): **S1 `antenna_diode_iters`** (`MAX_REPAIR_ANTENNAS_ITER_GRT/_DRT=10`,
default 5; rerun route) → **S2 `antenna_density_relief`** (lower `CORE_UTILIZATION` −5; rerun
floorplan). LVS: triage `unknown`, macro-CDL (operator), honest residual on KLayout C++ crash.

## Key sequencing finding (2026-06-01)

After `install_nangate45_drc.sh` flips the deck 400:1→300:1, existing `reports/drc.json` are
**stale** (measured at the old ratio). `fix_signoff.sh` diagnoses the *current* report, so a
re-DRC must refresh the baseline before fixing — otherwise the loop compares a 400:1 "before"
against a 300:1 "after" and mis-fires `no_improvement`. **Convention: run `run_drc.sh` + extract,
THEN `fix_signoff.sh`.** (Candidate skill improvement: a `--recheck-first` flag to force an
initial DRC; deferred pending Phase-1 evidence.)

## Honest-baseline reference (from `/tmp/wave_f1_results.tsv`, the 29 antenna designs)

`pre` = count at honest 300:1; `post` = count at masked 400:1. Under the restored 300:1 deck
the *full* `pre` population reopens as antenna-fail. The 9 that survived even 400:1 (post>0)
are the hard "residual-7" set + the two ethernet designs.

Hard residual set (post>0 @ 400:1): fifo_basic(98→7), cv32e40p stream_register(7→7),
pyocd stream_register(7→7), iccad2017_unit18_F(7→7), iccad2017_unit2_G(7→7),
riscv_alu4b(14→7), microcontroller_cpu(7→7), eth_arb_mux(161→133), eth_demux(231→147).

## Run log

| design | check | baseline (300:1) | after fix | strategy path | verdict | notes |
|--------|-------|------------------|-----------|---------------|---------|-------|
| PicoRV32_…_fifo_basic | drc | 14 (raw 98) | 16 | density_relief→exhausted | **honest residual** | density relief COUNTERPRODUCTIVE (14→16); diode repair inert. Fixer correctly reported residual, exit 2. |

### Density-relief verdict (2026-06-01) — nangate45 antennas have NO viable real fix

Ran the fixed fixer on fifo_basic (honest baseline 14). `antenna_density_relief`
(`CORE_UTILIZATION` 10→5) **increased** antennas to 16 (spreading cells lengthens metal); the
fixer escalated (fix #3 works), found no more strategies, and reported honest residual (status
fail, exit 2). Confirms: with OpenROAD repair inert (Finding B) AND density relief
counterproductive, **no real-layout lever fixes nangate45 KLayout-300:1 antennas.** Decision:
the fixer should classify nangate45 antenna fails as **residual immediately** (residual_reason
documents the root cause), not burn a ~45-min counterproductive re-route. → improvement #4b.
This is the honest answer the "real fixes only" mandate demands — the prior 400:1 masking is
correctly rejected; these are genuine residuals.

The fixer itself is now **validated end-to-end** on real ORFS: diagnose→apply→re-route→re-DRC→
escalate→honest-residual, with honest item counts. ✔

## Phase 0 findings

### Residual antennas root cause (2026-06-01) — STALE NO-DIODE ARTIFACTS

Inspecting `fifo_basic`'s preserved backend (`RUN_2026-05-19`):
- `grt_antennas.log` / `drt_antennas.log` are **empty**; final DEF has **0** `ANTENNA_X1`
  placements; both route logs carry `[WARNING GRT-0246] No diode with LEF class CORE
  ANTENNACELL found.` → **OpenROAD inserted zero diodes**; antenna "repair" was a near-no-op,
  leaving the residual antennas the corpus shows.
- BUT a headless probe of the *current* install (`read_lef` tech + `macro.mod.lef`) finds
  `ANTENNA_X1 type=CORE_ANTENNACELL` (1 master). The diode-class LEF (`.macro.mod.lef`,
  `CLASS CORE ANTENNACELL`) is **stock ORFS** (in ORFS git, not a local mod) and is the
  configured `SC_LEF`.
- Conclusion: the 2026-05-19 corpus runs predate an OpenROAD rebuild that now recognizes the
  antenna diode. **The residual antennas are stale; a fresh re-route should actually insert
  diodes and clear them.** The fixer's value is to trigger that fresh, diode-enabled re-route
  (S1 also raises repair iters). To be confirmed empirically on `fifo_basic` next.
- **Skill implication:** the dominant nangate45 antenna "fail" population is an artifact of
  old no-diode runs, not unfixable layout. The real fix is a fresh diode-enabled route —
  which is precisely what `fix_signoff.sh` does. (No rule relaxation needed or used.)

### LVS "unknown" population (52) characterized (2026-06-01)

Sampled all 52: every one has `6_lvs.log` + `lvs_run.log` but **no `6_lvs.lvsdb`** and no
match/mismatch verdict → `extract_lvs.py` returns `unknown`.
- **6 are hard KLayout crashes** (signal 11 / SIGSEGV / Ruby-interpreter backtrace +
  `~/.klayout/klayout_crash.log`), e.g. `fifo_basic`, `verilog_axi_axi_fifo_wr`,
  `wb2axip_aximwr2wbsp`, `secworks_sha256_…_axi4_slave`. Not fixable without KLayout upgrade.
- **46 "other"** extract devices OK then die at `FreePDK45.lylvs:246` (netlist build) with no
  verdict — killed/crashed mid-LVS (no signal string captured).

**Skill-improvement finding (to apply):** `extract_lvs.py` classifies crashers as `unknown`,
so the diagnoser emits `lvs_resolve_unknown` and would **re-run a job that just re-crashes**.
Fix: detect the crash signature (signal 11 / `klayout_crash.log` / ruby backtrace, and the
"extracted-but-no-verdict/no-lvsdb" pattern) and classify as `klayout_cpp_crash` residual so
the fixer does NOT waste an expensive re-run. → tracked as improvement #1.

### fifo_basic fixer smoke test (2026-06-01) — THREE findings

Ran `fix_signoff.sh fifo_basic --check drc` end-to-end (full re-route + DRC, 28 min). The
driver worked (applied S1, re-routed, re-DRC'd, compared, early-exited) but surfaced 3 bugs/
truths. Final: 98→98 "no_improvement", residual.

**Finding A — DRC count inflated ~7×.** `6_drc_count.rpt` (ORFS `grep -c "<value>"`) = 98, but
the true KLayout `<item>` count = **14** (METAL4=3, METAL5=3, METAL6=8). `extract_drc.py`
carries the inflated value as `total_violations`. So fifo_basic really has **14** antenna
violations at 300:1, not 98; the whole corpus's antenna counts are ~7× inflated. → fix #2.

**Finding B — the nangate45 antenna repair flow is fundamentally inert (root cause).**
On the fresh routed ODB: OpenROAD `check_antennas` reports **0 net / 0 pin violations**;
`repair_antennas ANTENNA_X1` → `ERROR GRT-0244: Diode ANTENNA_X1/A ANTENNADIFFAREA is zero`;
0 `ANTENNA_X1` placed; `GRT-0246 No diode found` recurs. Confirmed in the LEF:
`ANTENNA_X1` pin A has `ANTENNADIFFAREA 0.0` (`# unknown`), and the nangate45 **tech LEF has
no antenna rules** (`grep -ci ANTENNA` = 0). So:
  - OpenROAD detects 0 antenna violations (no rules) → `repair_antennas` inserts nothing even
    in principle;
  - the only diode cell has zero diffusion area → unusable even if violations were detected.
  → **`repair_antennas` (strategy S1) can NEVER fix nangate45 antennas.** These violations are
  visible only to KLayout's `FreePDK45.lydrc` (300:1). The 2026-05-30 400:1 relaxation was
  effectively dragging KLayout toward OpenROAD's "0 antennas" view (masking). Under real-fixes-
  only, the tractable lever is **layout relief** (density/area/reroute) — strategy S2 — or an
  honest residual. (Enabling OpenROAD antenna repair would require inventing tech-LEF antenna
  rules + a non-zero-diffarea diode — i.e. fabricating data — which is out of "real fixes"
  scope.) → drives strategy reorder + scope note.

**Finding C — early-exit abandoned escalation.** The driver stopped after S1's no_improvement
and never tried S2 (density relief). `no_improvement` should advance to the NEXT strategy, not
abandon the check. → fix #3.

### Stuck-DRC probe (2026-06-01) — TRACTABLE via BEOL-only DRC

Probed the 271 stuck designs. `stuck_at_rule` distribution: `FreePDK45.lydrc:131` (137),
`:91` (105), `:121` (26) — all in the **FEOL** (front-end-of-line) section (Well/Poly/Active
boolean `or`/`and`/`not` ops). Designs are 14K–30K+ instances. The deck is parameterized:
lines 35–36 are `FEOL = true` / `BEOL = true` toggles.

**Insight:** FEOL checks validate the *internal* geometry of standard cells, which come from a
pre-characterized, DRC-clean library (NangateOpenCellLibrary) and are NOT modified by P&R —
only the BEOL metal/via/antenna routing varies per design. So FEOL DRC on a placed design is
largely redundant re-checking of clean library cells, and it is exactly those FEOL boolean ops
that hang KLayout.

**Decision (scope):** Add a **BEOL-only DRC mode** (`FEOL=false`) as a fallback for designs that
hang on FEOL. It's tractable (a deck flag, not a rewrite), defensible (library cells are
vendor-verified), and would unblock all 271 stuck designs' routing-DRC signoff. Must be
labelled honestly in results (`drc_mode: beol_only`, "FEOL skipped — library cells
pre-verified"), NOT reported as full "clean". → improvement #6. Empirical validation (run a
stuck design with FEOL=false, confirm completion + BEOL count) deferred until the fifo_basic
fixer run releases the DRC machinery.

## Skill improvements identified (from runs)

1. **extract_lvs.py crash classification** — DONE (commit `4d15d76`). Validated on real data;
   corpus LVS reclassified: **crash=6, incomplete=43, unknown=3** (was 52 `unknown`), plus
   fail=10/failed=1, clean=603, clean_algorithmic=7. The 49 crash/incomplete are KLayout-
   instability residuals (no `6_lvs.lvsdb`, no verdict) — now correctly residual instead of
   triggering doomed re-runs. The honest LVS-clean rate is 610/673 (≈91%); the ~7%
   crash/incomplete need a KLayout ≥0.30.10 upgrade, not a flow fix.
2. **extract_drc.py true count** — `total_violations` should be the KLayout `<item>` count
   (sum of category counts), not ORFS's inflated `grep -c "<value>"` count.rpt (~7× over). Keep
   the raw marker count as a separate field. Corpus-wide honesty fix. [applying]
3. **fix_signoff.sh escalate** — on `no_improvement`, advance to the NEXT strategy instead of
   abandoning the check; terminate only on clean / STOP (strategies exhausted) / max-iters /
   run_orfs-fail. [applying]
4. **nangate45 antenna catalog = density relief only** — `repair_antennas` (diode_iters) is
   inert on nangate45 (Finding B: no tech-LEF antenna rules + zero-diffarea ANTENNA_X1). Skip
   it so the fixer doesn't waste a ~28-min re-route; go straight to the tractable layout lever.
   Keep diode_iters for platforms with a working diode. [applying]
5. **Document nangate45 antenna reality** in failure-patterns + signoff-fixing (Finding B).
   [pending docs]
6. **BEOL-only DRC mode** (`FEOL=false`) — fallback for the 271 designs that hang on FEOL
   boolean ops; completes routing-DRC signoff, labelled `drc_mode: beol_only`. **DONE +
   empirically validated** (see Phase 1 below): commits `b8d6` (mode), `56a1` (also disable
   ANTENNA). Validated on real ORFS — DMA_Controller (hung ~4h at FEOL :131) now completes
   in **7.7s**, ip_demux in **34s**, both `clean_beol`.
7. **`clean_beol` qualified status** (commit `76c81b9`) — a 0-violation BEOL-only run was
   being emitted as plain `clean` by `extract_drc.py`, which status-based aggregation /
   dashboard would silently miscount as a *full* clean (inflating the clean-rate). BEOL-only
   skips BOTH FEOL and ANTENNA, so it only proves metal/via/cut routing is clean. Now emit
   the qualified status **`clean_beol`** (mirrors LVS `clean_algorithmic`);
   `diagnose_signoff_fix.py` treats it as needing no fix. Test added; full suite 265 pass.
   **Superseded invariant:** "BEOL-only 0-viol ⇒ status `clean`" → now `clean_beol`. [DONE]

## Phase 1 (stuck-DRC set via BEOL-only) — IN PROGRESS

**Goal:** convert the 271 FEOL-hang `stuck` designs to an honest routing-DRC verdict via
BEOL-only mode. Validation wave (size-ordered), real ORFS, honest 300:1 deck minus FEOL+ANTENNA:

| design | inst | full-deck result | BEOL-only result | wall | verdict |
|--------|------|------------------|------------------|------|---------|
| DMA_Controller_DMA_registers | 700 | stuck ~4h @ FEOL :131 | **0 viol** | 7.7s | `clean_beol` ✔ |
| verilog_ethernet_ip_demux | 2,979 | stuck @ :131 | **0 viol** | 33.6s | `clean_beol` ✔ |
| verilog_ethernet_eth_mac_1g_fifo | 469,520 | stuck @ FEOL | **hung @ BEOL CONTACT** | killed @ ~15min | `stuck` (honest) |
| koios_gemm_layer | 978,362 | stuck @ FEOL | **hung @ BEOL CONTACT** | killed @ ~14min | `stuck` (honest) |

**Finding (large-design BEOL CONTACT hang — confirmed):** BEOL-only is near-instant for
small/medium stuck designs (seconds), but for **≥~470K-instance** designs the hang simply
**migrates from the FEOL booleans to the BEOL CONTACT-layer ops** (`CONTACT.1` `cont.width` /
`CONTACT.2` `cont.space`, deck line ~143–144). Both large designs advanced only 1–2 deck lines
into the BEOL section, then froze for 5–8 min at 100% CPU (RSS climbing to 7.3GB) — the same
KLayout polygon-op-no-progress mode, now on the millions of contact polygons. Killed (per the
campaign's anti-zombie rule); their `reports/drc.json` stays honest `stuck`.
- **Root insight:** the `cont` (contact) layer, like FEOL, is **library-internal** geometry —
  P&R adds only routing *vias* (VIA1+), never new poly/active→M1 contacts inside cells. So
  `CONTACT.*` on a placed design re-checks pre-verified library cells, exactly like FEOL.
- **Implication / candidate improvement #8:** a deeper fallback could also skip the `CONTACT.*`
  rules (same library-pre-verified justification as FEOL), leaving VIA/METAL/OFFGRID. That is
  rule-line surgery (CONTACT isn't a top-level deck toggle), so it's deferred pending evidence
  it's worth it. For now: BEOL-only fully unblocks the small/medium stuck majority cheaply; the
  large tail (incl. BOOM 5–9M-inst) remains `stuck` and needs the deeper fallback or a very
  long timeout. A population batch must also bound parallelism by memory (~7GB/large design).

## Phase 2 (large_rtl_designs) — pending

`large_rtl_designs/` = BOOM CPU (boom_mediumboom 9.1M, boom_mediumseboom 8.3M, boom_smallseboom
5.5M — all in the stuck-DRC set), Faraday ASIC (faraday_risc 406K, also stuck), Gaisler. These
map onto the Phase-1 large-design BEOL-cost finding above.
