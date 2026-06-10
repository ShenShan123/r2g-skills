# Engineer Loop (autonomous closed learning loop) — Design Spec

**Date:** 2026-06-09
**Skill:** `r2g-rtl2gds` (extension — no new skill)
**Status:** Design (brainstorming) — awaiting user review of this spec. Spec only; no
implementation in this session.
**Depends on:** `2026-06-05-fix-learning-loop-design.md` (3-tier fix store, live),
`2026-06-09-symptom-indexed-memory-design.md` (symptom index, implemented on main).
**Authors:** user5 + agent. Grounded in a very-thorough read-only inventory of the existing
learning machinery (knowledge tiers, fix loop, symptom memory, A/B harness, dashboards).

---

## 1. Goal & one-paragraph summary

Make the skill behave like a **back-end (PD) engineer with a closed learning loop**: run the
PD flow, observe every bug, ingest the full **action trajectory** (not just strategy-level
steps), **propose** fix recipes, validate each proposal with an **inline A/B comparison**, and
loop — autonomously, design after design, so the skill measurably **gets stronger as it
processes more RTL designs** and transfers experience to unseen ones. Today the loop is
semi-open: observe(human) → ingest(auto) → learn(auto) → apply(human), the A/B harness has
never run, recipes go live without validation, and trajectories stop at strategy granularity.
This design closes the loop with a **two-tier architecture**: a deterministic, resumable
**campaign orchestrator** (`engineer_loop.py`) executes the bulk
flow→observe→ingest→propose→fix→A/B cycle unattended, and an **agent escalation tier** handles
only what the deterministic core cannot (unknown symptoms, exhausted catalogs, unseen
crashes), authoring *new* strategies that re-enter the same evidence pipeline as shadow
candidates. There are **no human gates**; safety is machine-enforced (absolute hard clamps +
evidence-only recipe promotion + auto-demotion).

## 2. Key decisions (locked, from user Q&A)

1. **Extend `r2g-rtl2gds`** — not a new skill. Reuses the trained store (780+ runs), symptom
   index, fix loop, and safety clamps. No duplication.
2. **Bug-fix learning first; optimization learning second.** This spec is Phase 1
   (violation-fix loop closure). Continuous PPA-optimization learning (knob→WNS/area/power
   sensitivity per design shape) is Phase 2, a separate future spec.
3. **Fully autonomous, no human gates.** Recipe promotion is decided by machine evidence
   (A/B verdicts), not review. Consequence: every gate that used to be a human becomes an
   explicit, tested mechanism (§7).
4. **Action-level trajectory capture.** A new append-only **Tier-0 `actions` table** records
   every discrete action (each config-knob delta, SDC edit, stage re-entry, tool invocation +
   exit status, escalation, A/B launch, promote/demote), enabling credit assignment inside
   stacked fixes.
5. **Recipe-level inline A/B.** Each new/changed recipe from the learner is validated by an
   automatic matched-design A/B (new recipe ranked first vs. prior ranking). Win → promote;
   loss/inconclusive → stays shadow. The existing never-run global eval harness
   (`eval_heuristics.py`) is reused for verdict semantics, not replaced.
6. **Strength metric = first-pass + iterations-to-clean.** Rolling held-out: every new
   design's *first* attempt is by construction unseen; stamp `first_attempt_clean`,
   `fix_iters_to_clean`, `wall_s_to_clean`, and the heuristics **generation counter** at
   ingest. Strength = first-pass clean rate trending up and median iterations/wall-clock
   trending down across generations.
7. **Architecture C: hybrid.** Deterministic loop core + agent escalation tier. The catalog
   can *grow* (agent-authored strategies), not just re-rank, while the bulk loop stays cheap,
   reproducible, and immune to session limits.

## 3. Background — what exists vs. what this adds

### 3.1 Already live (reused as-is)
- **3-tier fix store:** `fix_events` (Tier-1 raw, session-keyed) → `fix_trajectories`
  (Tier-2, per-episode path/outcome, never archived) → `heuristics.json:fix_recipes`
  (Tier-3 aggregate). Auto-learn on ingest (`fix_log_manager.manage()`).
- **Symptom index:** `symptom_id`/`signature_json` on all raw tiers; `symptoms` +
  `lessons` tables; pooled `symptoms[sid]` projection in `heuristics.json`.
- **Fix execution:** `fix_signoff.sh` adaptive-budget iteration loop; `diagnose_signoff_fix.py`
  + `fix_model.py` Beta(1,1)-ranked strategy proposal; hard safety clamps.
