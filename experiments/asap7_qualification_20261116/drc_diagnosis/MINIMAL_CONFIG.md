# Fully Signed-Off GCD ASAP7 Configuration

Accepted project: ../gcd_pin_window_v2; ORFS variant asap7_pin_window_v2_20261116. The pinned OpenROAD/ORFS and RVT library complete all 334 DRC checks with zero results and LVS CORRECT. This qualifies this GCD configuration, not every design or corner.

## Changes from standard GCD

```make
export VIA_IN_PIN_MIN_LAYER = M1
export VIA_IN_PIN_MAX_LAYER = M1
override export TECH_LEF = /home/yangao/r2g-skills/experiments/asap7_qualification_20261116/drc_diagnosis/asap7_tech_asym_v1_m4_only_candidate.lef
export SC_LEF = /home/yangao/r2g-skills/experiments/asap7_qualification_20261116/drc_diagnosis/asap7sc7p5t_R_edge_pin_window_candidate.lef
export PDN_TCL = /home/yangao/r2g-skills/experiments/asap7_qualification_20261116/gcd_pin_window_v2/constraints/pdn.tcl
```

The project contains full configuration, RTL, and SDC. Official GDS, Liberty, and complete PEX source CDL remain unchanged. No extra DONT_USE restrictions, global M1 spacing/EOL tightening, or OpenROAD binary patch is required for this GCD.

## Reproduction

Run from the repository root with a unique variant to preserve archived results:

```bash
R2G_ALLOW_UNSUPPORTED_PLATFORM=1 ORFS_MAX_CPUS=4 ORFS_TIMEOUT=1200 \
bash r2g-skills/signoff-loop/scripts/flow/run_orfs.sh \
  experiments/asap7_qualification_20261116/gcd_pin_window_v2 asap7 \
  asap7_pin_window_v2_recheck
```

R2G_ALLOW_UNSUPPORTED_PLATFORM=1 is an isolated qualification override; normal platform support remains gated. Production integration must adopt the passing configuration deliberately.

Inputs: ../gcd_pin_window_v2_input/{drc.gds,lvs.gds,source.cdl}. DRC uses all 334 rules in flat mode, four zero offsets, and unchanged official rules. Transistor LVS uses ../run_check.py. Initial property limit 100000 was independently rechecked at 1024, still CORRECT; the latter is accepted.

```bash
QUAL_PROJECT=gcd_pin_window_v2 /usr/bin/klayout -b -r \
  experiments/asap7_qualification_20261116/verify_geometry.py
/usr/bin/klayout -b -r \
  experiments/asap7_qualification_20261116/drc_diagnosis/verify_edge_pin_window.py
```

These checks verify output/signoff GDS geometry equality and unchanged M1 pin+OBS unions across 212 cells. Native PPA time is ps; divide by 1000 for ns.
