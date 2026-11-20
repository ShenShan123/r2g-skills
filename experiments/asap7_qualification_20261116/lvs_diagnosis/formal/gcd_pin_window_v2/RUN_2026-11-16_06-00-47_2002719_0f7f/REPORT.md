# gcd_pin_window_v2: Final-Netlist Formal Equivalence

**PASS.** Using original ASAP7 Liberty semantics, the saved RTL and final routed netlist remain equivalent on all 18 output bits after reset and acceptance of the first request.

- Four-step induction proves all 18 points.
- Registers start arbitrarily. Step 1 resets; step 2 accepts arbitrary request data; outputs match in steps 3–6 with other inputs free.
- Inverting resp_val in a separate copy produces a counterexample; the original netlist is unchanged.

Input: `/home/yangao/r2g-skills/experiments/asap7_qualification_20261116/gcd_pin_window_v2/backend/RUN_2026-11-16_06-00-47_2002719_0f7f/results/6_final.v`. Logic cells: 395. See [verification.json](verification.json) for inputs, Liberty/log hashes, and actual proof depths.

This proof uses Boolean/state-transition semantics, not direct sequential PEX transistor behavior. Independent transistor-network isomorphism complements it. Delay, skew, and physical DRC/LVS are outside its scope.

Reproduce: `/usr/bin/python3 /home/yangao/r2g-skills/experiments/asap7_qualification_20261116/lvs_diagnosis/run_gcd_formal.py /home/yangao/r2g-skills/experiments/asap7_qualification_20261116/gcd_pin_window_v2`.
