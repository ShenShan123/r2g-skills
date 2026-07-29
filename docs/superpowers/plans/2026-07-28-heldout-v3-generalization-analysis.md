# Held-Out V3 Generalization Analysis: E2E and Frontend Cohorts

Date: 2026-07-28  
Last updated: 2026-07-29  
Agent commit: `ff01d5ccd62fa53e167446bcf33dce9911bda288`  
Platforms: `nangate45`, `sky130hd`, `sky130hs`

## 1. Executive Verdict

Two independently frozen four-design cohorts were evaluated on all three supported
platforms. Across the resulting 24 design-platform attempts, the production Agent
published **7 strict-clean graph datasets (29.2%)**. The successful designs were USB CDC
on all three platforms, FTDI Bridge on all three platforms, and SPI Flash on Nangate45.
Every published generation passed strict DRC/LVS/timing/route/RCX checks and all 297
independent graph checks.

The first cohort scored **22/49 gate cells and 1/4 end to end on every platform**. The
second cohort improved to **27/49 and 1/4 on Sky130HD**, **31/49 and 2/4 on Nangate45**,
and **27/49 and 1/4 on Sky130HS**. The combined gate-cell result is **151/294 (51.4%)**.
This establishes repeatable strict-clean RTL-to-graph capability, but the low and
platform-dependent yield does not yet support a broad corpus-generalization claim.

Five production-Agent issues were confirmed across the two cohorts and associated
controlled diagnostics:

1. **P0-HO-01:** semantically invalid or explicitly unfinished RTL can be promoted.
2. **P1-HO-01:** the wrong synthesis log hides memory-limit failures and permanently
   excludes valid RTL.
3. **P1-HO-02:** acquisition-stage `acquire_exclude` evidence leaks into the signoff A/B
   planner and creates meaningless physical-design trials.
4. **P1-HO-03:** language-valid RTL rejected by the installed synthesis frontend is
   mislabeled as low-value source and removed from normal acquisition.
5. **P1-HO-04:** a rejected live repair restores configuration text but leaves the active
   run pointer and terminal reports on the regressed physical result.

The subsequent frontend development and reserved-validation campaigns confirmed a sixth
production issue, **P1-FE-VAL-01**: autonomous closure extraction misses module
dependencies selected through Verilog preprocessor macros. Those campaigns are summarized
in Sections 8--10.

No new strict-signoff capability blocker was found. Nangate45 full-deck DRC remains
materially slower, but both of its second-cohort designs completed successfully without
weakening the gate.

## 2. Experimental Protocol

Each official cohort was frozen before execution. Each repository revision, top module,
clock, source list, include directory, and source digest was fixed in the registry.
The four designs in each cohort were run independently on all three platforms with one
Agent worker and four tool cores. Production Agent code was not modified during either
campaign.

### Cohort A

| Fixture | Repository commit | Top | RTL files |
|---|---|---|---:|
| `openrisc_mor1kx` | `f46074a88576e1d7e2fc6cfae14a664dc593a2d8` | `mor1kx` | 49 |
| `ultraembedded_jpeg` | `bb03cce45d0b7459d395486e9e1db3de1b416bd2` | `jpeg_core` | 23 |
| `ultraembedded_audio` | `f90ca0067d40aac95dbbea1dd0a14d0a950fe785` | `audio` | 8 |
| `ultraembedded_usb_cdc` | `d61b43fbd97fcb333fde203d7b885da315e3c2d4` | `usb_cdc_core` | 7 |

### Cohort B

| Fixture | Repository commit | Top | RTL files |
|---|---|---|---:|
| `ultraembedded_spiflash` | `5c9575ab2820fea64ad5307bfa736fe94ca87881` | `spirom` | 3 |
| `ultraembedded_ftdi_bridge` | `d099493a9a2d63cb599980ea27ee4e638f86725f` | `ftdi_bridge` | 3 |
| `roalogic_apb4_gpio` | `2bada1f7fe5d18ed5ecf357cbb5a93cc7eaea7ad` | `apb_gpio` | 1 |
| `freecores_wb_dma` | `56126339e2cef66c8769c3f8516ba7222063e851` | `wb_dma_top` | 12 |

