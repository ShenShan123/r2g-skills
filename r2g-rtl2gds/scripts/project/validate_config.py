#!/usr/bin/env python3
"""Pre-synthesis validation for ORFS config.mk and RTL files.

Usage: validate_config.py <project-dir> [output.json]

Checks:
  1. config.mk has required fields (DESIGN_NAME, PLATFORM, VERILOG_FILES, SDC_FILE)
  2. Floorplan initialization method is specified
  3. All referenced files exist
  4. DESIGN_NAME matches a module declaration in the RTL
  5. RTL files don't use reserved Verilog keywords as identifiers
"""

import json
import os
import re
import sys

# Verilog-2005 / SystemVerilog reserved keywords commonly misused as identifiers
RESERVED_KEYWORDS = {
    "int", "bit", "logic", "byte", "shortint", "longint", "shortreal",
    "string", "type", "void", "chandle", "event", "real", "realtime",
}

REQUIRED_FIELDS = ["DESIGN_NAME", "VERILOG_FILES", "SDC_FILE"]


def parse_config_mk(config_path):
    """Parse config.mk and return a dict of key-value pairs."""
    fields = {}
    with open(config_path, "r") as f:
        content = f.read()

    # Handle backslash line continuations
    content = content.replace("\\\n", " ")

    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        # Match: export KEY = VALUE  or  KEY = VALUE
        m = re.match(r"(?:export\s+)?(\w+)\s*=\s*(.*)", line)
        if m:
            key = m.group(1)
            val = m.group(2).strip()
            fields[key] = val
    return fields


def resolve_verilog_files(verilog_files_str):
    """Parse VERILOG_FILES value into a list of paths, expanding $(PLATFORM_DIR) etc."""
    # Split on whitespace, filtering out make variable references
    paths = []
    for token in verilog_files_str.split():
        if token.startswith("$(") or token.startswith("${"):
            continue  # Skip make variable references
        paths.append(token)
    return paths


def check_module_match(verilog_files, design_name):
    """Check if any Verilog file contains a module matching DESIGN_NAME."""
    pattern = re.compile(r"^\s*module\s+" + re.escape(design_name) + r"\b", re.MULTILINE)
    for vf in verilog_files:
        if not os.path.isfile(vf):
            continue
        try:
            with open(vf, "r") as f:
                content = f.read()
            if pattern.search(content):
                return True, vf
        except (OSError, UnicodeDecodeError):
            continue
    return False, None


def check_reserved_keywords(verilog_files):
    """Scan RTL files for reserved keywords used as port or signal names.

    ORFS reads Verilog with `read_verilog -defer -sv`, which enables
    SystemVerilog mode. Designs that legally use SV-reserved words as
    port/wire/reg names (e.g., Faraday DMA's `wire [...] int;`) hit
    `syntax error, unexpected TOK_INT` during 1_1_yosys_canonicalize.

    We flag declarations of any kind — port (input/output/inout), net
    (wire/tri/supply*), variable (reg/integer/logic) — that name a
    reserved keyword. Matches both single declarations and the first
    identifier in a comma list (`wire a, int, b;` → `int` flagged).
    """
    warnings = []
    kw_alt = "|".join(RESERVED_KEYWORDS)
    # Declaration heads we recognize. Order matters only for readability.
    decl_head = (
        r"\b(?:input|output|inout|wire|reg|logic|integer|tri|wand|wor|trireg|"
        r"supply0|supply1|bit|byte|shortint|longint|int|logic)\b"
    )
    # Optional signedness, optional packed-range, then the identifier.
    decl_pattern = re.compile(
        decl_head
        + r"\s+(?:signed\s+|unsigned\s+)?(?:\[[^\]]*\]\s+)?(?:signed\s+|unsigned\s+)?"
        + r"(" + kw_alt + r")\s*[;,=]"
    )
    # Catch `wire a, int, b;` — keyword in the middle of a comma list.
    list_pattern = re.compile(r",\s*(" + kw_alt + r")\s*[;,=]")
    for vf in verilog_files:
        if not os.path.isfile(vf):
            continue
        try:
            with open(vf, "r") as f:
                for lineno, line in enumerate(f, 1):
                    stripped = line.split("//", 1)[0]
                    seen = set()
                    for pat in (decl_pattern, list_pattern):
                        for m in pat.finditer(stripped):
                            kw = m.group(1)
                            if kw in seen:
                                continue
                            seen.add(kw)
                            warnings.append(
                                f"{os.path.basename(vf)}:{lineno}: reserved keyword "
                                f"'{kw}' used as port/signal name"
                            )
        except (OSError, UnicodeDecodeError):
            continue
    return warnings