- **Honest A/B semantics:** `eval_heuristics.py` (`is_success` quality + wall-clock-only cost;
  cheaper-but-both-fail = inconclusive, never a win).
- **Observability:** `build_lineage_view.py` panels, `monitor_health.py` degradation alerts.

### 3.2 Dormant (activated by this design)
- Pooled cross-platform symptom priors → used in live ranking (`fix_model` `pooled` param).
- `lessons_for_symptom()` surfaced at the fix decision point (consumed by the agent tier).
- Timing-fix journal (`check_timing.py --journal`) always-on + a first timing strategy
  catalog (`period_relax` is already proven: 3 attempts / 2 successes).
- A/B harness execution (Phase-0 small-design campaign becomes the loop's first live run).

### 3.3 Missing (built by this design)
- Autonomous orchestration (campaign ledger, state machine, A/B scheduling, escalation).
- Tier-0 action journal + credit assignment data.
- Recipe lifecycle (`shadow → candidate → promoted`, auto-demotion).
- Strength metrics report + dashboard panel.
- Confidence floor on cross-platform ranking (untried strategy needs pooled n ≥ N to outrank
  a locally proven one).

## 4. Architecture

```
                    ┌─────────────────── deterministic core ────────────────────┐
 design queue ──► engineer_loop.py ──► flow scripts ──► reports/*.json
 (resumable          │    ▲                                   │
  ledger)            │    │                              ingest_run.py
                     │    │                                   │
                     │    │              runs.sqlite (fix_events + NEW actions Tier-0)
                     │    │                                   │
                     │    │                            learn_heuristics.py
                     │    │                                   │
                     │    │                       recipe diff → recipe_lifecycle.py
                     │    │                                   │ (shadow→candidate)
                     │    └── ab_runner: matched-design A/B ──┘
                     │            win → promote (machine gate, no human)
                     │
                     └─► escalations queue ──► agent tier (drains, authors NEW
                          (unknown symptom,     shadow strategies + predicates,
                           catalog exhausted,   journals actions) ──► back into
                           unseen crash)        the same A/B lifecycle
```

The loop core is plain Python: survives session limits, runs for days, fully resumable. The
agent tier is the only LLM consumer and runs only when the core hits something it cannot
handle. A/B arms ride the **same ledger and execution path** as regular designs — one
execution path, no parallel infrastructure.

## 5. Components

### 5.1 `scripts/loop/engineer_loop.py` — campaign orchestrator
- Input: design-queue ledger (promotes the proven resumable-ledger pattern from
  `tools/sky130_campaign.py` into the skill). Entries: design, RTL path, platform, kind
  (`normal` | `ab_arm`), state, attempt history, budgets.
- Per-design state machine: `pending → flow → signoff → fixing → clean | escalated |
  abandoned`. Ledger checkpoints on every transition; kill/crash → restart resumes.
- Calls existing scripts only: `run_orfs.sh`, `run_drc.sh`, `run_lvs.sh`, `run_rcx.sh`,
  `fix_signoff.sh`, `ingest_run.py`. No new flow logic.
- Concurrency: configurable workers honoring the hard rules — unique
  `DESIGN_NAME`+`FLOW_VARIANT` pairs, **single concurrent LVS for >100K-cell designs**,
  `PLACE_DENSITY_LB_ADDON ≥ 0.10` untouched.
- After each ingest, triggers the learn → recipe-diff → A/B-enqueue chain (§5.3, §5.4) and
  the strength-report rebuild (§5.6).

### 5.2 Tier-0 action journal (`actions` table)
- Append-only, one row per discrete action:
  `action_id`, `ts`, `run_id`/`fix_session_id` (nullable), `design`, `platform`,
  `actor` (`loop` | `agent` | `operator`), `action_type` (`config_knob_delta` | `sdc_edit` |
  `stage_rerun` | `tool_invoke` | `escalate` | `ab_launch` | `promote` | `demote` | …),
  `payload_json` (knob name + old/new value, stage, exit code, …), `parent_action_id`
  (nullable, for grouping a stacked fix).
- Producers: `fix_signoff.sh` (each sub-step), `diagnose_signoff_fix.py` (each knob delta
  **individually**, not just the marked block), `run_orfs.sh` (stage entry/exit/exit-code),
  `engineer_loop.py` (its own decisions), and a new `knowledge/journal_action.py` CLI so the
  agent tier journals identically.
- Credit assignment (Phase 1 scope): record everything + two consumers — (a)
  last-action-before-clear attribution in the learner, (b) the A/B mechanism itself, which
  can ablate a single ranked-first strategy. Full causal attribution is explicitly out of
  scope for Phase 1.
- Size policy: `actions` joins the existing `fix_log_manager` archival policy (raw tier;
  Tier-2 derivations survive archival, same invariant as `fix_events`).

### 5.3 `knowledge/recipe_lifecycle.py` — recipe states + generation counter
- Recipes (both Tier-3 learned entries and static-catalog strategies added by the agent
  tier) carry a status: `shadow → candidate → promoted` (+ `demoted`), persisted in a new
  `recipe_status` table (§8).
- After each learn cycle, diff `heuristics.json` recipes against the previous **generation**
  (monotonic counter stored in the DB and stamped into `heuristics.json`); new/changed
  entries become `candidate` and are enqueued for A/B.
- **Only `promoted` recipes affect live ranking** in `diagnose_signoff_fix.py`. Shadow and
  candidate recipes are logged but inert in arm-A/live runs.
- Bootstrap: recipes already validated by live use before this design ships (e.g.
  `period_relax` on iccad2015) are grandfathered as `promoted` with provenance
  `grandfathered:<date>`; everything new goes through A/B.

### 5.4 `knowledge/ab_runner.py` — inline recipe A/B
- For each `candidate` recipe (keyed by symptom signature + platform): select matched
  designs from `run_violations` history (same `symptom_id`; **cheapest matching designs
  first** — Phase-0 small-design-first decision), default 2 matched designs per trial.
- Arm A: prior ranking (candidate excluded). Arm B: candidate ranked first. Arms are emitted
  as ordinary ledger entries (`kind=ab_arm`) with distinct project dirs / `FLOW_VARIANT`s, so
  the same loop executes them.
- Verdict: existing honest criteria (`knowledge_db.is_success`, wall-clock cost,
  iterations-to-clean; crash → `inconclusive`; cheaper-but-both-fail → `inconclusive`, never
  a win). Stored in a new `ab_trials` table:
  `trial_id`, `recipe_key`, `symptom_id`, `platform`, `arm_a_run_id`, `arm_b_run_id`,
  `verdict` (`win` | `loss` | `inconclusive`), `metrics_json`, `ts`.
- Win → `promote` (journaled action); loss/inconclusive → reverts from `candidate` to
  `shadow` with the evidence attached. Promotion requires the configured minimum
  matched-design count (default 2).

### 5.5 Escalation queue + agent tier
- `escalations` table: `escalation_id`, `design`, `run_id`, `symptom_id` (nullable),
  `reason` (`unknown_symptom` | `catalog_exhausted` | `unseen_crash` |
  `repeated_regression`), `status` (`open` | `drained` | `wont_fix`), `notes`.
- The loop never blocks on an escalation: it records it and moves to the next design.
- Agent runbook: new `references/engineer-loop.md` + a SKILL.md section ("Escalation
  drain"). The agent diagnoses with symptom lookup + `lessons_for_symptom()` +
  `failure-patterns.md`, authors a **new strategy** into the static catalog as `shadow` with
  a symptom predicate, journals every action via `journal_action.py`, and requeues the
  design. Agent-authored strategies get **no special trust** — same A/B lifecycle.
- Prose: only the agent tier writes `failure-patterns.md` (authored, not auto-merged) —
  existing invariant preserved.

### 5.6 `scripts/reports/build_strength_report.py` — strength metric
- At ingest, every run is stamped with the current heuristics **generation** plus
  `first_attempt_clean` (bool, first attempt for that design+platform),
  `fix_iters_to_clean`, `wall_s_to_clean` (NULL until clean).
- Report: trend of first-pass clean rate and median iterations/wall-clock-to-clean vs.
  generation; per-symptom **transfer evidence** (recipe learned on designs {X…}, cleared
  unseen design Y at generation G). Read-only projection (same discipline as
  `build_lineage_view.py`); gets a dashboard panel.

### 5.7 Dormant-path activation (prerequisite work items)
1. `diagnose_signoff_fix.py` passes pooled symptom priors into `fix_model.rank_strategies`.
2. **Confidence floor:** an untried strategy ranked purely on pooled/cross-platform priors
   cannot outrank a locally proven (≥1 success same-platform) strategy unless pooled
   attempts ≥ N (default 5). Implemented in `fix_model.py`; unit-tested.
3. `lessons_for_symptom()` output included in `diagnose_signoff_fix.py --list` JSON (the
   agent tier's decision context).
4. Timing: `check_timing.py --journal` becomes default-on in the loop; timing strategy
   catalog seeded with `period_relax` + `utilization_reduce` so timing recipes flow through
   the same lifecycle.

## 6. Data flow (one full loop turn)

1. `engineer_loop` pulls the next ledger entry → runs the flow via existing scripts.
2. Reports land → `ingest_run.py` ingests (now also: Tier-0 `actions`, strength stamps,
   generation counter).
3. Violations → `fix_signoff.sh` iterates; `diagnose_signoff_fix.py` ranks **promoted**
   recipes with pooled symptom priors + confidence floor; every sub-action journaled;
   re-ingest.
4. Auto-learn fires → `learn_heuristics.py` rebuilds → `recipe_lifecycle` diffs vs. previous
   generation → new/changed recipes → `candidate` + A/B enqueue.
5. `ab_runner` emits arm entries into the ledger → the same loop executes them → verdict →
   promote/demote (journaled).
6. Unfixable → `escalations` row; loop moves on. Agent tier drains when run; new shadow
   strategies re-enter at step 4–5.
7. `build_strength_report.py` re-projects; dashboard updates.

## 7. Error handling & safety (machine gates replace human gates)

- **Hard clamps absolute and unchanged:** `PLACE_DENSITY_LB_ADDON ≥ 0.10`, design-type caps,
  platform gates. Learning never overrides them; the loop never edits rule decks.
- **Promotion is evidence-only:** A/B win under honest criteria, minimum matched-design
  count. A promoted recipe showing `repeated_regression` in live use (2 consecutive
  regressions on its symptom) is **auto-demoted** to shadow and escalated.
- **Prose never auto-merged:** the loop writes only structured tiers; `failure-patterns.md`
  is written only by the agent tier as an author. `mine_rules.py` candidates remain a review
  queue.
- **Resumability:** ledger checkpoint per state transition; per-design wall-clock budget and
  stage timeouts; known workaround flags (`PLACE_FAST`, `ROUTE_FAST`, …) applied via the
  existing diagnosis path and journaled as actions.
- **DB safety:** WAL + `busy_timeout`; single-writer discipline — workers write journals
  into their project dirs; only the loop process ingests into `runs.sqlite`.
- **A/B honesty inherited:** wall-clock-only cost, no fabricated CPU-hours; crashed arms are
  `inconclusive`; cheaper-but-both-fail is never a win.
- **Family/name never becomes a key again:** all new tables key on `symptom_id` +
  conditioning attributes, consistent with the symptom-indexed-memory spec.

## 8. Schema changes (all migrations legacy-DB safe, like prior migrations)

| Table | New/changed | Purpose |
|---|---|---|
| `actions` | NEW (Tier-0) | append-only action journal (§5.2) |
| `ab_trials` | NEW | one row per recipe A/B trial (§5.4) |
| `escalations` | NEW | open problems for the agent tier (§5.5) |
| `recipe_status` | NEW | recipe lifecycle state + provenance + generation (§5.3) |
| `runs` | +cols | `heuristics_generation`, `first_attempt_clean`, `fix_iters_to_clean`, `wall_s_to_clean` |
| `heuristics.json` | +field | top-level `generation`; per-recipe `status` view folded from `recipe_status` |

## 9. Testing

- **Unit (extends the 417-test pytest suite):** ledger state-machine transitions; recipe
  diff/lifecycle (shadow never ranks in live/arm-A); A/B verdict honesty cases; `actions`
  idempotent ingest; generation stamping; confidence-floor ranking; auto-demotion trigger.
- **Integration:** dry-run mode with a mocked flow runner drives ~10 synthetic designs
  through the complete loop — including one forced escalation and one forced A/B
  promotion — deterministically in CI. No real EDA tools needed.
- **Live validation:** the already-primed Phase-0 small-design campaign becomes the loop's
  first real run, exercising ingest→learn→candidate→A/B→promote end-to-end on cheap
  nangate45 designs before any large corpus.

## 10. Phasing & out of scope

- **Phase 1 (this spec):** everything above — violation-fix loop closure, Tier-0 capture,
  recipe lifecycle + inline A/B, escalation tier, strength metrics, dormant-path activation.
- **Phase 2 (separate future spec):** optimization learning — PPA-delta trajectories,
  knob-sensitivity recipes conditioned on design shape, continuous-metric A/B, and full
  causal credit assignment over Tier-0.
- **Out of scope entirely:** multi-clock/CDC/DFT designs (existing escalate-to-user rule
  stands); editing rule decks; auto-writing prose; replacing the existing global eval
  harness (it remains available as a regression backstop an operator can run).