The exact Cohort B revisions and top modules were absent from frozen Agent memory before
execution. Candidate scouting was limited to repository health, source closure, and
top-module identification; no production-Agent qualification result was observed before
the registries were frozen.

The evaluator also ran its two fixed negative controls. Both passed on every platform in
both cohorts.
The Pilot evaluator and registry were validated before the run (`13` evaluator tests
passed; registry lint reported four positive and two negative fixtures).

## 3. Official Results: Cohort A

| Platform | Passed gate cells | Strict-clean E2E | Successful held-out design |
|---|---:|---:|---|
| Sky130HD | 22/49 (44.9%) | 1/4 (25.0%) | `ultraembedded_usb_cdc` |
| Nangate45 | 22/49 (44.9%) | 1/4 (25.0%) | `ultraembedded_usb_cdc` |
| Sky130HS | 22/49 (44.9%) | 1/4 (25.0%) | `ultraembedded_usb_cdc` |

The identical scores are not evidence that the physical tools failed identically. Three
fixtures were stopped before promotion by the same platform-independent acquisition
classification defect. Only USB CDC exercised the complete physical and graph pipeline.

### Successful cross-platform evidence

| Platform | Fmax period | Final WNS | DRC | LVS | Route | Graph verification |
|---|---:|---:|---:|---:|---:|---:|
| Sky130HD | 3.90551 ns | +0.182319 ns | 0 | clean | 0 | 297/297 |
| Nangate45 | 1.01292 ns | +0.0795608 ns | 0 | clean | 0 | 297/297 |
| Sky130HS | 2.69161 ns | +0.133401 ns | 0 | clean | 0 | 297/297 |

All three USB CDC manifests declared `strict_clean=true`, strict platform capability,
same-run report consensus, and a `strict_clean` dataset tier. Each platform published a
distinct graph identity and atomic generation. This is positive evidence that the current
Fmax, ORFS, strict-signoff, provenance, graph-conversion, and publication path is coherent
for an unseen non-memory-heavy design.

## 4. Official Results: Cohort B

| Platform | Passed gate cells | Strict-clean E2E | Published held-out designs |
|---|---:|---:|---|
| Sky130HD | 27/49 (55.1%) | 1/4 (25.0%) | `ultraembedded_ftdi_bridge` |
| Nangate45 | 31/49 (63.3%) | 2/4 (50.0%) | `ultraembedded_spiflash`, `ultraembedded_ftdi_bridge` |
| Sky130HS | 27/49 (55.1%) | 1/4 (25.0%) | `ultraembedded_ftdi_bridge` |

Both source bundles that passed acquisition reached Fmax and full ORFS on every platform.
The two remaining bundles failed before promotion because of frontend compatibility, not
missing source files or source drift.

### Successful strict-clean evidence

| Platform | Design | Fmax period | Repair evidence | Graph verification |
|---|---|---:|---|---:|
| Sky130HD | FTDI Bridge | 6.13442 ns | no signoff repair required | 297/297 |
| Nangate45 | SPI Flash | 0.996423 ns | no signoff repair required | 297/297 |
| Nangate45 | FTDI Bridge | 1.07591 ns | 3 M5 antenna violations to 0 | 297/297 |
| Sky130HS | FTDI Bridge | 4.2134 ns | no signoff repair required | 297/297 |

Nangate45 FTDI Bridge is a useful positive Agent control. The first strict full-deck DRC
found three `METAL5_ANTENNA` violations. The Agent selected `antenna_diode_repair`, reran
from route with digest-verified earlier-stage lineage, and the second full-deck DRC
returned zero violations. LVS, route, timing, and RCX were clean, and the graph
publication gate accepted the repaired run.

