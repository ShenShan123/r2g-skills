# gbrain → r2g Techniques Absorption Plan

> **Status:** ✅ **IMPLEMENTED 2026-06-18** on branch `feat/gbrain-absorption`
> (A + C + **D**; see §10). Q-a/Q-b closed (§9). Suite **643 → 676** green; real
> committed store passes all 4 honesty gates; probe clean. NOT committed (harness
> commits only when asked).
>
> _History:_ REVISED v2 after 5-person review panel (2026-06-18). The panel
> reached a unanimous direction: **ship A + C (deterministic, $0, stdlib) now;
> split B (skillopt) into a separate, evidence-gated spec.** This revision
> applied that, plus the panel's specific fixes.

**Author:** r2g maintainer (via Claude Code)
**Source material:** `https://github.com/garrytan/gbrain` (cloned `/tmp/gbrain`,
master @ 2026-06-18).
**Reviewers:** Adversary (premise), Feasibility (code-grounded), Scope, Standards
(invariants), Product (mission). Verdicts in §0.

---

## 0. Panel outcome (what changed from v1)

| Reviewer | Verdict | Decisive point |
|---|---|---|
| Adversary | premise-shaky | r2g's diagnose is deterministic stdlib, not a stochastic LLM prompt; skillopt's denoising apparatus (median-of-3 + ε) solves a problem r2g doesn't have. Benchmark circularity worse than v1 admitted. |
| Feasibility | A as-written; B/C with-changes | A is cheap (infra present). B needs a real refactor + the skill's **first-ever LLM dependency**. C cites **CLI verbs that don't exist** and needs a 3-way join. Baseline is **643 tests, not 635**. |
| Scope | split-required | "Three plans in a trench coat." Cut the metric triple, the LLM judge / Wilson CI / verdict enum / cache, and the whole skillopt apparatus. |
| Standards | compliant-with-fixes | No firewall/honesty breach by design, but the §8-R3 test must allowlist legit both-DB readers or it false-fails. 5 required edits. |
| Product | mixed | A advances the mission; C is cheap insurance; **B is hygiene masquerading as the frontier** — zero evidence prose is the bottleneck. Promote cross-platform corroboration instead. |

**Decisions taken:**
- **Q1 → A + C now; B split out.** (unanimous)
- **Q4 → B, if ever built, is fully offline.** No LLM/API dependency enters the
  skill in this plan. (Feasibility + Adversary + Scope)
- **Metrics trimmed** to the honesty asserts + a single rank-1 diagnose
  stability assertion. The gbrain Jaccard@k / latency-multiplier triple is
  *cut* as metric cosplay for a deterministic system. (Scope)
- **Baseline corrected to 643.** (Feasibility)
- A new candidate — **cross-platform corroboration boost** — is recorded in §7
  as the panel's preferred alternative to B (serves the stated mission directly).

## 1. Why (unchanged premise for A + C; B premise retracted)

gbrain and r2g share a shape — agent memory + self-improving loop. Two gbrain
ideas transfer cleanly **without relying on the LLM-retrieval analogy**:

1. **r2g's honesty invariants are hand-checked today.** CLAUDE.md spells out a
   "Fast honesty check" but nothing *executes* it. → Workstream A.
2. **The knowledge store will accumulate conflicting recipes as campaigns
   scale** (a fix that helps nangate45 but regresses sky130 — the exact
   cross-platform failure the mission cares about). Nothing detects it. →
   Workstream C.

**Retracted from v1:** the claim that r2g has a "diagnose prose trainable
parameter" analogous to gbrain's `SKILL.md`. Adversary + Feasibility verified it
does not — `diagnose_signoff_fix.py:83-360` holds diagnostic logic + HINT
strings as inline Python; nothing loads a prose `.md` at runtime. B's true first
task is a refactor to *create* that parameter, which v1 hid. B is therefore
deferred (§6).

**Hard constraint (non-negotiable):** the CLAUDE.md **knowledge-only-learner
firewall** — learner/inference paths read ONLY `knowledge.sqlite`. Python 3.10
stdlib only; **no new network/API dependency** (the skill has none today —
Feasibility confirmed). Prefer editing existing `scripts/` over adding new ones.

## 2. Committed scope

