# Engineer Loop — Runbook

## 1. Loop Overview

The engineer loop is a deterministic, resumable campaign orchestrator
(`scripts/loop/engineer_loop.py`) that drives the full PD flow unattended across a queue of
designs. Its core cycle is: pull the next design from a JSONL ledger → run the flow scripts
(`run_orfs.sh`, `run_drc.sh`, `run_lvs.sh`, `run_rcx.sh`) → ingest results into the
knowledge store (`ingest_run.py`) → auto-learn (`learn_heuristics.py`) → diff the new recipe
generation against the prior one (`recipe_lifecycle.diff_and_enqueue`) → enqueue new or
changed recipes as A/B candidates → the same loop executes matched-design A/B arms
(`ab_runner.record_trial`) → net decisive wins promote the recipe to live ranking; net losses
demote it to shadow (an `inconclusive` carries no information and never demotes — status is a
function of the FULL trial corpus, 2026-06-24). The loop never blocks: when the deterministic
core cannot handle a design (unknown symptom, exhausted catalog, unseen crash, or repeated
regression), it opens an escalation record and moves on. The agent tier drains escalations by
diagnosing the root cause and authoring new shadow strategies, which then re-enter the same
A/B lifecycle before affecting live ranking.

```
                    ┌─────────────────── deterministic core ────────────────────┐
 design queue ──► engineer_loop.py ──► flow scripts ──► reports/*.json
 (resumable          │    ▲                                   │
  ledger)            │    │                              ingest_run.py
                     │    │                                   │
                     │    │      journal.sqlite (Tier-0: commands, log/report
                     │    │        summaries, tool bugs — gitignored, evidence)
                     │    │              knowledge.sqlite (fix_events,
                     │    │                trajectories — git-tracked, conclusions)
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

## 2. Running a Campaign

### CLI Commands

```bash
# Add one project to the campaign ledger
python3 scripts/loop/engineer_loop.py add \
    --ledger design_cases/_batch/campaign.jsonl \
    --project design_cases/my_design \
    [--platform nangate45]

# Run the campaign (process up to N designs, default: unlimited)
python3 scripts/loop/engineer_loop.py run \
    --ledger design_cases/_batch/campaign.jsonl \
    [--max N]

# Inspect current state of each design in the ledger
python3 scripts/loop/engineer_loop.py status \
    --ledger design_cases/_batch/campaign.jsonl

# Fire A/B trials for already-enqueued candidate recipes WITHOUT re-running the
# normal designs (the production "drain the A/B queue" button — see Gate A below).
python3 scripts/loop/engineer_loop.py ab-drain \
    --ledger design_cases/_batch/ab.jsonl [--n-designs 2]

# Force a grandfathered recipe into 'candidate' for explicit A/B re-validation.
python3 scripts/loop/engineer_loop.py ab-enqueue \
    --symptom <sid> --design-class crypto/small \
    --platform sky130hd --strategy antenna_diode_repair
