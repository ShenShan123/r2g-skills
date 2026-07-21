# R2G V1 Pilot Run 01: Independent Analysis

## Scope and Reproducibility

- Agent commit: `3117f0e3c00ba528f6029fd9a7d569e37ac3b9dd`
- Platform: `nangate45`
- Positive fixtures: GCD, WBUART32/AXI UART, Verilog I2C master, Secworks SHA-256
- Negative controls: provenance/publication guards and A/B causality/lifecycle guards
- Execution coverage: 49/49 applicable Gate cells
- Result: 32/49 Gate cells passed (65.3%); strict end-to-end yield 0/4
- Agent source code was not modified during the campaign.

The zero strict yield does not mean that no useful output was produced. GCD and UART
both generated all five graph views and passed 293/293 independent graph checks. They
still fail V1 because their signoff evidence is incomplete, so the independent clean
dataset index correctly remains empty.

## Confirmed Agent Findings

### P0-1: Enforced graph generation accepts non-strict signoff

`signoff_gate.py` treats `pass_with_caveats` as publishable. GCD and UART therefore
generated graph datasets even though LVS was skipped, timing was unknown, and report
binding was weak. This conflicts with frozen `FLOW-004`, under which skipped or missing
signoff evidence is never `r2g_clean`.

Recommended change: define separate strict and research tiers. In strict V1 mode,
`run_graphs.sh` must require the exact verdict `pass`; `pass_with_caveats` may only
produce an explicitly non-clean research artifact that cannot enter the clean index.

### P0-2: The signoff path does not emit the complete strict evidence bundle

All four projects lack canonical `reports/route.json`, `reports/rcx.json`, and
`reports/timing_check.json`. Nangate45 LVS is also recorded as `skipped`. Consequently,
the Fmax winners remain placement proxies marked `UNVERIFIED`, every CONSTRAINT Gate
fails, and every SIGNOFF Gate fails even where ORFS route, SPEF, and full DRC exist.

Recommended change: make final timing confirmation, route extraction, RCX/SPEF
validation, and supported LVS execution explicit signoff stages. Bind their report
digests, SDC digest, platform policy, and confirming run ID into one signoff manifest.

### P0-3: Environment readiness can pass although strict signoff is impossible

The Pilot ENV Gate and current `check_env.sh` passed, but the installed Nangate45 setup
had no LVS rule and its `ANTENNA_X1` was unusable for repair (`ANTENNADIFFAREA 0.0`). The
installer documents `platform_rules` as outside the default plan. Thus a green
environment check does not currently mean that the selected platform can satisfy the
strict V1 Gate.

Recommended change: add a per-platform capability manifest. Strict Nangate45 readiness
must verify the DRC deck, LVS deck, antenna model, positive diode diffusion area, timing
policy, and RCX prerequisites. Missing strict capabilities should fail ENV/PLATFORM Gate
before a multi-hour flow starts.

### P0-4: A repair-only generation can be reported as a complete ORFS run

The second I2C backend generation contains only successful `route` and `finish` rows,
but `signoff_gate.py` reports ORFS `complete` because it only requires a successful
`finish` row. The independent grader correctly rejects the generation because synth,
floorplan, place, and CTS are absent and no parent-generation chain proves which
upstream artifacts were consumed.

Recommended change: either materialize all six bound stage records in every official
generation, or record a content-addressed parent chain for reused stages. Completion
must require a reconstructable six-stage lineage, not merely a successful finish row.

### P0-5: Corpus graph IDs are not assigned by the full-flow graph entry point

The independently verified GCD and UART manifests both contain `graph_id: 0`; only their
`generation_id` values differ. `run_graphs.sh` never supplies `build_graphs.py --graph-id`,
whose default is zero. A multi-design corpus can therefore contain valid individual
graphs with colliding graph-level and node-level IDs.

Recommended change: assign a stable corpus-level graph ID before feature/label
extraction, carry it through every view and manifest, and add an independent
cross-project uniqueness check before publication.

