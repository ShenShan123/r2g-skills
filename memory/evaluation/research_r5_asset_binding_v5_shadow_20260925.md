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

The initial integration kept v5 authority closed until a v5-specific
raw-TRAIN lineage and rollback verifier could be added. That verifier is now
separate from v4 and requires the v3-profile `asset-rollback-v5-r2` evidence,
exact TRAIN row metadata, and cold replay. A caller-supplied `PASS`, a forged
rollback boolean, or a v4 rollback version cannot pass it; ordinary promotion
without strict authority is rejected. The RAM-only v5 authority check does
not itself construct M+/Mremove or claim an independent target, answer-free
transfer, or delta attribution. A new software/Memory epoch remains required.
