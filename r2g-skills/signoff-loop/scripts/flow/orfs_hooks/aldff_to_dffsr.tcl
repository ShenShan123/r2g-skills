# Quarantined on 2026-10-26. DRC/LVS-clean implementations did not establish
# original-RTL behavioral equivalence. C01169 and C03509 both have executable
# reset-behavior counterexamples; do not assume undocumented input stability.
# The rejected transformation is preserved ONLY as a diagnostic replay fixture:
# experiments/sky130_repair/semantic_evidence/aldff_to_dffsr_rejected_original.tcl
# See experiments/sky130_repair/replay_aldff_counterexamples.py.
error "R2G_SEMANTIC_REVIEW_REQUIRED: aldff_to_dffsr is quarantined after original-RTL reset-behavior counterexamples (C01169, C03509); physical DRC/LVS clearance is insufficient"
