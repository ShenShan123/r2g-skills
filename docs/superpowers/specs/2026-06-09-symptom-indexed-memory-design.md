# Symptom-Indexed Memory (PD-engineer experience accumulation) — Design Spec

**Date:** 2026-06-09
**Skill:** `r2g-rtl2gds`
**Status:** Design (brainstorming) — awaiting user review of this spec. Spec only; no
implementation in this session.
**Test platform for validation:** `sky130hd` (greenfield for the memory system — see §3.4).
**Authors:** user5 + an agent team. A 4-agent read-only exploration wave mapped the current
loop (knowledge-store data model, learn/apply path, observation side, prose-vs-struct split).
A 4-lens design panel then debated improvements: learning-systems lens `a481d6871142f51c8`,
senior-PD lens `a152d2f9219dae454`, data-architecture lens `a46fee0e5fd7faee2`,
adversarial-pragmatist lens `aa4ee5dc0db0a6be6`. Synthesized and source-verified by the lead.

---

> **Note (2026-06-10, engineer-loop spec):** the knowledge store DB file `knowledge/runs.sqlite` was RENAMED to `knowledge/knowledge.sqlite` (decision-11 evidence/conclusions split: journal.sqlite = evidence, knowledge.sqlite = conclusions). References below to `runs.sqlite` are historical; the `runs` TABLE name is unchanged.


## 1. Goal & one-paragraph summary

Make the skill **accumulate transferable experience the way a senior physical-design (PD)
engineer does**: recall by **symptom/bug**, not by design name. Today every learned repair
decision is keyed on the **design-family-name prefix** (`aes_…` → family `"aes"`) plus
`(platform, violation_class)`. That is per-name tuning, not expertise — a new design with an
unseen name inherits *nothing*, even when it is structurally and symptomatically a twin of
something the skill has closed a hundred times, and the tiny per-family samples (median **2**
successes/family across **780** runs) make the learned numbers brittle. This design **deletes
the family/design-name prefix as an index everywhere** and re-organizes all learned repair
experience under a **symptom signature** — a canonical, hashable descriptor of the bug itself
(check + discriminating predicate vector). `platform` and design `shape` become **conditioning
attributes**, not keys; `family`/design-name survives only as a **provenance tag** (which runs
contributed evidence). The rich engineering judgment that currently lives only in prose
(`failure-patterns.md` et al.) is linked to its symptom signature and surfaced to the running
agent **in-context at the decision point**. Indexing by symptom also *pools* every antenna
case (or every balanced-unmatched-net LVS case) across all designs and platforms into one
bucket, turning the small-data problem into larger, more trustworthy samples.

This is a **memory-organization + retrieval** change, not new autonomous fixing or new ML. The
two existing loops (closed fix-recipe loop, open config loop) keep their "observe + suggest"
contract; the model **re-orders** candidate strategies and **surfaces** prose lessons — it
**never** overrides the hard safety clamps.

## 2. Key decisions (locked)

1. **Symptom signature is the universal index.** Primary key for all learned repair
   experience = `symptom_id` = stable hash over `(check, class, decision-relevant predicate
   set)`. The design-family/name prefix is **removed as an index**; it is retained only as a
   denormalized provenance tag inside each symptom bucket.
2. **`platform` is a conditioning attribute, not a key.** Stored per-strategy and on lessons
   with a `"*"` wildcard for platform-agnostic (tool-behavior) experience. Same-platform
   evidence is preferred at ranking time; otherwise pooled/agnostic evidence transfers, except
   for strategies explicitly flagged `platform_specific`.
3. **`shape` (cell-count band, design_type, macro presence — structural, never a name) is
   secondary conditioning**, used to refine ranking and to key the *initial-config*
   recommendation (which has no symptom yet). Mostly Phase 2.
4. **Prose stays the human-editable source of truth.** No migration of prose into SQLite; no
   auto-promotion of mined candidates into `failure-patterns.md` (preserves the existing
   review-queue invariant).
