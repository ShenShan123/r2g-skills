#!/usr/bin/env python3
"""Design-aware ORFS parameter recommender.

Usage: suggest_config.py <project-dir> [output.json]

Analyzes synthesis results and design characteristics to recommend
ORFS config.mk parameters (utilization, density, safety flags).
"""
import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path

import knowledge_db
import query_knowledge

HEURISTICS_PATH = knowledge_db.DEFAULT_KNOWLEDGE_DIR / "heuristics.json"
FAMILIES_PATH = knowledge_db.DEFAULT_FAMILIES_PATH

# Win 5: numeric pre-route features used for KNN retrieval (presynth.py order).
FEATURE_KEYS = ("instance_count", "primary_io", "est_logic_depth",
                "target_utilization", "clock_period_ns", "routing_layers")
KNN_K = 5


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _zscore_stats(vecs: list[list]) -> tuple[list[float], list[float]]:
    """Per-column mean + std over non-None values; std 0 -> 1.0 (no scaling).
    Mixed-scale columns (instance count in thousands vs utilization in [0,50])
    MUST be normalized or instance_count dominates the Euclidean distance."""
    ncol = len(FEATURE_KEYS)
    means: list[float] = []
    stds: list[float] = []
    for j in range(ncol):
        col = [v[j] for v in vecs if v[j] is not None]
        if col:
            m = sum(col) / len(col)
            sd = (sum((x - m) ** 2 for x in col) / len(col)) ** 0.5 or 1.0
        else:
            m, sd = 0.0, 1.0
        means.append(m)
        stds.append(sd)
    return means, stds


def _normalize(vec: list, means: list[float], stds: list[float]) -> list[float]:
    # Impute a missing feature to the column mean -> contributes 0 to the distance.
    return [((means[j] if vec[j] is None else vec[j]) - means[j]) / stds[j]
            for j in range(len(FEATURE_KEYS))]


def _euclidean(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _target_feature_vector(project: Path) -> dict | None:
    """The target's pre-route feature vector, read from reports/presynth_features.json
    (emitted by presynth.py). None when absent -> retrieval is skipped (fall back to
    family medians). Decoupled from the extractor by design — no cross-dir import."""
    data = None
    p = project / "reports" / "presynth_features.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    if not data or all(data.get(k) is None for k in FEATURE_KEYS):
        return None
    return data


def _load_feature_corpus(db_path: Path | str, platform: str) -> list[dict]:
    """Prior CLEAN, non-bench runs on this platform that carry a pre-route feature
    vector, with the config they used. Held-out bench runs are excluded (Win 3)."""
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn)
    cols = ("design_name", "core_utilization", "place_density_lb_addon",
            "outcome_score", "drc_status", "lvs_status", "rcx_status",
            "orfs_status", "lvs_mismatch_class", "presynth_features_json")
    rows = conn.execute(
        f"SELECT {', '.join(cols)} FROM runs WHERE platform=? AND "
        "presynth_features_json IS NOT NULL AND COALESCE(is_bench,0)=0",
        (platform,)).fetchall()
    conn.close()
    corpus: list[dict] = []
    for r in rows:
        d = dict(zip(cols, r))
        if not knowledge_db.is_success(d):       # CLEAN exemplars only
            continue
        try:
            vec = json.loads(d["presynth_features_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        corpus.append({"vector": vec, "cu": d["core_utilization"],
                       "pd": d["place_density_lb_addon"],
                       "score": d["outcome_score"], "design_name": d["design_name"]})
    return corpus


def _retrieve_by_features(target_vec: dict, corpus: list[dict],
                          k: int = KNN_K) -> dict | None:
    """KNN over z-score-normalized pre-route features: among clean runs with
    outcome_score >= the corpus median, return the median config of the k nearest.
    None when the corpus is too small. Replaces the infer_family prefix lookup."""
    if len(corpus) < 2:
        return None
    scores = [c["score"] for c in corpus if c["score"] is not None]
    pool = corpus
    if scores:
        med = statistics.median(scores)
        best = [c for c in corpus if c["score"] is None or c["score"] >= med]
        if len(best) >= 2:
            pool = best
    vecs = [[_num(c["vector"].get(key)) for key in FEATURE_KEYS] for c in pool]
    tvec = [_num(target_vec.get(key)) for key in FEATURE_KEYS]
    means, stds = _zscore_stats(vecs + [tvec])
    tn = _normalize(tvec, means, stds)
    ranked = sorted(zip((_euclidean(tn, _normalize(v, means, stds)) for v in vecs),
                        pool), key=lambda x: x[0])
    nearest = [c for _, c in ranked[:k]]
    cus = [c["cu"] for c in nearest if c["cu"] is not None]
    pds = [c["pd"] for c in nearest if c["pd"] is not None]
    if not cus:
        return None
    return {"CORE_UTILIZATION": int(round(statistics.median(cus))),
            "PLACE_DENSITY_LB_ADDON": float(statistics.median(pds)) if pds else None,
            "k": len(nearest), "neighbors": [c["design_name"] for c in nearest]}


