"""Build an OAI21 reference from the independently published ASAP7 PEX.

Only parasitic resistor connectivity is contracted, and parasitic capacitors are
removed. Every transistor and its model, L, W, and nfin is retained. The tested
design's extracted netlist is not an input to this derivation.
"""
from collections import Counter
from pathlib import Path
from itertools import product
import hashlib
import json
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CELL = "OAI21x1_ASAP7_75t_R"
COMMIT = "e5bd25da72cd297b8c0ec6f77600e0cbaa1124d0"
UPSTREAM = HERE / "upstream_extracted_R.sp"


def logical_lines(text):
    out = []
    for line in text.splitlines():
        if line.startswith("+"):
            out[-1] += " " + line[1:].strip()
        else:
            out.append(line.strip())
    return out


def circuits(text):
    result = {}
    current = None
    for line in logical_lines(text):
        if line.lower().startswith(".subckt "):
            fields = line.split()
            current = {"ports": fields[2:], "lines": []}
            result[fields[1].lower()] = current
        elif line.lower().startswith(".ends"):
            current = None
        elif current is not None and line and not line.startswith("*"):
            current["lines"].append(line)
    return result


class Union:
    def __init__(self):
        self.parent = {}

    def find(self, node):
        node = node.upper()
        self.parent.setdefault(node, node)
        if self.parent[node] != node:
            self.parent[node] = self.find(self.parent[node])
        return self.parent[node]

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def derive(cell_name=CELL, all_cells=None):
    if all_cells is None:
        all_cells = circuits(UPSTREAM.read_text())
    top = all_cells[cell_name.lower()]
    connectivity = Union()
    devices = []
    parasitic_resistor_count = 0
    parasitic_capacitor_count = 0
    for line in top["lines"]:
        fields = line.split("$", 1)[0].split()
        kind = fields[0][0].upper()
        if kind == "M":
            devices.append(fields)
        elif kind == "C":
            parasitic_capacitor_count += 1
        elif kind == "X":
            sub = all_cells[fields[-1].lower()]
            assert len(fields[1:-1]) == len(sub["ports"])
            port_map = dict(zip((p.upper() for p in sub["ports"]), fields[1:-1]))
            for element in sub["lines"]:
                e = element.split("$", 1)[0].split()
                if e[0][0].upper() == "R":
                    parasitic_resistor_count += 1
                    n1 = port_map.get(e[1].upper(), fields[0] + "/" + e[1])
                    n2 = port_map.get(e[2].upper(), fields[0] + "/" + e[2])
                    connectivity.union(n1, n2)
                else:
                    assert e[0][0].upper() == "C", element
                    parasitic_capacitor_count += 1
        else:
            raise AssertionError(line)

    canonical = {}
    for port in top["ports"]:
        group = connectivity.find(port)
        assert group not in canonical, "Resistors must not short distinct cell ports"
        canonical[group] = port
    for d in devices:
        for node in d[1:5]:
            canonical.setdefault(connectivity.find(node), node)
        d[1:5] = [canonical[connectivity.find(node)] for node in d[1:5]]
    if cell_name == CELL:
        assert len(devices) == 12
        assert Counter(d[5] for d in devices) == {"nmos_rvt": 6, "pmos_rvt": 6}
    return devices, parasitic_resistor_count, parasitic_capacitor_count


def truth_table(lines):
    mos = [line.split() for line in lines if line.upper().startswith("M")]
    result = []
    for a1, a2, b in product((0, 1), repeat=3):
        signals = {"A1": a1, "A2": a2, "B": b}
        union = Union()
        for d in mos:
            gate = signals[d[2].upper()]
            on = gate == (1 if d[5].startswith("nmos") else 0)
            if on:
                union.union(d[1], d[3])
        high = union.find("Y") == union.find("VDD")
        low = union.find("Y") == union.find("VSS")
        assert high != low, "Output floating or supply short"
        y = int(high)
        assert y == int(not ((a1 or a2) and b))
        result.append({"A1": a1, "A2": a2, "B": b, "Y": y})
    return result


def main():
    original = ROOT / "gcd_calibre_input/gcd_device_complete.cdl"
    text = original.read_text()
    pattern = re.compile(r"(?im)^\.SUBCKT " + re.escape(CELL) + r"\b[^\n]*\n.*?^\.ENDS[^\n]*", re.S)
    matches = list(pattern.finditer(text))
    assert len(matches) == 1
    original_block = matches[0].group()
    header = original_block.splitlines()[0]
    devices, resistors, capacitors = derive()
    new_lines = [" ".join(d) for d in devices]
    new_block = header + "\n" + "\n".join(new_lines) + "\n.ENDS"
    old_truth = truth_table(original_block.splitlines())
    new_truth = truth_table(new_lines)
    assert old_truth == new_truth
    replacement = "* OAI21x1 transistor topology from independently published official xACT PEX.\n" + new_block
    output_text = text[:matches[0].start()] + replacement + text[matches[0].end():]
    output = HERE / "gcd_oai21_official_pex.cdl"
    output.write_text(output_text)
    (HERE / "oai21_reference.cdl").write_text(replacement + "\n")
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    report = {
        "source_url": f"https://github.com/The-OpenROAD-Project/asap7sc7p5t_28/blob/{COMMIT}/CDL/xAct3D_extracted/asap7sc7p5t_28_R.sp",
        "upstream_commit": COMMIT,
        "upstream_pex_sha256": digest(UPSTREAM),
        "input_sha256": digest(original),
        "output_sha256": digest(output),
        "replaced_cell": CELL,
        "original_devices": 6,
        "upstream_transistors_retained": len(devices),
        "contracted_parasitic_resistors": resistors,
        "omitted_parasitic_capacitors": capacitors,
        "port_order_preserved": header,
        "tested_layout_extraction_used_to_generate_reference": False,
        "other_cells_or_top_instances_modified": False,
        "original_and_candidate_truth_tables_equal": old_truth == new_truth,
        "function": "Y = !((A1 | A2) & B)",
        "truth_table": new_truth,
    }
    (HERE / "oai21_reference_provenance.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
