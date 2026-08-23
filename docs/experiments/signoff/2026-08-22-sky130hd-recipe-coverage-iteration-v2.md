# Sky130HD Recipe Coverage Iteration V2

## Scope and Acceptance Rule

This is a Sky130HD-only development campaign at a fixed 100 MHz (10 ns) target. Every
subject uses a source-bound RTL closure, the default ORFS baseline, the normal strict
DRC/LVS/route/timing/RCX gate, and unchanged clock/SDC. A candidate is allowed to alter
one reviewed implementation setting only. A production Recipe requires two independent
natural RTL families, two repeatable A/B trials per family, distinct arm-owned run IDs,
complete provenance, strict clean signoff, and no global regression. A partial
improvement, timeout, no-op, or new DRC/LVS/route/timing/antenna/RCX failure is rejection
evidence, not a promotion.

The campaign started from `2a9ba1b306dfa7f19e758956d51df74be3e9af65`; the production
reliability corrections listed below were subsequently merged to `main` through
`54bf14de48a43947c7a4ecd38e1c25da6bbddc04`.

## Agent Reliability Corrections Merged

| Correction | Why it mattered | Verification |
|---|---|---|
| Park learner-owned Recipe rows with no positive evidence | A candidate queue should not turn empty or inconclusive history into an automatic A/B job. | Such rows now remain `parked` until a real clearance/win or operator review. |
| Separate compilation units from dependencies | Treating headers and collateral as top-level compilation inputs can change synthesis semantics. | The probe now keeps a frozen source closure but passes only real compilation units to ORFS. |
| Reject zero-cell netlists before physical design | A syntactically accepted but empty netlist is not a physical-design candidate. | It now becomes an input-qualification failure before floorplan. |
| Exclude interrupted flows from learning | A manually stopped execution must not be learned as an RTL or Recipe failure. | It is emitted as `execution_interrupted` and excluded from replay/learning. |
| Reuse stable baseline evidence exactly once | A stable replay was followed by an unnecessary third baseline before repair, wasting route time and allowing inconsistent evidence semantics. | The loop consumes the digest-bound replay return code once, then performs any later reflow normally. |

The complete repository regression suite passed under the configured EDA environment:
`1241 passed, 1 skipped`.

## Natural Evidence Collected

| Cohort / family | Baseline outcome | Candidate or classification | Decision |
|---|---|---|---|
| AGR | 20 `m3.2` DRC violations | `PLACE_DENSITY_LB_ADDON=0.05` reduced count to 16; detailed-placement padding increased it to 24 | Reject both: neither closed strict signoff, and the density effect is below the protected bound. |
| Secworks Blake2s | setup WNS `-3.40724 ns` | `ABC_AREA=1` improved WNS to `-2.91272 ns` but introduced LVS mismatch | Reject: a global regression invalidates local timing improvement. |
| Secworks SHA512 | setup WNS `-2.05581 ns` after the same memory-cap recovery in both arms | `ABC_AREA=1` reached `-0.689897 ns` | Reject: no timing closure. |
| Matrix accelerator | setup WNS `-0.246106 ns` | `SETUP_SLACK_MARGIN=0.3` reached `-0.0929008 ns`; place-repair timing reached `-0.140751 ns` | Reject: partial improvements only. |
| SHA-256 stream | small setup deficit | `SETUP_SLACK_MARGIN=0.5` reached clean `+0.146288 ns` | Discovery lead only: one close is insufficient for a new promotion. |
| PCS RX | natural `ROUTE_TIMEOUT` | existing promoted `route_relief` at `CORE_UTILIZATION=10` timed out after 4004 s | Negative transfer evidence; do not claim route relief is universal. |
| TSA systolic 8x8 | natural `ROUTE_TIMEOUT` at 100 MHz | second baseline replay in progress | Eligible only if the failure repeats with the same protected-task digest. |
| ACE2 RoPE score core | clean DRC/LVS/route/RCX, setup WNS `-5.07195 ns` | second baseline replay in progress | This is outside the narrow existing setup-margin domain; a new effect needs its own evidence. |
| TSA AXI-lite top | strict clean, setup WNS `+0.608222 ns` | baseline control | Not a repair subject. |

No new Recipe ID was promoted in this iteration. That is the correct result under the
acceptance rule: the candidate effects produced useful negative evidence, but no repeated
natural fail-to-clean causal result.

## Active Bounded Follow-up

The two new natural failures are running a second independent baseline under an isolated
copy of the current knowledge store. If, and only if, their failure signature and protected
task digest match, the existing production Recipes are ranked and applied with the clock
relaxation strategy excluded. The isolated run preserves the canonical knowledge database
and cannot silently promote a result. Its output will distinguish three outcomes: a strict
non-regressive closure that can become additional generalization evidence; a stable failure
that identifies a catalog gap; or an unstable result that is discarded as non-actionable.

## Next Iteration Rule

The next acquisition round should target an underrepresented natural symptom class rather
than repeatedly varying broad synthesis knobs. Any newly proposed effect must first state
its exact config/Tcl delta, applicable symptom range, protected fields, and the two natural
families required for confirmation. Artificial stress cases may test guardrails, but never
contribute promotion evidence or a paper success claim.
