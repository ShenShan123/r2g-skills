# Signoff Fixing (DRC / LVS)

Automated, iterative loop that applies **real layout fixes** for DRC and LVS violations
after the main backend run. Real-fixes-only means the fix loop modifies routing, diode
insertion, or placement density — it **never** relaxes the DRC rule deck.

The honest 300:1 antenna-ratio rule deck for nangate45 is the reference. Install it once
with `tools/install_nangate45_drc.sh`. The 400:1 ratio relaxation used in an earlier
campaign is **retired**; that deck is no longer in use.

**nangate45 antenna repair requires a one-time model install.** The stock nangate45 LEFs
ship no antenna model (no tech-LEF ratios, gate areas stripped from the SC LEF, a
zero-`ANTENNADIFFAREA` diode), so OpenROAD's `repair_antennas` is a no-op out of the box.
Install the model once with `tools/install_nangate45_antenna.sh` (reversible; idempotent).
It adds `ANTENNAAREARATIO 300` per routing layer (**matching**, not relaxing, the signoff
deck), merges per-pin gate areas from `.macro.lef`, and gives the diode a usable
`ANTENNADIFFAREA`. The KLayout signoff deck is never touched. See
`failure-patterns.md` "Antenna DRC Violations" for the full root-cause.

---

## Scripts

### `scripts/reports/diagnose_signoff_fix.py` — pure diagnoser

```
diagnose_signoff_fix.py <project-dir> --check drc|lvs [--apply <strategy-id>]
                        [--next] [--exclude id1,id2]
```

- **Default (no flags):** prints the full fix-plan JSON to stdout.
- **`--apply <id>`:** writes the named strategy's `config_edits` into an idempotent
  marked block in `constraints/config.mk` delimited by:
  ```
  # >>> r2g signoff-fix (auto) >>>
  # <<< r2g signoff-fix (auto) <<<
  ```
  Re-applying the same (or a new) strategy replaces the block — never duplicates lines.
  Exit 2 = unknown strategy id. Exit 3 = operator-only strategy (no auto-apply allowed).
- **`--next`:** prints one tab-separated action line consumed by `fix_signoff.sh`:
  `<id>\t<rerun_from>\t<recheck>`, or `STOP\t<status>\t<reason>` when no auto strategy
  remains.
- **`--exclude id1,id2`:** skip listed strategy ids (already-tried, in the driver loop).

### `scripts/flow/fix_signoff.sh` — iterative driver

```
fix_signoff.sh <project-dir> [platform] [--check drc|lvs|both] [--max-iters N] [--resume]
```

Default: `platform=nangate45`, `--check both`. The iteration budget is **adaptive**: base
3 iterations, hard cap 8, with early-stop after 2 consecutive non-improving iterations past
the base (`--max-iters N` overrides the cap).

**Loop per check (drc / lvs):**

1. Read current violation count from `reports/{drc,lvs}.json` (re-extracts if missing).
2. Call `diagnose_signoff_fix.py --next` to get the next auto strategy.
3. Call `--apply <id>` to write `config_edits` into the marked block in `config.mk`.
4. Re-run the flow:
   - Without `--resume`: `run_orfs.sh <project-dir> <platform>` (full run from scratch).
   - With `--resume`: `FROM_STAGE=<rerun_from> run_orfs.sh <project-dir> <platform>`.
5. Re-run `run_drc.sh` or `run_lvs.sh` and re-extract results.
6. Compare before/after violation count.

**Early-exit conditions:**

- Violation count reaches 0 (verdict flips to `cleared`).
- 2 consecutive non-improving iterations past the base budget (adaptive early-stop).
- `diagnose_signoff_fix.py --next` returns `STOP` (residual — no auto strategy left).
- `run_orfs.sh` fails (rc ≠ 0) — aborts that check, does NOT re-read a stale report.
- The hard cap (8, or `--max-iters`) is reached.

**Outputs:**

| File | Content |
|------|---------|
| `<project>/reports/fix_log.jsonl` | One **session-keyed, lossless** JSON line per iteration: `fix_session_id`, `check`, `iter`, `strategy`, `from_stage`, before/after counts, the pre-fix `violation_class` + `before_categories` snapshot, `after_status`, `cumulative_config`, `verdict`, `ts`. Flushed on-the-fly (not buffered to end). This is the Tier-1 system of record; step-10 ingest reads it into `fix_events`. |
| `<project>/reports/fix_summary.md` | Markdown table of all iterations, written once at end. |

