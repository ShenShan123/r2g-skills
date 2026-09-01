"""Eight-step activation pipeline (design doc 10, 21.3, 26 Phase 8).

   1 Retrieve (Phase 7)      -> activation/pipeline (selects the rule)
   2 Applicability Check     -> activation/applicability.py
   3 Structural Binding      -> activation/binding.py
   4 Obligation Transfer     -> activation/obligation_transfer.py
   5 Instantiate Rewrite     -> activation/instantiate.py
   6 Sandbox Execute         -> activation/execute_adapter.py
   7 Oracle Verify           -> activation/verify.py
   8 Update                  -> activation/update.py

The three activation-time axes — Applicable, Executable, Verifiable — are stored
SEPARATELY in the ActivationRecord (design doc 11), never collapsed into one
success flag. The R2G execution base (engineer_loop / run_orfs / fix_signoff /
oracles) is the shared substrate injected as callables; TEHM adapters wrap it.
"""
from tehm.activation.pipeline import (
    ACTIVATION_VERSION,
    ActivationError,
    ActivationRecord,
    activate,
)
from tehm.activation.runtime_binding import (
    RuntimeBindingReceipt, bind_asset_to_repair_context,
)

__all__ = [
    "ACTIVATION_VERSION", "ActivationError", "ActivationRecord", "RuntimeBindingReceipt",
    "activate", "bind_asset_to_repair_context",
]