5. **Prose↔struct link uses Option B (a synced `lessons` table).** Chosen by the user over the
   lighter direct-read variant: one-way prose→SQLite sync with back-filled evidence counts,
   giving auditable, queryable retrieval. (The lighter variant remains a fallback if the table
   proves to be maintenance overhead.)
6. **Phase 0 A/B is small-design-first.** Run the never-run `eval_heuristics.py` payoff
   harness on ~6–10 small nangate45 designs (where learned≠naive knob diffs exist) and publish
   an honest verdict; do not sweep the corpus.
7. **`sky130hd` is the validation platform.** It has ~0 prior fix evidence, so it directly
   tests cross-platform symptom transfer and platform-specific gating (§6).
8. **Phasing.** Phase 0 (honesty gate) → Phase 1 (symptom-indexed core) → Phase 2 (deferred,
   trigger-gated). Phase 0 + Phase 1 are the committed scope of the eventual plan.
9. **Raw is the system of record.** Raw actions (`fix_events`), trajectories
   (`fix_trajectories`), and symptoms (a new `symptoms` table + `symptom_id`/`signature_json`
   on every raw row) are kept in the database losslessly. The symptom-indexed recipes in
   `heuristics.json` are a **derived projection**, rebuildable from raw at any time — never the
   source of truth.

## 3. Background (verified against the code & live DB)

### 3.1 Two decoupled memories
- **Structured store** `knowledge/runs.sqlite` + `knowledge/heuristics.json`. Tables: `runs`,
  `run_violations`, `failure_events`, `config_lineage`, `fix_events` (3-tier: `fix_events` →
  `fix_trajectories` → `fix_recipes` in `heuristics.json`), `fix_events_archive`.
- **Prose docs** `references/failure-patterns.md`, `lessons-learned.md`, `signoff-fixing.md`,
  `orfs-playbook.md`. **The learner never reads them.** `search_failures.py` (BM25 over prose +
  `failure_candidates.json`) is consumed only by `analyze_execution.py` (a backend path), not
  by the signoff fixer `diagnose_signoff_fix.py`. There is no link between a prose lesson and a
  DB row.

### 3.2 Everything is family-name-keyed
- Config heuristics: per-`(design_family, platform)` medians of `CORE_UTILIZATION` /
  `PLACE_DENSITY_LB_ADDON` (`learn_heuristics.py`, `MIN_SUCCESSFUL=3`).
- Fix recipes: `heuristics.json["families"][FAM]["platforms"][PLAT]["fix_recipes"][check]
  [violation_class]` → per-strategy `{attempts, successes, failures, wins}`; ranked by
  `fix_model._score = (successes + 0.5·wins + 1)/(attempts + 2)` (flat Beta(1,1) prior,
  uniform 0.5 for untried). `design_family` is inferred from the **project-dir basename split
  on `_`** — a brand-new name prefix → `family="unknown"` → inherits nothing.

### 3.3 Measured corpus facts (live `runs.sqlite`)
- **780 runs**, **779 nangate45 / 1 sky130hd**; **417 fix-trajectories, all nangate45, 0
  sky130**.
- **133 families**, median **2** successes/family; only **47** family/platform pairs clear
  `MIN_SUCCESSFUL=3`. Small data — further stratification by name shatters samples; **symptom
  pooling enlarges them**.
- **`orfs_status='pass'` = 0/780**; all "success" flows through the relaxed
  `knowledge_db.is_success` predicate (the linchpin that was silently `False` for months once
  before — must stay the single source of truth).
- **`config_lineage.current_outcome` = `'partial'` on 100% of rows** → inert; credit
  assignment is dead-on-arrival until populated.
- **`eval_heuristics.py` has never run** (no `eval_results.json`/`eval_summary.json`) → the
  config loop's payoff is **unvalidated**.
- **Dead `fix_events` columns** (0/417 populated): `config_delta_json`, `env_flags_json`,
  `tool_versions_json`, `stage_metrics_json`, `rule_details_json`. The per-iteration
  `config_delta` is already printed at `diagnose_signoff_fix.py:367` and **discarded** at
  `fix_signoff.sh:140`.
