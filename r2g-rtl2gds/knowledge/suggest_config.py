#!/usr/bin/env python3
"""Design-aware ORFS parameter recommender.

Usage: suggest_config.py <project-dir> [output.json]

Analyzes synthesis results and design characteristics to recommend
ORFS config.mk parameters (utilization, density, safety flags).
"""
import json
import os
import re
import sys
from pathlib import Path

import knowledge_db
import query_knowledge

HEURISTICS_PATH = knowledge_db.DEFAULT_KNOWLEDGE_DIR / "heuristics.json"
FAMILIES_PATH = knowledge_db.DEFAULT_FAMILIES_PATH


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


def recommend(project: Path) -> dict:
    """Generate parameter recommendations."""
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
        families = knowledge_db.load_families(FAMILIES_PATH)
        family = knowledge_db.infer_family(config.get('DESIGN_NAME', ''), families)
        learned = query_knowledge.get_family_heuristics(
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
    if len(sys.argv) < 2:
        print('Usage: suggest_config.py <project-dir> [output.json]', file=sys.stderr)
        sys.exit(1)

    project = Path(sys.argv[1])
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    result = recommend(project)

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
