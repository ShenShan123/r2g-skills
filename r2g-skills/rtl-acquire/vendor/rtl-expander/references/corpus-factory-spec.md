# RTL Corpus Factory Policy

This reference is the compact normative form of the user-supplied V1.0 corpus specification. Preserve these invariants when extending scripts or schemas.

## 1. Objective and units

Maximize unique, high-quality, synthesis-valid RTL design families plus functional, structural, scale, and source diversity. Do not maximize raw files, modules, or repositories. Use these units:

- `Repository`: immutable source revision and provenance.
- `File`: one source artifact.
- `Module`: language unit; not automatically an independent design.
- `DesignInstance`: top, complete build configuration, dependency closure, language views, validation, synthesis, evidence, dedup, and quality.
- `DesignFamily`: IP lineage shared by forks, mirrors, minor revisions, renames, and parameter variants.
- `TrainingView`: module, subhierarchy, top hierarchy, or full design derived from an instance.

An independent instance requires a top, complete I/O and dependency closure, legal elaboration, and independent synthesis. Do not admit testbench, formal harness, simulation wrapper, or verification tops.

## 2. Required design record

Record identifiers (`design_id`, `family_id`, `variant_id`, `revision_id`); repository/provider/URL/commit/source paths/license/timestamp; top/sources/includes/defines/packages/parameters/language/dependency closure; original/canonical source and transformation metadata; ports/clocks/resets/memories/macros/hierarchy/statistics; parse/elaboration/structural checks; repair level/rules/patch/equivalence; generic and mapped synthesis; functional assets/confidence; dedup fingerprints/family cluster; benchmark contamination; engineering quality, functional confidence, training value, tier, and flags.

## 3. Discovery and repository safety

Discover independently from public forges, FuseSoC/Bender/IP-XACT ecosystems, hardware/IP categories, organizations/siblings/dependencies, and historical sources. Maintain repository states from `DISCOVERED` through `SYNTH_VALID`, or specific terminal states such as `NO_RTL`, `DUPLICATE`, `LICENSE_BLOCKED`, and classified `FAILED`.

Search a hardware ontology covering Verilog/SV/VHDL; processors; AXI/AHB/APB/Wishbone/PCIe/USB/Ethernet/SPI/I2C/UART/DDR; DSP/FFT/FIR/accelerators; memory/cache/DMA; NoC/crossbar/bridge; crypto; and control. Reweight toward diversity gaps and record yield in new valid/Gold families per repository and per CPU hour.

Treat every external repository as untrusted. Never execute its Makefiles, shell/Python scripts, installers, binaries, hooks, or download steps unless separately allowlisted. Controlled parsing, conversion, lint, equivalence, and Yosys are allowed with no-network and resource/time/output limits.

## 4. Recovery and source frontend

Statically inspect `.core`, Bender manifests, file lists, Yosys/Tcl metadata, project manifests, FPGA configs, Makefile metadata, README, and docs. Rank top evidence: explicit synthesis top, structured target, project config, file list, instantiation-DAG root, docs, project naming, controlled elaboration, then heuristic name.

Resolve module/interface/package/include/macro/typedef/parameter/generated-header/external-IP closure. Record resolved, unresolved, and external dependencies. Use official configurations before RTL defaults; only create bounded, meaningful, nonredundant parameter variants.

Preserve original human-written RTL. Produce canonical Verilog for synthesis without replacing the original. Classify origin separately from representation. Grade conversions T0 native; T1 deterministic plus both parses; T2 equivalence verified; T3 target-only validation; T4 heuristic/uncertain.

## 5. Validation, repair, and macros

Run Q0 static, Q1 parse, Q2 elaborate, Q3 structural integrity. Detect encrypted/unsupported/netlist/testbench sources, missing include, unresolved hierarchy, multiple drivers, loops, empty logic, width errors, and unsupported constructs.

Repair levels:

- R0: none.
- R1: build ordering, include/define/package/library/top/parameter/file-list recovery; no RTL functional text edits.
- R2: deterministic portability normalization.
- R3: controlled synthesis-compatibility lowering or isolation.
- R4: very small heuristic repair; never accepted as correct merely because an LLM proposed it or Yosys passed.

Never automatically alter FSM transitions, state/reset/clock-domain behavior, algorithms, protocol logic, memory semantics, priorities, or behaviorally relevant signedness/width without equivalence proof. Save repair hashes, patch, reason, tool/version, validation and equivalence (`PASS`, `FAIL`, `UNKNOWN`, `UNAVAILABLE`). Bound search to attempts 0–4.

Allow and prefer automatic failure adjudication and repair. Classification is routing evidence, not correctness evidence. Support explicit `ABSTAIN`, preserve complete diagnostics and classifier provenance, and require the normal parse, elaboration, synthesis, applicable equivalence, and functional gates before publishing any recovered design. Human confirmation may add evidence but is not mandatory.

