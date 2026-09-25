# R5 RTL TRAIN Asset source-rollback rehearsal (2026-09-24)

Status: fresh evaluator-only TRAIN execution; **not** Asset authority,
Memory `Mremove`, unseen target transfer, or full-design signoff.

The preregistered runner
`tehm.evaluation.research_r5_train_asset_rollback` was executed in a new
isolated work root:
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/training/asset-rollback-r1`.
It cold-replayed the two original TRAIN receipts and the preceding draft Asset
validation preflight. For each source it staged the exact v3 candidate,
restored the backed-up pre-action faulted RTL into the simulator's source
path, checked the restored source and test bytes, and then ran the frozen
source-specific oracle afresh. Upstream repository checkouts and original
TRAIN workspaces were not changed.

The fresh axis cocotb run executed 9 cases: the two preregistered payload
targets failed and seven preservation cases passed. The fresh ZipCPU
evaluator-private augmented oracle found backpressure payload order FAIL and
direct-path preservation PASS. The compiled images identify the restored
staged RTL files. Source restoration returned both cases to the original
faulted-source SHA, while the candidate-before-rollback artifacts retain the
exact repaired-source SHA. The ZipCPU result is **not** a native formal PASS.

Saved receipt:
`_r5_pilot/training/asset-rollback-r1/receipt.json`, SHA256
`e289319e0c9fa9d404cfbb2a0e348f1472aa750b914e8e53a1b1cea497f2c704`,
internal digest
`sha256:253492d57a30b52bd539bf595d4a39b956fe2b49ae86b2b4b4ffa6d6679d2030`.
The `verify` entrypoint re-read all staged source, test, command, native
report, log, compiled image, and tool lock evidence; a second invocation
reconstructed a receipt equal to the saved JSON.

This is a source/action rollback witness. The Asset lifecycle still cannot
accept an unbound `verified=true` supplied by a caller: the strict ledger
must bind this receipt to the registered Asset, its two TRAIN validation and
binding rows, and the audited source-lineage relationship, then cold-replay
the same gates. Separately, R5's three-state `Mremove` requires removing the
eventual Memory delta and dependent state from a read-only snapshot, not
merely restoring a source file. Both remain to be implemented.