def check_include_files(verilog_files, include_dirs):
    """Check if all `include files can be resolved."""
    warnings = []
    include_pattern = re.compile(r'`include\s+"([^"]+)"')
    for vf in verilog_files:
        if not os.path.isfile(vf):
            continue
        vf_dir = os.path.dirname(vf)
        try:
            with open(vf, "r") as f:
                for lineno, line in enumerate(f, 1):
                    m = include_pattern.search(line)
                    if m:
                        inc_file = m.group(1)
                        found = False
                        # Check relative to file, then each include dir
                        search_dirs = [vf_dir] + include_dirs
                        for d in search_dirs:
                            if os.path.isfile(os.path.join(d, inc_file)):
                                found = True
                                break
                        if not found:
                            warnings.append(
                                f"{os.path.basename(vf)}:{lineno}: include file '{inc_file}' "
                                f"not found in {search_dirs}"
                            )
        except (OSError, UnicodeDecodeError):
            continue
    return warnings


def check_clock_port_match(verilog_files, sdc_path, design_name):
    """Check that SDC clock port name exists as a port in the top-level RTL module."""
    warnings = []
    if not os.path.isfile(sdc_path):
        return warnings

    sdc_text = open(sdc_path, 'r').read()
    clock_ports = set()
    # Match: set clk_port_name <name>
    for m in re.finditer(r'set\s+clk_port_name\s+(\S+)', sdc_text):
        port = m.group(1).strip()
        if port and not port.startswith('$') and not port.startswith('{'):
            clock_ports.add(port)
    # Match: get_ports <name> (but not variable references or option flags
    # like `-quiet`/`-regexp`/`-of_objects`/`-filter`).
    for m in re.finditer(r'get_ports\s+((?:-\w+\s+)*)([^\]\}\s\$]+)', sdc_text):
        port = m.group(2).strip('{}[]')
        if port and not port.startswith('$') and not port.startswith('-'):
            clock_ports.add(port)

    if not clock_ports:
        return warnings

    # Find all port names in the top-level module
    top_ports = set()
    module_pattern = re.compile(
        r'module\s+' + re.escape(design_name) + r'\b(.*?);',
        re.DOTALL | re.MULTILINE
    )
    for vf in verilog_files:
        if not os.path.isfile(vf):
            continue
        try:
            content = open(vf, 'r').read()
            mm = module_pattern.search(content)
            if mm:
                port_text = mm.group(1)
                for ident in re.findall(r'\b(\w+)\b', port_text):
                    top_ports.add(ident)
                # Also scan input/output declarations after module header
                remainder = content[mm.end():]
                for line in remainder.split('\n')[:200]:
                    if re.match(r'\s*(?:input|output|inout)', line):
                        for port_id in re.findall(r'(\w+)\s*(?:[;,\)\[])', line):
                            top_ports.add(port_id)
                    elif re.match(r'\s*endmodule', line):
                        break
                break
        except (OSError, UnicodeDecodeError):
            continue

    if not top_ports:
        return warnings

    for cp in clock_ports:
        if cp not in top_ports:
            clk_candidates = sorted([p for p in top_ports if 'clk' in p.lower() or 'clock' in p.lower()])
            warnings.append(
                f"SDC clock port '{cp}' not found in top module '{design_name}' ports. "
                f"This causes unconstrained timing (WNS=1e+39). "
                f"Clock-like ports found: {clk_candidates}"
            )

    return warnings


PARAM_RANGES = {
    "PLACE_DENSITY_LB_ADDON": (0.10, 0.50, "Placement diverges below 0.10 (CLAUDE.md hard rule)"),
    "CORE_UTILIZATION": (5, 75, "Below 5% wastes area; above 75% causes routing congestion"),
    "PLACE_DENSITY": (0.30, 0.95, "Below 0.30 causes placement failure"),
}


def check_parameter_ranges(fields):
    """Validate ORFS parameter values are within safe ranges."""
    warnings = []
    for param, (lo, hi, reason) in PARAM_RANGES.items():
        if param in fields:
            try:
                val = float(fields[param])
                if val < lo:
                    warnings.append(f"{param}={val} is below minimum safe value {lo}. {reason}")
                elif val > hi:
                    warnings.append(f"{param}={val} is above maximum safe value {hi}. {reason}")
            except ValueError:
                pass
    return warnings