**Verdict vocabulary.** The canonical per-iteration verdict is one of
`cleared | win | no_change | regression | inconclusive`. Two producers feed the log:
`fix_signoff.sh` emits legacy strings (`applied`, `no_improvement`, `stop_*`, `apply_failed`,
`rerun_failed_*`); `check_timing.py --journal` emits the **canonical** strings directly
(`cleared`/`win`/`no_change`). The **ingester** (`_normalize_verdict`) maps both: legacy strings
to canonical, and canonical strings pass through **idempotently**. (Regression note 2026-06-06:
canonical `win`/`no_change` were previously falling through to `inconclusive`, silently dropping
the learning signal from timing-journal episodes; fixed so a timing `period_relax` win is kept.)

**Exit codes:** 0 = final status clean; 2 = residual violations remain.

---

## Fix-plan JSON schema

```json
{
  "check": "drc|lvs",
  "status": "fail|residual|clean|clean_beol|skipped|stuck|timeout|unknown",
  "violation_count": 42,
  "dominant_category": "METAL4_ANTENNA",
  "strategies": [
    {
      "id": "antenna_diode_iters",
      "rationale": "...",
      "config_edits": {"MAX_REPAIR_ANTENNAS_ITER_GRT": "10", "MAX_REPAIR_ANTENNAS_ITER_DRT": "10"},
      "rerun_from": "route",
      "recheck": "drc",
      "auto_apply": true,
      "operator_note": "(optional, operator-only strategies only)"
    }
  ],
  "residual_reason": null
}
```

`strategies` is an ordered list — apply from front to back. Already-applied strategies
(all `config_edits` match the current `config.mk`) are filtered out before delivery.

---

## Strategy catalog (v1)

### DRC — antenna violations only

Strategies are `auto_apply: true`, applied in order; already-applied entries are skipped;
when all are exhausted `status` becomes `residual`. **The catalog is platform-aware** —
`nangate45` (in `DIODE_FORCED_REPAIR_PLATFORMS`) gets the single `antenna_diode_repair`
strategy; every other platform gets the classic `antenna_diode_iters` → `antenna_density_relief`
pair.

| id | platforms | config_edits | rerun_from | Effect |
|----|-----------|-------------|------------|--------|
| `antenna_diode_repair` | nangate45 | `SKIP_ANTENNA_REPAIR=1`, `MAX_REPAIR_ANTENNAS_ITER_DRT=10` | `route` | **Forces physical diode insertion** (the only repair the FreePDK45 deck credits). `SKIP_ANTENNA_REPAIR=1` disables OpenROAD's global-route *jumper* repair — jumpers satisfy OpenROAD's PAR but the deck still flags (it sums the whole net's per-layer metal and credits only diodes). The DRT repair loop then inserts `ANTENNA_X1` diodes. **Requires `tools/install_nangate45_antenna.sh` (one-time).** |
| `antenna_diode_iters` | non-nangate45 (working diode) | `MAX_REPAIR_ANTENNAS_ITER_GRT=10`, `MAX_REPAIR_ANTENNAS_ITER_DRT=10` | `route` | Raises OpenROAD repair-antennas iterations (default 5) so more diodes are inserted. Diode auto-discovered from its `CLASS CORE ANTENNACELL` LEF declaration; do NOT set `CORE_ANTENNACELL` (not an ORFS env var). |
| `antenna_density_relief` | non-nangate45 | `CORE_UTILIZATION` lowered by 5 (floor 5) | `floorplan` | Reduces placement density / grows area so the router can break long metal runs. `PLACE_DENSITY_LB_ADDON` is **never** touched (hard rule: never below 0.10). **Not offered on nangate45** — empirically counterproductive there (enlarging the die lengthens nets → more antennas; fifo_basic 14→16 at util 10→5). |

**Why nangate45 needs `antenna_diode_repair` specifically (verified 2026-06-02, supersedes the
2026-06-01 "inert/residual" Finding B):** with the antenna model installed, OpenROAD's per-net
PAR matches KLayout exactly (stream_register 488.80 vs 489.17), but its *default* repair uses
jumpers, which the FreePDK45 deck does not credit. Disabling jumper repair forces diode
insertion, which both engines credit → clean. If the model is **not** installed, the re-route
won't repair and the loop honestly reports no-improvement → residual (reason points at the
installer). The deck is never relaxed.

