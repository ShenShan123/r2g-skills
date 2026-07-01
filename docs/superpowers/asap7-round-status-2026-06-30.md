# ASAP7 `/r2g-debug` Round — Status Report (2026-06-30)

**Prepared on operator request** ("stop the campaign and write a report"). The wave campaign has
been **stopped** (driver process group killed + orphaned KLayout/OpenROAD stage trees reaped +
the recurring 2h `/loop` cron `a2d9a809` cancelled). Load fell 77 → ~6 immediately after.

This report is scoped to `platform='asap7'`. `knowledge.sqlite` is a **shared** store; the
nangate45/sky130 rows below are prior-round history, not this campaign's work.

---

## TL;DR

- **The loop is HONEST (`honesty.py` 5/5 GREEN) and its A/B machinery is proven live** (asap7
  `ab_trials` 0→3; 12 candidates learned; 2427 fix_events). Integrity: **0 ALARM** (2 explained WARN).
- **7 real skill bugs found + fixed this round (#1–#7), all TDD-guarded + committed + pushed to
  `origin/main` (@ `883a446`).** Plus one self-inflicted operator-script bug (iter-5 ledger
  corruption), found and repaired.
- **An 8th bug was discovered during this very status check** and **reconciled**: 8 asap7 A/B
  antenna-arm rows were fabricated-clean from stale nangate45 reports (`lvs=clean`, impossible on
  asap7). Deleted; store re-verified honest. Root-cause code fix is **recommended/open** (below).
- **asap7 has 0 genuinely DRC-clean designs — and that is the honest platform truth, not a loop
  failure.** asap7's `asap7.lydrc` deck is not flow-achievable-clean (even ORFS's own `gcd` yields
  ~20 violations); ORFS gates asap7 only on router-DRC. Per operator decision, we kept gathering
  honest DRC-fail + Fmax + bug data rather than forcing a promotion or redefining signoff.

---

## Campaign state at stop

| Metric (asap7)            | Value                                                        |
|---------------------------|-------------------------------------------------------------|
| Runs ingested             | 49                                                          |
| DRC (`asap7.lydrc`)       | **clean=0**, fail=38, stuck=9 (+2 orfs-fail w/o DRC)        |
| LVS                       | **skipped=47** (honest — no deck), clean=0 (post-reconcile) |
| A/B trials                | 3 (judging proven working; arms reach + stay terminal)     |
| Candidates learned        | 12                                                          |
| Promotions                | **0 — honest** (asap7 not DRC-cleanable; see below)         |
| Ledger (708 normal)       | pending=668, escalated=34, fixing=6 (interrupted mid-flow)  |

