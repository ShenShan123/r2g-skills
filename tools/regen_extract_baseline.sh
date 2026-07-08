#!/usr/bin/env bash
set -euo pipefail

# regen_extract_baseline.sh [OUT_DIR]
# ----------------------------------------------------------------------------
# Regenerates the feature- AND label-stage CSVs for the two pinned techlib-gate
# designs (aes_core/nangate45, cordic/sky130hd) into OUT_DIR (default
# /tmp/techlib_baseline), then records per-design MD5SUMS.
#
# This is the reproducible baseline generator for the techlib-restructure safety
# gate (Task 0). Tasks 7/8 call it with a fresh OUT_DIR to produce a "current"
# set and diff vs the committed baseline; the gate is byte-for-byte identical
# CSV output. For that diff to be meaningful the INPUTS must be pinned
# identically every run — which is what this script guarantees.
#
# == Why a staged project dir (the cordic DEF trap) ==
# run_features.sh honors the R2G_DEF override, but run_labels.sh does NOT — it
# auto-finds the DEF/ODB by scanning <project>/backend/RUN_* | sort -r and
# taking the first. cordic has THREE backend runs; two are nangate45
# (FILLCELL_X32 masters) and only RUN_2026-05-17_05-58-40 is the real sky130hd
# run. aes_core likewise has multiple runs and the reverse-sort would pick a
# different one than the pinned RUN_2026-04-12_18-04-55.
#
# We may not modify the extractor scripts (read-only w.r.t. extractors), so to
# pin BOTH stages deterministically we stage a throwaway project dir per design
# containing only constraints/ plus a symlink to the SINGLE pinned backend RUN.
# With exactly one RUN present, run_labels.sh's auto-find is forced onto the
# pinned artifacts, and run_features.sh additionally gets R2G_DEF for belt-and-
# suspenders. Inputs are then identical here and in any "current" regen.
#
# Python: none here. Bash only.
# ----------------------------------------------------------------------------

OUT_DIR="${1:-/tmp/techlib_baseline}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL="$REPO_ROOT/r2g-skills/def-graph"
RUN_FEATURES="$SKILL/scripts/flow/run_features.sh"
RUN_LABELS="$SKILL/scripts/flow/run_labels.sh"
DESIGN_CASES="$REPO_ROOT/design_cases"

# Pinned (design, platform, backend RUN) tuples. The RUN is the directory that
# holds results/6_final.{def,odb,spef}. Verified to carry the expected masters.
PINS=(
  "aes_core|nangate45|RUN_2026-04-12_18-04-55|FILLCELL_X32"
  "cordic|sky130hd|RUN_2026-05-17_05-58-40|sky130_fd_sc_hd__"
)

STAGE_ROOT="$(mktemp -d -t techlib_regen.XXXXXX)"
cleanup() { rm -rf "$STAGE_ROOT"; }
trap cleanup EXIT

mkdir -p "$OUT_DIR"
echo "== regen_extract_baseline -> $OUT_DIR =="
echo "   stage_root=$STAGE_ROOT"