```

### Tier −1 Gate A — making the A/B loop fire on the production path

**The gap (diagnosed 2026-06-16).** The `shadow → candidate → promoted` pipeline
lived *only* inside `engineer_loop.run`, which never drove a production campaign (no
`design_cases/_loop` ledger ever existed). The batch driver ingests + calls
`learn_heuristics.learn()` directly — and `learn()` never enqueued candidates. So
across 1267 runs `recipe_status` stayed empty and `ab_trials` = 0: the substrate every
signal-dependent win builds on had **never produced a verdict**.

**The fix.** `learn_heuristics.learn()` now calls `recipe_lifecycle.diff_and_enqueue`
after writing `heuristics.json` (diffing against the prior on-disk copy), so **every**
learner rebuild — batch or loop — enqueues new/changed recipes as candidates. A
standalone `ab-drain` then plans, runs, and judges the arms for those candidates
without re-running the normal designs. Because the existing corpus's recipes are all
*grandfathered* (absent `recipe_status` row == promoted), nothing auto-enqueues on a
steady-state corpus; use `ab-enqueue` to force a chosen recipe into `candidate` for a
real validation campaign. The orchestration is unit-proven end-to-end in
`tests/test_gate_a_ab_loop.py` (production-path `learn()` → candidate → `ab-drain` →
`ab_trials` row + recipe transition). The *real* EDA campaign (a non-mocked `ab-drain`
on the live corpus) needs hours of flow time — that is Gate B.

### Tier −1 Gate B — seed the dense-reward gradient (operator, compute-bound)

`outcome_score` (Win 1) is near-dataless on the existing corpus: only ~12/1267 runs
carry `drc_violations > 0` and ~2 have a usable before→after fix pair. Gate B seeds
the gradient with a deliberately partial-progress campaign, and exercises Gate A's
A/B loop at scale. This needs **hours of real EDA flow time** — it is an operator
procedure, not unit-testable:

1. **Target the difficulty bands that REACH signoff and MISS** — cell-dense and
   congested designs that hit DRC/LVS violations (not the ones that abort at place).
   Add them to a campaign ledger and run them; the signoff-fixing loop records
   before/after counts per iteration into `reports/fix_log.jsonl`.
   ```bash
   python3 scripts/loop/engineer_loop.py add --ledger design_cases/_loop/gateB.jsonl \
       --project design_cases/<congested_design> --platform sky130hd
   python3 scripts/loop/engineer_loop.py run --ledger design_cases/_loop/gateB.jsonl
   ```
2. **Exit criterion:** the corpus carries ≥30–50 runs with `drc_violations > 0`
   **and** a populated `fix_log.jsonl` before/after pair, spanning ≥3 bands — enough
   for `outcome_score`'s VRR term to be non-degenerate and for r2g-bench (Win 3) to
   have signal. Ingest **honestly** (every run, per the invariants).
3. **Fire Gate A's real trial on the live corpus** (the existing recipes are
   grandfathered, so enqueue one explicitly, then drain):
   ```bash
   python3 scripts/loop/engineer_loop.py ab-enqueue \
       --symptom <sid> --design-class <type/size> --platform sky130hd --strategy <recipe>
   python3 scripts/loop/engineer_loop.py ab-drain --ledger design_cases/_loop/ab.jsonl
   # confirm: ab_trials gains a row; the recipe transitions candidate -> promoted/shadow.
   ```
4. **Win 5 (5b) feature backfill** — emit pre-route vectors where synth survives,
   then re-ingest (designs without a preserved `synth/synth.log` need a re-synth):
   ```bash
   python3 tools/backfill_presynth_features.py design_cases --ingest \
       --db r2g-skills/signoff-loop/knowledge/knowledge.sqlite
   ```
5. **Score r2g-bench** after ingesting the held-out designs:
   ```bash
   python3 knowledge/eval_heuristics.py bench --db knowledge/knowledge.sqlite
   ```
6. **Re-validate both DBs** (the "When You Fix a Bug" honesty checklist): the
   `fail`-rows == `orfs-fail`-events count must still hold, and no clean run's
   `is_success` may have flipped after re-ingesting `outcome_score`.

### Tier −1 Gate B — FIRED on the live corpus (2026-06-16)

Running Gate B for real surfaced a **second blocker on top of Gate A**, and then closed
both end-to-end:

- **Blocker 2 — A/B subject-space mismatch (`ab_runner.plan_trial`).** `plan_trial` picked
  A/B subjects only from `run_violations`, which is the run's **post-fix** snapshot. A
  symptom that gets **successfully fixed** (e.g. antenna) therefore has **no rows** there —
  so the recipes that actually *win* could never be A/B'd, even after Gate A enqueued them
  (`plan_trial(antenna 02d5eba1) → None`, verified). This is the same fixture-vs-production
  trap as Gate A: the Gate A integration test had to hand-insert a `run_violations` antenna
  row to make `plan_trial` work, masking the gap. **Fix:** `plan_trial` now falls back to the
  recipe's `evidence_designs` (the **pre-fix** exhibitors the learner records in
  `heuristics.symptoms[sid]`), resolved to on-disk project dirs via `runs.project_path`
  (Tier-2 match levels `evidence_platform` / `evidence_pooled`). Test:
  `test_plan_trial_falls_back_to_recipe_evidence_designs`.
- **A real catalog strategy for the pending sky130 designs — `density_relief`.** The 9
  pending sky130hd DRC-fail designs all carried genuine metal/via **spacing** residuals
  (`m3.2`, `via.4*`, `via_OFFGRID`; symptom `f670d8e567`) that v1 deliberately left
  unhandled. A pilot showed lowering `CORE_UTILIZATION` clears them (eeprom_top 4→0 at
  20→12), so `diagnose_signoff_fix._routing_drc_strategies` now offers `density_relief`
  (real layout change, deck never relaxed; see signoff-fixing.md). Driving 5 of the 9 through
  `fix_signoff.sh` cleared **all 5** (34→0, 20→0, 10→0, 6→0, 4→0) → 5 newly fully-signed-off
  sky130 designs + 5 `before→after` fix episodes seeding the dense-reward gradient.
- **Gate A fired live.** `learn_heuristics.learn()` derived the `density_relief` recipe for
  the m3.2 symptom (previously empty) and `diff_and_enqueue` enqueued it as a `candidate`
  (`recipe_status` 0→2, provenance `learner_diff`) — the first time the production learner
  enqueued on this store.
- **Gate B fired live.** `engineer_loop ab-drain` planned arms on baseline m3.2 designs
  (`spi_controller`, `RV32I_Memorycontroller`), ran arm A (`--exclude density_relief`, stays
  dirty) vs arm B (`--rank-first density_relief`, clears), and `judge_repeated` recorded a
  verdict into `ab_trials`. **Result: `ab_trials` 0→2, both `win`** — arm A (control) stayed dirty
  (`escalated`) while arm B cleared on both `spi_controller` (4→0) and `RV32I_Memorycontroller`
  (84→0); the `logic/small density_relief` recipe transitioned **`candidate → promoted`**
  (provenance `ab_trial:2`). The Gate A signature (`ab_trials=0` alongside fail/partial rows) is now
  cleared on the live corpus — the first end-to-end A/B verdict ever recorded on this store.

### Ledger Format and Resumability

The ledger is a JSONL file: one line per state transition, last-state-wins. This means:

- Killing or crashing the loop mid-run leaves the ledger intact and fully consistent.
- Restarting with the same `--ledger` path resumes exactly where the loop left off; no design
  is reprocessed unless its state is `pending` or `flow` (the in-progress states).
- States: `pending → flow → signoff → fixing → clean | escalated | abandoned`.
- A/B arms are ordinary ledger entries (`kind=ab_arm`) that the same loop executes.

### Environment Knobs

| Variable | Effect |
|---|---|
| `R2G_JOURNAL=0` | Disable journaling to `journal.sqlite` (skips all journal DB writes) |
| `R2G_JOURNAL_DB=<path>` | Override the journal DB path (default: `knowledge/journal.sqlite`) |
| `R2G_LOOP_RUN_FLOW=<script>` | Override the flow subprocess script (testing) |
| `R2G_LOOP_FIX=<script>` | Override the fix subprocess script (testing) |
| `R2G_LOOP_INGEST=<script>` | Override the ingest subprocess script (testing) |
| `R2G_FIX_EXCLUDE=<strategy>` | Exclude a strategy from the fix ranking (A/B arm knob) |
| `R2G_FIX_RANK_FIRST=<strategy>` | Force a strategy to rank first (A/B arm B knob) |
| `R2G_AB_REPEATS=<k>` | Win 2: repeats per A/B arm side (default **k=2**). The verdict is the lower-confidence bound (mean − z·stderr) over the k repeats, so one lucky run cannot promote a recipe (the LVS-crash heisenbug). k=3 is opt-in for high-stakes promotions; each k is a k× wall-clock multiplier on the already-slow A/B path |
| `R2G_FIX_DEAD_AFTER=<n>` | Dead-fix gate threshold (default 2): a strategy with ≥n terminal failures and zero clears on THIS design+check is skipped by auto-apply (2026-07-04) |
| `R2G_FIX_RETRY_DEAD=1` | Disable the dead-fix gate (restore pre-2026-07-04 always-retry) |
| `R2G_MINE_AUTORUN=0` | Skip the automatic `mine_rules.mine` refresh of `failure_candidates.json` at ingest/learn time (default: runs) |

`R2G_FIX_EXCLUDE` and `R2G_FIX_RANK_FIRST` are consumed by `fix_signoff.sh`, which passes
`--exclude` / `--rank-first` to `diagnose_signoff_fix.py` to implement A/B arm separation.

### Phase-1 Constraints (Hard)

- **Workers = 1.** Phase-1 runs single-process. Parallel campaigns require separate ledgers
  on separate machines.
- **Unique `DESIGN_NAME` + `FLOW_VARIANT`.** Never run two configs with the same design name
  and flow variant concurrently; `run_orfs.sh` derives `FLOW_VARIANT` from the project dir
  basename — keep project names unique within a `DESIGN_NAME`.
- **Single concurrent LVS for designs > 100 K cells.** Each KLayout LVS process uses 3–5 GB
  RAM; concurrency causes wall-time inflation. With workers=1 this is automatically satisfied.

## 3. Escalation Drain (Agent Runbook)

When `engineer_loop.py` cannot handle a design it opens an escalation and moves on. The
agent drains these periodically. **Agent-authored strategies carry no special trust — they
must win their A/B trial before promoting to live ranking (decision 7).**

### Step-by-step

**(a) List open escalations**

```bash
python3 -c "
import sys; sys.path.insert(0,'r2g-skills/signoff-loop/knowledge')
import escalations, knowledge_db
conn = knowledge_db.connect()
for e in escalations.list_open(conn):
    print(e)
