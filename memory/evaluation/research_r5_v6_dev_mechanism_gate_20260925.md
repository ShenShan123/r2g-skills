# R5 v6 bounded drain-side DEV mechanism gate

Revision 5 §18 directs an execution-support miss into DEV, while keeping the
observed PILOT_TRANSFER task in its original denominator. The gen3 negative
pilot remains unchanged at frozen software `fccdca6a5fa8664119652f4403903b6dfe24dcfa`:
its SkillSurf capture-side fault yielded M−/M+/Mremove target FAIL/FAIL/FAIL.
The v5 binder rejected that source before action. This report does **not**
relabel that fault as a new unseen transfer or claim a gen3 Memory gain.

## Mechanism split from actual TRAIN evidence

The two gen3 TRAIN repairs (`axis_register`, `ZipCPU/skidbuffer`) restore the
buffered payload at an output/drain obligation. The negative pilot's
SkillSurf fault corrupts the *capture* into the buffer. A shared phrase such
as “temporary payload” is not sufficient evidence that the old Asset can
repair the reverse dataflow slot. In particular, changing the old binder to
rewrite the capture side under the same Asset would confound new software
capability with Memory attribution.

For a bounded DEV continuation, a distinct one-line *drain-side* SkillSurf
probe was declared in
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/skillsurf-drain-v6-r1/experiment-card.md`.
It is observed development material, not PILOT_TRANSFER or TRAIN. Its native
clean/fault target and preservation outcomes are PASS/FAIL/PASS/PASS;
four-arm cold receipt SHA-256
`1f61a7718d9506c37b9779ebdf27f7fcf55ae456f3d48f64064f315b740bb542`.

## New bounded source-only binder

`tehm.evaluation.research_r5_skid_binding_v6` is a *new* DEV contract,
not an edit to v5. It retains exact v5 delegation for the two existing TRAIN
shapes and observed v5 DEV shape. Its added branch accepts only `WIDTH=8`
state-machine skid RTL with a unique guarded capture of input payload into
one buffer and a unique `FULL`-state output drain that erroneously reads the
current input. The replacement register is inferred from that guarded
capture relationship, not supplied by target ID, filename, oracle, or a
mutation coordinate. Ambiguous, unsupported, no-target, stale/tampered, and
answer-access cases fail closed. The proof is syntactic, not functional.

The definitive v6 conformance receipt has 33/33 checks, including
alpha-renaming consistency, an unrelated no-target module, duplicate writes,
extra state writes, capture-side non-misbinding, old TRAIN delegation, and
file/network access denial. Receipt SHA-256:
`06e269b3571f714c6460c8703acf3233ed7d1fd7c887336afcd6517330cbadbd`.
The binder code and check-script SHA-256 values are respectively
`3e04b2f247108314c39ce89b656ef1eb57e40738959a79e11100ec11d0d13a1b`
and `8175540d2a7d6d3348a90a91b7645998ba0049f0d63c0170edd15056ed2b4a0d`.
The frozen v5 DEV regression reverified 32/32 after this addition.

The *generated* v6 candidate, staged separately from upstream clean RTL,
was freshly compiled with the native target and separate preservation tests:
both PASS. Native receipt SHA-256
`009330677b0c62a94c4fcd8e581bf32fe7c95699149d2152f2553fb0af2bd306`.
This is a researcher-assisted DEV result on an observed source, not an
independent transfer success. Earlier r1/r2 DEV binding/candidate outputs
were exploratory; r3 is definitive for this code.

## Remaining before another transfer

This binder is not yet an integrated registered Asset/action, and no v6
TRAIN Memory, gen4 M−/M+/Mremove, physical source-only handoff, transform-only
control, or fresh independent target has been frozen. R5 GD is therefore
DEV conformance only; GM/GT/GA for a new v6 generation remain open.

If integration is pursued, use a new profile and software/Memory epoch,
replay actual TRAIN evidence under the existing core authority gates, and
record the binder/primitive expansion separately from delta-Memory effects.
Only after that freeze should a *new* target and target/preservation oracle
be preregistered without selecting on TEHM success. The observed SkillSurf
source and alpha-renaming fixtures cannot count as new source-lineage units.
No real model/API calls or GitHub push were made.