Classify macros as preprocessor, memory, hard IP, vendor primitive, user blackbox, generated, or unknown. Known macros may be preserved and do not inherently reduce quality. Unknown unresolved components do.

## 6. Synthesis and functional confidence

Run a canonical generic Yosys flow and optional canonical mapping target such as Nangate45. Save command, tool versions, input manifest, logs, runtime, exit status, netlists, and statistics. Use synthesis classes `SYNTH_COMPLETE`, `SYNTH_MACRO_PRESERVED`, `SYNTH_GENERIC_ONLY`, `SYNTH_PARTIAL`, `SYNTH_FAIL`; also report mapping completeness, standard cells, preserved macros, and unknown cells.

Statically recover testbenches, cocotb, vectors, SVA/formal properties, reference/golden models, CI tests, and expected outputs. Execute only allowlisted assets in a sandbox. Functional confidence is independent: F0 unknown, F1 assets present, F2 official simulation pass, F3 formal pass, F4 reference equivalence pass.

## 7. Deduplication and contamination

Perform repository lineage, exact source closure, normalized RTL, AST, hierarchy, generic-netlist, mapped-netlist, and family clustering. Audit exact/normalized/AST/hierarchy/netlist/provenance/spec overlap against a versioned benchmark registry (including VerilogEval, HDLBits, RTLLM, VerilogBench, internal and future benchmarks). Keep matches in the lake, mark them, and exclude them from benchmark-facing exports.

## 8. Scores and hard gates

EngineeringQuality 0–100: provenance 15, completeness 20, transformation integrity 15, RTL validation 15, synthesis quality 25, reproducibility 10. Grades: A+ 95–100, A 90–94, B+ 85–89, B 80–84, C 65–79, D 50–64, F below 50.

TrainingValue 0–100: novelty 20, functional rarity 20, structural richness 20, scale/hierarchy 15, documentation 10, verification assets 10, readability 5. Large does not mean high engineering quality.

Hard gates override scores: parse failure is not training-eligible; elaboration failure is not synthesis-valid; unknown dependency/blackbox blocks highest Gold; R4 without evidence blocks highest generation tier; contamination blocks formal benchmark-facing export.

Tiers are `TRAINING_PREMIUM`, `TRAINING_GOLD`, `TRAINING_SILVER`, `TRAINING_AUXILIARY`, `TRAINING_EXCLUDED`. Premium requires functional evidence and documentation in addition to clean human-written, complete, synthesis-valid, uncontaminated provenance. Gold does not require a testbench. Generated/HLS, module-only, historical, weak-provenance, and R4 assets normally remain Auxiliary.

## 9. Publication and snapshots

Keep repositories, recovered designs, original/canonical/repaired RTL, generic/mapped synthesis, functional assets, documentation, training views, manifests, quarantine, and rejected areas. Publish repository/all-design/family/repair/contamination ledgers; synthesis-class manifests; tier manifests; and quality, functional, failure, discovery summaries.

Every snapshot freezes source revisions/hashes/family IDs, all tool/converter/repair versions, Yosys/ABC and liberty hashes, synthesis scripts, score schema, benchmark registry, and training-view generator. Report discovery, file/language, recovered instance/family, validation, synthesis, repair, functional, training-tier, quality distribution, dedup, contamination, and size metrics. Retain all potentially valuable data; filter at Training Export, not Data Lake ingestion.

Compute content hashes once when immutable source, static-design, synthesis,
staging, plan, contract, or snapshot bytes are created. Subsequent factory
operations consume the recorded digest and ledger generation. Whole-corpus byte
rehashing is an explicit integrity-audit mode, never a per-round prerequisite.

## 10. Family identity and dataset split

Run `DEDUP -> FAMILY CLUSTER -> SPLIT ASSIGNMENT -> CONTAMINATION AUDIT -> TRAINING VIEWS`. Use `rtl_family_v1` and store `family_id`, confidence (`EXACT`, `HIGH`, `PROBABLE`, `UNRESOLVED`), and evidence from repository/upstream/fork lineage, exact and normalized RTL, AST, hierarchy, generic netlist, and mapped structure. Never compare family counts without the family schema.

Use persisted `rtl_split_v1` assignments with `rtl_split_group_closure_v1`. Every record must contain `split`, `split_group_id`, `split_schema`, `split_group_schema`, and grouping evidence. A DesignFamily may occur in exactly one of train/val/test, but `DesignFamily` is not `SplitGroup`: one SplitGroup may contain multiple families.

Construct SplitGroups as connected components after family clustering. Add a hard union edge when two DesignInstances share an exact RTL source-unit content hash, when one published top is an ancestor/descendant of another within the same immutable repository revision, or when both belong to the same tightly coupled recovered project target (`repository revision + project key`). Also include fork, mirror, and upstream lineage; strict evaluation may union the entire source organization. This means a SoC top and independently recovered CPU/UART/SPI families in its source or hierarchy closure remain separate families for the family KPI but always receive one split as a group.

