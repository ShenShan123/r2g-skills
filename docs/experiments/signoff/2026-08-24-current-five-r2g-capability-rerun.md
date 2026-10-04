# Current Full-R2G Capability Rerun on Five Qualified Repair Tasks

## Scope

This rerun answers a narrow question: with the current Sky130HD knowledge,
promoted Recipe catalog, ranking, diagnosis, signoff loop, and strict
independent evaluator, how many of the five *qualified* 100 MHz repair tasks
can Full R2G repair without an external LLM, manual RTL edit, answer boundary,
or relaxed check?

The two remaining members of the original seven-RTL audit set are excluded
from the denominator: `eth_mac_1g` and `async-fifo-dpretet` have incomplete
sequential timing-constraint coverage. They cannot establish a valid fixed
100 MHz repair result until their real clock specifications are supplied.

The current Agent worktree is `b31caab` plus a local A/B execution-isolation
fix. The fix preserves the knowledge database and Recipe ranking, but makes an
A/B subject executable only when it is a normal project registered in the
current engineer-loop ledger. This prevents historic absolute paths in the
long-lived knowledge store from being launched by a new experiment.

## Result

| Qualified RTL | Initial stable failure | Full R2G action | Strict final result | Flows | Agent wall time |
|---|---|---|---|---:|---:|
| `forencich_axi_interconnect` | PPL-0024 pin-capacity place abort | `pin_perimeter_floor` | Clean | 2 | 351.195 s |
| `forencich_axil_interconnect` | PPL-0024 pin-capacity place abort | `pin_perimeter_floor` | Clean | 2 | 220.596 s |
| `cores_usb_device_src_v_usbf_device_core` | 32 `m3.2` DRC violations | no eligible catalog action | Not clean | 1 | 399.201 s |
| `Hazard3_hdl_hazard3_frontend` | 68 `m3.2` DRC; 44 route violations | no eligible catalog action | Not clean | 1 | 4,644.996 s |
| `serv` | 2 residual DRC violations | `density_relief` | Clean | 2 | 370.622 s |

The strict repair result is **3/5 (60.0%)**. A two-sided 95% Wilson interval
for this small qualified set is **[23.1%, 88.2%]**. Thus the data do not
support an 80% claim for this task set or for natural RTL generally.

Every clean result independently passed ORFS completion, full DRC, LVS, route,
antenna, setup/hold timing, RCX, protected-input checks, source/artifact
binding, and complete sequential constraint coverage. Every Full R2G execution
recorded zero external LLM tokens and zero human interventions.

## What the Rerun Establishes

1. The prior A/B cross-campaign execution issue is no longer reproduced. The
   five runs contain no `/home/yangao/r2g_ab_*` historical subject path; new
   candidates with fewer than two current-round subjects are explicitly marked
   unvalidatable rather than running an old project.
2. Current R2G can autonomously repair two independent pin-capacity failures
   and a small residual-DRC case under the frozen 100 MHz task policy.
3. The system fails honestly on the two remaining tasks. Both become
   `catalog_exhausted`, not falsely clean, and their independent evaluator
   records the unresolved violations.

## Current Boundary and Next Work

The dominant missing capability is not generic tool availability. USB and
Hazard3 both have clean timing, LVS, antenna, RCX, complete constraints, and
completed ORFS, but retain Sky130HD `m3.2` minimum-metal-spacing violations.
Hazard3 additionally retains 44 detailed-route violations after a bounded
77-minute flow. The catalog currently has no live, evidence-backed action for
this `m3.2` condition in these task classes.

The next scientific step is to develop candidate Sky130HD `m3.2` remedies on
a separate development set, test each in a sandbox, require strict A/B and
global non-regression before promotion, then rerun this frozen five-task set.
Do not add a per-design exception or relax the DRC deck merely to raise this
number.

## Evidence

Campaign root:
`/home/yangao/r2g_exp2_current5_r2g_capability_2026_08_24`

Per-task terminal artifacts:

* `methods/full-r2g/<fixture>/full_r2g_submission.json`
* `methods/full-r2g/<fixture>/validations/full_r2g_final.json`
* `methods/full-r2g/<fixture>/project/reports/fix_log.jsonl`

The earlier aborted attempt and its original isolation finding remain recorded
in `2026-08-24-current-seven-r2g-revalidation-analysis.md` and
`2026-08-24-current-seven-r2g-remediation-plan.md`.
