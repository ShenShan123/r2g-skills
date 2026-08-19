# Development Repair Coverage and Promotion Record

## Scope and evidence rule

This remediation uses the 29 admitted repair-family records under
`docs/experiments/signoff/evidence` plus the earlier Pilot and Experiment-1 failure
logs.  Every RTL used to author, tune, or A/B-test an action is **development/seen
evidence** and must not be reported as an untouched held-out result later.

The objective is mechanism coverage, not one hard-coded Recipe per RTL.  A production
action is added only when it has a reproducible failure signature, at least one clean
physical-signoff witness at frozen constraints, a bounded platform/effect domain, and a
material intervention that preserves the registered design target. Dataset publication
additionally requires the Fmax/provenance gates. Acquisition errors, incomplete source
trees, toolchain failures, and unsupported physical residuals are classified honestly
instead of being converted into signoff Recipes.

## Six-family inventory

| Family | Admitted evidence | Platform coverage | Production handling after this branch |
|---|---:|---|---|
| Pin perimeter (`PPL-0024`) | 7 | Sky130HD 5, Nangate45 2 | Existing perimeter-aware die recovery and `core_util_relief`; no new per-design constants. |
| Rule-specific DRC (`m3.2`) | 5 | Sky130HD 5 | Existing density relief plus the branch's edge-localized `pin_side_rebalance`; formally promoted for the validated Sky130HD logic classes. |
| PDN floorplan (`PDN-0185`) | 7 | Sky130HD 7 | Existing deterministic `pdn_die_floor` recovery; retained as a bounded backend-abort handler. |
| Timing closure | 3 | Sky130HD 3 | New A/B-gated `setup_slack_margin`: only clean-route, finite WNS in `[-0.2, 0)` ns, Sky130HD, and no prior margin. It sets `SETUP_SLACK_MARGIN=0.2` without editing the SDC or clock period. |
| Footprint/congestion | 1 | Sky130HD 1 | Existing `density_relief`/`route_relief`; no claim of broad family coverage from one witness. |
| Antenna closure | 6 | Nangate45 6 | Existing Nangate45 diode-forced action with capability precheck; retained for migration evaluation, not used to inflate the Sky130HD main result. |

The evidence spans 29 admitted tasks and multiple repositories.  Several RTLs exhibit more
than one family; they remain one development subject, not independent evidence counted twice.

## Timing repair added in this branch

Three source- and target-bound witnesses motivated the same effect:

| Subject | Repository | Frozen target | Baseline setup WNS | Witness setup WNS |
|---|---|---:|---:|---:|
| `axi_interconnect` | `alexforencich/verilog-axi` | 460 MHz | -0.0180278 ns | +0.00176459 ns |
| `jesd204b` | `zeroasiccorp/logikbench` | 172 MHz | -0.0160414 ns | +0.081974 ns |
| `sha256_stream` | `secworks/sha256` | 100 MHz | -0.00451017 ns | +0.125437 ns |

The action is deliberately narrow.  It is not offered on Sky130HS/Nangate45, when routing
is dirty, for a deficit worse than 0.2 ns, or after the margin is already present.  It was
promoted only after the formal two-subject A/B campaign below. Timing Recipe lookup,
ranking, negative evidence, and lifecycle filtering are now wired to the same
symptom-indexed path already used by DRC/LVS.

## A/B and lifecycle corrections

The first dry campaign exposed a general promotion defect: the timing judge treated the
`minor` tier as success even when WNS was negative.  `minor` means "eligible for automatic
repair", not "timing closed".  That rule made an untreated negative-WNS arm successful and
reduced a real closure experiment to a noisy wall-time comparison.  Timing A/B success now
requires `tier=clean` or non-negative WNS.

Two further campaign defects were corrected before evidence was admitted:

- A fresh timing arm has a current `route.json` but intentionally inherits no old signoff
  report. Diagnosis now uses that explicit route result instead of incorrectly requiring a
  pre-existing DRC report before it can apply the timing action.
- A timing improvement is not sufficient for promotion. After each timing arm, the harness
  runs a checker-only DRC/LVS pass with zero repair iterations, and the judge also requires
  ORFS completion, clean route/DRC/LVS, and complete RCX. This prevents timing closure from
  hiding a new physical regression or a second intervention from confounding the trial.

