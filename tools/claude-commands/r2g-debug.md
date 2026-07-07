---
description: Drive an RTL→GDS sign-off campaign on an ORFS platform (sky130hd by default — full, genuinely clean-able DRC (KLayout gate + optional Magic advisory) + LVS (Netgen) + RCX signoff; nangate45/asap7/gf180/ihp also supported) in parallel waves, hunt r2g-rtl2gds skill bugs, and prove the engineer-learning-loop is closed (DRC clean where the deck allows — sky130hd cleans via KLayout DRC + Netgen LVS; asap7 KLayout DRC is NOT clean-able and needs the un-installed Calibre deck — + best Fmax + promoted recipes). Also independently VERIFIES the RTL→Graph dataset conversion (5 PyG graph views b–f, techlib/LEF parser, feature + label extraction incl. the new congestion labels) against raw DEF/LEF/liberty + OpenDB ground truth on both sky130hd and nangate45.
argument-hint: "[overrides, e.g. PLATFORM=sky130hd WAVE_MAX=24 WORKERS=3 NUM_CORES=4]"
---

# /r2g-debug — Drive, debug, and PROVE the r2g-rtl2gds learning loop (any ORFS platform)

You are debugging the `r2g-rtl2gds` skill by running a **real, parallel, wave-batched
RTL→GDS sign-off campaign** over the RTL designs in this project on a chosen **ORFS
platform**, and using that campaign as the test harness that surfaces skill bugs and proves
the closed learning loop works.

**Platform is the central knob — pick it from `$ARGUMENTS`, default `sky130hd`.** The whole
command is platform-parameterized; only the *signoff success contract* and a few bug-hunt
leads change per platform (see "Per-platform signoff contract" below). sky130hd (SkyWater
130nm, with a genuinely clean-able KLayout DRC gate (+ opt-in Magic advisory) and Netgen LVS — so a
DRC/LVS-clean win can actually **promote** a recipe) is the primary target of this command;
nangate45 (the completed historical round), asap7, gf180, and ihp-sg13g2 also work. **asap7 is
deliberately NOT the default: its community KLayout DRC deck has an irreducible false-violation
floor and the authoritative Calibre deck is not installed on this machine, so asap7 DRC is not
clean-able — see the "asap7 arm specifics" note below.**

**Mission (do all of these — they are one connected goal, not a menu):**
1. Run the RTL designs in this project through the **`$PLATFORM` sign-off flow** using the
   *newest* version of the skill (the canonical `r2g-rtl2gds/` tree, freshly symlink-deployed).
2. **Batch the RTL designs into waves** and run them **in parallel to fully use the CPUs**
   (respecting the shared-host hard rule below — do not oversubscribe).
3. For every design: drive sign-off to the platform's **honest terminal state** (see the
   contract below — for the default **sky130hd** that is genuinely **DRC clean (KLayout) + LVS
   clean (Netgen) + RCX**; for the non-default asap7 arm under the KLayout deck it is instead
   **DRC run with its honest residual floor + RCX, LVS skipped**, because asap7 KLayout DRC is
   NOT clean-able), and **search for the best Fmax**.
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
8. **Verify the RTL→Graph dataset conversion is correct** (Step 5) — the skill's `run_graphs.sh`
   stage turns each completed backend run into training-ready PyG graphs (5 views b–f) by joining
   the feature (X) and label (Y) stages. This is a **second, orthogonal bug-hunt axis** to the
   sign-off loop: verify the topology conversion, the techlib/LEF parser, and the feature + label
   extraction (**especially the new congestion labels**) against **raw DEF/LEF/liberty + OpenDB
   ground truth** on **both sky130 and nangate45**. A conversion that reproduces its own CSVs is not
   verified — cross-check against the *tool truth*, not another pipeline artifact.

User-supplied overrides for this run (may be empty): **$ARGUMENTS**
Apply any `KEY=value` pairs above as environment overrides (`PLATFORM`, `LEDGER`, `WAVE_MAX`,
`WORKERS`, `NUM_CORES`). If empty, use the defaults below. Set the working variables once and
reuse them in every step:

```bash
cd /proj/workarea/user5/agent-r2g
PLATFORM=${PLATFORM:-sky130hd}                               # $ARGUMENTS may override
LEDGER=${LEDGER:-design_cases/_batch/${PLATFORM}_campaign.jsonl}
# NOTE: the original nangate45 round historically lives in design_cases/_batch/campaign.jsonl
# (892 designs, all terminal). To RESUME it, pass LEDGER=design_cases/_batch/campaign.jsonl.
# New rounds (incl. sky130hd, asap7) use <platform>_campaign.jsonl so each round's history stays immutable.
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

| Platform       | DRC            | LVS              | RCX | Honest terminal state means …                     |
|----------------|----------------|------------------|-----|---------------------------------------------------|
| **sky130hd** ★ | **Yes (KLayout²)**| **Yes (Netgen)** | Yes | **GDS + DRC clean + LVS clean + RCX** — the default; genuinely clean-able, so a clean win can promote |
| nangate45      | Yes (KLayout)  | Yes (KLayout)    | Yes | GDS + DRC clean + LVS clean + RCX                 |
| sky130hs       | Yes (KLayout²) | Yes (Netgen)     | Yes | GDS + DRC clean + LVS clean + RCX                 |
| gf180/ihp      | Yes (KLayout)  | Yes (KLayout)    | Yes | GDS + DRC clean + LVS clean + RCX                 |
| asap7          | Yes¹ (KLayout) | **No (skipped)** | Yes | GDS + **DRC run w/ honest residual floor (NOT clean-able)** + RCX; **`lvs=skipped` is honest-clean** |

¹ **asap7 KLayout DRC is NOT clean-able.** The community `asap7.lydrc` deck is a DRM
reverse-engineering with an *irreducible false-violation floor* (min ~8; e.g. traffic_control=25,
master_dma=119 — `V*.M*.AUX`, `LIG*`, `V0`, `M4.S.5` tech-LEF-vs-deck artifacts present even on
ORFS's own `gcd`). No flow lever clears it. So on asap7 the honest terminal DRC state is
**`fail` with a documented residual floor**, and **"no asap7 DRC-clean" / "no asap7 DRC promotion"
is HONEST platform truth, not a bug to chase.** Chasing asap7 to "DRC clean" is exactly what
spawned the 2026-06-30/07-01 fabricated-clean bug. See `references/failure-patterns.md`
"ASAP7 residual-DRC-by-design".

² **sky130 DRC gate = KLayout, NOT Magic (2026-07-02 finding, commits cd33f62+00351d8).** The loop
signs sky130 DRC off with the KLayout `sky130hd.lydrc` deck. A naive full-chip Magic
`gds read`+`drc catchup` reports thousands of `li.*`/`mcon.*` std-cell-internal/abutment artifacts
(~4777 on a KLayout-clean design) and would false-fail the whole corpus — so Magic full-chip DRC
must NEVER be the gate. It runs as an **advisory cross-check** when `R2G_MAGIC_ADVISORY=1`
(non-fatal, `R2G_MAGIC_ADVISORY_TIMEOUT` default 300s): `extract_drc` attaches
`magic_advisory{...authoritative:false}` and NEVER changes `status`. (Magic itself is still
REQUIRED on sky130 — Netgen LVS uses Magic to extract SPICE from the GDS.)

**sky130hd specifics (the default platform):**
- sky130hd signs off DRC with **KLayout** (`sky130hd.lydrc`; Magic advisory optional — footnote ²)
  and LVS with **Netgen** (`run_netgen_lvs.sh`, Magic-extract + netgen-compare) — both genuinely
  clean-able, so the honest terminal state is a *genuine* DRC-clean + LVS-clean, not a residual
  floor. A recipe that clears a real DRC/LVS violation can therefore actually **promote** — the
  whole reason sky130hd is the default.
- Required tools for sky130hd: `yosys`/`openroad`/ORFS + **KLayout** (DRC gate) + **magic** +
  **netgen-lvs** (LVS: Magic extracts SPICE, Netgen compares) +
  the **sky130A PDK**. On this machine magic/netgen live in `~/miniconda3/envs/eda` and sky130A is
  staged at `/proj/workarea/user5/sky130_pdk/share/pdk/sky130A`, both pinned in
  `references/env.local.sh` and green in `check_env.sh`. A red klayout/magic/netgen/PDK row **does**
  block sky130 signoff — fix the env first, or DRC/LVS will falsely *skip* and teach the loop a lie.
- **Wrong-LVS-tool guard (historical sky130 bug, 2026-06-17):** on sky130 LVS is **Netgen, NOT
  KLayout**. The dominant early blocker was the loop running KLayout LVS on sky130 → 12/12
  false-`fail`. `fix_signoff.sh` is now platform-aware and `extract_lvs` is most-recent-tool-wins;
  before believing any sky130 `lvs=fail`, confirm the *right tool* ran.
- sky130hd is 130nm; sizing is `CORE_UTILIZATION`-based (ORFS auto-sizes the die), so per-design
  configs port across platforms — absolute areas/periods differ from asap7/nangate45.

**asap7 arm specifics (non-default — kept for when the Calibre deck lands):**
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
- `r2g-rtl2gds/references/graph-dataset.md` — the RTL→Graph dataset stage (Step 5): the five
  topologies b–f, the shared tensor schema (`x[N,10]`/`y[N,5]`/`edge_attr`/`edge_y`), the
  feature/label join, and the ground-truth verification harness. Provenance + the 2026-07-05/06/07
  audit chain (every dataset-extraction defect found + fixed) is documented here and in
  `references/failure-patterns.md` → **"Dataset-Extraction Silent-Value Defects"**.
- `tools/verify_graph_dataset.py` — the RTL→Graph **ground-truth harness** (Step 5): independently
  re-derives every structural + label expectation from the CSVs (separate pandas code, not
  `graph_lib`) AND re-parses the raw liberty/LEF/DEF (never `techlib`) and diffs the shipped
  tensors. `--batch <root>` sweeps a corpus (exit non-zero on any failure). Its pure helpers are
  pinned by `tests/test_verify_graph_dataset_helpers.py`; the synthetic end-to-end guardrail is
  `tests/test_corner_case_pipeline.py` + `tests/test_corner_case_units.py` (fixture
  `tests/fixtures/corner_synth.py`).
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
# exist yet (a fresh platform round, e.g. sky130hd), Step 1b builds it — skip the status line.
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
     ||' parked='||(SELECT COUNT(*) FROM recipe_status WHERE status='parked')
     ||' promo='||(SELECT COUNT(*) FROM recipe_status WHERE status='promoted');"
# Judge-v2 (2026-07-04) inconclusive REASONS — an inconclusive corpus is queryable now.
# Dominant 'both_arms_never_succeed' on a strategy = its subjects never sign off at all
# (subject-quality lead); 'success_tie_cost_within_noise' = genuinely cost-neutral recipe.
sqlite3 "$KDB" "SELECT strategy, json_extract(metrics_json,'\$.reason') reason, COUNT(*)
  FROM ab_trials WHERE verdict='inconclusive'
  AND json_extract(metrics_json,'\$.judge_version')>=2 GROUP BY 1,2 ORDER BY 3 DESC LIMIT 12;"
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

`check_env.sh` lists every ORFS platform it found and the tool paths. For the default **sky130hd**
you need `yosys`/`openroad`/ORFS green **plus KLayout (the DRC gate — footnote ²), `magic` +
`netgen-lvs` (LVS: Magic extracts, Netgen compares), and the sky130A
PDK** green (`references/env.local.sh` pins them on this machine). For nangate45 you need its
KLayout LVS rule; for the non-default asap7 arm you instead need `KLAYOUT_CMD` green (KLayout drives
asap7 DRC) and magic/netgen being absent is fine (sky130-only). A flow that aborts on a missing
tool — **or silently *skips* DRC/LVS because its tool/PDK is unset** — teaches the loop a lie, so fix
the environment *before* running flows.

## Step 1b — Bootstrap the per-platform ledger (run all RTL designs on `$PLATFORM`)

The campaign runs over **all set-up RTL designs configured for `$PLATFORM`**. The honest source
of truth for "which designs are on platform P" is each project's own `constraints/config.mk`
(`run_orfs.sh` builds against config.mk's `PLATFORM`, never the ledger field) — so a new platform
round **re-points config.mk for the whole corpus, then enumerates it into a fresh ledger**.
Bootstrap **only when `$LEDGER` is absent** (a fresh round — e.g. there is no `sky130hd` ledger yet;
the prior rounds were nangate45 and asap7). If `$LEDGER` already exists, treat it as immutable history:
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
# SINGLE-INSTANCE GUARD (hard rule): NEVER launch a second driver — two drivers on one ledger
# race set_state appends and can run the same design concurrently (FLOW_VARIANT collision).
# Since 2026-07-04 the driver ALSO self-guards (per-ledger flock + pgrep net inside
# campaign_resume_waves.sh; SKIP_INSTANCE_GUARD=1 for debug) — but a PRE-lock legacy driver
# holds no flock, so keep this operator-side check as the belt to its braces.
# The pgrep is END-ANCHORED on the script name (an un-anchored -f false-matches YOUR OWN shell,
# whose cmdline contains the pattern). If a driver is alive: monitor it, retune via pool.env,
# and skip the launch — under /loop this makes every later tick a pure supervisor check-in.
pgrep -f 'campaign_resume_waves\.sh$' && echo "driver ALREADY RUNNING — do NOT relaunch" || {
  # Optional: pre-seed the live pool (re-sourced each wave):
  #   mkdir -p tools/_${PLATFORM}_resume_logs
  #   printf 'WORKERS=3\nNUM_CORES=4\nWAVE_MAX=24\n' > tools/_${PLATFORM}_resume_logs/pool.env
  PLATFORM="$PLATFORM" LEDGER="$LEDGER" WAVE_MAX=${WAVE_MAX:-24} WORKERS=${WORKERS:-3} NUM_CORES=${NUM_CORES:-4} \
    setsid bash tools/campaign_resume_waves.sh >/dev/null 2>&1 &
  echo "driver pgid: $!"   # record the PGID — to stop a wave campaign you must kill the GROUP
}
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
  - **sky130-specific lead (default):** on sky130 LVS is **Netgen**, not KLayout. An sky130 `lvs=fail`
    is a lead to check the *tool* first (wrong-tool KLayout-on-sky130 = 100% false-fail, 2026-06-17) and
    the *match-then-writer-crash* class (LVS matched with 0 mismatches, then the net2id writer crashed →
    should be `crash`/retry, not `fail`). Only a real connectivity mismatch is a genuine sky130 LVS fail.
    **Read the netgen report's FINAL verdict, not intermediate lines** (2026-07-03): a `.rpt` full of
    subcircuit-level "Netlists match uniquely." lines can still end `Final result: Netlists do not
    match.` — only the final-result line (what `extract_lvs` classifies, e.g. `netgen_topology`) is
    the verdict; the early "match uniquely" lines are per-cell passes, not a false-fail signal.
  - **asap7-arm lead (non-default):** `lvs` filed as `fail` when asap7 has **no LVS deck** → it must be
    `skipped` (the honest-clean state), not `fail`. An asap7 design marked `incomplete`/`fail` *only*
    because LVS didn't "pass" is a misclassification bug — fix the gate, don't chase a non-existent LVS clean.
- **Fabricated `clean` from STALE artifacts** (the 2026-06-30/07-01 bug — the single worst failure mode).
  The mechanism: an extractor read a LOCAL `drc/6_drc_count.rpt` / `lvs/6_lvs.lvsdb` that was OLDER than
  its own `drc_run.log` / `lvs_run.log` (a pre-copytree-fix A/B arm dir inherited a stale count, or the
  fresh result-copy was skipped), so a 25-violation run recorded `clean/0`. **`honesty.py` does NOT catch
  this** — its five gates check `fail↔event` parity, not whether a *clean* verdict is real. Now guarded:
  `extract_drc.py` / `extract_lvs.py` / `extract_calibre_drc.py` carry an mtime freshness guard → they
  emit `stale` (fail-closed, outside the `{clean,clean_beol,skipped}` whitelist) rather than a fabricated
  clean, and `run_drc.sh` purges stale local artifacts before `make drc`.
  - **On the default sky130hd a `clean` is EXPECTED and legitimate — it IS the goal — but it MUST be
    FRESH.** So the invariant here is not "clean ⇒ alarm" (that is the asap7 rule) but **`stale` must be
    0** and a fresh result-copy must back every clean: `sqlite3 "$KDB" "SELECT COUNT(*) FROM runs WHERE
    drc_status='stale' OR lvs_status='stale'"` MUST be 0, and spot-check that a `clean` design's
    `6_drc_count.rpt`/`6_lvs.lvsdb` is NEWER than its `drc_run.log`/`lvs_run.log`.
  - **On the non-default asap7 arm, ANY `drc_status='clean'` or `lvs_status='clean'` is an ALARM by
    construction** (asap7 DRC is not clean-able — footnote ¹ — and asap7 has no LVS deck): `sqlite3
    "$KDB" "SELECT COUNT(*) FROM runs WHERE platform='asap7' AND (drc_status='clean' OR
    lvs_status='clean')"` MUST be 0. A non-zero count means a stale-read slipped a fabrication in.
  See `references/failure-patterns.md` "Stale prior-platform signoff report".