def validate(project_dir):
    errors = []
    warnings = []

    config_path = os.path.join(project_dir, "constraints", "config.mk")
    if not os.path.isfile(config_path):
        return {"valid": False, "errors": ["config.mk not found"], "warnings": []}

    fields = parse_config_mk(config_path)

    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in fields:
            errors.append(f"Missing required field: {field}")

    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings}

    design_name = fields["DESIGN_NAME"]
    verilog_files_str = fields.get("VERILOG_FILES", "")
    sdc_file = fields.get("SDC_FILE", "")

    # Check floorplan initialization
    has_util = "CORE_UTILIZATION" in fields
    has_area = "DIE_AREA" in fields or "CORE_AREA" in fields
    if not has_util and not has_area:
        errors.append(
            "No floorplan initialization: config.mk needs CORE_UTILIZATION or DIE_AREA/CORE_AREA"
        )

    # Resolve and check file paths
    verilog_files = resolve_verilog_files(verilog_files_str)
    for vf in verilog_files:
        if not os.path.isfile(vf):
            errors.append(f"VERILOG_FILES: file not found: {vf}")

    if sdc_file and not sdc_file.startswith("$("):
        if not os.path.isfile(sdc_file):
            errors.append(f"SDC_FILE not found: {sdc_file}")

    # Check DESIGN_NAME matches RTL module
    if verilog_files:
        match, match_file = check_module_match(verilog_files, design_name)
        if not match:
            errors.append(
                f"DESIGN_NAME '{design_name}' does not match any module declaration in VERILOG_FILES"
            )

    # Check reserved keywords
    kw_warnings = check_reserved_keywords(verilog_files)
    warnings.extend(kw_warnings)

    # Check include files
    include_dirs_str = fields.get("VERILOG_INCLUDE_DIRS", "")
    include_dirs = [d for d in include_dirs_str.split() if not d.startswith("$(")]
    inc_warnings = check_include_files(verilog_files, include_dirs)
    warnings.extend(inc_warnings)

    # Check SDC clock port matches RTL
    if sdc_file and not sdc_file.startswith("$(") and os.path.isfile(sdc_file):
        clk_warnings = check_clock_port_match(verilog_files, sdc_file, design_name)
        for w in clk_warnings:
            errors.append(w)

    # Check parameter ranges
    range_warnings = check_parameter_ranges(fields)
    warnings.extend(range_warnings)

    # Check ADDITIONAL_LEFS files exist
    add_lefs = fields.get('ADDITIONAL_LEFS', '')
    if add_lefs:
        for lef_token in add_lefs.split():
            if '$(' in lef_token or '${' in lef_token:
                continue
            if not os.path.isfile(lef_token):
                warnings.append(
                    f"ADDITIONAL_LEFS file not found: {lef_token}. "
                    f"Macro LEF must exist for floorplanning."
                )

    # Check ADDITIONAL_LIBS files exist
    add_libs = fields.get('ADDITIONAL_LIBS', '')
    if add_libs:
        for lib_token in add_libs.split():
            if '$(' in lib_token or '${' in lib_token:
                continue
            if not os.path.isfile(lib_token):
                warnings.append(
                    f"ADDITIONAL_LIBS file not found: {lib_token}. "
                    f"Macro LIB must exist for synthesis and timing."
                )

    # Check DIE_AREA > CORE_AREA
    die_area = fields.get('DIE_AREA', '')
    core_area = fields.get('CORE_AREA', '')
    if die_area and core_area:
        try:
            die_coords = [float(x) for x in die_area.split()]
            core_coords = [float(x) for x in core_area.split()]
            if len(die_coords) == 4 and len(core_coords) == 4:
                die_w = die_coords[2] - die_coords[0]
                die_h = die_coords[3] - die_coords[1]
                core_w = core_coords[2] - core_coords[0]
                core_h = core_coords[3] - core_coords[1]
                if core_w >= die_w or core_h >= die_h:
                    warnings.append(
                        f"CORE_AREA ({core_area}) is not smaller than DIE_AREA ({die_area}). "
                        f"Core must fit inside die with margin for IO pads and power rings."
                    )
                if core_coords[0] < die_coords[0] or core_coords[1] < die_coords[1]:
                    warnings.append(
                        f"CORE_AREA origin ({core_coords[0]}, {core_coords[1]}) is outside "
                        f"DIE_AREA origin ({die_coords[0]}, {die_coords[1]})."
                    )
        except (ValueError, IndexError):
            pass

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "design_name": design_name,
        "platform": fields.get("PLATFORM", "unknown"),
        "num_verilog_files": len(verilog_files),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: validate_config.py <project-dir> [output.json]", file=sys.stderr)
        sys.exit(1)

    project_dir = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    result = validate(project_dir)

    if output_file:
        with open(output_file, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Validation result written to {output_file}")
    else:
        print(json.dumps(result, indent=2))

    if not result["valid"]:
        print(f"\nVALIDATION FAILED: {len(result['errors'])} error(s)", file=sys.stderr)
        for e in result["errors"]:
            print(f"  ERROR: {e}", file=sys.stderr)
    if result["warnings"]:
        print(f"\n{len(result['warnings'])} warning(s):", file=sys.stderr)
        for w in result["warnings"]:
            print(f"  WARN: {w}", file=sys.stderr)

    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
