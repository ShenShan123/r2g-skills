# Plan — Close the `journal.sqlite` Decision Loop (2026-06-17)

> **Origin.** Produced from a live audit of `journal.sqlite` state (2026-06-17).
> The audit asked whether `journal.sqlite` records *what decisions were made* (A/B
> launches, promotions, demotions, escalations) in addition to *what tools did*.
>
> **Status:** PLANNED — not yet implemented.
>
> ---
>
> ## ⚠ Revision note (2026-06-17, post agent-team review)
>
> A four-agent review (code-claim verifier, live-DB auditor, feasibility reviewer,
> adversarial premise reviewer) **re-grounded this plan against the actual code and the
> live databases.** The first draft was written largely from the *spec/prose* and from
> *table row counts*, and several of its load-bearing claims did not survive contact
> with the source. The corrections below are folded into the body; the most important:
>
> | First-draft claim | Reality (verified in code/DB) | Impact |
> |-------------------|-------------------------------|--------|
> | **Gap 1 CRITICAL:** `detect_bugs()` result is discarded; wire it to `append_tool_bug` | The loop **already exists and runs** (`journal_action.py:54-61`), invoked from `run_orfs.sh:36`, `run_drc.sh:316`, `run_lvs.sh:292`, `run_rcx.sh:163`. `tool_bugs=0` is **correct**: `_BUG_PATTERNS` (`summarize_log.py:25-30`) matches only tool **crashes** (sigsegv/assert/oom/timeout) and this corpus's failures are routine ORFS **aborts** (PPL/DPL/GRT codes). Empirically 0/8000 logs match. | Gap 1 inverted: it is **not** a wiring bug. The real question is a *semantic decision* — should non-crash aborts be `tool_bugs` at all? |
> | Acceptance #1: `trace_provenance.py bug --symptom` returns the tool_bug | `bug --symptom` → `bug_solutions()` reads **knowledge.sqlite only** (`trace_provenance.py:84-103,118`). The journal `tool_bugs` read lives in `solution_origin()` — the **`solution`** subcommand (`:65-66`). | Wrong subcommand; acceptance criterion fixed. |
> | Audit: actions=3,448, log_summaries=8,030 | Live: **3,457 / 8,039** (+9 each since audit; new rows have NULL run_id). | Numbers refreshed; premise "DB unchanged" was already stale. |
> | Actions attributed to actors `run_orfs.sh` (3,201) / `fix_signoff.sh` (248) | `actor` is uniformly **`loop`**; the producer split is by `action_type`: **`tool_invoke`=3,209**, **`config_knob_delta`=248**. No such actor strings exist. | Any check keyed on `actor='run_orfs.sh'` would never match. |
> | Gap 3: `SYMPTOM_ID` "already in scope" in `fix_signoff.sh` | The variable **does not exist** anywhere in `fix_signoff.sh`. | Gap 3 needs SYMPTOM_ID *plumbed in* first — not a one-line add. |
> | "`engineer_loop` decisions are categorically unrecorded" | Partial journaling exists: `diagnose_signoff_fix.py:746` already journals `sdc_edit`; `config_knob_delta` has 248 rows. | Scope is the **remaining** decision types, and `sdc_edit` is the template to copy. |
> | Acceptance #4: `count(promote actions) == count(ab_trials win)` (1:1) | Structurally impossible: `promote` is an **idempotent UPSERT** (`recipe_lifecycle.py:93-110`); live store has 3 wins → 2 promoted (trials 1+2 share a key); grandfathered/`ab-enqueue` promotes have **no** win. | Honesty check rewritten (see below). |
> | "Honesty invariants unchanged" + Tier C reads journal for learning | **Contradiction.** Tier C (mine `tool_bugs`, pick A/B subjects from journal) makes a *learner* read the journal — breaching the `knowledge.sqlite`-is-the-only-learner-input firewall. journal.sqlite is **gitignored/rotatable**, so journal-fed learning is non-reproducible across checkouts. | Tier C re-scoped: route crash signatures through knowledge at ingest, **or** explicitly amend the invariant. |
>
> **Net verdict from the review:** the genuinely valuable, low-risk work is the
> provenance linkage (Gaps 3–4) and the *remaining* decision-journaling (Tier B). Tiers
> A+B improve **operator forensics** (`trace_provenance`), not the learning loop —
> promotion/demotion already live authoritatively in `knowledge.sqlite`. Frame the value
> honestly, fix Gap 1's diagnosis, decouple honesty checks from best-effort journaling,
> and keep Tier C on the knowledge side of the firewall.

---

## The meta-finding (revised)

`journal.sqlite` is a two-tier evidence store:

- **Tier 0 (flow telemetry):** ORFS stage outcomes, config knob deltas, report digests,
  log summaries, and tool-crash signatures. This part **works** — `run_orfs.sh`,
  `fix_signoff.sh`, `run_{drc,lvs,rcx}.sh`, and `ingest_run.py` populate it. The
  crash-signature path (`detect_bugs → append_tool_bug`) is **wired and live**; it has
  produced 0 rows only because this corpus has no crash-class failures (see Gap 1).
- **Tier 1 (decision telemetry):** A/B launches, recipe promotions/demotions, escalation
  opens/resolves, and stage reruns. This is **mostly absent** — `engineer_loop.py`
  records these decisions only to `knowledge.sqlite`. (Two decision types *are* already
  journaled: `config_knob_delta` from `fix_signoff.sh`, and `sdc_edit` from
  `diagnose_signoff_fix.py:746`.)

The journal is therefore a **largely write-only ledger with one read-only consumer**
(`trace_provenance.py`, a manual operator forensics CLI). The value of this plan is to
**complete the journal's defined role** — the comprehensive, detailed record of *all*
status and actions — serving audit, forensics, debugging, and provenance. It is *not*
"closing the learning loop": the learning loop is closed today through `knowledge.sqlite`,
and per the project's honesty model it must stay that way (`knowledge.sqlite` is the sole
learner input).

---

## Design contract (confirmed by operator, 2026-06-17)

This plan operates under an explicit, operator-affirmed division of responsibility. It is
the governing constraint for every task below:

| | `journal.sqlite` | `knowledge.sqlite` |
|---|---|---|
| **Role** | **All detailed status + actions** — the complete "what was done" ledger | **Knowledge + experience** — learned recipes, lessons, symptoms, A/B verdicts |
| **Completeness goal** | **Exhaustive** — every decision and stage outcome should land here (this is *why* Tiers A+B exist) | Curated — only what generalizes/transfers |
| **Source of truth?** | No — evidence only; best-effort writes | **Yes** — the only thing the learner reads; the only honesty-gate source |
| **Git** | **Ignored** (`.gitignore:249-250`; high-volume, machine-local, rotatable) | **Tracked / committed** (ships pre-trained with the skill) |

**Three consequences that bound the implementation:**

1. **The journal must be comprehensive (endorses Tiers A+B).** Because its role is the
   *complete* detailed record, journaling the remaining decision types
   (`ab_launch`/`promote`/`demote`/`escalate`/`stage_rerun`) and the linkage columns
   (`symptom_id`/`parent_action_id`) is *fulfilling its contract*, not gold-plating. The
   "no automated consumer" objection is answered: the consumer is the **operator** (audit,
   forensics, debugging, replay), and completeness has standalone value.

2. **The journal feeds learning ONLY via ingest-time promotion into committed knowledge —
   never a runtime/learner read (this is the shape of Tier C).** The operator directive
   (2026-06-17) is that the skill *should* dig lessons out of the journal. The safe
   mechanism is an **ingest-time promoter** (on the operator box, where the journal is
   local) that projects net-new journal evidence into committed `knowledge.sqlite` *tables*;
   the existing learners then learn from knowledge as they always have. This is the *same
   pattern by which knowledge is already built* from gitignored local run artifacts
   (`design_cases/`, `reports/*.json`), so it does not change the reproducibility class.
   Guardrails: (a) every mined pattern is validated against a knowledge-side outcome
   (journal = hypothesis, knowledge = evidence, human = gate); (b) distilled content lands
   in knowledge **tables**, never directly in `heuristics.json`/`failure_candidates.json`
   (full-rewritten from knowledge each run → would clobber it); (c) runtime + honesty gates
   read only knowledge, so a fresh clone behaves identically. See Tier C.

3. **`knowledge.sqlite` stays the only honesty-gate source.** Since journal writes are
   best-effort and the file is gitignored, no honesty *gate* may key on journal counts
   (see "Best-effort journaling vs. honesty gates" below).

---

## Current state (audit 2026-06-17, refreshed post-review)

### What is populated

| Table | Rows (live) | Producer | What it captures |
|-------|-----:|---------|-----------------|
| `actions` | 3,457 | `tool_invoke`=3,209 (run_orfs.sh) + `config_knob_delta`=248 (fix_signoff.sh) | ORFS stage commands + config knob deltas. **`actor` is always `loop`.** |
| `log_summaries` | 8,039 | `ingest_run.py` + `run_{drc,lvs,rcx}.sh` | Digests of every report JSON + signoff tool logs |
| `tool_bugs` | **0** | `journal_action.py summarize` (wired, live) | Crash signatures — **legitimately empty** (no crash-class failures in corpus; see Gap 1) |