The campaign also exposed two competing design-class implementations. Ingest used PPA
geometry while live diagnosis used only a local backend directory, making an archived
`crypto/medium` project appear as `crypto/unknown` and stranding its promoted lifecycle
row. `suggest_config.detect_design_class` is now the single implementation: it uses PPA
geometry first and synthesis statistics as fallback, and both ingest and live diagnosis
call it. RTL type detection likewise covers the configured `VERILOG_FILES` closure rather
than only `project/rtl/*.v`.

## Formal A/B result

The admitted campaign is
`/home/yangao/r2g_ab_setup_margin_2026_08_19_run06`. It used the exact lifecycle key
`913f3c15479aa474 / crypto/medium / sky130hd / setup_slack_margin`, two independent
development subjects, two repeats per arm, and eight fresh physical runs.

| Subject | Arm A setup WNS | Arm B setup WNS | A/B SDC digest | B physical checks | Trial |
|---|---:|---:|---|---|---|
| `jesd204b` | -0.0160414 ns (both repeats) | +0.081974 ns (both repeats) | identical | route, DRC, LVS clean; RCX complete | win |
| `sha256_stream` | -0.00451017 ns (both repeats) | +0.125437 ns (both repeats) | identical | route, DRC, LVS clean; RCX complete | win |

Every A arm omitted the margin and every B arm contained exactly
`SETUP_SLACK_MARGIN=0.2`. Both trials recorded distinct, locally owned run IDs,
`provenance_complete=true`, no regression veto, and `success_lcb_delta`. The isolated
corpus therefore produced `promoted / ab_corpus:2w0l`; the additive merge imported 24
evidence rows into the tracked store and all honesty gates remained green.

Runs 01--05 are retained outside the repository as audit history but are excluded from
promotion evidence. They respectively exposed a wrong Python binding, the negative-WNS
success bug, missing fresh-route handling, absent post-timing physical checks, and a
`logic/medium` versus `crypto/medium` lifecycle-key mismatch. Only run06 was merged.

These development probes were created at frozen registered clock targets and do not carry
an Fmax-search winner record. Consequently their `signoff_manifest.json` correctly remains
non-publishable even though the full physical signoff vector is clean. The A/B result
validates a repair action; it is not claimed as graph-dataset publication evidence.

## Experiment-1 and historical Pilot disposition

The earlier failures were re-read by mechanism rather than copied blindly into the catalog:

- Sky130HD `m3.2`, pin-overflow, PDN-width, route-congestion, and small setup-miss cases map
  to the bounded handlers above.
- Nangate45 antenna cases remain platform-specific and capability-gated.
- Missing headers, unresolved source closure, invalid tops, unsupported memories, and
  synthesis timeouts belong to RTL acquisition/synthesis qualification. They must not teach
  a physical signoff Recipe.
- Missing decks, stale tool installations, Python/toolchain mismatches, and checker timeouts
  are environment evidence. They must not become RTL repair knowledge.
- A physical residual without a reproducible strict-clean witness remains an honest residual;
  no speculative action is added merely to make a historical Pilot row pass.

This means the branch covers all six admitted repair mechanisms and the corresponding
signoff-level Pilot failures, but intentionally does **not** claim that every historical
failed RTL is repairable by the current catalog.

## Validation record

- Focused diagnosis, lifecycle, policy, classifier, and A/B tests: **93 passed**.
- Full signoff-loop suite with the active ORFS/PDK/Python toolchain bound explicitly:
  **1193 passed, 1 skipped** across all 143 test files. A monolithic pytest process was
  killed by the shared host at 84% with no failed assertion; the same complete file set
  then passed in five sequential processes to avoid process-memory accumulation.
- Formal timing A/B: **2 wins, 0 losses**, two independent subjects and two repeats per
  arm; all eight arms provenance-complete and globally checked.
- Knowledge merge: **24 additive rows**, zero honesty-gate failures; final lifecycle is
  `promoted / ab_corpus:2w0l`.
- Live smoke: both original projects and a backend-free archived copy select
  `setup_slack_margin -> floorplan -> timing` with `lifecycle_status=promoted`.