"
```

Each record includes `escalation_id`, `design`, `project_path`, `run_id`, `symptom_id`
(may be NULL), and `reason` ∈ `{unknown_symptom, catalog_exhausted, unseen_crash,
repeated_regression}`.

**(b) Diagnose**

For each escalation, gather context from three sources:

```bash
# Provenance: what is known about this bug?
python3 r2g-skills/signoff-loop/knowledge/trace_provenance.py bug --symptom <symptom_id>

# Evidence-ranked strategy list for the design's violation
python3 r2g-skills/signoff-loop/scripts/reports/diagnose_signoff_fix.py <project_path> \
    --check <drc|lvs> --list

# Prose lessons and known failure patterns
# Read: r2g-skills/signoff-loop/references/failure-patterns.md
```

**(c) Author a new strategy**

If the violation is not handled by any existing strategy, author a new predicate + strategy
in the appropriate catalog inside `diagnose_signoff_fix.py`. The predicate must reference the
`symptom_id` so the lifecycle machinery can key it correctly.

**(d) Stage the strategy as shadow**

```python
from knowledge import recipe_lifecycle, knowledge_db
conn = knowledge_db.connect()
recipe_lifecycle.stage_shadow(
    conn,
    provenance='agent:<escalation_id>',
    symptom_id='<sid>',
    design_class='<design_class>',
    platform='<platform>',
    strategy='<strategy_name>',
)
```

The new strategy now exists in `recipe_status` with state `shadow`. It is **inert** in live
ranking until an A/B win promotes it.

**(e) Journal every action**

Every discrete action taken during diagnosis and authoring must be journaled:

```bash
python3 r2g-skills/signoff-loop/knowledge/journal_action.py action \
    --project <project_path> \
    --actor agent \
    --type <config_knob_delta|sdc_edit|stage_rerun|tool_invoke|escalate|ab_launch|promote|demote> \
    [--payload '{"key": "value"}'] \
    [--symptom <symptom_id>] \
    [--session <fix_session_id>]
