# r2g-rtl2gds — How to Improve the Skill Next (2026-06-22)

**Status:** planning / recommendation. Brief by design — a prioritized roadmap, not a spec.
**Author context:** written after auditing the references wiki + a full sweep of the skill's
state (git history, escalations, failure-patterns, tests). Companion to the just-completed
`r2g-rtl2gds/references/README.md` wiki index.

---

## TL;DR — the one thing that matters

This project's whole value proposition is **a closed learning loop that stays honest**
(`CLAUDE.md` → "The Closed Learning Loop" + "Honesty invariants"). Every serious bug this repo
has shipped has come from the loop *silently lying* — not from a missing feature. So the next
phase should harden trust and autonomy first, and gate capability expansion behind that.

The headline finding: **three of the worst bugs in the project's history share one root cause —
the fixture≠production gap.** Fix the *class*, not just the instances.

---

## 1. The recurring meta-bug: fixture ≠ production (P0 — systemic)

The same failure shape has bitten the loop repeatedly. Tests passed on mocked/string fixtures
while live runs used a different shape, so the bug shipped invisibly:

| Bug | Root cause | Why tests missed it |
|-----|------------|---------------------|
| `orfs_status` int/str mismatch (commit `0381b44`) — **all 929 runs misclassified `partial`, backend aborts invisible** | `run_orfs.sh` writes int exit codes; ingest compared to strings | fixtures used `"pass"/"fail"` strings |
| Gate A inert loop (`paper-absorption`) — A/B machinery shipped but **`learn()` never enqueued candidates** | A/B lived only in `engineer_loop.run`, never the prod `learn()` driver | integration test hand-inserted a `run_violations` row, masking the empty Tier-1 |
| Fail-OPEN clean gate (2026-06-20) — **11/101 nangate45 runs mislabeled `clean`** | `fix_signoff.sh` exit gate used a denylist `{fail,failed,…}`; `stuck`/`incomplete`/`crash` fell through | no test asserted the gate is fail-CLOSED |
| A/B planner blind to successful recipes (2026-06-22) — candidates stuck forever | `plan_trial` Tier-1 reads **post-fix** `run_violations`; a recipe that clears its symptom leaves no residual → unreachable | fixture had a residual; live cleared-symptom runs did not |

**Recommendation:**
1. **Institute a "production-shaped fixtures" rule** in `CLAUDE.md` / test conventions: any
   fixture that stands in for a `runs` row, a stage status, a `run_violations` snapshot, or a
   recipe-subject list must match the *exact* type and naming convention the live writer emits
   (int exit codes, repo-prefixed project dirs, cleared-symptom-has-no-residual). Add a test that
   round-trips a real `run_orfs.sh` status file through ingest.
2. **Allowlists, never denylists, for control-flow honesty gates.** The clean gate bug was a
   denylist that silently passed unknown statuses. Audit every "is this clean/done/passing?"
   predicate in the loop for the same shape; encode the *good* states, reject everything else.
3. **Run the honesty invariants against a real store snapshot in CI, not just mocks.** Today
   `test_honesty_invariants.py` asserts `find_contradictions(...)==[]` on fixtures; add a gate that
   runs the same checks over a committed, real `knowledge.sqlite` fixture so a schema/shape drift
   that re-breaks the int/str or failure_events-parity invariant fails the build.

This is the highest-leverage work: it stops the *category* of bug that has cost the most.

---

## 2. Make the closed loop actually close, per-platform (P1)

`CLAUDE.md`'s own alarm: *"An empty `ab_trials` alongside `fail`/`partial` rows is the alarm —
the loop is inert and lying."* The 2026-06-22 fix (`_symptom_designs` tier in `ab_runner.py`)
just **unblocked** the nangate45 A/B queue — but unblocking is not the same as validated.

**Recommendation:**
1. **Run a Gate-B wave on nangate45** (now that the planner can reach successful recipes) and
   confirm `ab_trials` gains rows *per platform* with a successful-recipe candidate transitioning
   `candidate → promoted`. The prior honesty bar ("ab_trials non-empty") was too weak — it must
   hold **per-platform**, since all 14 historical trials were sky130hd. This is the
   EXECUTE+VERIFY step the project keeps learning it cannot skip.
2. **Wire Win-5 presynth → ingest.** `presynth.py` is currently invoked by nothing, so the KNN
   pre-synthesis feature key is `None` for most runs and config suggestion can't use it. Hook it
   into `ingest_run.py` (or an ORFS post-synth stage hook). Small, self-contained, high payoff.
