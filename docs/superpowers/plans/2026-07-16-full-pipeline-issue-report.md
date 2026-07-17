# 2026-07-16 R2G Full-Pipeline Issue Report

Date: 2026-07-16 to 2026-07-17  
Repository: `/home/yangao/r2g-skills`  
Remote: `https://github.com/ShenShan123/r2g-skills.git`  
Commit tested: `cb50537e70f328b51ba087d84a3f5b25068ab5bd`  
Synchronization at audit start: `HEAD == origin/main`

Artifacts:

- Adversarial harness: `/home/yangao/r2g-skills/tools/audit_full_pipeline_2026_07_16.py`
- Independent runs: `tools/audit_full_pipeline_2026_07_16_results_dynamic_{a,b,c}.json`
- Real-flow workspace: `/home/yangao/r2g_full_pipeline_audit_2026_07_16`
- Preserved pre-audit work: `stash@{0}` (`pre-cb50537-agent-audit-2026-07-16`)

## Scope and Method

This audit covers the complete Agent-controlled path:

```text
online RTL discovery -> clone/archive acquisition -> candidate closure
-> synth-only proof -> quality scoring/publication gate -> promotion
-> ORFS implementation -> DRC/LVS signoff -> b/c/d/e/f graph construction
```

The audit combined two kinds of evidence:

1. Three automatically discovered open-source RTL repositories were cloned and
   exercised through the production entry points.
2. Each suspected invariant was reduced to an isolated temporary-directory
   probe and executed three times. A condition is reported only when all three
   runs reproduced it with no harness error.

The final harness contained 17 probes. Sixteen reproduced in all three runs;
one timeout-descendant hypothesis did not reproduce and is not reported. The 16
reproduced probes consolidate into 12 root-cause issues below. Local tool-path
selection, GitHub API rate limiting, and design-intrinsic DRC failures are not
classified as Agent defects.

The latest tree also passed its existing regression baseline before this audit:

- `signoff-loop`: 877 passed, 1 skipped
- `def-graph`: 363 passed, 61 skipped
- `rtl-acquire`: 63 passed
- targeted signoff tests: 89 passed
- targeted graph-gate tests: 43 passed

## Real-Flow Evidence

The production discovery and clone path selected the following independent
design families:

| Repository | Resolved local HEAD | Selected top | Synth cells | Role |
|---|---|---|---:|---|
| `freecores/ethmac` | `dd26899086edf3b797d2775ef9502d204a9a8149` | `eth_registers` | 1,245 | medium control; explicit `Clk` override |
| `ZipCPU/wbi2c` | `8a57e756685a8f82a5b5227dab5d05a9598ce638` | `wbi2cmaster` | 6,324 | medium sequential design |
| `seonskim/verilog_axi-interconnect` | `3629cf7a84d055577f0561c50e91378fc3e54c49` | `interconnect` | 14,801 | large high-pin-count design |

The automatic scanner emitted 28 candidates. The three strict-size subjects
synthesized successfully and produced `mapped_netlist.v`, `netlist_graph.pt`,
`cell_stats.json`, and `design_meta.json`. The Ethernet control used the
production promotion entry point's explicit `--clock-port Clk` override because
the automatic detector missed the case-sensitive port; that miss is reported in
Issue 5 rather than hidden in the experiment. The full Ethernet and I2C ORFS
flows completed. The first AXI flow stopped at `PPL-0024`; the engineer loop enlarged
the die from the required pin perimeter and then completed the backend flow.
That recovery is useful control evidence: the specialized PPL recovery worked,
while the generic diagnosis still assigned the wrong failure kinds (Issue 12).

The strict-medium `eth_registers` control then completed full DRC with zero
violations; LVS was explicitly `skipped` because nangate45 ships no LVS deck.
The graph gate recorded `pass_with_caveats`, generated all heterogeneous
`b/c/d/e/f_graph.pt` views plus `netlist_graph.pt`, and the independent
`verify_graph_dataset.py` run passed 291 of 291 checks. This successful control
separates the defects below from a general inability to execute the pipeline.

The larger `wbi2cmaster` and `interconnect` controls both reached full KLayout
DRC, then exhausted the configured 7,200-second limit while evaluating
`FreePDK45.lydrc:131`. The timeout wrapper reclaimed both process groups. In
each case the Agent wrote `status=stuck`,
`reason=klayout_polygon_op_no_progress`, stopped after one DRC attempt, and
recorded LVS as unsupported on nangate45. The AXI engineer ledger terminated in
`escalated/signoff_stuck_scan` rather than applying another repair. The graph
entry point then rejected both projects with `DRC status='stuck'` and created no
official dataset directory. These are correctly managed tool-performance
outcomes, not added to the issue list.

