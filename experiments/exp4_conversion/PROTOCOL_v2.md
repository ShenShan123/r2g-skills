# Experiment 4 (conversion) v2: full def-graph product vs LLM-written converters

Fixed 2026-11-07, before any v2 run, from the design the user approved. The scoped v1 study
(`PROTOCOL.md`, `RESULTS.md`) stays as a separate reported data point; nothing of it is deleted.

## 1. Arms

| Arm | Producer | Model calls |
|---|---|---|
| R2G | def-graph from the fixed 208 working tree (code identity recorded again, `r2g_arm/identity_v2/`) | 0 |
| DeepSeek | `deepseek-flash` (216 relay :18774, Chat Completions, thinking on, temperature 0.2) | ≤ 200 per development run |
| GPT | `gpt-6.1-sol` via OpenRouter (216 relay :18775, Responses; no temperature, the route rejects it) | ≤ 200 per development run |

## 2. Task (identical for every arm)

**Graphs — the full def-graph graph product:**
- views **b, c, d, e, f**, both forms def-graph emits (`R2G_GRAPH_KIND=both`): heterogeneous
  `HeteroData` `{v}_graph.pt` and homogeneous `Data` `{v}_graph_homo.pt`;
- node features; folded-entity edge features (`edge_attr`/`edge_y`, interleaved fwd/rev);
- labels wirelength, congestion, timing (raw twins included); RC parasitics from the SPEF —
  ground capacitance as a node label, coupling capacitance and equivalent resistance as the
  separate `rc_edge_*` edge set;
- the **four-stage causal graphs** (floorplan / placement / CTS / route `HeteroData` on the
  `1_2_yosys.v` canonical topology, cross-stage net mapping, shared post-route labels) —
  **Nangate45 only**.

**Images:** exactly as v1 (`spec_image/CONTRACT_IMAGE.md`'s arrays: five feature maps from the
placement DEF, `congestion` and `congestion_raw`).

**Excluded: power** — IR drop (graph `y2`, four-stage `gate.ir_drop_mV`, image `ir_drop`) and
every power feature/label. Such slots may hold anything; they are never scored. Also not in the
task list and not scored: `netlist_graph.pt`, manifests and graph-level metadata files.

## 3. What the LLM receives

- A short task statement (`harness/run_dev_agent.py --task full`): interface, inputs, output
  layout, budget, the power exclusion, "no ground truth is available".
- def-graph's OWN public docs, unmodified except that passages naming R2G script paths or
  commands are stripped (`spec_v2/redact_docs.py`, log in `spec_v2/docs/REDACTION_LOG.json`):
  `SKILL.md`, `references/graph-dataset.md`, `feature-extraction.md`, `label-extraction.md`,
  `four-stage-dataset.md`, **plus** `scripts/r2g2/upstream_docs/B_VIEW_DATASET_STRUCTURE.md`.
  The last one is added because `four-stage-dataset.md` names it "the authoritative
  per-dimension contract" of the four-stage product: without it the four-stage tensors are not
  specified anywhere the model can read. It is a document, not code. **No new pseudocode
  contract is written.**
- The public format checker `spec_v2/check_format_v2.py` (loadability, required keys, shapes,
  dtypes, node/edge store layout, NaN ↔ mask conventions, fwd/rev interleaving, cross-form
  consistency). It never reads ground truth.
- Platform files for nangate45, sky130hd and gf180: the ORFS platform directories inside the
  ORFS image (`/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/<platform>`), as R2G uses them.
- No R2G code, no built dataset, no test design in the container (the image is checked to
  contain no def-graph code; the OpenROAD/OpenSTA/Yosys sources and ORFS flow scripts are
  tool sources and present, as in v1).

## 4. Interface, inputs and output layout

`bash <converter>/run.sh <design_dir> <platform_dir> <out_dir>`

`<design_dir>` (read-only; identical layout for every arm and platform):

