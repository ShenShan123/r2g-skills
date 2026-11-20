# Experiment 4 (conversion): R2GSkills def-graph vs an LLM that writes its own converter

Frozen 2026-11-07 before any LLM development run started. Changes after that point are listed
in RESULTS.md under "Deviations".

**Question.** Given the same signed-off-layout files, how does R2GSkills' `def-graph` skill
compare with a model that writes its own conversion script, in (a) dataset correctness,
(b) conversion success rate, and (c) resource use (model calls, tokens, wall time, USD)?

## 1. Arms

| Arm | Who produces the dataset | Model calls |
|---|---|---|
| R2G | the def-graph skill (`run_graphs.sh`, which runs its label and feature stages) at the frozen repo commit, through a thin documented project shim (§4) | 0 |
| DeepSeek-only | a converter written and debugged by `deepseek-flash` (§5), then frozen and run alone | 100 cap, development only |
| GPT-only | the same, by `gpt-6.1-sol` | 100 cap, development only |

The models and routes are those of Exp 3 (R2G vs model-only signoff): DeepSeek through the
216 relay `http://127.0.0.1:18774/v1` (Chat Completions, thinking on), GPT through
`http://127.0.0.1:18775/v1` (OpenAI Responses protocol, served by OpenRouter at list prices).

## 2. Protocol: develop once, then apply

1. **Development** (LLM arms): each model writes ONE converter, working on 6 development
   designs (`exp4_validation/dev_designs.json`, 2 small / 2 medium / 2 large, disjoint from the
   test sample), within the budget of §5. It may run the public format checker; it never sees
   ground truth or a score.
2. **Freeze**: the harness copies `/work/converter`, tars it deterministically, records the
   sha256 and makes the copy read-only. No edit after freezing.
3. **Application**: the frozen converter is run, with no model and no network, on every test
   design in a fresh container (§6). R2G is applied the same way to the dev and test designs.
4. **Scoring**: all three arms' outputs are scored by the same checker (§7).

Test designs: exactly the 43 of `exp4_validation/sample.json:v1_all` (frozen by the V1
ground-truth study; 5 small, 13 medium, 25 large). Development: the 6 of `dev_designs.json`.

## 3. Scope and target format

Target: the def-graph skill's standard graph views as documented in `def-graph/SKILL.md` and
`references/{graph-dataset,feature-extraction,label-extraction}.md`. The public specification
the models receive is `spec/CONTRACT.md`. It states WHAT every tensor holds (fields, shapes,
units, node sets and ordering, label definitions) and contains no R2G code. Where the docs do
not pin a definition (e.g. the I/O-pin keep rule, the GCell index of a bbox edge), the
behaviour was read from the skill's code and restated as a definition; these are the same
"definition-from-code" points the V1 study lists. The contract was checked against R2G's
actual output on two development designs before any LLM run (all 20 groups pass, §7).

**Subset in scope** (identical for every arm):
- views **b** (gate/net/iopin/pin nodes, bipartite incidence edges) and **c** (pins folded
  into gate–net edges carrying pin features), in the homogeneous `torch_geometric.data.Data`
  form (`R2G_GRAPH_KIND=homo`, the skill's verified source of truth; the default HeteroData is a
  value-preserving re-view of it);
- all node features of those views (`x[N,10]`), c's edge features/labels;
- labels **congestion** (y1), **timing** (y3), **wirelength** (y4), **RC ground capacitance**
  (y5), each with its raw twin (`y_raw`).

Out of scope: IR drop (y2; needs a PDNSim set-up whose result is not reproducible from the
contract alone at the same precision), coupling capacitance and equivalent resistance
(`rc_edge_*`; the latter needs an RC-tree reduction per pin pair), graph-level features
(`global_feat`, reads config.mk), and views d/e/f.

Why b and c: d, e and f are deterministic re-foldings (cliques) of exactly the entities and
features b already contains — given a correct b they are bookkeeping, and they multiply
edge counts (Σ C(k,2) per net) without adding any new extraction from the raw files; c is
kept as the one folded view so that folding itself is exercised. Every raw-file extraction
the skill performs for b–f (DEF connectivity, LEF geometry, Liberty, SPEF, routing,
STA) is exercised by b+c with the four labels.

## 4. R2G arm: the project shim (documented, no gate bypass)

The pilot500 directories are bare ORFS outputs; def-graph expects an ORFS project. The shim
(`r2g_arm/make_project.sh`) builds the smallest project the skill's documented
reference-DEF path accepts: `constraints/config.mk` (DESIGN_NAME from the run's config.mk,
PLATFORM=nangate45), `constraints/constraint.sdc`, `inputs/6_final.def` (= 5_route.def),
`inputs/6_final.spef`, `inputs/6_final.sdc` (= constraint.sdc; the timing stage reads the SDC
beside the DEF) and `inputs/6_final.odb` (OpenROAD `read_lef` + `read_def` of that same DEF +
`write_db`; the timing and IR stages read an ODB). Knobs: `R2G_DEF/R2G_ODB/R2G_SPEF` (pin the
layout), `R2G_GRAPH_KIND=homo`, `R2G_GRAPH_VARIANTS=bc`. The shim time is reported with the
R2G time.

