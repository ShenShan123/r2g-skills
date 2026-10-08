# Phase 1.5: Quality and Recovery Optimization

## Objective

Do not set a repository-count target. Improve the recovery yield, mapped-synthesis evidence, functional labeling, licensing evidence, contamination readiness, and scheduler calibration of the frozen Phase-1 lake without weakening DesignInstance gates.

## Failure audit and repair

Deterministically sample at least 100 `PARSE_FAIL` and 100 `GENERIC_SYNTH_FAIL` candidates, stratified by source language, project context, and diagnostic signature. Preserve every sampled candidate and classify it as `BAD_TOP`, `INCOMPLETE_SOURCE`, `BUILD_CONTEXT_RECOVERABLE`, `PORTABILITY_RECOVERABLE`, `SYNTH_COMPAT_RECOVERABLE`, `TRUE_BROKEN_RTL`, or `UNSUPPORTED_TOOLCHAIN`.

Automatic adjudication is allowed and preferred. Classification is not correctness evidence. The system must support abstention, preserve diagnostic evidence, and require the normal parse/elaboration/synthesis/equivalence/functional gates before any recovered design is published.

Map evidence-supported recoverable classes to R1, R2, or R3 and permit bounded automatic repair. Record `ABSTAIN` instead of forcing a class when diagnostic evidence is insufficient. Preserve patches, original sources, classifier provenance, confidence, and diagnostics. Never optimize pass rate by editing design behavior or accepting a questionable top. Report automatic suspected-recoverable classifications as a triage rate, never as recovery yield; report candidate recovery and publication yield only from completed validation evidence.

Finalize every `RECOVERED_CANDIDATE` with exactly one terminal disposition: `PUBLISH`, `QUARANTINE`, or `REJECT`. For R1, record that every original source-unit hash is unchanged, set equivalence to `NOT_APPLICABLE` with reason `SOURCE_RTL_UNCHANGED_BUILD_RECOVERY`, and require parse, elaboration, structural, and generic-synthesis PASS. For R2/R3, require deterministic transformation evidence and applicable `rtl_equiv_v1`; quarantine any candidate with unavailable equivalence plus structural uncertainty or any possible behavioral change. Never close a phase with an ambiguous recovered candidate.

## Mapping validation cohort

Select 100–200 DesignFamilies with deterministic stratification across resource class, language mix, and `rtl_function_ontology_v2`. Map one representative per family using a content-addressed Nangate45 liberty and a versioned Yosys/ABC script. Save the mapped netlist, JSON structure, log, cell inventory, unknown/internal-cell count, runtime, tool version, liberty hash, and outcome.

Keep `SYNTH_GENERIC_ONLY` until an individual design passes mapping. Report `SYNTH_COMPLETE`, `SYNTH_MACRO_PRESERVED`, or mapping failure only from recorded artifacts; cohort success never promotes unsampled designs.

## Functional ontology and discovery

Use `rtl_function_ontology_v2` with specific categories including arithmetic, codec, video/image, networking, storage, debug, clock/reset, protocol bridge, signal processing, sensor, timer, interrupt, memory controller, cache, PCIe, NoC, accelerator, CPU, and SoC. Persist label, confidence, and matched evidence. Use `misc_ip` only as an explicit low-confidence residual, never silently alias it to a diversity category.

Recalibrate the scheduler from Phase-1 yield without permanently disabling a source on a small sample. Increase deficit weights for large hierarchical RTL, XLARGE, multi-clock, CPU/SoC, NoC, cache, memory controller, DDR, PCIe, and complex accelerators. Decrease simple counter/FIFO/peripheral saturation and generic low-yield keyword search. Continue measuring dependency, README-reference, organization, ecosystem, and provider-specific yield.

## License evidence

Run `rtl_license_evidence_v1` over license/copying/notice files, provider metadata, README license sections, SPDX identifiers, and per-file headers. Preserve evidence paths, detected identifiers, confidence, and these resolution states: `LICENSE_FILE_ABSENT`, `LICENSE_METADATA_ONLY`, `LICENSE_CONFLICT`, `PER_FILE_LICENSE_MIXED`, and `LICENSE_UNRESOLVED`.

Only change ReleaseEligibility from strong, consistent evidence. Never convert an unknown or conflicting license into public-export eligibility merely to improve corpus yield.

## Benchmark registry

Build `rtl_benchmark_registry_v1` from immutable, explicitly identified sources and freeze the benchmarks actually used for evaluation into a named `rtl_benchmark_profile_vN`. Gold/Premium means a contamination PASS against that exact profile; adding a benchmark requires a new profile and new audit manifest, never a silent semantic change. Record task count, source-artifact count, fingerprint count by type, source URL/path, immutable revision or snapshot hash, benchmark version, and registry state. A required profile entry that is pending, empty, or ambiguous blocks that profile; benchmarks explicitly outside the profile do not permanently block it. Record `TASK_SPEC_ONLY`, `NOT_APPLICABLE_TO_PROFILE`, and `AMBIGUOUS_SOURCE` with reasons and never count them as PASS.

After every registry update, rerun contamination audit over all existing designs; acquisition and generic synthesis do not need to rerun.

## Counter continuity and completion

Recover historical candidate counters from immutable success/failure ledgers only when the reconstruction is unambiguous. Record the correction. Otherwise freeze the legacy gap permanently and require all new-schema runs to have zero counter residual.

Phase 1.5 reporting must include sampled failure-class and abstention distributions, triage suspected-recoverable rate, repair and publication outcomes by R-level, mapping cohort coverage and pass/failure classes, ontology residual, license-resolution transitions, benchmark registry readiness, scheduler calibration, and counter residual. Human confirmation is optional evidence and is never a mandatory completion gate.

Close Phase 1.5 only when every recovered candidate has a terminal disposition, the bounded mapping retry ledger is complete, at least 20 representative mixed-language VHDL failures have been adjudicated, the named benchmark profile is ready and its audit has been applied to every published design, the historical counter residual is zero, and all publication invariants and tests pass. Persistent mapping timeout or diagnosed lowering gaps enter the engineering backlog and do not trigger unbounded retries. Mark Phase 2 active independently of background frontend, license, and future benchmark-profile work.