for pin in "${PINS[@]}"; do
  IFS='|' read -r design platform run expect_master <<< "$pin"
  src_proj="$DESIGN_CASES/$design"
  run_dir="$src_proj/backend/$run"
  def="$run_dir/results/6_final.def"

  echo
  echo "=== $design ($platform) :: $run ==="

  # Exit 77 (the autotools "skip" convention) when a frozen INPUT artifact is absent.
  # A campaign re-target (/r2g-debug Step 1b re-points + re-flows the WHOLE design_cases/
  # corpus) legitimately consumes these pinned RUN dirs -- that is "cannot regenerate the
  # baseline", NOT "the extractor regressed", so the byte-diff gate must SKIP, not fail.
  # (The master-mismatch check below stays exit 2 = a genuinely wrong/corrupt pinned run.)
  if [[ ! -d "$run_dir" ]]; then
    echo "  SKIP: pinned backend run absent (campaign consumed design_cases/?): $run_dir" >&2
    exit 77
  fi
  if [[ ! -f "$def" ]]; then
    echo "  SKIP: pinned DEF absent (campaign consumed design_cases/?): $def" >&2
    exit 77
  fi
  # Master sanity-check: guards against a re-pinned run silently changing PDK.
  if ! grep -q -- "$expect_master" "$def"; then
    echo "  ERROR: pinned DEF $def does not contain expected master '$expect_master'" >&2
    echo "         (cordic DEF trap guard — wrong/nangate run pinned?)" >&2
    exit 2
  fi
  echo "  DEF: $def  (master '$expect_master' confirmed)"

  # --- Stage a throwaway project dir with exactly the ONE pinned run ----------
  stage="$STAGE_ROOT/$design"
  mkdir -p "$stage/backend"
  # constraints/ is needed by resolve_platform_paths.sh + label SDC parsing.
  if [[ -d "$src_proj/constraints" ]]; then
    cp -r "$src_proj/constraints" "$stage/constraints"
  else
    echo "  WARN: $src_proj/constraints missing — platform resolution may fall back" >&2
  fi
  # Symlink the single pinned RUN so auto-find (labels) is deterministic and the
  # SPEF/ODB pairing stays within this run.
  ln -s "$run_dir" "$stage/backend/$run"

  out_design="$OUT_DIR/$design"
  mkdir -p "$out_design/features" "$out_design/labels"

  # --- Feature stage (R2G_DEF pins the DEF; single run anyway) ----------------
  echo "  --- features ---"
  R2G_DEF="$def" bash "$RUN_FEATURES" "$stage" "$platform" 2>&1 | sed 's/^/    /' || \
    echo "    NOTE: run_features.sh returned nonzero (fail-soft stages logged)"

  # --- Label stage (single-run auto-find resolves to the pinned artifacts) ----
  # R2G_DEF is also set here for symmetry / future-proofing: run_labels.sh does
  # not honor it today (the single-symlinked RUN is what actually pins it), but
  # if it ever gains the override this keeps the pin correct.
  echo "  --- labels ---"
  R2G_DEF="$def" bash "$RUN_LABELS" "$stage" "$platform" 2>&1 | sed 's/^/    /' || \
    echo "    NOTE: run_labels.sh returned nonzero (fail-soft stages logged)"

  # --- Collect CSVs -----------------------------------------------------------
  if compgen -G "$stage/features/*.csv" > /dev/null; then
    cp "$stage"/features/*.csv "$out_design/features/"
  fi
  if compgen -G "$stage/labels/*.csv" > /dev/null; then
    cp "$stage"/labels/*.csv "$out_design/labels/"
  fi

  # --- Record md5sums (sorted, relative paths) --------------------------------
  (
    cd "$out_design"
    # List CSVs deterministically; md5sum over sorted relative paths.
    find features labels -name '*.csv' -type f 2>/dev/null | LC_ALL=C sort | xargs -r md5sum
  ) > "$out_design/MD5SUMS"

  n_feat=$(find "$out_design/features" -name '*.csv' -type f 2>/dev/null | wc -l)
  n_lab=$(find "$out_design/labels" -name '*.csv' -type f 2>/dev/null | wc -l)
  echo "  collected: $n_feat feature csv(s), $n_lab label csv(s) -> $out_design"
  echo "  md5sums:   $out_design/MD5SUMS"

  # --- Fail hard on an empty/partial baseline ---------------------------------
  # The fail-soft `... || echo NOTE` above swallows a nonzero extractor exit, so
  # a broken run would otherwise quietly write a 0-CSV "baseline" that the gate
  # test then treats as green. The feature stage has NO external-tool dependency
  # (pure python over the DEF) and MUST always emit all 8 CSVs — anything less is
  # a real failure, not an environment skip. (Label workers timing/irdrop need
  # OpenROAD and may legitimately skip on a tool-less host, so we don't hard-gate
  # on n_lab.)
  if [[ "$n_feat" -lt 8 ]]; then
    echo "  ERROR: $design produced only $n_feat/8 feature CSV(s) — extractor failed." >&2
    echo "         Refusing to write a partial/empty baseline. See $stage/features/*.log" >&2
    exit 2
  fi
done

echo
echo "== done. baseline at $OUT_DIR =="