3. **Add a reconciliation auditor for dual-write projections.** `failure_events` is a derived
   projection maintained by *multiple* writers (`ingest_run.py`, `repair_run_status.py`); it has
   silently desynced from `runs.orfs_status` before. Extend `detect_contradictions.py` (already
   covers H3–H5) to also audit recipe-lifecycle transitions (`shadow→candidate→promoted/demoted`)
   and `run_violations` lineage, and surface drift as a dashboard red panel.

---

## 3. Cut surface area — execute the paused refactor (P2)

The refactor plan (`docs/superpowers/plans/r2g-skill-refactor-2026-06-19.md`, currently PAUSED for
campaign priority) is the right next structural move *because* smaller surface = fewer
fixture≠production traps:

- Delete ~390 LOC of dead report builders (`build_run_history.py`, `build_run_compare.py`,
  `list_artifacts.py`, `write_success_summary.py`, `build_strength_report.py`, etc. — confirmed
  zero non-test references).
- Demote operator-only CLIs (e.g. the dormant `eval_heuristics.py` payoff bench, ~980 LOC,
  superseded by live `ab_runner.py`) from the skill into `tools/`.
- Merge the 8-step learning loop into ~4 composite commands (`knowledge update <run>` =
  ingest + learn + mine, etc.), so the documented happy path matches the code path.

**Do this on `main` after the in-flight fixes are committed**, not concurrently with a live
campaign (the loop imports this code).

---

## 4. Capability expansion — only after the loop is trustworthy (P3)

Standing intractables, each a candidate for a *bounded* improvement (none is "just add a knob"):

- **Synth AST pathology** (Yosys constant-folding blowup on wide LFSRs, `failure-patterns.md`
  ~1419-1466): build an **escalate-early classifier** that distinguishes `ast_pathology` (no
  config lever — escalate immediately) from `scale_timeout` (try hierarchical synth) so the loop
  stops burning a full timeout on the unwinnable case.
- **Large-design DRC timeouts ≥~465K instances** (`failure-patterns.md` ~519-527): a partitioned /
  windowed DRC strategy, or an honest size-gate that escalates instead of hanging.
- **ChipTop-scale LVS** (5–9M-cell BOOMs): currently genuinely intractable (Netgen stalls) —
  keep as documented escalation; not worth automation effort yet.
- **Route-congestion residual at util floor** (crypto cores on 5-layer sky130hd): no flow lever
  exists below the density floor — these *should* escalate to RTL restructuring, and the loop now
  labels them `route_congestion_residual` correctly. Leave as-is.

**Scope discipline (keep the Hard Rule):** CDC, multi-clock, DFT, and signoff-quality closure stay
**out of scope / escalate-first**. Do not expand into them opportunistically — they would multiply
the honesty-invariant surface before the single-clock loop is fully trusted.

---

## 5. Durability & docs hygiene (P0-cheap, do immediately)

- **Push the work.** The branch is ~40 commits ahead of `origin/main`, and the two loop-closure
  fixes + their regression tests (`test_ab_fixhist_subjects.py`, `test_fix_signoff_clean_gate.py`)
  are **uncommitted**. None of the recent honesty work is durable until it's committed and pushed.
  (Confirm both DBs are honest per the invariants before committing, per `CLAUDE.md` "When You Fix
  a Bug" step 5.)
- **Docs-follow-fixes check.** The references wiki had drifted from reality (stale `/opt/...` env
  paths in `workflow.md`/`orfs-playbook.md`; a wrong LVS-platform claim in `spec-template.md`) —
  exactly the doc-rot the team's own rule ("update related docs/specs after every fix") guards
  against. The new `references/README.md` index makes drift easier to catch; keep it current.

---

## Recommended near-term sequence

1. **Commit + push** the in-flight loop-closure + honesty-gate fixes and their two regression
   tests (§5). Verify DB honesty first.
2. **Run a nangate45 Gate-B wave**; confirm per-platform `ab_trials` populate and a recipe
   promotes (§2). This validates the 2026-06-22 fix end-to-end.
3. **Add the honesty CI gate + "production-shaped fixtures" / "allowlist-only gates" rules** to
   `CLAUDE.md` and the test suite (§1) — stop the meta-bug class.
4. **Wire Win-5 presynth → ingest** (§2, small).
5. **Execute the refactor** on `main` once the campaign quiesces (§3).

> Sizing note: steps 1, 3, 4 are hours; step 2 is a multi-hour live EDA wave (operator job); step 5
> is a focused day. Capability work (§4) is deliberately *after* all of the above.
