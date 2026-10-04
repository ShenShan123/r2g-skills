"""DEV-only source-derived valid-bit/mux skid binding, not Memory authority.

A complete bounded active-module grammar unifies signal roles and captures one
payload mux RHS. No filesystem, oracle, gold edit, task ID or source-name input.
Inactive FORMAL text is opaque and preserved, under an explicit empty macro
context. This is not a general parser/preprocessor or functional proof.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from . import research_r5_skid_binding as base

CONTRACT = 'rtl_skid_mux_payload_binding_dev_v8'
OPERATOR = 'rtl.SKID_MUX_PAYLOAD_RESTORE_DEV_V8'
PROFILE = 'rtl.skid.mux_payload.v8.dev'
TEMPLATE = {'contract': CONTRACT, 'operator': OPERATOR, 'profile': PROFILE,
            'proof_scope': 'unique_syntactic_captured_payload_mux_not_functional'}
CONTEXT = {'WIDTH': 8, 'defined_macros': []}
IDENT = re.compile(r'[A-Za-z_][A-Za-z_0-9]*\Z')
TOKEN = re.compile(r"\$[A-Z_]+|1'b[01]|[A-Za-z_][A-Za-z_0-9]*|\d+|<=|&&|\|\||[^\w\s]")
DIRECTIVE = re.compile(r'(?m)^[ \t]*`([A-Za-z_]\w*)[^\r\n]*')

# Placeholders denote roles, not required target names or repair coordinates.
# Matching the WHOLE token stream rules out unmatched declarations or writers.
GRAMMAR = """
module $MODULE # (parameter int WIDTH = 8) (
 input logic $CLK, input logic $RST,
 input logic $IV, output logic $IR, input logic [WIDTH-1:0] $ID,
 input logic $IL, output logic $OV, input logic $OR,
 output logic [WIDTH-1:0] $OD, output logic $OL
);
logic $SV; logic [WIDTH-1:0] $SD; logic $SL; logic $XFER; logic $FREE;
assign $IR = !$SV;
assign $XFER = $IV && $IR;
assign $FREE = !$OV || $OR;
always_ff @(posedge $CLK) begin
 if (!$RST) $SV <= 1'b0;
 else if ($XFER && !$FREE) begin
  $SV <= 1'b1; $SD <= $ID; $SL <= $IL;
 end else if ($FREE) $SV <= 1'b0;
end
always_ff @(posedge $CLK) begin
 if (!$RST) $OV <= 1'b0;
 else if ($FREE) begin
  $OV <= $SV || $IV;
  $OD <= $SV ? $RHS : $ID;
  $OL <= $SV ? $SL : $IL;
 end
