# R5 v6 RAM Asset/core gate, with TRAIN authority still closed

This is a new software/profile increment after the v6 DEV binder and action
gate. It does not change the gen3 frozen M−/M+/Mremove or reclassify the
observed SkillSurf source as a new unseen target.

The v6 shadow Asset wrapper now binds a registered TRAIN witness to supplied
RTL and public context. The core action catalog is `rtl-actions-v0.7`; source
selection and structured candidate construction require replayed source-only
proof for its domain. A draft RAM Asset can be bound and statically executed
on both existing TRAIN sources and the already-observed SkillSurf drain-side
DEV fault. None of these RAM actions confer Memory or functional authority.

The dedicated RAM check generated a fresh receipt at
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/skillsurf-drain-v6-r1/asset-core-r2.json`.
Its cold replay passed 36/36 checks. It includes template tamper, stale source,
missing public context, capture-side nonbinding, proofless candidate, forged
source copy, forged PASS/oracle and rollback fields, and a forged raw authority
receipt. The exact authority outcome remained
`cross_lineage_verified=false`, `rollback_verified=false`, and promotion was
rejected through both legacy and strict paths. Receipt SHA-256:
`e99121f157e2d9fbeb1a5b8cb0aec95f79d7c119c0f729219aaefb0db6f8d400`.
The earlier exploratory r1 RAM receipt is superseded by r2.

The new binding wrapper SHA-256 is
`daae5ee4ec0ed9f09a08d47ea964f7e0fce11dbf7463c14963ea61f31007760f`;
the RAM check module SHA-256 is
`35c78f949fd91f7aeb591b097ebef007acfe639003c20d639e1edd8a2686c1f0`.
The old v5 RAM Asset conformance still passed 30/30, and the deeper v5 raw
TRAIN authority regression passed 10/10 with eligible RAM receipt digest
`sha256:8c277c76b41ac28fc5d353574624f5e354f5d64473323548cb0c325365da0d22`.
No old Asset was promoted or modified.

This explicit fail-closed gate is temporary: v6 needs a **new**, independently
replayable TRAIN source rollback and a v6-specific evidence verifier before
its cross-lineage/rollback gates can open. Only then can a new clean software
epoch build M−/M+/Mremove and preregister a fresh target. The existing DEV
transform-only repair must remain a separate control, not a Memory gain.
No model/API calls or GitHub push were made.