### Correctly blocked physical nonclosure

Sky130HD SPI Flash completed ORFS and improved full-deck DRC from 35 to 20 violations
after `density_relief`, while LVS and routing remained clean. Because residual DRC
remained, graph generation was blocked.

Sky130HS SPI Flash began with two `li.3` DRC violations and a clean route. A
`density_relief` attempt still had two DRC violations, introduced one route violation,
and produced an LVS `pin_pdn_short` mismatch. The global comparator correctly recorded
`verdict=regression`; the learner stored a regression rather than a win, and publication
was blocked. This is direct positive evidence that the global live-repair veto works.

## 5. Confirmed Production-Agent Findings

### P1-HO-01: Recoverable Memory-Limit Failures Are Permanently Misclassified

**Observed behavior.** Mor1kx, JPEG, and audio all failed the default 4096-bit synthesis
memory guard. Their actual failed runs reported inferred memories with largest sizes of
25,856, 16,384, and 65,536 bits respectively. Nevertheless, all three diagnoses became
`synth-frontend-low_value_failure`, and the acquisition learner recorded
`strategy=acquire_exclude`.

**Root cause.** `synth_log_from()` in
`rtl-acquire/scripts/execute/expand_candidates.py` copies the first existing file in this
order:

1. `1_1_yosys.log`
2. `1_1_yosys_canonicalize.log`
3. `flow.log`

The successful canonicalization log exists even when the later synthesis step fails.
Consequently, the copied `synth.log` omits the memory-limit exception. The classifier then
falls through to permanent `low_value_failure`, although the repair layer already knows
how to recognize `SYNTH_MEMORY_MAX_BITS` and `Synthesized memory size`.

**Controlled counterfactual.** A separate, non-scored experiment changed only the explicit
memory cap. All three designs then completed synth-only and produced netlist graphs:

| Design | Counterfactual cap | Synthesized cells |
|---|---:|---:|
| Mor1kx | 65,536 bits | 105,102 |
| JPEG | 65,536 bits | 126,816 |
| Audio | 131,072 bits | 223,538 |

This proves that the source bundles were synthesizable and that permanent exclusion was
incorrect. It also shows why a global high cap is not a sound fix: memory lowering expands
these designs into very large standard-cell netlists. The Agent needs truthful failure
capture, bounded resource-aware retry, and honest deferral when projected cost exceeds the
selected execution tier.

### P1-HO-02: Acquisition Disposition Leaks Into Signoff A/B Planning

**Observed behavior.** On each platform, the three failed acquisition candidates produced
three `fix_events` with `strategy=acquire_exclude`. The learner then created one
`recipe_status` candidate and the signoff engineer loop executed four physical A/B trials
for that strategy. Each trial used two repeats per arm and ended inconclusive because both
arms failed. The same pollution occurred independently on all three platforms:

| Per-platform database evidence | Count |
|---|---:|
| `acquire_exclude` fix events | 3 |
| Candidate lifecycle rows | 1 |
| Physical A/B trials | 4 |
| Inconclusive physical A/B trials | 4 |

**Root cause.** `acquire_exclude` is not a registered signoff apply strategy. However,
`_known_apply_strategy()` treats any strategy with a non-empty historical `fix_events`
verdict as executable. The acquisition event therefore bypasses the no-op guard.
`plan_arms_for_candidates()` has platform filtering but no action-domain or subject-stage
filter, so it plans signoff arms from acquisition synth workspaces.

This wastes execution, contaminates the A/B store with meaningless evidence, and can keep
an unapplyable candidate alive. A historical event proves that an action was recorded; it
does not prove that the current executor has a compatible handler or that the selected
subject belongs to the same pipeline stage.

