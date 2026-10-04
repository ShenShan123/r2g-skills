# Six-Family Repair Regression Report

Date: 2026-08-19  
Agent commit: `17bca61` (`fix/signoff-repair-policy-and-classification`)  
Campaign: `/home/yangao/r2g_regression_2026_08_19_six_family_run01`

## Scope

This regression tested six previously failing, development-known RTL instances through the
normal `engineer_loop.py run` path. It did not force recipe ranking, bypass lifecycle gates,
or apply experiment-only action policies. Each project started without inherited backend or
signoff results. The campaign used an isolated knowledge database and three flow workers.

The purpose was to test executable repair behavior, not merely the presence of Recipe entries.
These are development/seen cases and therefore do not constitute held-out generalization evidence.

## Results

| Intended family | Instance | Observed baseline | Agent action | Final physical result | Verdict |
|---|---|---|---|---|---|
| Pin perimeter | `ip_demux`, Sky130HD | PPL-0024: 1,521 pins exceeded 938 legal positions | `core_util_relief`; replaced the utilization-sized floorplan with a 615 x 615 um die | route 0, full DRC 0, LVS clean, timing clean, RCX complete | Pass |
| PDN floorplan | `simple_gpio`, Sky130HD | PDN-0185: die too narrow for the power-grid straps | `pdn_die_floor`; expanded die from 30 x 30 to 200 x 200 um | route 0, full DRC 0, LVS clean, timing clean, RCX complete | Pass |
| Rule DRC | `can_fifo`, Sky130HD | 10 `m3.2` minimum-spacing violations | `density_relief`; set `CORE_UTILIZATION=12` and reran from floorplan | route 0, full DRC 0, LVS clean, timing clean, RCX complete | Pass |
| Footprint/congestion candidate | `nt35510`, Sky130HD | Flow completed; the actual residual was 2 `m2.2` DRC violations | `density_relief`; set `CORE_UTILIZATION=17` | route 0, full DRC 0, LVS clean, timing clean, RCX complete | Instance repaired, but distinct route-congestion coverage is inconclusive |
| Timing closure | `axi_interconnect`, Sky130HD | Post-route WNS was -0.0180278 ns (`minor`) | No timing repair was attempted; only DRC/LVS were checked | DRC/LVS/route clean, but timing remained negative | Fail |
| Antenna closure | `ip_arb_mux`, Nangate45 | 143 full-deck antenna violations: M4=24, M5=42, M6=77 | No auto-eligible Recipe; lifecycle gate returned no strategy | DRC failed; LVS, route, timing, and RCX otherwise clean | Safe escalation, repair capability not demonstrated |

Four of six instances reached a physically clean state after the run. Only three clearly
validated the intended distinct recovery family: pin perimeter, PDN floorplan, and rule DRC.
The `nt35510` result validates another density-based DRC recovery, not a separate route-abort
or footprint mechanism. Timing and antenna closure remain unsupported on these witnesses.

None of the six projects is a publishable strict-clean graph source in this campaign. The
historical snapshot probes intentionally lacked an Fmax winner and a stamped clock constraint;
the manifests correctly remained `strict_clean=false`. This does not invalidate the physical
repair evidence, but it prevents claiming dataset publication success.

## Agent Findings

### 1. Normal engineer-loop completion ignores timing failure

`process_one()` derives normal signoff status from DRC and LVS only. Consequently,
`timing_axi_interconnect` was written as ledger state `clean` even though its final timing tier
was `minor` and WNS was negative. The strict signoff manifest correctly blocked publication,
but the main loop neither invoked the available timing repair path nor represented the terminal
state honestly.

Recommended fix: define an explicit normal-run completion policy that includes timing. Before
marking a design clean, evaluate `timing_check.json`; invoke the bounded timing fixer when WNS is
negative, and distinguish `physical_checks_clean` from `strict_publishable` if Fmax qualification
is intentionally a separate stage. Add a regression where DRC/LVS are clean but WNS is negative.

### 2. Isolated campaigns still rebuild the tracked default knowledge store

The campaign exported `R2G_KNOWLEDGE_DB` to an isolated database. Ingestion honored it, but
`engineer_loop._learn()` ignored the environment and called the learner with
`knowledge_db.DEFAULT_DB_PATH` and the tracked `knowledge/heuristics.json`. At batch completion,
this modified the repository's tracked knowledge artifacts and compared default-store
heuristics against the isolated lifecycle database. It then enqueued 32 unrelated historical
A/B arms from `/home/yangao/r2g_ab_pin_side_2026_08_12_run05` into the regression ledger.

The extra workload was stopped after all six requested instances were terminal, and the two
tracked knowledge files were restored to commit `17bca61`. No EDA child processes remained.

Recommended fix: make `_learn()` accept the same resolved database path used by ingestion and
write heuristics beside that database. Pass the resulting same-store snapshot to
`diff_and_enqueue`; reject mixed database/heuristics provenance. Add a test proving that an
isolated run cannot modify tracked knowledge files or plan arms from another campaign.

### 3. Antenna safety behavior is correct, but capability is not yet promoted

The Nangate45 checker completed normally and exposed real antenna categories. The Agent did not
silently apply a candidate strategy: it stopped with `catalog_exhausted`, which is the safe
lifecycle behavior. However, this means the present branch cannot yet claim autonomous antenna
repair on the selected witness.

Recommended next step: perform independent, provenance-complete Nangate45 A/B validation for the
bounded diode/antenna intervention. Promote it only if target violations improve without route,
LVS, timing, or non-antenna DRC regression.

## Conclusion

The regression does not support the claim that every repair-needed pool case is now repairable.
It provides strong executable evidence for pin-perimeter, PDN-floorplan, and density-based DRC
recovery. It also identifies two consequential Agent gaps: normal runs do not close timing before
declaring ledger clean, and batch learning violates knowledge-store isolation. Antenna handling
fails safely but still requires formal promotion evidence. A true route-congestion witness and
publishable Fmax-stamped fixtures are still needed for complete six-family coverage.
