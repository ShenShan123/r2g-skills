---
description: Drive an RTL→GDS sign-off campaign on an ORFS platform (ASAP7 by default; nangate45/sky130hd/gf180/ihp also supported) in parallel waves, hunt r2g-rtl2gds skill bugs, and prove the engineer-learning-loop is closed (DRC clean where the deck allows — asap7 KLayout DRC is not clean-able, Calibre is the authoritative path — + best Fmax + promoted recipes; LVS where the platform supports it).
argument-hint: "[overrides, e.g. PLATFORM=asap7 WAVE_MAX=24 WORKERS=3 NUM_CORES=4]"
---

# /r2g-debug — Drive, debug, and PROVE the r2g-rtl2gds learning loop (any ORFS platform)

You are debugging the `r2g-rtl2gds` skill by running a **real, parallel, wave-batched
RTL→GDS sign-off campaign** over the RTL designs in this project on a chosen **ORFS
platform**, and using that campaign as the test harness that surfaces skill bugs and proves
the closed learning loop works.

**Platform is the central knob — pick it from `$ARGUMENTS`, default `asap7`.** The whole
command is platform-parameterized; only the *signoff success contract* and a few bug-hunt
leads change per platform (see "Per-platform signoff contract" below). ASAP7 (a 7nm
predictive PDK shipped with ORFS) is the primary target of this command; nangate45 (the
completed historical round), sky130hd, gf180, and ihp-sg13g2 also work.

**Mission (do all of these — they are one connected goal, not a menu):**
1. Run the RTL designs in this project through the **`$PLATFORM` sign-off flow** using the
   *newest* version of the skill (the canonical `r2g-rtl2gds/` tree, freshly symlink-deployed).
2. **Batch the RTL designs into waves** and run them **in parallel to fully use the CPUs**
   (respecting the shared-host hard rule below — do not oversubscribe).
3. For every design: drive sign-off to the platform's **honest terminal state** (see the
   contract below — for ASAP7 under the KLayout deck that is **DRC run with its honest residual
   floor + RCX, LVS skipped**; asap7 KLayout DRC is NOT clean-able, so "DRC clean" is the wrong
   goal there — genuine DRC-clean needs the Calibre deck), and **search for the best Fmax**.
4. **Find bugs in the skill.** A campaign that runs without surfacing/fixing a real defect is
   suspicious — the loop is only as honest as its weakest writer. Treat every `fail` row, every
   misclassification, and every honesty-gate miss as a lead.
5. Prove the **engineer-learning-loop is well closed**: the skill is *actually learning* from
   **both failure and success** trajectories of the iterative fix actions — not just shipping
   machinery. The two memory DBs must record divergent action trajectories per fix attempt
   (including abandoned/failed ones: negative learning). **After every move, BOTH memory DBs
   (`knowledge.sqlite` = what resulted, `journal.sqlite` = what was done) must be updated
   correctly and tell the SAME story** — verify with `tools/check_db_integrity.py` (Step 0/2/4).
6. **New/successful solutions get promoted** (recipe `shadow → candidate → promoted`).
7. Prove the **effectiveness and robustness** of the skill end-to-end with evidence, not claims.

User-supplied overrides for this run (may be empty): **$ARGUMENTS**
Apply any `KEY=value` pairs above as environment overrides (`PLATFORM`, `LEDGER`, `WAVE_MAX`,
`WORKERS`, `NUM_CORES`). If empty, use the defaults below. Set the working variables once and
reuse them in every step:

```bash
cd /proj/workarea/user5/agent-r2g
PLATFORM=${PLATFORM:-asap7}                                   # $ARGUMENTS may override
LEDGER=${LEDGER:-design_cases/_batch/${PLATFORM}_campaign.jsonl}
# NOTE: the original nangate45 round historically lives in design_cases/_batch/campaign.jsonl
# (892 designs, all terminal). To RESUME it, pass LEDGER=design_cases/_batch/campaign.jsonl.
# New rounds (incl. asap7) use <platform>_campaign.jsonl so each round's history stays immutable.
EL=r2g-rtl2gds/scripts/loop/engineer_loop.py
KDB=r2g-rtl2gds/knowledge/knowledge.sqlite
JDB=r2g-rtl2gds/knowledge/journal.sqlite
```