### What is NOT populated (specified, never wired)

| `action_type` | Spec | Rows | Missing writer |
|--------------|------|-----:|----------------|
| `stage_rerun` | CLAUDE.md §journal | 0 | `fix_signoff.sh` re-runs ORFS but never journals it |
| `ab_launch` | CLAUDE.md §journal | 0 | `engineer_loop._process_*_ab_arm()` |
| `promote` | CLAUDE.md §journal | 0 | `ab_runner.record_trial()` win branch |
| `demote` | CLAUDE.md §journal | 0 | `ab_runner.record_trial()` loss/inconclusive branch |
| `escalate` | CLAUDE.md §journal | 0 | `escalations.open_escalation()` callsite |

> Note the promote/demote decision is written in `ab_runner.record_trial()` (`:218-234`),
> which calls `recipe_lifecycle.promote/demote` — *not* in `judge_repeated()` (which only
> returns a verdict string). The first draft pointed at the wrong function.

Cross-DB linkage columns — all NULL across every existing row (schema columns confirmed present):

| Column | Purpose | NULL rows |
|--------|---------|----------:|
| `actions.symptom_id` | Link action to symptom in `knowledge.sqlite` | 3,457 / 3,457 |
| `actions.parent_action_id` | Stacked-fix chains | 3,457 / 3,457 |
| `tool_bugs.symptom_id` | Link crash to symptom | N/A (0 rows) |

`run_id` backfill (set by `ingest_run.backfill_run_id`, which covers **all three** tables
incl. `tool_bugs` — `journal_db.py:107-116`):
- `actions`: 3,188 filled / 269 NULL
- `log_summaries`: 7,981 filled / 58 NULL

### Root cause (Tier 1 only)

`engineer_loop.py` (568 lines) has **zero references to `journal_db`/`journal_action`**.
When it was built as the production driver it inherited none of the decision-journaling
calls the spec anticipated would come from a separate "agent tier" that was never built.
The fix-level decisions (`config_knob_delta`, `sdc_edit`) *are* journaled by the scripts
the loop shells out to; the loop's own A/B-lifecycle decisions are not.

---

## Gap analysis (revised)

### Gap 1 — `tool_bugs` is empty by *coverage*, not by *wiring* (was: CRITICAL → now: DESIGN DECISION)

**Status: the original diagnosis is wrong.** `journal_action.py:54-61` already calls
`detect_bugs()` and loops `append_tool_bug()` over the result, on every non-pass log, from
all four flow scripts. There is no discarded result to wire.

`tool_bugs=0` is **correct behavior**: `summarize_log._BUG_PATTERNS` (`:25-30`) matches
only four tool-**crash** classes — `sigsegv`, `internal_assertion`, `oom`, `timeout`. This
corpus's failures are routine ORFS **aborts** (`[ERROR PPL-0024]`, `[ERROR DPL-0036]`,
`[ERROR GRT-0116]`, Tcl errors), which are not crashes. Across an 8,000-log sample, 0 logs
matched any crash pattern, so `detect_bugs` correctly returned `[]` every time.

**The real, unmade decision:** should non-crash ORFS aborts become `tool_bugs`? The table's
documented purpose (`journal_schema.sql:45`, README §`tool_bugs`) is "EDA-**tool** bug" —
i.e. a tool defect, not a design that legitimately failed P&R. Two options:

- **Option 1 (recommended) — leave `tool_bugs` crash-only.** It is working as designed.
  Backend aborts already become first-class `failure_events` / `run_violations`
  (`orfs_stage` symptoms) in `knowledge.sqlite` — that is where the route_relief /
  density_relief A/B loop already reads them. `tool_bugs` stays the narrow "a tool
  literally crashed" channel (i2c CTS SIGSEGV, route exit-124 timeout). **Action: none —
  delete Gap 1 from the implementation tier; document `tool_bugs` semantics instead.**
- **Option 2 — broaden `_BUG_PATTERNS`** (e.g. add `\[ERROR [A-Z]{2,5}-\d{3,4}\]`). This
  reclassifies routine congestion aborts as "tool bugs" (semantically wrong) and, given the
  per-line/first-200-char signature construction (`summarize_log.py:88-92`), would **flood**
  `tool_bugs` with thousands of low-quality signatures. **Not recommended.** If pursued,
  `detect_bugs` output quality must be validated on a labeled crash set *first* (it has been
  effectively dead-on-this-corpus since inception and is unvalidated at scale).

**Either way, validate before relying:** before any consumer (incl. Tier C) trusts
`tool_bugs`, run `detect_bugs` over a labeled set of known crashes and confirm the
signatures are canonical and de-duplicating.