## Executive Summary

### P0: Can Admit Untrusted Inputs, Promote Invalid State, or Corrupt Dataset Identity

1. Promotion is not bound to the RTL bytes that passed synth-only.
2. Unknown source licensing and unresolved revisions pass the publication gate.
3. Synthesis success can be reconstructed from stale files despite a failed command.
4. An explicit `design_action=reject` is publish eligible under the shipped policy.
5. Sequential designs with non-standard clocks are silently promoted under a virtual clock.
6. Graph regeneration is not an atomic, invalidating dataset transaction.
7. Signoff restaging can permanently retain an older backend run.
8. Acquisition does not enforce a filesystem-containment boundary.
9. ORFS run identity and workspace identity can collide.

### P1: Can Reduce Corpus Recall, Bias Selection, or Teach the Wrong Repair

10. Dependency closure is silently truncated at 16 RTL files.
11. Quality scoring consumes a statistics schema that graph conversion does not emit.
12. Generic diagnosis misclassifies `PPL-0024` and clean timing text.

## Issue 1: P0 - Promotion Is Not Bound to the RTL Bytes That Passed Synthesis

### Reproduction

The probe completed synth-only metadata for one RTL file, changed the file's
logic afterward, and then promoted the candidate.

```json
{
  "original_sha256": "8215385de9c77ecd7f75164330c927619064e8f6a4976cd45fa4b5e5ec580954",
  "mutated_sha256": "5326eea624c0fd1b9f20e137446c0f3c1d4fb0bb17cbe70a5a0bb3c94b5f90c3",
  "stored_signature_unchanged": true,
  "promote_status": "promoted",
  "vendored_sha256": "5326eea624c0fd1b9f20e137446c0f3c1d4fb0bb17cbe70a5a0bb3c94b5f90c3"
}
```

### Root Cause

`expand_candidates.py` computes `rtl_signature` from the top name and sorted
path strings, not file contents. `promote_candidates.py` later copies the
current bytes from those paths without comparing them with any synth-time
digest. The real `design_meta.json` files likewise record paths and a path-based
signature, but no per-file content digest or resolved upstream revision.

### Impact

The full-flow layout and graph can describe RTL different from the RTL that
earned the synth-only success. Deduplication, failure learning, and dataset
provenance can therefore refer to different designs under one identity.

### Recommendation

Create an immutable source snapshot at candidate expansion. Record repository
URL, resolved commit, relative path, size, and SHA-256 for every RTL/include
file. Synthesize from that snapshot and promote only from that snapshot. Verify
the digest set at every boundary and carry a source-manifest digest into the
backend and graph manifests.

## Issue 2: P0 - Unknown Licensing and Unresolved Revisions Pass Publication

### Reproduction

A successful `keep` row was assigned `license_status=unknown` and no resolved
commit. `build_publish_candidates.py` emitted:

```json
{
  "publish_eligible": "True",
  "publish_reasons": "",
  "license_fields_in_publish_manifest": [],
  "commit_fields_in_publish_manifest": []
}
```

The real clone summary records URL and branch but not resolved commit or source
license. Those fields are also absent from the synth metadata and graph
manifest. This mattered in the real sample: the repositories expose mixed
licensing evidence, including a GPL document and a repository with no obvious
root license file, but the publication decision cannot represent that state.

### Root Cause

The acquisition manifest schema, clone summary schema, publish policy, and graph
manifest have no end-to-end license/revision contract. Publication is decided
only from synthesis/quality fields.

### Impact

An automatically discovered design with unknown or incompatible redistribution
terms can enter a release candidate. The generated dataset also cannot be
reconstructed from an immutable upstream revision.

### Recommendation

Resolve and record the exact Git commit after clone. Add SPDX/license discovery
with explicit states such as `allow`, `review`, `deny`, and `unknown`; default
the publish gate to deny or manual review for every state other than `allow`.
Carry source URL, commit, license evidence, and source-manifest digest into the
official dataset manifest.

## Issue 3: P0 - Failed Synthesis Can Be Reconstructed as Success from Stale Files

### Reproduction

Two independent probes reached the same unsafe state:

```json
{
  "nonzero_synth_rc": 7,
  "index_status_after_expand": "success",
  "mapped_netlist_was_stale": true,
  "failed_meta_status_before_rebuild": "synth_failed",
  "index_status_after_rebuild": "success"
}
```

### Root Cause