---

## Per-platform signoff contract (read this before believing any `fail`/`incomplete`)

`r2g-rtl2gds/SKILL.md` "Platform Support Matrix" is ground truth. The honest "clean" state is
**platform-dependent** — demanding LVS on a platform with no LVS deck would mislabel every
clean design and teach the loop a lie. The clean-gate is fail-closed on `{clean, clean_beol,
skipped}`, so a *legitimately skipped* check IS clean.

| Platform     | KLayout DRC | LVS            | RCX | Honest terminal state means …                     |
|--------------|-------------|----------------|-----|---------------------------------------------------|
| **asap7**    | Yes¹        | **No (skipped)** | Yes | GDS + **DRC run w/ honest residual floor (NOT clean-able)** + RCX; **`lvs=skipped` is honest-clean** |
| nangate45    | Yes         | Yes (KLayout)  | Yes | GDS + DRC clean + LVS clean + RCX                 |
| sky130hd     | Yes         | Yes (Netgen)   | Yes | GDS + DRC clean + LVS clean + RCX                 |
| gf180/ihp    | Yes         | Yes (KLayout)  | Yes | GDS + DRC clean + LVS clean + RCX                 |

¹ **asap7 KLayout DRC is NOT clean-able.** The community `asap7.lydrc` deck is a DRM
reverse-engineering with an *irreducible false-violation floor* (min ~8; e.g. traffic_control=25,
master_dma=119 — `V*.M*.AUX`, `LIG*`, `V0`, `M4.S.5` tech-LEF-vs-deck artifacts present even on
ORFS's own `gcd`). No flow lever clears it. So on asap7 the honest terminal DRC state is
**`fail` with a documented residual floor**, and **"no asap7 DRC-clean" / "no asap7 DRC promotion"
is HONEST platform truth, not a bug to chase.** Chasing asap7 to "DRC clean" is exactly what
spawned the 2026-06-30/07-01 fabricated-clean bug. See `references/failure-patterns.md`
"ASAP7 residual-DRC-by-design".

**ASAP7 specifics (the new platform this command adds):**
- ORFS ships an `asap7.lydrc` KLayout DRC deck and `rcx_patterns.rules`, but **no `.lylvs`
  deck**. So `run_lvs.sh` *gracefully skips* and records `lvs.status='skipped'` — that is the
  expected, honest result, **NOT** a failure to hunt. Do **not** chase ASAP7 designs to
  "LVS clean" **or** "DRC clean"; success = **DRC ran + honest residual recorded + best Fmax (+ RCX)**.
- **Authoritative asap7 signoff = Calibre, not KLayout.** The official (encrypted) ASAP7 Calibre
  deck is the only genuinely clean-able asap7 DRC/LVS. This machine has Calibre 2025.1 + a license
  but NOT the deck (only placeholder READMEs; the deck is a gated academic download from
  https://asap.asu.edu/download/). A **guarded scaffold** is in place — `scripts/flow/run_calibre_drc.sh`
  + `scripts/extract/extract_calibre_drc.py` (skip cleanly until the deck is installed; emit
  `engine:calibre` verdicts in the `extract_drc` schema). When the deck is installed AND
  `R2G_CALIBRE_SMOKE=1` confirms it loads on this Calibre (deck targets 2017.4 — a real version risk),
  asap7 DRC-clean becomes achievable and the "no asap7 promotion is honest" premise must be revisited.
  Runbook + integration steps: `references/calibre-signoff.md`.
- ASAP7 is 7nm/dense; sizing is `CORE_UTILIZATION`-based (ORFS auto-sizes the die), so the same
  per-design configs port across platforms — but absolute areas/periods differ from nangate45.
- Required tools for ASAP7: `yosys`/`openroad`/ORFS + **KLayout** (for DRC). magic/netgen are
  sky130-only and irrelevant here — a red magic/netgen row in `check_env.sh` does NOT block ASAP7.

## Ground truth — read these first, they OVERRIDE your priors

- `CLAUDE.md` → **"The Closed Learning Loop"** and **"Honesty invariants"** — the contract you
  are verifying. Re-read the honesty invariants; they are the pass/fail criteria for "the loop
  is closed." They are **platform-agnostic** (only the signoff contract above is per-platform).
- `r2g-rtl2gds/SKILL.md` — workflow, **Platform Support Matrix**, hard rules, env knobs
  (`PLACE_FAST`, `ROUTE_FAST`, Fmax step 5a).
- `r2g-rtl2gds/knowledge/README.md` — DB schema, CLI, the full numbered invariants.
- `r2g-rtl2gds/references/engineer-loop.md` — the autonomous driver, escalation, A/B lifecycle.
- `r2g-rtl2gds/references/failure-patterns.md` → **"Learning-Loop Closure Failures"** (the known
  ways the loop silently lies) and **"Platforms without KLayout LVS: asap7"**.
- `tools/check_db_integrity.py` — the one-command **both-DBs** verifier (`--platform $PLATFORM`):
  knowledge honesty (delegated to `honesty.py`) + journal liveness + cross-DB `run_id` linkage +
  per-move correspondence (`ab_launch`/`promote`/`escalate` recorded in both books). ALARM = the
  loop is lying/blind; WARN = a best-effort-ledger drift to explain. Guarded by
  `tests/test_check_db_integrity.py`.

## Step 0 — Situational awareness (run, then summarize state before acting)

```bash
git log --oneline -5
git status -s | head
# Free cores: this host is SHARED (user4 finesim often pins ~80/96). Size to what's free.
nproc; uptime
# Campaign ledger + pending count + honesty snapshot (the alarm panel). If $LEDGER does not
# exist yet (a fresh platform round, e.g. asap7), Step 1b builds it — skip the status line.
[ -f "$LEDGER" ] && python3 "$EL" status --ledger "$LEDGER" 2>/dev/null | tail -20 \
  || echo "no ledger at $LEDGER yet — Step 1b will build the $PLATFORM round"

# BOTH-DBs integrity in one shot, scoped to this platform — knowledge honesty (delegated to
# honesty.py's five gates) PLUS journal liveness + cross-DB linkage + per-move correspondence.
# Read its verdict FIRST. ALARM => the loop is lying/blind (stop and fix); WARN => a
# best-effort-ledger gap to chase (a lead, not a blocker).
python3 tools/check_db_integrity.py --platform "$PLATFORM"

# Drill-down counts behind that verdict (knowledge = what RESULTED):
sqlite3 "$KDB" "
  SELECT 'fail='||(SELECT COUNT(*) FROM runs WHERE orfs_status='fail')
     ||' fe='||(SELECT COUNT(DISTINCT run_id) FROM failure_events WHERE signature LIKE 'orfs-fail-%')
     ||' partial='||(SELECT COUNT(*) FROM runs WHERE orfs_status='partial')
     ||' ab_trials='||(SELECT COUNT(*) FROM ab_trials)
     ||' fix_ev='||(SELECT COUNT(*) FROM fix_events)
     ||' cand='||(SELECT COUNT(*) FROM recipe_status WHERE status='candidate')
     ||' promo='||(SELECT COUNT(*) FROM recipe_status WHERE status='promoted');"
# Per-platform promotions (the 2026-06-24 'arms identical' alarm hides HERE, not in ab_trials):
sqlite3 "$KDB" "SELECT platform, status, COUNT(*) FROM recipe_status GROUP BY platform, status ORDER BY platform, status;"
# Journal = what was DONE. Confirm the decision ledger is alive and run_id-linked:
sqlite3 "$JDB" "
  SELECT 'actions='||(SELECT COUNT(*) FROM actions)
     ||' run_id_linked='||(SELECT COUNT(*) FROM actions WHERE run_id IS NOT NULL)
     ||' ab_launch='||(SELECT COUNT(*) FROM actions WHERE action_type='ab_launch')
     ||' promote='||(SELECT COUNT(*) FROM actions WHERE action_type='promote')
     ||' demote='||(SELECT COUNT(*) FROM actions WHERE action_type='demote')
     ||' escalate='||(SELECT COUNT(*) FROM actions WHERE action_type='escalate');"
```

Report what you see in plain language: how many pending **for `$PLATFORM`**, what
`check_db_integrity` says (PASS/WARN/ALARM and why), is honesty internally consistent, is
`promoted` growing **per-platform** or flat, and is the **journal keeping step with knowledge**.
Decide the wave plan from this, not from assumptions. Knowledge is a **shared** store across all
platforms — the historical nangate45 round's `runs`/`promoted` rows live here too; scope your
"did THIS campaign improve things" claims to `platform='$PLATFORM'`.

## Step 1 — Deploy the NEWEST skill as a symlink (non-negotiable)

A stale deployed skill is the single most expensive failure mode in this repo (the 2026-06-08
defect): the harness loads `.claude/skills/r2g-rtl2gds/`, **not** the canonical tree. A `cp`
goes silently stale while you edit the canonical skill. Force a symlink deploy:

```bash
bash r2g-rtl2gds/install.sh --project . --link --force
readlink .claude/skills/r2g-rtl2gds   # MUST resolve to the canonical r2g-rtl2gds/ tree
bash r2g-rtl2gds/scripts/flow/check_env.sh   # the tools $PLATFORM needs must be green
```

`check_env.sh` lists every ORFS platform it found and the tool paths. For **ASAP7** you need
`yosys`/`openroad`/ORFS green **and `KLAYOUT_CMD` green** (KLayout drives ASAP7 DRC); magic/netgen
being absent is fine (sky130-only). For nangate45/sky130 you additionally need the LVS tool of
that platform (`references/env.local.sh` pins magic/netgen for sky130). A flow that aborts on a
missing tool teaches the loop a lie — fix the environment *before* running flows.

## Step 1b — Bootstrap the per-platform ledger (run all RTL designs on `$PLATFORM`)

The campaign runs over **all set-up RTL designs configured for `$PLATFORM`**. The honest source
of truth for "which designs are on platform P" is each project's own `constraints/config.mk`
(`run_orfs.sh` builds against config.mk's `PLATFORM`, never the ledger field) — so a new platform
round **re-points config.mk for the whole corpus, then enumerates it into a fresh ledger**.
Bootstrap **only when `$LEDGER` is absent** (a fresh round — e.g. there is no `asap7` ledger yet;
the prior round was nangate45). If `$LEDGER` already exists, treat it as immutable history:
resume its `pending` rows, and if it has 0 pending the round is COMPLETE — report that and stop
(to deliberately start a new round, `rm` the ledger or pass a new `LEDGER=`):

```bash
if [ ! -f "$LEDGER" ]; then
  # 1) Re-target EVERY design's config.mk to $PLATFORM (rewrites `export PLATFORM = $PLATFORM`).
  #    Sizing is CORE_UTILIZATION-based, so this is platform-agnostic and safe; --force regenerates.
  #    (This intentionally overwrites the nangate45 config.mk — that round is COMPLETE and its
  #    runs are already ingested into knowledge.sqlite + recorded in campaign.jsonl, so no history
  #    is lost. design_cases/ is gitignored ephemeral build state.)
  python3 tools/setup_rtl_designs.py --platform "$PLATFORM" --force
  # 2) Enumerate every project whose config.mk now says PLATFORM=$PLATFORM into a fresh ledger.
  #    build_pending_ledger.py refuses to clobber an existing --out without --force, so the
  #    [ ! -f ] guard above + plain (no --force) call doubly protects prior-round history.
  python3 tools/build_pending_ledger.py --platform "$PLATFORM" --out "$LEDGER"
fi
python3 "$EL" status --ledger "$LEDGER" | tail   # confirm N pending $PLATFORM designs (0 ⇒ round complete)
```

`setup_rtl_designs.py` (re-point) and `build_pending_ledger.py` (enumerate) are the canonical
two-step re-target documented in `build_pending_ledger.py`'s header. **Never re-point ONLY the
ledger** — that would claim a platform the project isn't configured for, and `run_orfs.sh` would
silently build the OLD platform. After this step, `$LEDGER` holds one `pending` row per
`$PLATFORM` design.

## Step 2 — Run the campaign in parallel waves (Fmax → flow → A/B per wave)

**Hard rule (shared host):** keep `WORKERS × NUM_CORES ≤ free cores`. Default to the
good-neighbour pool `WORKERS=3 NUM_CORES=4` (≈12 cores) when finesim is loaded; scale UP toward
`WORKERS=8 NUM_CORES=12` only when `nproc`/`uptime` show the host is yours. Live-retune the
*next* wave with **no restart** by writing `tools/_${PLATFORM}_resume_logs/pool.env` (the driver
re-sources it each wave). Apply `$ARGUMENTS` overrides here.

The platform-generic batch driver `tools/campaign_resume_waves.sh` loops waves until `pending=0`
and — unlike the legacy nangate45 driver — runs the **full per-wave learning sequence**
(`fmax-drain → run → ab-drain → check_db_integrity`), emits a `WAVE_DONE` summary, and appends an
honesty snapshot + integrity verdict per wave. **Launch it in the background and monitor** — do
not block:

```bash
# Optional: pre-seed the live pool (re-sourced each wave): 
#   mkdir -p tools/_${PLATFORM}_resume_logs
#   printf 'WORKERS=3\nNUM_CORES=4\nWAVE_MAX=24\n' > tools/_${PLATFORM}_resume_logs/pool.env
PLATFORM="$PLATFORM" LEDGER="$LEDGER" WAVE_MAX=${WAVE_MAX:-24} WORKERS=${WORKERS:-3} NUM_CORES=${NUM_CORES:-4} \
  setsid bash tools/campaign_resume_waves.sh >/dev/null 2>&1 &
echo "driver pgid: $!"   # record the PGID — to stop a wave campaign you must kill the GROUP
```

If you prefer to drive each wave by hand (or to debug a single wave), run the same interleaved
sequence the driver runs — Fmax search is a *pre-pass* that proxy-searches the fastest closing
period per design and stamps its SDC, and **must run BEFORE `run`** on the same wave prefix:

```bash
python3 "$EL" fmax-drain --ledger "$LEDGER" --platform "$PLATFORM" \
        --max "${WAVE_MAX:-24}" --workers "${WORKERS:-3}"   # best Fmax → SDC, same prefix as run
python3 "$EL" run        --ledger "$LEDGER" --max "${WAVE_MAX:-24}" --workers "${WORKERS:-3}"
python3 "$EL" ab-drain   --ledger "$LEDGER" --workers "${WORKERS:-3}"   # judge pending A/B candidates
# After EVERY wave verify BOTH memory DBs recorded what happened, consistently. Exits non-zero
# iff a HARD invariant tripped — treat that ALARM as the next bug (Step 3); note WARN as a lead.
python3 tools/check_db_integrity.py --platform "$PLATFORM" \
  || echo "!! DB integrity ALARM after this wave — go to Step 3 before launching the next"
```

`--max N` makes `fmax-drain` and `run` pick the **same** first-N-pending prefix, so Fmax
characterization and sign-off interleave per wave instead of front-loading all of them. (`run`
has no `--platform` flag — it reads each ledger row's `platform`; the per-platform `$LEDGER`
already scopes it.)

While waves run, **`kill -9 -<PGID>` the process GROUP** (not just the python) if you must stop —
`run_orfs.sh` wraps stages in `setsid timeout`, so killing the driver alone orphans the make/openroad
tree. If a single huge design tail-blocks a wave at ~99% CPU for hours, that is *legit super-linear
extraction*, not a hang — only kill it if it truly blocks progress, and log that you did (no silent caps).

## Step 3 — Hunt skill bugs (this is the point, not a side effect)

After every wave, interrogate **both** DBs. Start with `python3 tools/check_db_integrity.py
--platform "$PLATFORM"` — it prints one PASS/WARN/ALARM line per invariant and its codes name the
lead directly (`H:*` knowledge honesty, `J1/J2/J4` journal liveness + cross-DB linkage,
`L1/L2/L3` per-move correspondence, `K3` per-platform stall). Each line below is a *lead*, and
several map to documented patterns — chase them down rather than papering over:

- **`fail` rows without a `failure_event`** (`H:every_fail_has_event`) → the learner is blind to a
  whole backend-failure class. `count(runs WHERE orfs_status='fail')` MUST equal the count carrying
  an `orfs-fail-%` event.
- **A move that landed in only ONE book** (`J2`, `L1`, `L2`, `J4`) → the two DBs disagree about what
  happened. `J2` (a run + journal actions but ZERO back-filled `run_id`) is an ALARM; `L1`/`L2`/`J4`
  are WARN (journal is best-effort — confirm a skipped journal call vs benign re-ingest/wipe residue).
- **Misclassified aborts** — diagnose the *true* reason from the stage log via
  `references/failure-patterns.md` before believing the status. Common: early synth abort filed as
  `unseen_crash`; FLW-0024 die-too-small filed as place divergence.
  - **ASAP7-specific lead:** `lvs` filed as `fail` when ASAP7 has **no LVS deck** → it must be
    `skipped` (the honest-clean state), not `fail`. An ASAP7 design marked `incomplete`/`fail` *only*
    because LVS didn't "pass" is a misclassification bug — fix the gate, don't chase a non-existent
    LVS clean. (The match-then-writer-crash LVS-filed-as-`fail` lead applies to nangate45/sky130, not ASAP7.)
- **Fabricated `clean` from STALE artifacts** (the 2026-06-30/07-01 bug — the single worst failure mode).
  Any **asap7 `drc_status='clean'` or `lvs_status='clean'`** is an ALARM by construction: asap7 KLayout
  DRC is not clean-able (footnote ¹) and asap7 has no LVS deck. The mechanism: an extractor read a LOCAL
  `drc/6_drc_count.rpt` / `lvs/6_lvs.lvsdb` that was OLDER than its own `drc_run.log` / `lvs_run.log`
  (a pre-copytree-fix A/B arm dir inherited a stale count, or the fresh result-copy was skipped), so a
  25-violation run recorded `clean/0`. **`honesty.py` does NOT catch this** — its five gates check
  `fail↔event` parity, not whether a *clean* verdict is real. Now guarded: `extract_drc.py` /
  `extract_lvs.py` / `extract_calibre_drc.py` carry an mtime freshness guard → they emit `stale`
  (fail-closed, outside the `{clean,clean_beol,skipped}` whitelist) rather than a fabricated clean, and
  `run_drc.sh` purges stale local artifacts before `make drc`. Still WATCH the invariant directly:
  `sqlite3 "$KDB" "SELECT COUNT(*) FROM runs WHERE platform='asap7' AND (drc_status='clean' OR
  lvs_status='clean')"` MUST be 0. A non-zero count means a stale-read slipped a fabrication in — reconcile
  it and check the arm-clone / copy path. The open belt: a 6th honesty gate (no-LVS-deck platform ⇒
  `lvs∈{skipped,NULL}`). See `references/failure-patterns.md` "Stale prior-platform signoff report".
- **`ab_trials` grows but `promoted` is flat for `$PLATFORM`** → the 2026-06-24 "arms are identical"
  alarm (subtler than empty `ab_trials`). Verify a trial's `metrics_json` shows the two arms genuinely
  diverging (different `is_success`/`outcome_score`/`fix_iters`), not wall-clock noise.
- **`fail`/`partial` rows exist but `ab_trials` is empty** → the loop is inert and lying; treat it
  exactly like an empty `heuristics.json`.
- **Fmax `status='error'`** vs honest `unconstrained`/`inconclusive` — an error that should have been
  a fallback (null floorplan slack → fall back to post-place) is a bug.

**When you find a real bug, fix it the project way** (see `CLAUDE.md` → "When You Fix a Bug"):
1. Find the existing bucket in `references/failure-patterns.md`/`lessons-learned.md`; append a sub-section.
2. Fix the offending `scripts/` file to detect + self-heal or emit a clear HINT. **Prefer editing
   existing scripts over adding new ones.**
3. Add/extend a **TDD test** that fails before and passes after; keep the pytest suite green.
4. Re-validate on the triggering design, **ingest** (`knowledge/ingest_run.py`), re-run
   `learn_heuristics.py`/`mine_rules.py` if a new rule is implied.
5. Reconcile any rows the bug mislabeled — but touch only the **latest-ingested row per project**;
   old `fail` + new `pass` must coexist (never clobber history).
6. **Commit** with a `feat(skill):`/`fix(skill):` prefix (the commit log is the long-term record).

## Step 4 — Prove the loop is CLOSED (evidence, not assertion)

The loop is "closed" only when ALL of these hold — show the SQL/output for each:

- **Honesty 5/5:** `python3 r2g-rtl2gds/knowledge/honesty.py --db r2g-rtl2gds/knowledge/knowledge.sqlite`
  passes over the **real committed store** (honesty is global, never platform-scoped).
- **Both DBs agree (no ALARM):** `python3 tools/check_db_integrity.py --platform "$PLATFORM"`
  exits 0 — knowledge honesty 5/5 *and* the journal kept step (every A/B launch / promote / escalate
  move recorded in both books, `run_id` back-fill intact, no dangling cross-DB references). Explain any
  residual `WARN` — acceptable only once you've named why it is not a live writer bug.
- **Failure learning:** `fix_events`/`fix_trajectories` captured fix attempts — including
  `abandoned`/`failed` ones (negative learning), not just successes.
- **Success learning + promotion:** at least one recipe transitioned `candidate → promoted`
  **on `$PLATFORM` (per-platform `promo` for `$PLATFORM` grew)**, backed by an `ab_trials` row whose
  arms genuinely diverged (arm A control loses / arm B forced-recipe wins).
- **Cross-design transfer:** a recipe learned on one design/class applies to another (symptom-keyed,
  not family-named) — evidence in `lessons`/`symptoms` or a promotion spanning classes.
- **Signoff + Fmax (per the platform's contract above):** the platform's honest terminal-state count
  grew this campaign. For **nangate45/sky130** that is DRC-clean + LVS-clean (+ RCX). For **ASAP7 under
  KLayout** it is **GDS reached + DRC ran and recorded its residual floor as `fail` (NEVER a fabricated
  `clean` — verify the fabrication invariant above is 0) + RCX + `lvs=skipped`** — do NOT require (or
  expect) asap7 DRC-clean or an asap7 DRC promotion; that is honest platform truth, not a gap. (Genuine
  asap7 DRC-clean requires the Calibre deck — if it is installed, run `run_calibre_drc.sh` and prove the
  clean via `engine:calibre`.) And Fmax is recorded (realistic GHz or an honest
  `unconstrained`/`inconclusive`, never a silent `error`).

If any of these fail, that failure **is** the next bug to fix — loop back to Step 3. Do not declare
victory on the strength of machinery existing; the A/B arms must have *executed, diverged, and
promoted*.

## Step 5 — Record durable learnings (don't let the session evaporate)

- Update `r2g-rtl2gds/references/` (failure-patterns / lessons-learned) and any
  `docs/superpowers/{plans,specs}` touched, with a **dated note (commit hash + superseded
  invariants)** — not just code+tests. Keep `CLAUDE.md`'s "no per-run results here" rule.
- Update the operator memory index for this campaign's outcome (platform, promotions gained, bugs
  fixed, honesty state) so the next session resumes from truth.
- Keep all changes on a branch off `main`; commit per fix; **only push/PR when the user asks.**

## Looping this command

This command is **idempotent and resumable**, so it is safe under `/loop` (e.g.
`/loop /r2g-debug PLATFORM=asap7`): each tick re-deploys the skill, picks up the same per-platform
`$LEDGER` where it left off (Step 1b is a no-op once the ledger has designs), runs the next waves,
and re-verifies the honesty invariants. Use a per-platform `pool.env` to retune the pool between
ticks without restart. Keep `WORKERS × NUM_CORES ≤ free cores` on every tick.

## Guardrails (hard rules — violating one corrupts the campaign or the host)

- Never run two configs with the same `DESIGN_NAME` + `FLOW_VARIANT` concurrently (the driver derives
  `FLOW_VARIANT` from the project-dir basename — keep names unique).
- Never set `PLACE_DENSITY_LB_ADDON` below `0.10` (placer divergence is irrecoverable).
- For >100K-cell designs, never run multiple LVS jobs concurrently (3–5 GB RAM each → 2–3× wall time).
  (N/A on ASAP7 — LVS is skipped — but DRC/extraction on large designs still wants headroom.)
- `WORKERS × NUM_CORES ≤ free cores` — the default grabs `nproc` (96) per flow; N flows oversubscribe N×.
- **One platform per round.** Don't mix platforms in one ledger or re-point config.mk for designs that
  are mid-flow on another platform — `run_orfs.sh` builds against config.mk's PLATFORM. Re-target only
  when the prior round is terminal (Step 1b overwrites config.mk).
- **Ingest after EVERY flow** — clean, failed, or partial. A failed run never ingested teaches nothing.
- **Escalate to the user before** attempting CDC, multi-clock, DFT, or signoff-quality closure —
  the loop NEVER blocks on unknowns; they go to the `escalations` queue.