---

### Gap 2 — `engineer_loop` A/B-lifecycle decisions not journaled (HIGH — confirmed)

Four decision types touch the A/B lifecycle without journaling. Copy the **existing**
template (`diagnose_signoff_fix.py:746` for `sdc_edit`; `fix_signoff.sh` for
`config_knob_delta`):

| Decision | Where (corrected) | Journal call to add |
|---------|-------|-------------------|
| A/B arm launched | `engineer_loop._process_signoff_ab_arm()` / `_process_backend_ab_arm()` | `action(type=ab_launch, payload={arm, strategy, symptom_id, trial_id})` |
| Recipe promoted | `ab_runner.record_trial()` **win** branch (`:218-234`) | `action(type=promote, payload={recipe_id, symptom_id, trial_id, lcb_score})` |
| Recipe demoted | `ab_runner.record_trial()` **loss/inconclusive** branch | `action(type=demote, payload={recipe_id, symptom_id, trial_id, verdict})` |
| Escalation opened | `escalations.open_escalation()` callsite | `action(type=escalate, payload={reason, symptom_id})` |
| Stage re-run (fix iter) | `fix_signoff.sh` re-invokes `run_orfs.sh FROM_STAGE` | `action(type=stage_rerun, payload={from_stage, strategy})` |

**Carry `trial_id` in the payload** for `ab_launch`/`promote`/`demote` — it is the only
key that makes the cross-DB check (acceptance #4, rewritten below) well-defined, because a
recipe key can be promoted by multiple wins.

Each call uses the existing `journal_action.py action` CLI (which already honors
`R2G_JOURNAL=0` and exits 0 on error) or `journal_db.append_action()` directly:

```python
from knowledge import journal_db
conn = journal_db.connect()
journal_db.append_action(
    conn, project_path=str(project), actor="loop", action_type="promote",
    payload={"recipe_id": recipe_id, "symptom_id": sid, "trial_id": tid,
             "lcb_score": score},
    symptom_id=sid)
```

**Concurrency — pin the write location relative to the thread-join boundary.** `ab-drain`
runs arms in parallel (`R2G_AB_WORKERS`), but `judge_finished_trials` (and thus
`record_trial`'s promote/demote) runs **serially after the pool joins**
(`engineer_loop.py:427`). Journal `promote`/`demote` from that serial section. Journal
`ab_launch` (which *is* per-arm) using the per-thread connection pattern already in
`_drain_arm` (`:389-399`). Do **not** add knowledge-side writes from worker threads —
`knowledge_db.connect` is not WAL (`knowledge_db.py:29-30`), unlike `journal_db` (WAL +
30s busy_timeout, `:28-34`), so concurrent knowledge writes would contend.

---

### Gap 3 — `symptom_id` not linked, and `SYMPTOM_ID` is **not in scope** (MEDIUM)

The first draft assumed `SYMPTOM_ID` was already available in `fix_signoff.sh`. **It is
not** — the variable does not exist in that script. So this gap has two steps:

1. **Plumb the symptom in.** `diagnose_signoff_fix.py` produces `symptom_signature`;
   `fix_signoff.sh` must capture it (env export or arg) before it can journal it. Decide
   the carrier: simplest is for `fix_signoff.sh` to read the symptom from the diagnosis
   JSON it already consumes, export `SYMPTOM_ID`, and pass `--symptom "$SYMPTOM_ID"` on the
   `_journal_knob_deltas` call.
2. **Pass `--symptom`** on the existing `config_knob_delta` action (and on the new Tier-B
   actions). `journal_action.py action` already accepts `--symptom` (`:94`) and writes it.

This makes "all config changes that addressed symptom X" a direct `WHERE symptom_id=?`
query instead of a fragile `project_path` join.

---

### Gap 4 — `parent_action_id` for stacked fixes (MEDIUM — confirmed)

Multi-iteration fix sequences (`fix_signoff.sh` runs ≤3 iterations) should form a parent–
child chain. Capture the `action_id` returned by the first `_journal_knob_deltas` call and
pass it as `--parent <id>` on later iterations. `journal_action.py action` already accepts
`--parent` (`:95`) and writes `parent_action_id`. Note: `_journal_knob_deltas` does not
currently *return* the action_id — that plumbing must be added.

---

## Implementation plan (revised)

### Tier A — provenance linkage, self-contained (implement first)

| Task | File | Lines | Test |
|------|------|:---:|------|
| ~~A1. Wire `detect_bugs → append_tool_bug`~~ | — | — | **DROPPED** — already wired (Gap 1). Replace with a doc note on `tool_bugs` crash-only semantics. |
| A2. Plumb `SYMPTOM_ID` into `fix_signoff.sh` + pass `--symptom` | `fix_signoff.sh` | ~8 | `test_flow_journaling.py::test_fix_session_symptom_linked` |
| A3. Stacked-fix `parent_action_id` chain (capture + return action_id) | `fix_signoff.sh` | ~10 | `test_flow_journaling.py::test_stacked_fix_parent_chain` |

> All four named test files already exist under `r2g-rtl2gds/tests/`
> (`test_journal_action.py`, `test_flow_journaling.py`, `test_engineer_loop.py`,
> `test_ab_runner.py`) — these tasks **add cases**, not files.

### Tier B — wire the remaining `engineer_loop` decisions (implement second)

| Task | File | Lines | Test |
|------|------|:---:|------|
| B1. Journal `ab_launch` (per-arm, per-thread conn) | `engineer_loop.py` | ~6/arm | `test_engineer_loop.py::test_ab_launch_journaled` |
| B2. Journal `promote`/`demote` (serial, post-join) | `ab_runner.py` (`record_trial`) | ~8 | `test_ab_runner.py::test_verdict_journaled` |
| B3. Journal `escalate` | `escalations.py` | ~5 | `test_engineer_loop.py::test_escalate_journaled` |
| B4. Journal `stage_rerun` | `fix_signoff.sh` | ~6 | `test_flow_journaling.py::test_stage_rerun_journaled` |

### Tier C — learn from the journal via an ingest-time PROMOTER (operator directive, 2026-06-17)

> **Operator directive:** *"the skill should learn, refine, and dig out new solutions and new
> fix strategies by summarizing the lessons from `journal.sqlite`."*
>
> **Round-2 agent-team review (2026-06-17)** stress-tested this against the code and live DBs.
> It is buildable **without breaching the firewall**, but only in a specific shape and with a
> much narrower payoff than the directive's wording implies. Findings folded in below.

**The mechanism: a promoter, not a journal-reading learner.** The journal is read **only at
ingest time** (on the operator box, where it is local) by a **promoter** that projects net-new
journal evidence into committed `knowledge.sqlite` *tables*. The existing learners
(`learn_heuristics.py`, `mine_rules.py`) then learn from knowledge exactly as they do today;
no learner and no runtime path ever opens the journal. This is the *same pattern* by which
knowledge is already built from gitignored local run artifacts (`fix_log.jsonl`,
`reports/*.json`), so it adds no new reproducibility class: a fresh clone has empty promoter
tables, the knowledge-derived joins yield nothing extra, and inference is unaffected.

**Three hard rails (each proved against the code; violate one and the firewall or the honesty
model breaks):**

1. **Never write `heuristics.json` / `failure_candidates.json` directly.** Both are
   *full-rewritten* from knowledge-only tables every run (`learn_heuristics.py:459`,
   `mine_rules.py:186`), and inference reads the rebuilt `symptoms`/`recipes` keys
   (`diagnose_signoff_fix.py:553,580`). A journal-distilled entry written there is **clobbered
   by the next clone's routine `learn()`**. Durable content lives in knowledge **tables**; the
   JSON files stay regenerated views.
2. **Outcome-join is mandatory (journal = hypothesis, knowledge = evidence).** Journal writes
   are best-effort/silenceable (`journal_action.py:82-83,121-123`), so the corpus has silent,
   load-correlated gaps — disqualifying for any *quantitative* claim on its own. Every mined
   pattern must join via `actions.run_id`/`fix_session_id` to a knowledge-side **outcome**
   (`runs.outcome_score`/`is_success`, `ab_trials.verdict`, `fix_trajectories.outcome`). A
   candidate with no knowledge-side win is rejected, not staged. Validation authority stays on
   the knowledge side.
3. **New strategies pass the existing human-review gate.** Genuinely-new symptom→strategy
   mappings land in `failure_candidates.json` (regenerated from a knowledge staging table),
   never auto-merged into `failure-patterns.md`.

**Honest scope — what mining CAN and CANNOT produce.** The fix strategies (`density_relief`,
`route_relief`, `period_relax`, antenna-diode, …) are **hardcoded code paths**. Mining cannot
*synthesize* a new code-level strategy; it can only **re-weight** (which existing strategy wins
for a symptom), **re-sequence** (try X before Y), and **re-map** (this strategy also helps that
symptom) — plus surface human-review candidates. Much of even that is **already** produced by
`learn_heuristics`/`mine_rules` from knowledge. The plan must not promise emergent novel repair
strategies.

**What is net-new vs. redundant vs. absent (live-DB audit).** The directive is **~75% already
satisfied** by the existing `fix_log.jsonl → ingest → fix_events → learn` path. Build only for
the thin net-new band, and know which signal exists as data today:

| Signal | Status | Action |
|--------|--------|--------|
| per-fix knob values; old→new deltas; crash sigs; A/B lifecycle | **redundant** (`fix_events.config_delta_json`, `config_lineage`, `symptoms`, `ab_trials`/`recipe_status`/`escalations`) | do **not** re-mine |
| strategies that ran but never wrote a `fix_log` (e.g. `lvs_same_nets_seed`, `antenna_diode_iters` — 0 rows in `fix_events`) | **net-new, available** | promote tried-strategy evidence |
| per-stage retry/thrash cadence (`runs.stage_times_json` keeps only the latest attempt) | **net-new, available** | promote retry counts |
| route-congestion / error *magnitudes* (`log_summaries.metrics_json.total_violations`; knowledge stores only status+text) | **net-new, available** | promote severity into `run_violations` |
| stacked-fix compound recipes (`parent_action_id` chains) | **absent data** — column is 100% NULL until Tier A3 wires it, *and* the journal payload lacks the old-value | **deferred** to after Tier A |
| `tool_bugs` crash signatures | **absent data** — 0 rows (corpus has no crash-class failures; Gap 1) | nothing to mine |

**Dependency on Tiers A+B: NOT load-bearing.** Because the promoter outcome-joins to knowledge
regardless, Tier C does not require Tiers A/B. The knowledge-only sub-mining (below) needs no
journal at all. Tiers A/B remain justified by *operator forensics* (`trace_provenance`), and
they *enrich* the hypothesis space (A3's `parent_action_id` unlocks compound recipes) — but they
are not a prerequisite.

**Build order (each independently shippable; prefer extending existing scripts per CLAUDE.md):**

| Step | What | Source → Dest | Journal? | Tier A/B? |
|------|------|---------------|:---:|:---:|
| C1. Escalation-cluster candidates | cluster open `unknown_symptom` escalations by signature/family → `failure_candidates.json` (via a knowledge staging table) | `knowledge.escalations` → staging table → mine_rules view | no | no |
| C2. A/B-margin weighting | LCB margin of winning trials → new `recipe_status.ab_lcb_margin`; feeds `fix_model` tiebreak | `knowledge.ab_trials` → `recipe_status` | no | no |
| C3. Journal promoter (the net-new band) | tried-but-unlogged strategies, retry cadence, congestion magnitudes → knowledge tables, **outcome-joined** | `journal.actions`/`log_summaries` → knowledge tables, at ingest | **yes** | no |
| C4. Compound stacked-fix recipes | `parent_action_id` chains whose run resolved a symptom → candidate multi-step recipe (human-gated) | journal chains → knowledge → `failure_candidates.json` | **yes** | **needs A3** + journal-writer change to record it |

**Where it runs:** C1/C2 fold into the existing `mine_rules.mine()` batch (knowledge-only, no
new cadence). C3's promotion runs **at ingest** (`ingest_run.py`, beside the existing
`backfill_run_id` journal touch — best-effort, `try/except`, no-op when the journal is absent).
**Not** a per-flow learner phase and **not** a runtime read.

**Remaining caveats (state in any spec that implements this):**
- *Multi-operator divergence* — two operators mining their own local journals commit divergent
  knowledge. This is a **pre-existing** property of the committed binary `knowledge.sqlite`
  (not new to Tier C), but C3/C4 widen its surface; record the *journal snapshot identity*
  (row-count + max `ts` + hash) in each promoted row's provenance.
- *Idempotency* — the promoter must use stable content-derived keys and the existing upsert
  paths, with a `journal_sessions_promoted` sidecar so re-runs don't double-count. Acceptance:
  running it twice on the same snapshot yields zero net new rows.
- *Scope* — C3/C4 are effectively a second (small) learning subsystem. If pursued beyond the
  knowledge-only C1/C2, give it its **own spec/brainstorm**; do not let it balloon this
  journaling-completeness plan.

---

## What the loop looks like after Tiers A + B

```
engineer_loop.py ab-drain
    │
    ├─ journal: ab_launch  (actor=loop, symptom_id=X, trial_id=T, arm=B, strategy=…)  [per-thread conn]
    │
    ├─ run_orfs.sh
    │   ├─ journal: tool_invoke × N stages
    │   └─ journal_action summarize → tool_bugs ONLY on crash-class logs (already wired)
    │
    ├─ fix_signoff.sh
    │   ├─ journal: config_knob_delta (symptom_id=X, session=S)               ← Gap 3
    │   ├─ journal: stage_rerun       (from_stage=route, strategy=…)          ← Tier B4
    │   └─ child knob rows: parent_action_id=<first_action_id>                ← Gap 4
    │
    ├─ ingest_run.py → journal: log_summaries; backfill run_id (all 3 tables)
    │
    └─ [pool join] judge_finished_trials → record_trial → verdict             [serial]
         ├─ journal: promote (symptom_id=X, trial_id=T, lcb_score=0.82)       ← Tier B2
         └─ knowledge.sqlite: ab_trials row, recipe_status → promoted  (SOURCE OF TRUTH)

trace_provenance.py solution --symptom X      ← reads journal.tool_bugs + actions (forensics)
    └─ follows fix_session_id / parent_action_id chains
```

> Reminder: `trace_provenance.py bug --symptom X` reads **knowledge.sqlite** only
> (`bug_solutions`). It is the **`solution`** subcommand that reads `journal.tool_bugs`
> (`solution_origin`). Acceptance criteria are keyed accordingly.

---

## Honesty invariants

The project's numbered invariants live in `r2g-rtl2gds/knowledge/README.md`. Map this plan
to them precisely (the first draft paraphrased them):

- **Invariant 17** (README: "Journal archival loses no conclusions") + the governing
  CLAUDE.md rule ("`knowledge.sqlite` = the learner reads ONLY this; journal = what was
  done"). **All Tier A/B changes write *additional evidence* into the journal only.** No
  Tier A/B change reads the journal for a learning decision. Tier C is explicitly
  constrained to keep it that way (see above). `knowledge.sqlite` remains the sole source
  of truth for recipes, trajectories, and A/B verdicts.
- **Invariant 20** ("the A/B loop fires on the production path"). Tier B's `ab_launch` /
  `promote` / `demote` rows give an **advisory observability** cross-check — but they are
  **not** an honesty gate (see acceptance #4, rewritten).

### Best-effort journaling vs. honesty gates (new, critical)

Journal writes are **best-effort**: `journal_action.py` swallows errors and exits 0, and
`R2G_JOURNAL=0` silences them entirely. Therefore **journal counts must never be an honesty
gate** — a dropped or silenced journal write would make the gate lie (a benign telemetry
drop masquerading as a missing decision, the exact failure class CLAUDE.md warns against).

**Rule:** every honesty *gate* reads `knowledge.sqlite` (the must-succeed source of truth).
Journal-vs-knowledge cross-checks are **advisory observability only**, and must be
conditioned on `R2G_JOURNAL!=0`.

---

## Acceptance criteria (revised)

1. **Gap 1 (semantics, not wiring):** confirm `tool_bugs` write path is live by feeding a
   *synthetic crash log* (containing `Segmentation fault` / `TIMEOUT reached`) to
   `journal_action.py summarize --tool openroad --stage route` → ≥1 `tool_bugs` row; and
   `trace_provenance.py solution --symptom <sig>` surfaces it (note: **`solution`**, not
   `bug`). Document that PPL/DPL/GRT aborts are *intentionally not* `tool_bugs`.
2. **Gap 3 / A2–A3:** a `fix_signoff.sh` run on a DRC-fail design produces
   `config_knob_delta` rows with **non-NULL `symptom_id`**; iteration-2 rows carry
   `parent_action_id` pointing to iteration-1.
3. **Tier B:** `engineer_loop ab-drain` on a design with a pending candidate produces
   `ab_launch` + (`promote` or `demote`) rows in `actions`, each carrying `symptom_id` and
   `trial_id`; none with NULL `symptom_id`.
4. **Cross-DB observability (advisory, not a gate):** for every `ab_trials` row with
   `verdict='win'`, there exists a `promote` action carrying its `trial_id`
   (one-`promote`-action-per-winning-trial — **not** per recipe key, since one key can win
   repeatedly and `promote` is an idempotent UPSERT). This check is skipped when
   `R2G_JOURNAL=0`, and a missing row is reported as *journal-incomplete*, never as a
   loop-honesty failure. The honesty **gate** for promotion remains knowledge-side:
   `recipe_status` rows with `provenance LIKE 'ab_trial:%'` are consistent with `ab_trials`
   wins.
5. **`R2G_JOURNAL=0`** must still silence all new writes without error (existing guard at
   `journal_action.py:82-83` covers the CLI; the direct-API Tier-B callsites must replicate
   the guard).

---

## Sequencing & branch hygiene (new)

This plan lives on `feat/paper-absorption`, which carries **uncommitted, unpushed**
paper-absorption / route_relief work that *also* edits `engineer_loop.py`, `ab_runner.py`,
`knowledge.sqlite`, and `CLAUDE.md`. Starting a second multi-tier effort that touches the
same files risks an unmergeable tangle.

**Recommendation:**
1. **Finish & merge** the paper-absorption / route_relief workstream first (it has a live
   A/B win to land).
2. Do this journal work on its **own branch**, after a fresh code-grounded re-audit
   (the first draft's drift shows the audit must be re-run against the code at start).
3. Treat **Gaps 3–4 (provenance linkage)** as the highest-value, lowest-risk slice — land
   those first; they are pure additive evidence with a clear consumer (`trace_provenance`).

### Legacy backfill (forward-only caveat)

The 3,457 existing `actions` rows are 100% NULL `symptom_id`/`parent_action_id`. The new
writers populate these **going forward only**; `backfill_run_id` fills `run_id` but never
`symptom_id`. So `trace_provenance` answers are **forward-complete only** — pre-change
history stays unlinked. If that is unacceptable, scope a one-time backfill joining
`actions.fix_session_id → knowledge.fix_events.symptom_id` (the one recoverable link;
covers `config_knob_delta` rows, not `tool_invoke`). State the forward-only limitation
explicitly wherever `trace_provenance` output is consumed.

---

## Implementation log — Tiers A + B SHIPPED (2026-06-17, branch `feat/paper-absorption`)

Implemented on top of the route_relief work (the plan's sequencing note preferred a
separate branch; the operator chose to continue here, and the journaling changes are
additive + isolated from the route_relief files). **Status: Tiers A + B DONE, unit-tested
and live-validated. Tier C NOT started (its own spec, as the plan recommends).**

**Tier A (provenance linkage) — `fix_signoff.sh`:**
- A2: `_compute_symptom_id` mirrors the ingester's `symptom.canonical_signature →
  symptom_id` (incl. the `route → orfs_stage` remap); `fix_one` computes `$sym` per
  iteration and passes `--symptom` through `_journal_knob_deltas`. **Live-confirmed:**
  poly1305_core route fix wrote `config_knob_delta` with `symptom_id=11f02dbb19046bb6`,
  which *matches* the `route_relief` recipe key in `knowledge.sqlite` — the cross-DB
  link works. (Fixed a `${3:-{}}` bash brace-expansion bug found in TDD.)
- A3: `_journal_knob_deltas` now prints its first `action_id`; `fix_one` captures it and
  passes `--parent` on later iterations (star chain from iter 1). Unit-tested.

**Tier B (decision journaling):**
- B1 `ab_launch` — `engineer_loop._journal_ab_launch`, called per-arm in `process_one`
  (own WAL journal conn, thread-safe). Unit-tested.
- B2 `promote`/`demote` — `ab_runner._journal_verdict`, called from `record_trial`
  (serial post-join), carries `trial_id` (acceptance #4). Unit-tested.
- B3 `escalate` — `escalations._journal_escalate`, called from `open_escalation`
  (not on dedup hits). **Live-confirmed:** wbsafety canary → `escalate`/`catalog_exhausted`.
- B4 `stage_rerun` — `fix_signoff.sh` `_journal_action stage_rerun` before each reroute.
  **Live-confirmed** (poly1305: `stage_rerun`, `from_stage=floorplan`, symptom-linked).

**Honesty rails honored:** all new writes are best-effort (honor `R2G_JOURNAL=0`, never
raise), go ONLY to the gitignored journal, and feed NO learner. The knowledge-side gates
still pass (48 orfs-fail rows ↔ 48 `failure_events`; `ab_trials` non-empty; promoted
recipes carry `ab_trial:` provenance). An autouse `conftest` fixture redirects all test
journal writes to a temp DB; a one-time scrub removed 226 `actions` + 1,889
`log_summaries` rows of historical `/tmp/pytest%` test pollution from the operator journal.

**Two skill bugs surfaced by the live campaign and fixed (not in the original plan):**
1. **Signoff baseline never established** — `fix_one` never *ran* DRC/LVS on a fresh flow
   (only after a fix), so DRC was silently skipped (`status: unknown`). Added
   `_ensure_baseline`. See `failure-patterns.md` §"Signoff baseline never established".
2. **`STAGE_STATUS: unbound variable`** — module-scope synth-timeout HINT referenced a
   function-local var; a synth timeout crashed `run_orfs.sh` before recording status.
   Fixed to `MAKE_STATUS`. See `failure-patterns.md` §"Timeout / Stalled Backend Runs".

**Tests:** +10 (suite 620 → 630). Files: `test_flow_journaling.py` (+5),
`test_ab_runner.py` (+2), `test_engineer_loop.py` (+2), `test_fix_signoff_logging.py` (+1).