Non-antenna DRC categories are **not** handled in v1 — reported as residual.

### LVS

| id | auto_apply | Effect |
|----|-----------|--------|
| `lvs_resolve_unknown` | yes | Re-extract to resolve an ambiguous `unknown` status before attempting any fix. `config_edits` is empty (no config change). |
| `lvs_macro_cdl` | **no** (operator-only) | Macro design: generate a combined CDL (std-cells + fakeram stubs) and add `override export CDL_FILE = <combined.cdl>` to `config.mk`. See `failure-patterns.md` "LVS CDL_FILE Override". |

---

## Residual taxonomy (NOT fixable in v1)

These are reported honestly by `diagnose_signoff_fix.py` with a non-null `residual_reason`.
`fix_signoff.sh` stops and exits 2.

| Condition | `residual_reason` | What to do |
|-----------|-------------------|-----------|
| DRC stuck or timeout | `drc_stuck_tooling_out_of_v1_scope` / `drc_timeout_tooling_out_of_v1_scope` | KLayout polygon-op hang, outside v1 scope. Accept GDS+LVS+RCX pass as evidence. |
| Non-antenna DRC class | `non-antenna DRC class not handled in v1: ...` | Operator review of the specific category. |
| All antenna strategies exhausted | `antenna: all real-fix strategies exhausted` | No further config lever available; consider manual routing intervention or structural RTL change. |
| LVS KLayout C++ crash (`sort_circuit` / `gen_log_entry` SIGSEGV) | `klayout_cpp_crash` | **RETRY — no longer a hard residual (2026-06-03).** A non-deterministic heap heisenbug in KLayout-0.30.7's comparer; a surviving run gives the true verdict (clean OR fail). `run_lvs.sh` retries automatically (`LVS_CRASH_RETRIES`, default 4; auto-1 for >150K cells). Validated: fifo_basic/verilog_axi_axi_fifo_wr→clean; aximwr2wbsp/core_usb_host_top/sha256_axi4_slave→fail/symmetric. `threads(1)`/`verbose(false)`/tcmalloc don't fix it; `flat` dodges the crash but yields garbage mismatches. Only a crash-free run (`grep -a "Signal number" 6_lvs.log` empty) is trustworthy; ≥0.30.10 fixes the source but no such build is on this host. See `failure-patterns.md` "LVS KLayout sort_circuit/gen_log_entry SIGSEGV". |
| LVS symmetric-matcher residual (`Netlists don't match` with **balanced** schematic-only==layout-only unmatched nets, 0 paired-net deltas, 0 device deltas, plus instance swaps / *ambiguous group* warnings) | `lvs_symmetric_matcher_residual` | **No automated flow fix; layout is correct.** KLayout-0.30.7 limit on symmetric logic (parallel NAND/XOR trees, register files / memory arrays, replicated bit-slices, flat combinational benchmarks). Comparer budget does **not** help (validated). **Operator escape hatch (validated 2026-06-03):** strict `same_nets!` seeding on swapped-instance input nets — clears it on localized symmetry (rx_64), does NOT generalize (unit5_G). See "Symmetric-matcher seeding" below + `failure-patterns.md`. |
| LVS real connectivity error (a port/signal net genuinely unmatched, "not matching any net", **imbalanced** unmatched-net counts, or a paired `net(N M mismatch)`) | `lvs_real_connectivity_mismatch` | A genuine layout defect — inspect the GDS/DEF at the named net. Not auto-fixable. Current corpus: wb2axip_axi2axilite (1 net open), wb2axip_axilsingle (16 bus opens — was mislabeled `clean_algorithmic`). |
| LVS CDL parse error (`Pin count mismatch ... Netlist::read`, no verdict) | `cdl_parse_error` (status `unknown`) | KLayout's SPICE reader mis-tokenized an escaped-bracket/negative-index instance name (e.g. `\[-1\]$_DFFE_`). Sanitise the CDL name or avoid the `[-1]` bit-blast. Not a layout defect. Reproducer: spi_master_single_cs. |
| LVS rule-deck mismatch (non-macro, none of the above) | `lvs mismatch with no auto-fix in v1; ...` | Operator review of the `.lylvs` rule deck. |

