# Changelog

Notable changes to the `r2g-skills` collection. Earlier history lives in the
git log (the commit messages are the long-term record — see CLAUDE.md "When
You Fix a Bug").

## 2026-07-24 — three-platform revalidation remediation (signoff-loop, def-graph, eda-install)

Closed the 2026-07-24 revalidation findings (pilot at agent commit 8d449b0 — nangate45 40/49,
sky130hd 45/49, sky130hs 44/49 Gate cells; `docs/superpowers/plans/2026-07-24-three-platform-*.md`,
failure-patterns #55). Real design/tool limits (sky130hd GCD `m3.2`, sky130hs SHA-256
non-closure, nangate45 full-DRC scale) are preserved as measured results, never "fixed" by
weakening decks, budgets, or gates.

- **RMD2-P0-01 bounded checker termination** — new `_bounded_run.sh` (`r2g_bounded_run`): each
  checker runs in its own session/process group, logs directly to the run-local file (no
  `timeout | tee` pipe whose reader can outlive the run), gets TERM→grace→KILL delivered to the
  WHOLE group at expiry, and no session survivor may exist before the verdict is written. The
  pilot's Nangate45 SHA-256 DRC had orphaned KLayout at `PPID=1` (GNU timeout supervised the
  non-exec ORFS wrapper) and froze the campaign behind the tee pipe until an operator SIGKILL.
  Migrated: `run_drc.sh` (KLayout invoked directly; stuck-rule diagnosis + exit 124 preserved;
  verdicts also record `cell_count` + `wall_s` for RMD2-LIM-01 scale-stratified throughput),
  `run_lvs.sh` (crash-retry loop; the pattern-scoped pkill reaper is retired — the supervisor's
  unconditional session reap covers the 2026-06-03 klayout-leak case), `run_netgen_lvs.sh`
  (Magic extraction — the cd-into-scratch happens inside the session via `bash -c … exec` —
  plus the Netgen compare and the OpenROAD powered-netlist write), and the advisory
  `run_magic_drc.sh` (whose count parse also no longer aborts pre-JSON on a countless log).
  Grace knobs: `DRC_KILL_GRACE` / `LVS_KILL_GRACE` / `NETGEN_KILL_GRACE` / `MAGIC_KILL_GRACE`.
- **RMD2-P0-02 digest-complete resume lineage** — ONE versioned stage→artifact contract
  (`stage_artifacts.py`, v2; the old inline map fingerprinted the nonexistent `1_synth.v`, so
  every repair run recorded `synth.sha256=null` and the gate accepted it). `run_orfs.sh` appends
  a per-stage `stage_artifact_manifest.jsonl` row (canonical path/size/sha256, identity,
  toolchain) after every successful stage; BEFORE a `FROM_STAGE` resume mutates anything,
  `resume_lineage.py verify` hashes each reused workspace artifact and STOPS the resume (exit 4)
  unless the bytes match a recorded parent digest (legacy parents degrade LOUDLY to
  `legacy_stage_log`/unverified — never silently to "newest clean sibling";
  `R2G_RESUME_LINEAGE_ENFORCE=0` is a recorded operator override). `signoff_gate.py`
  independently re-verifies every recorded entry — valid sha256, canonical artifact name,
  same-design/platform/variant parent with a matching manifest digest, acyclic chain, preserved
  bytes re-hashed — and any failure hard-blocks graph generation; the verdict's
  `lineage_root_digest` rides `signoff_health` into every graph manifest. def-graph carries a
  sync-tested fallback copy of the contract.
- **RMD2-P1-01 fail-closed strict-platform install** — platforms selected via
  `bootstrap.sh --strict-platforms` / `R2G_STRICT_PLATFORMS` / `--platforms` are FATAL on a
  missing or failing rule installer or an unverifiable canary, and are gated post-install by
  `platform_capability.py --strict` with the resolved env; the capability verdict + per-platform
  collateral sha256 digests land in `eda-install/references/install_manifest.json`; unselected
  platforms keep best-effort behavior; repeated installation is idempotent.

## 2026-07-22 — three-platform pilot remediation (signoff-loop, def-graph, eda-install)