- **Dead modules** queried by nobody: `search_failures.py`, `monitor_health.py`,
  `analyze_execution.py`. `suggest_config.py` is marked *"optional, not critical path"* in
  SKILL.md.

### 3.4 Why sky130hd is the right test bed
sky130hd has **1 run / 0 fix-trajectories** — a clean slate. Under the new design, a sky130hd
run's symptom signature should match the **pooled, platform-agnostic** experience learned on
nangate45 (e.g., LVS symmetric-matcher diagnosis, synth AST-pathology, timing-tier relax) and
**must not** pull nangate45-deck-specific fixes (antenna diode-forcing). The historical sky130
liberty quote-bug (cell_type/area/power → 0/UNKNOWN) is **already fixed** (`liberty.py:32-37`,
commit `363a8b2`); it becomes an extraction **regression check**, not a blocker.

## 4. Design

### 4.1 The symptom signature (the index)

A **symptom signature** is a small canonical object plus a derived stable id:

```
symptom = {
  "check":      "drc" | "lvs" | "timing" | "synth" | "orfs_stage",
  "class":      <coarse class>,      # dominant DRC category | lvs mismatch_class
                                     # | timing tier | synth-timeout kind | stuck-stage name
  "predicates": { <name>: true, ... } # the SMALL, curated, decision-relevant boolean set
}
symptom_id = sha1(canonical_json(check, class, sorted(decision_relevant_predicate_keys)))
```

The **predicate set is deliberately small and curated per check** (not "every boolean") to
avoid singleton buckets. Initial set, all already computed inside the extractors and currently
discarded:

| check | class examples | predicates (booleans) |
|-------|----------------|------------------------|
| lvs | `symmetric_matcher`, `real_connectivity`, `generic`, `incomplete` | `nets_balanced`, `device_mismatch_present`, `same_cell_swap_present`, `sigsegv`, `internal_assertion`, `extraction_terminated` |
| drc | dominant category (`*_ANTENNA`, `MET_SPACING`, …) | (category vector already in `class`); `beol_only` |
| timing | tier (`minor`/`moderate`/`severe`) | `single_dominant_path` |
| synth | `ast_pathology`, `scale_timeout` | `post_ast_marker_ge_3` |
| orfs_stage | stuck-stage fingerprint (`place_resized_repair_design_stuck`, …) | (fingerprint already in `class`) |

**Conditioning attributes** (NOT part of `symptom_id`): `platform` (with `"*"` wildcard),
`shape_band` (Phase 2), `env_regime` (PLACE_FAST/ROUTE_FAST). **Provenance** (NOT a key):
`evidence_designs` (design names / `fix_session_id`s).

**Capture point — symptoms are raw, first-class records.** A new **`symptoms`** dimension
table holds one row per distinct `symptom_id` (the canonical `{check, class, predicates}` +
`symptom_schema_version` + `first_seen`). Every raw row that observes a symptom carries a
denormalized **`symptom_id`** column (FK → `symptoms`) plus its `signature_json`: on
**`fix_events`** (Tier-1, raw actions), **`fix_trajectories`** (Tier-2, raw episodes), and
**`run_violations`** (per-run snapshot). The extractors
(`extract_lvs.py::classify_lvs_mismatch`, the synth-timeout classifier, `extract_drc.py`) emit
the predicate booleans; `fix_signoff.sh::_log_iter` / `check_timing.py --journal` write them
into `fix_log.jsonl`; `ingest_run.py` computes `symptom_id` and stores both the hash and the
raw `signature_json` — so `symptom_id` can be re-derived if the hashing/predicate scheme
changes (the predicates are never lost). Raw `fix_events` may still archive to the
`fix_events_archive.sqlite` sidecar past the size threshold (moved, never deleted); the
`symptoms` table and Tier-2 trajectories are **never archived**, so every symptom stays
queryable.

### 4.2 Re-keyed fix recipes (symptom buckets)