**LVS comparer budget knobs (do NOT chase symmetric residuals with these).** `FreePDK45.lylvs`
exposes `max_branch_complexity` / `max_depth` via `LVS_MAX_BRANCH_COMPLEXITY` / `LVS_MAX_DEPTH`
env vars (defaults 65536 / 16). Raising them removes the "Maximum depth exhausted" *warning* but
does **not** resolve the actual mismatches — empirically validated 2026-06-02 on
`verilog_ethernet_axis_baser_rx_64` (depth 32: 2 swaps unchanged) and `iccad2017_unit5_F`
(depth 64 / complexity 1M: 292 net mismatches unchanged). The knobs exist only so an operator can
experiment on a genuinely depth-limited *future* design; they are not a lever for the residuals in
this corpus.

---

## Symmetric-matcher seeding (operator-only, validated 2026-06-03)

A KLayout-0.30.7 `symmetric_matcher` residual (layout correct, comparer can't prove it) can be
forced to a true `match` by seeding strict `same_nets!` constraints on the swapped instances'
**input-pin** nets. This is **operator-only and opportunistic** — it is NOT wired into the auto fix
loop because it does not generalize and a bad seed can amplify the mismatch.

**Validated:** `verilog_ethernet_axis_baser_rx_64` (2 NAND2 swaps) → "SYMSEED applied 4 same_nets!
constraints" → "CONGRATULATIONS! Netlists match." It clears **localized** symmetry (isolated
XOR/parity gate pairs); it does **not** clear deep/global symmetry (`iccad2017_unit5_G`: every seed
strategy left it equal or worse).

**Hard rules (learned empirically):**
- Use `same_nets!` (strict) — soft `same_nets` is a no-op the matcher overrides.
- Seed the swapped instances' **input** nets only — seeding the gate's own **output** net
  over-constrains and re-fails.
- Layout internal nets are mostly anonymous (~4% named); address them as net objects via
  `expanded_name`, not `net_by_name`.
- **Gate the result:** accept the seeded verdict ONLY if the re-run is genuinely clean; otherwise
  keep the honest `lvs_symmetric_matcher_residual`.

**Mechanism (two-pass):** pass-1 = a normal failing LVS producing `6_lvs.lvsdb`; pass-2 runs the
seeding-enabled deck against it. The deck reads the swapped-instance ids + matched-net xref from the
prior lvsdb and emits `same_nets!` automatically — no per-design hand-listing.

```bash
# Inputs: GDS + concat CDL (platform std-cell CDL + design 6_final.cdl) + a prior FAILING lvsdb.
cat $PLATFORM_DIR/cdl/NangateOpenCellLibrary.cdl <proj>/lvs/6_final.cdl > /tmp/concat.cdl
klayout -b \
  -rd in_gds=<results>/6_final.gds \
  -rd cdl_file=/tmp/concat.cdl \
  -rd report_file=/tmp/seeded.lvsdb \
  -rd lvs_prior_db=<proj>/lvs/6_lvs.lvsdb \
  -r r2g-rtl2gds/assets/platforms/nangate45/lvs/FreePDK45_symseed.lvs
# Accept ONLY if the log shows "CONGRATULATIONS! Netlists match."
```

`FreePDK45_symseed.lvs` is the plain-DSL form of `FreePDK45.lylvs` + the `SYMMETRIC-MATCHER SEEDING`
block; its device-extraction body is identical to the bundled deck (re-sync if that changes).

---

## Fix-Learning Loop

Every fix iteration is captured losslessly and distilled into evidence that re-ranks the
strategy catalog on the next similar violation. The data flows through **three tiers**
(detailed schema in `knowledge/README.md`):

| Tier | Store | Granularity | Archival |
|------|-------|-------------|----------|
| **1 — `fix_events`** | `knowledge.sqlite` (append-only) | raw, one row per iteration (the lossless system of record) | archivable past a size threshold into the sidecar `knowledge/fix_events_archive.sqlite` |
| **2 — `fix_trajectories`** | `knowledge.sqlite` (materialized, idempotent rebuild) | per-episode path: `resolved`/`abandoned`, `winning_strategy`, `failed_strategies` | **never archived** — derived from Tier-1, so raw archival loses no learning signal |
| **3 — `fix_recipes`** | `heuristics.json` sub-key | per-(family, platform) aggregate per check/violation_class: strategy attempts/successes/failures (+ `median_reduction_pct`), `n_sessions` | folded by `learn_heuristics.py` |