### P1-1: Antenna repair is executed without checking its stated prerequisite or effect

I2C selected `antenna_diode_repair` although OpenROAD repeatedly reported
`GRT-0246: No diode with LEF class CORE ANTENNACELL found`. The pre- and post-repair
`6_final.def` files had the same SHA-256 digest, yet the Agent reran a roughly 5,200 s
full DRC. The loop did eventually record `4 -> 4 (no_improvement)` and stop, so the old
infinite-retry defect did not recur.

Recommended change: check the antenna model and usable diode before ranking this Recipe;
after reroute, compare canonical DEF/ODB and structural effect fingerprints. If no
effect occurred, emit `recipe_no_effect` and skip the expensive signoff rerun. Cache
signoff by deck digest, canonical-layout digest, and tool version.

### P1-2: A DRC-only request can rebuild upstream ORFS stages

During the first I2C DRC invocation, `make drc` reran synthesis, floorplan, placement,
CTS, route, and finish before KLayout. This did not happen consistently for the other
fixtures, showing that timestamp-based restaging does not provide deterministic stage
reuse.

Recommended change: run signoff directly on a frozen, digest-verified GDS, or represent
the dependency generation explicitly instead of relying on copied-file mtimes. A DRC
entry point should never rebuild physical implementation implicitly.

### P1-3: Batch completion status is ambiguous

The signoff batch command returned code zero even though I2C remained DRC-dirty and
SHA-256 ended with `drc status='stuck'`. Per-project reports remained honest and graph
gating rejected both designs, but a caller observing only the process return code could
misinterpret the batch as successful.

Recommended change: emit an aggregate terminal state such as `clean`, `partial`, or
`failed`, and use a nonzero strict-mode exit code when any required subject is not clean.
Keep a separate execution-completed status for resumable campaign control.

## Capability Limit Observed

SHA-256 completed all ORFS stages and reached zero routing violations, but the full
FreePDK45 KLayout DRC spent 7,200 s in the polygon operation at rule line 131 and timed
out. The Agent correctly classified `drc_stuck_tooling_out_of_v1_scope`, did not claim
clean, and blocked graph generation. This is not a false-success logic bug; it is a
current scalability limit that must be addressed through a validated deck/tool strategy
or an explicit V1 size budget. BEOL-only DRC cannot substitute for strict full DRC.

## Validation-Harness Findings

1. The Pilot ENV oracle must be strengthened to test strict per-platform decks and
   models, not only executable availability.
2. The graph verifier should report `blocked/not_applicable` when graph generation was
   intentionally denied, rather than raising `FileNotFoundError` for the absent manifest.
3. The CONSTRAINT failure message should enumerate the missing final timing confirmation;
   its current summary shows only the otherwise matching proxy period and SDC period.

These are evaluator/reporting changes, not Agent fixes, and must remain separate from
the confirmed Agent findings above.

## Positive Evidence

- All four pinned source closures passed acquisition, digest verification, synth-only,
  and promotion.
- All four bounded Fmax searches terminated with finite proxy results.
- All four ORFS designs reached routed/final artifacts; three were independently seen as
  complete six-stage generations.
- GCD and UART full DRC were clean, and their five graph views passed 293/293 checks.
- Dirty or stuck signoff blocked I2C and SHA-256 graph generation.
- All fixed provenance, publication, A/B causality, and lifecycle negative controls passed.
- I2C antenna repair stopped after measured no improvement rather than looping forever.

## Acceptance Priority

The shortest path to a meaningful second run is: align ENV with strict platform
capabilities; produce and bind final timing/route/RCX/LVS reports; make enforce mode
require exact strict pass; preserve full lineage across repair generations; then add
antenna precondition/effect checks and corpus-wide graph-ID uniqueness. The same four
pinned positives and two negative fixtures should then be rerun without changing the
frozen acceptance questions.
