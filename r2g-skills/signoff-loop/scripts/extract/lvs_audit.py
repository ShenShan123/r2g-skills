"""Read-only, fail-closed audit of raw LVS evidence, never of its JSON verdict.

The supported format is KLayout's long-form LVS database. Counts refer to
normalized circuit definitions, not raw transistor fingers or flat net counts.
Netgen evidence remains UNKNOWN until its quantitative adapter is implemented.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET

MAX_BYTES = 32 * 1024 * 1024
MAX_TOKENS = 1_000_000
MAX_DEPTH = 40
SUPPORTED_DECK = Path(__file__).resolve().parents[2] / "assets/platforms/nangate45/lvs/FreePDK45.lylvs"
_TOKEN = re.compile(r"\s+|\#[^\n]*|'(?:\\.|[^'\\])*'|[()]|[^\s()'#]+")
_FINAL_OK = re.compile(r"^\s*(?:CONGRATULATIONS!\s*)?Netlists match\.\s*$", re.M | re.I)
_FINAL_BAD = re.compile(r"\b(?:netlists|circuits)\s+(?:don't|do not|did not|cannot)\s+match\b|\bno matching net\b", re.I)
_CRASH = re.compile(r"signal number:|segmentation fault|core dumped|internal error|compare raised exception|timed out", re.I)


class EvidenceError(ValueError):
    pass


class Node:
    __slots__ = ("tag", "items")

    def __init__(self, tag, items):
        self.tag, self.items = tag, items

    def children(self, tag):
        return [x for x in self.items if isinstance(x, Node) and x.tag == tag]

    def atoms(self):
        return [x for x in self.items if isinstance(x, str)]


def _tree(text):
    if not text.startswith("#%lvsdb-klayout"):
        raise EvidenceError("unsupported_lvsdb_format")
    tokens = []
    end = 0
    for match in _TOKEN.finditer(text):
        if match.start() != end:
            raise EvidenceError("invalid_lvsdb_token")
        end = match.end()
        value = match.group()
        if value.isspace() or value.startswith("#"):
            continue
        tokens.append(value)
        if len(tokens) > MAX_TOKENS:
            raise EvidenceError("lvsdb_token_limit")
    if end != len(text):
        raise EvidenceError("truncated_lvsdb_token")
    index = 0

    def group(depth, closing=False):
        nonlocal index
        if depth > MAX_DEPTH:
            raise EvidenceError("lvsdb_depth_limit")
        items = []
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if token == ")":
                if not closing:
                    raise EvidenceError("unbalanced_lvsdb")
                return items
            if token == "(":
                items.append(Node("", group(depth + 1, True)))
            elif index < len(tokens) and tokens[index] == "(":
                index += 1
                items.append(Node(token, group(depth + 1, True)))
            else:
                if token.startswith("'"):
                    token = re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m[1], m[1]), token[1:-1])
                items.append(token)
        if closing:
            raise EvidenceError("truncated_lvsdb")
        return items

    result = group(0)
    if any(not isinstance(n, Node) for n in result):
        raise EvidenceError("unsupported_lvsdb_format")
    if Counter(n.tag for n in result) != Counter({"layout": 1, "reference": 1, "xref": 1}):
        raise EvidenceError("incomplete_lvsdb_sections")
    return {n.tag: n for n in result}


def _one(node, name):
    values = node.children(name)
    if len(values) != 1:
        raise EvidenceError("missing_or_duplicate_" + name)
    return values[0]


def _index(nodes):
    out = {}
    for node in nodes:
        atoms = node.atoms()
        if not atoms or atoms[0] in out:
            raise EvidenceError("missing_or_duplicate_identifier")
        out[atoms[0]] = node
    return out


def _name(node):
    values = node.children("name")
    return values[0].atoms()[0] if len(values) == 1 and values[0].atoms() else None


def _cdl_top_ports(text, top):
    """Read OpenROAD's CDL subcircuit header, including continuation lines.

    This establishes port coverage before simplify/purge. It does not certify
    arbitrary SPICE expressions or infer that a removed port was harmless.
    """
    statements = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        if line.startswith("+"):
            if not statements:
                raise EvidenceError("invalid_cdl_continuation")
            statements[-1] += " " + line[1:]
        else:
            statements.append(line)
    headers = [line.split() for line in statements if line.lower().startswith(".subckt ")]
    matches = [parts[2:] for parts in headers if len(parts) > 1 and parts[1].casefold() == top.casefold()]
    if len(matches) != 1 or not matches[0] or any("=" in p or p.lower().startswith("params:") for p in matches[0]):
        raise EvidenceError("source_cdl_top_ports_unavailable")
    names = [p.casefold() for p in matches[0]]
    if len(set(names)) != len(names):
        raise EvidenceError("source_cdl_top_ports_ambiguous")
    return set(names)


def _side(section, layout):
    circuits = _index(section.children("circuit"))
    class_nodes = _index(section.children("class"))
    if any(len(n.atoms()) != 2 for n in class_nodes.values()):
        raise EvidenceError("invalid_device_class")
    classes = {key: n.atoms()[1] for key, n in class_nodes.items()}
    abstracts = {n.atoms()[0]: n.atoms()[1] for n in section.children("device") if len(n.atoms()) >= 2}
    counts, references, empty, bad_mos = {}, set(), [], []
    for name, circuit in circuits.items():
        groups = {kind: _index(circuit.children(kind)) for kind in ("net", "device", "circuit")}
        pins = circuit.children("pin")
        mos = 0
        for device in groups["device"].values():
            atoms = device.atoms()
            if len(atoms) < 2:
                raise EvidenceError("invalid_device")
            model = abstracts.get(atoms[1], atoms[1]) if layout else atoms[1]
            if model not in classes:
                raise EvidenceError("undefined_device_class")
            if classes.get(model) == "MOS4":
                mos += 1
                params = {n.atoms()[0]: n.atoms()[1] for n in device.children("param") if len(n.atoms()) == 2}
                terms = {n.atoms()[0] for n in device.children("terminal") if len(n.atoms()) == 2}
                try:
                    valid = all(math.isfinite(float(params[k])) and float(params[k]) > 0 for k in ("W", "L"))
                except (KeyError, ValueError):
                    valid = False
                if not valid or terms != {"S", "G", "D", "B"}:
                    bad_mos.append(name)
        for instance in groups["circuit"].values():
            atoms = instance.atoms()
            if len(atoms) < 2 or atoms[1] not in circuits:
                raise EvidenceError("undefined_subcircuit")
            references.add(atoms[1])
        if not groups["device"] and not groups["circuit"]:
            empty.append(name)
        counts[name] = {"nets": len(groups["net"]), "devices": len(groups["device"]),
                        "instances": len(groups["circuit"]), "pins": len(pins), "mos_devices": mos}
    tops = set(circuits) - references
    if len(tops) != 1:
        raise EvidenceError("missing_or_ambiguous_top")
    top = next(iter(tops))
    if layout and _one(section, "top").atoms() != [top]:
        raise EvidenceError("layout_top_mismatch")
    metrics = {"circuits": len(circuits), "top": top,
               **{key: sum(v[key] for v in counts.values()) for key in ("nets", "devices", "instances", "pins", "mos_devices")},
               **{"top_" + key: value for key, value in counts[top].items()},
               "count_scope": "normalized_hierarchical_definitions", "empty_circuits": empty,
               "invalid_mos_circuits": sorted(set(bad_mos))}
    metrics["device_classes"] = classes
    return circuits, counts, metrics


class Evidence:
    def __init__(self, project):
        self.project = Path(project).resolve()
        self.files = {}
        self.used = 0

    def read(self, path):
        path = Path(path)
        if not path.is_absolute():
            path = self.project / path
        try:
            relative = path.relative_to(self.project)
        except ValueError:
            raise EvidenceError("evidence_path_escape")
        if ".." in relative.parts or any(p.is_symlink() for p in (path, *path.parents) if p != self.project.parent):
            raise EvidenceError("evidence_symlink_refused")
        key = relative.as_posix()
        if key in self.files:
            return self.files[key][1]
        # Hold each directory by descriptor: an ancestor replaced with a
        # symlink after the checks above must not redirect the evidence read.
        directory = os.open(self.project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in relative.parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(relative.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES or self.used + before.st_size > MAX_BYTES * 4:
                raise EvidenceError("evidence_size_or_type_limit")
            data = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != before.st_size:
            raise EvidenceError("evidence_changed_during_read")
        self.used += len(data)
        self.files[key] = ({"path": key, "sha256": hashlib.sha256(data).hexdigest()}, data)
        return data

    def text(self, path):
        return self.read(path).decode("utf-8", errors="strict")

    def optional(self, path):
        try:
            return self.text(path)
        except FileNotFoundError:
            return ""


def _layer(name):
    return {"id": name, "verdict": "PASS", "reasons": [], "evidence": {}}


def _reason(layer, code, verdict="UNKNOWN"):
    if code not in layer["reasons"]:
        layer["reasons"].append(code)
    priority = {"PASS": 0, "UNKNOWN": 1, "FAIL": 2, "STALE": 3}
    if priority[verdict] > priority[layer["verdict"]]:
        layer["verdict"] = verdict


def _receipt(evidence, lvs_dir, final):
    raw = evidence.optional(lvs_dir / "lvs_execution.json")
    if not raw:
        _reason(final, "execution_receipt_missing")
        return None
    receipt = json.loads(raw)
    if (not isinstance(receipt, dict) or type(receipt.get("schema_version")) is not int
            or receipt["schema_version"] != 1 or receipt.get("engine") != "klayout"):
        raise EvidenceError("unsupported_execution_receipt")
    final["evidence"].update(exit_code=receipt.get("exit_code"), run_tag=receipt.get("run_tag"),
                              gds_sha256=receipt.get("gds_sha256"))
    if type(receipt.get("exit_code")) is not int or receipt["exit_code"] != 0:
        _reason(final, "checker_did_not_exit_successfully")
    required = {"6_lvs.lvsdb", "6_lvs.log", "lvs_run.log", "audit_rule.lylvs", "audit_masters.cdl", "6_final.cdl"}
    files = receipt.get("files")
    if not isinstance(files, dict) or not required.issubset(files):
        _reason(final, "execution_evidence_incomplete")
        return receipt
    for name in sorted(required):
        data = evidence.read(lvs_dir / name)
        if hashlib.sha256(data).hexdigest() != files[name]:
            _reason(final, "execution_evidence_changed:" + name, "STALE")
    tag = receipt.get("run_tag")
    if not isinstance(tag, str) or not re.fullmatch(r"RUN_[A-Za-z0-9_.-]+", tag):
        _reason(final, "backend_run_unbound")
        return receipt
    candidates = [evidence.project / "backend" / tag / sub / "6_final.gds" for sub in ("results", "final")]
    gds = next((p for p in candidates if p.exists()), None)
    if gds is None:
        _reason(final, "checked_layout_missing")
    elif hashlib.sha256(evidence.read(gds)).hexdigest() != receipt.get("gds_sha256"):
        _reason(final, "checked_layout_changed", "STALE")
    for key, name in (("rule_sha256", "audit_rule.lylvs"), ("masters_sha256", "audit_masters.cdl")):
        if receipt.get(key) != files[name]:
            _reason(final, "checker_input_changed:" + name, "STALE")
    if not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("tool_sha256", ""))):
        _reason(final, "checker_identity_unbound")
    return receipt


def audit(project: Path, lvs_dir: Path) -> dict:
    """Re-derive the three checks from bounded, project-local raw evidence."""
    evidence = Evidence(project)
    lvs_dir = Path(lvs_dir)
    final, quantitative, technical = [_layer(name) for name in ("final_result", "quantitative", "technical")]
    layers = [final, quantitative, technical]
    result = {"schema_version": 1, "verdict": "UNKNOWN", "layers": layers,
              "verification_level": "unverified", "metrics": {}, "evidence_files": [],
              "policy_sha256": None}
    try:
        result["policy_sha256"] = hashlib.sha256(Path(__file__).read_bytes() + SUPPORTED_DECK.read_bytes()).hexdigest()
        log = evidence.optional(lvs_dir / "6_lvs.log")
        runlog = evidence.optional(lvs_dir / "lvs_run.log")
        if _FINAL_BAD.search(log + "\n" + runlog):
            _reason(final, "explicit_comparison_failure", "FAIL")
        if not _FINAL_OK.search(log):
            _reason(final, "final_comparison_success_missing")
        if _CRASH.search(log + "\n" + runlog):
            _reason(final, "checker_crash_or_timeout")
        if re.search(r"^\s*(?:ERROR|FATAL)\s*:", log + "\n" + runlog, re.M | re.I):
            _reason(final, "checker_reported_error")
        receipt = _receipt(evidence, lvs_dir, final)
        tree = _tree(evidence.text(lvs_dir / "6_lvs.lvsdb"))
        left, lc, lm = _side(tree["layout"], True)
        right, rc, rm = _side(tree["reference"], False)
        result["metrics"].update(layout=lm, schematic=rm)
        for key in ("circuits", "devices", "nets", "instances", "pins"):
            if lm[key] != rm[key]:
                _reason(quantitative, "hierarchical_count_mismatch:" + key, "FAIL")
        pairs = tree["xref"].children("circuit")
        seen_left, seen_right, totals = set(), set(), Counter()
        for pair in pairs:
            atoms = pair.atoms()
            if len(atoms) != 3 or atoms[0] not in left or atoms[1] not in right:
                _reason(quantitative, "unmatched_circuit", "FAIL")
                continue
            a, b, status = atoms
            if a in seen_left or b in seen_right:
                _reason(quantitative, "duplicate_circuit_pair", "FAIL")
            seen_left.add(a); seen_right.add(b)
            totals["circuit_" + status] += 1
            if status != "match":
                _reason(quantitative, "circuit_not_matched:" + status, "FAIL" if status in ("mismatch", "nomatch", "no-match") else "UNKNOWN")
            if lc[a] != rc[b]:
                _reason(quantitative, "paired_circuit_count_mismatch:" + a, "FAIL")
            xref = _one(pair, "xref")
            for pairlog in pair.children("log"):
                for entry in pairlog.children("entry"):
                    descriptions = entry.children("description")
                    description = descriptions[0].atoms()[0] if descriptions and descriptions[0].atoms() else ""
                    # KLayout explicitly reports ambiguous but equivalent net
                    # pairings. Other warnings may concern device parameters.
                    known_ambiguity = ((entry.atoms() == ["warning"] and description.endswith("from an ambiguous group of nets"))
                                       or (entry.atoms() == ["info"] and description.endswith("following an ambiguous match")))
                    if not (known_ambiguity and description.startswith("Matching nets ")):
                        _reason(technical, "unclassified_comparison_diagnostic")
            for kind, metric in (("net", "nets"), ("device", "devices"), ("circuit", "instances"), ("pin", "pins")):
                rows = xref.children(kind)
                seen = [set(), set()]
                for row in rows:
                    values = row.atoms()
                    if len(values) != 3:
                        _reason(quantitative, "unmatched_" + kind, "FAIL")
                        continue
                    x, y, state = values
                    totals[("instance" if kind == "circuit" else kind) + "_" + state] += 1
                    for side, ident in enumerate((x, y)):
                        if ident in seen[side]:
                            _reason(quantitative, "duplicate_" + kind + "_pair", "FAIL")
                        seen[side].add(ident)
                    if state != "match" and not (kind == "net" and state == "warning"):
                        _reason(quantitative, kind + "_not_matched:" + state,
                                "FAIL" if state in ("mismatch", "nomatch", "no-match") else "UNKNOWN")
                if len(rows) != lc[a][metric] or len(rows) != rc[b][metric]:
                    _reason(quantitative, "xref_coverage_incomplete:" + a + ":" + kind, "UNKNOWN")
                expected_ids = ([set(str(i) for i in range(lc[a][metric])), set(str(i) for i in range(rc[b][metric]))]
                                if kind == "pin" else [set(_index(left[a].children(kind))), set(_index(right[b].children(kind)))])
                if seen != expected_ids:
                    _reason(quantitative, "xref_identity_coverage_incomplete:" + a + ":" + kind)
            if a == lm["top"]:
                if b != rm["top"]:
                    _reason(technical, "top_circuit_not_paired", "FAIL")
                lp, rp = left[a].children("pin"), right[b].children("pin")
                names = [[_name(p) for p in pins] for pins in (lp, rp)]
                if any(not n or len(set(x.casefold() for x in n if x)) != len(n) for n in names):
                    _reason(technical, "top_pin_names_missing_or_case_ambiguous")
                else:
                    for pin in xref.children("pin"):
                        p = pin.atoms()
                        try:
                            matching = names[0][int(p[0])].casefold() == names[1][int(p[1])].casefold()
                        except (ValueError, IndexError, AttributeError):
                            matching = False
                        if not matching:
                            _reason(technical, "top_pin_label_mismatch", "FAIL")
        result["metrics"]["xref"] = dict(totals)
        if seen_left != set(left) or seen_right != set(right):
            _reason(quantitative, "uncompared_circuit_definitions")
        if not pairs:
            _reason(final, "comparison_cross_reference_missing")
        if lm["mos_devices"] and rm["mos_devices"]:
            result["verification_level"] = "transistor_hierarchical"
        else:
            result["verification_level"] = "structure_only"
            _reason(technical, "mos_device_evidence_missing")
        for side, metrics in (("layout", lm), ("schematic", rm)):
            if metrics["empty_circuits"]:
                _reason(technical, side + "_empty_or_blackbox_circuits")
            if metrics["invalid_mos_circuits"]:
                _reason(technical, side + "_mos_parameters_or_terminals_incomplete")
            if any(value != "MOS4" for value in metrics["device_classes"].values()):
                _reason(technical, side + "_unsupported_device_classes")
        messages = []
        for section in (tree["layout"], tree["reference"]):
            for message in section.children("message"):
                description = message.children("description")
                value = description[0].atoms()[0] if description and description[0].atoms() else ""
                if value:
                    messages.append(value[:600])
                if "must-connect" in value.lower():
                    _reason(technical, "must_connect_not_closed_at_top")
                elif message.atoms() and message.atoms()[0] in ("warning", "error"):
                    _reason(technical, "unclassified_extraction_warning")
        technical["evidence"].update(extraction_messages=messages[:32], pin_name_policy="unique_SPICE_casefold")
        source_ports = _cdl_top_ports(evidence.text(lvs_dir / "6_final.cdl"), rm["top"])
        coverage = {}
        for side, circuit in (("layout", left[lm["top"]]), ("schematic", right[rm["top"]])):
            normalized_ports = {_name(p).casefold() for p in circuit.children("pin") if _name(p)}
            coverage[side] = {"missing": sorted(source_ports - normalized_ports),
                              "extra": sorted(normalized_ports - source_ports)}
            if coverage[side]["missing"] or coverage[side]["extra"]:
                _reason(technical, "source_top_port_coverage_not_closed:" + side)
        technical["evidence"]["source_top_port_coverage"] = coverage
        if re.search(r"black\s*box|blackboxing|ignored? (?:device|circuit|cell)|no matching net", log + "\n" + runlog, re.I):
            _reason(technical, "blackbox_or_ignored_structure_reported")
        deck = evidence.optional(lvs_dir / "audit_rule.lylvs")
        if not receipt or not deck:
            _reason(technical, "extraction_rule_not_bound")
        else:
            if hashlib.sha256(deck.encode()).digest() != hashlib.sha256(SUPPORTED_DECK.read_bytes()).digest():
                _reason(technical, "unregistered_extraction_rule")
            root = ET.fromstring(deck)
            code = root.findtext("text") or ""
            code = "\n".join(line.split("#", 1)[0] for line in code.splitlines())
            if re.search(r"\b(?:blank_circuit|same_nets|same_device_classes|ignore_parameter|cheat)\s*\(", code):
                _reason(technical, "rule_contains_unverified_comparison_exclusions")
            if not re.search(r"extract_devices\s*\(\s*mos4\s*\(", code):
                _reason(technical, "mos_extractor_not_established")
        metadata = json.loads(evidence.optional(evidence.project / "metadata.json") or "{}")
        if not isinstance(metadata, dict):
            raise EvidenceError("unsupported_project_metadata")
        expected = metadata.get("top")
        if not isinstance(expected, str):
            _reason(technical, "expected_top_not_recorded")
        elif expected.casefold() != lm["top"].casefold() or expected.casefold() != rm["top"].casefold():
            _reason(technical, "unexpected_top_circuit", "FAIL")
        final["evidence"].update(final_match_observed=bool(_FINAL_OK.search(log)), circuit_pairs=len(pairs))
        quantitative["evidence"]["count_scope"] = "normalized_hierarchical_definitions"
    except (OSError, ValueError, TypeError, KeyError, IndexError, ET.ParseError, UnicodeError, RecursionError) as exc:
        code = str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__
        for layer in layers:
            _reason(layer, "evidence_unavailable_or_unsupported:" + code[:160])
    result["evidence_files"] = [evidence.files[key][0] for key in sorted(evidence.files)]
    result["verdict"] = next((v for v in ("STALE", "FAIL", "UNKNOWN") if any(x["verdict"] == v for x in layers)), "PASS")
    return result