| file | Nangate45 (pilot500) | SKY130HD / GF180 (reruns) |
|---|---|---|
| `config.mk` | run's own | run's own |
| `constraint.sdc` | run's own | run's own |
| `6_final.sdc` | copy of constraint.sdc (pilot500 has none) | run's own |
| `6_final.def` | `5_route.def` (final routed + filled layout) | run's own |
| `6_final.spef` | run's own | run's own |
| `3_place.def` | run's own | `write_def` of the run's `3_place.odb` (staged once, sha256 recorded) |
| `1_2_yosys.v`, `2_floorplan.def`, `4_cts.def`, `5_route.def` | run's own (four-stage inputs) | — |

`<platform_dir>` = the ORFS platform directory of the design's `PLATFORM` (config.mk).

`<out_dir>` must receive:
```
dataset/{b,c,d,e,f}_graph.pt        HeteroData (def-graph's default form)
dataset/{b,c,d,e,f}_graph_homo.pt   homogeneous Data
four_stage/generated/stages/{floorplan,placement,cts,route}/heterograph.pt   (Nangate45 only)
images/features.npz, images/labels.npz
```
(def-graph's own output layout, so the shared adapter reads every arm the same way.)

## 5. Development (LLM arms)

- 6 Nangate45 development designs only (`exp4_validation/dev_designs.json`), staged in the
  layout above.
- **3 independent runs per model**, each in a fresh container with its own run seed
  (seeds 1, 2, 3; the seed is recorded and stated in the task header — the harness's model
  adapters expose no sampling-seed parameter, so independence comes from sampling (DeepSeek
  temperature 0.2, GPT default) plus the differing header).
- Caps per run, identical for both models: **200 model calls, 12 h wall**, max output tokens
  32768, no cost cap. Harness otherwise as v1 (compaction, clipping, retry, nudges).
- Each run delivers one converter (`run.sh`; it may call several scripts) covering graphs,
  four-stage and images. Frozen at run end: deterministic tar + sha256, read-only copy.
- Feedback: the format checker only.

## 6. Test designs and application

- `exp4_validation/sample_v2.json` (written by the validation agent): 30 designs per platform,
  Nangate45 (pilot500), SKY130HD (`rerun_sky130hd_20261022`), GF180 (`rerun_gf180_20261026`).
  Inputs staged on 216 from those trees (Nangate45 from the v1 copy of pilot500), sha256 recorded.
- Every frozen converter runs UNCHANGED on all 90 test designs: fresh `--network none`
  container, inputs read-only, 3,600 s per design (graphs + four-stage + images in one run).
- R2G runs def-graph on the same 90 designs (graphs both forms, images; four-stage on the 30
  Nangate45) through a documented shim that builds an ORFS backend run (ODBs by read_def +
  write_db of the same DEFs). Signoff gate in `warn` (no DRC/LVS reports exist for these
  re-run layouts on Nangate45; recorded per design, not counted against conversion).
- **Conversion success** per task: the run ends by itself within 3,600 s (not killed) and that
  task's files are present and pass the public format check for that task (graphs, four-stage,
  images judged separately, so a crash in one product does not void another that was written
  completely). The run's exit code is recorded.

## 7. Scoring

Only with the validation agent's extended shared checker (`exp4_validation/`), once it reports
the checker ready (proven on R2G first). Snapshot sha256 recorded. My own scorers are not used for
the v2 numbers. Reported, per arm × platform × task:
- conversion success; per-check-group accuracy (pooled match rate / F1) and share of designs
  passing each group; full-pass share per design;
- for each model: mean and range over its 3 development runs, plus best and worst run;
- development calls, tokens, USD (DeepSeek peak CNY 2 / 0.04 / 8 per 1M ÷ 7.10; GPT list USD
  2.00 / 0.10 / 10.00 per 1M), dev wall time; per-design conversion wall time, peak RSS;
- failure classes, each with one concrete example.

## 8. Resources (216)

≤ 6 extra containers in total; a container starts only while the 1-min load < 110 and
MemAvailable > 200 GB. `--memory/--cpus` are ignored on 216 (rootless cgroups v1): thread pools
capped by environment variables (dev 8, application 4), identically for all arms.

## 9. Amendments during the run (recorded, not hidden)

