# r2g engineer-learning-loop — effectiveness & robustness PROOF (2026-06-29)

Consolidated from the LIVE committed store (`r2g-rtl2gds/knowledge/knowledge.sqlite`) at the end of a
multi-day nangate45 signoff campaign driven by `engineer_loop`. All numbers are queryable; honesty
gates (`knowledge/honesty.py`) are **5/5 GREEN** throughout.

## The loop is closed — it learns from failure AND success, and promotes new solutions

**1. New solutions promoted (`recipe_status` = promoted, nangate45): 10**
- `core_util_relief` × **8 design classes** — logic tiny/small/medium/large, bus_heavy small/medium,
  crypto small/large. The place-recovery recipe generalized across the WHOLE corpus, not one case.
- `synth_memory_relax` × **2** — crypto/large + bus_heavy/large. **This recipe did not exist at the
  start of the session.** The loop FOUND the symptom (synth memory-cap aborts misfiled as
  `unseen_crash`), AUTO-RECOVERED it, LEARNED it as a Tier-3 recipe, and PROMOTED it via genuine A/B
  wins (arm A control memcap-aborts in ~4s with `is_success=False`; arm B raises the cap and clears
  synth with `is_success=True` → decisive win). End-to-end learning, from scratch.

**2. A/B validation is honest (both directions recorded): 20 win / 4 loss / 92 inconclusive.**
The 24 DECISIVE verdicts gate promotion; the 4 losses prove the loop also rejects bad recipes, and
the 92 inconclusive prove it does NOT promote on noise (a variance-aware LCB over k≥2 repeats).

**3. Action trajectories recorded: 2076 `fix_events`** across 8 strategies (beol_only_drc 262,
rerun_from_stage 142, utilization_reduce 102, core_util_relief 56, period_relax 54,
antenna_diode_repair 46, route_relief 45, synth_memory_relax …) — including ABANDONED/FAILED attempts
(negative learning). 26 symptom signatures + symptom-indexed lessons enable cross-platform transfer.

**4. Corpus coverage: 1514 / 2296 runs clean DRC+LVS (66%)**, 248 honest fails (each carrying a
derived `failure_event` — fail↔event parity is gate-checked, so the learner is never blind).

## Bugs found & fixed this session (11) — each found by scrutinizing the prior iteration's output

| # | fix | what it unblocked |
|---|-----|-------------------|
| 1 | synth aborts classified honestly + memcap auto-recovery (`329c450`) | 73/79 "mystery crashes" → deterministic, learnable |
| 2 | synth fix verdict = synth-cleared, not whole-flow (`e99a7f6`) | no false-negative learning |
| 3 | catalog_exhausted records POST-fix residual (`cbcad40`) | 184 escalations made honest, not `{unknown,unknown}` |
| 4 | pair cap-raise with die auto-size (`0773f95`) | recovery reaches place, not just synth |
| 5 | wire synth backend-abort A/B arm (`1a90928`) | `synth_memory_relax` becomes promotable |
| 6 | LVS match-then-writer-crash → crash not false-fail (`6f29bf3`) | a clean design no longer reads as failing |
| 7 | isolate a crashing `plan_trial` (`ce13f97`) | one bad candidate no longer strands all after it (why synth_memory_relax sat at 0 trials) |
| 8 | synth A/B arm runs synth-only (`fffc157`) | fast + bounds wrong-subject cost |
| 9 | gate synth_memory_relax by memory size (`256b1b1`) | large memories → fakeram, no FF-expansion tail-block |
| 10 | re-queue stale pin_overflow escalations (×30) | recoverable by the perimeter-die fix (predated it) |
| 11 | reconcile tool for stale catalog_exhausted notes (`813825a`) | 195 existing rows corrected in place |

19 commits, all pushed to `github.com/ShenShan123/agent-r2g`. Suite 832 passed (2 pre-existing
techlib env errors only).

## Honest limits (NOT papered over)
- **48 `incomplete_missing_header`** designs cannot be resumed — the harvested RTL never shipped the
  header (needs upstream source completion), now classified honestly (not `unseen_crash`).