The defect repeated in Cohort B. On every platform, two acquisition exclusions produced
two `fix_events`, one `recipe_status` candidate, and four inconclusive physical A/B
trials. Across the three platforms, this added 12 experiments that could not apply a
meaningful physical effect.

### P1-HO-03: Frontend Compatibility Failures Are Mislabeled as Low-Value RTL

**Observed behavior.** APB4 GPIO and WB DMA had complete, digest-verified source bundles,
but normal acquisition marked both `frontend_low_value_failure`, emitted
`strategy=acquire_exclude`, and prevented promotion on all three platforms.

For APB4 GPIO, the Agent first selected the Slang frontend even though the installed
Yosys had no `slang.so`. Its sv2v fallback then produced Verilog that Yosys rejected with
`Non-constant expression in constant function`. For WB DMA, Yosys rejected a Verilog-2001
port named `int` because its parser treated the identifier as a keyword. These are
frontend capability and dialect-compatibility failures, not evidence that the source is
low value.

**Controlled counterfactual.** The original APB4 GPIO source compiled with Icarus
Verilog in SystemVerilog mode, and the complete original WB DMA source closure compiled
with Icarus in IEEE 1364-2001 mode; both commands returned zero. This does not prove
physical-flow success, but it proves that permanent source-quality exclusion is not
justified by the observed parser failures.

**Root cause.** Frontend selection is not guarded by an installed-capability check, and
the terminal acquisition taxonomy conflates unsupported frontend behavior with source
quality. The Agent does create a manual repair case, but the normal autonomous path still
ends in exclusion and records negative learning evidence. It needs a capability-aware
frontend matrix and a distinct `tool_compatibility` or `deferred_frontend` outcome.

### P1-HO-04: Rejected Live Repair Leaves the Regressed Run Active

**Observed behavior.** In Sky130HS SPI Flash, the baseline run had zero route violations.
The `density_relief` rerun introduced one route violation and an LVS mismatch, so the
Agent correctly rejected it as a global regression and restored `CORE_UTILIZATION` from
22 to the baseline value of 30.

The rollback was incomplete. `backend/.r2g_signoff_run` still points to the rejected
rerun, and the project-level DRC, route, LVS, signoff-manifest, and gate reports also
describe that rerun. The accepted baseline remains in the backend archive but is no
longer the active evidence bundle. The ingested `runs` row consequently pairs restored
`core_utilization=30` with the rejected rerun's DRC/LVS outcome.

**Impact.** Publication remained safely blocked, so no untrusted graph escaped. However,
a subsequent diagnosis, repair, or ingest starts from a run the comparator explicitly
rejected, while configuration text describes the prior state. This breaks the invariant
that the active run and active configuration represent the best accepted state and can
amplify regressions in a longer repair loop.

**Root cause.** Live repair rollback is file-oriented rather than transactional. It
restores configuration edits but does not atomically restore the active-run pointer and
the report bundle that belongs to the accepted run.

### P0-HO-01: Explicitly Unfinished RTL Can Pass Acquisition and Promotion

This finding came from a V2 scouting run and is not included in the official V3 score.
The initially selected `secworks_sha3` repository states in its README:
`Not completed. Does not work. Do. Not. Use.` The Agent still synthesized and promoted the
design despite substantial undriven internal-wire evidence, then spent physical-design
resources until detailed routing reported 8,726 violations.

The route failure happened to block publication in this case, but physical cleanliness is
not a functional-correctness proof. A structurally incomplete design could conceivably
pass physical signoff and enter the graph corpus. Acquisition therefore needs a semantic
readiness gate that combines explicit repository health declarations with structural
synthesis checks. README text alone should not be a universal rejection rule, but an
explicit unusable status corroborated by undriven logic must prevent normal promotion.

## 6. Tool and Platform Observations

All selected platform capability checks were strict-ready. USB CDC completed full DRC,
LVS, timing, route, RCX, and graph publication on all three platforms. The previous
capability-manifest inconsistency was not reproduced.

