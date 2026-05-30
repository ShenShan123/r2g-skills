
import re
import sys
import os
import csv
import math

# sys.path bootstrap: make `import techlib.*` resolve when run via run_labels.sh
# (cwd is the project dir, not scripts/extract). Insert scripts/extract/ = the
# parent of this file's directory (labels/). Dup-guarded.
_EXTRACT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _EXTRACT_DIR not in sys.path:
    sys.path.insert(0, _EXTRACT_DIR)

from techlib.def_parse import route_segments  # noqa: E402

def parse_def_wirelength(def_file):
    wirelengths = {}
    net_types = {}
    current_net = None
    design_name = "unknown"
    
    # Defaults (should match DEF header ideally)
    db_units = 2000.0
    
    net_start_pattern = re.compile(r'^\s*-\s+(\S+)')
    
    with open(def_file, 'r') as f:
        lines = f.readlines()

    # Parse UNITS
    for line in lines:
        if line.startswith('DESIGN'):
            parts = line.split()
            if len(parts) >= 2:
                design_name = parts[1]
        elif line.startswith('UNITS DISTANCE MICRONS'):
            parts = line.split()
            if len(parts) >= 4:
                try:
                    db_units = float(parts[3])
                except ValueError: pass
        elif line.startswith('COMPONENTS'):
            break

    in_nets = False
    for line in lines:
        line = line.strip()
        if line.startswith('NETS') and not line.startswith('END NETS') and not line.startswith('SPECIALNETS'):
            in_nets = True
            continue
        if line.startswith('END NETS'):
            in_nets = False
            continue
            
        if not in_nets:
            continue

        if line.startswith(';'):
            continue
            
        # Check for new net
        net_match = net_start_pattern.match(line)
        if net_match:
            current_net = net_match.group(1)
            wirelengths[current_net] = 0.0
            net_types[current_net] = "SIGNAL"

        if current_net and 'USE' in line:
            tokens = line.replace(';', ' ').split()
            if 'USE' in tokens:
                use_idx = tokens.index('USE')
                if use_idx + 1 < len(tokens):
                    net_types[current_net] = tokens[use_idx + 1].upper()
            if net_match:
                continue
            
        if current_net and ('ROUTED' in line or 'NEW' in line):
            # techlib.route_segments reproduces the prior inline point-extraction +
            # *-relative Manhattan chain walk byte-for-byte (proven 0-mismatch on
            # aes_core + cordic; see tests/test_techlib_def_parse.py).
            for x1, y1, x2, y2 in route_segments(line):
                wirelengths[current_net] += abs(x2 - x1) + abs(y2 - y1)

    # Convert DB units to Microns
    for net in wirelengths:
        wirelengths[net] = wirelengths[net] / db_units
        
    return wirelengths, net_types, design_name

def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_wirelength.py <def_file> <output_csv> [design_name]")
        def_file = os.path.join(os.path.dirname(__file__), '../6_final.def')
        output_csv = os.path.join(os.path.dirname(__file__), 'wirelength.csv')
        design_name = None
    else:
        def_file = sys.argv[1]
        output_csv = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), 'wirelength.csv')
        design_name = sys.argv[3] if len(sys.argv) > 3 else None
    
    if not os.path.exists(def_file):
        print(f"Error: {def_file} not found.")
        sys.exit(1)
        
    print(f"Extracting wirelength from {def_file}...")
    wl_map, net_types, parsed_design_name = parse_def_wirelength(def_file)
    if design_name is None:
        design_name = parsed_design_name
    
    print(f"Writing to {output_csv}...")
    with open(output_csv, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Design', 'Net', 'NetType', 'WireLength_um', 'label', 'mask_wl'])
        for net, length in wl_map.items():
            net_type = net_types.get(net, "SIGNAL")
            label = math.log1p(length)
            mask_wl = net_type == "SIGNAL"
            writer.writerow([design_name, net, net_type, f"{length:.4f}", f"{label:.9f}", str(mask_wl).lower()])
            
    print(f"Done. Processed {len(wl_map)} nets.")

if __name__ == "__main__":
    main()