Closed the four P0 defects of the 2026-07-22 three-platform pilot (nangate45 / sky130hd /
sky130hs, agent commit 2025737; `docs/superpowers/plans/2026-07-22-three-platform-*.md`,
failure-patterns #54). Two were residuals of #53 guards that detected post-hoc without removing
the cause.

- **RMD-P0-01 frozen-layout DRC** — `run_drc.sh` is checker-only: KLayout invoked directly on the
  preserved backend GDS (no `make drc`; plain `timeout`, closing this file's leftover #40
  `setsid`); restage stamps non-stage results (incl. `clock_period.txt`, a YOSYS dependency)
  OLDER than stage 1 and numbered logs at their stage-result epoch; `run_lvs.sh` gains a
  fail-closed `make --question` preflight (`physical_rebuild_required`) + a full artifact digest
  set replacing the single-GDS guard.
- **RMD-P0-02 strong signoff provenance** — one shared backend-run resolver (`_backend_run.sh`)
  for restage/DRC/LVS/netgen/RCX writes `backend/.r2g_signoff_run` (run tag + GDS/DEF sha256)
  where `report_io` actually reads it (the old marker glob was a dead path — 12/12 pilot reports
  `source=latest_run`); checkers record the exact layout digest graded; extractors carry it (the
  netgen + skip LVS paths previously stamped NO provenance) and accept `--run-dir`; the def-graph
  gate's new `artifact_digest` check hard-blocks digest mismatches and unreadable records, and
  digestless legacy evidence can never be a strict `pass`.
- **RMD-P0-03 fail-closed platform readiness** — `check_env.sh --platform <p>` /
  `R2G_TARGET_PLATFORM` makes the named campaign platform's strict capability REQUIRED;
  `platform_capability.py` reports tiers (`installed`/`research_ready`/`strict_signoff_ready`).
- **RMD-P0-04 sky130hs GDS import postcondition** — capability requires the modern `.lyt`
  (patched-options check), and `install_platform_rules.sh` verifies `patch_sky130hs_lyt.py
  --check` + the new `tools/sky130hs_gds_canary.py` (synthetic DEF import must land routing /
  pin / special / via geometry on canonical sky130A layer numbers — the legacy failure mode is
  wrong numbers, not missing shapes) and FAILS setup on a broken repair.

Out of repo scope: RMD-P1-01 (pilot grader registry identity — external harness), RMD-P1-02
(nangate45 strict-support decision — V1 owner), RMD-P1-03 (sky130hd GCD `m3.2` design case —
lives in the pilot workspace).

## 2026-07-20 — the identity chain (all three skills)

Closed the ten claims the 2026-07-19 post-consolidation audit left parked as "architectural"
(failure-patterns.md #52 continued), plus the P0-R3 operator ruling. They shared one root:
**identity was inferred from mutable paths, filenames, timestamps and file presence instead of
carried**. Each fix is a call site of a small shared recorder rather than another bespoke guard.
Tests 1485 → 1534 (signoff-loop 969, def-graph 461, rtl-acquire 104); honesty gates green;
`check_db_integrity` unchanged (`0 alarm, 1 warn, 15 pass`); committed `knowledge.sqlite` and
`heuristics.json` unchanged.

- **def-graph** — new `scripts/flow/_stage_provenance.py` stamps the DEF's sha256 + run tag +
  X/Y schema version into each stage marker, so `run_graphs.sh` reuses features/labels only on a
  content match (P0-R8; mtimes never were identity). `build_graphs.py` builds into a staging
  generation and publishes only when every view exists, so a failed rebuild leaves the previous
  generation byte-identical (P0-R9). Every manifest now declares `graph_schema_version` +
  `generation_id`, and `verify_graph_dataset.py` rejects an unversioned generation with a rebuild
  hint (P0-N7). `signoff_gate` gained `report_binding`: a DRC/LVS report naming a different backend
  run now BLOCKS, while unattributed reports stay a caveat and single-run projects stay clean
  (P0-R7).
- **rtl-acquire** — expansion freezes a typed `compile_manifest` (top params, defines, frontend,
  include order, transitive header closure, `config_digest`); promotion reads that instead of
  re-parsing the mutable synth `config.mk`, and blocks on drift (P0-N2). The header closure is
  vendored into the promoted project, which no longer inherits external include dirs (P0-R5).
  `resolve_candidate_rtl` falls back to the corpus's own vendored `rtl/` by longest path tail —
  **708/708 local candidates now resolve** where 0 did — and EACCES became a structured failure
  instead of aborting an entire `--all` run (P1-N6).
- **signoff-loop** — a no-PPA ingest now derives run identity from the backend RUN tag + stage
  ledger digest instead of a constant, so distinct attempts stop overwriting each other (P1-R1;
  the with-ppa derivation is byte-identical, test-pinned). `build_diagnosis` lets a terminal-clean
  stage ledger veto a superseded intermediate timing message (P1-R3). Per the 2026-07-20 operator
  ruling, untraceable legacy A/B evidence is **quarantined forward only** — it can no longer drive
  a promotion, but nothing is demoted: re-judging all 114 real trial keys changed 0 statuses and
  left promoted at 25 (P0-R3).

Two deliberate, test-pinned contract changes: `promote_candidates` returns `rtl_files_unresolved`
rather than a generic `failed` for unreachable RTL, and an unversioned graph manifest now fails
`verify_graph_dataset.py`.

## 2026-07-18 — learning-loop module consolidation (signoff-loop knowledge/)

Merged the small single-purpose modules into their subsystem homes so each memory system is
ONE core module (knowledge/README.md "Module map" is the new orientation; every caller, test,
and living doc updated in the same change — old names survive only in dated narratives).
**No schema, no DB rows, and no behavior changed**: 927 signoff-loop + 80 rtl-acquire tests
green, honesty gates 5/5 green over the committed store, `check_db_integrity` verdict
unchanged (pre-existing J4 WARN only), `ab_runner.py reconcile-verdicts --dry-run` flips 0 of
397 stored trials.

- **journal system → `journal_db.py`**: absorbed `journal_action.py` (producer CLI — flow
  scripts now call `journal_db.py action|summarize|report`) and `summarize_log.py`
  (deterministic summarizer). The CLI now also honors `R2G_JOURNAL_DB` without an explicit
  `--db` (parity with every library caller).
- **knowledge read API → `knowledge_db.py`**: absorbed `query_knowledge.py`
  (`get_family_heuristics`/`get_closing_period`/`get_deterioration`/`list_families`).
- **A/B maintenance → `ab_runner.py`**: absorbed `reconcile_ab_verdicts.py` as
  `reconcile_verdicts()` + the `ab_runner.py reconcile-verdicts [--dry-run]` CLI.
- **read-only forensics → `observe.py`** (new): `health` (formerly `monitor_health.py`) +
  `trace` (formerly `trace_provenance.py`); firewall-test allowlist updated to match.
- **invariant-32 timestamp**: ONE canonical `knowledge_db.now_local()` replaces 14 duplicated
  local-offset stamp copies across both DBs' writers (drift risk eliminated); the firewall and
  local-timestamp gates still pass through each module's `_now` alias.
- Housecleaning sweep found **no dead scripts**: every `tools/` + `scripts/` entry is
  referenced by a living runbook, the four `_env.sh` copies stay byte-identical (md5
  `9fa599b7…`), and `auto_demote_on_regression` has its production caller in `learn()`.

## 2026-07-16 — close both 2026-07-16 external audits: 21 issues + 1 found in verification (all four skills)

Two external adversarial reports (`docs/superpowers/plans/2026-07-16-{agent-logic,full-pipeline}-issue-report.md`,
probed at cb50537) were audited against live code — **all 21 findings confirmed** — and fixed TDD.
Detail: failure-patterns **#50** (agent-logic, 9 issues) + **#51** (full-pipeline, 12 issues + the
verification-found #13). Committed `knowledge.sqlite`: one nullable column added
(`recipe_status.status_version`), **0 rows moved, 0 verdicts flipped** (verified old-vs-new evidence
counting identical across all 114 trial keys; honesty gates green before/after).

### signoff-loop
- **A/B evidence OWNERSHIP** (`_arms_owned`): decisive trials must cite THEIR OWN arms (role, subject,
  strategy tail, platform), not just existing runs; stamped-True provenance is re-derived at judge time.
- **Deterministic tie**: a tied decisive corpus re-queues as `candidate` (order-independent), never
  inheriting a transient promote/demote.
- **Causal isolation**: baseline-region (outside the fix auto-block) knob equality across arms; a
  divergence vetoes decisive verdicts both ways.
- **Global regression veto**: a target-symptom win that flips LVS/timing/ORFS/DRC good→bad (or loses a
  check A definitively ran) is vetoed; both arms' global vectors ride `metrics_json`.
- **Apply/judge lifecycle revalidation**: `--apply` re-reads the CURRENT lifecycle (rc=5; `--rank-first`
  = the arm-B bypass); `recipe_status.status_version` bumps on every transition and cancels trials whose
  recipe was withdrawn mid-flight.
- **Trial identity**: arm dirs carry a per-trial hash; the judge groups by full `ab_key` — two symptoms
  sharing a subject+strategy can no longer merge/overwrite each other's experiments.
- **Exact-domain demotion**: regression auto-demote counts only live-provenance events on the key's own
  platform + design_class.
- **Verified-effect apply**: rc=0 only when every declared edit LANDED (rc=4 `precondition_failed`/
  `no_effect`; literal `create_clock -period N` SDCs now rewritable; `applied_no_op` for recheck-only
  strategies).
- **Flow identity**: collision-proof `RUN_<ts>_<pid>_<rand>` backend dirs, per-workspace flock with the
  hard-rule message, `flow_variant` in run-meta.json, fix_signoff `--variant` + run-meta recovery
  forwarded to all six runner call sites; identity-bearing `.r2g_restaged` (a newer backend run now
  re-stages; the empty-marker forever-pin is gone).
- **Diagnosis**: PPL-0024 gets a first-class `io_pin_capacity_overflow` kind; utilization overflow is
  error-code-anchored (Yosys "100% utilization" info no longer matches); "No setup violations found" no
  longer reads as a timing violation.

### def-graph
- **DEF binding preserved end-to-end**: extractor re-gates pass `--def`, the gate fingerprint gains a
  content sha256, and the verifier fails an unbound embedded verdict (binding rode the manifest before
  but was overwritten to unknown on every first build).
- **Atomic regeneration**: gate-blocked `run_graphs.sh` exits 7 + stamps `blocked_unsigned` over a stale
  green manifest; stale variant/kind `.pt` files are deleted before the manifest commit.
- **Provenance beats an explicit platform arg** (#13, found verifying these fixes): `_provenance.sh` now
  corrects a contradicting CLI platform to the DEF's run-meta platform (`R2G_PLATFORM_FORCE=1` opt-out).

### rtl-acquire
- **Byte provenance**: `source_manifest` sha256s at expansion; promote refuses
  `rtl_bytes_changed_since_synth`.
- **License/revision fail-closed publish**: resolved commit + conservative license classification ride
  clone summary → design_meta → publish gate (`allowed_license_status`, `require_source_commit`).
- **rc + freshness**: a failed rerun can no longer be reconstructed as success from stale artifacts
  (synthesize checks rc + mtime; failed reruns quarantine prior artifacts; the index rebuilder trusts
  design_meta).
- **`reject` removed from the shipped publish policy** + loader validation of terminal actions.
- **Clock inference + gate**: `common/clock_infer.py` (top-body posedge/negedge inputs); sequential
  designs falling back to a virtual clock are `rejected_unconstrained_clock` unless overridden.
- **Containment**: safe tar/zip extraction; discovery skips paths resolving outside their repo root.
- **Closure honesty**: `bundle_incomplete` markers + `retry,missing_local_module` classification.
- **Quality schema honesty**: `cell_histogram` emitted; absent schema blocks assessment instead of
  scoring zeros.

### tools / housecleaning
- Removed dead one-off wrappers `run_two_designs.sh`, `run_full_sweep.sh`, `batch_run.sh` (superseded by
  batch_flow.sh/batch_orfs_only.sh; ghost comment references reworded).
- Suites after all fixes: signoff-loop **925 passed/1 skipped**, def-graph **421 passed/14 skipped**,
  rtl-acquire **80 passed**; `verify_graph_dataset --batch` green over all 7 built datasets (iir proven
  294/294 after the #13 red→green).

## 2026-07-16 — def-graph emits HeteroData by default (def-graph)

The five post-layout dataset views `{b..f}_graph.pt` are now torch_geometric **`HeteroData`** by
default, generalizing the external RTL2Graph `generate_hetero_bgraph.py` (b-graph only) to all five
views — including the folded `edge_attr`/`edge_y` families and the RC parasitic edge set. The verified
block-positional **homogeneous `Data` is still built first as the source of truth** (every
filter/sort/label-join happens there); the hetero graph is a **value-preserving re-view**
(`graph_lib.homo_to_hetero`, exact inverse `hetero_to_homo`).

### def-graph
- **Heterogeneous default** — per-type node stores (`gate`/`net`/`iopin`/`pin`; the redundant
  `node_type` col0 dropped, so `x` is width 9 and `y`/`y_raw` width 5) + `(src_type, relation, dst_type)`
  edge stores. The **relation is the folded entity** from the view's `edge_schema` (b physical edges →
  `connects`; c/d/e/f → `pin`/`net`/`gate`/…; RC → `rc_coupling`/`rc_resistance`). View **e** folds
  *both* gates and nets onto pin↔pin edges, so the folded entity is REQUIRED in the relation key
  (`(pin,gate,pin)` vs `(pin,net,pin)`).
- **`R2G_GRAPH_KIND`** (= `build_graphs.py --kind`) — `hetero` (default) / `homo` (legacy flat
  `x[N,10]`/`y[N,6]`) / `both` (hetero `{v}_graph.pt` + homo `{v}_graph_homo.pt`). The manifest records
  `graph_kind` + a per-variant per-type/per-relation `hetero` breakdown.
- **`netlist_graph.pt` stays homogeneous** — it is a pre-layout artifact shared with the `rtl-acquire`
  corpus supply line, whose contract is homogeneous.
- **Verifier is hetero-aware** — `tools/verify_graph_dataset.py` reconstructs the homogeneous `Data`
  **independently** (a second implementation of `hetero_to_homo`, so a conversion bug fails a homo check)
  and runs the full topology/feature/label/RC/signoff surface on it, then adds a `hetero.*` group (node
  types, per-type tensor widths, relations over present types, manifest-breakdown parity) and swaps the
  homo `[fwd,rev]` interleaving guard for a hetero-native per-store alignment + reverse-relation symmetry
  guard. Negative controls on a corrupted hetero label / `edge_attr` fail loudly.
- Validated on adder_tree sky130hs: verifier **294/294**, exact homo→hetero→homo round-trip on all five
  views, def-graph pytest **406 passed / 14 skipped**.

## 2026-07-14 — RTL2Graph_v3 reference alignment: raw-label twins + num_drivers no-fill + LEF pin geometry (def-graph)

Compared a fresh `RTL2Graph_v3` reference drop subsystem-by-subsystem against `def-graph`. The reference
("updated after debugging") is **behind** ours on correctness — it never absorbed our 2026-07 silent-value
fixes — so its four bugs (congestion vertical-demand transpose, wirelength/congestion RECT-patch not stripped,
timing STA↔ODB name not de-escaped, and the c/d/e/f `[all-fwd|all-rev]` edge-attr misalignment = our "bug #5")
were **reported, not ported** (`failure-patterns.md` #47). Three deliberate reference improvements were adopted
(user-approved: keep both raw + normalized labels). Validated end-to-end on cordic sky130hs: verifier
**212/212**, def-graph pytest **395 passed / 14 skipped**, OpenDB orientation oracle **5105/5105**.

### def-graph
- **Raw-label twins** — every label tensor gains a parallel raw twin (`y_raw` / `edge_y_raw` / `rc_edge_y_raw`)
  carrying the raw physical value (EDA-Schema/CircuitNet convention: demand/cap ratio, mV, path-delay ns, um,
  fF, Ohm) beside the normalized log/sqrt target, so a downstream trainer picks either convention without a
  regen. The raw columns already lived in the label CSVs; `graph_lib`/`build_graphs`/`attach_rc_labels` surface
  them, with a `y_raw_schema` on the tensor + manifest.
- **`num_drivers` no-fill** — `nodes_net.py` no longer fabricates `num_drivers=1` on a driver parse-miss (which
  also corrupted `num_sinks`); a genuinely undriven / unresolved net honestly reads 0. Verifier `>=1 on ALL
  nets` assert relaxed accordingly.
- **LEF pin-center geometry** — new `techlib.lef.macro_pin_geometry` + `apply_orient` place pins at their true
  orientation-aware in-cell centers, so `hpwl_um` / `pin_x/y_std_um` are real geometry (matters for macros);
  `run_features.sh` now exports `SC_LEF`/`ADDITIONAL_LEFS`; empty cell LEF ⇒ instance-origin fallback.

### Post code-review (xhigh) fixes
- **`apply_orient` FN/FS swap** — the initial port carried the reference's transposed FN/FS (FN returned MX,
  FS returned MY). FS is the alternating-row flip = **~half of all std cells** (cordic 2488/5105), so
  `hpwl_um`/`pin_x/y_std_um` were wrong for every net touching a flipped cell — and the verifier's
  `_v_apply_orient` + the unit test replicated the same swap, so the build verified green anyway. Fixed (swap
  FN↔FS) and **validated against OpenDB placed pin locations** (cordic FS=MX matched 2488/2488).
- **Timing raw `+inf`** — the raw twin read `Cell_Slack_ns`, which is the string `"INF"` off-path (→ `+inf`);
  switched to `Path_Delay_ns` (finite, clean `y[:,3]==log1p(y_raw[:,3])` identity). `Cell_Slack_ns` stays a
  CSV-only column.
- Verifier hardening: raw-tensor value checks (timing/wirelength/ground-cap `log1p` identities + SPEF-oracle
  raw ground/coupling), the raw edge twins added to the interleave (bug-#5) oracle, a `num_drivers==0` no-fill
  honesty check (covers 0-driver nets past the 200-net sample cap), `SC_LEF` whitespace-split + `CELL_LEFS`
  parity with the extractor, POLYGON `MASK` tolerance, and a `hasattr` guard against pre-raw-twin corpora.

## 2026-07-13 — MemoryStore & A/B evidence-chain audit: contract-drift + provenance fixes (#43–#46)

Acted on a 10-step read-only MemoryStore/A-B audit (report `docs/superpowers/plans/07-13-report.html`;
verdicts + fix map in `docs/superpowers/plans/2026-07-13-memorystore-audit.md`). Every claim re-verified
against the current tree (the report's `r2g-skills/shared/**`, `r2g-rtl2gds/**` paths are stale). 4 real code
bugs fixed (TDD); the state/operational findings (re-run pre-2026-07-10 A/B, inconclusive governance,
`journal.sqlite` fixture) are noted as follow-ups. Suites: signoff-loop **843 passed / 2 skipped**,
rtl-acquire **61 passed**, honesty **5/5**; store reprojected (heuristics gen 6036, `improved:46`, 250
symptom-split sessions).

### signoff-loop
- **MemoryStore contract unified (#43)** — `analyze_execution._derive_status` now delegates to the canonical
  `ingest_run._derive_orfs_status` (int/bool exit codes + `flow_scope`; the string-only copy classified every
  int-status run `partial`). `rank_proposals` reads the canonical `fix_recipes["orfs_stage"][stage]` (legacy
  `orfs` fallback) and `analyze()` surfaces `learned_stage_ranking` — the 91 stage recipes finally have a reader.
- **Trajectory honesty (#44)** — new `improved` outcome for a partial `win` (winner preserved, strictly below
  `resolved`); `fix_trajectories` PK grows `symptom_id` so a symptom-shifting session splits per symptom
  (`knowledge_db._migrate_drop_stale_fix_trajectories` drops the legacy-PK projection for recreation).
- **A/B + fix_event provenance (#45)** — `ab_trials` back-reference both arms' run-ids + stamp
  `provenance_complete`/`tool_versions`; `record_trial` warns on a decisive verdict without distinct run-ids.
  New `knowledge/tool_versions.py` (cached, fail-safe) finally writes the long-empty `tool_versions_json`.

### rtl-acquire
- **Dual-memory honesty (#46)** — `project_frontend_diagnosis --check` no longer reads an empty `synth_only`
  projection as convergence (`0==0`); reports COVERAGE, prints `COVERAGE EMPTY … UNPROVEN`, and adds
  `--require-nonempty` (empty ⇒ exit 2).

## 2026-07-13 — Codex debug-findings audit: `build_diagnosis` `kind:none` gap + git-pollution hygiene (#42)

Audited 5 instance-testing findings + 6 architectural learnings from an external reviewer
(grading in `docs/superpowers/plans/2026-07-13-codex-debug.md`). Only **1 of 5** Part-I
findings was a real (cosmetic) gap; the other 4 were phantom causes or already-shipped
features (#36/#38). Suites green: signoff-loop **833 passed / 2 skipped**, honesty **5/5**.

### signoff-loop
- **`build_diagnosis` reported `kind:none` for a backend stage abort/timeout** (`build_diagnosis.py`;
  #42). A stage SIGKILLed at `ORFS_TIMEOUT` (#40) leaves no `make` error line, so every text-log rule
  missed it and `main()` fell through to `kind:none` — even though `build_run_summary()` already knew
  `signoff.orfs_status='fail'`. `main()` now consults the ORFS stage ledger first: `_orfs_fallback_kind`
  emits `orfs_stage_failed` / `orfs_stage_incomplete` (naming the fail stage) with `issues:[]`
  (presentation-only ⇒ **no** duplicate `failure_event`; the learner already derived the `orfs-fail-<stage>`
  event independently from `stage_log`). Also echoes `antenna_nonconverged.json` into `run_summary`.
  The 4 other proposed rules were rejected as firing on non-existent conditions (see the plan doc).

### repo hygiene
- **`.gitignore`** now covers `tools/_*_resume_logs/` (~370 MB of campaign wave logs, same class as
  `design_cases/` — genuine untracked pollution). Added **`.gitattributes`** marking the tracked, churning
  `knowledge.sqlite` blob `binary` (cleaner diffs; cross-operator sharing stays on `knowledge_sync.py`).
  The reviewer's "gitignore the store" was rejected — it breaks the tracked shipped-store invariant (D14),
  a migration already tried and reverted 2026-06-23.

## 2026-07-12 — Codex robustness-suggestion audit: 5 latent bugs + per-metric/observability hardening (#38)

Audited 7 Codex robustness suggestions against the actual code (grading in
`docs/superpowers/plans/2026-7-12-codex-suggestion.md`). The 2026-07-10 sweep had
already shipped the big items; this pass closes **5 latent bugs** and 4 **partial**
gaps, each with tests + failure-patterns #38. Full suites green:
signoff-loop 818, def-graph 386, rtl-acquire 60.

### signoff-loop
- **Antenna non-convergence counter was cumulative, not consecutive** (`fix_signoff.sh`;
  #38a). `antenna_noimp` incremented but never reset on an improving antenna iteration, so
  a design converging via interleaved wins/no-ops (10→5→5→3→3) was falsely aborted at the
  2nd cumulative no-op. Now resets on each improving antenna iteration.
- **Diagnosis mislabeled route/LVS `ERROR` lines as synthesis errors** (`build_diagnosis.py`;
  #38b). `parse_synth_errors` got the full concatenated log; now scoped to the `synth.log`
  section (new `section_text`). Also **consolidated `run_summary`** (codex #7): stage
  durations + repair repetitions + DRC/LVS/route/timing status in `diagnosis.json`.
- **ORFS resume provenance** (codex #3). `run_orfs.sh` stamps per-stage `ts_start`/`ts_end`
  + output `artifact` into `stage_log.jsonl` (additive; contract preserved), tees the
  reuse/rerun decision to `flow.log` with its concrete `R2G_RERUN_REASON` (from
  `fix_signoff.sh`), and writes `resume_meta.json`.

### def-graph
- **Antenna is now its own gate dimension** (`signoff_gate.py` `_check_antenna`; codex #5) —
  clean/fail/nonconverged/not_covered/unknown, decoupled from routing-DRC so a
  routing-clean-but-antenna-dirty design is visible in `signoff_health` (a caveat, never a
  new blocker).
- **`_check_route` gates on the count, not the status string** (#38c): a foreign
  `status="clean"`+`violations>0` no longer reads clean; a genuine `unknown` no longer
  mislabels `dirty`.
- **Graph SKIP manifests carry the specific upstream reason** (`graph_skip_manifest.py`;
  codex #6) — antenna-nonconvergence marker / ORFS `orfs_fail_stage` / signoff blockers /
  newest `stage_log.jsonl` failing stage threaded into `graph_dataset.json`, not a bare
  "no 6_final.def".

### rtl-acquire
- **Promote manifest re-dumped after `--run`** (`promote_candidates.py`; #38d) — a failed
  immediate flow now records `promoted_flow_failed` on disk, not a stale `promoted`.
- **High-mem round guard scoped to runnable candidates** (`run_expansion_round.py`
  `runnable_high_mem_designs`; #38e) — a `resource_tier=high` row filtered out by
  `--priorities` no longer hard-blocks a round it was never in.
- **Low-priority deferral queue** (`expand_candidates.py`; codex #1) — risk-flagged /
  `resource_tier=high` candidates stable-sorted to the tail of the round (observable via a
  `risk_deferred` stage marker); `--no-defer-risky` opts out. Deeper static analysers
  (memory-bit estimate, dependency-completeness) remain a documented follow-up.

## 2026-07-11 — campaign driver single-instance guard end-anchors pgrep (#37)

### signoff-loop
- **The wave driver's single-instance guard no longer false-matches its own
  launching shell** (`tools/campaign_resume_waves.sh`; failure-patterns #37).
  The guard rejected a double-launch with an **un-anchored**
  `pgrep -f "campaign_resume_waves\.sh"`, which also matched the operator's
  launching shell — its `setsid bash tools/campaign_resume_waves.sh …` command
  line literally names the script. In the natural `… & sleep N; pgrep`
  confirm-it-came-up pattern the launcher outlives the check, so the guard saw a
  "rival" and refused to start, leaving a round dead-in-the-water after a reboot
  **while every DB honesty gate stayed green** (a driver that never starts is
  invisible to a store that only records runs that happened). Fix: END-ANCHOR the
  pattern (`campaign_resume_waves\.sh$`) so it matches only a process *exec'd* on
  the script, plus a `$PPID` exclude for the residual exact-match case; the robust
  per-ledger `flock` remains the primary guard underneath. A `R2G_GUARD_SELFTEST=1`
  hook runs the guard in isolation (report + exit before any wave work) so it is
  unit-testable. Tests: `signoff-loop/tests/test_campaign_driver_guard.py`
  reproduces the false-positive and proves a genuine second driver is still caught.
  **Lesson:** a `pgrep -f` liveness guard must match on the process's *exec
  identity* (anchored path), not on any command line that happens to *name* it.

## 2026-07-10 — robustness sweep across all four sub-skills

Six operator-reported robustness gaps, each closed with code + tests + a
failure-pattern entry (`signoff-loop/references/failure-patterns.md` #34–#36):

### rtl-acquire
- **Keyword screening is risk-marking, not rejection.** The RAM/hard-macro
  denylist no longer hard-rejects candidates on a raw whole-text substring hit
  (picorv32 was thrown away because the formal-only `RISCV_FORMAL_BLACKBOX_*`
  macro names contain "blackbox"). Tokenized, comment-stripped matching lives
  in `scripts/common/rtl_risk.py`; flags ride the candidate CSV `notes`
  (`risk_flags=…`); the synth attempt arbitrates, and the repair-side
  classifier excludes only on failure evidence (memory tokens only).
- **CWD-proof candidate paths.** `~`/`$VAR` expand; relative paths bind to the
  candidate CSV's directory, then the repo root — never the caller's CWD
  (`references/candidate_csv_schema.md` "Paths").
- **Retry mechanics.** Failed candidates always retried by default (unchanged);
  new `expand_candidates.py --force` re-runs recorded successes, and
  `discover_download_candidates.py --retry-excluded` re-emits candidates parked
  in `failed_candidates_exclude.csv`.
- **One-click promote** (`scripts/promote/promote_candidates.py`): synth-proven
  candidate (index `status==success`, optional publish gate) → ready-to-run
  signoff-loop full-flow project under `design_cases/` — vendored RTL, template
  config.mk carrying the proven synth knobs + floorplan directive (drops
  `R2G_FLOW_SCOPE=synth_only`), clock-port-detected SDC (virtual-clock
  fallback), `validate_config.py` as the readiness gate, optional `--run`.

### signoff-loop
- **Stage-scoped reflow instead of full rebuilds** (#35). `run_orfs.sh` now
  runs `make clean_<FROM_STAGE>` before a resume so a config edit is
  guaranteed to apply (ORFS's Makefile has no dependency on config.mk — a
  plain resume silently NO-OPed the edit) while earlier stages' artifacts are
  reused; `R2G_RESUME_NO_CLEAN=1` keeps the pure crash-resume. `fix_signoff.sh`
  resumes from each strategy's `rerun_from` by default (`--resume` is a no-op
  alias; `R2G_FIX_FULL_REFLOW=1` restores the old full rebuild).
- **Antenna repair non-convergence auto-exit** (#36). Two non-improving
  antenna strategies end the check with the terminal verdict
  `antenna_nonconverged` (ingested as `no_change` — negative evidence) and
  persist `reports/antenna_nonconverged.json`; later fix sessions auto-exclude
  the proven-futile strategies instead of re-burning the same diode+reroute
  reflows (the SHA-1/SHA-256 loop). `R2G_FIX_RETRY_NONCONVERGED=1` retries
  deliberately; the marker self-clears on CLEAN.

### def-graph
- **Automatic signoff gate before dataset generation** (#34). A `6_final.def`
  alone no longer builds a dataset: the shared `scripts/flow/signoff_gate.py`
  checks DRC ∈ {clean, clean_beol}, LVS ∈ {clean, skipped}, ORFS completion
  (`stage_log.jsonl`), and route/antenna residuals — fail-closed on missing
  reports. `run_graphs.sh` enforces; `run_labels.sh`/`run_features.sh` warn;
  `R2G_SIGNOFF_GATE=enforce|warn|off` overrides. The verdict is stamped into
  `graph_manifest.json` as `signoff_health`, and
  `tools/verify_graph_dataset.py`'s Group-C gate is now fail-closed (a dataset
  with neither signoff reports nor a recorded gate verdict FAILS instead of
  passing vacuously).

Tests: signoff-loop 806 passed / 1 skipped; def-graph 372 passed / 14 skipped
(torch venv); rtl-acquire 51 passed.
