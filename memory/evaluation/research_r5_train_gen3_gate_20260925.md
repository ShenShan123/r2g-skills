# R5 generation-3 TRAIN gate and M0 boundary

Generation-3 uses the versioned v5 shadow action, a new v3 scoped TRAIN record
and measurement contract, and fresh v5 source rollback. The original two
TRAIN tasks are explicitly reused DEV-as-TRAIN, researcher-assisted evidence.
They are not independent held-out targets or autonomous online learning.

Authoritative pilot receipts:

- `dev/measurement-v3-train-ram-r1.json`: 20/20 RAM conformance, four raw
  scoped transitions, two controlled L2 pairs, two-source L3 replication,
  strict Knowledge authority and negative evidence/held-out controls.
- `training/asset-rollback-v5-r2/receipt.json`: independently staged source
  rollback, target FAIL and preservation PASS for both TRAIN tasks with fresh
  evaluator runs; this is not Mremove.
- `memory/asset-authority-v5-ram-r2.json`: 10/10 strict v5 Asset authority
  checks in RAM, including rejection of forged lineage, forged rollback, v4
  rollback version, wrong target scope, and non-strict promotion. Asset remains
  draft in this check; no persistent M+ is created by it.

The earlier `asset-rollback-v5-r1` and `asset-authority-v5-ram-r1.json` were
exploratory v2-measurement-scope receipts. They are superseded, not inputs to
generation-3 M0, and must not be cited as its authority.

`research_r5_train_m0_v3.py` requires both r2/v3 RAM gate receipts, the
current source rollback digest, and a clean Git software epoch. It writes
fresh M−/M+/Mremove bundles and a delta manifest to a new pilot directory,
then cold-verifies actual bundle loading, complete dependency removal,
Mremove-versus-M− semantic equivalence, and TRAIN route/selection behavior.
This is a read-only deterministic Memory experiment, not a target transfer,
paper result, or production promotion. A new independent target and the
three-view oracle comparison remain separate R5-5/R5-6 gates.
