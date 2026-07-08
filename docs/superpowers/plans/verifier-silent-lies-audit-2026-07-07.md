# Bug Report — Silent-Lie Defects in the Dataset-Verification Infrastructure (2026-07-07)

**Component:** `def-graph` dataset verification — `tools/verify_graph_dataset.py`,
`r2g-skills/def-graph/scripts/extract/{labels/compute_label_stats.py,features/compute_feature_stats.py}`,
`r2g-skills/def-graph/scripts/extract/graph/graph_lib.py` (`label_health`).
**Class:** verification blind spots ("silent lies") — the built PyG dataset does **not** match the design
files (DEF/LEF/liberty/SPEF), yet every verification surface reports success (`verify_graph_dataset.py`
exits 0 / prints PASS, the stats gate says `ok`, the manifest `status`/`label_health` say `ok`).
**Status:** **FIXED 2026-07-08** (BUG-1/2/3/4 + L1) — see §7 Resolution. **Reporter:** verification-infra audit.
**Scope:** bugs in the *verifier/gates/tests themselves* — the last line of defense — not the extractors.
**Platform note:** asap7 and gf180 are **parked** at the current stage, so BUG-2 (whose vacuous-power
instance is specific to those techs' block-form leakage) is not a live risk on the active platforms
(sky130hd/hs, nangate45, ihp-sg13g2, which use scalar `cell_leakage_power`); it was still fixed, because the
`power_checked > 0` guard is a general anti-vacuous-pass protection for every platform.

---

## 1. Summary (the meta-finding)

The verification chain validates two links well:
- **tensor == CSV** (`verify_y`, node/edge counts, clique formulas, RC edge counts), and
- **CSV == its own header** (the stats gates' `REQUIRED_COLS`/label-column presence checks).

But the third link — **"the label VALUES are real numbers that match the DEF"** — is checked in exactly one
place (`reports/labels_stats.json`, via `compute_label_stats`), which:
1. the flagship verifier `verify_graph_dataset.py` **never reads**, and
2. `run_graphs.sh` consumes **only for its mtime** (a staleness marker), never its content — so a label the
   gate classifies `invalid` is still built into the graph, and the graph's own `label_health` re-reports it
   `ok` (it inspects column/row presence, never values).

Everywhere that third link is otherwise re-checked, it rests on **IEEE NaN semantics** that make the check
vacuous: `abs(got - exp) > tol` is **always `False`** when either operand is `NaN`, and the
`checked > 0 and bad == 0` guards increment `checked` on `NaN` rows *before* the comparison — so
"checked 400 cells, 0 bad" is satisfied **entirely by `NaN`**. A guard that reads as coverage is the vehicle
for the lie.

Net effect: a **NaN-producing** or **truncated** corpus regeneration — including regressions of documented
real bugs (transposed congestion, SPEF↔DEF escaping join, quoted-unit cap scaling) in their *NaN-shaped*
variants — can pass `verify_graph_dataset --batch` fully green.

---

## 2. How these were found (methodology)

1. Full manual read of `verify_graph_dataset.py` (1458 lines), both stats gates, and `graph_lib.label_health`
   / `build_graphs.py` manifest logic.
2. A **runnable reproduction** of the NaN-vacuous mechanism (§3, BUG-1) confirming the IEEE behavior and the
   green-through-the-whole-chain result.
3. An adversarial hunt-then-verify workflow: 5 finders produced **34 candidate findings**; each was handed to
   an independent skeptic instructed to **refute** it by locating a backstop elsewhere in the chain. Result:
   **6 confirmed**, **13 refuted with backstops** (§5), 2 latent/plausible (§4), remainder unadjudicated
   (verifier agents interrupted). The refuted set is reported deliberately — it prevents wasted fix effort.

---

## 3. Confirmed defects (ranked)

### BUG-1 — [HIGH] All-NaN / NaN-producing **congestion or timing** label ships fully green
**Where:** `tools/verify_graph_dataset.py` — `verify_y` value check `:1059-1060`, extended congestion
`:877-882`, extended timing `:912-922`; `graph_lib.label_health :236-252`; `run_graphs.sh` marker-only
freshness.

**Silent lie:** a join/extraction regression makes `cell_congestion.csv` / `timing_features.csv` `label`
column all-`NaN` (or NaN for a subset). The graph builder joins the same CSV, so the tensor `y1`/`y3` slot is
all-`NaN` too. Every verification surface is green; the dataset ships to GNN training with *no* congestion /
timing labels.

**Mechanism (traced):**
- `verify_y` count check (`:1054-1057`) compares `exp_nn == got_nn`; both derive from the same CSV, so
  `0 == 0` → PASS.
- `verify_y` value check (`:1058-1063`) samples `exp.dropna().index[:sample_n]` → empty → the
  `if len(idx):` guard is `False` → **no `check()` is emitted at all**.
- Extended congestion (`:877-880`): `abs(sg/cnt - float(row["label"]))` with `row["label"]` = `NaN` → `False`
  → `bad_l` never increments; `checked_c` was incremented at `:875` → `check(..., checked_c>0 and bad_l==0)`
  → PASS.
- Extended timing (`:912-922`) asserts only `covered == seq_insts` (sequential-instance **names** present),
  never that the timing label is a correct or even non-`NaN` value.
- `label_health` (`:236-252`) returns `ok` on column + row presence; it never inspects values.
- `run_graphs.sh needs_stage` uses `labels_stats.json` mtime only; `build_graphs.py` never reads its content.
  So the one surface that *does* catch all-`NaN` (`compute_label_stats._col_floats` drops `NaN` → empty →
  `invalid`) is a side file nothing downstream consults.

**Adversarially narrowed — which labels are actually exposed:**
- **congestion** ✗ no non-`NaN` backstop → **exposed**.
- **timing** ✗ no independent value oracle (only name-coverage) → **exposed** (this is the separately
  confirmed `timing-label-no-value-oracle`; note it also ships a *wrong-but-numeric* timing value undetected).
- wirelength ✓ safe — `ext.wirelength label == log1p(um)` (`:907-910`) FAILs on all-`NaN` via pandas `.max()`.
- RC ground cap ✓ safe — explicit `math.isnan(got)` (`:1261`).
- IR drop ✓ safe — the `d_floor` `NaN` catch in `irdrop_label_ok` (`:459-461`).

**Reproduction (confirmed):**
```
abs(float('nan') - 5.0) > 1e-4   -> False        # verify_y value check never flags a NaN slot
exp=[nan]*1000 ; got=[nan]*1000  -> exp_nn==got_nn==0 -> count check PASS
extended congestion: checked_c=400, bad_l=0 -> check(..., checked_c>0 and bad_l==0)=True -> PASS
label_health: column+rows present -> 'ok' ; manifest.status -> 'ok'
=> y1/y3 100% NaN, every gate/manifest/verifier surface GREEN; only labels_stats.json flags 'invalid'.
```

**Backstop:** none in the verifier/manifest for congestion & timing values.
**Fix (§6):** make `verify_y` flag `torch.isnan(got[i])` where `exp[i]` is non-`NaN`; count `NaN`
`row["label"]` as `bad_l` in extended congestion; add a per-slot populated-fraction assertion; strengthen
`label_health` to reject an all-`NaN` value column.

---

### BUG-2 — [HIGH] `ext.gate power` is vacuous on **asap7 (the default platform) and gf180 on every run**
**Where:** `tools/verify_graph_dataset.py` — `read_liberty_truth :146`, `ext.gate power :624, :632`.

**Silent lie:** `cell_power` in `nodes_gate.csv` is off by a scale factor or otherwise wrong (the gf180
quoted-value 1000× class named in CLAUDE.md); the verifier's `ext.gate power == liberty leakage` check
records PASS having compared **nothing**.

**Mechanism:** the verifier's `read_liberty_truth` matches only the **scalar** form
`cell_leakage_power : X` (`:146`). But asap7 and gf180 write leakage as **block-form**
`leakage_power(){ value : X }` (asserted by the repo's own `test_techlib_crossplatform.py:293-294`; gf180
additionally quotes the value). So `lc["power"]` is `None` for **every** cell → the
`if lc and lc.get("power") is not None` guard at `:624` short-circuits → `bad_power` never increments →
`check("ext.gate power == liberty leakage", checked > 0 and bad_power == 0)` at `:632` passes with
**zero comparisons** on every run for those platforms. The extractor *does* parse block-form leakage (the
techlib test asserts 100% positive), so real, possibly-wrong values ship unvalidated.
- The sibling `ext.gate area` check (`:622, :630`) is vacuous only when liberty fails to resolve *entirely* —
  but that total-failure case is **backstopped** by `ext.sum_pin_cap_fF` (`:696`, real caps vs expected 0 →
  FAIL). So **power is the airtight, always-on bug**; area is defense-in-depth.

**Backstop:** none — `compute_feature_stats` only summarizes the `cell_power` distribution (no ground truth);
`label_health`/`rc_health` cover Y labels, not the X `cell_power` feature.
**Fix (§6):** parse block-form `leakage_power(){value:…}` (quote-tolerant) in `read_liberty_truth`; add a
`power_checked > 0` guard so "no liberty leakage matched any sampled gate" FAILs instead of passing.

---

### BUG-3 — [MED] Row count vs DEF is never checked → a cleanly-truncated CSV reads `ok`
**Where:** `compute_feature_stats.py :120-124` (truncated check); `verify_graph_dataset.py :782-787`
(`ext.metadata num_cells`); `graph_lib` node build.

**Silent lie:** an interrupted writer flushes `nodes_gate.csv` after 8,000 of 12,000 components **on complete
row boundaries**. `metadata.csv` (a separate step) still reports `num_cells = 12000`. The graph gets 8,000
gate nodes; the design ships as `ok`.

**Mechanism:** the feature gate's `truncated` check (`:120`) flags only rows whose **required** columns are
`None`/`""` — all 8,000 present rows are complete → `truncated == 0` → `status = "ok"`. The verifier's
`ext.metadata num_cells == len(DEF comps)` (`:782-787`) validates the *self-reported scalar* (intact = 12000),
**not** the `nodes_gate` row count. Every node/edge-count check re-derives from the same truncated CSV, so
there is no comparison of `len(nodes_gate) (8000)` against `metadata.num_cells (12000)` or DEF `COMPONENTS`.

**Backstop:** none (all count checks share the truncated CSV as their reference).
**Fix (§6):** assert `len(nodes_gate) == metadata.num_cells == len(DEF COMPONENTS)` (and the analogous
net/pin/iopin identities) in the verifier.

---

### BUG-4 — [MED] SPEF de-escape oracle is byte-identical to the extractor and `continue`s past dropped nets
**Where:** `tools/verify_graph_dataset.py` — `_spef_deesc :465-468`, RC check (A) `:1257`, coupling count
`:1268-1271`.

**Silent lie:** a **two-sided** regression of the SPEF↔DEF escaping join (bug #20 class — dropped ~80% of RC
labels on hierarchical / double-bus nets) makes affected SPEF names de-escape to a form that no longer joins
`nodes_net`. Their RC labels drop from the tensor; the verifier keys its own `gt`/`ct` with the **same** wrong
names and passes.

**Mechanism:** `_spef_deesc` (`:468`, `re.sub(r"\\([^\[\]])", r"\1", name)`) is byte-identical to
`techlib/spef.py`'s `_deesc`. Check (A) iterates DEF net names and does `if nm not in gt: continue` (`:1257`)
— a name absent from the equally-mis-escaped `gt` is silently skipped; the only guard is `chk > 0` (`:1265`),
satisfied by the ~20% surviving flat nets. Coupling `exp_coup` (`:1268`) is computed from the same-escaped
`ct` and matches the equally-reduced tensor → PASS. `rc_health.ground_cap_nets` counts **pre-join** SPEF-side
CSV rows (`extract_rc.py`, no DEF join at the label stage) → structurally blind to graph-join loss.
- Note: a **one-sided** extractor-only regression *is* caught — the oracle's `gt` keeps correct DEF-form keys,
  so `nm ∈ gt`, `got = NaN`, and the `math.isnan(got)` at `:1261` fires.

**Backstop:** none for the two-sided case (no join-rate floor anywhere).
**Fix (§6):** replace `continue`-on-miss with a **join-rate floor** —
`|design nets matched to SPEF| / |SPEF D_NETs ∩ design nets| ≥ ~0.95` → FAIL below it.

---

### BUG-5 — [LOW] Sampling caps miss localized value defects, with no coverage logged
**Where:** `verify_graph_dataset.py` — congestion `cong.head(400)` `:842`, net `checked_net > 200` `:713`,
wirelength top-10 + middle-10 `:901`, RC `[::stride]` `:1275,:1289,:1308`.

**Silent lie:** a value defect confined to unsampled rows (a congestion error on cells past CSV row 400, a
net-feature error beyond the 200th DEF net, a wirelength error on a mid-magnitude net in neither the top-10
nor the exact middle-10 slice) passes because those rows are never compared. The PASS reads as if the whole
design were verified. Violates the repo's own "No silent caps — `log()` what was dropped" principle.
**Fix (§6):** emit the sampled-vs-total coverage in the check detail; raise/parametrize the caps.

---

### BUG-6 — [LOW] Netlist connectivity check is presence-only over the first 40 instances
**Where:** `verify_graph_dataset.py :1373` (`stmts[:40]`), `:1355` (cell count), `:1358` (bipartite parity).

**Silent lie:** the netlist builder adds a **phantom** cell↔net edge (the "const phantom net" class) or wires
a port to the wrong net beyond the first 40 statements. The check only counts netlist edges **missing** from
the tensor; a phantom edge present in the tensor but absent from the netlist is never examined, and the
bipartite-parity check stays even under a symmetric phantom. **Fix (§6):** also verify tensor edges are
*justified* by the netlist (reverse direction) and validate the net-node count against an independent source.

---

## 4. Latent (PLAUSIBLE — not a live lie today)

- **L1 — congestion recompute skipped with no `check()` emitted** when `GCELLGRID`/routing-layers/diearea
  don't resolve (`:822 if gs_x and gs_y and layers and die:`). Today partially backstopped: a resolver
  failure that empties `layers` also empties `lib`, so `ext.sum_pin_cap_fF` FAILs — but a `TECH_LEF`-only
  unresolved window would silently drop *all* congestion value verification. Harden by emitting an explicit
  FAIL when the congestion inputs are unexpectedly absent.
- **L2 — verifier and extractor liberty parsers are both line-anchored** (`re.match` per line); the documented
  "fixture liberty MUST be one-attribute-per-line" gotcha is never *asserted*, so a crammed liberty would
  mis-parse identically on both sides. Real PDK liberties are tool-generated one-attr-per-line, so the trigger
  doesn't occur — but the shared blind spot is real.

---

## 5. Refuted candidates (NOT bugs — a verified backstop exists; do not "fix")

| Candidate | Why it is safe |
| --- | --- |
| `inf` passes the `v==v` NaN filter in the gates | Extractors guard every division (`if cap_h > 0 else 0.0`); RES/CAP are finite SPEF sums — no `inf` reaches the gate. `json.load` reads `Infinity` back; training reads CSV/tensors, not the stats JSON. |
| Feature required-col full of literal `'nan'` reads `ok` | Numeric feature writers fall back to `0.0` via format-spec — a literal `'nan'`/empty required numeric column is never produced. |
| `connects_macro_flag` oracle agrees with the flag=0 bug | The verifier's `blocks` set is an **independent** source (LEF `CLASS BLOCK`, `:212-214`) vs the extractor's liberty lib-diff; `:748` catches flag=0. |
| `ext.distinct liberty masters get distinct ids` vacuous when `lib={}` | `:659` catches the realistic in-liberty injectivity collision; the empty-lib case is caught by degenerate all-zero output visible in `features_stats.json`. |
| RC checks pass on a single joined net (`chk>0`) | Check (A) reads `b.y` **positionally** over all design-nets-in-SPEF → a dropped label is `NaN` → `isnan` → FAIL; coupling has an exact SPEF-count floor (check B, `:1270`). |
| Congestion oracle shares the scipy-ported gaussian/capacity | Anchored by the `<1e-9` scipy golden test (`test_extract_congestion.py:148-162`) + the independent DEF route walk (`read_def_truth`); the only shared piece (the capacity formula) is a definitional constant with no DEF ground truth. |
| Partial-NaN in **timing/wirelength row coverage** | The seq-instance coverage check and the wirelength route walk catch CSV-stage under-coverage. (The **value**-NaN gap for congestion/timing remains — that is BUG-1, a different axis.) |
| `compute_label_stats` pooled across designs | `label_health` per-design filter → `no_rows_for_design`; tensor builders emit all-`NaN` y for 0 matching rows. |
| `resolve_platform_files` swallows failures with no sentinel | A total resolver failure is caught by `ext.sum_pin_cap_fF` (real caps vs expected 0 → FAIL). |
| RC loss indistinguishable from `no_rc_labels` | The RC block is driven by the independent on-disk SPEF re-derivation (`:1247, :475-480`), not by manifest `rc_health`. |

---

## 6. Fix plan (priority order; each ships with a regression test that FAILs on pre-fix code)

1. **BUG-1 (HIGH)** — NaN-safe + coverage-asserting label value checks:
   - `verify_y`: add `bad += int(torch.isnan(got[int(i)]))` for sampled non-`NaN` `exp`; add a per-slot
     assertion that the tensor populated-fraction matches an independent expectation (congestion: every kept
     gate; timing: every in-STA-path cell).
   - extended congestion: `if not (val == val): bad_l += 1` (or `math.isnan`) so a `NaN` `row["label"]` is a
     failure, not a skip.
   - `graph_lib.label_health`: add an `all_nan`/`mostly_nan` value status (reject a column whose values are
     entirely `NaN` for the design) so the manifest `status` degrades to `ok_with_label_gaps`.
   - *Regression test:* a fixture with an all-`NaN` congestion and an all-`NaN` timing `label` column must make
     `verify_case` return `n_fail > 0` **and** `label_health != "ok"`.
2. **BUG-2 (HIGH)** — block-form leakage parse + `power_checked > 0` guard in `read_liberty_truth`/`ext.gate`.
   *Regression test:* a block-form `leakage_power(){value:X}` liberty fixture yields a non-`None` `lc["power"]`
   and a wrong `cell_power` FAILs the check.
3. **BUG-3 (MED)** — row-count-vs-DEF identities in the verifier.
   *Regression test:* a truncated `nodes_gate.csv` (rows < `metadata.num_cells`) FAILs.
4. **BUG-4 (MED)** — SPEF join-rate floor.
   *Regression test:* a SPEF whose names don't de-escape to DEF form drops the join rate below the floor → FAIL.
5. **BUG-5/6, L1/L2 (LOW/latent)** — log sampled coverage; verify netlist edges are justified; emit an
   explicit FAIL when congestion inputs are unexpectedly absent; assert the one-attr-per-line fixture invariant.

**Cross-references to update on fix (per CLAUDE.md "When You Fix a Bug"):**
`r2g-skills/signoff-loop/references/failure-patterns.md` → "Dataset-Extraction Silent-Value Defects"
(append a "Verification-infra blind spots" sub-section); `r2g-skills/def-graph/references/graph-dataset.md`
(audit-notes); and this file's status line once each fix lands.

---

## 7. Resolution (2026-07-08)

All confirmed defects fixed; asap7/gf180 parked so BUG-2 was low-risk but fixed for generality.

| Bug | Fix | File |
| --- | --- | --- |
| BUG-1a | `label_health` now returns `all_nan` when a label column is present + has rows but every value is NaN → manifest `status` degrades to `ok_with_label_gaps` → verifier `manifest.label_health all ok` FAILs. All-ZERO (legit degenerate) still reads `ok`. | `graph_lib.py` |
| BUG-1b | `verify_y` value check flags `math.isnan(got[i])` where the CSV has a value (NaN-safe). | `verify_graph_dataset.py` |
| BUG-1c | extended congestion recompute counts a NaN `cell_congestion`/`label`/`label_raw` as a mismatch. | `verify_graph_dataset.py` |
| BUG-2 | `read_liberty_truth` parses block-form `leakage_power(){value:X}` (quote-tolerant); `ext.gate` requires `area_checked>0`/`power_checked>0` so "no liberty compared" FAILs instead of passing vacuously. | `verify_graph_dataset.py` |
| BUG-3 | `ext.nodes_{gate,net,iopin} rows == DEF {COMPONENTS,NETS,PINS}` truncation guards. | `verify_graph_dataset.py` |
| BUG-4 | RC block asserts a ≥0.8 SPEF-join rate over escape-sensitive DEF nets (`.`/`$`/`\[`), skipping when <20 such nets (flat designs) so no false-fail; catches a two-sided de-escape regression the shared-oracle skip missed. | `verify_graph_dataset.py` |
| L1 | congestion value-vs-DEF block now emits an explicit FAIL (not a silent skip) when GCELLGRID/layers/DIEAREA don't resolve. | `verify_graph_dataset.py` |

**Deferred (LOW):** BUG-5 sampling-coverage logging and BUG-6 netlist reverse-edge / phantom-edge check —
tracked here, not yet implemented.

**Regression tests (fail on pre-fix code):**
- `tests/test_graph_stage.py::test_label_health_flags_all_nan_label` (+ `…_all_zero_label_is_ok` guard).
- `tests/test_verify_graph_dataset_helpers.py::test_read_liberty_truth_block_form_leakage`
  (+ `…_scalar_leakage_still_parsed` guard).

**Validation evidence:**
- Pristine `iir` (sky130, RC): **106/106** checks pass (was 103; +3 truncation guards) — no false-FAIL; the
  new `ext.gate power` check passes on scalar-leakage sky130; the RC floor + congestion-else correctly SKIP.
- E2E injections on a copy of correct `iir`: NaN-ing the congestion label → `ext.congestion 292/292
  mismatched` + `y1 non-NaN count` FAILs (97/102); truncating `nodes_gate.csv` 292→242 → truncation-guard
  FAIL (86/106); simulated de-escape regression → RC floor 0% → FAIL, 100% → PASS.
- Full def-graph suite: **312 passed, 14 skipped, 0 failed**.

---

## 2026-07-08 addendum — comprehensive three-dimension verification

Extends this audit's "a check that cannot fail is a silent lie" philosophy from
patching individual blind spots to **organizing the whole harness into three named
check groups** (`topology_checks` / `feature_stat_checks` / `signoff_report_checks`
in `verify_graph_dataset.py`, +750 LOC), closing the gaps where the historical
checks covered only variant **b** or never cross-checked a sign-off artifact.

**Topology (`top.*`) — all five views b–f, not just b:** symmetry, self-loop ban,
per-block `node_name` uniqueness on c/d/e/f; **block-positional node order** (the
guard that labels align by position, pin block included); the **`[fwd0,rev0,…]`
interleaving invariant** on c/d/e/f directed edges AND `rc_edge_*` for every view
(audit bug #5); **d/e edge_attr content** (completing c/f). A stale pre-RC dataset
(`edge_y` width 5, no `rc_edge_*`) now FAILs loudly instead of IndexErroring — the
guarded `edge_y width==6` / `rc_edge_* present` checks.

**Feature statistics (`feat.*`):** re-derives `placement_status_id` (DEF) and
`fanout` exactly; bounds `num_layer` / `nearest_tap_distance_um` (quirky worker
semantics pinned exactly on the synthetic fixture instead, to avoid a false-fail);
categorical vocab/enum coverage on the tensors; and a **stats-gate honesty** check
that independently recomputes every `features_stats.json` / `labels_stats.json`
distribution from the CURRENT CSVs (same `_percentile` as the gate) — catching a
stale or hand-edited stats JSON, a lie no prior check saw.

**Labels ↔ sign-off reports (`signoff.*`):** DRC/LVS clean-provenance gate; `ppa.json`
geometry (`io_count` exact, `macro_count`==DEF BLOCK instances, `sequential_count`
==liberty-`is_seq` — the fill-inflated `instance_count` deliberately NOT asserted, a
false-fail trap avoided by grounding every check in real counts first); the timing
label↔`6_final.sdc` clock-period transform (`Path_Delay==max(0,period−slack)`,
`label==log1p`); `C_total`∈[Σg+Σc, Σg+2Σc] and `equiv_res`≤ΣR vs an independent SPEF
re-parse. Opt-in **`--signoff-recheck`** re-runs OpenROAD PDNSim on `6_final.odb` to
re-derive the IR-drop label (the one label whose tool report is deleted on success);
honest SKIP (never a vacuous pass) when `OPENROAD_EXE` is absent — the new module-level
`skip()` records skips separately from passed/failed.

**Deferred BUG-6** (netlist reverse-edge / phantom-edge check) remains open.

**Regression tests (fail on pre-fix code):** `tests/test_verify_comprehensive.py`
— 19 tests, every group-level check exercised CLEAN and CORRUPTED (each proven to
FAIL on a deliberate corruption), driving the REAL graph builder + REAL verifier on
a synthetic RC-complete mini-design. Hermetic (stubs `resolve_platform_files`, no CWD
pollution). `read_def_truth` now also captures placement `status`;
`test_verify_graph_dataset_helpers.py` updated for the new key.

**Validation evidence:**
- `iir` (sky130, RC): **167/167** (168/168 with `--signoff-recheck` — the PDNSim
  re-run's re-derived IR drop matched the CSV within 5%). `DMA_Controller_DMA_fsm`:
  **164/164** after regeneration (was a stale pre-RC dataset the new topology guards
  flagged — regenerated labels→features→graphs to the width-6 + RC schema).
- E2E injections (negative controls) on real `iir` tensors/reports: swapped node
  order → `top.d node_name block-positional order` FAIL; shuffled edge_attr → `top.c
  interleaved` FAIL; injected self-loop → `top.f` symmetry+self-loop+interleave FAIL;
  tampered `features_stats.json` mean / stale row count → stats-honesty FAIL; DRC
  status `violations` → provenance FAIL; SDC period drift → timing-transform FAIL;
  `equiv_res`×1000 (ohm↔kΩ unit bug) → SPEF-bound FAIL.
- Full def-graph suite: **331 passed, 14 skipped, 0 failed**.
