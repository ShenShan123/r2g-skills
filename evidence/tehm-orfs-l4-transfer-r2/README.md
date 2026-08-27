# ORFS L4 held-out transfer replay (2026-08-27)

This is a negative, evaluation-only receipt from the current exact-ORFS
transfer lane. A fresh L3 training path was rebuilt from the v4 snapshot with
two source-disjoint fail→pass lineages (`mux32` and `parity64`), including
distinct run witnesses. An independent held-out `shift32` fail→pass pair was
then evaluated with `require_full_oracle=true`.

The held-out pair matches the typed mechanism, compatibility profile, and
effect key, but its persisted verifier does not contain both sides of the
exact 14-check ORFS contract. The evaluator therefore returns
`heldout_transfer_witness_failed` and keeps the evidence at
`L3_REPLICATED_EFFECT`; it does not alter the causal path, canonical memory,
rule lifecycle, or production runtime. The immutable source database digest is
unchanged before and after evaluation.

The same case was replayed through the batch wrapper with a new isolated
ledger. The ledger receipt verified successfully while remaining
`eligible=false`; batch status is `FAIL` because the case stays in the
denominator. See `batch_replay_summary.json` and `transfer_manifest.json`.

This artifact is deliberately not an authority or promotion receipt. A future
positive receipt must replace the incomplete held-out pair with a real
source-disjoint fail→pass run whose two arms each pass all 14 checks.
