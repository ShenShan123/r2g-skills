---
description: Drive an RTL→GDS sign-off campaign on an ORFS platform (default sky130hd — genuinely clean-able KLayout DRC + Netgen LVS + RCX; sky130hs equally clean-able since 2026-07-09 via the bundled sibling-DRC-deck + .lyt lefdef repairs; nangate45/asap7/gf180/ihp also work) in parallel waves, hunt r2g-skills bugs, and prove the engineer-learning-loop is closed (DRC/LVS clean where the deck allows + best Fmax + promoted recipes). Also independently VERIFIES the RTL→Graph dataset conversion across three dimensions — topology (5 PyG views b–f, HeteroData by default), feature statistics, and labels↔sign-off reports — against raw DEF/LEF/liberty/SPEF + OpenDB ground truth (opt-in PDNSim IR-drop re-run) — and AUDITS the rtl-acquire synth-only corpus supply line (flow_scope='synth_only' honesty, synth-frontend-* event parity, publish gating).
argument-hint: "[overrides, e.g. PLATFORM=sky130hd WAVE_MAX=24 WORKERS=3 NUM_CORES=4]"
---

# /r2g-debug — Drive, debug, and PROVE the r2g-skills learning loop (any ORFS platform)

Run a **real, parallel, wave-batched RTL→GDS sign-off campaign** over this project's RTL designs on a
chosen **ORFS platform**, and use it as the harness that surfaces skill bugs and proves the closed
learning loop. **Platform is the central knob** (`$ARGUMENTS`, default `sky130hd`); only the *signoff
success contract* and a few bug leads change per platform. sky130hd is primary (clean-able DRC/LVS, so
a clean win can **promote** a recipe); sky130hs is equally clean-able since 2026-07-09 (footnote ³ —
verify its two bundled repairs each round); nangate45/asap7/gf180/ihp also work.

**Mission (one connected goal):** (1) run all designs through the `$PLATFORM` flow on the *freshly
symlink-deployed* skill; (2) batch into waves, parallel but not oversubscribed; (3) drive each design to
its platform's **honest terminal state** (per the contract below) + best Fmax; (4) **find skill bugs** —
a campaign that surfaces none is suspicious; (5) prove the loop **learns from both success and failure
trajectories** and **both DBs tell the same story** (`check_db_integrity.py`); (6) new/successful recipes
**promote** (`shadow→candidate→promoted`); (7) prove effectiveness with evidence, not claims; (8) verify
the **RTL→Graph dataset conversion** (Step 5) against raw DEF/LEF/liberty + OpenDB truth — an orthogonal
bug-hunt axis; (9) audit the **rtl-acquire synth-only corpus** (Step 6) — every `flow_scope='synth_only'`
fail carries a `synth-frontend-*` event, `graph_skipped` never counts as success, publish gating holds.

Apply any `KEY=value` from **$ARGUMENTS** as env overrides. Set the working vars once and reuse:

```bash
cd /proj/workarea/user5/r2g-skills   # (renamed from agent-r2g)
PLATFORM=${PLATFORM:-sky130hd}                               # $ARGUMENTS may override
LEDGER=${LEDGER:-design_cases/_batch/${PLATFORM}_campaign.jsonl}
# The historical nangate45 round lives in design_cases/_batch/campaign.jsonl (892 designs, terminal);
# resume it with LEDGER=…/campaign.jsonl. New rounds use <platform>_campaign.jsonl (immutable per round).
EL=r2g-skills/signoff-loop/scripts/loop/engineer_loop.py
KDB=r2g-skills/signoff-loop/knowledge/knowledge.sqlite
JDB=r2g-skills/signoff-loop/knowledge/journal.sqlite
```

---

## Per-platform signoff contract (read before believing any `fail`/`incomplete`)

`r2g-skills/signoff-loop/SKILL.md` "Platform Support Matrix" is ground truth. The clean-gate is
fail-closed on `{clean, clean_beol, skipped}` — a *legitimately skipped* check IS clean; demanding LVS
on a deck-less platform would mislabel every clean design.

| Platform       | DRC            | LVS              | RCX | Honest terminal state |
|----------------|----------------|------------------|-----|-----------------------|
| **sky130hd** ★ | Yes (KLayout²) | Yes (Netgen)     | Yes | GDS + DRC clean + LVS clean + RCX — clean-able ⇒ a clean win can promote |
| nangate45      | Yes (KLayout)  | Yes (KLayout)    | Yes | GDS + DRC clean + LVS clean + RCX |
| sky130hs       | Yes (KLayout²³)| Yes (Netgen³)    | Yes | GDS + DRC clean + LVS clean + RCX — clean-able ⇒ promotable (VERIFY the two bundled repairs³) |
| gf180/ihp      | Yes (KLayout)  | Yes (KLayout)    | Yes | GDS + DRC clean + LVS clean + RCX |
| asap7          | Yes¹ (KLayout) | No (skipped)     | Yes | GDS + **DRC run w/ honest residual floor (NOT clean-able)** + RCX; `lvs=skipped` is honest-clean |

¹ **asap7 KLayout DRC is NOT clean-able** — the community deck has an irreducible false-violation floor
(min ~8; e.g. traffic_control=25). "No asap7 DRC-clean / no asap7 promotion" is **honest platform truth,
not a bug** (chasing it spawned the 2026-06-30/07-01 fabricated-clean bug). The authoritative deck is
Calibre (not installed — guarded scaffold `run_calibre_drc.sh`/`extract_calibre_drc.py`; runbook
`references/calibre-signoff.md`). See failure-patterns.md "ASAP7 residual-DRC-by-design".

² **sky130 DRC gate = KLayout, not Magic** (2026-07-02, cd33f62+00351d8). Full-chip Magic reports ~4777
std-cell-internal artifacts on a KLayout-clean design → never the gate; it runs as a non-fatal advisory
only under `R2G_MAGIC_ADVISORY=1` (`extract_drc` attaches `magic_advisory{authoritative:false}`, never
changes `status`). Magic is still REQUIRED on sky130 — Netgen LVS uses it to extract SPICE.

