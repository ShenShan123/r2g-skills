# R5 RTL TRAIN Asset strict-authority gate (2026-09-24)

Status: **eligible in an isolated RAM core-gate rehearsal only**. The Asset
remains `draft`; no persistent read-only M+ snapshot, held-out transfer,
production promotion, or Memory `Mremove` has been performed.

The v3 lifecycle now derives source-lineage and source-rollback gates from a
bounded, raw-replayed TRAIN evidence bundle. It cold-replays the two TRAIN
verifiers, the pinned source-history audit, exact source-only bindings and
candidate bytes, target/preservation validations, and the separate fresh
rollback run. The proof is limited to the two disclosed DEV-reused TRAIN
sources and `rtl.skid.temp_payload.v3.dev`; another source, software
generation, or target scope needs a new evidence contract. The strict Asset
ledger additionally requires each validation and binding row to carry the
exact `training` split, case ID, and audited repository lineage, and checks
the same metadata again from stored rows on cold verification. For v3 only,
the registry refuses a non-strict all-true gate dictionary at promotion.

Frozen evaluator receipt:
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/memory/asset-authority-preflight-r5.json`,
file SHA256 `76f0b93a0b68cbb16af234d9113e69171e06809a54be7fd9b0c3043d360851bf`.
The RAM authority receipt digest is
`sha256:8d11c55e00878011ebd365a190b1b089f68b96457e45a27470636f6bf66890ca`.
Replay from the repository root:

```bash
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_train_asset_authority_checks \
  --verify /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/memory/asset-authority-preflight-r5.json
```

Nine checks passed twice, including pure and strict ledger eligibility,
forged rollback rejection, forged lineage-row rejection in the actual ledger,
wrong-scope rejection, non-strict promotion refusal, and unchanged `draft`
status. The older DEV synthetic gate check still passed 12/12 after its
expectation was updated: synthetic booleans now leave *both* lineage and
rollback gates closed. No synthetic PASS was admitted as training.

The first positive strict-authority preflight attempts (`r1`–`r2`) exposed a
nonsemantic wall-clock `created_at` field in the bound Asset copy, which made
their receipt digests differ between fresh RAM databases. The final RAM-only
check fixes the fixture timestamp before binding; it does not modify the
production registry or claim that separate real registrations have identical
receipt identities. Prior files remain as diagnostic history, not current
frozen evidence.

Remaining R5-4 work: construct immutable read-only M−/M+ snapshots from
the actual TRAIN Knowledge and Asset, bind the Asset to the Knowledge object,
record a delta/dependency manifest, and verify cold reload. The source-file
rollback receipt does not substitute for removing that delta to form
`Mremove`. R5-5/6 still require a separately preregistered new target and
actual route/select/bind/execute with fresh oracle runs.