`heuristics.json` gains a top-level **`symptoms`** map — a **derived projection** of the raw
`symptoms` table + symptom-tagged `fix_trajectories`, rebuildable at any time and never the
source of truth. The agent reads this JSON in-context; the SQLite tables are the raw store. The
`families[…]fix_recipes` subtree is demoted (see §8 migration):

```
symptoms[symptom_id] = {
  "check": ..., "class": ..., "predicates": {...},
  "platforms_seen": ["nangate45", ...],
  "evidence_designs": [...],            # provenance only
  "n_sessions": N,
  "strategies": {
    "<sid>": {
      "attempts": N, "successes": N, "failures": N, "wins": N,
      "median_reduction_pct": P, "median_elapsed_s": S,   # S used in Phase 2 cost ranking
      "platform_specific": false,
      "by_platform": { "nangate45": {attempts,successes,...}, "sky130hd": {...} }
    }, ...
  }
}
```

- `learn_heuristics.py` aggregates the symptom-tagged `fix_trajectories` (and archived
  `fix_events` via the existing ATTACH+UNION) **by `symptom_id`**, pooled across all families
  and platforms, into the derived `symptoms[…]` projection. `by_platform` retains the
  per-platform breakdown so gating still works. Because the aggregate is derived, re-learning
  after a predicate-set or hashing change is a full rebuild from raw — no data loss.
- `platform_specific` is set when a strategy's clearances are concentrated on one platform AND
  it is declared platform-specific by a linked lesson (e.g. `antenna_diode_repair` on
  nangate45). Platform-specific strategies do **not** transfer their prior to other platforms.

### 4.3 Informed cold-start priors (falls out of §4.2)

`fix_model._score` no longer starts every untried strategy at 0.5. For a run whose symptom is
`symptom_id` on platform `P`:
- prior for strategy `sid` = pooled clearance of `sid` in `symptoms[symptom_id]` — preferring
  `by_platform[P]` if present, else the pooled (platform-agnostic) rate, **unless**
  `platform_specific` and `P` differs (then fall back to the flat prior).
- The Beta posterior then updates from `P`'s own observations as they accrue. This is the one
  endorsed-by-all "ML" change; no schema change beyond §4.2.

### 4.4 The `lessons` table (Option B) — prose↔struct link + in-context retrieval

Prose remains source-of-truth. Each `##` section in `failure-patterns.md` /
`signoff-fixing.md` may carry a machine-readable front-matter block:

```html
<!-- r2g-lesson:
id: lesson-lvs-symmetric-matcher
status: active            # active | retired
trigger: {check: lvs, class: symmetric_matcher,
          predicates: {nets_balanced: true, device_mismatch_present: false},
          platform: "*"}                      # "*" = tool-behavior, transfers across platforms
strategy_ids: [lvs_same_nets_seed]
-->
```

(An antenna lesson would set `platform: nangate45` to gate it.)

New table:

```sql
CREATE TABLE IF NOT EXISTS lessons (
  lesson_id            TEXT PRIMARY KEY,
  source_doc           TEXT,                 -- failure-patterns.md#<anchor>
  section_title        TEXT,
  status               TEXT,                 -- active | retired
  symptom_trigger_json TEXT,                 -- {check, class?, predicates?, platform}
  strategy_ids_json    TEXT,
  prose_excerpt        TEXT,                 -- first ~400 chars for in-context display
  evidence_runs_json   TEXT,                 -- AUTO back-filled (do not hand-edit)
  content_hash         TEXT,                 -- sha1(section body) -> drift detection
  synced_at            TEXT
);
```

- **`knowledge/sync_lessons.py`** (new): one-way, idempotent prose→table upsert by
  `lesson_id`; back-fills `evidence_runs_json` by matching `symptom_trigger_json` against
  `run_violations.signature_json` / `fix_trajectories.signature_json`; `content_hash` re-syncs
  on prose edits. Invoked from the existing post-ingest hook `fix_log_manager.manage()`. Tiny
  table (~one row per `##` section); no archival needed.