`expand_candidates.py` treats the presence of a returned netlist path as the
authoritative signal and does not require synthesis return code zero. Older
artifacts are not transactionally invalidated before a forced rerun.
`rebuild_external_index_from_dirs.py` can then infer success from surviving
`mapped_netlist.v` and `netlist_graph.pt` even when `design_meta.json` records a
failed rerun.

### Impact

A stale netlist/graph can be promoted and published as the result of a newer
failed run. The index no longer represents the latest generation.

### Recommendation

Assign every expansion attempt a generation ID and write into an isolated
staging directory. Require command return code zero, expected fresh artifacts,
and generation-matching metadata before atomically committing success. On
failure, quarantine the staging generation and never infer success solely from
file existence. Index rebuild must require a committed success manifest whose
digests match the artifacts.

## Issue 4: P0 - Explicitly Rejected Designs Are Publish Eligible

### Reproduction

```json
{
  "design_action": "reject",
  "publish_eligible": "True",
  "publish_reasons": "",
  "policy_allowed_design_actions": ["keep", "conditional", "reject"]
}
```

### Root Cause

The shipped `references/publish_policy.json` includes `reject` in
`allowed_design_actions`. `build_publish_candidates.py` faithfully accepts that
policy, so its action gate does not distinguish rejection from approval.

### Impact

The quality scorer's strongest negative decision has no publication effect.
Low-value or explicitly rejected designs can enter promotion and downstream
dataset generation.

### Recommendation

Remove `reject` from the allowed set. Validate policy semantics at load time so
reserved terminal actions cannot be configured as publishable accidentally.
Add an invariant test for each action state, with `reject` always fail-closed.

## Issue 5: P0 - Non-Standard Sequential Clocks Become Virtual Clocks

### Reproduction

The isolated probe used a top input `MTxClk` and an
`always @(posedge MTxClk ...)` register. Detection returned `(virtual)`.

The real Ethernet top contains 13 top-level `MTxClk` edge-triggered blocks and
synthesized to 119 sequential cells. Promotion nevertheless wrote a virtual
clock SDC. ORFS completed and reported:

```text
Warning: There are 119 unclocked register/latch pins.
[WARNING STA-0450] virtual clock virtual_clk can not be propagated.
```

### Root Cause

`detect_clock_port()` accepts only a fixed name list (`clk`, `clock`, `i_clk`,
`wb_clk_i`, and several variants). It does not inspect event controls or the
clock pins of synthesized sequential cells. No gate rejects a sequential design
when the detector falls back to a virtual clock.

### Impact

ORFS can finish and graph conversion can proceed while setup/hold labels are not
defined against the real sequential clock. Such labels are not trustworthy for
training even though the physical artifacts exist.

### Recommendation

Parse top-module event controls and/or inspect the synthesized clock network.
Rank input ports by sequential fanout, exclude reset-like signals, and require
an explicit operator choice when multiple candidates remain. Fail promotion for
`seq_cells > 0` plus virtual-clock fallback unless a recorded override declares
the design intentionally self-timed. Add an unconstrained-register gate before
signoff and graph publication.

## Issue 6: P0 - Graph Regeneration Is Not an Atomic, Invalidating Transaction

### Reproduction

Three probes exercised skip, partial failure, and variant shrink:

```json
{
  "skipped_run_rc": 0,
  "new_report_status": "skipped",
  "old_dataset_manifest_status": "ok",
  "old_manifest_unchanged": true,
  "partial_failure": "b replaced, c remained old, old green manifest survived",
  "new_manifest_variants": ["b"],
  "stale_f_files_still_present": true
}
```

The real Ethernet DRC failure provided the corresponding production control:
`run_graphs.sh` correctly blocked conversion at the signoff gate, wrote a
skipped report, and returned zero. On a directory containing a prior dataset,
the isolated probe showed that this path leaves the old official manifest green.

### Root Cause

`run_graphs.sh::skip()` writes `reports/graph_dataset.json` and exits zero, but
does not invalidate `dataset/graph_manifest.json` or previous `.pt` files.
`build_graphs.py` writes each requested variant directly into the official
directory and commits the manifest only at the end. It does not remove files
for omitted variants or graph kinds.

### Impact

Consumers can observe mixed generations: a current `b`, old `c`, old manifest,
and stale unlisted variants. A failed rebuild can therefore leave an apparently
valid dataset that no single run produced.

### Recommendation

Build every generation in a fresh staging directory. Run the independent graph
verifier there, fsync the files, then atomically publish a generation manifest
or switch a `current` pointer. On skip/failure, atomically mark the current
publication state non-OK while preserving the previous generation only as an
explicit historical generation. Return a distinct non-success status for
automation and delete/quarantine files excluded by the new manifest.