**Signoff gate.** `pilot500/README.txt` states that this re-run was **not** DRC/LVS checked
("签核：本次未做 DRC / LVS 检查"; `result.json: "signoff": "not run"`); the designs come from
pilot runs that were DRC/LVS clean, but these layouts are new. So no drc/lvs report exists
and none is fabricated. The skill's gate runs, records its verdict and blockers in
`reports/signoff_gate.json` and the manifest's `signoff_health` (`dirty`), and — by the
skill's own documented rule that an explicit `R2G_DEF` override downgrades the gate to warn —
the build proceeds. The gate is not disabled. The same layouts go to every arm.

## 5. LLM development: environment, caps, feedback

- Harness: `harness/run_dev_agent.py`, on 216, modelled on `env_install/run_install_agent.py`
  and the Exp 3 `bare_agent/run_bare.py` (same model adapters, usage normalisation,
  failed-invocation accounting with backoff, truncated-reply nudges, context compaction at
  120k characters keeping the 4 newest tool results, 12,000-character tool-result clip).
- Container: image `localhost/exp4c-conv:1` = `localhost/r2g-orfs:26Q3` (OpenROAD/OpenSTA/Yosys
  exactly as R2G uses them) + Python 3.13 with numpy 2.5.1, pandas 3.0.3, torch 2.13.0+cpu,
  torch_geometric 2.8.0 (the versions of def-graph's torch venv). `--network none`,
  64 GB memory, 8 CPUs. Read-only mounts: the 6 dev designs (only `5_route.def`,
  `6_final.spef`, `constraint.sdc`), the three platform files, the spec dir (CONTRACT.md,
  check_format.py). Writable: `/work`. The image contains no R2G repository and no def-graph
  code (checked: no graph_lib/build_graphs/run_graphs/verify_graph_dataset file anywhere).
- Tools: `shell` (bash in the container, default 600 s, max 3,600 s per command) and `finish`.
- Caps, identical for both models: **100 model calls**, **12 h wall**, max output tokens
  **32768** (Exp 3 harness default), **temperature 0.2**, no cost cap. The temperature is sent
  to DeepSeek; the GPT Responses route is a reasoning model that rejects `temperature`, so its
  adapter omits it (as in Exp 3) — recorded as `temperature_sent=false` in meta.json.
- Feedback: only `spec/check_format.py` (shapes, dtypes, required keys, block layout and
  sortedness, NaN placement, log1p twin identities, edge layout, b/c consistency). It never
  reads the design and knows no correct value. The ground-truth checker is not in the
  container and never shown to the model.

## 6. Application and conversion success

`harness/apply_arm.py`: one fresh `--network none` container per design (64 GB, 4 CPUs), the
three input files and the platform files read-only, an empty `/out`. LLM: `bash
/work/converter/run.sh /data/design /data/platform /out` with the frozen converter mounted
read-only. R2G: `r2g_arm/run_r2g_design.sh`. Each run is wrapped by `harness/measure.py`
(3,600 s limit, kills the process group on expiry; records exit code, wall, user+sys CPU,
peak RSS = ru_maxrss of the process tree).

**Conversion success** on a design = exit code 0, within 3,600 s, `b_graph.pt` and
`c_graph.pt` present, and the public format check passes. Correctness is scored for every
design whose files load, but only successful designs count as converted.

Host etiquette (216 runs the paper's main campaign): ≤ 6 extra containers at once; a design
starts only while the 1-min load < 85 and available memory > 200 GB.

## 7. Correctness scoring (same checker for every arm)

`scoring/defgraph_check.py`. Ground truth comes from the V1 study's format-independent module
`exp4_validation/gt_raw.py` (own DEF/LEF/Liberty/Verilog/SPEF parsers; sha256 recorded per
truth file) and `exp4_validation/match.py` (`set_compare`, `value_compare`). On top of gt_raw's
raw facts, `truth()` derives the CONTRACT quantities (net/pin classification and kept sets,
ids, oriented pin centres, the congestion grid with an own numpy reflect-Gaussian, path
delay from an own OpenSTA script `scoring/timing_truth.tcl` that iterates instances, not
pins). No arm's code is imported; R2G's `tools/verify_graph_dataset.py` is NOT the scorer
(it is part of the system under test). All comparisons are by name (backslashes removed).

Groups (a group passes on a design iff every entity matches; reported as match rate, or F1
for sets, pooled over test designs, plus the share of designs where the group passes):

| group | content | criterion |
|---|---|---|
| ID_gates / ID_nets / ID_iopins / ID_pins | b node sets | set F1 = 1 |
| ID_c_nodes | c node set (typed) | F1 = 1 |
| ORDER | b and c node order (block order, name-sorted) | exact |
| EDGES_b | multiset of directed typed name pairs | F1 = 1 |
| EDGES_c | multiset of directed (src, dst, edge_type) | F1 = 1 |
| F_gate_categorical | cell_type_id, orientation_id, placement_status_id (b and c) | exact |
| F_gate_liberty | cell_area, cell_power | atol 1e-6, rtol 1e-5 |
| F_gate_position | x_um, y_um | 1e-4, 1e-6 |
| F_net_integer | net_type_id, fanout, pin_count, num_drivers, num_sinks, connects_macro_flag, num_layer | exact |
| F_net_hpwl | hpwl_um | 6e-3, 1e-6 |
| F_iopin | pin_x/y_um, nearest_tap_distance_um (1e-4, 1e-6), pin_direction_id (exact) | |
| F_pin | b pin_type_id (exact), sum_pin_cap_fF (1e-6, 1e-5) | |
| F_c_pin_edges | c edge_attr per (gate, net) multiset | as F_pin |
| L_congestion | y1, y_raw1 on gates (b, c) | 1e-6, 1e-5 |
| L_timing | y3, y_raw3 on b pins; c edge_y3, edge_y_raw3 | 1e-4 ns, 1e-6 |
| L_wirelength | y4 (1e-6, 1e-6), y_raw4 (2e-4 µm, 1e-6) on nets (b, c) | |
| L_ground_cap | y5, y_raw5 on nets (b, c); NaN iff no *D_NET | 1e-5, 1e-5 |

Tolerances are float32-storage level and wide enough to absorb R2G's own CSV rounding
(hpwl 2 decimals, wirelength 4, timing 6), so a correct converter that does not round passes
too. They are published in CONTRACT.md §9. (V1's own tolerances assume float64 labels and do
not apply to a float32 format.) The scorer has negative controls (`tests/negative_controls.py`:
11 corruptions, each fails its intended group; the clean output passes all groups).

A design **fully passes** iff its conversion succeeded and all 20 groups pass.

## 8. Resource metrics and cost

Development (per LLM arm): model calls, failed invocations, provider-reported tokens
(input, cached input, output, reasoning), wall seconds, USD:
- DeepSeek `deepseek-flash` at peak CNY prices per 1M tokens: uncached input 2, cache-hit
  input 0.04, output 8; USD = CNY / 7.10.
- GPT `gpt-6.1-sol` at list USD per 1M: input 2.00, cached input 0.10, output 10.00
  (reasoning tokens are part of output).
Per design (all arms): conversion wall seconds, CPU seconds, peak RSS, success. R2G: 0 model
calls, $0; its per-design time includes the shim.

## 9. Amendments after the freeze (coordinator instructions, 2026-11-07; recorded, not hidden)

1. **Host limits changed** (09:2x): new containers only while 216's 1-min load < 110 and
   MemAvailable > 200 GB, at most ~4 extra containers. All launchers/apply runs use these gates.
   216 is rootless cgroups v1, so `podman --memory/--cpus` are silently ignored there; thread
   pools are capped through `OMP_NUM_THREADS/MKL_NUM_THREADS/OPENBLAS_NUM_THREADS` instead
   (dev containers 8, application containers 4), identically for every arm.
2. **R2G arm deferred**: def-graph is being debugged by the validation agent. The R2G arm is
   rebuilt from the FIXED def-graph working tree once it lands (its sha/diff recorded). The 14
   designs already run from git HEAD 48bd9970 are kept as `r2g/apply_PREFIX_partial_stopped/`
   and are not scored as the R2G arm.
3. **Scoring adapter**: the validation agent's `exp4_validation/adapter_defgraph.py` is the
   adapter of record for the graph comparison; `scoring/defgraph_check.py` (written here first)
   is kept aside and reconciled with it.
4. **New task: image conversion** (`spec_image/CONTRACT_IMAGE.md`, `check_format_image.py`).
   def-graph's image product (SKILL.md §5, `scripts/extract/images/build_images.py`): five
   feature maps from `3_place.def` (cell_density, macro_region, rudy, pin_rudy, pin_density) and
   the congestion labels (congestion, congestion_raw) on the routed DEF's GCell grid, arrays
   `[x][y]`. **All power-related channels are excluded** (user rule): the `ir_drop` label is
   dropped (def-graph has no power feature channel). Inputs: `3_place.def`, `5_route.def` +
   platform files. Same protocol, caps, feedback rule (format checker only), dev and test
   designs; one separate development run per model (`harness/run_dev_agent.py --task image`).
   The contract was checked against def-graph's `build_images.py` (git HEAD) on two dev designs
   with an independent re-derivation (`scoring/image_check.py`): all 7 arrays match. The R2G
   image arm uses def-graph's `labels.npz` with `ir_drop` removed (the only post-processing).
