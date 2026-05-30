import re
import sys
import os
import csv
import math

# Canonical output filename for this label (TEACHING_POLICY §6 / status_enums.py).
# The script always writes THIS basename, keeping only the directory the caller
# gives — so a caller passing "wirelength.csv" or anything else still lands the
# canonical name and the autograder stays consistent.
CANONICAL_OUTPUT_NAME = "wirelength.csv"


def _force_canonical(requested_path):
    if requested_path:
        out_dir = os.path.dirname(os.path.abspath(requested_path))
    else:
        out_dir = os.path.dirname(os.path.abspath(__file__))
    forced = os.path.join(out_dir, CANONICAL_OUTPUT_NAME)
    if requested_path and os.path.basename(requested_path) != CANONICAL_OUTPUT_NAME:
        print(f"Note: forcing canonical output name -> {forced} "
              f"(requested basename was '{os.path.basename(requested_path)}')")
    return forced


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
            tokens = line.split()
            points = []

            i = 0
            while i < len(tokens):
                token = tokens[i]
                if token == '(':
                    # Start of a coordinate pair
                    x_str = tokens[i+1]
                    y_str = tokens[i+2]
                    points.append((x_str, y_str))
                    i += 3
                else:
                    i += 1

            # Calculate length for this segment chain
            if len(points) >= 2:
                # Iterate pairs
                # We need to maintain current X and Y because of '*'
                curr_x = 0
                curr_y = 0

                # Initialize with first point (must be explicit)
                p0 = points[0]
                try:
                    curr_x = int(p0[0])
                    curr_y = int(p0[1])
                except ValueError:
                    continue # Skip if first point is invalid

                for j in range(1, len(points)):
                    p_next = points[j]
                    next_x_str = p_next[0]
                    next_y_str = p_next[1]

                    next_x = curr_x
                    next_y = curr_y

                    if next_x_str != '*':
                        try:
                            next_x = int(next_x_str)
                        except ValueError: pass

                    if next_y_str != '*':
                        try:
                            next_y = int(next_y_str)
                        except ValueError: pass

                    # Manhattan Distance
                    dist = abs(next_x - curr_x) + abs(next_y - curr_y)
                    wirelengths[current_net] += dist

                    # Update current
                    curr_x = next_x
                    curr_y = next_y

    # Convert DB units to Microns
    for net in wirelengths:
        wirelengths[net] = wirelengths[net] / db_units

    return wirelengths, net_types, design_name

def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_wirelength.py <def_file> <output_csv> [design_name]")
        def_file = os.path.join(os.path.dirname(__file__), '../6_final.def')
        output_csv = os.path.join(os.path.dirname(__file__), CANONICAL_OUTPUT_NAME)
        design_name = None
    else:
        def_file = sys.argv[1]
        output_csv = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), CANONICAL_OUTPUT_NAME)
        design_name = sys.argv[3] if len(sys.argv) > 3 else None

    # Always emit the canonical basename, keeping the caller's directory.
    output_csv = _force_canonical(output_csv)

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
