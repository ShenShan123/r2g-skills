# R5 gen4 healthy non-target control: bounded three-view result

This is a **healthy non-target control**, not a PILOT_TRANSFER repair task,
an injected fault, an unseen positive transfer, or a new independent design
sample. The public source is the AXI FIFO shift-chain in
`abarajithan11/cocotb-example` at checkout
`0e653a503eb57d134be6531459389e80de3489ab`. It lacks the occupied
one-slot skid-drain obligation represented by the gen4 v6 TRAIN Asset.
Its repository/source-lineage relation to TRAIN has not been certified.

Before TEHM execution, an external preregistration was written at
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/qualification/abarajithan-axis-fifo-nontarget-r1/preregister.md`
(SHA-256 `be3905a15f0d7739bbd13637113f522d549f269d358c782c860af62af727cfb9`).
The exact upstream RTL/test/helper files were copied, not edited, into an
isolated stage. The clean native test passed all three preregistered
configurations (`WIDTH,DEPTH` = `8,4`; `8,2`; `16,6`): pytest 3/3 and one
actual cocotb testcase per configuration, with zero errors, failures, or
skips. Raw XML, simulator builds, and logs remain external. The qualification
receipt has SHA-256
`455d126dff446d23dccc67f20a6b40ce3eec5a9d8250be1f9644c8e8eb624c6d`.
That original native qualification used upstream unseeded random stimulus;
it is not a fault-sensitivity result.

Frozen TEHM code is the gen4 M0 software worktree at `9818492`. The
non-target harness was fixed at local commit `dda85a9`, then all three
Memory views were actually loaded. Its first invocation (`run-r1`) failed
before any routing due to a harness circular-import order. The empty native
directory is retained with an infrastructure-failure marker; it is not a
hardware/method result. The corrected invocation (`run-r2`) is the sole
three-view control result. The source-only consumer saw only the staged RTL,
public context, and evaluator handoff inside a no-network filesystem
namespace; no upstream checkout, private test, or clean-reference path was
mounted. The actual query used the public `AXIS_FIFO_SHIFT_CHAIN` mechanism
classification, **not** a false skid-fault label.

| Memory view | Route | Selection | Action | Native clean result |
|---|---|---|---|---|
| M− | NO_SKILL | NO_SKILL | NO_ACTION | 3/3 PASS |
| M+ | NO_SKILL | NO_SKILL | NO_ACTION | 3/3 PASS |
| Mremove | NO_SKILL | NO_SKILL | NO_ACTION | 3/3 PASS |

The nine entries are test executions across three Memory views and three
parameter settings, **not nine independent designs or repair tasks**. All
views used separate native build roots and the same cocotb/NumPy seed 1729.
The three VCD files for a given configuration differ in their wall-clock
`$date` header; the waveform bodies after that header are byte-identical
across the views. The view source/test/helper hashes remain identical.

External `run-r2/report.json` has SHA-256
`ea1111752d12c5bb41708a9792b1588f1b07a629c36d8e860883d75b872cdc3f`.
Independent auditor `research_r5_gen4_nontarget_audit.py` was frozen at local
commit `cd1bb68`; its external `audit/audit-r1.json` has SHA-256
`10a30aaea1411022336a4fe3fe275add16e2fd46373bcc69ce34998574ad6947`.
The audit cold-checks bundle digests, source/report identities, raw XML,
seed properties, no-action receipts, network isolation, and VCD bodies.

This closes only the **healthy non-target control** part of R5 P5. It does
not qualify a new faulted target or demonstrate answer-free transfer or
positive DeltaMemory attribution. R5-5 still needs a genuinely fresh,
native-oracle-qualified target preregistered before TEHM action. Do not
relabel the already observed SkillSurf or tick-to-trade examples as new.
No model/API calls, upstream edits, production promotion, or GitHub push
occurred in this control.