- **Fabricated `clean` with NO reports at all — the LEDGER lies while both DBs stay green** (2026-07-02,
  bug #7 of the sky130 round). A fix path whose success criterion is weaker than the platform's clean
  contract (route_relief: "flow completes") marked designs ledger-`clean` with **no `drc.json`/`lvs.json`
  on disk**; knowledge honestly recorded empty statuses, so `honesty.py` AND `check_db_integrity` stayed
  green — no current gate cross-checks the LEDGER's clean against the signoff contract. **Run this
  cross-check every tick** (must return 0): for each ledger-clean non-`ab_arm` design, its latest
  knowledge row must have `drc_status`/`lvs_status ∈ {clean, clean_beol, skipped}`. Tell-tale in
  `reports/fix_log.jsonl`: a vacuous `before=0 after=0 verdict=cleared` route entry as the only signoff
  evidence. Fixed: a cleared route abort now falls THROUGH to real signoff — a route-fixed design's
  fix_log must show the route session AND fresh DRC/LVS sessions after it. See failure-patterns.md
  "Fabricated clean via cleared route abort".
- **GHOST A/B arms — `*_arm_incomplete` escalations for arm dirs that don't exist** (2026-07-03, bug #8).
  `plan_trial` Tier 1 (`run_violations` exhibitors) selected subjects whose project dirs a prior wipe
  removed (immutable `runs` history is correct; picking non-existent dirs as PHYSICAL subjects is not) —
  cheapest-first even ranked the tiny wiped clones FIRST, so ghost arms were ledger'd, flowed against
  nothing, escalated every drain, and **starved the candidate** (`ab_trials` flat for its symptom while
  arm escalations pile up). Check `ls design_cases/ | grep _ab` against the ledger's `ab_arm` entries.
  Fixed: Tier-1 `isdir` filter + plan_arms skips subject-less arms (logged). A candidate whose exhibitors
  are ALL gone now escalates `unvalidatable_insufficient_subjects` honestly. See failure-patterns.md
  "Ghost A/B arms".
- **`route_relief` cleared route but DRC comes back `stuck`** — the big-die scan pattern (2026-07-02,
  2-of-2 on this round). The utilization floor (8) clears a route timeout by inflating the die; the DRC
  deck then can't scan the huge, mostly-empty die inside its 7200s stage bound (`klayout_polygon_op_
  no_progress`, exit 124) → honest `stuck` residual, NOT a fabrication and NOT a hang to kill. Small
  designs relieved modestly DRC fine — it is die-size-dependent. Candidate future lever: intermediate
  utilization steps (12–20) before the floor so route clears AND the deck can scan.
- **Global `fail` count drifts DOWN while `fe` parity holds** — benign, do not chase (2026-07-03). A fix
  session that re-flows and re-ingests without a regenerated `ppa.json` REPLACEs the same `run_id`
  (documented ingest keying), flipping its own fail row to pass; the trajectory survives in
  `fix_events`/`fix_trajectories`. The ALARM is only a parity BREAK (`fail != fe`), never the drift.
- **`ab_trials` grows but `promoted` is flat for `$PLATFORM`** → the 2026-06-24 "arms are identical"
  alarm (subtler than empty `ab_trials`). Since judge v2 (2026-07-04) read the trial's
  `metrics_json.reason` FIRST — it names the cause (`both_arms_never_succeed` = subjects never sign
  off; `success_tie_cost_within_noise` = cost-neutral recipe; `arm_no_samples` = arms crashed) —
  then verify the arms genuinely diverged (`judged_on`/`is_success` per sample). A DRC/LVS signoff
  arm is judged on ITS OWN symptom clearing (`judged_on: "symptom:drc:<class>"`), NOT whole-run
  success; a trial without `judge_version: 2` predates that and its inconclusive proves nothing.
- **Capped candidates suddenly re-planning after the judge-v2 upgrade is EXPECTED, not a runaway**
  (2026-07-04): `_ab_coverage_gap` counts only v2 inconclusives, so candidates capped dead under the
  symptom-blind judge get exactly one fresh round of v2 trials. Similarly, `cand=` may DROP at the
  start of a drain — `park_nondivergent` heals guaranteed-inconclusive rows (e.g.
  `lvs_resolve_unknown`) to `parked`; that is the healer working, not lost work.
- **The same strategy re-applied on the same design across sessions** → the dead-fix gate is off or
  bypassed. `diagnose_signoff_fix` skips auto-applying a strategy with ≥`R2G_FIX_DEAD_AFTER` (=2)
  terminal failures and zero clears on that design+check (`dead_here`); check `R2G_FIX_RETRY_DEAD`
  is not exported and the plan's `--list` shows the `dead_here` annotation. A/B arms bypass the gate
  by design (`--rank-first`).
- **`fix_trajectories` outcome counts shift once after the first post-2026-07-04 `learn()`** —
  benign, do not chase: none-only episodes reclassify `abandoned → not_attempted` and legacy
  quoted-class signatures (`"'m3.2'"`) re-key into their normalized symptom buckets
  (`symptom.normalize_class` healing). A parity BREAK is still the only alarm.
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
- **Every ledger-clean is signoff-backed (the bug-#7 gate the DBs can't see):** this cross-check
  must report 0 — it catches a `clean` the LEDGER claims that knowledge can't back, which
  `honesty.py`/`check_db_integrity` structurally miss (they audit the two DBs, not the ledger):

  ```bash
  python3 - <<'EOF'
  import json, sqlite3
  last = {}
  for line in open('design_cases/_batch/sky130hd_campaign.jsonl'):   # $LEDGER
      d = json.loads(line)
      if d.get('design'): last.setdefault(d['design'], {}).update(d)
  con = sqlite3.connect('r2g-rtl2gds/knowledge/knowledge.sqlite')
  ok = lambda s: (s or '') in ('clean', 'clean_beol', 'skipped')
  bad = 0
  for name, row in last.items():
      if row.get('state') != 'clean' or row.get('kind') == 'ab_arm': continue
      pp = row.get('project_path') or f"design_cases/{name}"
      r = con.execute("SELECT drc_status, lvs_status FROM runs WHERE project_path LIKE ? "
                      "ORDER BY ingested_at DESC LIMIT 1", (f"%{pp.split('/')[-1]}",)).fetchone()
      if r and not (ok(r[0]) and ok(r[1])): bad += 1; print("FABRICATED-CLEAN:", name, r)
  print("not-signoff-backed cleans:", bad)   # MUST be 0
  EOF
  ```
- **Failure learning:** `fix_events`/`fix_trajectories` captured fix attempts — including
  `abandoned`/`failed` ones (negative learning), not just successes.
  A **loss** verdict is closure evidence too: a recipe arm doing WORSE than control (e.g.
  core_util_relief 2026-07-03, losses across 4 class keys) proves the judge gets real signal and
  correctly withholds promotion — "promoted stays flat" is only an alarm when trials are NOISE, not
  when they are honest losses.
- **Success learning + promotion:** at least one recipe transitioned `candidate → promoted`
  **on `$PLATFORM` (per-platform `promo` for `$PLATFORM` grew)**, backed by an `ab_trials` row whose
  arms genuinely diverged (arm A control loses / arm B forced-recipe wins). Under judge v2 show the
  divergence directly — the winning trial's `metrics_json` must carry `judge_version: 2`, a decisive
  `reason` (`success_lcb_delta`/`cost_tiebreak`), and per-sample `judged_on` naming the recipe's own
  symptom (`symptom:drc:<class>` for a DRC recipe), e.g.:

  ```bash
  sqlite3 "$KDB" "SELECT strategy, verdict, json_extract(metrics_json,'\$.reason'),
    json_extract(metrics_json,'\$.target.class') FROM ab_trials
    WHERE json_extract(metrics_json,'\$.judge_version')>=2 AND verdict IN ('win','loss')
    ORDER BY ts DESC LIMIT 10;"
  ```
- **Cross-design transfer:** a recipe learned on one design/class applies to another (symptom-keyed,
  not family-named) — evidence in `lessons`/`symptoms` or a promotion spanning classes.
- **Signoff + Fmax (per the platform's contract above):** the platform's honest terminal-state count
  grew this campaign. For the default **sky130hd** (and nangate45/sky130hs/gf180/ihp) that is a genuine
  **DRC-clean + LVS-clean (+ RCX)** — the count of designs reaching that state MUST grow, and a promoted
  recipe should be backed by a real DRC/LVS-clean win (not a residual-floor tie). For the non-default
  **asap7** arm it is instead **GDS reached + DRC ran and recorded its residual floor as `fail` (NEVER a
  fabricated `clean` — verify the asap7 fabrication invariant is 0) + RCX + `lvs=skipped`**; do NOT
  require asap7 DRC-clean (needs the Calibre deck — if installed, run `run_calibre_drc.sh` and prove via
  `engine:calibre`). And Fmax is recorded (realistic GHz or an honest `unconstrained`/`inconclusive`,
  never a silent `error`).

If any of these fail, that failure **is** the next bug to fix — loop back to Step 3. Do not declare
victory on the strength of machinery existing; the A/B arms must have *executed, diverged, and
promoted*.

## Step 5 — Verify the RTL→Graph dataset conversion (topology · techlib · features · labels · congestion)

The skill's `run_graphs.sh` stage (`SKILL.md` step 13d) turns each **completed backend run** into
training-ready **PyG graphs** by joining the feature stage (X, `run_features.sh`) with the label
stage (Y, `run_labels.sh`). This step **verifies that conversion is correct** — a bug-hunt axis
**orthogonal** to the sign-off loop above, and a required part of the mission (item 8). The contract
and the five topologies live in `references/graph-dataset.md`; **read it first**. Verify on **BOTH
sky130 and nangate45** — the pipeline is platform-sensitive (quoted liberty, `PITCH` direction,
layer names, tap patterns, MACRO ids), so a bug can hide on one platform while the other stays green.

**Prereq — the graph venv (non-negotiable, or you verify NOTHING).** The stage needs
`torch + torch_geometric + pandas`. `run_graphs.sh` and `verify_graph_dataset.py` **SKIP cleanly**
without them — a skip is honest but means **zero** graph verification, so a silent skip here is the
Step-5 analogue of a silently-skipped DRC. Point `R2G_GRAPH_PYTHON` at the venv and confirm it imports:

```bash
export R2G_GRAPH_PYTHON=/proj/workarea/user5/pyenvs/rtl2graph/bin/python   # this machine
"$R2G_GRAPH_PYTHON" -c "import torch, torch_geometric, pandas; print('graph venv OK')" \
  || echo "!! graph venv missing — Step 5 would SKIP and verify nothing (install per graph-dataset.md)"
```

### 5a — Build + run the ground-truth harness (the primary evidence)

`tools/verify_graph_dataset.py` is the oracle: it independently re-derives **every** structural +
label expectation from the CSVs (separate pandas code, **not** `graph_lib`) — node counts, b/c edge
counts by row accounting, **d/e/f edge counts by the clique formula Σ C(k,2)**, `edge_attr` == the
folded entity's features, exact expected-NaN counts per y slot, `node_name` order, global_feat — AND
re-parses the **raw liberty/LEF/DEF** (never `techlib`) to check gate area/leakage/x/y/orientation,
`cell_type_id` injectivity + the shared MACRO id, `sum_pin_cap_fF` vs Σ liberty load caps, net
driver/sink/`connects_macro_flag`, wirelength vs an independent DEF route walk, timing coverage of
every sequential instance, and a **full independent congestion demand/capacity/gaussian recompute**.

```bash
# Build the dataset for a completed $PLATFORM design (runs 13b/13c first if stale).
bash r2g-rtl2gds/scripts/flow/run_graphs.sh design_cases/<design> "$PLATFORM"
# Ground-truth verify a single case:
"$R2G_GRAPH_PYTHON" tools/verify_graph_dataset.py design_cases/<design>
# Sweep a whole corpus root — exits NON-ZERO on ANY design's failure (run after any regen):
"$R2G_GRAPH_PYTHON" tools/verify_graph_dataset.py --batch design_cases
```

A green `--batch` over N designs is the primary evidence the conversion is correct. **A verifier that
passes is only as trustworthy as its own checks** — before believing green, confirm `--batch` actually
exits non-zero on a real mismatch (no vacuous "skip when a column is absent" paths) and that its
independent re-parsers don't re-implement the same bug (Step-5's version of "the loop is only as honest
as its weakest writer").

### 5b — Both platforms (the user requirement)

`design_cases/` is currently **100% sky130hd**. For **nangate45** coverage either (a) run a nangate45
fixture through ORFS to produce a real `6_final.def`, or (b) drive the extractors directly against the
routed reference DEF `/proj/workarea/user5/rtl2graph_verify/cordic_ng45_5_route.def` with the nangate45
libs exported (`TECH_LEF`/`SC_LEF`/`R2G_LIB_FILES`/`R2G_SC_LIB_FILES`/`R2G_PLATFORM=nangate45`; truth in
`rtl2graph_verify/truth_cordic_ng45_route.json`). The **synthetic corner-case suite** (5c) is a
nangate45-style design and is **always** available even with no backend run.

### 5c — The synthetic guardrail (always runnable; a merge that changes an extractor MUST re-run it)

```bash
"$R2G_GRAPH_PYTHON" -m pytest -q \
  r2g-rtl2gds/tests/test_corner_case_pipeline.py r2g-rtl2gds/tests/test_corner_case_units.py \
  r2g-rtl2gds/tests/test_graph_stage.py r2g-rtl2gds/tests/test_extract_congestion.py
```

These drive the **real** feature workers → label extractors → PyG builder over a hand-computable
fixture (`fixtures/corner_synth.py`) and assert every stage against hand-derived truth **across all
five views b–f**. A **red corner suite = either the conversion regressed OR a guardrail rotted** —
both are bugs. **Lesson (2026-07-07):** the congestion-method merge (`c9b9e3a`) changed the label
kernel but did **not** re-run this suite, leaving `test_corner_case_pipeline` **RED on main** (it baked
in the *retired* radius-1 3×3 kernel's "a fill cell far from wires reads exactly 0" locality; the new
scipy-matched **radius-4** Gaussian correctly spreads congestion up to 4 GCells). Fixed by asserting on
`label_raw` (raw → exactly 0 for an empty GCell) vs `cell_congestion` (smoothed → small-but-nonzero).
See `references/failure-patterns.md` → **"Congestion 2-vector method (radius-4 Gaussian)"**.

### 5d — What must be TRUE per dimension (each lead maps to a real historical defect — chase, don't paper over)

- **Topology (5 views b–f)** — node/edge counts match the clique formulas; `edge_attr` carries the
  **folded** entity's features (c=pin, d/f=net, e=gate **and** net) **aligned** with `edge_index`
  (interleaved fwd/rev — audit #5); clock/reset nets + FILL/TAP cells excluded (**`net_type_id==0`
  only** — the clock tree is NOT in the graph); undirected symmetry; `node_name` uniqueness.
- **Techlib LEF/liberty parser** — sky130 **QUOTED** liberty attributes (`direction`/`clock`, cap unit
  `"pf"`→fF — #5) parse; `bus()`/`bundle()` macro pins resolve to per-bit connects (#11);
  `is_sequential` covers `ff_bank`/`latch_bank`/`statetable`; `PITCH` direction correct (sky130 `li1`
  has two-value `PITCH 0.46 0.34`, VERTICAL → pick the x-pitch); the **nangate45 curated cell map is
  RETIRED** — a runtime liberty-derived map + a shared `MACRO` id, and `UNKNOWN` never silently
  swallows a live master (#12).
- **Feature (X)** — `cell_type_id`/area/power/x/y/orient/status; net `num_drivers`/`num_sinks`
  (chip-perspective: an INPUT port *drives*), `connects_macro_flag`, `num_layer` (the per-platform
  routing-layer regex), `hpwl_um`; `sum_pin_cap_fF` **excludes** an output pin's `max_capacitance`
  (drive limit ≠ load); `tracks_per_layer` is **numeric**, not a string.
- **Label (Y)** — wirelength **strips `RECT` patches** (sky130 — #10) and `label == log1p(um)` vs
  OpenROAD `getLength`; timing covers **every** sequential instance (the register-losing-join class);
  irdrop `y2` not silently all-NaN under a manifest `"ok"` (#6).
- **Congestion (the NEW script `extract_congestion.py` — the user's headline)** — a faithful port of
  `RTL2Graph/label_test/py/Congestion_Parse.py`: `label = mean(sqrt(gaussian_util))` (== ref
  `node_label[1]`), `label_raw = mean(sqrt(util))` (== `node_label[0]`), `cell_congestion =
  mean(gaussian_util)`, each averaged over the cell's **orientation-aware bbox** GCells (origin-GCell
  fallback when no cell SIZE); **VERTICAL demand keys `(x_gcell,y_gcell)`, NOT the mirror** (#7
  transpose); the pure-python `gaussian_filter_2d` **bit-matches** scipy's radius-4 (`sigma=1.0`,
  `truncate=4.0`) reflect convolution; capacity uses the **per-direction** pitch. Cross-check vs the
  reference (needs scipy) **< 1e-6**, or — if no scipy env — vs the **pre-gaussian `util` grid** (needs
  no scipy) plus a hand-check of the gaussian on a tiny asymmetric grid. `graph_lib` gate `y1` reads
  the **`label`** column (the smoothed sqrt); confirm no consumer swaps `label`/`label_raw`.
- **Verifier correctness** — `verify_graph_dataset.py` is the oracle, so audit **it**: does its
  congestion recompute match the **current** (bbox-averaged, radius-4) method and column set
  (`label`/`label_raw`/`cell_congestion`)? A stale verifier = false green.

### 5e — Staleness (the .pt is only as fresh as the DEF that made it)

The dataset `.pt` is keyed to the DEF's mtime; **regenerate features AND labels AND graphs after any
extractor fix.** The `design_cases/aes_core` dataset shipped on disk **predates** the 2026-07-06
congestion method and the `label_health` field (its manifest shows `label_health: null`), so its `y1`
is **stale** — rebuild before trusting it. Ingest is unaffected (the graph dataset is a training
artifact, not a sign-off verdict, so it does not enter the two memory DBs or the honesty gates).

## Step 6 — Record durable learnings (don't let the session evaporate)

- Update `r2g-rtl2gds/references/` (failure-patterns / lessons-learned) and any
  `docs/superpowers/{plans,specs}` touched, with a **dated note (commit hash + superseded
  invariants)** — not just code+tests. Keep `CLAUDE.md`'s "no per-run results here" rule.
- Update the operator memory index for this campaign's outcome (platform, promotions gained, bugs
  fixed, honesty state) so the next session resumes from truth.
- Keep all changes on a branch off `main`; commit per fix; **only push/PR when the user asks.**

## Looping this command

This command is **idempotent and resumable**, so it is safe under `/loop` (e.g. `/loop /r2g-debug`,
which defaults to `PLATFORM=sky130hd`; pass `PLATFORM=…` to drive a different arm): each tick
re-deploys the skill, picks up the same per-platform `$LEDGER` where it left off (Step 1b is a no-op
once the ledger has designs), runs the next waves, re-verifies the honesty invariants, and
re-runs the **Step 5 RTL→Graph verification** (`verify_graph_dataset.py --batch` + the corner suite
are idempotent and staleness-aware, so they refresh whatever a new backend RUN invalidated). Use a
per-platform `pool.env` to retune the pool between ticks without restart. Keep
`WORKERS × NUM_CORES ≤ free cores` on every tick.

## Guardrails (hard rules — violating one corrupts the campaign or the host)

- Never run two configs with the same `DESIGN_NAME` + `FLOW_VARIANT` concurrently (the driver derives
  `FLOW_VARIANT` from the project-dir basename — keep names unique).
- Never set `PLACE_DENSITY_LB_ADDON` below `0.10` (placer divergence is irrecoverable).
- For >100K-cell designs, never run multiple LVS jobs concurrently (3–5 GB RAM each → 2–3× wall time).
  (This BITES on the default sky130hd — Netgen LVS is real; on the asap7 arm LVS is skipped, but
  DRC/extraction on large designs still wants headroom either way.)
- `WORKERS × NUM_CORES ≤ free cores` — the default grabs `nproc` (96) per flow; N flows oversubscribe N×.
- **One platform per round.** Don't mix platforms in one ledger or re-point config.mk for designs that
  are mid-flow on another platform — `run_orfs.sh` builds against config.mk's PLATFORM. Re-target only
  when the prior round is terminal (Step 1b overwrites config.mk).
- **Ingest after EVERY flow** — clean, failed, or partial. A failed run never ingested teaches nothing.
- **Escalate to the user before** attempting CDC, multi-clock, DFT, or signoff-quality closure —
  the loop NEVER blocks on unknowns; they go to the `escalations` queue.
- **Step 5 (RTL→Graph) needs the graph venv** (`R2G_GRAPH_PYTHON` → torch+torch_geometric+pandas) or
  it verifies nothing. Building datasets is memory/CPU-heavy (the `.pt` for a large design can be
  hundreds of MB and re-derivation is super-linear), so it counts against `WORKERS × NUM_CORES ≤ free
  cores` too — don't build many large datasets concurrently. Never trust a `SKIP` line as a pass.