| # | Workstream | Status | Cost |
|---|---|---|---|
| A | Honesty CI gate + lean diagnose-replay assertion | **BUILD NOW** | $0, stdlib |
| C | Deterministic contradiction probe (v1) | **BUILD NOW** | $0, stdlib |
| B | `skillopt` for diagnose prose | **SPLIT to separate spec** (preconditions in §6) | deferred |
| (new) | Cross-platform corroboration boost | **EVALUATE as B-alternative** (§7) | $0, stdlib |

## 3. Workstream A — Honesty / replay CI gate  *(build first)*

**Deliverables (revised per Standards + Feasibility):**

- `r2g-rtl2gds/tests/test_honesty_invariants.py` — executes the CLAUDE.md "Fast
  honesty check" against a seeded `tmp_knowledge_dir` (the **existing**
  `conftest.py` fixture that copies real `schema.sql` + `families.json`):
  - `count(runs.orfs_status='fail') == count(failure_events 'orfs-fail-%')`
  - every `runs` fail/partial row carries a matching `failure_event`
  - `ab_trials` non-empty whenever fail/partial rows exist (the **Gate-A alarm**,
    knowledge/README inv. 20)
  - `failure_events` derivable from `orfs_status` / `orfs_fail_stage`
- **Firewall test (corrected per Standards edit #1):** assert no
  **learner/inference** file imports `journal_db` — scoped to
  `learn_heuristics.py`, `mine_rules.py`, `diagnose_signoff_fix.py`,
  `suggest_config.py`. **Allowlist** the legitimate both-DB readers
  (`knowledge_db.py` [busy_timeout ref], `trace_provenance.py` [README inv. 18],
  `engineer_loop.py`, `ingest_run.py`, `ab_runner.py`, `escalations.py`,
  `journal_action.py`). A naïve "no file imports journal_db" assert false-fails.
- `r2g-rtl2gds/tests/test_diagnose_replay.py` — **one** assertion: a fixed
  `symptom → expected top recipe` returns that recipe at **rank-1** from
  `diagnose_signoff_fix.py`, env-overridable floor. (Scope: drop the Jaccard@k /
  latency triple.)
- **Fixture privacy (Standards edit #3, made concrete):** fixtures use
  placeholder design names only; a check denies any name appearing in the real
  corpus `families.json`. Wired into the A gate, not just PR review. (Reuses the
  existing `fixtures/sample_run_*` placeholder pattern.)

**Why first:** all data + fixture infra already present (Feasibility); $0; turns
the manual firewall/honesty guard into CI; de-risks C.

## 4. Workstream C — Deterministic contradiction probe (v1)  *(build second)*

**Deliverable:** a lean `scripts/reports/` probe (no LLM) that flags **structural
recipe contradictions**: two recipes for the same symptom signature with
**opposite knob direction** where **both claim success**.

**Reality corrections (Feasibility):**
- Knob direction is **not** in `heuristics.json` (recipes store
  `{attempts,successes,failures,wins}` only). The probe must join across
  `heuristics.json` (strategy name + outcomes) × strategy definitions in
  `diagnose_signoff_fix.py:83-209` × actual deltas in
  `fix_events.config_delta_json` (`schema.sql:95`) / `config_lineage.diff_json`
  (`schema.sql:57-66`). Deterministic, but a 3-way join — scope accordingly.
- **Supersession exclusion** (the "old fail + new pass coexist" invariant): there
  is **no `superseded` status** (`recipe_status` = shadow|candidate|promoted).
  Infer supersession from `config_lineage.previous_run_id` / `current_outcome` +
  the `generation` counter on `recipe_status`. Exclude superseded pairs so the
  probe doesn't cry wolf.
- **No fake CLI verbs.** v1 cited `engineer_loop demote`/`supersede`; neither
  exists as a CLI (`demote()` is a Python fn at `recipe_lifecycle.py:109`;
  `supersede` is nowhere). The probe emits a **real** paste-ready command (either
  add a thin `engineer_loop demote` subparser, or point at the existing
  `recipe_lifecycle` entry) — never an invented verb.
- **Output into the existing dashboard panel** (`build_lineage_view.py` +
  `generate_multi_project_dashboard.py`), never auto-applied (CLAUDE.md inv. 5/9).
- **Cut from v1:** LLM judge, Wilson CI, six-member verdict enum, persistent
  content-hash cache. (Scope — framework creep for a deterministic detector.)

**A-gate tie-in:** A asserts "0 unresolved structural contradictions in the
fixture store."

## 5. Standards-required edits applied (checklist)

1. ✅ Firewall test scoped to learner/inference files + allowlist (§3).
2. ✅ §6 per-workstream completion includes "When You Fix a Bug" **step 5**
   (verify both DBs: `failure_events` mirrors `orfs_status`; `ab_trials`
   non-empty) for any ingest/learn-touching change; steps 2–3 marked N/A here
   (these add capability, not a triggering-design fix).
3. ✅ Fixture-privacy rule made concrete + wired into the A gate (§3).
4. ✅ (B only, deferred) optimizer opens `knowledge.sqlite` `mode=ro`; benchmark
   extraction reads knowledge ONLY, never the journal.
5. ✅ Baseline re-confirmed at task-start (currently **643**, not 635).

## 6. Workstream B — `skillopt` (SPLIT to its own spec)

Deferred. **Preconditions before B is even specced:**
- **P1 (evidence — Product).** A's replay data must show diagnose **prose**, not
  recipe coverage / corpus thinness, is measurably blocking closures. Without
  this, B optimizes the wrong layer.
- **P2 (refactor — Feasibility/Q2).** Diagnose must first externalize one
  symptom's HINT block into a runtime-loaded `references/*.md` (the "body-only
  trainable parameter"). Scope to ONE symptom to bound it.
- **P3 (offline — Q4).** v1 is fully offline (template/structural edit proposals
  + the **existing** LCB gate in `ab_runner.py` / `recipe_lifecycle.py`). Any
  reflect-LLM is a separate, clearly-fenced follow-up that introduces the skill's
  first API dependency — its own decision.
- **P4 (label-provenance gate — Adversary/R1).** Before trusting the benchmark,
  audit what fraction of held-out-platform "correct recipes" share a
  `symptom_id` / recipe lineage with the train set. High overlap ⇒ benchmark is
  self-referential ⇒ B invalid as designed. The held-out *platform* split leaks
  by construction precisely because r2g's thesis is cross-platform transfer.

## 7. Candidate to evaluate INSTEAD of B — cross-platform corroboration boost

Product's promotion (from v1's out-of-scope list). The mission word is
**transfer**; this serves it head-on and is $0/stdlib:

- In `diagnose_signoff_fix.py` ranking, **boost a recipe when independent
  designs/platforms corroborate it** (already-tracked `evidence_designs` /
  `symptoms` cross-platform pooling) and **demote single-design flukes**.
- Add an `--explain` mode (gbrain pattern) printing why a recipe ranked: evidence
  count, cross-platform corroboration, A/B verdict, each boost that fired.
- **Evaluate against B for the next cycle** once A+C land. Likely higher leverage
  than tuning prose, and it's testable via Workstream A's replay harness.

## 8. Sequencing, risks, success criteria

```
A (honesty + lean replay)  ──►  C (deterministic probe)  ──►  evaluate §7 vs B-spec
   $0, infra present            $0, 3-way join + real verb        (next cycle)
```

**Risks (revised):**
- R1 — *C underestimated*: the "one SQL query" is a 3-way join + supersession
  inference. Mitigation: scope v1 to the highest-value symptom families; expand
  later.
- R2 — *Firewall test false-fail*: fixed via the §3 allowlist (Standards #1).
- R3 — *Fixture privacy*: real design names in committed fixtures. Mitigation:
  §3 deny-list gate.
- R4 — (B, deferred) circularity / first-ever LLM dep — see §6 P3/P4.

**Success criteria:**
- A: CI fails loudly on any honesty-invariant breach or rank-1 diagnose
  regression; firewall test correctly scoped; fixtures privacy-clean; suite
  green (no regression from **643**).
- C: probe surfaces structural recipe contradictions with severity + a **real**
  paste-ready (never auto-applied) command; supersession excluded via lineage;
  wired into the existing dashboard panel + the A gate.
- All: CLAUDE.md honesty invariants intact; **no new runtime dependency on
  `journal.sqlite`; no LLM/API dependency added.**

## 9. Open questions remaining (post-panel)

- **Q-a (resolved 2026-06-18):** **Add a thin `engineer_loop demote` CLI verb.**
  `recipe_lifecycle.demote()` already exists; the new subparser wraps it so the
  probe emits a real paste-ready operator command (never an invented verb).
- **Q-b (resolved 2026-06-18):** **Yes — §7 corroboration boost folded in as
  Workstream D.** It is $0/stdlib, testable through A's replay harness, and serves
  the mission word *transfer* head-on (Product's promotion). Scope justified: the
  scout confirmed the data (`platforms_seen`/`by_platform`) already exists, so it
  is a bounded tiebreaker, not new plumbing.
- **Q-c (resolved):** B's LLM dependency — **no**, not in this plan (Q4 closed).
- **Q-d (resolved):** scope — **A + C now, B split** (Q1 closed).

## 10. Implementation (2026-06-18, branch `feat/gbrain-absorption`)

Delivered via a 3-phase dynamic workflow (C∥D parallel → A → verify), then a
maintainer verification + contract-correction pass. **Suite 643 → 676 green** (8
env-gated skips unchanged); **real committed `knowledge.sqlite` passes all four
honesty gates**; contradiction probe clean on the real store.

**Workstream A — honesty / replay CI gate** (`tests/`):
- `test_honesty_invariants.py` — four reusable, dependency-light checks
  (`check_fail_event_parity` = H3 count; `check_every_fail_has_event` = per-run
  offender list; `check_ab_trials_nonempty_when_failures` = Gate-A; 
  `check_failure_events_derivable` = signature↔`orfs_fail_stage`), each with a
  POSITIVE pass and a NEGATIVE "fails-loudly" test. Synthetic `test_*` seed names +
  `assert_synthetic_names` privacy gate (scoped to NEW seed data — a repo-wide
  deny-list would false-fail the legit public-benchmark fixtures `aes128_core`/
  `black_parrot`). Contradiction tie-in: clean fixture store ⇒ `find_contradictions == []`.
- `test_firewall_knowledge_only.py` — asserts the 4 learner/inference files never
  import `journal_db`; allowlists the legit both-DB readers.
- `test_diagnose_replay.py` — one rank-1 `build_plan` assertion (unambiguous winner,
  stable under D's tiebreaker).

**Workstream C — deterministic contradiction probe** (`scripts/reports/detect_contradictions.py`):
- Data-driven (NOT strategy-name): joins `fix_events` (symptom+strategy spine) ×
  `config_lineage.diff_json` (authoritative `{old,new}` direction) — because knob
  direction is unrecoverable from strategy names and absent from `heuristics.json`.
- Success-gated (both arms cleared) + conservative supersession exclusion (lineage
  `previous_run_id` chain **and** `recipe_status.generation`). Emits severity + the
  real paste-ready `engineer_loop.py demote …` command; **never auto-applies**.
- Wired into `build_lineage_view.build_view()` (`contradictions` key, defensive
  try/except → `[]`) + a `contradiction_panel` in the dashboard. New
  `engineer_loop demote` subparser + `test_engineer_loop_demote.py`.

**Workstream D — cross-platform corroboration boost** (`fix_model.py` + `diagnose_signoff_fix.py`):
- `rank_strategies` gains a `platform_count` SORT TIEBREAKER between
  `mean_outcome_score` and `static_pos` — corroborated-across-platforms beats a
  single-design fluke, but it can **never** override a clearly stronger clearer
  (defaults 0 ⇒ legacy ranks byte-unchanged; the confidence floor is untouched).
- `--explain` mode prints WHY each recipe ranked (evidence, platform corroboration,
  provenance/A-B verdict). Tests: `test_corroboration_boost.py`, `test_diagnose_explain.py`.

**Maintainer correction (grounding beat the plan prose).** §3 said "every
fail/**partial** row carries a matching `failure_event`." Verified against the
ingest projection contract (`ingest_run.py:782`: events are written iff
`orfs_status=='fail'`) and `_derive_orfs_status` (`:410-415`): a `partial` is the
HONEST incomplete state — *no stage reported `fail`*, and `orfs_fail_stage` names the
furthest stage **reached**, not one that aborted. Demanding an event there would
FABRICATE a failure that never happened. So `check_every_fail_has_event` is scoped to
`fail` only (a per-run complement to H3, not a partial-coverage gate). This flipped
the agent's `test_partial_run_without_event_is_*` from "is_flagged" to "is_honest"
and removed the partial's seeded event. Net: all 4 gates pass the real store
**honestly** (the 8 event-less corpus partials are this honest class, not a backlog),
not by suppression. The one pre-existing test asserting `build_view`'s exact key set
was updated to include `contradictions`.

**Deferred unchanged:** B (`skillopt`) stays split per §6 P1–P4. Honesty invariants
intact; no new `journal.sqlite` runtime dependency; no LLM/API dependency added.
