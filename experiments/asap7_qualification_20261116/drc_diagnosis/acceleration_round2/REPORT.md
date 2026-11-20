# ASAP7 Second-Round DRC Acceleration Experiments

Compiled: 2026-11-17, GMT+8. Times are wall-clock measurements in preserved invocation receipts.

**Individual AES ACTIVE.W.1 and ACTIVE.W.2 checks both complete in about six seconds with zero results; the full rule combination remains incomplete.** Individual checks provide diagnostic evidence, not full 334-check signoff or validated partitioned equivalence.

| Experiment | Input | Completed checks | Wall time | Outcome |
|---|---|---:|---:|---|
| Original AES + hier/turbo4/hyper | Original DRC GDS without FIN/GATE merging | 330/334 | 120.021 s | Timeout; ACTIVE.AUX.3, ACTIVE.S.1, ACTIVE.W.1, ACTIVE.W.2 unfinished |
| GCD, select ACTIVE.W.1/W.2 | Saved GCD input | 2/2 | 6.365 s | Both zero results, normal exit |
| AES, select ACTIVE.W.1 | Original AES DRC GDS | 1/1 | 6.338 s | Zero results, normal exit |
| AES, select ACTIVE.W.2 | Identical original AES DRC GDS | 1/1 | 5.981 s | Zero results, normal exit |

The full AES attempt reports 604 expanded partial results: FIN.S.1=447, V0.M1.AUX.3=56, M1.S.6=100, M5.S.5=1. This is not a final count. FIN.S.1 requires hierarchy-representation review; it cannot be compared directly with the 157 partial results from FIN/GATE-merged input as though execution scope and representation were identical.

Every run retains the official deck, full GDS, ALL result limit, and four zero metal offsets. Individual checks use DRC SELECT CHECK without changing rule algorithms; their scope is explicitly smaller than complete signoff. [Runner](run_probe.py).

Raw evidence:

- [Full original AES](../../calibre_evidence/accel2_aes_original_hyper/drc/invocation.json)
- [GCD width pair](../../calibre_evidence/accel2_gcd_widths/drc/invocation.json)
- [AES W.1 receipt](../../calibre_evidence/accel2_aes_w1/drc/invocation.json), [report](../../calibre_evidence/accel2_aes_w1/drc/drc_report)
- [AES W.2 receipt](../../calibre_evidence/accel2_aes_w2/drc/invocation.json), [report](../../calibre_evidence/accel2_aes_w2/drc/drc_report)

A complete partitioned experiment must finish every group, cover exactly the 334 rule names, and match marker geometry and multiplicity on completed clean and violating controls. Timeout logs cannot substitute for finished groups. Fast individual rules justify investigating combined execution, but do not establish the internal root cause.

ACTIVE shape reuse remains analysis only: 59081 AES polygons reduce to 54 translation-normalized patterns. No recell GDS has been constructed, verified, or checked. See [geometry statistics](../../reference_diagnosis/active_geometry).