**Recording** is done by `fix_signoff.sh` and `check_timing.py --journal` → `reports/fix_log.jsonl`
(see Outputs above). **Step-10 ingest** (`knowledge/ingest_run.py`) reads `fix_log.jsonl` into
Tier-1 `fix_events`, writes a `run_violations` snapshot for **every** run (clean or not), and
auto-runs `fix_log_manager.manage()` (toggle `R2G_FIX_AUTOLEARN`, default on; failures warn,
never break the ingest). **Learning** is `learn_heuristics.py`: it rebuilds Tier-2 then Tier-3
in one idempotent pass.

**Survivorship — failures count.** Abandoned episodes are folded in (so `n_sessions` includes
failures) and the strategies that *didn't* clear are recorded. Negative evidence down-ranks a
losing strategy; it is never blacklisted.

**Correctness note.** `fix_recipes` derive from Tier-2 `fix_trajectories`, **not** from raw
`fix_events`. That is precisely why archiving raw `fix_events` past the size threshold loses no
learning signal — the distilled trajectory survives. The Tier-2 rebuild is **archive-aware**:
it `ATTACH`es `fix_events_archive.sqlite` (when present) and rebuilds from the union of hot +
archived rows, so an episode's trajectory is never destroyed after its raw rows are evicted.

**Correctness invariants** (enforced after the 2026-06-07 review — see the Implementation Log in
`docs/superpowers/plans/2026-06-05-fix-learning-loop.md`):

- **One trajectory per `(fix_session_id, check_type)`.** A default `fix_signoff.sh <proj>` run
  (`--check both`) shares one session id across its DRC and LVS passes; Tier-2 grouping keys on
  `(session, check_type)` (and the `fix_trajectories` PK is composite) so LVS strategies are
  never mis-filed under a DRC `violation_class` (and vice-versa).
- **One family namespace for writers *and* readers.** `design_family` =
  `_explicit_family(DESIGN_NAME)` else `infer_family(<project-dir basename>)`. Live ingest
  (`ingest_run._project_family`), `backfill_fix_events`, and the recipe reader
  (`diagnose_signoff_fix._load_recipes`) all use this identical rule, so backfilled and live
  evidence aggregate together. Backfill also resolves each record's **platform** from the design
  dir's `config.mk` (not a blanket default), so its recipes land in the live platform bucket.
- **`win` earns partial credit.** A real partial improvement (`win`) is counted separately and
  scored at half a success, so a strategy that reliably *improves* outranks an untried one
  without being credited a full clearance (see the score formula below).