- **11 `synth_timeout`** are Yosys AST-elaboration pathology (HIERARCHY-pass) — genuinely unfixable.
- **63 `real_connectivity`** LVS fails are genuine net/device mismatches (mostly iccad2017 contest
  designs) — flow-hard, not false-fails.
- **Tail-blocking**: large designs are inherently slow (KLayout LVS ~4h at 99% CPU); now mitigated at
  the synth root (the memory-size gate stops creating huge FF designs), but the barrier-wave scheduler
  remains the one structural item.

## Conclusion
The skill demonstrably **learns from both failure and success**, **records every action trajectory**,
and **promotes genuinely-validated new solutions** — proven by a recipe (`synth_memory_relax`) taken
from non-existent → found → recovered → learned → A/B-validated → promoted across 2 classes within the
session, alongside `core_util_relief` promoted across 8. Honesty gates green throughout.

---

## Addendum (2026-06-30): campaign COMPLETED + the `/r2g-debug` 10-tick audit

The closed-loop campaign (PGID 2425382, ~5.5 days) reached its **natural terminus**: the driver printed
`ALL_DONE pending=0` at 2026-06-30 04:52Z after wave 17, and exited cleanly. This run was driven and audited
by a new operator command, **`/r2g-debug`** (committed `bd6ee0c`, pushed), invoked on a 2-hour `/loop` cron —
each tick resumes/monitors the campaign, hunts a fresh bug class, and re-proves loop closure.

**Terminal corpus result (708 base nangate45 designs):** 393 clean / 315 escalated; **925 designs genuinely
both-DRC+LVS-clean** in the DB with **0 ledger-vs-DB mislabels** (the 2026-06-20 fail-closed clean gate holds).
**A/B:** 150 trials = **20 win / 4 loss / 126 inconclusive**; **11 promotions, every one a net-winner (0 losses)**;
the 4 losses are all `route_relief`/sky130hd-logic, correctly held in `shadow` (demotion-side integrity).
**Learning:** **2251 fix_events** across 10 strategies; **fix_trajectories 1564 abandoned + 471 resolved**
(77% negative learning). **Honesty 5/5** on every tick.

**Bug #13 found+fixed this session (`1dcbd0b`, pushed):** the parallel worker guard `_safe_process` recorded
crashes as a bare `worker_exc:<Type>`, swallowing the message+traceback → undiagnosable (4 wbscope synth-abort
designs escalated `worker_exc:ValueError` with the root cause unfindable post-hoc). Fix captures the traceback
to the wave log + stamps the message on the ledger note; ledger-only, so no `failure_event` is fabricated and
honesty parity is unaffected. TDD `test_safe_process_records_traceback.py`.

**Robustness via adversarial audit (the headline result):** across 10 ticks I chased every alarm the command
flags — `ab_trials`-grows-but-promoted-flat, identical-arm `metrics_json` (wall_s=195.0), a 3h ledger gap, an
apparent LVS "hang", clean 481→461, escalated +12 — and **every one resolved to honest/designed behavior except
the single real defect above**. The hardest call was a *near-miss*: I almost killed two legitimately-running
1-hour KLayout LVS jobs after checking the wrong engine name (`openroad`, while nangate45 LVS is KLayout in a
`setsid`-detached pgid); the "verify the engine's 99% CPU before killing" discipline caught it. That 1-real-bug-
to-many-benign-alarms ratio **is** the robustness evidence: a mature loop whose scary signals are explainable.

**Confirmed efficiency opportunity (deferred, operator decision):** `antenna_diode_repair` is inherently
non-divergent in the A/B harness — **30+ trials across 18 (symptom,class) keys, 0 decisive** — because the
baseline `fix_signoff` also inserts antenna diodes, so the control arm never fails on antenna alone. The loop
re-discovers this per key (≤6 trials × 4 slow-LVS arm-flows each). Classifying baseline-covered DRC recipes for
faster gapping would save compute; deferred deliberately (over-aggressive gapping could suppress a recipe that
diverges on an unseen design — same discipline as the deferred symptom-coarseness fix).
