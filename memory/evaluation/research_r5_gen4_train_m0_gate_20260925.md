# R5 gen4 TRAIN M0: three cold-verified read-only views

Revision 5 R5-4 has a new, researcher-assisted TRAIN Memory generation under
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot`. The frozen TEHM software commit
is `981849267118e3163904ec6b77c0d8f726f0888e`; its spec
`epochs/r5-gen4-m0-v1.json` has SHA-256
`90cdd475fa98bffe64d3771d69dc46604c1edf59776c1bf2313167ef1004c89f`.
The software lock covers all 330 current TEHM Python files and
`memory/contracts.py`; the verifier rejects a truncated file inventory.
No model/API calls, online update, production promotion, or held-out target
execution occurred.

The actual output is `memory/r5-rtl-gen4-m0-v1`. Its build report has SHA-256
`12a74441b3176109dfba1fd7c9cccfe925f92dc1ae7718389754a66f536ee3eb`
and internal report digest
`sha256:4a1fe5c076c00136ffd2a2b1bba9ccd9ad5f387261bdbc4454ed10537d30a0a5`.
The delta manifest has SHA-256
`48dd39e3586be19e43a420e8034ea5152bc9f730e1516023fbb87dd493b4241f`.
It records four canonical TRAIN transitions, two intervention pairs, one
causal path, one validated Knowledge object
`mk_71bb306601feb5c73e52@1`, and one TRAIN-authorized **candidate** Asset
`asset_1dbf0249659062b7e68a7a82`. The Asset is not production-promoted.

An independent invocation of `research_r5_train_m0_v4 verify` cold-opened and
checked all three bundles, source/gate hashes, TRAIN authority, and actual
TRAIN route/selection. The verified bundle digests and outcomes are:

| View | Bundle digest | Semantic rows | Route | Selection |
|---|---|---:|---|---|
| M− | `a31ef13da284f5cc3fb9569a94dba036c2bc355e8901b3cad5140ce5d86e815d` | 1 | NO_SKILL | NO_SKILL |
| M+ | `34afb338cdc3ffe41d2bb6f56aad1336607a0a6116f410e780aeb8b36e158994` | 101 | CONSIDER | SELECT |
| Mremove | `b0c9019e22ba1d4a2df0e61e6135ac794ad83955a242731d8349e53a2158f1da` | 1 | NO_SKILL | NO_SKILL |

`Mremove` removes the designated Knowledge/Asset delta and its derived rows;
the verifier confirmed its relevant semantic state and TRAIN consumption
equal M−. M+ selection is a TRAIN preflight, **not** answer-free target
transfer, target repair, positive DeltaMemory attribution, or an autonomous
learning result. The original gen3 SkillSurf transfer remains a negative
observed task and cannot be relabeled as fresh gen4 target evidence.

Next gate: freeze a genuinely new, oracle-qualified target and its exact
source/test closure before any gen4 route/bind/action. Keep clean/gold,
mutation coordinates, and target tests evaluator-private. A durable second
fault-domain copy and a full isolated-case recovery check are still open
before publication or cleanup under Revision 5 section 19.4.