end
endmodule
"""
PATTERN = [m.group() for m in TOKEN.finditer(GRAMMAR)]


def result(status, reason, **fields):
    return {'contract': CONTRACT, 'status': status, 'reason': reason, **fields}


def blank(text):
    return ''.join('\n' if ch == '\n' else ' ' for ch in text)


def active_text(source):
    """Offset-preserving masking under the caller-validated macro context."""
    text = base._mask_comments(source)
    directives = list(DIRECTIVE.finditer(text))
    names = [m[1] for m in directives]
    allowed = ([], ['default_nettype', 'default_nettype'], ['ifdef', 'endif'],
               ['default_nettype', 'ifdef', 'endif', 'default_nettype'])
    if names not in allowed:
        raise ValueError('unsupported_nested_or_unknown_directives')
    nettype, formal = [], None
    ranges = []
    if names and names[0] == 'default_nettype':
        first, last = directives[0], directives[-1]
        if (not re.fullmatch(r'[ \t]*`default_nettype[ \t]+none[ \t]*', first[0])
                or not re.fullmatch(r'[ \t]*`default_nettype[ \t]+wire[ \t]*', last[0])):
            raise ValueError('nettype_pair_mismatch')
        nettype = [first.span(), last.span()]
        ranges.extend(nettype)
    if 'ifdef' in names:
        opening, closing = directives[names.index('ifdef')], directives[names.index('endif')]
        if (not re.fullmatch(r'[ \t]*`ifdef[ \t]+FORMAL[ \t]*', opening[0])
                or not re.fullmatch(r'[ \t]*`endif[ \t]*', closing[0])):
            raise ValueError('formal_guard_mismatch')
        formal = (opening.start(), closing.end())
        # No macro tokens hidden inline inside the inactive block are admitted.
        if '`' in text[opening.end():closing.start()]:
            raise ValueError('inline_directive_in_formal_block')
        ranges.append(formal)
    for start, stop in sorted(ranges, reverse=True):
        text = text[:start] + blank(text[start:stop]) + text[stop:]
    if re.search(r'[`"\\]|[^\x09\x0a\x0d\x20-\x7e]', text):
        raise ValueError('unsupported_active_lexical_construct')
    return text, nettype, formal


def bind_skid_payload_v8(asset: Mapping, source: str, public_context: Mapping):
    if not isinstance(asset, Mapping) or asset.get('binding_template') != TEMPLATE:
        return result('UNSUPPORTED', 'binding_template_mismatch')
    if (not isinstance(public_context, Mapping) or set(public_context) != set(CONTEXT)
            or type(public_context['WIDTH']) is not int or public_context['WIDTH'] != 8
            or type(public_context['defined_macros']) is not list or public_context['defined_macros']):
        return result('UNSUPPORTED', 'explicit_width_and_empty_macro_context_required')
    if not isinstance(source, str) or not 0 < len(source) <= 65536:
        return result('UNSUPPORTED', 'source_size_or_type')
    try:
        source.encode('utf-8')
        text, nettype, formal = active_text(source)
    except (ValueError, UnicodeError) as exc:
        return result('UNSUPPORTED', str(exc))
    tokens = list(TOKEN.finditer(text))
    modules = sum(m[0] == 'module' for m in tokens)
    if modules == 0:
        return result('NO_MATCH', 'no_module')
    if modules > 1:
        return result('AMBIGUOUS', 'multiple_modules')
    if len(tokens) != len(PATTERN):
        return result('UNSUPPORTED', 'active_module_grammar_length_mismatch')
    roles, rhs = {}, None
    for expected, actual in zip(PATTERN, tokens):
        if expected.startswith('$'):
            role = expected[1:]
            if not IDENT.fullmatch(actual[0]) or roles.get(role, actual[0]) != actual[0]:
                return result('UNSUPPORTED', 'role_or_flow_relation_mismatch')
            roles[role] = actual[0]
            if role == 'RHS':
                rhs = actual.span()
        elif expected != actual[0]:
            return result('UNSUPPORTED', 'active_module_grammar_mismatch')
    signals = [v for k, v in roles.items() if k not in {'MODULE', 'RHS'}]
    reserved = {'WIDTH', 'module', 'endmodule', 'logic', 'parameter', 'int', 'assign',
                'begin', 'end', 'if', 'else', 'always_ff', 'posedge', 'input', 'output'}
    if len(set(signals)) != len(signals) or set(signals) & reserved:
        return result('AMBIGUOUS', 'signal_roles_alias_or_reserved')
    if nettype and not (nettype[0][1] <= tokens[0].start() and tokens[-1].end() <= nettype[1][0]):
        return result('UNSUPPORTED', 'nettype_not_at_module_boundaries')
    if formal and not (tokens[-2].end() <= formal[0] < formal[1] <= tokens[-1].start()):
        return result('UNSUPPORTED', 'formal_not_in_trailing_inactive_slot')
    if roles['RHS'] == roles['SD']:
        return result('NO_MATCH', 'payload_already_uses_captured_slot')
    if roles['RHS'] != roles['ID']:
        return result('UNSUPPORTED', 'unrecognized_mux_payload_source')
    witness = {'shape': 'two_process_valid_bit_captured_payload_mux',
        'module': roles['MODULE'], 'roles': roles, 'rhs_span': list(rhs),
        'old_rhs': roles['ID'], 'replacement_rhs': roles['SD'],
        'source_sha256': base._source_sha(source), 'active_sha256': base._source_sha(text),
        'public_context': {'WIDTH': 8, 'defined_macros': []},
        'inactive_formal_span': list(formal) if formal else None,
        'nettype_spans': [list(x) for x in nettype], 'proof_scope': TEMPLATE['proof_scope']}
    return result('BOUND', 'unique_captured_payload_mux_mismatch', witness=witness,
                  witness_digest=base._digest(witness))