1. **Contended host.** 216 ran the paper's main campaign and another user's jobs (1-min load 90–220
   during v2). Development wall times and per-design conversion times were therefore taken on a
   contended host and are **indicative only**, as in v1. The six development runs ran
   concurrently (launched 16:00–16:03 at load < 110); the heavy work (applying frozen converters
   to the 90 test designs, R2G builds, scoring) starts a container only while load < 110,
   MemAvailable > 200 GB and ≤ 6 exp4c containers run.
2. **Voided run gpt seed 2.** The development harness crashed at call 53 (UnicodeDecodeError while
   decoding binary shell output from a `.lib.gz`), not a model decision. The harness was fixed
   (`errors="replace"` when decoding tool output; no other change) and seed 2 was re-run from
   scratch with the same caps. The voided run is kept as `216:$W/v2/dev/VOID_gpt_s2_harness_crash/`.
3. **R2G arm rebuilt on the current tree.** def-graph changed again (failure-patterns #69–#72) after
   the first R2G v2 designs had been built; those 8 designs are kept as
   `216:$W/v2/apply/r2g_PRE_fix69_stopped/` and not scored; the arm was rebuilt from the
   re-deployed tree (`r2g_arm/identity_v2/`; previous identity in `identity_v2/pre_fix69/`).
   The LLM docs were redacted before that change; the only difference in the shipped docs is
   SKILL.md's count of vendored deltas ("seventeen (D1–D17)" vs "eighteen (D1–D18)").
4. **Shared-checker robustness amendment A10** (conversion agent, in
   `exp4_validation/adapter_defgraph.py`): a homogeneous graph whose `x_schema` is not a dict made
   the adapter crash (GPT seed 1 stores a column list); it now falls back to the documented
   column order, which is what the adapter already did when `x_schema` is absent. No truth or
   tolerance changed. Scoring snapshot: `216:$W/v1snap/v2_20261107_1719` (SHA256SUMS inside).
5. **Format-checker gap (known before scoring, not fixed — converters were already frozen).**
   `check_format_v2.py` did not require the four-stage node stores to carry the documented
   identity attributes (`inst_name`, `net_name`, `iopin_name`, `pin_name`) and per-store
   `x_schema`/`y_schema`, which the shared four-stage checker reads. A converter without them is
   reported as "four-stage not scorable", with this caveat.
6. **Shared-checker robustness amendment A12 (2026-11-09, after scoring, METHOD — no verdict changes).**
   `v2_defgraph_check.py` looked up node labels in the truth table with a bare dict index, so a node the
   truth does not know (in practice a clock net — `clk_en`, `clkbuf_0_clk` — that the public spec
   excludes, or a renamed net such as `test_214`) raised a KeyError and the design's whole graph check
   produced no result: 193 of the LLM graph results (DeepSeek 102, GPT 91, R2G 0). The feature checks
   already treated an unknown node as a NaN truth. A12 makes the label lookups do the same: the unknown
   node is a FAILED position of its group (no NaN==NaN credit), recorded as `unknown_entities` with
   examples. Controls on 216 before the rescore: on the 4 already-scored arm results re-run with the
   patched checker (R2G C00115; GPT s2 + DeepSeek s1 C00210) every group's flags, counts, rates and
   errors are identical; only the order inside `*_examples` lists differs (frozenset hash order). On the
   2 crashed results tried (GPT s1, DeepSeek s1 C00115) it now scores (673/900 and 618/900 groups
   pass; 24 groups flag the unknown net `test_214`). Snapshot `216:$W/v1snap/v2_20261109_A12`
   (SHA256SUMS inside; only `v2_defgraph_check.py` differs from `v2_20261107_1719`). Rescore root
   `v2/score_A12` = a copy of `v2/score` in which only the results that were missing are computed;
   the 180 LLM four-stage results (all crashed on the missing identity attributes, item 5) are written
   as `not scorable` instead of being re-run.
7. **Summary fix (2026-11-09).** `summarize_v2.counts()` counted an all-or-nothing group that carries
   `n` but no `matched` (ordering, NaN placement) as 0 of n, so R2G's "mean group accuracy" read 0.73
   with 896/900 groups passing. Such groups now count as one pass/fail; the table also gives, per arm,
   the number of scorable designs and the median share of groups passing per design.
