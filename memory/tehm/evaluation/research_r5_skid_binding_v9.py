"""DEV-only one-process buffered-payload drain binding.

The complete active token grammar proves a narrow structural relation and
rejects every unmodelled writer/control construct. Role names may change;
source paths, tests, mutation manifests and gold source are never inputs.
This is a syntactic proposal, not a functional oracle or Memory authority.
"""
from __future__ import annotations

from collections.abc import Mapping
import re

from . import research_r5_skid_binding as base
from . import research_r5_skid_binding_v8 as lexical


CONTRACT = 'rtl_skid_one_process_drain_binding_dev_v9'
OPERATOR = 'rtl.SKID_TEMP_PAYLOAD_RESTORE_DEV_V9'
PROFILE = 'rtl.skid.one_process_drain.v9.dev'
TEMPLATE = {'contract': CONTRACT, 'operator': OPERATOR, 'profile': PROFILE,
            'proof_scope': 'unique_one_process_skid_drain_rhs_syntactic_only'}
CONTEXT = {'WIDTH': 8, 'defined_macros': []}

# Full active-module grammar intentionally fails closed on additional writers,
# preprocessor directives, sidebands, alternate reset/timing, or extra logic.
# Placeholders are structural roles, not APEX names or private edit locations.
GRAMMAR = """
module $MODULE #(parameter int WIDTH = 8) (
 input logic $CLK, input logic $RST,
 input logic $IV, output logic $IR,
 input logic [WIDTH-1:0] $ID,
 output logic $OV, input logic $OR,
 output logic [WIDTH-1:0] $OD
);
logic $SV;
logic [WIDTH-1:0] $SD;
assign $IR = ~$SV;
always_ff @(posedge $CLK) begin
 if (!$RST) begin
  $OV <= 1'b0; $SV <= 1'b0; $OD <= '0; $SD <= '0;
 end else begin
  if ($OR || !$OV) begin
   if ($SV) begin
    $OD <= $RHS; $OV <= 1'b1; $SV <= 1'b0;
   end else if ($IV && $IR) begin
    $OD <= $ID; $OV <= 1'b1;
   end else begin
    $OV <= 1'b0;
   end
  end else begin
   if ($IV && $IR) begin
    $SD <= $ID; $SV <= 1'b1;
   end
  end
 end
end
endmodule
"""
PATTERN = tuple(m.group() for m in lexical.TOKEN.finditer(GRAMMAR))
IDENT = re.compile(r'[A-Za-z_][A-Za-z_0-9]*\Z')


def _result(status: str, reason: str, **fields: object) -> dict:
    return {'contract': CONTRACT, 'status': status, 'reason': reason, **fields}


def bind_one_process_drain_v9(asset: Mapping, source: str,
                              public_context: Mapping) -> dict:
    if not isinstance(asset, Mapping) or asset.get('binding_template') != TEMPLATE:
        return _result('UNSUPPORTED', 'binding_template_mismatch')
    if (not isinstance(public_context, Mapping) or set(public_context) != set(CONTEXT)
            or type(public_context['WIDTH']) is not int or public_context['WIDTH'] != 8
            or type(public_context['defined_macros']) is not list
            or public_context['defined_macros']):
        return _result('UNSUPPORTED', 'explicit_width_and_empty_macro_context_required')
    if not isinstance(source, str) or not 0 < len(source) <= 65536:
        return _result('UNSUPPORTED', 'source_size_or_type')
    try:
        source.encode('utf-8')
        active, nettype, formal = lexical.active_text(source)
    except (ValueError, UnicodeError) as exc:
        return _result('UNSUPPORTED', str(exc))
    if nettype or formal:
        return _result('UNSUPPORTED', 'directives_not_in_v9_one_process_profile')
    tokens = list(lexical.TOKEN.finditer(active))
    module_count = sum(token[0] == 'module' for token in tokens)
    if module_count == 0:
        return _result('NO_MATCH', 'no_module')
    if module_count > 1:
        return _result('AMBIGUOUS', 'multiple_modules')
    if len(tokens) != len(PATTERN):
        return _result('UNSUPPORTED', 'active_module_grammar_length_mismatch')
    roles: dict[str, str] = {}
    rhs_span: tuple[int, int] | None = None
    for expected, actual in zip(PATTERN, tokens):
        if expected.startswith('$'):
            role = expected[1:]
            if not IDENT.fullmatch(actual[0]) or roles.get(role, actual[0]) != actual[0]:
                return _result('UNSUPPORTED', 'role_or_flow_relation_mismatch')
            roles[role] = actual[0]
            if role == 'RHS':
                rhs_span = actual.span()
        elif expected != actual[0]:
            return _result('UNSUPPORTED', 'active_module_grammar_mismatch')
    signals = [value for role, value in roles.items() if role not in {'MODULE', 'RHS'}]
    reserved = {'WIDTH', 'module', 'endmodule', 'logic', 'parameter', 'int',
                'assign', 'begin', 'end', 'if', 'else', 'always_ff', 'posedge'}
    if len(signals) != len(set(signals)) or set(signals) & reserved:
        return _result('AMBIGUOUS', 'signal_roles_alias_or_reserved')
    if rhs_span is None or source[rhs_span[0]:rhs_span[1]] != roles['RHS']:
        return _result('UNSUPPORTED', 'rhs_offset_does_not_replay')
    if roles['RHS'] == roles['SD']:
        return _result('NO_MATCH', 'drain_already_uses_captured_payload')
    if roles['RHS'] != roles['ID']:
        return _result('UNSUPPORTED', 'unrecognized_drain_payload_source')
    witness = {'shape': 'one_process_valid_bit_captured_payload_drain',
               'module': roles['MODULE'], 'roles': roles,
               'rhs_span': list(rhs_span), 'old_rhs': roles['ID'],
               'replacement_rhs': roles['SD'],
               'source_sha256': base._source_sha(source),
               'active_sha256': base._source_sha(active),
               'public_context': {'WIDTH': 8, 'defined_macros': []},
               'proof_scope': TEMPLATE['proof_scope']}
    return _result('BOUND', 'unique_captured_payload_drain_mismatch',
                   witness=witness, witness_digest=base._digest(witness))


def apply_bound_one_process_drain_v9(asset: Mapping, source: str,
                                     public_context: Mapping,
                                     binding: Mapping) -> tuple[str, dict]:
    fresh = bind_one_process_drain_v9(asset, source, public_context)
    if fresh['status'] != 'BOUND' or not isinstance(binding, Mapping) or dict(binding) != fresh:
        raise ValueError('v9 binding stale, tampered, or unsupported')
    witness = fresh['witness']
    start, stop = witness['rhs_span']
    if source[start:stop] != witness['old_rhs']:
        raise ValueError('v9 source span identity mismatch')
    edited = source[:start] + witness['replacement_rhs'] + source[stop:]
    healthy = bind_one_process_drain_v9(asset, edited, public_context)
    if (healthy['status'], healthy['reason']) != (
            'NO_MATCH', 'drain_already_uses_captured_payload'):
        raise ValueError('v9 candidate failed structural postcondition')
    return edited, {'binding_contract': CONTRACT, 'operator': OPERATOR,
                    'profile': PROFILE, 'rewritten': 1,
                    'before_source_sha256': base._source_sha(source),
                    'after_source_sha256': base._source_sha(edited),
                    'witness_digest': fresh['witness_digest'],
                    'source_binding_rederived': True,
                    'non_target_source_bytes_preserved': True,
                    'functional_verdict': 'NOT_EVALUATED',
                    'memory_authority_granted': False,
                    'proof_scope': TEMPLATE['proof_scope']}
