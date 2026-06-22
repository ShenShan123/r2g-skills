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

### Route Results (`reports/route.json`)
Emitted by `extract_route.py` so a detailed-route abort can be read like a signoff symptom:
- `status`: clean / fail / timeout / unknown
- `total_violations`: residual DRT DRC marker count (0 on a clean route)
- `completed`: whether the route stage exited 0
- `route_stage_status`: route-stage exit code (124/137 = wall-clock timeout / OOM)
- `backend_run`: the backend `RUN_*` dir the verdict came from

### Fmax Search (`reports/fmax_search.json`)
Produced by `scripts/reports/fmax_search.py` (see `orfs-playbook.md` "Fmax Search") — the fastest
clock period a design can close at, found with a place-stage proxy. Top level: `status` (`ok` or a
non-ok reason), `design`, `platform`, `seed_period`, `model_provenance`, `place_fast`, `labels`,
and (only when `status == "ok"`) a `winner` block:
- `winner.fmax_predicted_signoff` (GHz) + `winner.period` (ns) — the headline number, the
  model-corrected predicted-signoff Fmax at the tightest closing period.
- `winner.fmax_place_proxy` — the raw post-place crossover Fmax, before deterioration correction.
- `model_provenance` — which deterioration model produced the prediction (e.g. `default-static` or
  a learned per-family model).
- `labels` — a list of human-readable strings, including the `[proxy, UNVERIFIED]` trust tag. **The
  predicted Fmax is UNVERIFIED** (it is a place-proxy + model estimate) unless a `--verify` full
  flow was run at the winning period — never read it as a signed-off number. See `orfs-playbook.md`
  "Fmax Search" for the `--verify` path and the proxy/model details.

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
| `extract_route.py` | project root | `route.json` | route status (clean/fail/timeout), residual DRT DRC count, stage exit code |
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
