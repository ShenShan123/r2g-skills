# Independent GCD RTL-to-Final-Netlist Formal Equivalence

**Induction and initialization base case PASS.** Under synchronous two-state logic, all 18 outputs of the final routed netlist and original RTL remain equal after reset and acceptance of the first request; subsequent inputs are unrestricted.

Inputs are saved gcd_smoke/rtl/gcd.v and the initial flow's 6_final.v. Five actual ASAP7 RVT FF Liberty files expand 395 logic cells into Boolean functions and state transitions without logic blackboxes. The netlist comes from implementation, not Calibre extraction. [Inputs, versions, hashes](verification.json).

1. equiv_make compares req_rdy, resp_val, and 16-bit resp_msg: 18 points. Combinational/SAT checking proves one; four-step induction proves the other 17; none remain unproved. [Script](equiv.ys), [log](equiv.log).
2. Induction alone assumes a preceding equality window. An independent miter proves the base: arbitrary register initial values; reset=1 in abstract step 1, req_val=1 with arbitrary request data in step 2, all other inputs free; all outputs equal in steps 3–6. Ready after reset accepts the request and loads the 32 unreset data registers. No zero initialization is assumed. [Script](reset_base.ys), [log](reset_base.log).
3. Four consecutive equal base cycles plus four-step induction establish subsequent unbounded equivalence. Steps describe synchronous state transitions, not physical waveforms or delay.

The original 32 data registers have no reset, so resp_msg need not have equal arbitrary initial values before the first request. This proof checks RTL, Liberty semantics, and final connectivity; it does not replace independent PEX/CDL transistor checks.

## Negative control and reproduction

An independent copy replaces final resp_val driver BUFx2_ASAP7_75t_R output53 with INVx1_ASAP7_75t_R. The same base check FAILS with a counterexample; the original netlist is unchanged. [Log](mutant_base.log), [VCD](mutant_counterexample.vcd).

Run `bash experiments/asap7_qualification_20261116/reference_diagnosis/formal/run.sh` from the repository root. The final negative control must fail through a SAT counterexample; the wrapper checks this before declaring overall PASS.
