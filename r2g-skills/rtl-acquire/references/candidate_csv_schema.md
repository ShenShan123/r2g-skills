# Candidate CSV Schema

This file documents the intended schema for candidate CSV inputs consumed by the expansion workflow.

It complements the shorter contract in `SKILL.md` with more operational detail.

## Required Columns

- `source`
  - source family or dataset label

- `design`
  - stable unique design identifier for this expansion round

- `priority`
  - typical values: `high`, `medium`, `low`

- `expected_top`
  - expected synthesis top module

- `source_path`
  - main RTL file path or anchor file path for the bundle

## Optional Columns

- `rtl_files`
  - `;` or `|` separated file list
  - use for bundle-aware synthesis instead of relying on blind discovery

- `include_dirs`
  - `;` or `|` separated include directories

- `synth_variant`
  - e.g. `yosys_abc_area0`, `yosys_abc_area1`

- `synth_memory_max_bits`
  - override memory limit for memory-heavy designs

- `synth_frontend`
  - e.g. `slang`

- `top_parameters`
  - `PARAM=VALUE` pairs separated by `;` or `,`
  - written into synthesis via `chparam`

- `resource_tier`
  - use `high` for memory-heavy or long-tail large designs

- `notes`
  - free-form human annotation

## Field Guidance

### `design`

- must stay stable across retries
- should encode variant only when the graph is intentionally distinct
- avoid random suffixes that break audit continuity

### `expected_top`

- do not leave this ambiguous for multi-module repos
- if the repo is parameterized, align `expected_top` and `top_parameters`

### `rtl_files`

- prefer explicit bundles for non-trivial repos
- do not rely on accidental file order in large repos

### `resource_tier`

- set to `high` when the design is already known to be:
  - memory-heavy
  - million-node scale
  - high retry cost

## Anti-Patterns

- duplicate `design` names pointing to different source bundles
- `expected_top` copied from repo name without verification
- missing `rtl_files` for large multi-directory repos
- using `notes` as a substitute for structured fields

## Good Example

```csv
source,design,priority,expected_top,source_path,rtl_files,include_dirs,synth_variant,resource_tier,notes
other_external,verilog_ethernet_udp_64,high,udp_complete_64,$HOME/work/_downloads/verilog-ethernet/rtl/udp_complete_64.v,$HOME/work/_downloads/verilog-ethernet/rtl/udp_complete_64.v|$HOME/work/_downloads/verilog-ethernet/rtl/udp_ip_tx_64.v|$HOME/work/_downloads/verilog-ethernet/rtl/udp_ip_rx_64.v,$HOME/work/_downloads/verilog-ethernet/rtl,yosys_abc_area0,high,large ethernet datapath bundle
```

## Use

This file is the right place to evolve CSV semantics and examples.

Do not bury schema decisions only inside code or ad hoc round notes.
