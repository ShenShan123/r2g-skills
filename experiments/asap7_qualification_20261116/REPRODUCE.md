# Reproducing the Fully Signed-Off ASAP7 Example

These commands use the workspace on 208 and Calibre 2025.1_16.10, private rules, and the existing license on 216. Use new project directories and checker names for every reproduction to preserve accepted originals.

## Accepted inputs

- Project:`gcd_pin_window_v2`; backend run `RUN_2026-11-16_06-00-47_2002719_0f7f`.
- Technology configuration: project `constraints/config.mk` and `constraints/pdn.tcl`.
- TECH_LEF:`drc_diagnosis/asap7_tech_asym_v1_m4_only_candidate.lef`.
- SC_LEF:`drc_diagnosis/asap7sc7p5t_R_edge_pin_window_candidate.lef`.
- Independent transistor reference:`lvs_diagnosis/asap7_R_official_pex_full.cdl`.
- OpenROAD:`26Q3-318-g6b9d7fb806`; Yosys:`0.64`.

Candidate LEF and PDN overrides are project-local; shared platform files are unchanged. Generate the pin-access LEF with `drc_diagnosis/prepare_edge_pin_window.py` and verify its metal union with `drc_diagnosis/verify_edge_pin_window.py`. The directory REPORT.md explains the adaptations.

## Regeneration

Run from the repository root, replacing NEW_PROJECT with a new project name:

1. Copy `rtl/`, `tb/`, `input/`, `constraints/`, and `metadata.json` into a new project. Update project paths in its configuration; do not copy backend or reports. Retain both candidate LEF paths.
2. Run the complete physical flow:

```bash
PATH=/usr/bin:/bin:/home/yangao/.local/bin:$PATH \
R2G_ALLOW_UNSUPPORTED_PLATFORM=1 ORFS_MAX_CPUS=4 ORFS_TIMEOUT=1200 \
bash r2g-skills/signoff-loop/scripts/flow/run_orfs.sh \
  experiments/asap7_qualification_20261116/NEW_PROJECT asap7 NEW_VARIANT \
  > experiments/asap7_qualification_20261116/NEW_PROJECT/backend_driver.log 2>&1
```

3. Generate signoff inputs, PPA, and functional evidence:

```bash
QUAL_PROJECT=NEW_PROJECT /usr/bin/klayout -b -r experiments/asap7_qualification_20261116/prepare_variant_gds.py
/usr/bin/python3 experiments/asap7_qualification_20261116/prepare_variant_cdl.py NEW_PROJECT
QUAL_PROJECT=NEW_PROJECT /usr/bin/klayout -b -r experiments/asap7_qualification_20261116/verify_geometry.py
/usr/bin/python3 r2g-skills/signoff-loop/scripts/extract/extract_ppa.py experiments/asap7_qualification_20261116/NEW_PROJECT experiments/asap7_qualification_20261116/NEW_PROJECT/reports/ppa.json
/usr/bin/python3 experiments/asap7_qualification_20261116/lvs_diagnosis/run_gcd_formal.py NEW_PROJECT
```

These helpers target this GCD example. Other designs require top-name and functional-test adaptations; the GCD formal proof cannot be reused as their proof.

## Signoff

Use SFTP to upload `NEW_PROJECT_input/` to `/proj/workarea/yangao/asap7_qualification_20261116/` on 216. SCP is disabled; SFTP works. Execute on 216:

```bash
module load calibre/2025.1_16.10
cd /proj/workarea/yangao/asap7_qualification_20261116
python3 run_check.py drc NEW_CHECK --gds "$PWD/NEW_PROJECT_input/drc.gds" --flat
python3 run_check.py lvs NEW_CHECK --gds "$PWD/NEW_PROJECT_input/lvs.gds" --cdl "$PWD/NEW_PROJECT_input/source.cdl" --property-limit 1024
```

`run_check.py` sets the four required metal offsets to zero, PDK path, and existing license. It runs unchanged official rules and preserves invocation records, logs, reports, and input hashes. DRC must report **334 rulechecks, 0 results**; LVS logs and reports must say **CORRECT**. Exit status alone is not a signoff verdict.

Download `calibre_runs/NEW_CHECK/` into local `calibre_evidence/`. Generate the summary with:

```bash
/usr/bin/python3 experiments/asap7_qualification_20261116/summarize_qualification.py --project NEW_PROJECT --evidence NEW_CHECK
```

This updates top-level `qualification.json` and `evidence_sha256.json`; save the current summaries before a reproduction. The accepted summary uses the first clean DRC and the same-input LVS recheck at budget 1024:

```bash
/usr/bin/python3 experiments/asap7_qualification_20261116/summarize_qualification.py --project gcd_pin_window_v2 --evidence gcd_pin_window_v2 --lvs-evidence gcd_pin_window_v2_property1024
```
