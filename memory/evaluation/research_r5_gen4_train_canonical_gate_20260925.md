# R5 generation-4 canonical TRAIN preflight

The gen3 TRAIN corpus is explicitly reused as researcher-assisted TRAIN, but
gen4 has a distinct acquisition campaign, scoped measurement contract, v6
action/profile, oracle-instance witness version, and canonical record IDs.
This does not create a new source lineage or an unseen transfer task.

`tehm.adapters.research_r5_rtl_scoped_v4` regenerates the pinned TRAIN
candidate with the v6 action and records the new shared contract under
`rtl.skid.temp_payload.v6.dev`. The restricted RAM learner replay dispatch
recognizes only the exact `tehm-r5-rtl-train-scoped-v4` version. The v3
adapter and frozen gen3 M0 script were not changed.

The definitive preflight receipt is
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/measurement-v4-train-ram-r2.json`.
It passed 25/25 cases and cold-replayed byte-equivalently in an independent
process. Coverage includes four distinct TRAIN transitions, canonical and
persisted acquisition replay, wrong-campaign and v3/v4 cross-version
rejection, held-out membership rejection, two L2 controlled pairs, two-source
L3 replicated effect, exact shared-contract Knowledge authority, and the
v6 candidate's target/preservation PASS with faulted-source target FAIL.
Receipt file SHA-256:
`8b762d4c6e9b4fa62d802d1679a0a0a41d713ddfdd3223a2dde15e051739766c`.
The shared contract digest is
`sha256:b036d76b5e5018eb5169da7942f90eb6fdc65e28dd6048a4c16ae7180ba11a3c`;
RAM Knowledge object ID is `mk_71bb306601feb5c73e52@1`.

The v4 adapter SHA-256 is
`d4dee87293b8e51bc01f5dbe2bcfd0f381aecde944cb7ce6f49e6b08f47fcdb3`;
the check module SHA-256 is
`e65c2cdf99a96f193e8e38cd599665cea43f3bf32053b1ff98ff1701c967f612`.
The first r1 preflight passed but had a weaker oracle assertion; it remains
an exploratory receipt superseded by r2. The fixture clock is fixed only in
the RAM conformance test for byte-exact comparison; production time behavior
is unchanged.

No M−/M+/Mremove bundle exists for gen4 yet. This preflight is a prerequisite
for building a new clean software epoch, not proof of Memory attribution,
online evolution, or independent transfer. No model/API calls or GitHub push
were made.
