# R5 v5 shadow Asset integration (DEV gate)

The v5 source-only binder now has its own shadow action domain, immutable
binding template, exact public-context/source replay, candidate proof
requirement, and registered Asset binding route. It extends the two known TRAIN
shapes with the already-observed `axis_skid` DEV shape; it does not change the
frozen v4 Asset or generation-2 M0. The v5 action re-derives its witness at
execution and performs one RTL RHS edit. Static eligibility is not functional
correctness.

`python3 -m tehm.evaluation.research_r5_asset_binding_v5_checks` is a RAM-only
check over the two original TRAIN sources and the observed `axis_skid` fault.
It checks exact source copies, one edit, stale/tampered payload rejection,
missing context, and proofless candidate rejection. It creates no persistent
Memory and runs no new target oracle. The existing v5 binder conformance
receipt remains independently verifiable with
`python3 -m tehm.evaluation.research_r5_skid_binding_v5_checks --verify
/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/axis-skid-v5-candidate-r1`.

The strict v5 Asset authority remains deliberately closed: no v5-specific
raw-TRAIN lineage, rollback, and row-metadata verifier has been admitted. A
caller-supplied `PASS` or rollback boolean cannot pass `cross_lineage_verified`
or `rollback_verified`; ordinary promotion is also rejected. This gate must be
replaced only by raw-replayed v5 evidence under a new software/Memory epoch,
never by inheriting the v4 authority receipt. No v5 M+/Mremove, independent
target, answer-free transfer, or delta attribution is claimed here.
