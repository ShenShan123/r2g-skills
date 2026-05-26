# PPA Report Guide

When summarizing reports, extract only the most decision-useful metrics first.

## Synthesis Stage

Look for:

- Top module recognized
- Cell count
- Inferred sequential elements (flip-flops, latches)
- Obvious warning categories

## Backend Stage (ORFS)

Look for:

- Whether final GDS was generated in `results/<platform>/<design>/`
- Timing slack / WNS (Worst Negative Slack)
- Area and utilization
- Power breakdown (internal, switching, leakage)
- DRC violations
- Routing congestion

## Signoff Stage

### DRC Results (`reports/drc.json`)
- `status`: clean / fail / unknown
- `total_violations`: integer count
- `categories`: per-rule violation breakdown with counts and descriptions
- `log_info`: elapsed time, any error messages

### LVS Results (`reports/lvs.json`)
- `status`: clean / fail / skipped / unknown
- `mismatch_count`: number of netlist mismatches
- `lvsdb`: parsed comparison database details (net/device/pin counts)
- `log_info`: log-derived status, elapsed time

### RCX Results (`reports/rcx.json`)
- `status`: complete / empty / skipped / no_spef
- `net_count`: number of extracted nets
- `total_cap_ff`: total parasitic capacitance in femtofarads
- `total_res_ohm`: total parasitic resistance in ohms
- `cap_unit` / `res_unit`: units from SPEF header
- `header`: SPEF metadata (design name, date, vendor)
- `spef_size_bytes`: file size indicator
- `log_info`: extraction stats, elapsed time

## ORFS Report Files

ORFS generates reports in `reports/<platform>/<design>/`:
- Timing reports (setup/hold analysis)
- Area reports
- Power reports
- DRC reports (`6_drc.lyrdb`, `6_drc_count.rpt`)

Also check `logs/<platform>/<design>/` for per-stage logs.

## Extraction Scripts

| Script | Input | Output | Key Fields |
|--------|-------|--------|------------|
| `extract_ppa.py` | project root | `ppa.json` | area, timing, power, geometry |
| `extract_drc.py` | project root | `drc.json` | violations, categories |
| `extract_lvs.py` | project root | `lvs.json` | match status, mismatches |
| `extract_rcx.py` | project root | `rcx.json` | net count, cap (fF), res (Ohm) |
| `extract_progress.py` | project root | `progress.json` | per-stage completion |
| `build_diagnosis.py` | project root | `diagnosis.json` | issues, suggestions |

## Summary Format

Prefer the following structure:

- `status`: PASS / FAIL / PARTIAL
- `artifacts`: Key file paths (GDS, SPEF, reports)
- `metrics`: Small JSON-like block with key numbers
- `signoff`:
  - DRC: violation count (0 = clean)
  - LVS: match / mismatch / skipped
  - RCX: net count, total cap (fF), total res (Ohm)
- `blockers`: Short bullet list of issues
- `next_step`: One recommended action
