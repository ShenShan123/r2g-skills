# Campaign semantic guard, not an EDA workaround.
# C01169 has an executable counterexample: input changes while reset is held
# and the clock is static. ALDFF -> DFFSR follows the data; the original
# edge-triggered RTL does not. Do not assume undocumented input stability.
error "R2G_SEMANTIC_REVIEW_REQUIRED: async-load emulation lacks original-RTL equivalence evidence; see semantic_evidence/aldff_counterexample.json"