Nangate45 full-deck DRC remains much slower than the Sky130 checks. For Cohort B FTDI
Bridge, the initial and confirming full-deck checks took 4,617 and 4,590 seconds
respectively. Both terminated normally, and the confirming check returned zero
violations. This is a performance limitation to measure and budget, not a reason to relax
strict signoff or remove Nangate45 from V1.

SPI Flash also demonstrates real platform sensitivity rather than a universal Agent
failure: it was strict-clean on Nangate45, retained 20 DRC violations on Sky130HD, and
regressed route/LVS under the attempted Sky130HS density change. The gates represented
these outcomes honestly and published only the clean platform result.

## 7. Scientific Interpretation

The two cohorts provide a useful held-out result, not a near-complete validation:

- It establishes a real cross-platform positive control for the entire Agent pipeline.
- It shows that strict gates reject or block incomplete outputs rather than publishing
  them as clean data.
- It exposes a high-impact acquisition recovery defect before physical implementation.
- It exposes a stage-boundary defect in the shared learning and A/B machinery.
- It confirms that global regression detection can reject a locally attractive repair.
- It exposes two additional state/capability defects under different RTL dialects and a
  real rejected physical repair.
- It does not support a claim of broad held-out success at 7/24 corpus yield.

After the fixes in the accompanying remediation plan, both frozen cohorts should be rerun
unchanged. Only then should a larger, separately frozen held-out cohort be used to
estimate generalization rather than to continue debugging.

## 8. RTL-Acquisition / Frontend Development Cohort

### Scope and interpretation

An additional eight-repository cohort was frozen and run only through repository
discovery, authoritative source qualification, netlist-graph synthesis, and promotion.
Fmax, ORFS, signoff repair, DEF graph conversion, and publication were deliberately not
invoked. This isolates the previously under-tested frontend boundary from physical-tool
closure.

This is a **development cohort**: its first run found defects, the production worktree was
changed, and the same fixtures were rerun. The final result is therefore regression
evidence, not an unbiased held-out generalization estimate.

| Fixture | Commit | Frontend stress |
|---|---|---|
| `ultraembedded_dbg_bridge` | `3ea8f99cb458acf3f6cfe27862afe77d560caf08` | Small multi-file Verilog |
| `ultraembedded_enet` | `05ecb69278eee6040c979898509c42d1637d0c81` | Multi-file inferred RAM |
| `ultraembedded_mmc` | `b30e552f3abd096bb17b79d48b46c859877894fe` | Include/header closure and RAM |
| `roalogic_ahb3lite` | `4ad2737c73fc29a4ae35ba892ac0545a1730b877` | SystemVerilog package and unpacked arrays |
| `olofk_serv` | `41e8aeedfd1e9ad5f95902c5b0dfc83d1c99e5d2` | FuseSoC-declared multi-file core |
| `ultraembedded_uriscv` | `a6dc9bee3a99f693ba9f2ec526f41c54b4e191a2` | Include-driven RISC-V core |
| `alexforencich_axis_fifo` | `48ff7a7e2ef782cf778d47910cf85835c64b1bce` | Parameterized 40,960-bit FIFO |
| `yosys_picorv32` | `87c89acc18994c8cf9a2311e871818e87d304568` | Large single-file core with debug tasks |

All eight authoritative closures passed an independent Icarus or Verilator
parse/elaboration oracle. Exact commits were absent from the frozen Agent knowledge and
script/config trees.

### Independent Frontend Pilot

The new Pilot separates autonomous discovery from controlled qualification. It grades 12
gates per fixture: source identity, discovery, closure, dialect, frontend capability,
elaboration, synthesis, bounded resources, diagnosis, promotion, provenance, and
cross-stage isolation.

| Run | Gate cells | Safe terminal decisions | Promotions |
|---|---:|---:|---:|
| Initial diagnostic run on unmodified production code | 48/96 | 4/8 | 4/8 |
| Final regression run | **92/96 (95.8%)** | **8/8** | **7/8** |

