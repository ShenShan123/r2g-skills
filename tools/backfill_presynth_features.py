#!/usr/bin/env python3
"""Win 5 (5b) — backfill pre-route feature vectors over the clean-run corpus.

Scans a projects root for design dirs that still have a synthesized netlist
(synth/synth.log), runs the Win-5 pre-route extractor, writes
reports/presynth_features.json, and (optionally) re-ingests so
runs.presynth_features_json is populated and suggest_config can KNN-retrieve.

This is the funded 5b backfill. NOTE (honest): historical corpus runs are often
backend-only — their synth dirs were not preserved — so this driver emits features
ONLY where a synth/synth.log survives. Designs without one need a re-synth (the
compute-bound part); it reports how many were skipped for that reason. Going
forward, emit presynth_features.json as part of the flow (post-synth) so new runs
carry the vector for free.

Usage:
  backfill_presynth_features.py <projects-root> [--ingest] [--db <path>]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "r2g-skills/signoff-loop"
PRESYNTH = SKILL / "scripts" / "extract" / "presynth.py"
INGEST = SKILL / "knowledge" / "ingest_run.py"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("projects_root", type=Path)
    ap.add_argument("--ingest", action="store_true",
                    help="re-ingest each project after emitting features")
    ap.add_argument("--db", default=None, help="knowledge.sqlite path for --ingest")
    args = ap.parse_args(argv)

    emitted = skipped = ingested = 0
    for cfg in sorted(args.projects_root.glob("*/constraints/config.mk")):
        proj = cfg.parent.parent
        if not (proj / "synth" / "synth.log").exists():
            skipped += 1
            continue
        subprocess.run([sys.executable, str(PRESYNTH), str(proj)], check=False)
        emitted += 1
        if args.ingest:
            cmd = [sys.executable, str(INGEST), str(proj)]
            if args.db:
                cmd += ["--db", args.db]
            subprocess.run(cmd, check=False)
            ingested += 1
    print(f"presynth backfill: emitted={emitted}, ingested={ingested}, "
          f"skipped(no synth.log -> needs re-synth)={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