Global (shared store): `ab_trials=165`, `fix_events=2427`, `fix_trajectories=2217`, journal
`actions=26923`. Promotions: nangate45 **10 promoted** / 34 cand; sky130hd **1 promoted** / 6 cand
/ 4 shadow. (These are prior rounds — this campaign's contribution is the asap7 rows above.)

> The 6 `fixing` normal designs were mid-flow when the campaign was killed. They were never ingested
> as clean, so they are **not** a stored lie — just an interrupted ledger state that a future resume
> re-picks. No reconciliation needed.

---

## Honesty & integrity

- `python3 r2g-rtl2gds/knowledge/honesty.py --db …/knowledge.sqlite` → **ALL 5 GATES GREEN**
  (fail↔event parity, no event on non-fail run, ab_trials-nonempty-when-failures, events derivable).
- `python3 tools/check_db_integrity.py --platform asap7` → **verdict WARN (0 ALARM, 2 WARN, 11 PASS)**.
  Both WARNs are explained, not writer bugs:
  - **K3** (asap7 ab_trials>0 but promoted=0, 3 inconclusive) — the honest "asap7 not cleanable"
    outcome; the arms genuinely diverge in *work*, they just can't reach a decisive clean verdict.
  - **J4** (2 dangling journal run_ids) — the documented baseline, back to 2 after cleanup.

---

## Bugs found + fixed this round (all committed, pushed to `origin/main`)

1. **#1 `setup_rtl_designs.py --platform` no-op** (`58c7e27`) — only accepted `--platform=asap7`
   (equals form); the documented space form silently re-pointed 0 config.mk. Blocked the whole round.
2. **#2 techlib byte-diff gate** (`c1b07cd`) — hard-errored when a campaign consumed pinned RUN inputs;
   now exits 77 (skip) on absent input.
3. **#3 asap7 Fmax 1000× ps/ns** (`c679ee6`) — asap7 liberty is `time_unit=1ps`; Fmax reported 1000×
   fast. Reporting-boundary normalize; nangate45 byte-identical.
4. **#4 fabricated clean from stale prior-platform reports** (`b710905`) — the cardinal-sin honesty
   bug: re-targeted asap7 designs inherited stale nangate45 `reports/` and were ingested clean without
   running signoff. `_run_flow` now deletes stale signoff reports before re-flow.
5. **#5 `run_lvs.sh` set-e abort** (`e29e329`) — a no-match `grep` under `set -euo pipefail` aborted
   before the graceful skip path; `|| true` makes the honest `lvs=skipped` reachable.
6. **#6 A/B re-plan resets clean arms before judge** (`3b2e7b6`) — `plan_arms_for_candidates` reset
   terminal-unjudged arms to pending each cycle → arms never both-terminal at one judge moment →
   `ab_trials_asap7` stuck at 0. `_arm_awaiting_judge` guard fixed it (asap7 `ab_trials` 0→3).
7. **#7 `synth_memory_residual` escalation reason unregistered** (`883a446`) — the loop emits it but it
   was missing from `escalations.REASONS` → `open_escalation` raised `ValueError` → worker crashed →
   design mislabeled `worker_exc:ValueError`. Added to `REASONS` (TDD). Same latent-crash class the
   code's own `place_density_residual` comment had flagged.

Plus a **self-inflicted operator-script bug** (iter-5): a reconcile script rewrote the ledger
last-line-wins, dropping `project_path` on 24 designs → `worker_exc:KeyError`. Append-repaired + the
reconcile script fixed to merge.

---

## NEW finding this status check — fabricated-clean A/B antenna arms (reconciled; code fix open)

While gathering numbers for this report, I found **8 asap7 rows with `lvs_status='clean'`** — which is
impossible on asap7 (no LVS deck ⇒ must be `skipped`). All 8 were **A/B antenna-arm dirs**
(`…_abA_antenna__0` …) whose config.mk was re-pointed to asap7 but which carried a **stale nangate45
`reports/lvs.json`** (`raw_status:text_match_found`), written today. The arm's signoff was read from
stale prior-platform reports instead of freshly run/skipped for asap7 — the bug-#4 class, on an A/B
arm path the #4 fix did not cover.

- **Impact:** these were fabricated asap7 "cleans." They were `ab_arm` rows only (0 normal designs
  affected) and were referenced by 0 `ab_trials`, so they poisoned no verdict — but left uncorrected
  they are a stored lie.
- **Why the gates missed it:** `honesty.py`'s five gates check `fail`↔`failure_event` parity, **not
  whether a *clean* row is genuine.** Fabricated cleans are invisible to them — the same blind spot
  that made bug #4 dangerous.
- **Reconciled:** deleted the 8 rows + their projections + 16 stale report files. asap7 now reads the
  honest `lvs_clean=0 / lvs_skipped=47 / drc_clean=0`. Honesty re-verified 5/5 GREEN.
- **RECOMMENDED FIX (open, high-value):** add a **6th honesty gate** — *a run on a no-LVS-deck platform
  (asap7) MUST have `lvs_status ∈ {skipped, NULL}`; `clean`/`fail` is a contamination ALARM.* That
  single gate auto-catches this at ingest, turning a silent lie into a hard stop. Secondarily, harden
  the A/B arm create/ingest path to clear `reports/` before signoff (mirror the `_run_flow` fix).
  Documented in `references/failure-patterns.md` ("Stale prior-platform signoff report … RECURRENCE
  2026-06-30").

---

## The asap7 platform truth (why promotions = 0 is honest, not a bug)

asap7 is a 7nm **predictive** PDK. Its `asap7.lydrc` KLayout deck is community-reverse-engineered from
the DRM and is **not flow-achievable-clean**: every routed design carries an irreducible FEOL/MOL
cell-internal + tech-LEF via-AUX violation floor that no flow lever (utilization, density, period)
clears — even ORFS's own reference `gcd` fails it (~20 violations). ORFS itself only gates asap7 on
`detailedroute__route__drc_errors=0` (TritonRoute), **not** on `asap7.lydrc`. So:

- Our asap7 designs route to GDS (`orfs_status=pass`) but carry residual `asap7.lydrc` DRC.
- "0 DRC-clean" is the **honest** signoff result for this deck, and the loop **correctly refuses to
  promote** on it — that refusal is honesty working, not the loop being inert.
- Per operator decision, we did **not** change the asap7 signoff definition or force a promotion; the
  value of the asap7 round is honest DRC-fail data, realistic Fmax, and the 8 bugs it surfaced.

---

## Git / deployment state

- `main` @ **`883a446`** == `origin/main` (all 9 round commits pushed; clean fast-forward).
- Working branch `r2g-debug/asap7-round` merged into `main`; this report + the failure-patterns note
  are the only uncommitted changes (committed alongside this report).
- Skill deployed as a **symlink** (`.claude/skills/r2g-rtl2gds` → canonical tree); pytest suite green.

---

## Recommended next steps (for whoever resumes)

1. **Implement the 6th honesty gate** (no-LVS-deck ⇒ `lvs_status ∈ {skipped, NULL}`) — the highest-value
   follow-up; it closes the fabricated-clean blind spot that both bug #4 and this round's arm
   contamination exploited.
2. **Harden the A/B arm path** to clear `reports/` before signoff (mirror the `_run_flow` fix) so arms
   can't inherit a subject's cross-platform reports.
3. **RCX is not positively re-run on asap7** (all rows `rcx=NULL`) — honest absence, but the asap7
   "clean" contract wants RCX; wire an RCX pass for asap7 clean rows if pursuing that contract.
4. Resume with `/loop 2h /r2g-debug PLATFORM=asap7` (or a single `/r2g-debug PLATFORM=asap7`) — the
   round is idempotent/resumable off the existing `asap7_campaign.jsonl` (668 pending normal designs).
