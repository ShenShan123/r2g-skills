# Experiment 4 Pure-LLM Converter Prompt

You are implementing a general, deterministic Python converter for a frozen
physical-design graph contract. Your program is developed on six public
development cases and is later executed unchanged on 24 hidden cases.

Return exactly this delimiter envelope, with no Markdown fence or text outside it:

```text
===SUMMARY_BEGIN===
A concise description of the implementation or revision.
===SUMMARY_END===
===PYTHON_SOURCE_BEGIN===
The complete replacement Python program.
===PYTHON_SOURCE_END===
```

This transport deliberately does not embed source in JSON. Do not JSON-escape
the source and do not omit either closing delimiter.

The program is invoked as:

```text
python converter.py --config /absolute/path/to/method_config.json
```

The config follows `r2g2_four_stage_sample_v1`. The supplied machine-readable
public contract gives every exact config key and type, every required output
path and CSV header, and every required graph/store attribute and tensor shape.
Use those names literally; do not invent aliases such as `netlist_path`.
The converter must create the complete
`r2g2_four_stage_hetero_pipeline_v3` output expected by the supplied public
contract. The four stages are `floorplan`, `placement`, `cts`, and `route`.

Hard constraints:

1. Use only the files named by the config and installed Python packages.
2. Do not use the network, subprocesses, shell commands, or any R2G source.
3. Do not special-case a design, task ID, path, repository, or mapped-cell count.
4. Preserve causal stage boundaries: route DEF, SPEF, and timing reports are
   labels only and cannot leak into an earlier-stage input feature.
5. Write deterministic outputs. A rerun with identical inputs must be equal.
6. Missing physical values must use NaN plus an explicit validity mask; do not
   fabricate zero-valued measurements.
7. The response always contains the full replacement source, not a patch.
8. Treat `PUBLIC_CONTRACT_V2.json` as authoritative when prose is less exact.

After each development round you receive bounded, structured development-only
feedback: aggregate pass counts, a complete static-contract score, bounded
static-contract issues, validator checks, normalized exception signatures,
missing output paths relative to `output_dir`, structural issue counts, runtime,
and output size. The static linter reports independent missing products and
schema defects together instead of stopping at the first exception. Use those
diagnostics to replace the complete converter source in the next round. You
never receive hidden-test identities, paths, inputs, or results before the
source is frozen.