- **Projections attribute per strategy.** `build_lineage_view.fix_effectiveness` and
  `mine_rules.fix_candidates` tally resolved/abandoned **per strategy from `path_json`** (not by
  the episode's single `winning_strategy`, which is null for abandoned episodes), so clearance
  rates are honest and no phantom `strategy=null` bucket appears.

### Ranked-candidate fall-through

When a Tier-3 recipe exists for the design's family/platform/violation_class,
`diagnose_signoff_fix.py` reorders the strategy list via `scripts/reports/fix_model.py` — a
**Beta(1,1)-smoothed clearance score** `(successes + 0.5·wins + 1) / (attempts + 2)`:

- Untried strategies get the neutral `0.5` prior.
- Proven winners rank high; proven losers are down-ranked but **never zeroed or blacklisted**.

There is **no hard gate** — *all* real-fix strategies are always proposed, priority-ordered, so
the loop falls through to the next-best candidate if the top one fails. Inspect the full
evidence-ranked candidate set with:

```bash
python3 r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py design_cases/my_design \
  --check drc --list | python3 -m json.tool
```

Hard safety clamps are unchanged and absolute: ranking only reorders existing real-fix
strategies, and no strategy ever edits `PLACE_DENSITY_LB_ADDON`.

### Other consumers

- `knowledge/analyze_execution.py::rank_proposals(ids, family=, platform=, stage=, heuristics_path=)`
  ranks backend-stage proposals by `fix_recipes["orfs"][stage]`.
- `build_lineage_view.py` adds a read-only **fix_effectiveness** projection (per
  family/platform/check/violation_class strategy resolved/abandoned + clearance_rate) to the
  dashboard.
- `eval_heuristics.py summarize-fix --db <db>` A/B-scores ranked-vs-static fix ordering on
  iters-to-resolve.

### failure-patterns.md stays human-curated

`mine_rules.py` emits a `fix_candidates` key into `failure_candidates.json` (≥3 resolved
episodes per family/check/violation_class/winning_strategy) as a **human-review queue**.
`failure-patterns.md` is **never auto-written** — an operator promotes a candidate by hand,
exactly as for the existing `failure_candidates` review queue.

### Backfill & repair (one-time / maintenance)

- `knowledge/backfill_fix_events.py --batch-dir design_cases/_batch --db <db>` mines historical
  batch logs (`antenna_fix_*`/`beol_drc_*` → `check=drc`; `retry_pass*`/`recover_pass*`/`orfs_retry`
  → `check=orfs`, `violation_class` from the stage) into synthetic `fix_events` tagged with
  provenance `backfill:<filestem>` (idempotent).
- `knowledge/repair_run_status.py --db <db>` reconciles `orfs_status` from per-project backend
  stage logs (backs up the DB to `<db>.bak` first; idempotent). On the current corpus it is
  largely a no-op — stage logs store integer exit codes and `is_success` already credits
  signoff-positive partials.

The knowledge store (`knowledge.sqlite` + `heuristics.json`, plus `fix_events_archive.sqlite` once
created) is tracked in git, so the skill ships **pre-trained** with this experience.

## Real-fixes-only policy

The fix loop applies only genuine layout changes:

- **nangate45 antenna:** force physical diode insertion (`SKIP_ANTENNA_REPAIR=1` +
  `MAX_REPAIR_ANTENNAS_ITER_DRT=10`) after installing the antenna model
  (`tools/install_nangate45_antenna.sh`). A real `ANTENNA_X1` diode is added to the layout.
- More antenna diode insertion on other platforms (raise ORFS
  `MAX_REPAIR_ANTENNAS_ITER_GRT`/`_DRT`, default 5 → 10; the diode is auto-discovered from the
  LEF, so `CORE_ANTENNACELL` is **not** set — it is a no-op env var ORFS does not read)
- Placement density/utilization reduction (`CORE_UTILIZATION`, non-nangate45 only)
- LVS macro CDL (operator-provided combined CDL)

It **never** relaxes the DRC rule deck. The 400:1 antenna-ratio variant of
`FreePDK45.lydrc` (used in the 2026-05-30 campaign wave) is retired. Re-install the
honest 300:1 deck with `tools/install_nangate45_drc.sh`.

---

## Quick start

```bash
# One-shot: attempt all real fixes for both DRC and LVS, up to 3 iterations each
bash r2g-rtl2gds/scripts/flow/fix_signoff.sh design_cases/my_design nangate45

# DRC only, up to 5 iterations, resuming from the stage named in each strategy
bash r2g-rtl2gds/scripts/flow/fix_signoff.sh design_cases/my_design nangate45 \
  --check drc --max-iters 5 --resume

# Inspect the fix plan without applying anything
python3 r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py design_cases/my_design \
  --check drc | python3 -m json.tool

# Apply one strategy manually
python3 r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py design_cases/my_design \
  --check drc --apply antenna_diode_iters
```

Check results afterwards:

```bash
cat design_cases/my_design/reports/fix_summary.md
cat design_cases/my_design/reports/fix_log.jsonl
```

### Batch BEOL-only DRC for the `stuck` population

`tools/batch_beol_drc.sh` converts the FEOL-hang `stuck` designs to an honest
routing-DRC verdict (`clean_beol` / `fail`) in bulk. It auto-discovers
`status==stuck` designs, orders them by instance count, caps by size (to skip the
large tail that re-hangs on the BEOL CONTACT op), bounds parallelism by memory, and
is idempotent (skips designs already `clean_beol`). Any design that still hangs is
killed by the per-design `DRC_TIMEOUT` and left honestly `stuck`.

```bash
# Preview the work-list (no runs)
tools/batch_beol_drc.sh --max-inst 100000 --dry-run

# Convert all stuck designs <=100K instances, 5 concurrent, 30-min per-design cap
tools/batch_beol_drc.sh --max-inst 100000 --jobs 5 --timeout 1800

# Specific designs
tools/batch_beol_drc.sh DMA_Controller_DMA_registers verilog_ethernet_ip_demux
```

Results: one JSON line per design in `design_cases/_batch/beol_drc_<stamp>.jsonl`
plus a status-count summary on stdout. **Size guidance:** ≤20K-inst designs finish
in seconds, 20K–100K in tens of seconds to minutes; >~400K designs hang on the BEOL
CONTACT op (`cont.width`/`cont.space` over millions of contacts) — leave them
`stuck` (see failure-patterns.md "BEOL-only fallback").
