#!/usr/bin/env python3
"""Apply the versioned functional ontology to published design records."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from functional_ontology import CONFIDENCE_WEIGHTS, ONTOLOGY_SCHEMA, classify
from corpus_state import CorpusState
from run_expansion_round import load_jsonl, validate_publish_invariants, write_manifests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path.home() / "work/data/rtl_corpus")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    designs = load_jsonl(args.corpus_root / "manifests/all_designs.jsonl", "design_id")
    labels = {}
    for design_id, record in designs.items():
        labels[design_id] = classify(record)
        if args.apply:
            record["functional_ontology"] = labels[design_id]
    if args.apply:
        validate_publish_invariants(args.corpus_root, designs)
        write_manifests(args.corpus_root, designs)
        with CorpusState(args.corpus_root) as state:
            if not state.populated():
                state.sync_materialized_views()
            else:
                state.apply_incremental(designs=designs.values())
    summary = {
        "schema": ONTOLOGY_SCHEMA, "apply": args.apply, "designs": len(designs),
        "labels": dict(Counter(value["label"] for value in labels.values())),
        "confidence": dict(Counter(value["confidence"] for value in labels.values())),
        "confidence_weights": CONFIDENCE_WEIGHTS,
        "weighted_design_contribution": round(sum(float(value["diversity_weight"]) for value in labels.values()), 6),
        "misc_ip": sum(value["label"] == "misc_ip" for value in labels.values()),
    }
    target = args.corpus_root / "quality/phase1_5/function_ontology_summary.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
