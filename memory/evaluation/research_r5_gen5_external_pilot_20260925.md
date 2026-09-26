# R5 generation-5 external-source pilot: qualified task, frozen binder abstention

## Outcome and scope

One prospectively selected external RTL task was qualified and run through the actual frozen generation-5 three-view path. M+ retrieved TRAIN-derived support but its source binder rejected the target; M− and Mremove returned NO_SKILL. All three emitted the unchanged faulty candidate and failed the required target/native tests while preserving the non-target obligation. The same frozen transform-only/no-history primitive also abstained. **No external repair gain or positive ΔMemory attribution was established.** This is an informative bounded negative, not a reason to modify the frozen binder or hide the task from the denominator.

This checkpoint follows `research_r5_gen5_memory_20260925.md`. Core software remains `2ce921a599c406ab63331561a182d6f4f1bef8cb` and M0 report remains `sha256:f31072bf2c233974c9fbbf4bb4c30539942f07a57a5b6b7e9022611e90f3206b`. No production promotion, model/API call, GitHub push, upstream-source edit or cleanup occurred.

## Source and qualification

The bounded acquisition is [drewbabel/eth-datapath](https://github.com/drewbabel/eth-datapath), commit `5b8e276fe4cfd3387084709d40befbb9ddc30cfa`, stored under `/data1/zhangdy/RTL/RTL_testbench/drewbabel/eth-datapath`. The selected standalone closure is `rtl/axis_skid.sv` plus unchanged `tb/axis_skid_tb.sv`, WIDTH=8, packet length=4. The pinned checkout has Apache-2.0 licensing and remained clean.

The original `make MOD=axis_skid` clean run passed 1599 checks with zero mismatches on the pinned Icarus 13/vvp toolchain. A separate task preregistration then fixed one researcher-constructed occupied-slot payload fault, target/preservation obligations and native source-list adapter before any fault simulation or TEHM route. The six-arm qualification produced:

| Source | Private actual-delivery target | Private direct/reset preservation | Unchanged upstream native TB |
|---|---|---|---|
| Clean | PASS, 2 accepted / 2 delivered | PASS, 3 accepted / 3 delivered | PASS, 1599 checks / 0 mismatches |
| Constructed fault | FAIL on second actual output handshake | PASS, 3 accepted / 3 delivered | FAIL, 1599 checks / 76 mismatches |

Private witnesses are evaluator-authored; the native aggregate is a separate sensitivity check, not a native per-phase target/preservation attribution. This is one constructed task, not 1599 independent tasks or a discovered upstream bug. Simulator-default random stream, no seed retries.

Source history, selected elaboration modules and byte/comment-stripped token comparisons were inspected against AXIS/ZipCPU/LibSV TRAIN and PULP DEV. The selected file is not an exact/token clone in that bounded comparison. The repository also vendors Alex Ethernet and ZipCPU formal properties, but neither enters the selected native elaboration. GitHub fork=false and distinct local history do **not** prove independent authorship or rule out uncredited influence. Report bounded external source-group evaluation, not proven universal source independence. This observed source/task cannot later be called unseen FINAL_TEST.

## Actual three-view and controls

All views used the same code, primitive, public WIDTH context, mechanism-guided query, qualified oracles, one-candidate/action budget and zero model calls. Each snapshot was actually reloaded into a separate RAM SQLite connection. TRAIN authority was cold-verified; target observations never entered Knowledge/Asset admission. Mremove semantic rows equal M− after removal of the complete 145-row/20-table increment.

| Faulty task arm | Route / selection | Action | Target | Preservation | Native | VerifiedRepair@1 |
|---|---|---|---|---|---|---|
| M− | NO_SKILL / NO_SKILL | None | FAIL | PASS | FAIL | 0/1 |
| M+ | CONSIDER / ABSTAIN | None | FAIL | PASS | FAIL | 0/1 |
| Mremove | NO_SKILL / NO_SKILL | None | FAIL | PASS | FAIL | 0/1 |
| Transform-only/no-history | Bypass Memory / direct frozen primitive | None | FAIL | PASS | FAIL | 0/1 |

M+ and the direct primitive report the same underlying rejection: `v6_delegate:code_outside_unique_module`. This localizes the observed rejection to the frozen source-binding boundary; it does not establish that the mechanism is semantically inapplicable. No preprocessor, oracle or binder rescue was attempted after observing it.

The healthy counterpart ran all four arms: unchanged source, target/preservation/native PASS, false-action 0/1 in each arm. These are controls on the same task, not four new sources. Eight controller attempts and 24 oracle executions were retained. No-Memory arms are deterministic controller baselines, not Agent/LLM baselines. The mechanism family is supplied public context, so this is not an unconstrained autonomous diagnosis evaluation.

Evaluator-side routing/selection is separated from a bubblewrap action consumer. The consumer receives one source, public context, frozen software and a sanitized decision/action handoff; no target clean counterpart, tests, mutation coordinates, `.git`, TRAIN DB or network. Any executable payload is rederived from source. All candidates were emitted before candidate oracles ran.

## Cold audit and raw evidence

Evidence root: `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot` (owner-local, not public Git content).

| Artifact relative to evidence root | SHA256 |
|---|---|
| `qualification/drewbabel-axis-skid-native-r1/runs/r1/receipt.json` | `2c7e3f603d1c5ed40256d66a8b3eb2d10d7fbc8b75400d17c518283ee94c3cb8` |
| `qualification/drewbabel-axis-skid-native-r1/source-audit-r1/receipt.json` | `462e8d152246304f9d6c55d9ac96b00a3a355d8f9734170eece1bbab5faa274f` |
| `qualification/drewbabel-axis-skid-task-v1/evaluator-private/runs/r1/receipt.json` | `bf281dab12166fdec630f7eb8f65e76ce6f691e686c1a9554024fa90caa420d2` |
| `transfer/drewbabel-axis-skid-gen5-pilot-r1/run-r1/receipt.json` | `633f68fce28bdd30b075801001f36e9cb5d5df95151d054d3490488b1c690e07` |
| `transfer/drewbabel-axis-skid-gen5-pilot-r1/audit-r1.json` | `c51f2bbaa37944551957153194276a04b00d7d97b69dae88bee9bc2a01f87d05` |

The separate-process audit recomputed all 24 verdicts from raw commands/logs/actual compiled sources, checked handoffs/isolation mount lists, unchanged DB digests and Mremove semantic equality, and rejected forged verdict, forged candidate and dropped-attempt counterexamples. Its `valid=true` means the reported negative is supported; it does not mean a repair succeeded.

## Stage disposition

R5-5 now has a bounded new, qualified external task with the above lineage limitation. R5-6 has actual three-view/control outcomes, cold audit and isolated recovery, with **zero measured repair advantage**. The original evidence must remain intact. R5-7 model comparison has not run and needs fresh explicit call/token authorization if selected. R5-8 should fix the paper protocol and untouched FINAL_TEST selection before scaling; do not turn this observed task into a new final sample or silently extend v7 to make this pilot positive.

## Completed isolated recovery

The second-device archive is `/tmp/tehm-r5-gen5-evidence-d7truf73/pilot-evidence.tar`, SHA256 `66efb3d0b799bc86aa2729f9c2b8a1a1cd68e354fab754d1a3e9ff7814814b00`. Its 19,150-file/link inventory matched the original and extracted tree. A frozen-software Git bundle and archived TRAIN repositories/history permit recovery without the original linked TEHM worktree.

With the original corpus hidden, the archived recovery entrypoint reverified TRAIN authority and gen5 Memory, reloaded M+, reran actual route/select, emitted the same sanitized handoff/candidate in a nested source-only sandbox, then freshly compiled/executed all three oracle scopes. It reproduced CONSIDER/ABSTAIN, target second-delivery FAIL, preservation PASS, native FAIL (76/1599 mismatches/checks). Recovery is a new execution of the same case, not a new statistical unit or reconstruction of the original execution identity.

- Recovery receipt SHA256: `62dc8c5bfc004445d42ecdaf0c9df91667723ff206fd47c27607440cc6d399bd`.
- Independent recovery raw-audit SHA256: `c352fa7614345a65274c290ed65b2393d6ca9d0ed0fa1b83fbf721a86d798c3f` (`valid=true`).
- Exact paths and commands: `_r5_pilot/archive/gen5-local-recovery-trial-20260925.md`.

This is a same-host, different-device, owner-only temporary copy. The system toolchain remains an external hash-checked dependency; `/tmp` is not managed durable/off-host storage. Do not delete raw evidence or publish private answers. The archive predates subsequent report/index notes; sealed experimental bytes remain unchanged.