## Issue 7: P0 - Signoff Restaging Can Permanently Retain an Older Run

### Reproduction

The probe staged `RUN_ONE`, created a newer complete `RUN_TWO`, and staged again:

```json
{
  "first_staged": "RUN_ONE",
  "after_newer_run_staged": "RUN_ONE",
  "newest_backend_content": "RUN_TWO",
  "restage_marker_exists": true
}
```

### Root Cause

`_restage_for_signoff.sh` uses a destination-wide `.r2g_restaged` marker and
`cp -n`. Once the marker exists, later authoritative backend runs are not
restaged. The marker does not identify its source run or artifact digests.

### Impact

DRC/LVS can verify an older layout after a newer backend run completes. Project
reports then look current but refer to stale ORFS scratch artifacts.

### Recommendation

Replace the boolean marker with a manifest containing source run ID, config
digest, and artifact digests. Restage whenever that identity changes. Populate a
fresh temporary variant directory and atomically replace it, rather than using
`cp -n` into persistent scratch state. Record the staged run identity in every
signoff report and require the graph gate to match it.

## Issue 8: P0 - Acquisition Does Not Enforce Filesystem Containment

### Reproduction

Two independent ingress paths escaped their expected roots:

```json
{
  "archive_status": "cloned",
  "archive_parent_member_written": true,
  "external_symlink_candidate_emitted": true,
  "symlink_resolved_outside_downloads": true
}
```

### Root Cause

`clone_repo_manifest.py` calls `ZipFile.extractall()` and
`TarFile.extractall()` without validating member paths, absolute paths, links,
or device entries. `discover_download_candidates.py` scans candidate paths
without requiring each resolved path to remain below the cloned repository root.

### Impact

An untrusted archive can overwrite files outside extraction scratch, and a
cloned repository can make the Agent synthesize a host file through a symlink.
This is both a host-safety issue and a dataset-provenance violation.

### Recommendation

Use a safe extractor that rejects absolute paths, `..` traversal, escaping
symlink/hardlink targets, and non-regular members before writing anything.
During discovery and vendoring, resolve every path and require
`resolved_path.is_relative_to(repo_root.resolve())`; either reject all symlinks
or copy only verified regular-file bytes into an immutable snapshot.

## Issue 9: P0 - ORFS Run and Workspace Identities Can Collide

### Reproduction

Two simultaneous invocations started in the same second. Both returned zero,
but produced one backend directory with two stage rows:

```json
{
  "run_dir_count": 1,
  "run_tag": "RUN_2026-07-17_00-13-28",
  "stage_log_rows_in_single_dir": 2,
  "return_codes": [0, 0]
}
```

The real campaign independently exposed the workspace half of the problem:
projects promoted under different parent directories retained the same basename,
so the default `FLOW_VARIANT` targeted the same ORFS design/results directory.
The conflicting campaign had to be stopped and restarted with explicit unique
variants.

### Root Cause

`run_orfs.sh` creates `RUN_TAG` with one-second timestamp precision and derives
the default `FLOW_VARIANT` only from the project basename. It has no atomic lock
or collision check for either backend run directories or ORFS workspaces.
There is also a cross-stage identity break: `run_drc.sh` and `run_lvs.sh`
support an explicit third `FLOW_VARIANT` argument, but `fix_signoff.sh` invokes
both with only project and platform. A backend run created under an explicit
variant is therefore signed off under the project-basename variant. In the real
`eth_registers` control, this caused restaging into another workspace and an
otherwise unnecessary backend rebuild before DRC.

### Impact

Concurrent Agent workers can clean, overwrite, or append to the same physical
run. The resulting artifacts and stage ledger cannot be attributed to one
invocation.

### Recommendation

Use a collision-resistant run ID containing an atomic sequence or UUID, PID,
and timestamp. Derive workspace identity from a stable project UUID plus run ID,
not basename. Acquire an exclusive lock before cleaning or writing a variant;
fail before EDA execution if the target identity is already active. Persist the
authoritative flow variant in backend metadata and require signoff/RCX/graph
stages to consume that identity instead of re-deriving it.

## Issue 10: P1 - Dependency Closure Is Silently Truncated at 16 Files

### Reproduction

An 18-module local dependency chain was discovered as a normal candidate:

```json
{
  "local_dependency_depth": 18,
  "emitted_bundle_files": 16,
  "deep_dependency_omitted": true,
  "incomplete_marker_present": false
}
```

