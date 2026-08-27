# ORFS routing semantic L4 transfer r5

This directory records an evaluation-only, source-bound routing transfer. It
does not import anything into canonical memory and does not alter production
runtime policy.

## Evidence

- `semantic_oracle.json` requires `ROUTING_LAYER_ADJUSTMENT` to be present in
  the materialized `constraints/config.mk`. The before arm is therefore a
  source-bound semantic `FAIL`; the after arm is `PASS`.
- `training_controlled_replication_report.json` contains two independent
  training lineages (`selector_crc16` and `selector_uart16`) with complete
  physical ORFS evidence, explicit no-op controls, and an
  `L3_REPLICATED_EFFECT` path:
  `causal_path_54ba6f410c35d0b2`.
- The held-out `selector_arbiter8` pair is captured only as
  `split=heldout`, `learner_eligible=false`. Both arms have the exact 14-check
  physical oracle; its semantic witness is `FAIL -> PASS` and utility is
  `NEUTRAL`.
- `transfer_report.json` is a read-only batch replay with
  `L4_TRANSFER_SUPPORTED_MECHANISM`, mechanism/effect/lineage binding,
  `batch_status=PASS`, and a verified isolated ledger receipt
  `causal_transfer_1a7ecbdba8119b1717c0`. The source DB hash is unchanged.
- `heldout_oracle_summary.json` exposes the held-out transition's exact 14
  physical check names and the semantic presence receipt without packaging the
  large ORFS run directories.

## Reproduction

The ORFS project arms are retained on scratch under
`/tmp/tehm-orfs/{selector-crc-routing-r1,selector-uart-routing-r1,selector-arbiter-routing-heldout-r1}`.
The training path was rebuilt with `memory/scripts/build_orfs_controlled_replication.py`
and `--transformation-family ROUTING_CAPACITY_RECOVERY`; the held-out pair was
then captured with `run_orfs_diversity_campaign.capture_pairs()` and replayed
through `memory/scripts/evaluate_causal_transfer_batch.py --require-full-oracle`.

## Boundary

This is stronger cross-lineage transfer evidence, but it is not an authority
receipt. Rollback, registry, obligation, harmful-rate, cross-lineage-TE, and
conformal gates must still be evaluated independently. Promotion remains
`promotion_attempted=false`; Parametric remains shadow-only.