- **Retrieval (the payoff):** `search_failures.py` is upgraded to parse the front-matter and
  do a **structured pre-filter on the current symptom** (and platform, honoring `"*"`) before
  BM25; `diagnose_signoff_fix.py` calls it at ranking time and surfaces the matched **active**
  lesson's `prose_excerpt` + linked recipe alongside the Beta ranking. The agent thus sees the
  data *and* the human rationale at the moment it chooses. `status: retired` filters
  superseded lessons (e.g. the antenna "unfixable→fixable" reversal) without deleting them.
- This is the act of **wiring `search_failures.py` into the decision path** — it stops being a
  dead module (§5.0.3).

### 4.5 Cross-cutting observation capture (decision-changing signals only)

- **`config_delta_json`** — capture the per-iteration edit already at
  `diagnose_signoff_fix.py:367` (currently discarded at `fix_signoff.sh:140`). Highest-value
  single capture: makes per-edit attribution work instead of crediting the whole stacked
  block.
- **`env_flags_json`** — record PLACE_FAST/ROUTE_FAST regime so recipes aren't blended across
  effort regimes (a ROUTE_FAST=1 DRT-capped fix isn't comparable to full routing).
- `stage_metrics_json` (slack/congestion trajectory) and `tool_versions_json` are **deferred**
  (Phase 2) — only added when a consumer reads them (pragmatist's no-dead-plumbing rule).

### 4.6 Initial-config side (shape-indexed, not name)

`suggest_config.py` is off the critical path, so this is lower priority, but it must also stop
keying on family-name. When choosing pre-failure knobs (no symptom exists yet), index by
**design shape** — `(cell_count_band, design_type, platform)`, all structural — with a
nearest-neighbor fallback across all designs. `heuristics.json` gains a parallel `by_shape`
index. **This is Phase 2** (it shares the `by_shape` work and is off the critical path);
Phase 1 only removes family-name as the config key by routing `suggest_config` through
`design_type`/size, not the name prefix. (If the user later prefers to drop the
config-heuristic side entirely and keep this purely symptom-indexed repair memory, that is a
clean cut.)

## 5. Phasing

### Phase 0 — Honesty gate (cheap; de-risks everything)
- **0.1** Run `eval_heuristics.py` on ~6–10 **small nangate45** designs with non-empty
  learned≠naive knob diffs; produce `eval_results.json` + `eval_summary.json`; publish the
  verdict on whether the learned config loop beats naive. Gates all of Phase 2's config work.
- **0.2** Populate `config_lineage.current_outcome` in `ingest_run._record_lineage` with
  structured `{is_success, wns_ns, drc_violations, total_elapsed_s}` for child *and* parent;
  make the write re-ingest-idempotent (see §7).
- **0.3** Resolve the 3 dead modules: **wire** `search_failures.py` into the signoff decision
  path (it becomes §4.4's retrieval engine); **wire or deprecate** `monitor_health.py` (it
  already computes drift — surface in the dashboard health panel or mark deprecated);
  **delete** `analyze_execution.py` if unconsumed, else document why kept. No unqueried
  plumbing remains.

### Phase 1 — Symptom-indexed core (committed)
- **1.1** Symptom signature schema + predicate capture in extractors → `signature_json` on
  `run_violations`/`fix_trajectories` (§4.1).
- **1.2** Re-key fix recipes to `symptoms[symptom_id]`, pooled cross-family/cross-platform,
  with `by_platform` + `platform_specific` (§4.2).
- **1.3** Informed cold-start priors in `fix_model` (§4.3).
- **1.4** `lessons` table + front-matter + `sync_lessons.py` + retrieval wired into
  `diagnose_signoff_fix.py` (§4.4).
- **1.5** Capture `config_delta_json` + `env_flags_json` (§4.5).

### Phase 2 — Deferred, trigger-gated
Credit-assignment-weighted config medians (unblocked by 0.2) · cost-aware fix ranking
(`median_elapsed_s` tiebreaker, wall-clock only) · `by_shape` conditioning of symptom recipes ·
hierarchical shrinkage on config medians · tool-version drift discounting · `rtl_origin`
provenance tags · `stage_metrics_json` capture. **Revisit when:** corpus ≥ ~2.5× current OR
median successes/family ≥ 5 OR a tool upgrade lands OR Phase-0's A/B shows clear config-loop
payoff.

## 6. Validation plan (sky130hd)

Set up **3–5 small sky130hd designs** under `design_cases/`, chosen to provoke distinct
symptoms, run the full flow, ingest, re-learn, and assert:

1. **Extraction regression guard.** `reports/ppa.json` on sky130hd has non-zero
   area/power/cell_type and a resolved `cell_type_id` (guards the historical quote-bug,
   `363a8b2`).
2. **Signature capture.** Each sky130hd run writes a `signature_json` to `run_violations`
   (and to `fix_trajectories` if it enters a fix episode).
3. **Cross-platform transfer.** For a **platform-agnostic** symptom (e.g. an LVS
   symmetric-matcher residual, a synth AST-pathology, or a timing-tier relax), the sky130hd run
   retrieves the **nangate45-learned** recipe + the matching `active` lesson via `symptom_id`
   — demonstrating recall by symptom, not by name/platform.
4. **Platform-specific gating.** A sky130hd **antenna/DRC** symptom does **not** pull the
   nangate45 `antenna_diode_repair` recipe (flagged `platform_specific` / lesson
   `platform: nangate45`); sky130's own handling is used.
5. **Experience accrual.** After ingest, the touched `symptoms[…]` buckets show
   `platforms_seen += "sky130hd"` and `evidence_designs += <sky130 designs>` — the memory now
   carries sky130 experience **indexed by symptom**, with family-name nowhere in the key.

Per the repo's "When You Fix a Bug" workflow: add/adjust the relevant
`failure-patterns.md`/`signoff-fixing.md` sections (with front-matter), re-run
`ingest_run.py` + `learn_heuristics.py`, and commit with a `feat(skill):` prefix.

## 7. Must-not-break invariants (each ships a test)

1. **`is_success` stays the single source of truth** (`knowledge_db.is_success`) — never
   forked.
2. **Idempotent ingest.** `run_id = sha1(project_path + ppa_mtime)`. New columns on existing
   rows (`signature_json`, `config_delta_json`, `env_flags_json`, lineage outcome) require
   switching `_ingest_fix_events`'s `INSERT OR IGNORE` (`ingest_run.py:151`) to an explicit
   `ON CONFLICT … DO UPDATE` for the *new* columns only (don't clobber `provenance`), else
   re-ingest silently no-ops the enrichment.
3. **Hard safety clamps beat learned values** — `PLACE_DENSITY_LB_ADDON ≥ 0.10` floor +
   design-type clamps applied as a post-filter; no learned/symptom path can route around them.
4. **Symlink deploy** — any memory the agent must read lives on the `.claude/skills/…`
   symlinked path (a `cp` install goes stale).
5. **No auto-promotion of prose** — `failure_candidates.json`/`mine_rules.py` stay
   human-review-only; `sync_lessons.py` only *links* curated prose to evidence, never writes
   prose.
6. **Raw is the system of record; aggregates are derived.** Raw actions (`fix_events`),
   trajectories (`fix_trajectories`), and symptoms (`symptoms` table +
   `symptom_id`/`signature_json` on the raw rows) are retained losslessly — Tier-2 and the
   `symptoms` table are **never archived**; Tier-1 `fix_events` archives to the sidecar (moved,
   not deleted). The `heuristics.json["symptoms"]` projection must be fully rebuildable from
   raw. A test asserts a from-scratch re-learn reproduces the aggregate.
7. **New invariant:** family/design-name must not reappear as a learning or lookup key — only
   as `evidence_designs` provenance. A test asserts `symptoms[…]` keys are symptom hashes and
   that lookup never consults `design_family`.

## 8. Data-model changes & migration

- **New `symptoms` dimension table** (raw symptom catalog): `symptom_id` PK, `check`, `class`,
  `predicates_json`, `symptom_schema_version`, `first_seen`. Plus a denormalized **`symptom_id`**
  column + `signature_json` on `fix_events`, `fix_trajectories`, and `run_violations`
  (FK → `symptoms`).
- **Raw primary keys are unchanged** — `fix_events UNIQUE(fix_session_id, iter, strategy)`,
  `fix_trajectories PK(fix_session_id, check_type)`, `run_violations PK(run_id)` all stay; we
  only *add* the `symptom_id` attribute, so the raw grain is preserved.
- **Indexes:** ADD a SQL index on `symptom_id` for `fix_events`, `fix_trajectories`, and
  `run_violations`, and on `symptoms(check, class)`, so symptom lookups are fast. KEEP the
  existing `runs(design_family, platform)` indexes (now provenance/human-query only, no longer
  the learning path).
- **Other schema additions:** `lessons` table (§4.4); populate `fix_events.config_delta_json` /
  `env_flags_json`; structured `config_lineage.current_outcome`. `heuristics.json` gains the
  derived `symptoms` (and `by_shape`) maps and a `schema_version` bump.
- **One-time rebuild:** derive `symptoms[…]` from existing `fix_trajectories`. Historical
  trajectories lack the new predicate booleans, so backfilled signatures use `class` +
  whatever predicates are recoverable from `before_categories_json` / `lvs_mismatch_class` /
  the on-disk report JSONs; rows that can't be enriched fall into the coarse `class`-only
  bucket and are flagged lower-fidelity (consistent with the existing
  `provenance="backfill:*"` convention).
- **Backward-compat:** retain `families[…]fix_recipes` read-only for one release (or drop with
  the migration); `suggest_config`/`diagnose_signoff_fix` read `symptoms` first, falling back
  to the old subtree only during the transition.

## 9. Risks & open questions

- **Signature granularity.** Too coarse → distinct bugs collapse; too many predicates →
  singleton buckets that defeat pooling. Mitigation: the curated minimal predicate set (§4.1),
  reviewed against the prose's actual discriminators; revisit after sky130 validation.
- **Cross-platform mis-transfer.** A genuinely platform-specific symptom not yet flagged could
  transfer a wrong recipe to sky130. Mitigation: same-platform-preferred ranking +
  `platform_specific` flag + lesson `platform` gating; sky130 validation step 4 is the guard.
- **Backfill fidelity.** Most of the 417 nangate45 trajectories backfill to coarse signatures;
  full-fidelity predicates only accrue on new runs. Acceptable — the new signal compounds.
- **sky130hd flow cost.** sky130 flows are heavier than nangate45; keep validation designs
  small (§6) and few.
- **Config-side scope.** §4.6 keeps a shape-indexed config recommender; open question whether
  to drop it entirely and make this purely symptom-indexed repair memory (user's call).

## 10. Out of scope / escalation

CDC, multi-clock, DFT, signoff-quality closure (already user-escalated) · contextual
bandits / embeddings / hierarchical Bayesian models (unjustified at 780 rows) · a true config
version DAG · CPU/RAM cost capture (only wall-clock is honestly available) · auto-promotion of
mined rules into prose · inventing vendor-defined values during RTL recovery.

## 11. Success criteria

1. No learning or lookup path keys on design-family/name; symptom signature is the index
   (invariant test §7.7).
2. A symptom learned on nangate45 is retrieved for a structurally-matching sky130hd run
   (validation §6.3), and a platform-specific fix is correctly *not* transferred (§6.4).
3. The matched prose lesson + its recipe appear in the agent's context at the signoff
   decision point.
4. Phase-0 publishes an honest A/B verdict and leaves zero unqueried modules.
5. Full pytest suite green with new tests for signature extraction/matching, symptom
   re-keying, informed priors, front-matter sync/retrieval, lineage-outcome population, and
   idempotent re-ingest.
