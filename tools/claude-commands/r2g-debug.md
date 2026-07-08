---
description: Drive an RTL→GDS sign-off campaign on an ORFS platform (default sky130hd — genuinely clean-able KLayout DRC + Netgen LVS + RCX; nangate45/asap7/gf180/ihp also work) in parallel waves, hunt r2g-skills bugs, and prove the engineer-learning-loop is closed (DRC/LVS clean where the deck allows + best Fmax + promoted recipes). Also independently VERIFIES the RTL→Graph dataset conversion across three dimensions — topology (5 PyG views b–f), feature statistics, and labels↔sign-off reports — against raw DEF/LEF/liberty/SPEF + OpenDB ground truth (opt-in PDNSim IR-drop re-run).
argument-hint: "[overrides, e.g. PLATFORM=sky130hd WAVE_MAX=24 WORKERS=3 NUM_CORES=4]"
---

# /r2g-debug — Drive, debug, and PROVE the r2g-skills learning loop (any ORFS platform)

Run a **real, parallel, wave-batched RTL→GDS sign-off campaign** over this project's RTL designs on a
chosen **ORFS platform**, and use it as the harness that surfaces skill bugs and proves the closed
learning loop. **Platform is the central knob** (`$ARGUMENTS`, default `sky130hd`); only the *signoff
success contract* and a few bug leads change per platform. sky130hd is primary (clean-able DRC/LVS, so
a clean win can **promote** a recipe); nangate45/asap7/gf180/ihp also work.

**Mission (one connected goal):** (1) run all designs through the `$PLATFORM` flow on the *freshly
symlink-deployed* skill; (2) batch into waves, parallel but not oversubscribed; (3) drive each design to
its platform's **honest terminal state** (per the contract below) + best Fmax; (4) **find skill bugs** —
a campaign that surfaces none is suspicious; (5) prove the loop **learns from both success and failure
trajectories** and **both DBs tell the same story** (`check_db_integrity.py`); (6) new/successful recipes
**promote** (`shadow→candidate→promoted`); (7) prove effectiveness with evidence, not claims; (8) verify
the **RTL→Graph dataset conversion** (Step 5) against raw DEF/LEF/liberty + OpenDB truth — an orthogonal
bug-hunt axis.

Apply any `KEY=value` from **$ARGUMENTS** as env overrides. Set the working vars once and reuse:

```bash
cd /proj/workarea/user5/agent-r2g
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
| sky130hs       | Yes (KLayout²) | Yes (Netgen)     | Yes | GDS + DRC clean + LVS clean + RCX |
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
```

Report in plain language: pending count **for `$PLATFORM`**, the `check_db_integrity` verdict + why, is
honesty internally consistent, is `promoted` growing **per-platform** or flat, does the journal keep step.
Knowledge is a **shared** store — scope "did THIS campaign improve things" to `platform='$PLATFORM'`.

## Step 1 — Deploy the NEWEST skill as a symlink (non-negotiable)

A stale deployed skill is the most expensive failure mode here (2026-06-08): the harness loads
`.claude/skills/signoff-loop/`, not the canonical tree; a `cp` goes silently stale. Force symlinks:

```bash
bash r2g-skills/install.sh --project . --link --force
readlink .claude/skills/signoff-loop   # MUST resolve to canonical r2g-skills/signoff-loop/
bash r2g-skills/signoff-loop/scripts/flow/check_env.sh   # the tools $PLATFORM needs MUST be green
```

