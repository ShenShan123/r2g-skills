# sky130hs Round-2 Campaign Report — status at stop (2026-07-29)

**Round:** sky130hs r2 (failure-patterns **#58**), launched 2026-07-26 off the four plan docs
(`2026-07-25-three-platform-{pilot-analysis,remediation-plan}-74a0113.md`,
`2026-07-26-heldout-generalization-{analysis,remediation-plan}.md`), stopped on operator request
2026-07-29 after **34 green waves**.
**Code baseline at stop:** `main` @ `b0da91a` (== `origin/main`; 34 commits this round, every
code/doc change pushed per the campaign directive).
**Driver shutdown:** clean — TERM to pgid 653342 drained the whole group in <2 s, zero surviving
EDA processes in any session (the `_bounded_run` supervisors reaped their checker sessions).

---

## 1. Campaign status at stop

Ledger `design_cases/_batch/sky130hs_r2_campaign.jsonl`, 732 designs (last-writer-wins):

| state | count | meaning |
|---|---|---|
| **clean** | **449** | honest terminal signoff (DRC+LVS clean or legitimately skipped), ledger-gate **backed=441** + 8 ab-arm/aux entries |
| escalated | 116 | terminal with a named escalation reason (see §5) |
| pending | 163 | never dispatched — round is resumable |
| flow | 4 | in-flight when the driver was stopped; **auto-reclaim to `pending` on any resume** (`verilog_ethernet_arp`, `verilog_ethernet_axis_baser_rx_64`, `verilog_ethernet_axis_baser_tx_64`, `verilog_ethernet_eth_mac_10g`) |

**Final gates (run at stop, after the last snapshot commit):**

- `honesty.py` → **ALL HONESTY GATES GREEN**
- `check_ledger_signoff_backed.py --platform sky130hs --ledger …/sky130hs_r2_campaign.jsonl`
  → **PASS (fabricated=0, not_ingested=0, backed=441)**
- fail↔event parity **379 == 379** (held at every one of the 34 wave boundaries)
- `check_db_integrity` **PASS at all 34 wave closes**; fabricated=0 throughout; zero orphan
  processes across the whole round
- knowledge store: `ab_trials` **422**, `fix_events` **5323**, sky130hs recipes
  **12 promoted / 18 candidate / 8 shadow**

**Resume:** relaunch `tools/campaign_resume_waves.sh` with the same `LEDGER=`; the drain
entrypoints reclaim the 4 `flow` entries to `pending` (167 open total). `pool.env` is at
`WORKERS=4 NUM_CORES=8 WAVE_MAX=16`.

---

## 2. Plan-doc defects fixed this round (all four docs closed)

Every fix carries tests AND a live production proof from the campaign itself:

| ID | Fix | Live proof |
|---|---|---|
| **RMD3-P0-01** | Global non-regressive live repair: ONE comparator (`knowledge/result_vector.py`, VECTOR_VERSION=1) shared by the live fix loop (`fix_signoff.sh`) and the A/B judge; a measured fresh good→bad flip on a non-target check ⇒ `verdict="regression"` + **config revert** + journal `config_restore` + audit artifact | sha1_wrapper_repo: DRC 6→10 under a relief reflow → flagged `drc_regression:6->10`, config.mk reverted, iter2 re-baselined at 6, ingested as `regression`. Wins unaffected: ARM9 8→4, fifo_basic 2→0, c6288 4→0, usb_host 4→0 |
| **RMD3-P1-01** | ONE effective-stage resolver (`scripts/flow/effective_stages.py`, STAGE_RESOLVER_VERSION=1): digest-verified FROM_STAGE resume lineage merged with the local stage log; consumers `signoff_gate` / `extract_ppa` / `ingest_run` / `repair_run_status` all agree; tamper ⇒ fail-closed everywhere | ARM9 resume: FLOW gate, `ppa.json` (`orfs_effective.lineage_quality="recorded"`), and `runs.orfs_status='pass'` all read the same completion — the FLOW-says-complete / LEARNING-says-partial split is gone |
| **RMD3-P1-02** | Capability metadata bound to the **resolved** signoff env: `platform_capability.resolve_signoff_env()` sources `_env.sh`; manifests persist `env_source`/`env_digest`/`resolved_env`; consistency gate flags a clean bundle that contradicts capability | strict-clean sky130hs manifests now read `strict_signoff_ready=true, missing=[], env_source=_env.sh` |
| **LIM-HO-01** | DRC scaling telemetry quad (`cell_count`/`gds_bytes`/`wall_s`/`peak_rss_kb`) via `_bounded_run.sh` 1 s-tick RSS sampling, stamped into `drc.json` | produced the two-regime scaling picture in §4.3 |

---

## 3. New bugs found BY the campaign (the point of the round)

### 3.1 `pin_pdn_short` — a real, DRC-invisible short class (5 designs)

Chain: opaque Netgen `top_pin_mismatch` on ROM_16 → port-loss theory → the new
`restore_ext_ports.py` safety guard (a merge class with 2+ declared ports is a genuine short,
never restored) exposed a real VSS merge → a `gds flatglob "VIA_*"` "fix" was tried and
**REVERTED** (it merely renamed the shorted blob; wrong turn recorded in failure-patterns #58)
→ geometric DEF analysis proved **IO pins physically overlapping same-layer PDN stripes**.

- New classifier `scripts/extract/check_pin_pdn_overlap.py` (exit 4 + named pins), wired into
  `run_netgen_lvs.sh` ⇒ `MISMATCH_CLASS="pin_pdn_short"` + `lvs/pin_pdn_shorts.json`.
- Confirmed designs (5): `A_Single_Path_Delay_32_Point_FFT_Processor_src_ROM` (ROM_16, 2 pins),
  `qspiflash_spi` (3), `qspiflash_spixpress`, `secworks_…_sha256_w_mem` (12),
  `verilog_axis_axis_crosspoint` (2).
- This is a **systematic sky130hs IO-placement/PDN collision**, invisible to geometry-only DRC.
  **Open recipe lead:** IO_PLACER layer selection or PDN stripe offset (next round).

### 3.2 Ledger cross-round trap

`check_ledger_signoff_backed.py` defaulted to `<platform>_campaign.jsonl`; judging the completed
r1 round against disk state r2 had overwritten produced a **false FABRICATED alarm** (apb_ram).
Hardened: the tool now WARNs and names sibling round ledgers when `--ledger` is omitted.

### 3.3 Follow-up sweep residuals (found while wiring the trio, all fixed + tested)

- `fix_signoff.sh _capture_vector` refreshed the target check's own report — clobbering the
  seeded route baseline (caught by `test_fix_signoff_route`); the target's report is now never
  refreshed by the capture.
- `ingest_run` effective-upgrade hook passed `flow_scope` as `local_status` (self-caught).
- `engineer_loop._fail_stage` judged the **lexically-last** RUN dir, not the mtime-newest run
  ingest ingests → `_newest_stage_log()`.
- `engineer_loop._synth_cleared_ondisk` read every FROM_STAGE resume as "synth never cleared"
  (no local synth row) → now attributes synth through the shared resolver, honest on tamper.
- `journal_db.ACTION_TYPES` gained `config_restore` (the revert action was silently droppable).

### 3.4 DRC wall-time/RSS scaling (LIM-HO-01 telemetry, 8 points, two regimes)

- **Size-driven:** bound-crossings at the 7200 s scan bound in tail-rule band **454–473** for
  169–234 K-cell designs at 17–24 GB peak RSS.
- **Geometry-driven:** iccad2015 units cross at rules **825/853/855** at only ~114–120 K cells.
- ≤ ~123 K cells completes normally (~5000 s). Honest `stuck` classification held for every
  bound-crossing (no fabrication, telemetry attached). Recipe/bound tuning is an open lead
  (LIM-HO-02).

---

## 4. Learning-loop evidence

- **12 promoted sky130hs recipes**: `density_relief` on two symptom families
  (`04d38c5a…` ×4 generations, `7130eb48…` ×6), `route_relief` (`11f02dbb…`),
  `synth_memory_relax` (`4216f450…`) — per-platform `promoted` GREW, so the 2026-06-24
  "arms-identical" alarm is structurally absent this round.
- Judge-v2 verdicts in force (`success_tie_cost_within_noise` ties are inconclusive, never
  demote); `auto_demote_on_regression` (window=2, exact-domain) newly reachable via the shared
  comparator.
- Both arms demonstrably diverge (verified per-trial `metrics_json`); 422 trials total.
- Negative learning captured: the sha1_wrapper_repo regression trajectory (§2) is in
  `fix_trajectories` with the revert, not paved over.

## 5. Escalation landscape (sky130hs, cumulative — includes r1 + pre-#32/#57 eras)

`catalog_exhausted` 682, `unseen_crash` 198, `incomplete_missing_header` 52,
`pin_overflow_residual` 40, `synth_memory_residual` 34, `route_congestion_residual` 22,
`unvalidatable_insufficient_subjects` 20, `ab_coverage_gap` 19, `signoff_stuck_scan` 16,
`synth_timeout` 13, `repair_cycle_nonconverged` 4, `place_density_residual` 2, `cts_crash` 1.
Caveat: the table is cumulative across sky130hs history — the large `catalog_exhausted` bucket
is dominated by the r1 phantom-DRC era (#32) and the dead-corpus-root wave (#57), not this round.

## 6. Open leads for the next round

1. **`pin_pdn_short` repair recipe** — IO_PLACER layer / PDN stripe offset; 5 recurring designs.
2. **LIM-HO-02** — DRC scan bound/deck tuning for the >169 K-cell tail-rule band.
3. `select_run` unification + `eval_heuristics` follow-ups (parked from #58 sweep).
4. 163 pending + 4 reclaimed designs finish the ledger on resume.
5. **New external analysis docs arrived mid-round** (untracked):
   `2026-07-27-three-platform-{pilot-analysis,remediation-plan}-ff01d5c.md` and
   `2026-07-28-heldout-v3-{generalization-analysis,remediation-plan}.md` (six defects at
   baseline `ff01d5c`) — the natural input for the next round. The same external pass deleted
   12 older tracked plan docs in the working tree (including this round's four inputs and the
   two V1 spec docs); those deletions are **deliberately left uncommitted** pending operator
   confirmation — note the V1 registry pins the spec's sha256.