Scope hierarchy-name matching by repository revision; never join unrelated repositories only because they contain common names such as `top`, `uart`, or `fifo`. Use per-file cryptographic hashes for source-closure evidence. Persist family membership and grouping evidence so later arrivals join the frozen component deterministically.

Freeze assignments in snapshots; a later run may add new groups but must not silently reassign an existing split. Same-split frozen components may be consolidated with an explicit `merged_from` audit trail. Normalize every raw cross-split conflict into a maximal transitive component before authorization. An exact train/val component may use the versioned automatic policy that promotes the whole component to val. A test-boundary component requires a new profile and defaults to hard stop; automatic rollover is permitted only under a separately authored `CAMPAIGN_INTERNAL` no-external-consumption contract and a zero-consumer audit. Unknown splits, incomplete or overlapping authorization, member loss, lineage cycles, and nonunique canonical targets always hard-fail before manifests change.

## 11. Mixed-language closure

Represent language per source unit, not per repository. Store `source_languages[]` plus `source_units[]` containing path, language, and hash. Recover a mixed Verilog/SystemVerilog/VHDL closure, run the proper frontend per file, and build a canonical elaboration representation followed by canonical Verilog synthesis view. Preserve origin for every source unit and transformation/equivalence evidence. Unsupported mixed-language direction remains a classified frontend failure; never discard the original closure or pretend it is single-language.

## 12. Documentation and semantic facts

Run `FUNCTIONAL EVIDENCE -> DOCUMENT/SPEC RECOVERY -> MACHINE FACT EXTRACTION -> DEDUP`. Snapshot README, docs/spec directories, register maps, protocol/interface documentation, module/port comments, and related design text at acquisition time. Content-address or revision-scope documentation so many designs in one repository do not duplicate it.

Write versioned `semantic_facts.json` for every published design. Include ports/directions/widths, parameters, clocks, resets, memories, interfaces, child/dependency modules, FSM candidates, arithmetic operations, hierarchy statistics, and RTL size. Treat these as machine-derived facts, not an unverifiable natural-language specification.

## 13. Release eligibility

Keep release policy independent from EngineeringQuality. Use license status `PERMISSIVE_CONFIRMED`, `COPYLEFT_CONFIRMED`, `RESEARCH_ONLY`, `UNKNOWN`, or `INCOMPATIBLE`; use release policy `PUBLIC_EXPORT_ALLOWED`, `INTERNAL_TRAINING_ONLY`, `QUARANTINE`, or `EXCLUDED`. A technically excellent design with unknown license may retain a high EngineeringQuality while remaining in release quarantine.

## 14. Idempotency, resume, locking, and resources

Define a run key from repository revision, pipeline schema, converter/tool versions, repair schema, synthesis schema, and effective build configuration. Repeating the same run key must return cached artifacts without creating a second object. Persist stage states for `ACQUIRED`, `RECOVERED`, `FRONTEND_DONE`, `VALIDATED`, `REPAIRED`, `SYNTH_DONE`, `DEDUP_DONE`, `SCORED`, and `PUBLISHED`; completed repository revisions must survive process/server crashes.

Use a nonblocking per-run-key worker lock and a central manifest lock. Let workers emit immutable staged artifacts; update shared JSONL ledgers through temporary files plus atomic rename. Never let concurrent workers append directly to central manifests. Classify resources as `TINY`, `SMALL`, `MEDIUM`, `LARGE`, or `XLARGE` and select bounded CPU/RAM/time policies accordingly.

## 15. Equivalence semantics and deterministic value scoring

Version equivalence as `rtl_equiv_v1`. Every result must specify combinational or sequential mode, parameter assumptions, blackbox matching, macro abstraction, and reset assumptions. `PASS` values from different modes or assumptions are not interchangeable.

Keep TrainingValue rules deterministic and versioned. Compute its components from observable rarity categories, family novelty, hierarchy/module/FSM/memory/arithmetic features, RTL size, recovered documentation, verification assets, and source readability. Calibrate thresholds after empirical corpus analysis by introducing a new schema; never silently redefine old scores.

## 16. Top-recovery correctness

Partition monorepositories into build-target or project boundaries before parsing names or building DAGs. Within each boundary, rank top evidence as explicit synthesis top, structured manifest target, project/file list, local DAG root, documentation hint, project-name match, then generic naming. Reject testbench/formal/simulation modules using combined path, name, zero-I/O, delay/system-task, and harness evidence. Detect duplicate definitions instead of selecting an arbitrary file.

Treat heuristic ranking only as candidate generation. Validate each candidate with its recovered source order, include/define/package context, dependency closure, and controlled elaboration. Publish it as a DesignInstance only after elaboration/structural validation succeeds; write failed candidates to the top-recovery failure ledger and exclude them from the family KPI.
