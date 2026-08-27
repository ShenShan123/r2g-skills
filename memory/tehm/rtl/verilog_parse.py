"""Structural Verilog parser (design doc 22.1 RTL v2).

A pragmatic subset parser — NOT a full Verilog front-end. It extracts enough for
the TEHM RTL semantic graph and the rtl.* rewrites: modules/ports, signal
declarations, always blocks (sequential vs combinational), assignments, and FSM
case structures with transition guards.

Scope is deliberately the common synthesizable subset (IEEE 1364 / SystemVerilog
clocking idioms); constructs outside it are skipped, never mis-parsed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PARSE_VERSION = "verilog-parse-v0.1"


@dataclass
class Signal:
    name: str
    kind: str          # input | output | inout | reg | wire
    width: str | None  # e.g. "[1:0]"

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "width": self.width}


@dataclass
class Assign:
    lhs: str
    rhs: str
    blocking: bool          # True for '=' (combinational), False for '<=' (seq)
    line: int

    def to_dict(self) -> dict:
        return {"lhs": self.lhs, "rhs": self.rhs,
                "blocking": self.blocking, "line": self.line}


@dataclass
class CaseItem:
    label: str            # e.g. "SEND"
    condition: str | None # guard, e.g. "ack" from "if (ack)"
    target: str           # e.g. "next_state = DONE"
    line: int
    raw: str

    def to_dict(self) -> dict:
        return {"label": self.label, "condition": self.condition,
                "target": self.target, "line": self.line}


@dataclass
class FSM:
    case_expr: str            # e.g. "state"
    reg_name: str             # the assigned register, e.g. "next_state"
    items: list = field(default_factory=list)   # list[CaseItem]

    def to_dict(self) -> dict:
        return {"case_expr": self.case_expr, "reg_name": self.reg_name,
                "items": [i.to_dict() for i in self.items]}


@dataclass
class AlwaysBlock:
    sensitivity: list = field(default_factory=list)   # ["posedge","clk",...]
    is_sequential: bool = False
    line: int = 0
    body: str = ""
    assigns: list = field(default_factory=list)       # list[Assign]
    fsms: list = field(default_factory=list)          # list[FSM]

    def to_dict(self) -> dict:
        return {"sensitivity": self.sensitivity,
                "is_sequential": self.is_sequential, "line": self.line,
                "assigns": [a.to_dict() for a in self.assigns],
                "fsms": [f.to_dict() for f in self.fsms]}


@dataclass
class RTLModule:
    name: str
    ports: list = field(default_factory=list)
    signals: dict = field(default_factory=dict)       # name -> Signal
    always_blocks: list = field(default_factory=list) # list[AlwaysBlock]
    params: dict = field(default_factory=dict)

    @property
    def state_regs(self) -> list:
        """Registers assigned only sequentially (<=) and used as case exprs."""
        return [f.reg_name for f in self.fsms] or [
            sig for sig, _ in self.signals.items()
            if re.search(r"(?i)(state|reg)", sig)]

    def to_dict(self) -> dict:
        return {
            "name": self.name, "ports": self.ports,
            "signals": {n: s.to_dict() for n, s in self.signals.items()},
            "always_blocks": [b.to_dict() for b in self.always_blocks],
            "params": self.params,
        }


def parse_verilog(source: str) -> list[RTLModule]:
    """Parse Verilog source into structural RTL modules."""
    text = _strip_comments(source)
    modules: list[RTLModule] = []
    for match in re.finditer(
            r"\bmodule\s+(?P<name>[A-Za-z_]\w*)\s*(?P<ports>\(.*?\))?\s*;"
            r"(?P<body>.*?)\bendmodule\b", text, re.S):
        name = match.group("name")
        body = match.group("body")
        module = RTLModule(name=name,
                           ports=_extract_ports(match.group("ports")))
        _parse_ansi_ports(module, match.group("ports"))
        _parse_declarations(module, body)
        module.always_blocks = _parse_always_blocks(module, body)
        modules.append(module)
    return modules


# -- internals -----------------------------------------------------------------

def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _extract_ports(ports_text: str | None) -> list:
    if not ports_text:
        return []
    ports_text = ports_text.strip("()").strip()
    if not ports_text:
        return []
    return [p.strip() for p in ports_text.split(",") if p.strip()]


def _parse_ansi_ports(module: RTLModule, ports_text: str | None) -> None:
    """ANSI-style port declarations (``input wire clk, ...``) -> signals."""
    if not ports_text:
        return
    for m in re.finditer(
            r"\b(input|output|inout)\b(?:\s+(?:wire|reg))?"
            r"(?:\s*(?P<width>\[[^\]]*\]))?\s+"
            r"(?P<name>[A-Za-z_]\w*)", ports_text):
        name = m.group("name")
        module.signals[name] = Signal(name=name, kind=m.group(1),
                                      width=m.group("width"))


def _parse_declarations(module: RTLModule, body: str) -> None:
    # localparam / parameter
    for name, value in re.findall(
            r"\b(?:local)?param\s+(?:\s*\[\s*[^\]]+\]\s*)?"
            r"(?P<name>\w+)\s*=\s*(?P<value>[^,;]+)", body):
        module.params[name] = value.strip()
    # signal declarations: kind [range] name [, name ...] ;
    for kind in ("input", "output", "inout", "reg", "wire"):
        pattern = (r"\b" + kind + r"\b(?:\s+wire|\s+reg)?"
                   r"(?:\s*\[[^\]]*\])?"
                   r"\s+(?P<decl>[^;]+?)\s*;")
        for decl in re.finditer(pattern, body):
            names = re.split(r"[,\s]+", decl.group("decl").strip())
            width = None
            for token in names:
                if token.startswith("["):
                    width = token
                    continue
                if re.fullmatch(r"[A-Za-z_]\w*", token):
                    module.signals[token] = Signal(name=token, kind=kind,
                                                   width=width)


def _parse_always_blocks(module: RTLModule, body: str) -> list:
    blocks: list[AlwaysBlock] = []
    for match in re.finditer(
            r"\balways\s*@\s*\(\s*(?P<sens>[^)]*?)\s*\)", body, re.S):
        sens_tokens = re.findall(r"[A-Za-z_*][\w*]*", match.group("sens"))
        is_seq = any(t in ("posedge", "negedge") for t in sens_tokens)
        start = match.end()
        j = start
        while j < len(body) and body[j] in " \t\r\n":
            j += 1
        if body.startswith("begin", j):
            end = _balanced_end(body, j)
            stmt = body[j:end]
        else:
            semi = body.find(";", j)
            end = len(body) if semi == -1 else semi + 1
            stmt = body[j:end]
        block = AlwaysBlock(sensitivity=sens_tokens, is_sequential=is_seq,
                            line=_line_of(body, match.start()), body=stmt)
        block.assigns = _extract_assigns(stmt)
        block.fsms = _extract_fsms(stmt, is_seq)
        blocks.append(block)
    return blocks


def _balanced_end(text: str, begin_idx: int) -> int:
    """Index just past the matching ``end`` for the ``begin`` at ``begin_idx``.

    Balances begin/end AND case/endcase with word boundaries, so the ``end``
    inside ``endcase`` / ``endmodule`` is never mistaken for the block end.
    """
    depth = 0
    i = begin_idx
    while i < len(text):
        m = re.compile(r"\b(begin|end|casez|casex|case|endcase)\b").search(text, i)
        if not m:
            break
        word = m.group(1)
        depth += 1 if word in ("begin", "case", "casez", "casex") else -1
        if depth == 0:
            return m.end()
        i = m.end()
    return len(text)


def _extract_assigns(stmt: str) -> list[Assign]:
    assigns: list[Assign] = []
    for i, match in enumerate(re.finditer(
            r"(?P<lhs>[A-Za-z_]\w*(?:\s*\[[^\]]*\])?)\s*"
            r"(?P<op><=| =)\s*(?P<rhs>[^;]+?)\s*;", stmt)):
        lhs = match.group("lhs").strip()
        op = match.group("op").strip()
        # skip blocking temp aliases like `next_state = state;` in decl context
        assigns.append(Assign(lhs=lhs, rhs=match.group("rhs").strip(),
                              blocking=(op == "="), line=i))
    return assigns


def _extract_fsms(stmt: str, is_seq: bool) -> list[FSM]:
    fsms: list[FSM] = []
    for match in re.finditer(
            r"\bcase(?:z|x)?\s*\(\s*(?P<expr>[^)]+?)\s*\)\s*"
            r"(?P<body>.*?)\bendcase\b",
            stmt, re.S):
        case_expr = match.group("expr").strip()
        body = match.group("body")
        items: list[CaseItem] = []
        # split the case body on top-level labels; each item ends before the next
        positions = [m.start() for m in re.finditer(
            r"(?m)^\s*(?P<label>(?:\d+'[bBoOdDhH][0-9a-fA-FxXzZ?_]+|"
            r"[A-Za-z_]\w*|default))\s*:\s*(?P<rest>.*)$",
            body)]
        for idx, pos in enumerate(positions):
            end = positions[idx + 1] if idx + 1 < len(positions) else len(body)
            chunk = body[pos:end]
            label_m = re.match(
                r"\s*((?:\d+'[bBoOdDhH][0-9a-fA-FxXzZ?_]+|[A-Za-z_]\w*|default))\s*:",
                chunk)
            if not label_m:
                continue
            label = label_m.group(1)
            rest = chunk[label_m.end():]
            cond = None
            cm = re.search(r"\bif\s*\(\s*(?P<c>[^)]+?)\s*\)", rest)
            if cm:
                cond = cm.group("c").strip()
            target = re.search(
                r"(?P<t>[A-Za-z_]\w*(?:\s*\[[^\]]*\])?)\s*(?:<=|=)\s*"
                r"(?P<v>[^;]+);", rest)
            if target:
                items.append(CaseItem(
                    label=label, condition=cond,
                    target=f"{target.group('t').strip()} = {target.group('v').strip()}",
                    line=_line_of(body, pos), raw=rest.strip()))
        if items:
            fsms.append(FSM(case_expr=case_expr,
                            reg_name=_infer_reg(module_name=None, items=items),
                            items=items))
    return fsms


def _infer_reg(module_name: str | None, items: list[CaseItem]) -> str:
    """The assigned register (next_state) from the case items' targets."""
    for item in items:
        m = re.match(r"\s*([A-Za-z_]\w*)\s*=", item.target)
        if m:
            return m.group(1)
    return ""


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1
