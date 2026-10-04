# Repair-Needed RTL Pool: Real-Probe Results

Date: 2026-08-11  
Status: construction method validated; prospective benchmark pool not frozen

## Scope

This round tested whether a repair-needed RTL pool can be built with reproducible physical evidence instead of
selecting designs merely because they once failed. No production Agent code was changed. All additions are
experimental runners, validators, tests, registries, evidence, and documentation.

A task is admitted only when two distinct baseline runs reproduce the same non-environment failure and an
independent witness changes only an allowlisted family-specific effect, reaches fixed-target strict physical
signoff, and preserves source, SDC, task, check-set, run, report, and artifact provenance.

`fixed_target_physical_signoff` requires complete ORFS stages plus clean route, full DRC, LVS, setup/hold,
antenna, and RCX. It is intentionally separate from graph `publication_strict_clean`, which additionally requires
acquisition and Fmax/constraint provenance.

## Environment

| Component | Frozen value |
|---|---|
| R2G | `1915dc9b7fde2783637bde3d51fadeca44679c83` |
| ORFS | `a5ff7ef7dac4338e6e5fad7710b85fc6c8f3503c` |
| OpenROAD | `26Q3-318-g6b9d7fb806` |
| Yosys | `0.64-8449dd470` |
| KLayout | `0.29.12` |
| Netgen | `1.5.272` |
| Magic | `8.3.464` |
| Primary platform | Sky130HD; Nangate45 used for antenna evidence |

## Support Matrix

| Failure family | Verified independent designs | Claim threshold | Result |
|---|---:|---:|---|
| Footprint/congestion | 6 | 3 | Met |
| Pin perimeter | 3 | 3 | Met |
| Rule-specific DRC | 3 | 3 | Met |
| Timing closure | 2 | 3 | Development evidence only |
| Antenna closure | 3 | 3 | Met |
| PDN/floorplan | 1 | 3 | Development evidence only |

The 12 new evidence records use schema `repair-family-evidence-1.1`. The six footprint pairs remain in the
previously validated inventory and are reported separately rather than being relabeled as new-format records.
Meeting the table threshold establishes construction support, not Agent generalization; the final evaluation must
still use repository- and design-family-disjoint splits. In particular, the three pin designs span two repositories.

## What the Real Runs Proved

- Pin capacity: three designs reproduced `PPL-0024` twice and were repaired with bounded perimeter geometry.
- Rule-specific DRC: three designs reproduced `m3.2` with otherwise-clean signoff; a right-edge pin exclusion
  cleared DRC without changing footprint, RTL, frequency, or deck.
- Antenna: three Nangate45 designs reproduced antenna-only DRC and became fully clean through bounded antenna
  repair controls.
- Timing: AXI at 460 MHz and JESD204B at 172 MHz reproduced setup failure twice. JESD204B changed from
  `-0.0160414 ns` to `+0.081974 ns` using `SETUP_SLACK_MARGIN=0.2`, with every non-target gate clean.
- PDN: one real `PDN-0185` task was repaired using a geometry derived from
  `2 * offset + total strap width`, not an arbitrary oversized die.

## Rejections and Near Misses

- A historical `simple_i2c` antenna failure is now baseline-clean and was rejected as stale.
- `axil_interconnect` antenna evidence was rejected because `PPL-0024` was concurrent.
- `udp_mux` improved from 272 antenna violations to 6 but did not reach strict clean.
- SDRAM remained timing-clean at both 286 and 310 MHz; it was not admitted, and no post-hoc sweep was used to
  force a challenge.
- Blind PDN selection based on small RTL size had poor yield; real `PDN-0185` logs and numeric geometry are the
  reliable nomination source.
- A uniform 40% reverse challenge admitted 0/8 clean parents, showing that a single fixed stress strength is not
  a defensible pool-expansion method.

## Practical Discovery Strategy

1. Use historical memory only to nominate candidates, then rerun on the frozen current toolchain.
2. Rank cheaply before full signoff: pin demand/perimeter, routed congestion and DRC class, post-route WNS,
   antenna-only markers, or parsed PDN width requirements.
3. Run two independent baselines only for high-risk candidates.
4. Apply one preregistered family-specific action and require a globally clean witness.
5. Admit, quarantine, or label sentinel/near-miss through the machine validator; never fill quotas manually.

This action space follows the official OpenROAD interfaces for
[pin placement](https://openroad.readthedocs.io/en/latest/main/src/ppl/README.html),
[timing repair](https://openroad.readthedocs.io/en/latest/main/src/rsz/README.html), and
[antenna-aware global routing](https://openroad.readthedocs.io/en/latest/main/src/grt/README.html), while the
exact experiment knobs are frozen in the local family registry.

## Infrastructure Validation

- 30 focused tests passed.
- Registry lint passed.
- Collection-level support audit passed; counts are derived by unique `(repo, commit, top)` identity and checked
  against the registry rather than trusted as manually maintained totals.
- All 12 schema-1.1 evidence records passed the fail-closed validator.
- Probe execution now re-hashes frozen source/config/SDC artifacts before run and collection.
- Parallel execution preserves `NUM_CORES` and no longer misuses `ORFS_MAX_CPUS` as a worker count.

## Conclusion

The hard part is no longer “how to recognize one repair-needed RTL.” There is now a reproducible six-family
construction system with real positive and negative evidence. Four families meet the three-design claim
threshold; timing and PDN remain honestly limited to development evidence.

Before freezing the paper benchmark, run one outcome-unknown prospective funnel, report yield and cost with
uncertainty, add one timing and two PDN pairs or narrow those claims, and freeze repository/design-family-disjoint
splits for development, Experiment 2, and Experiment 3.
