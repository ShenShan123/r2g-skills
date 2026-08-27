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

### Paths (`source_path`, `rtl_files`, `include_dirs`) — resolution contract (2026-07-10)

Discovery always emits absolute paths. Hand-authored CSVs may use `~`, `$VAR`
(e.g. `$HOME` as in the Good Example below), or relative paths — the expand
stage normalizes each entry CWD-independently:

1. `~` and `$VAR` are expanded;
2. an absolute path is used as-is;
3. a relative path binds to the first base where it exists — the **candidate
   CSV's own directory**, then the **repo root** — else deterministically to
   the CSV's directory (so the failure message shows a stable path).

Never depends on the caller's CWD (ORFS and later stages chdir freely).
Before 2026-07-10 none of this expansion happened and a `$HOME` example row
silently landed as `unsupported/missing_source_file`.

### `notes` risk flags

Discovery appends `risk_flags=<tok>+<tok>|none` to `notes`: tokenized
RAM/hard-macro markers (`common/rtl_risk.py`) unioned over the bundle. These
are **markers, not rejects** — the synth attempt arbitrates; the repair-side
classifier excludes only when the tokens appear in the FAILURE evidence.

### `resource_tier`

- set to `high` when the design is already known to be:
  - memory-heavy
  - million-node scale
  - high retry cost

## Provenance fields (2026-07-16 full-pipeline issue-report fixes)

These ride **`design_meta.json`** (stamped at expansion) and the **clone summary
CSV**, not the candidate CSV itself:

- `source_manifest` (design_meta) — `[{path,size,sha256}]` of the RTL **bytes**
  that earned the synth success (issue 1). `promote_candidates` re-digests every
  file against it and refuses `rtl_bytes_changed_since_synth` on mismatch;
  `source_digest` is the rollup sha256. `rtl_signature` keeps its old PATH-based
  semantics (it is a dedup key — do not repurpose it).
- `source_kind` / `source_commit` / `license_status` / `license_evidence`
  (design_meta; issue 2) — origin classification (`cloned_repo`/`local_tree`),
  `git rev-parse HEAD` of the clone, and the conservative license verdict
  (`allow|review|deny|unknown`). The publish gate is FAIL-CLOSED: only
  `allowed_license_status` (default `["allow"]`) publishes, and a `cloned_repo`
  candidate needs a non-empty commit (`require_source_commit`).
- Clone summary CSV gains `resolved_commit`, `license_status`,
  `license_evidence` columns (clone_repo_manifest).
- `notes` may carry `bundle_incomplete=<n>; unresolved=<mods>` (issue 10) when
  the dependency-closure cap truncated the bundle — the repair classifier turns
  a missing-module failure on such a candidate into `retry,missing_local_module`,
  never a permanent `low_value_failure` exclusion.
- The quality CSV gains `quality_notes` (issue 11):
  `stats_schema_missing:cell_histogram` marks a design whose quality assessment
  was BLOCKED (action forced `conditional`) because its stats predate the
  `cell_histogram` emission — never scored from fabricated zeros.

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