```

The CLI never breaks the caller (exits 0 with a warning on error). `R2G_JOURNAL=0` disables
it entirely. Journal every config edit, every `diagnose_signoff_fix.py` invocation, and the
`stage_shadow` call.

**(f) Resolve the escalation and re-queue**

```python
escalations.resolve(conn, <escalation_id>, status='drained', notes='<summary>')
```

Then re-add the design to the ledger so the loop will pick it up again once the A/B trial
has promoted the strategy (or mark `wont_fix` if the root cause is irrecoverable):

```bash
python3 scripts/loop/engineer_loop.py add \
    --ledger design_cases/_batch/campaign.jsonl \
    --project <project_path> \
    [--platform <platform>]
```

### Prose update

If the escalation reveals a new failure class, add a section to
`r2g-skills/signoff-loop/references/failure-patterns.md` **by hand** — the loop never auto-merges prose.

## 4. Provenance Queries

`knowledge/trace_provenance.py` is a read-only CLI that joins both DBs via shared keys
(`symptom_id`, `run_id`, `fix_session_id`).

**Solution → origin:** given a recipe key, trace back through A/B trials, fix episodes,
journal actions, and designs to answer "where did this fix come from?"

```bash
python3 r2g-skills/signoff-loop/knowledge/trace_provenance.py solution \
    --symptom <symptom_id> \
    --class <design_class> \
    --platform <platform> \
    --strategy <strategy_name>
