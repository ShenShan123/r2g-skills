# Gen5 healthy non-target and paper protocol checkpoint

The requirement audit found that a healthy counterpart of the repair DUT does not substitute for a distinct healthy non-target design (R5 §6.6). The gen4 shift-FIFO control is now rerun under the **unchanged frozen gen5 code and three actual gen5 Memory bundles**, retaining its already-observed control role. It is not an unseen new source or repair task.

Source is `abarajithan11/cocotb-example` at `0e653a503eb57d134be6531459389e80de3489ab`, healthy AXIS shift FIFO. The public query remains `AXIS_FIFO_SHIFT_CHAIN`, not a fabricated skid label. Original source/test/helper and seed startup hook are unchanged; native execution uses three configurations `(8,4), (8,2), (16,6)` with cocotb/NumPy seed 1729. The same gen5 source-only consumer emits the unchanged candidate in each view; the evaluator compiles that candidate in a fresh native root.

All three views returned NO_SKILL/NO_ACTION, no false activation, native 3/3 PASS. Cold audit verifies actual candidate/source/test hashes, authority handoffs, raw pytest/cocotb XML, seed values and byte-identical VCD bodies after removing the wall-clock date header. This is **one distinct control design and nine native testcase executions**, not nine independent designs. Independent authorship/source-group lineage is not certified.

External root: `_r5_pilot/transfer/abarajithan-axis-fifo-nontarget-gen5-r1/`.

- Receipt SHA256: `fdc35ef4db30089fbb0a4fead3c453b12ee86e91b133b0225a2761e9391c50c1`.
- Independent cold audit SHA256: `2cd62d066d4e95c22b02f570d5da6cbbe2650e6d47d2ae12e09e907b84c1ef27`.
- Frozen method/M0 and prior negative pilot remain as recorded in `research_r5_gen5_external_pilot_20260925.md`.

The new [paper protocol](research_r5_paper_protocol_20260925.md) defines primary all-registered-task repair, paired source-group effects, separate preservation/healthy controls, failure denominators, scoped cost and bounded sample planning. It deliberately does not invent an effect target or power estimate from a single fault.

The experimental bookkeeping implementation passed 44 synthetic counterexamples after fixing one initial test-file syntax error and duplicate case-name bookkeeping; frozen code and Memory identity mutations are also rejected. Its real-data normalizer reexecutes the prior pilot raw auditor and reports **one qualified pilot task, zero repairs in every view**, with controls separate. `final_test_ready=false`, because the final source scopes, frozen final manifest and statistical sample basis are not established. This is substantive R5-8 infrastructure, not completion of final-data freezing or the still-unrun Agent comparison.

No new corpus acquisition, model/API call, upstream edit, production promotion or push. The original evidence remains intact. Local analysis/protocol code is committed at `6d80e053f3730f871f22765885dfa2f6ebc12ce3`; method runtime remains frozen at `2ce921a599c406ab63331561a182d6f4f1bef8cb`.

## Supplemental seal and isolated recovery

A new owner-only snapshot at `/tmp/tehm-r5-gen5-evidence-vkssa24q/` includes the new control and paper readouts, alongside the original QF/TRAIN/pilot dependencies. The earlier `d7truf73` snapshot was not overwritten. New base archive SHA256 is `0c09fcc6e429d8530ff8c4b6d8e90e2956e3d6fa5b809fb798bac2676d52115a`; 24,520 archive entries / 19,256 file-or-link inventory entries matched their extracted copy. Seal receipt SHA256 is `24a97e988c27ac350a73bafcd1214ca5a03e83993332f4b3ee77fb7435909ad5`.

Frozen method and later analysis Git bundles were independently cloned; analysis bundle SHA256 is `6edc449c14a91e011b7331716ac001550183f33375a7656d970d02a5a8ecf9d6`. Recovery mounts the archived corpus at its original absolute path, frozen method separately, and analysis at `/analysis`; the original `/data1` trees are not mounted. Nested action consumers retain their narrower source-only mounts and no network.

Recovery attempt r1 reached all three native arms but ended UNKNOWN during pytest collection because BLAS/LAPACK symlinks pointed into the unmounted `/etc/alternatives`. The failed logs and receipts are retained. Attempt r2 only supplies those two resolved library links, with library-byte SHA256 pins; no RTL, tests, seeds, method or Memory changed. This is an infrastructure recovery retry, not a successful repair retry.

The r2 recovery reran actual three-view load/TRAIN authority/route/select/source-only consumers and nine native testcases. The unchanged native test/helper/source, seed 1729 and VCD bodies agree with the prior control. It also reran all 44 protocol checks and the raw-audited normalizer: `manifest.json`, `normalized-results.json`, `summary.json`, `readiness.json`, and `receipt.json` match the original gen5 readout r2 byte-for-byte. Recovery does not add statistical tasks or establish transfer gain.

- Recovery receipt SHA256: `ed610ed6c12d47820fe693e5583e0cb03b6726cd75a251b3fb1d3f06b96d7dd6`.
- Independent recovery raw audit SHA256: `c4ae575875bb9994f2bd1a7dcb1c7c9b5bf6903823d5eb79610f0b29fb06f8d8`.
- Corrected recovery entry `replay_gen5_protocol_control_r2.py` SHA256: `c75cedf4b4d7272181ad6319acbeaff7c173e40becc3eb9e1e7904fdcf0a1724`.
- Supplement `recovery-supplement.tar` SHA256: `71cd3ce84309ebb362b124c7ef850274a0bdaa89fff07e4b286605739c7172be`; 182 file inventory entries / 261 archive entries, byte-identical extraction verified.
- Supplement seal receipt SHA256: `833e474ca8e8eee633aef3795dc2aca9c1fe686689d2a4cbed8546c61083ea57`.

The supplement includes analysis bundle, corrected replay entry, auditor, failed r1, successful r2 and their receipts. Matching owner-only copies are at the new `/tmp` snapshot and `_r5_pilot/archive/gen5-control-protocol-recovery-20260925/`. Reproduction uses the base snapshot plus this supplement, not the uncorrected recovery entry inside the base tar. The entry accepts `--restored`, `--software`, `--analysis` and a fresh `--output`; system Python/Git, numerical libraries and OSS CAD remain external dependencies.

Devices 2081 and 2050 are different local filesystems on the **same host**. `/tmp` is not managed long-term/off-host retention; nothing is authorized for cleanup or publication. These tracked notes postdate the sealed experiment and describe it; they are not themselves part of the earlier tar.
