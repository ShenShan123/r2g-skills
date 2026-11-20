# ASAP7 Official DRC Performance and Equivalence Audit

**The 60 s -hyper probe changes scheduling but does not clear the ACTIVE-width bottleneck.** AES completes 328/334 checks; ACTIVE.AUX.3, ACTIVE.S.1, ACTIVE.W.1, ACTIVE.W.2, M4.AUX.4, and M5.AUX.4 remain unfinished. The 977/1010 operation count at tool time 49 s is reordered progress, not proof of crossing the original block. GCD completes all 334 checks with zero results in 7.5656 s.

Completed AES checks report V0.M1.AUX.3=56, M1.S.6=100, M5.S.5=1: 157 expanded partial results. This is not the final total, and markers have not been individually verified. No physical flow was rerun. See [rule audit](rule_level_audit.json), [corrected AES receipt](../../calibre_evidence/asap7_accel_hyper_aes_20261116/drc/invocation.json), and [GCD full report](../../calibre_evidence/asap7_accel_hyper_gcd_20261116/drc/drc_report).

| Control | Input and rules | Rule-level outcome | Wall time |
|---|---|---|---:|
| AES baseline | Original probe GDS/deck, four threads, no -hyper | ACTIVE-width shared block unfinished | 600.015 s timeout |
| AES short probe | Identical GDS, runset bytes, deck, four threads; add only -hyper | 328/334; ACTIVE.W.1/W.2 unfinished | 60.023 s timeout |
| GCD baseline | FIN-union input, no -hyper | All 334, zero results | 9.52 s |
| GCD short control | Identical GDS/runset/deck, four threads; add only -hyper | All 334, zero results | 7.566 s |

ACTIVE.W.1/W.2 share encrypted derived block I99; there are no independent timings for the two rules. GCD executes it from tool T3 to T4, about one second. AES starts it at T6 and it remains unfinished at the last T49 log: an observed lower bound of 43 seconds. The earlier 54-second estimate mixed wall and tool timing origins and is superseded by this directly supported bound.

Original time -v CPU/RSS records may exclude independent HDB workers; they are not whole-job CPU or peak-memory measurements. The misleading passed_operation_113 field was removed from receipts and replaced by noncomparable-operation-index, completed/missing rule lists, and resource-scope fields. Original receipts remain as invocation.original.json. No additional 600 s run was started after the subsequently withdrawn instruction. No Calibre/HDB workers remained at the end. Only Calibre 2025.1_16.10 is installed; see [environment check](environment_check.txt).

SHA256 of the sorted missing-rule names, UTF-8 with one name per line and a final newline: `5e670c845fb4c7dbaed1cf457f5b5b80c6c4c471439ba332386366528ecb65db`.

## Bottleneck evidence

**The evidence does not establish FIN geometry as the root cause.** Across 18 archived DRC logs, eight AES/Pico timeouts occur after operation 86/1010. Operation 86 completes ACTIVE.FIN.EX.1 with zero results. In ten completed GCD/UART controls, the next encrypted block advances progress to 113 and immediately completes ACTIVE.W.1/W.2. This identifies a 27-operation ACTIVE-width-related derived block, without revealing the expensive internal operator.

[Log audit JSON](log_evidence.json) preserves hashes, operation boundaries, timing, CPU/RSS, and paths. The [collector](collect_log_evidence.py) reads normal output only, not encrypted rule contents.

| Evidence | Observation | Supported interpretation |
|---|---|---|
| [GCD log](../../calibre_evidence/gcd_pin_window_v2/drc/run.log), lines 2005–2044 | 86 → FIN check complete → encrypted block → 113 → width checks | Localization boundary is after FIN and within width derivation |
| [UART log](../../multi_uart_strict/signoff/drc/run.log), lines 2007–2046 | Same sequence; block about 1 s | The block can complete on small designs |
| [AES hierarchical candidate](../../calibre_evidence/multi_aes_feol_hier/drc/run.log), lines 2228–2261 | Reaches 86 at 10 s; times out at 600 s; 110% CPU, 147 MiB RSS, no swaps | Computation stalls on this input; no memory-exhaustion evidence |
| Five AES and three Pico attempts | Reach 86 in 9–15 s; 99–110% CPU, 137–151 MiB RSS, zero swaps | No evidence that more RAM, faster reading, or more threads alone fixes it |
| FIN/GATE exact normalization | Per-layer XOR=0; flattening, unions, and retained hierarchy all fail to clear the block | These tested representation changes did not solve it |

The [official DRM text](../../reference_diagnosis/asap7_drm_201207a.txt), lines 914–926, defines ACTIVE.W.1 as minimum vertical width 27 nm and ACTIVE.W.2 as width in integer multiples of 27 nm. AES has 59081 ACTIVE polygons before and after exact union, with bbox heights 27/54/81 nm. There is no FIN-like polygon-count reduction. [Statistics](../../multi_aes/reports/feol_shape_counts.json). Bounding boxes do not prove the full official width semantics and cannot justify skipping rules.

The deck contains one encrypted block; its 40 nonempty external lines are copyright/version/use notices. The readable wrapper specifies ALL results and includes the official file. No ACTIVE algorithm is available for direct rewriting. Installed usage supports -hyper; Siemens describes hyperscaling and shared/distributed parallelism in its [nmDRC factsheet](https://static.sw.cdn.siemens.com/siemens-disw-assets/public/1e0BnjDXlgGEezdKWH3Wbn/en-US/Siemens-SW-Calibre-nmDRC-FS-82858-C2.pdf). This supports bounded experimentation, not guaranteed acceleration.

The [probe](run_hyper_probe.py) retains GDS/deck hashes, identical wrapper bytes, four threads, and ALL results, adding only -hyper. AES uses the retained-hierarchy FIN/GATE input; GCD uses the accepted FIN-union control. Each run is bounded to 60 s; the production runner is unchanged.

Other platforms demonstrate geometry/semantics-preserving algorithms and scheduling, but arbitrary rewrites cannot be certified against this encrypted operator. A community KLayout ACTIVE.W.2 implementation enumerating 1–12 bbox-height multiples does not prove equivalence for general polygons or context. Recon, grayboxing, selected checks, and spatial clipping change coverage and cannot replace this complete signoff. See [Siemens Recon documentation](https://blogs.sw.siemens.com/calibre/2024/06/05/speeding-up-early-design-rule-checking-with-calibre-nmdrc-recon/).

No verified public parameter or readable rewrite point for this block was found. PDK validation records list Calibre 2017.4; only 2025.1 is installed. The documented xACT incompatibility does not establish a DRC-version cause. The probe did not clear the bottleneck. A minimal width-rule reproduction and support investigation are justified; repeated FIN/GATE variants are not supported by current evidence. Later rule-selection diagnostics are recorded separately in [round two](../acceleration_round2/REPORT.md).