```

Returns a JSON provenance tree: which A/B trials validated it, which fix episodes contributed
evidence, which journal actions were taken, which designs were involved, and which tool bugs
were observed.

**Bug → solutions:** given a symptom, list every known solution with lifecycle status and
evidence strength.

```bash
python3 r2g-skills/signoff-loop/knowledge/trace_provenance.py bug --symptom <symptom_id>
```

Returns a list of all known strategies for this symptom, each with its `recipe_status` state
(`shadow`, `candidate`, `promoted`), A/B trial summary, and the designs it was proven on.

## 5. Safety Invariants

- `PLACE_DENSITY_LB_ADDON ≥ 0.10` is never touched by the loop or any recipe; placement
  divergence is irrecoverable.
- Design-type / platform caps are applied as absolute post-filters by `suggest_config.py`
  over any learned median — safety rails beat empirical medians.
- **Only `promoted` recipes affect live strategy ranking** in `diagnose_signoff_fix.py`.
  Shadow and candidate recipes are logged but inert in arm-A and live runs. An absent
  `recipe_status` row means the recipe is grandfathered as promoted (pre-loop recipes that
  were already validated by live use).
- Promotion is evidence-only: `recipe_status` is a function of the recipe's FULL `ab_trials`
  corpus (`ab_runner.judge_recipe`, net wins>losses), not the last trial. No human gate, no agent
  shortcut. An `inconclusive` verdict carries no information — it never demotes (2026-06-24).
- Demotion to `shadow` is corpus-driven (net losses>wins). `ab_runner.auto_demote_on_regression`
  (a live-regression self-heal keyed on `fix_events` verdict='regression') is a PROVIDED HELPER but
  is NOT auto-wired into the drain, and the ingester does not currently emit a 'regression' verdict —
  so the live demotion path is `judge_recipe`'s A/B-corpus aggregation, not regression counting
  (2026-06-24 audit, #6).
- Agent-authored strategies stage as `shadow` and must win their A/B before promoting.
  They receive **no special trust** — same lifecycle as machine-learned re-rankings (decision 7).
- `failure-patterns.md` is authored prose, never auto-merged — the agent tier writes it
  explicitly when confirming a new failure class.
- Journal failures never break the flow; the journal is evidence, not a prerequisite.
- Only the loop process ingests into `knowledge.sqlite` (single-writer invariant).
- **A/B arms must do DIFFERENT work** (2026-06-24 loop-closure audit). The arm copytree excludes
  `reports/` and a signoff `ab_arm` always reaches `_run_fix`, so arm A (`R2G_FIX_EXCLUDE`) and arm B
  (`R2G_FIX_RANK_FIRST`) genuinely diverge — otherwise both arms inherit a clean verdict, short-circuit
  before the fixer, and every verdict is wall-clock noise (the bug that kept `promoted` flat at 2/both
  sky130hd while `ab_trials` grew). The success-tie cost tiebreak is variance-aware (`se==0` = maximal
  confidence). An arm with no backend ESCALATES (`route_arm_incomplete`), never ingests a junk
  `unknown` row. **Alarm: `ab_trials` grows but `promoted` is flat for a whole platform.**
- **A/B coverage routing (CLOSED in stages):** `_symptom_check` routes by STRATEGY — place recipes
  to the apply-then-flow backend runner, timing to `fix_signoff --check timing` judged on
  `wns_ns`/`timing_tier`, synth to a synth-only arm judged on stage clearance (2026-06-24/28).
- **Judge v2 — signoff arms judged on THEIR OWN symptom, with reason codes (2026-07-04).** The
  whole-run `is_success` metric tied both arms whenever an UNRELATED residual kept the run non-clean
  (85% of all trials inconclusive; antenna 0-decisive-in-93). `judge_finished_trials` now resolves
  the candidate's symptom to a target: a DRC arm succeeds iff the TARGET class count reached 0 on a
  definitively-run DRC, an LVS arm iff lvs is clean. Every trial's `metrics_json` records
  `judge_version: 2`, a `reason` code from `ab_runner.judge_repeated_ex`
  (`both_arms_never_succeed`, `success_tie_cost_within_noise`, …) and the `target`.
  `_ab_coverage_gap`'s re-plan cap counts only v2 inconclusives (pre-v2 verdicts were blind to the
  symptom under test; decisive verdicts count from any era). Non-divergent strategies are refused at
  enqueue and healed to the non-terminal `parked` status (`recipe_lifecycle.park_nondivergent`,
  called at the top of every drain). Detail: `references/failure-patterns.md` ("judge blind to the
  target symptom") + `knowledge/README.md` invariant 29.