The initial 48/96 figure was produced before three evaluator/fixture corrections
(development-candidate limit, explicit header roles, and minimal SERV closure), so it is
diagnostic rather than a strict before/after score. The production outcome is
unambiguous: promotion improved from 4/8 to 7/8, and every fixture ended in a safe
terminal decision.

The final campaign was bound to Agent commit
`ff01d5ccd62fa53e167446bcf33dce9911bda288` plus worktree patch digest
`0b3ec6108441ed639c5a0b38d29eee722e189f07c55a5af98dce826f021db08c`.
The acquisition stage completed in 515.5 seconds. The final report is:

`/home/yangao/r2g_frontend_pilot_2026_07_28_ff01d5c_dev01_fixed_run03/reports/frontend_pilot_report.md`

### Confirmed and corrected production defects

1. **Authoritative failure evidence:** acquisition copied the successful
   `1_1_yosys_canonicalize.log` instead of the failing `1_2_yosys.log`. ENET, MMC, and
   AXIS FIFO were therefore labeled low value rather than memory-limited. The terminal
   log is now selected first.
2. **Retry parameter loss:** auto-fix correctly generated
   `SYNTH_MEMORY_MAX_BITS=131072`, but scoped retry reconstruction discarded the field
   and repeated the 4096-bit failure. Retry CSV reconstruction now preserves frontend,
   memory, parameter, variant, and resource-tier fields.
3. **Discovery blind spots:** `src_v` was not a preferred RTL directory, and any
   `$display` rejected a file even when guarded by a synthesis-safe debug macro.
   Discovery now finds all eight expected tops.
4. **Cross-domain learning contamination:** acquisition decisions were written as
   signoff `fix_events`, creating signoff Recipe lifecycle state. They now use
   `acquisition_actions.jsonl`; the signoff planner also rejects acquisition/synthesis
   strategy prefixes.
5. **Missing-frontend disposition:** a missing `slang.so` was retried and then treated as
   source failure. It now terminates as `defer/frontend_tool_unavailable`, preserving the
   candidate without promotion or negative source-quality evidence.

Production tests after these changes passed: 109/109 RTL-acquire tests and 25/25 focused
A/B causal-guard tests.

### Remaining frontend limitation

AHB3-Lite is the only non-promoted fixture. Autonomous discovery finds its top but does
not add the package file stored in a Git submodule to the compilation-unit closure, and
the installed Yosys lacks `slang.so`. Verilator independently accepts the authoritative
closure. The Agent now fails safely and reports the missing capability, so this is no
longer a trust failure, but it remains a capability/yield gap responsible for the final
four failed gates (`CLOSURE`, `SYNTH`, `PROMOTION`, and `PROVENANCE`).

The ORFS memory checker also reports `Total inferred memory bits: 0` while reporting
nonzero memories and 32--40 Kbit largest instances. Recovery uses the independently
reported largest instance and succeeded, but the contradictory ORFS summary should be
fixed before total-memory estimates are used for resource planning.

## 9. Updated Scientific Verdict

The original eight end-to-end held-out fixtures remain the evidence for strict
RTL-to-graph yield. The new frontend cohort explains a major part of the prior yield gap:
the production Agent could reliably execute ORFS-to-graph once a design was admitted, but
unknown RTL admission had weaker discovery, diagnosis, retry propagation, and stage
isolation.

After the focused changes, seven diverse unseen repositories reach provenance-carrying
promotion and the eighth is safely deferred for a real missing frontend. This is strong
regression evidence that the frontend is materially improved. The separately reserved
validation cohort summarized next independently confirms the controlled frontend path,
while also showing that autonomous exact-target discovery and closure are not yet
complete.

## 10. Reserved Frontend Validation Cohort

### Protocol