A flow that aborts on a missing tool — **or silently *skips* DRC/LVS because its tool/PDK is unset** —
teaches the loop a lie. Fix the environment first (see the per-platform env note above).

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
`run_orfs.sh` would silently build the OLD one.

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
- **Fabricated `clean` from STALE artifacts** (2026-06-30/07-01, worst mode) — `honesty.py` does NOT catch it. Guarded by mtime freshness → `stale` (fail-closed). Invariant: `SELECT COUNT(*) FROM runs WHERE drc_status='stale' OR lvs_status='stale'` MUST be 0; spot-check a clean's `6_drc_count.rpt`/`6_lvs.lvsdb` is NEWER than its `*_run.log`. On asap7, ANY `drc/lvs_status='clean'` is an ALARM by construction (MUST be 0).
- **Fabricated `clean` with NO reports — the LEDGER lies while both DBs stay green** (2026-07-02, bug #7). Run **every tick**: `tools/check_ledger_signoff_backed.py --platform "$PLATFORM"` (non-zero on any fabrication; buckets `backed`/`fabricated`=ALARM/`not_ingested`=WARN→`reconcile_sky130_campaign.py --apply`). Don't hand-roll the join (the old `LIKE '%basename'` cried wolf on ~197/593 + masked ~500 real gaps).
- **GHOST A/B arms** — `*_arm_incomplete` escalations for arm dirs a prior wipe removed (2026-07-03, bug #8). `ls design_cases/ | grep _ab` vs the ledger's `ab_arm` entries. Fixed: Tier-1 `isdir` filter + subject-less arms escalate `unvalidatable_insufficient_subjects`.
- **`route_relief` cleared route but DRC comes back `stuck`** — big-die scan pattern (die inflated past the deck's 7200s scan bound → honest `stuck`, not a fabrication/hang). Die-size-dependent.
- **Global `fail` drifts DOWN while `fe` parity holds** — benign (a re-ingest REPLACEs a run_id, flipping its own fail→pass; trajectory survives in fix_events). Only a parity BREAK (`fail != fe`) is the alarm.
- **`ab_trials` grows but `promoted` flat for `$PLATFORM`** — the 2026-06-24 "arms identical" alarm. Read the trial's `metrics_json.reason` (judge v2), then confirm arms diverged (`judged_on`/`is_success` per sample; a DRC/LVS arm is judged on ITS symptom clearing, not whole-run success).
- **Capped candidates re-planning after judge-v2 / `cand=` dropping at drain start** — EXPECTED (one fresh v2 round; `park_nondivergent` heals guaranteed-inconclusive rows to `parked`), not a runaway.
- **Same strategy re-applied on the same design across sessions** — dead-fix gate off/bypassed (`dead_here` after ≥`R2G_FIX_DEAD_AFTER`=2 terminal fails + 0 clears; A/B arms bypass by design).
- **`fail`/`partial` exist but `ab_trials` empty** — loop inert and lying; treat like an empty `heuristics.json`.
- **Fmax `status='error'`** where a fallback was possible (null floorplan slack → post-place) — a bug, not honest `unconstrained`/`inconclusive`.

**When you find a real bug, fix it the project way** (`CLAUDE.md` → "When You Fix a Bug"): (1) append a
sub-section to failure-patterns.md/lessons-learned.md; (2) fix the offending `scripts/` file to
self-heal or HINT (**prefer editing existing scripts**); (3) add a **TDD test** (red→green, suite stays
green); (4) re-validate + **ingest** + re-run learn/mine; (5) reconcile only the **latest** row per
project (old `fail` + new `pass` coexist); (6) **commit** `feat(skill):`/`fix(skill):`.

## Step 4 — Prove the loop is CLOSED (evidence, not assertion)

Closed only when ALL hold — show the SQL/output for each:

- **Honesty 5/5** (global, never platform-scoped): `python3 r2g-skills/signoff-loop/knowledge/honesty.py --db r2g-skills/signoff-loop/knowledge/knowledge.sqlite`.
- **Both DBs agree:** `python3 tools/check_db_integrity.py --platform "$PLATFORM"` exits 0. Explain any residual WARN (why it's not a live writer bug).
- **Every ledger-clean is signoff-backed** (the blind spot the DBs can't see): `python3 tools/check_ledger_signoff_backed.py --platform "$PLATFORM"` with **`fabricated == 0`**.
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
deliberate corruption by `test_verify_comprehensive.py`):

- **`topology_checks`** — all five views b–f: node/edge counts (d/e/f by the clique formula Σ C(k,2)),
  block-positional `node_name` order (pin block included), the `[fwd0,rev0,…]` fwd/rev interleaving on
  directed + `rc_edge_*` edges, `edge_attr`==the folded entity (c=pin, d/e=gate/net, f=net), clock/reset +
  FILL/TAP excluded. A stale pre-RC `.pt` (`edge_y` width 5, no `rc_edge_*`) FAILs loudly, never IndexErrors.
- **`feature_stat_checks`** — re-derives `placement_status_id`/`fanout`, bounds `num_layer`/`nearest_tap`,
  categorical vocab/enum coverage, and recomputes `features_stats.json`/`labels_stats.json` to catch a
  stale/hand-edited stats gate.
- **`signoff_report_checks`** — DRC/LVS clean-provenance gate, `ppa.json` geometry (`io_count` exact,
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

## Step 6 — Record durable learnings

- Update `r2g-skills/signoff-loop/references/` (failure-patterns/lessons-learned) + any touched
  `docs/superpowers/{plans,specs}` with a **dated note (commit hash + superseded invariants)**. Keep
  CLAUDE.md's "no per-run results here" rule.
- Update the operator memory index (platform, promotions, bugs fixed, honesty state).
- Keep changes on a branch off `main`; commit per fix; **only push/PR when the user asks.**

## Looping this command

Idempotent + resumable ⇒ safe under `/loop` (defaults `PLATFORM=sky130hd`): each tick re-deploys the
skill, resumes the same `$LEDGER` (Step 1b is a no-op once built), runs the next waves, re-verifies
honesty, and re-runs Step 5 (`--batch` + corner suite are idempotent + staleness-aware). Retune via
`pool.env`; keep `WORKERS × NUM_CORES ≤ free cores` every tick.

## Guardrails (hard rules — violating one corrupts the campaign or the host)

- Never run two configs with the same `DESIGN_NAME` + `FLOW_VARIANT` concurrently (keep project-dir basenames unique).
- Never set `PLACE_DENSITY_LB_ADDON` below `0.10` (placer divergence is irrecoverable).
- For >100K-cell designs, never run multiple LVS jobs concurrently (3–5 GB RAM each → 2–3× wall time; bites on sky130hd Netgen).
- `WORKERS × NUM_CORES ≤ free cores` — the default grabs `nproc` (96) per flow; N flows oversubscribe N×.
- **One platform per round** — don't mix platforms in one ledger or re-point config.mk for designs mid-flow on another platform; re-target only when the prior round is terminal.
- **Ingest after EVERY flow** — clean, failed, or partial.
- **Escalate to the user before** CDC, multi-clock, DFT, or signoff-quality closure (the loop never blocks on unknowns — they go to `escalations`).
- **Step 5 needs the graph venv** or it verifies nothing; building datasets is memory/CPU-heavy (counts against `WORKERS × NUM_CORES`). Never trust a `SKIP` as a pass.