class _SkipLearned(Exception):
    """Internal sentinel: bypass the learned-heuristics override block
    (the --no-learned / naive arm). Caught alongside the malformed-file
    fall-through so the naive arm reuses the exact same baseline path."""


def parse_synth_stats(synth_dir: Path) -> dict:
    """Parse Yosys stat output from synth.log for cell counts."""
    stats = {}
    synth_log = synth_dir / 'synth.log'
    if not synth_log.exists():
        return stats

    text = synth_log.read_text(encoding='utf-8', errors='ignore')

    # Parse Yosys stat block
    for m in re.finditer(r'Number of cells:\s+(\d+)', text):
        stats['cell_count'] = int(m.group(1))
    for m in re.finditer(r'Number of wires:\s+(\d+)', text):
        stats['wire_count'] = int(m.group(1))
    # Chip area estimate from Yosys
    for m in re.finditer(r'Chip area for module.*?:\s+([\d.]+)', text):
        stats['synth_area'] = float(m.group(1))

    return stats


def parse_config_mk(config_path: Path) -> dict:
    """Parse existing config.mk fields."""
    fields = {}
    if not config_path.exists():
        return fields
    content = config_path.read_text().replace('\\\n', ' ')
    for line in content.splitlines():
        line = line.strip()
        if line.startswith('#') or not line:
            continue
        m = re.match(r'(?:export\s+)?(\w+)\s*=\s*(.*)', line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def detect_design_type(project: Path, config: dict) -> str:
    """Classify design type from RTL characteristics."""
    verilog_str = config.get('VERILOG_FILES', '')
    rtl_files = [f for f in verilog_str.split() if not f.startswith('$(')]

    combined_rtl = ''
    for vf in rtl_files:
        if os.path.isfile(vf):
            try:
                combined_rtl += open(vf).read().lower()
            except (OSError, UnicodeDecodeError):
                pass

    # Also check RTL directory
    rtl_dir = project / 'rtl'
    if rtl_dir.exists():
        for f in rtl_dir.glob('*.v'):
            try:
                combined_rtl += f.read_text().lower()
            except (OSError, UnicodeDecodeError):
                pass

    # Bus-heavy patterns
    bus_keywords = ['crossbar', 'arbiter', 'interconnect', 'wb_conmax', 'axi_', 'ahb_']
    if any(kw in combined_rtl for kw in bus_keywords):
        return 'bus_heavy'

    # Crypto/datapath patterns
    crypto_keywords = ['aes', 'sha', 'des_', 'cipher', 'encrypt', 'sbox']
    if any(kw in combined_rtl for kw in crypto_keywords):
        return 'crypto'

    # Memory-heavy patterns
    if 'sram' in combined_rtl or 'ADDITIONAL_LEFS' in config:
        return 'macro_heavy'

    return 'logic'


def recommend(project: Path, use_learned: bool = True,
              db_path: Path | str | None = None) -> dict:
    """Generate parameter recommendations.

    When ``use_learned`` is False the entire learned-heuristics override block
    is skipped: ``learned_source`` stays None and the recommendation is the
    pure ``params_by_size`` baseline plus the design-type clamps and the 0.10
    floor. This is the *naive* arm of the payoff A/B harness — the ONLY thing
    that differs from the learned arm is config provenance; the size
    classification, design-type clamps, safety flags and floor are identical.
    Default True keeps every existing ``recommend(project)`` caller unaffected.
    """
    config_path = project / 'constraints' / 'config.mk'
    config = parse_config_mk(config_path)
    synth_stats = parse_synth_stats(project / 'synth')
    design_type = detect_design_type(project, config)
    cell_count = synth_stats.get('cell_count', 0)
    platform = config.get('PLATFORM', 'nangate45')

    recommendations = {}
    explanations = []

    # Size classification
    if cell_count == 0:
        size_class = 'unknown'
        explanations.append('No synthesis data found. Using conservative defaults.')
    elif cell_count < 100:
        size_class = 'tiny'
    elif cell_count < 5000:
        size_class = 'small'
    elif cell_count < 50000:
        size_class = 'medium'
    else:
        size_class = 'large'

    # Base parameters by size
    params_by_size = {
        'unknown': {'CORE_UTILIZATION': 30, 'PLACE_DENSITY_LB_ADDON': 0.20},
        'tiny':    {'CORE_UTILIZATION': 30, 'PLACE_DENSITY_LB_ADDON': 0.20},
        'small':   {'CORE_UTILIZATION': 30, 'PLACE_DENSITY_LB_ADDON': 0.20},
        'medium':  {'CORE_UTILIZATION': 25, 'PLACE_DENSITY_LB_ADDON': 0.20},
        'large':   {'CORE_UTILIZATION': 20, 'PLACE_DENSITY_LB_ADDON': 0.25},
    }
    recommendations.update(params_by_size.get(size_class, params_by_size['unknown']))

    # --- Learned-heuristics override (before design-type adjustments) ----
    # Learned values become the new baseline. The design-type clamps below
    # still apply, so e.g. a bus_heavy design with a learned median of 28
    # will still be clamped to 15 by the existing bus_heavy rule. This is
    # intentional: safety rails beat empirical medians.
    learned_source = None
    try:
        if not use_learned:
            # Naive arm: skip the learned override entirely. learned_source
            # stays None and only params_by_size + clamps + floor apply.
            raise _SkipLearned
        # --- Win 5: feature-vector KNN retrieval (replaces infer_family) --------
        # When a pre-route feature vector exists, retrieve the k nearest CLEAN runs
        # by topology and seed from their median config. This fixes the infer_family
        # fragmentation (245/303 singleton families). Falls back to family medians
        # below when no feature vector / too-small corpus. The design-type clamps +
        # 0.10 floor still apply afterward (safety rails beat retrieval).
        retrieved = False
        tvec = _target_feature_vector(project)
        if tvec is not None:
            corpus = _load_feature_corpus(
                db_path or knowledge_db.DEFAULT_DB_PATH, platform)
            retr = _retrieve_by_features(tvec, corpus)
            if retr:
                recommendations['CORE_UTILIZATION'] = retr['CORE_UTILIZATION']
                if retr.get('PLACE_DENSITY_LB_ADDON') is not None:
                    recommendations['PLACE_DENSITY_LB_ADDON'] = retr['PLACE_DENSITY_LB_ADDON']
                learned_source = f"features:knn(k={retr['k']})"
                explanations.append(
                    f"Feature-KNN over {retr['k']} nearest clean runs "
                    f"({', '.join(retr['neighbors'][:5])}): "
                    f"CORE_UTILIZATION={retr['CORE_UTILIZATION']}, "
                    f"PLACE_DENSITY_LB_ADDON={recommendations.get('PLACE_DENSITY_LB_ADDON')}")
                retrieved = True
        # -----------------------------------------------------------------------
        families = knowledge_db.load_families(FAMILIES_PATH)
        family = knowledge_db.infer_family(config.get('DESIGN_NAME', ''), families)
        learned = None if retrieved else query_knowledge.get_family_heuristics(
            family, platform, heuristics_path=HEURISTICS_PATH,
        )
        if learned:
            cu = learned.get('core_utilization') or {}
            pd = learned.get('place_density_lb_addon') or {}
            if 'median' in cu:
                # Round to int to match the integer-percent convention of
                # params_by_size; a learned median can be a float (e.g. 22.5
                # from statistics.median on even-length samples).
                recommendations['CORE_UTILIZATION'] = int(round(cu['median']))
            if 'median' in pd:
                recommendations['PLACE_DENSITY_LB_ADDON'] = float(pd['median'])
            learned_source = f"{family}/{platform}"
            explanations.append(
                f"Learned heuristics for {family}/{platform} "
                f"(n={learned.get('sample_size', 0)}, "
                f"success_rate={learned.get('success_rate', 0):.2f}): "
                f"CORE_UTILIZATION={recommendations.get('CORE_UTILIZATION')}, "
                f"PLACE_DENSITY_LB_ADDON={recommendations.get('PLACE_DENSITY_LB_ADDON')}"
            )
    except _SkipLearned:
        # --no-learned / naive arm: deliberately fall through to the
        # hard-coded params_by_size baseline. Not an error.
        learned_source = None
    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
        # Malformed knowledge files should never break a real run.
        # Fall through to the hard-coded params_by_size baseline.
        learned_source = None
    # ----------------------------------------------------------------------

    # Design-type adjustments
    if design_type == 'bus_heavy':
        recommendations['CORE_UTILIZATION'] = min(recommendations['CORE_UTILIZATION'], 15)
        explanations.append(f'Bus-heavy design detected. Reduced CORE_UTILIZATION to {recommendations["CORE_UTILIZATION"]}%.')

    if design_type == 'macro_heavy':
        recommendations['PLACE_DENSITY_LB_ADDON'] = max(recommendations['PLACE_DENSITY_LB_ADDON'], 0.30)
        explanations.append(f'Macro-heavy design. Increased PLACE_DENSITY_LB_ADDON to {recommendations["PLACE_DENSITY_LB_ADDON"]}.')

    if design_type == 'crypto':
        recommendations['CORE_UTILIZATION'] = min(recommendations['CORE_UTILIZATION'], 25)
        explanations.append('Crypto/datapath design. Moderate utilization for routing flexibility.')

    # Safety flags for large designs
    if size_class == 'large' or cell_count > 50000:
        recommendations['SKIP_CTS_REPAIR_TIMING'] = 1
        recommendations['SKIP_LAST_GASP'] = 1
        recommendations['SKIP_GATE_CLONING'] = 1
        explanations.append('Large design (>50K cells). Added safety flags to prevent CTS crashes.')

    # LVS timeout recommendation based on estimated cell count
    if size_class == 'large' or (design_type == 'macro_heavy' and size_class == 'medium'):
        recommendations['LVS_TIMEOUT'] = 7200
        explanations.append('Large/macro design. KLayout LVS needs extended timeout (7200s).')

    # GDS_ALLOW_EMPTY for fakeram designs
    if design_type == 'macro_heavy':
        recommendations['GDS_ALLOW_EMPTY'] = 'fakeram.*'
        explanations.append('Macro design. Added GDS_ALLOW_EMPTY for fakeram stubs.')

    # Tiny design: suggest explicit die area
    if size_class == 'tiny':
        recommendations['DIE_AREA'] = '0 0 50 50'
        recommendations['CORE_AREA'] = '2 2 48 48'
        explanations.append('Tiny design (<100 cells). Use explicit DIE_AREA to avoid PDN grid errors.')
        # Remove CORE_UTILIZATION for tiny designs
        recommendations.pop('CORE_UTILIZATION', None)

    # Hard safety floor (CLAUDE.md): never recommend PLACE_DENSITY_LB_ADDON
    # below 0.10 — placer divergence is irrecoverable. Applied last so it
    # clamps learned medians and design-type adjustments alike.
    if "PLACE_DENSITY_LB_ADDON" in recommendations:
        recommendations["PLACE_DENSITY_LB_ADDON"] = max(
            float(recommendations["PLACE_DENSITY_LB_ADDON"]), 0.10
        )

    # Always recommend these
    recommendations['ABC_AREA'] = 1

    # Platform-specific adjustments
    if platform in ('sky130hd', 'sky130hs'):
        if 'PLACE_DENSITY' not in config:
            explanations.append('sky130 platform: consider higher PLACE_DENSITY (0.50+) vs nangate45 default (0.30).')

    return {
        'design_name': config.get('DESIGN_NAME', 'unknown'),
        'platform': platform,
        'cell_count': cell_count,
        'size_class': size_class,
        'design_type': design_type,
        'synth_stats': synth_stats,
        'recommendations': recommendations,
        'explanations': explanations,
        'learned_source': learned_source,
    }


def main():
    p = argparse.ArgumentParser(
        description='Design-aware ORFS parameter recommender.',
        usage='%(prog)s [--no-learned] <project-dir> [output.json]',
    )
    p.add_argument('project', type=Path, help='Path to the project directory')
    p.add_argument('output_file', nargs='?', default=None,
                   help='Optional output.json path (default: stdout)')
    p.add_argument('--no-learned', dest='use_learned', action='store_false',
                   help='Bypass learned heuristics; emit the naive '
                        'params_by_size baseline (still clamped + floored). '
                        'This is the naive arm of the payoff A/B harness.')
    args = p.parse_args()

    project = args.project
    output_file = args.output_file

    result = recommend(project, use_learned=args.use_learned)

    if output_file:
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        Path(output_file).write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(f'Recommendations written to {output_file}')
    else:
        print(json.dumps(result, indent=2))

    # Print human-readable summary
    print(f'\nDesign: {result["design_name"]} ({result["size_class"]}, {result["design_type"]})', file=sys.stderr)
    print(f'Cell count: {result["cell_count"]}', file=sys.stderr)
    print(f'\nRecommended parameters:', file=sys.stderr)
    for k, v in result['recommendations'].items():
        print(f'  export {k} = {v}', file=sys.stderr)
    for explanation in result['explanations']:
        print(f'  Note: {explanation}', file=sys.stderr)


if __name__ == '__main__':
    main()