A second set of eight repositories was reserved before the frontend repairs and remained
untouched during development. After the development cohort reached its final regression
result, this validation cohort was frozen at exact revisions with independently selected
top modules, clocks, ordered closures, include directories, and Icarus/Verilator oracles.

The registry digest was
`56b8499ed279a27fd03c8fbd102460ce87b091f78b58cde5a4e2c00c1856636a`.
The Agent snapshot remained commit
`ff01d5ccd62fa53e167446bcf33dce9911bda288` plus patch digest
`0b3ec6108441ed639c5a0b38d29eee722e189f07c55a5af98dce826f021db08c`.
Production code was not changed after observing the primary validation result.

| Validation fixture | Frontend stress |
|---|---|
| WB2AXIP | Macro-heavy bridge with a transitive skid buffer |
| common_cells | SystemVerilog module and macro-header closure |
| Verilog-6502 | Two-file Verilog CPU |
| RP32 Mouse | Typed, self-contained SystemVerilog CPU |
| HDMI TMDS | Parameterized SystemVerilog encoder |
| ZipCPU | Multi-module CPU with preprocessor-selected implementations |
| CV32E40P | Clocked SystemVerilog divider |
| Caliptra RTL | SystemVerilog AHB block with assertion macros |

### Combined frontend results

| Cohort and run | Gate cells | Safe decisions | Promotions | Interpretation |
|---|---:|---:|---:|---|
| Development, initial diagnostic | 48/96 | 4/8 | 4/8 | Bug-finding run; evaluator fixtures were subsequently corrected |
| Development, final regression | **92/96** | **8/8** | **7/8** | Regression evidence after local production fixes |
| Reserved validation, primary | **91/96** | **8/8** | **8/8** | Untouched-cohort generalization evidence |

All eight validation closures passed the independent frontend oracle, controlled
qualification, mapped-netlist graph probe, promotion, provenance, and isolation checks.
The acquisition stage took 146.44 seconds. This confirms that the repaired qualification,
retry, synthesis, diagnosis, promotion, and provenance paths generalize beyond the
development fixtures.

The result must not be read as 8/8 fully autonomous Internet-RTL conversion. The Pilot
deliberately separates autonomous discovery from controlled qualification. Exact
discovery succeeded for 6/8 validation targets, and exact discovery plus the complete
authoritative closure succeeded for 5/8.

### New confirmed production finding

**P1-FE-VAL-01: macro-indirect dependencies are missing from autonomous closure.**
ZipCPU's `zipcore` was discovered, but its bundle omitted `div.v`, `mpyop.v`, and
`slowmpy.v`. The source instantiates implementations through macros such as
`` `DIVIDE_MODULE`` and `` `MPYOP``; the current source-regex traversal resolves neither
macro substitution nor the resulting dependency chain. Controlled synthesis succeeded
only because the authoritative closure was supplied.

The two other discovery misses require a measurement caveat rather than two immediate
bug claims. The selected CV32E40P divider and Caliptra AHB mux ranked 78/82 and 217/269
under an uncapped diagnostic, below the frozen 64-candidate repository budget. They are
valid submodules, while the discovery policy is designed to favor likely project tops;
`cv32e40p_core` and `caliptra_top` both ranked first. Future cohorts must distinguish
repository-level project-top discovery from exact arbitrary-submodule discovery. The
primary 91/96 score remains unchanged.

### Final frontend conclusion

Across development and untouched validation cohorts, the Agent now makes safe terminal
decisions for 16/16 independently valid fixtures. Controlled promotion reached 15/16;
the only defer was the development AHB3-Lite case with a truthfully unavailable synthesis
frontend. The remaining actionable production gap is compilation-closure reconstruction
across packages, headers, build manifests, and preprocessor-indirect module names.

Detailed validation evidence:

- `2026-07-29-frontend-validation-cohort-analysis.md`
- `2026-07-29-frontend-validation-cohort-remediation-plan.md`
