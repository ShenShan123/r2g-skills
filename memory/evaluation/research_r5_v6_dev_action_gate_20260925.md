# R5 v6 DEV action-envelope gate

This note extends the earlier v6 binder gate without changing its frozen
receipt, the gen3 Memory epoch, or the observed SkillSurf pilot denominator.
It is a DEV-only software increment, not TRAIN Memory or transfer evidence.

`tehm.rtl.skid_payload_action_v6` rederives an exact source-bound payload
from supplied RTL and public context before the single drain-side edit. Its
domain is versioned separately from v5. The action is deliberately **not**
registered in the core action catalog or attached to an admitted Asset yet.

The new action-envelope receipt at
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/skillsurf-drain-v6-r1/action-envelope-r1.json`
cold-replayed with 17/17 checks. It rejects stale source, wrong domain,
module, context, witness, extra answer field, missing field, healthy source,
and the observed capture-side fault; it passes a no-file/no-network guard and
replays both existing TRAIN sources by explicit v5 delegation. Receipt SHA-256:
`8b67aa7576873e29f06f8a39ae5ed99edba2e792b67bd401f47417ecdab56ba1`.
The action module SHA-256 is
`2217a1daf3affd899f084afbccf1fe6413e3261f5eb58d1c1bb4beff3c028ad0`;
the check module SHA-256 is
`05dcdd92ed89cab3a12cc99bc64b2aea7ad62d63fbe9b70cf069a29eb2de5d27`.
The generated DEV candidate SHA-256 remains
`7415ad31787cd599fd90c34fde39167e5d0b308249806bed335e5f723da44403`.
The earlier v6 binder receipt reverified 33/33 and the frozen v5 DEV receipt
reverified 32/32 after the action addition.

The DEV result already demonstrates a transform-only software path on an
observed source. Any later v6 Memory experiment must use the same v6 software
in M−, M+, and Mremove, and report this transform-only arm separately; a
positive result from this branch alone cannot be attributed to Memory. Next
gates remain a registered v6 Asset/action under strict TRAIN authority, a new
clean software epoch and gen4 Memory bundle, then preregistration of a fresh
target with source and evaluator separation. No model/API calls or GitHub push
were made.
