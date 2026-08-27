# TEHM ORFS L4 semantic fail→pass transfer (r4)

This directory records an evaluation-only L4 transfer receipt.  The physical
ORFS arms were already completed with the packaged OpenROAD/Yosys toolchain;
the training pairs were re-captured under the source-bound semantic contract,
then paired with an independent held-out FIFO transition.  No canonical DB,
rule lifecycle row, or production runtime state was modified.

The training path is `causal_path_69ab879fe338f882` (`L3_REPLICATED_EFFECT`).
The held-out transition is `transition_40b69b61faf3eb7f` from lineage
`tehm-heldout-fifo-semantic-r10:sky130hs:selector_fifo16:base0`.

The batch evaluator required the exact two-arm 14-check ORFS receipt and wrote
only an isolated transfer ledger.  `PASS` here means that the typed mechanism
and semantic fail→pass witness transferred; it does not mean the physical
change is useful or eligible for promotion.  The density-relief utility was
`HARMFUL`, so authority remains denied.