³ **sky130hs needs two bundled repairs — auto-wired, but VERIFY each round** (2026-07-09,
failure-patterns #32/#33): (a) this ORFS checkout has **no sky130hs DRC deck** — `run_drc.sh` resolves
the **sibling `sky130hd.lydrc`** (pure sky130A process geometry, zero hd-specific content) as a
make-cmdline `KLAYOUT_DRC_FILE`; without it ORFS echoes "DRC not supported" with **exit 0** and every
design files a phantom `no_count_report` DRC fail. (b) stock `sky130hs.lyt` carries LEGACY KLayout
lefdef option names → `def2stream` silently drops ALL DEF geometry → portless SPICE → 100% false Netgen
`top_pin_mismatch`. Run `python3 tools/patch_sky130hs_lyt.py --check` (exit 2 = unpatched; **re-run
after every ORFS update** — an update restores the stock .lyt); the `run_netgen_lvs.sh` portless guard
files `status:"error"` ("GDS lost DEF geometry"), never a design `mismatch`.

**Env, per platform:** sky130hd needs yosys/openroad/ORFS + **KLayout + magic + netgen-lvs + sky130A
PDK** all green (pinned in `references/env.local.sh`; a red row **blocks** signoff — else DRC/LVS falsely
*skip* and teach a lie). LVS on sky130 is **Netgen, not KLayout** (wrong-tool = 12/12 false-fail,
2026-06-17). asap7 needs only KLayout (magic/netgen absent is fine). Sizing is `CORE_UTILIZATION`-based
everywhere, so per-design configs port across platforms (absolute areas/periods differ).

## Ground truth — read first, they OVERRIDE priors

- `CLAUDE.md` → **"The Closed Learning Loop"** + **"Honesty invariants"** — the pass/fail criteria (platform-agnostic).
- `r2g-skills/signoff-loop/SKILL.md` — workflow, Platform Support Matrix, hard rules, env knobs, Fmax (5a).
- `r2g-skills/signoff-loop/knowledge/README.md` — DB schema, CLI, numbered invariants.
- `r2g-skills/signoff-loop/references/engineer-loop.md` — driver, escalation, A/B lifecycle.
- `r2g-skills/signoff-loop/references/failure-patterns.md` → **"Learning-Loop Closure Failures"** + per-defect buckets cited below.
- `r2g-skills/def-graph/references/graph-dataset.md` — Step-5 stage: the 5 views, tensor schema, feature/label join, the 2026-07 audit chain.
- `r2g-skills/rtl-acquire/SKILL.md` — Step-6 stage: acquire → expand(synth-only) → repair → validate → publish; the scoped-reuse contract (BORROWS `run_orfs.sh`/`netlist_graph.py`/`ingest_run.py`, never reimplements) + the 5-rung definition-of-success ladder.
- `r2g-skills/eda-install/SKILL.md` — the env remedy: one-command detect → plan → install → pin `env.local.sh` → verify (no-sudo conda default).
- `tools/verify_graph_dataset.py` — the RTL→Graph **ground-truth oracle** (independent CSV re-derive + raw liberty/LEF/DEF re-parse; `--batch` sweeps, non-zero on any fail).
- `tools/check_db_integrity.py` — one-command **both-DBs** verifier (`--platform`): knowledge honesty (via `honesty.py`) + journal liveness + cross-DB `run_id` linkage + per-move correspondence. ALARM = loop lying/blind; WARN = ledger drift to explain.

## Step 0 — Situational awareness (summarize state before acting)

```bash
git log --oneline -5; git status -s | head
nproc; uptime   # SHARED host (user4 finesim often pins ~80/96) — size to free cores
[ -f "$LEDGER" ] && python3 "$EL" status --ledger "$LEDGER" 2>/dev/null | tail -20 \
  || echo "no ledger at $LEDGER yet — Step 1b will build the $PLATFORM round"
# BOTH-DBs integrity (read its verdict FIRST): ALARM ⇒ stop+fix; WARN ⇒ a lead.
python3 tools/check_db_integrity.py --platform "$PLATFORM"
# Knowledge = what RESULTED:
sqlite3 "$KDB" "
  SELECT 'fail='||(SELECT COUNT(*) FROM runs WHERE orfs_status='fail')
     ||' fe='||(SELECT COUNT(DISTINCT run_id) FROM failure_events WHERE signature LIKE 'orfs-fail-%')
     ||' partial='||(SELECT COUNT(*) FROM runs WHERE orfs_status='partial')
     ||' ab_trials='||(SELECT COUNT(*) FROM ab_trials)
     ||' fix_ev='||(SELECT COUNT(*) FROM fix_events)
     ||' cand='||(SELECT COUNT(*) FROM recipe_status WHERE status='candidate')
     ||' parked='||(SELECT COUNT(*) FROM recipe_status WHERE status='parked')
     ||' promo='||(SELECT COUNT(*) FROM recipe_status WHERE status='promoted');"
# Judge-v2 inconclusive reasons (both_arms_never_succeed=subjects never sign off; success_tie_cost_within_noise=cost-neutral):
sqlite3 "$KDB" "SELECT strategy, json_extract(metrics_json,'\$.reason') reason, COUNT(*)
  FROM ab_trials WHERE verdict='inconclusive'
  AND json_extract(metrics_json,'\$.judge_version')>=2 GROUP BY 1,2 ORDER BY 3 DESC LIMIT 12;"
# Per-platform promotions (the 2026-06-24 'arms identical' alarm hides HERE, not in ab_trials):
sqlite3 "$KDB" "SELECT platform, status, COUNT(*) FROM recipe_status GROUP BY platform, status ORDER BY 1,2;"
# Journal = what was DONE (decision ledger alive + run_id-linked):
sqlite3 "$JDB" "
  SELECT 'actions='||(SELECT COUNT(*) FROM actions)
     ||' run_id_linked='||(SELECT COUNT(*) FROM actions WHERE run_id IS NOT NULL)
     ||' ab_launch='||(SELECT COUNT(*) FROM actions WHERE action_type='ab_launch')
     ||' promote='||(SELECT COUNT(*) FROM actions WHERE action_type='promote')
     ||' escalate='||(SELECT COUNT(*) FROM actions WHERE action_type='escalate');"
# Synth-only corpus population (rtl-acquire ingests with flow_scope='synth_only'; a synth-only PASS is
# 'pass' — NEVER 'partial', which would flood the A/B planner with never-signoff subjects):
sqlite3 "$KDB" "SELECT flow_scope, orfs_status, COUNT(*) FROM runs GROUP BY 1,2;"
sqlite3 "$KDB" "SELECT signature, COUNT(*) FROM failure_events
  WHERE signature LIKE 'synth-frontend-%' GROUP BY 1 ORDER BY 2 DESC LIMIT 10;"
# Synth-only honesty parity — NEITHER honesty.py NOR check_db_integrity.py is flow_scope-aware;
# this is the ONLY gate that catches a synth_only fail missing its frontend event:
python3 r2g-skills/rtl-acquire/scripts/knowledge/project_frontend_diagnosis.py --check "$KDB"
```

Report in plain language: pending count **for `$PLATFORM`**, the `check_db_integrity` verdict + why, is
honesty internally consistent, is `promoted` growing **per-platform** or flat, does the journal keep step,
and — when synth_only rows exist — did the `--check` parity gate pass.
Knowledge is a **shared** store — scope "did THIS campaign improve things" to `platform='$PLATFORM'`.

## Step 1 — Deploy the NEWEST skill as a symlink (non-negotiable)

A stale deployed skill is the most expensive failure mode here (2026-06-08): the harness loads
`.claude/skills/signoff-loop/`, not the canonical tree; a `cp` goes silently stale. Force symlinks:

```bash
bash r2g-skills/install.sh --project . --link --force
for s in signoff-loop def-graph rtl-acquire eda-install; do readlink ".claude/skills/$s"; done
# ALL FOUR must resolve into the canonical r2g-skills/ tree
bash r2g-skills/signoff-loop/scripts/flow/check_env.sh   # the tools $PLATFORM needs MUST be green
```

A flow that aborts on a missing tool — **or silently *skips* DRC/LVS because its tool/PDK is unset** —
teaches the loop a lie. Fix the environment first (see the per-platform env note above). **The remedy
for red rows is the `eda-install` skill**, not ad-hoc installs:

```bash
bash r2g-skills/eda-install/bootstrap.sh --dry-run   # detect + per-tier plan, installs NOTHING
bash r2g-skills/eda-install/bootstrap.sh --yes       # install missing tiers → pin env.local.sh → verify
```

`eda-install/scripts/flow/check_env.sh` additionally prints a `[corpus expansion (rtl-acquire)]` section
probing the borrowed `run_orfs.sh` / `netlist_graph.py` / `ingest_run.py` trio — verify it (and that
`R2G_GRAPH_PYTHON` is set, else designs record `graph_skipped`) before any Step-6 corpus work.

## Step 1b — Bootstrap the per-platform ledger (only when `$LEDGER` is absent)

Truth for "which designs are on platform P" is each project's `constraints/config.mk` (`run_orfs.sh`
builds against config.mk's PLATFORM, never the ledger). A new round re-points config.mk for the whole
corpus then enumerates it. If `$LEDGER` exists, treat it as immutable history (resume `pending`; 0
pending ⇒ round COMPLETE — report and stop; `rm` or new `LEDGER=` to start a fresh round).

```bash
if [ ! -f "$LEDGER" ]; then
  # 1) Re-target EVERY config.mk to $PLATFORM (CORE_UTILIZATION sizing ⇒ platform-agnostic, safe).
  #    This overwrites the nangate45 config.mk — that round is COMPLETE + ingested; design_cases/ is gitignored.
  python3 tools/setup_rtl_designs.py --platform "$PLATFORM" --force
  # 2) Enumerate every project whose config.mk now says PLATFORM=$PLATFORM into a fresh ledger.
  python3 tools/build_pending_ledger.py --platform "$PLATFORM" --out "$LEDGER"
fi
python3 "$EL" status --ledger "$LEDGER" | tail   # confirm N pending (0 ⇒ round complete)
```

**Never re-point ONLY the ledger** — that claims a platform the project isn't configured for, and
`run_orfs.sh` would silently build the OLD one. Re-pointing config.mk for a NEW round no longer re-keys
the PRIOR round's built datasets: def-graph stages + the verifier read platform from **build provenance**
(`run-meta.json` / `graph_manifest.json`) BEFORE the mutable config.mk (failure-patterns #30) — but the
one-platform-per-round rule still holds for anything in flight.

## Step 2 — Run the campaign in parallel waves (Fmax → flow → A/B per wave)

**Hard rule (shared host):** keep `WORKERS × NUM_CORES ≤ free cores`. Default `WORKERS=3 NUM_CORES=4`
(~12 cores) when finesim is loaded; scale toward `8×12` only when the host is yours. Retune the *next*
wave with no restart via `tools/_${PLATFORM}_resume_logs/pool.env`.

`tools/campaign_resume_waves.sh` loops waves until `pending=0`, runs the full per-wave sequence
(`fmax-drain → run → ab-drain → check_db_integrity`), and appends an honesty snapshot per wave. **Launch
in background, monitor — do not block:**

```bash
# SINGLE-INSTANCE GUARD (hard rule): NEVER launch a second driver (set_state race / FLOW_VARIANT collision).
# The driver self-guards (per-ledger flock + pgrep since 2026-07-04); this is the operator-side belt.
# pgrep is END-ANCHORED (un-anchored -f false-matches your own shell). If alive: monitor, retune, skip launch.
pgrep -f 'campaign_resume_waves\.sh$' && echo "driver ALREADY RUNNING — do NOT relaunch" || {
  PLATFORM="$PLATFORM" LEDGER="$LEDGER" WAVE_MAX=${WAVE_MAX:-24} WORKERS=${WORKERS:-3} NUM_CORES=${NUM_CORES:-4} \
    setsid bash tools/campaign_resume_waves.sh >/dev/null 2>&1 &
  echo "driver pgid: $!"   # record the PGID — to stop, kill the GROUP
}
```

To drive a wave by hand (Fmax is a pre-pass that stamps the fastest closing period into SDC — **must run
BEFORE `run`** on the same `--max` prefix so they interleave):

```bash
python3 "$EL" fmax-drain --ledger "$LEDGER" --platform "$PLATFORM" --max "${WAVE_MAX:-24}" --workers "${WORKERS:-3}"
python3 "$EL" run        --ledger "$LEDGER" --max "${WAVE_MAX:-24}" --workers "${WORKERS:-3}"
python3 "$EL" ab-drain   --ledger "$LEDGER" --workers "${WORKERS:-3}"
python3 tools/check_db_integrity.py --platform "$PLATFORM" \
  || echo "!! DB integrity ALARM after this wave — go to Step 3 before the next"
```

To stop: **`kill -9 -<PGID>` the process GROUP** (`run_orfs.sh` wraps stages in `setsid timeout`;
killing the driver alone orphans the make/openroad tree). A single huge design at ~99% CPU for hours is
legit super-linear extraction, not a hang — only kill if it truly blocks progress, and log it.

A driver killed without its group — or a host reboot — leaves designs in **transient ledger states**
(`flow`/`signoff`/`fixing`). Every drain entrypoint (`run`/`fmax-drain`/`ab-drain`) reclaims them to
`pending` at start (`orphan_reclaim:<state>` ledger events, stale `judged` dropped so a re-run A/B arm is
RE-judged), and `campaign_resume_waves.sh` counts OPEN work (`pending|flow|signoff|fixing`) — so simply
relaunching resumes honestly; an `ALL_DONE` over transient designs is the pre-fix lie (failure-patterns #31).

## Step 3 — Hunt skill bugs (this is the point)

After every wave interrogate **both** DBs, starting with `check_db_integrity.py --platform "$PLATFORM"`
(one PASS/WARN/ALARM line per invariant; codes name the lead: `H:*` honesty, `J1/J2/J4` journal +
linkage, `L1/L2/L3` per-move, `K3` per-platform stall). Each below is a *lead* → chase, don't paper over
(mechanisms in failure-patterns.md):

- **`fail` rows without a `failure_event`** (`H:every_fail_has_event`) — learner blind to a backend-fail class; `count(fail)` MUST equal the `orfs-fail-%`-event count.
- **A move in only ONE book** (`J2`/`L1`/`L2`/`J4`) — DBs disagree. `J2` (run + actions, zero back-filled `run_id`) is ALARM; the rest WARN (journal is best-effort).
- **Misclassified aborts** — diagnose the true reason from the stage log first (early synth abort filed `unseen_crash`; FLW-0024 die-too-small filed as place divergence).
- **sky130 `lvs=fail`** — check the *tool* first (KLayout-on-sky130 = 100% false-fail) and the match-then-writer-crash class; read the netgen **Final result** line, not intermediate "match uniquely" lines (2026-07-03).
- **asap7 `lvs=fail`** — must be `skipped` (no LVS deck); marking incomplete/fail on missing LVS is a misclassification.
- **sky130hs DRC `no_count_report` with `exit_code=0` across a wave** — a *phantom symptom generator*, infra absence not design failure (#32): ORFS's "DRC not supported" echo. Verify `run_drc.sh` resolved the sibling `sky130hd.lydrc` (a missing sibling deck WARNs loudly + keeps the loud no_count_report path, never a silent skip). Pre-fix waves burned `recheck_unparsed → catalog_exhausted` escalations on violations that never existed — requeue those, don't chase them.
- **sky130hs `lvs=fail` `top_pin_mismatch` with `mismatch_count=null` across the board** — the `.lyt` lefdef regression dropped ALL DEF geometry from the GDS (#33): `python3 tools/patch_sky130hs_lyt.py --check` (exit 2 = unpatched — re-patch, re-merge, re-LVS). The portless-SPICE guard must file `status:"error"`, never teach the learner a design `mismatch`.
- **Fabricated `clean` from STALE artifacts** (2026-06-30/07-01, worst mode) — `honesty.py` does NOT catch it. Guarded by mtime freshness → `stale` (fail-closed). Invariant: `SELECT COUNT(*) FROM runs WHERE drc_status='stale' OR lvs_status='stale'` MUST be 0; spot-check a clean's `6_drc_count.rpt`/`6_lvs.lvsdb` is NEWER than its `*_run.log`. On asap7, ANY `drc/lvs_status='clean'` is an ALARM by construction (MUST be 0).
- **Fabricated `clean` with NO reports — the LEDGER lies while both DBs stay green** (2026-07-02, bug #7). Run **every tick**: `tools/check_ledger_signoff_backed.py --platform "$PLATFORM"` (non-zero on any fabrication; buckets `backed`/`fabricated`=ALARM/`not_ingested`=WARN→`reconcile_sky130_campaign.py --apply`). Don't hand-roll the join (the old `LIKE '%basename'` cried wolf on ~197/593 + masked ~500 real gaps).
- **GHOST A/B arms** — `*_arm_incomplete` escalations for arm dirs a prior wipe removed (2026-07-03, bug #8). `ls design_cases/ | grep _ab` vs the ledger's `ab_arm` entries. Fixed: Tier-1 `isdir` filter + subject-less arms escalate `unvalidatable_insufficient_subjects`.
- **`route_relief` cleared route but DRC comes back `stuck`** — big-die scan pattern (die inflated past the deck's 7200s scan bound → honest `stuck`, not a fabrication/hang). Die-size-dependent.
- **Global `fail` drifts DOWN while `fe` parity holds** — benign (a re-ingest REPLACEs a run_id, flipping its own fail→pass; trajectory survives in fix_events). Only a parity BREAK (`fail != fe`) is the alarm.
- **`ab_trials` grows but `promoted` flat for `$PLATFORM`** — the 2026-06-24 "arms identical" alarm. Read the trial's `metrics_json.reason` (judge v2), then confirm arms diverged (`judged_on`/`is_success` per sample; a DRC/LVS arm is judged on ITS symptom clearing, not whole-run success).
- **Capped candidates re-planning after judge-v2 / `cand=` dropping at drain start** — EXPECTED (one fresh v2 round; `park_nondivergent` heals guaranteed-inconclusive rows to `parked`), not a runaway.
- **Same strategy re-applied on the same design across sessions** — dead-fix gate off/bypassed (`dead_here` after ≥`R2G_FIX_DEAD_AFTER`=2 terminal fails + 0 clears; A/B arms bypass by design).
- **`fail`/`partial` exist but `ab_trials` empty** — loop inert and lying; treat like an empty `heuristics.json`.
- **A DECISIVE `ab_trial` with `provenance_complete=false` drove a promotion** (P0-1, #48) — an unverifiable trial (missing/identical arm run_ids) must NEVER move `recipe_status`. `judge_recipe` now excludes explicit-`false` rows; a `win`/`loss` here must not be what a promotion rests on:
  ```bash
  sqlite3 "$KDB" "SELECT strategy, verdict, COUNT(*) FROM ab_trials
    WHERE verdict IN ('win','loss') AND json_extract(metrics_json,'\$.provenance_complete')=0
    GROUP BY 1,2;"   # these are ignored by the judge — a promotion must rest on provenance-complete wins
  ```
- **A learned recipe live-ranked with NO `recipe_status` row** (P0-2 fail-open, #48) — every concrete recipe key in heuristics.json MUST be rostered; `filter_promoted` now FAILS CLOSED on an absent row (pre-fix it fail-opened to `promoted`), so an unrostered key is silently dropped from live ranking. Coverage MUST be complete (0):
  ```bash
  python3 - "$KDB" r2g-skills/signoff-loop/knowledge/heuristics.json <<'PY'
  import sys, json, sqlite3
  sys.path.insert(0, 'r2g-skills/signoff-loop/knowledge')
  import recipe_lifecycle
  conn = sqlite3.connect(sys.argv[1]); heur = json.load(open(sys.argv[2]))
  miss = recipe_lifecycle.unrostered_keys(conn, heur)
  print('unrostered recipe keys:', len(miss), '(MUST be 0)'); print(miss[:5])
  PY
  ```
  If non-zero: a `learn()` enqueue crashed/partialed — re-run `learn_heuristics.py` off the committed db (idempotent `ensure_rostered` closes the gap).
- **A/B arms that inherited the subject's POST-repair `config.mk`** (P0-3, #48) — arms now strip the `# >>> r2g signoff-fix (auto) >>>` block at materialization and stamp `baseline_config_sha` on the ledger entry. A trial whose two arms share a treated baseline (the candidate recipe already applied in BOTH) ties `inconclusive`; spot-check a fresh arm's `constraints/config.mk` carries no auto-block.
- **Fmax `status='error'`** where a fallback was possible (null floorplan slack → post-place) — a bug, not honest `unconstrained`/`inconclusive`.
- **`antenna_nonconverged` terminal verdicts** — honest negative evidence, NOT a hang or a fixer bug (#36): after 2 non-improving antenna iterations the loop STOPS, `reports/antenna_nonconverged.json` persists {residual_count, strategies_tried}, later sessions auto-exclude the proven-futile strategies (ingested `no_change`). Retry only deliberately via `R2G_FIX_RETRY_NONCONVERGED=1` (e.g. after a toolchain update); the marker self-clears on a CLEAN check. The alarm is the OPPOSITE: the same antenna strategy re-burning full reflows across sessions means the marker isn't being written/read.
- **A fix's config edit that seemingly "didn't take"** — resume semantics (#35): config.mk is NOT a make prerequisite, so fix iterations now resume from the strategy's `rerun_from` with `make clean_<stage>` first (stage-scoped rebuild is the DEFAULT, downstream rebuilds via the odb chain). `R2G_FIX_FULL_REFLOW=1` only for an edit affecting a stage EARLIER than the declared `rerun_from`; `R2G_RESUME_NO_CLEAN=1` is pure crash-resume (unchanged config, e.g. finish-stage GDS resume) — never for applying an edit.
- **synth_only `fail` without a `synth-frontend-*` event** — `project_frontend_diagnosis.py --check "$KDB"`
  non-zero ⇒ the frontend learner is blind. honesty.py only sees the generic `orfs-fail-synth`; run the
  `--check` gate after every rtl-acquire classification/retry wave (it re-ingests touched projects).
- **A full-scope run that only reached synth flipped to `pass`** — must stay `partial` (`flow_scope='full'`
  keeps the full required-stage list; only `flow_scope='synth_only'` earns `pass` on synth alone). Either
  direction of this lie corrupts the A/B planner's subject pool.
- **`graph_skipped` rows counted as expansion success** — with `R2G_GRAPH_PYTHON` unset the graph stage
  SKIPs with a HINT; a corpus round claiming success over `graph_skipped` designs produced no graphs.

**When you find a real bug, fix it the project way** (`CLAUDE.md` → "When You Fix a Bug"): (1) append a
sub-section to failure-patterns.md/lessons-learned.md; (2) fix the offending `scripts/` file to
self-heal or HINT (**prefer editing existing scripts**); (3) add a **TDD test** (red→green, suite stays
green); (4) re-validate + **ingest** + re-run learn/mine; (5) reconcile only the **latest** row per
project (old `fail` + new `pass` coexist); (6) **commit** `feat(skill):`/`fix(skill):`.

## Step 4 — Prove the loop is CLOSED (evidence, not assertion)

Closed only when ALL hold — show the SQL/output for each:

- **Honesty 5/5** (global, never platform-scoped): `python3 r2g-skills/signoff-loop/knowledge/honesty.py --db r2g-skills/signoff-loop/knowledge/knowledge.sqlite`.
- **Synth-only parity** (only when `flow_scope='synth_only'` rows exist): `python3 r2g-skills/rtl-acquire/scripts/knowledge/project_frontend_diagnosis.py --check "$KDB"` exits 0.
- **Both DBs agree:** `python3 tools/check_db_integrity.py --platform "$PLATFORM"` exits 0. Explain any residual WARN (why it's not a live writer bug).
- **Every ledger-clean is signoff-backed** (the blind spot the DBs can't see): `python3 tools/check_ledger_signoff_backed.py --platform "$PLATFORM"` with **`fabricated == 0`**.
- **Recipe-lifecycle coverage (P0-2, #48):** `recipe_lifecycle.unrostered_keys(conn, heur)` is EMPTY — every learned recipe has a lifecycle row, so the fail-closed `filter_promoted` never silently drops a live recipe. And no promotion rests on a `provenance_complete=false` decisive trial (P0-1). (Recipe-lifecycle audit 2026-07-14; failure-patterns #48, Patterns 17-21.)
- **Failure learning:** `fix_events`/`fix_trajectories` captured attempts incl. `abandoned`/`failed`. A **loss** verdict is closure evidence too (the judge got real signal and withheld promotion).
- **Success learning + promotion:** ≥1 recipe `candidate → promoted` **on `$PLATFORM`**, backed by an `ab_trials` row whose arms diverged (v2 `metrics_json`: decisive `reason`, per-sample `judged_on` naming the recipe's symptom):

  ```bash
  sqlite3 "$KDB" "SELECT strategy, verdict, json_extract(metrics_json,'\$.reason'),
    json_extract(metrics_json,'\$.target.class') FROM ab_trials
    WHERE json_extract(metrics_json,'\$.judge_version')>=2 AND verdict IN ('win','loss')
    ORDER BY ts DESC LIMIT 10;"
  ```
- **Cross-design transfer:** a symptom-keyed recipe applies across designs/classes (evidence in `lessons`/`symptoms` or a class-spanning promotion).
- **Signoff + Fmax (per the contract):** the platform's honest terminal-state count grew this campaign — sky130hd/nangate45/… a genuine DRC+LVS clean (promotion backed by a real clean win, not a residual-floor tie); asap7 a GDS + DRC-ran-with-residual-`fail` (verify the asap7 `clean`-fabrication invariant is 0) + `lvs=skipped`. Fmax recorded (real GHz or honest `unconstrained`/`inconclusive`, never silent `error`).

Any miss **is** the next bug → loop to Step 3. Don't declare victory on machinery existing; the arms must
have **executed, diverged, and promoted**.

## Step 5 — Verify the RTL→Graph dataset conversion (topology · feature-stats · labels↔sign-off)

`run_graphs.sh` joins features (X) with labels (Y) into the five PyG views. Verify the conversion —
orthogonal to the sign-off loop (mission item 8). Contract, topologies, and the three verification
dimensions: `r2g-skills/def-graph/references/graph-dataset.md` ("Comprehensive verification", **read
first**). The pipeline is platform-sensitive (quoted liberty, PITCH direction, layer names, MACRO ids),
so verify on **both sky130 and nangate45** — a bug can hide on one.

**The five views are `HeteroData` by default** (2026-07-16; `R2G_GRAPH_KIND=homo` for the legacy flat
tensors, `both` for both). The verified block-positional homogeneous `Data` is still built first as the
source of truth (a value-preserving re-view — `graph_lib.homo_to_hetero`, exact inverse `hetero_to_homo`);
`verify_graph_dataset.py` reconstructs homo **independently** at load and runs the full check surface on it,
so hetero needs no separate oracle — a conversion bug fails a homo check. Don't "fix" a hetero build by
forcing `R2G_GRAPH_KIND=homo`; that hides the default the corpus ships.

**The signoff gate is now in-stage machinery, not operator convention** (2026-07-10, #34): every
def-graph stage runs the shared `signoff_gate.py` — required fail-closed (MISSING = blocked): drc ∈
{clean, clean_beol}, lvs ∈ {clean, skipped}, ORFS complete, route residuals 0 when provable; timing is
advisory. `run_graphs.sh` **enforces** by default, labels/features warn; `R2G_SIGNOFF_GATE=enforce|warn|off`
overrides (an explicit `R2G_DEF` override downgrades to warn, deliberately, recorded). The verdict is
written to `reports/signoff_gate.json` + the manifest's `signoff_health` — a dataset with unrecorded or
dirty provenance must FAIL the verifier. A gate-blocked build on a fail/partial design is CORRECT
behavior, not a Step-5 bug; produce the sign-off with Step 2 first. Since 2026-07-16 a gate-blocked
`run_graphs.sh` exits **7** (distinct, expected — treat it as "blocked, go sign off", not a crash) and
atomically stamps any prior green `dataset/graph_manifest.json` to `status="blocked_unsigned"` so a
stale dataset can never read as current; benign skips (no torch venv / no DEF) stay exit 0 and leave
an existing manifest alone.

**Prereq — the graph venv** (`torch + torch_geometric + pandas`; `run_graphs.sh` and the verifier both
**SKIP cleanly** without it, and a silent skip verifies NOTHING):

```bash
export R2G_GRAPH_PYTHON=/proj/workarea/user5/pyenvs/rtl2graph/bin/python                            # this machine
export OPENROAD_EXE=/proj/workarea/user5/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad  # for --signoff-recheck
"$R2G_GRAPH_PYTHON" -c "import torch, torch_geometric, pandas; print('graph venv OK')" \
  || echo "!! graph venv missing — Step 5 would SKIP and verify nothing"
```

### 5a — Run the ground-truth harness (primary evidence)

`tools/verify_graph_dataset.py` is the oracle — independent CSV re-derivation (separate pandas, **not**
`graph_lib`) + raw liberty/LEF/DEF/SPEF re-parse, in **three named check groups** (each proven to FAIL on a
deliberate corruption by `test_verify_comprehensive.py`) — plus a fourth **`hetero_checks`** group that runs
only when the dataset is heterogeneous (the default):

- **`topology_checks`** — all five views b–f: node/edge counts (d/e/f by the clique formula Σ C(k,2)),
  block-positional `node_name` order (pin block included), `edge_attr`==the folded entity (c=pin, d/e=gate/net,
  f=net), clock/reset + FILL/TAP excluded. On a **homo** dataset it also checks the `[fwd0,rev0,…]` fwd/rev
  interleaving on directed + `rc_edge_*` edges; on a **hetero** dataset that homo-layout guard is swapped for
  the hetero-native equivalent — per edge-store tensor-row alignment (index/attr/type/y sliced by one column
  tensor) + reverse-relation symmetry (`(a,rel,b)` has `(b,rel,a)` with equal count). A stale pre-RC `.pt`
  (`edge_y` width 5, no `rc_edge_*`) FAILs loudly, never IndexErrors.
- **`hetero_checks`** (hetero only) — per-view node types == the view's blocks, per-type tensor widths
  (`x` 9 = graph_id+8 feats, `y`/`y_raw` 5), edge relations reference only present node types, and the
  manifest's per-variant `hetero` node/edge-type breakdown matches the tensors. Negative controls on a
  corrupted hetero label / `edge_attr` fail loudly (`b.y1[gate]`, `c edge_attr`).
- **`feature_stat_checks`** — re-derives `placement_status_id`/`fanout`, bounds `num_layer`/`nearest_tap`,
  categorical vocab/enum coverage, and recomputes `features_stats.json`/`labels_stats.json` to catch a
  stale/hand-edited stats gate.
- **`signoff_report_checks`** — DRC/LVS clean-provenance gate (**fail-closed since #34**: a dataset with
  NO signoff reports and no recorded `signoff_gate.json`/`signoff_health` verdict FAILS — the pre-fix
  `isfile()` guard passed such designs vacuously), `ppa.json` geometry (`io_count` exact,
  macro/sequential; NOT the fill-inflated `instance_count`), the timing↔`6_final.sdc` transform
  (`Path_Delay==max(0,period−slack)`, `label==log1p`), `C_total`/`equiv_res` vs an independent SPEF re-parse.
  Opt-in `--signoff-recheck` re-runs PDNSim (`analyze_power_grid` on `6_final.odb`) to re-derive the IR-drop
  label — the one label whose tool report is deleted; honest SKIP (never a vacuous pass) without `OPENROAD_EXE`.

```bash
bash r2g-skills/def-graph/scripts/flow/run_graphs.sh design_cases/<design> "$PLATFORM"       # build (runs labels/features if stale)
"$R2G_GRAPH_PYTHON" tools/verify_graph_dataset.py design_cases/<design> [--signoff-recheck]   # verify one
"$R2G_GRAPH_PYTHON" tools/verify_graph_dataset.py --batch design_cases                        # sweep (non-zero on any fail)
```

A green `--batch` is the primary evidence (baselines: iir 167/167, DMA_Controller_DMA_fsm 164/164 sky130hd;
168/168 with `--signoff-recheck`). But **a verifier is only as good as its checks** — confirm it exits
non-zero on a real mismatch and its re-parsers don't re-implement the extractor's bug. Per-label correctness
is 5b below; the feature-side **silent-value defect checklist** (quoted-liberty units, driver
`max_capacitance`, MACRO id, `tracks_per_layer` numeric — each a shipped bug) lives in `failure-patterns.md`
"Dataset-Extraction Silent-Value Defects" + graph-dataset.md.

### 5b — Label (Y) correctness: the independent oracle per label

Each label is cross-checked against an **independent** re-derivation (never the extractor's code) — all in
`verify_graph_dataset.py`, each guarding a shipped silent-value defect:

- **Congestion** (`y1`) — radius-4 REFLECT gaussian recompute over each cell's orientation-aware bbox (all 3
  columns) from an independent DEF demand walk + LEF pitch; loud-FAIL if grid/layers/die missing. Guards the
  vertical-demand **transpose** (~79.7 % wrong) + retired radius-1 kernel.
- **Wirelength** (`y4`) — DEF route re-walk, **RECT patches stripped** → `label==log1p(µm)`; cross-checked vs
  OpenROAD `getLength`. Guards RECT-as-route-points (~100–400× inflation).
- **Timing slack** (`y3`) — exact SDC transform `Path_Delay==max(0, clk_period−Cell_Slack)`, `label==log1p`,
  off-path zero; every sequential instance covered. Guards the escaped-name join that dropped timing on
  bus-named registers.
- **IR drop** (`y2`) — canonical (not raw) CSV, `IR_Drop_mV`≤20 % supply, `label==log1p(IR/P95)` else 0;
  opt-in `--signoff-recheck` **re-runs PDNSim** to diff per-cell (the only value check — the raw dump is
  deleted; honest SKIP without `OPENROAD_EXE`). Guards all-NaN IR under a manifest `"ok"`.
- **RC** (`y5` + `rc_edge_*`) — vs an independent SPEF re-parse: ground/coupling `log1p` match + cross-net
  pair count, resistance intra-net, type↔column separation; bounds `equiv_res≤ΣR` (ohm↔kΩ unit bug) +
  `C_total∈[Σg+Σc, Σg+2Σc]`. Guards the SPEF↔DEF de-escaping join (79–92 % RC-label loss; ≥0.8 floor).

`verify_y` also matches each label's tensor slot to its CSV (NaN-safe); `label_health` + `labels_stats.json`
must flag a raw/all-NaN CSV `invalid`, never `ok`.

### 5c — Coverage: nangate45 + the synthetic guardrail (re-run on ANY extractor change)

`design_cases/` is currently sky130hd (built datasets `iir`, `DMA_Controller_DMA_fsm`). For **nangate45**,
drive the extractors against the reference DEF `/proj/workarea/user5/rtl2graph_verify/cordic_ng45_5_route.def`
(nangate45 libs exported: `TECH_LEF`/`SC_LEF`/`R2G_LIB_FILES`/`R2G_SC_LIB_FILES`/`R2G_PLATFORM=nangate45`;
truth in `rtl2graph_verify/truth_cordic_ng45_route.json`). The **synthetic guardrail** is nangate45-style,
always available, and drives the **real** workers → labels → PyG builder over a hand-computable fixture:

```bash
"$R2G_GRAPH_PYTHON" -m pytest -q r2g-skills/def-graph/tests/test_corner_case_pipeline.py \
  r2g-skills/def-graph/tests/test_corner_case_units.py r2g-skills/def-graph/tests/test_verify_comprehensive.py \
  r2g-skills/def-graph/tests/test_graph_stage.py r2g-skills/def-graph/tests/test_extract_congestion.py
```

A red suite = the conversion regressed OR a guardrail rotted. **Lesson (2026-07-07):** the congestion merge
changed the kernel without re-running this suite, leaving `test_corner_case_pipeline` RED on main (retired
radius-1 vs the scipy-matched radius-4 Gaussian).

### 5d — Staleness (regenerate after any extractor fix)

The `.pt` is keyed to the DEF mtime; **regenerate features AND labels AND graphs** — RC labels in
particular need a forced label rebuild (`rm reports/labels_stats.json`). A pre-RC dataset (`y`/`edge_y`
width 5, no `rc_edge_*`; e.g. DMA before its 2026-07-08 regen) is now caught by `topology_checks`, but
regenerate rather than trust it. Ingest is unaffected (a training artifact, never entering the memory DBs).

## Step 6 — Audit the rtl-acquire synth-only corpus supply line (when the corpus grew)

rtl-acquire is **UPSTREAM** of signoff: it acquires RTL at corpus scale and expands each candidate
**synth-only** (borrowed `run_orfs.sh` with `ORFS_STAGES=synth`, unique `FLOW_VARIANT` per candidate)
into a pre-layout `netlist_graph.pt` — **nangate45-scoped in v1** (`R2G_ACQUIRE_PLATFORM`). It never
runs PnR/signoff — **promotion is one click** (2026-07-10):
`scripts/promote/promote_candidates.py <design …>|--all [--require-publish-eligible] [--platform P] [--run]`
converts a synth-proven candidate (index `status==success`) into a ready-to-run **full-flow** project
under `design_cases/` (RTL vendored, config.mk carries the proven synth knobs + floorplan directive,
**WITHOUT** `R2G_FLOW_SCOPE=synth_only` — a promoted project's runs ingest full-scope;
`validate_config.py` is the readiness gate; verdict in `<project>/reports/promote.json`) and hands it to
the Step-2 campaign. Skip this step when no acquisition round ran and Step 0's `flow_scope='synth_only'`
counts are unchanged.

- **Round driver** (idempotent; cwd = `r2g-skills/rtl-acquire`):
  `python3 scripts/run_expansion_round.py --discover --run-retry` (loops: `scripts/run_until_empty.py`,
  `scripts/search_and_expand_until_target.py`). A round is successful only when publish gating + the
  merged-manifest refresh completed — read `workspace/runs/run_manifest_latest.json` +
  `quality/publish_validation.json`, never the exit banner.
- **Keep the definition-of-success ladder separate** (SKILL.md): execution / repair / validation /
  publish / **learning** — the learning rung is the round's runs landing in knowledge.sqlite with
  `flow_scope='synth_only'` and every synth-fail carrying a `synth-frontend-<class>` event.
- **Verify a published corpus** — the synth-only analog of Step 5. `tools/verify_graph_dataset.py` does
  NOT apply (netlist graphs are pre-layout: no DEF/SPEF/labels); the gates are:

  ```bash
  cd r2g-skills/rtl-acquire
  python3 scripts/validate/validate_publish_readiness.py   # → quality/publish_validation.json pass:true
      # per-design netlist_graph.pt + mapped_netlist.v exist, cell_stats.json cells>0 (the check that
      # REPLACED the retired 30pt mapping-coverage gate), graph_stat_drift + duplicate-leakage bounds
  python3 scripts/knowledge/project_frontend_diagnosis.py --check \
      ../signoff-loop/knowledge/knowledge.sqlite            # synth-only honesty parity, non-zero on lie
  ```
- **Frontend-repair honesty chain** (after a wave with synth fails): `repair/classify_failed_candidates.py`
  → `repair/auto_fix_failures.py` → `knowledge/project_frontend_diagnosis.py` (writes `diagnosis.json` +
  `fix_log.jsonl`: an `acquire_exclude` is deliberate abandonment = negative learning; a cleared `retry`
  is a win) → re-ingest → `--check`. A skipped rung here is the synth-side "never ingested" lie.
- **Risk flags screen, never hard-reject** (2026-07-10): RAM/CDC/hard-macro keyword hits ride the
  candidate `notes` column as `risk_flags=…` (tokenized + comment-stripped matching in
  `scripts/common/rtl_risk.py`; the old whole-text substring reject threw picorv32 away on its
  formal-only macro names) — **the synth attempt is the real arbiter**: a true hard-macro dependency
  fails with evidence and the repair-side classifier excludes it then. `--retry-excluded` re-emits
  candidates parked in `failed_candidates_exclude.csv` — a past failure must not permanently block a
  retry after a fix.
- **`graph_skipped` ≠ success** — without `R2G_GRAPH_PYTHON` every design records `graph_skipped`
  (honest HINT, by design). Provision the venv (eda-install `graph` tier) before judging a round.
- **Same cores rule:** `R2G_ACQUIRE_NUM_CORES` × concurrent synths ≤ free cores; per-candidate synth
  timeout `R2G_ACQUIRE_SYNTH_TIMEOUT` (default 3600s). The LLM patch path is OFF by default
  (`R2G_ACQUIRE_ENABLE_LLM=1` opt-in; OpenAI fallback additionally needs `OPENAI_API_KEY`).

## Step 7 — Record durable learnings

- Update `r2g-skills/signoff-loop/references/` (failure-patterns/lessons-learned) + any touched
  `docs/superpowers/{plans,specs}` with a **dated note (commit hash + superseded invariants)**. Keep
  CLAUDE.md's "no per-run results here" rule.
- Update the operator memory index (platform, promotions, bugs fixed, honesty state).
- Keep changes on a branch off `main`; commit per fix; **only push/PR when the user asks.**

## Looping this command

Idempotent + resumable ⇒ safe under `/loop` (defaults `PLATFORM=sky130hd`): each tick re-deploys the
skill, resumes the same `$LEDGER` (Step 1b is a no-op once built), runs the next waves, re-verifies
honesty, and re-runs Step 5 (`--batch` + corner suite are idempotent + staleness-aware) and Step 6
(skippable when `flow_scope='synth_only'` counts are unchanged; the round driver + `--check` gate are
idempotent). Retune via `pool.env`; keep `WORKERS × NUM_CORES ≤ free cores` every tick.

## Guardrails (hard rules — violating one corrupts the campaign or the host)

- Never run two configs with the same `DESIGN_NAME` + `FLOW_VARIANT` concurrently (keep project-dir basenames unique).
- Never set `PLACE_DENSITY_LB_ADDON` below `0.10` (placer divergence is irrecoverable).
- For >100K-cell designs, never run multiple LVS jobs concurrently (3–5 GB RAM each → 2–3× wall time; bites on sky130hd Netgen).
- `WORKERS × NUM_CORES ≤ free cores` — the default grabs `nproc` (96) per flow; N flows oversubscribe N×.
- **One platform per round** — don't mix platforms in one ledger or re-point config.mk for designs mid-flow on another platform; re-target only when the prior round is terminal.
- **Ingest after EVERY flow** — clean, failed, or partial.
- **Escalate to the user before** CDC, multi-clock, DFT, or signoff-quality closure (the loop never blocks on unknowns — they go to `escalations`).
- **Step 5 needs the graph venv** or it verifies nothing; building datasets is memory/CPU-heavy (counts against `WORKERS × NUM_CORES`). Never trust a `SKIP` as a pass.
- **rtl-acquire rounds share the same core budget** (`R2G_ACQUIRE_NUM_CORES` × concurrent synths counts against the host total) — never run a corpus round and campaign waves oversubscribed. v1 is nangate45-scoped: don't re-point `R2G_ACQUIRE_PLATFORM` mid-corpus.