The real `ethmac` top hit the same cap. `eth_random.v` existed in the cloned
repository but was omitted from the 16-file bundle; synthesis failed on the
missing `eth_random` module. Failure classification then labeled the candidate
`low_value_failure` and emitted no retry row.

### Root Cause

`bundle_closure()` stops when `len(ordered) == max_files` with a default of 16.
It neither reports a non-empty queue nor records unresolved local references.
The failure classifier does not distinguish a missing module that exists in the
same repository from a genuinely external dependency.

### Impact

Larger valid RTL designs are converted into predictably incomplete candidates,
then permanently excluded. Corpus recall is biased against precisely the larger
designs the pipeline is intended to acquire.

### Recommendation

Compute complete module closure. If a resource cap is required, detect overflow
and emit `bundle_incomplete` with the unresolved-module list; do not send it to
synthesis or classify it as low value. On a missing-module synthesis error,
search the same repository and rebuild closure before any exclusion decision.

## Issue 11: P1 - Quality Scoring Uses a Non-Emitted Statistics Schema

### Reproduction

The graph converter emits `graph_gate_label_entropy`,
`graph_unique_gate_labels`, and `graph_dominant_gate_share`, but not
`cell_histogram`. The scorer reads only `cell_histogram` for entropy, unique
types, rare share, and redundancy. The controlled A/B input changed only the
presence of the equivalent histogram:

```json
{
  "without_histogram": {
    "unique_types": 0,
    "redundancy": 0.0,
    "score": 0.425,
    "action": "keep"
  },
  "with_histogram": {
    "redundancy": 1.0,
    "score": 0.105,
    "action": "reject"
  }
}
```

All three real designs showed zero entropy, zero unique types, zero rare share,
and zero redundancy in the quality CSV despite carrying 42 to 59 unique gate
labels in their graph statistics.

### Root Cause

The producer and consumer evolved different field contracts without a required
schema check. Missing fields silently default to empty collections and valid
zeroes instead of producing an error or `unknown` score.

### Impact

Novelty/redundancy ranking and keep/reject decisions are materially wrong while
appearing numerically valid. Corpus selection can over-admit redundant designs
and mis-rank useful candidates.

### Recommendation

Define one versioned statistics schema shared by graph conversion and quality
scoring. Either emit the gate-label histogram or compute every metric directly
from the emitted graph fields. Treat absent required fields as a blocked quality
assessment, not zero. Add producer-consumer contract tests and decision-boundary
fixtures.

## Issue 12: P1 - Generic Diagnosis Misclassifies PPL and Clean Timing Text

### Reproduction

The large AXI run failed because 3,460 IO pins exceeded 1,588 available
positions (`PPL-0024`). `build_diagnosis.py` reported:

```json
{
  "true_failure": "PPL-0024 IO-pin perimeter exhaustion",
  "diagnosis_kinds": [
    "placement_utilization_overflow",
    "timing_violation",
    "make_error"
  ]
}
```

The same classification reproduced with a four-line isolated log. The engineer
loop still recovered because it contains a separate exact `PPL-0024` detector;
the generic diagnosis and any consumer learning from it remained wrong.

### Root Cause

The utilization rule matches any line containing both `utilization` and `100%`.
Normal Yosys output contains `Design area ... 100% utilization`. The timing rule
searches the substring `setup violation`, so `No setup violations found` is a
positive match unless both setup and hold clean messages happen to be present.
There is no first-class PPL pin-capacity diagnosis.

### Impact

The Agent can retrieve or learn density/timing recipes for an IO-perimeter
failure. Recovery currently depends on a parallel hardcoded path rather than a
consistent diagnosis contract, which can make memory, reports, and live action
selection disagree.

### Recommendation

Parse explicit tool codes first and add a structured `io_pin_capacity_overflow`
kind for `PPL-0024`, including current and required perimeter. Restrict
utilization overflow to error-coded or numerically validated placement messages.
Handle negation before positive timing patterns and prefer authoritative final
timing reports over raw log substrings. Add fixtures containing normal `100%`
synthesis utilization and `No ... violations found` lines.

## Recommended Repair Order

1. Close publication correctness first: Issues 1, 2, 3, 4, and 5.
2. Make output identity transactional: Issues 6, 7, and 9.
3. Secure untrusted acquisition: Issue 8.
4. Restore acquisition and curation quality: Issues 10 and 11.
5. Unify diagnosis and repair semantics: Issue 12.

Each fix should retain its corresponding adversarial probe as a regression test.
For transactional fixes, test interruption before and after every commit point;
for provenance fixes, test byte mutation, repository movement, and mixed-run
artifacts rather than checking only names or file existence.
