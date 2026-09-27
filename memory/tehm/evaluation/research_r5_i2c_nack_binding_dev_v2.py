"""Source-only I2C NACK status-latch proposal, DEV generation v2.

This binder never reads source paths, tests, mutation metadata, or Memory.  It
only recognizes a narrow public status cone; functional authority belongs to
the native oracle and the research admission pipeline.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re

from . import research_r5_i2c_nack_binding_dev as v1


CONTRACT = 'rtl_i2c_nack_status_latch_binding_dev_v2'
OPERATOR = 'rtl.I2C_NACK_STATUS_LATCH_DEV_V2'
PROFILE = 'rtl.i2c.nack_status_latch.v2.dev'
TEMPLATE = {'contract': CONTRACT, 'operator': OPERATOR, 'profile': PROFILE,
            'proof_scope': 'unique_syntactic_public_status_latch_only'}
ROLES = ('top', 'byte_ctrl', 'bit_ctrl', 'defines')
INTERFACE = 'wishbone_status_err_bit_7'
IDENT = r'[A-Za-z_][A-Za-z_0-9]*'
INCLUDE = re.compile(r'(?m)^[ \t]*`include[ \t]+"i2c_master_defines\.v"[ \t]*$')
DIRECTIVE = re.compile(r'(?m)^[ \t]*`[^\r\n]*')
MACRO_REF = re.compile(r'`(' + IDENT + r')\b')
ASSIGN = re.compile(r'\b(' + IDENT + r')\s*<=\s*([^;]+);')


def _result(status: str, reason: str, **fields: object) -> dict:
    return {'contract': CONTRACT, 'status': status, 'reason': reason, **fields}


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=True).encode('ascii')
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def _sha(source: str) -> str:
    return 'sha256:' + hashlib.sha256(source.encode('utf-8')).hexdigest()


def _one(pattern: str, source: str, flags: int = 0) -> re.Match | None:
    matches = list(re.finditer(pattern, source, flags))
    return matches[0] if len(matches) == 1 else None


def _blank(text: str) -> str:
    return ''.join('\n' if c == '\n' else ' ' for c in text)


def _active(source: str, *, role: str, macros: set[str]) -> str:
    text = v1._mask_comments(source)
    if '/*' in text or '*/' in text:
        raise ValueError('unterminated_or_nested_comment')
    includes = list(INCLUDE.finditer(text))
    if len(includes) != 1:
        raise ValueError('include_closure_not_unique')
    include = includes[0]
    if re.search(r'\bmodule\b', text[:include.start()]):
        raise ValueError('include_inside_module')
    for directive in DIRECTIVE.finditer(text):
        if (directive.start() != include.start() and
                not re.match(r'[ \t]*`(?:' + '|'.join(map(re.escape, sorted(macros))) +
                             r')\b', directive[0])):
            raise ValueError('unsupported_directive')
    text = text[:include.start()] + _blank(text[include.start():include.end()]) + text[include.end():]
    if re.search(r'["\\]|[^\x09\x0a\x0d\x20-\x7e]', text):
        raise ValueError('unsupported_active_lexical_construct')
    refs = set(MACRO_REF.findall(text))
    if not refs.issubset(macros):
        raise ValueError('undefined_or_unsupported_macro')
    text = MACRO_REF.sub(lambda match: _blank(match[0]), text)
    return text


def _defines(source: str) -> set[str]:
    text = v1._mask_comments(source)
    if '/*' in text or '*/' in text:
        raise ValueError('unterminated_or_nested_comment')
    names: set[str] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"[ \t]*`define[ \t]+(" + IDENT +
                             r")[ \t]+[24]'b[01_]+[ \t]*", line)
        if match is None or match[1] in names:
            raise ValueError('unsupported_define_closure')
        names.add(match[1])
    if not names:
        raise ValueError('empty_define_closure')
    return names


def _module(text: str, name: str | None = None) -> str | None:
    modules = list(re.finditer(r'\bmodule\s+(' + IDENT + r')\b', text))
    ends = list(re.finditer(r'\bendmodule\b', text))
    if not modules and not ends:
        return None
    if len(modules) != 1 or len(ends) != 1 or modules[0].start() >= ends[0].start():
        raise ValueError('module_boundaries_not_unique')
    if name is not None and modules[0][1] != name:
        raise ValueError('module_role_mismatch')
    return modules[0][1]


def _branch(text: str, pattern: str, after: int) -> tuple[int, int] | None:
    match = re.match(pattern, text[after:])
    if match is None:
        return None
    begin = after + match.end() - len('begin')
    end = v1._block_end(text, begin)
    return (begin, end) if end is not None else None


def _bit7(sources: Mapping[str, str]) -> dict:
    macros = _defines(sources['defines'])
    text = {role: _active(sources[role], role=role, macros=macros)
            for role in ('top', 'byte_ctrl', 'bit_ctrl')}
    top, byte, bit = text['top'], text['byte_ctrl'], text['bit_ctrl']
    if _module(top) is None:
        return _result('NO_MATCH', 'no_top_module')
    _module(byte, 'i2c_master_byte_ctrl')
    _module(bit, 'i2c_master_bit_ctrl')
    if not re.search(r'\boutput\s*\[\s*7\s*:\s*0\s*\]\s*wb_dat_o\s*;', top):
        return _result('NO_MATCH', 'public_wishbone_data_output_absent')
    if not re.search(r'\breg\s*\[\s*7\s*:\s*0\s*\]\s*wb_dat_o\s*;', top):
        return _result('UNSUPPORTED', 'public_wishbone_data_declaration_absent')
    read = _one(r"\b3'b100\s*:\s*wb_dat_o\s*<=\s*(" + IDENT + r")\s*;", top)
    if read is None:
        return _result('UNSUPPORTED', 'status_address_read_not_unique')
    status_word = read[1]
    if _one(r'\bwire\s*\[\s*7\s*:\s*0\s*\]\s*' + re.escape(status_word) + r'\s*;', top) is None:
        return _result('UNSUPPORTED', 'status_word_declaration_not_unique')
    status = _one(r'\bassign\s+' + re.escape(status_word) +
                  r'\s*\[\s*7\s*\]\s*=\s*(' + IDENT + r')\s*;', top)
    if status is None:
        return _result('UNSUPPORTED', 'status_bit7_cone_not_unique')
    latch = status[1]
    if _one(r'\breg\s+' + re.escape(latch) + r'\s*;', top) is None:
        return _result('UNSUPPORTED', 'status_latch_declaration_not_unique')
    port = _one(r'\back_out\s*\(\s*(' + IDENT + r')\s*\)', top)
    inst = _one(r'\bi2c_master_byte_ctrl\s+' + IDENT + r'\s*\((.*?)\)\s*;', top, re.S)
    if port is None or inst is None or not (inst.start() < port.start() < port.end() < inst.end()):
        return _result('UNSUPPORTED', 'ack_source_connection_not_unique')
    sample = port[1]
    if len({status_word, latch, sample}) != 3:
        return _result('AMBIGUOUS', 'status_role_alias')
    sample_connections = re.findall(r'\.(' + IDENT + r')\s*\(\s*' +
                                    re.escape(sample) + r'\s*\)', inst[0])
    if sample_connections != ['ack_out']:
        return _result('AMBIGUOUS', 'ack_source_port_alias')
        return _result('UNSUPPORTED', 'ack_source_declaration_not_unique')
    if (_one(r'\boutput\s+ack_out\s*;', byte) is None or
            _one(r'\breg\s+ack_out\s*;', byte) is None or
            _one(r'\bi2c_master_bit_ctrl\s+' + IDENT + r'\s*\((.*?)\)\s*;', byte, re.S) is None or
            _one(r'\bwire\s+core_ack\s*,\s*core_rxd\s*;', byte) is None or
            _one(r'\.dout\s*\(\s*core_rxd\s*\)', byte) is None or
            len(re.findall(r'\back_out\s*<=\s*core_rxd\s*;', byte)) != 2 or
            v1._writer_count(byte, 'ack_out') != 4):
        return _result('UNSUPPORTED', 'byte_controller_ack_provenance_not_supported')
    writers = [m for m in ASSIGN.finditer(top) if m[1] == latch]
    if len(writers) != 3 or v1._writer_count(top, latch) != 3:
        return _result('AMBIGUOUS', 'status_latch_writer_count')
    processes = list(re.finditer(
        r'\balways\s*@\s*\(\s*posedge\s+wb_clk_i\s+or\s+negedge\s+rst_i\s*\)'
        r'\s*if\s*\(\s*!\s*rst_i\s*\)\s*begin', top))
    matched = []
    for process in processes:
        first_begin = process.end() - len('begin')
        first_end = v1._block_end(top, first_begin)
        if first_end is None:
            continue
        second = _branch(top, r'\s*else\s+if\s*\(\s*wb_rst_i\s*\)\s*begin', first_end)
        if second is None:
            continue
        third = _branch(top, r'\s*else\s*begin', second[1])
        if third is None:
            continue
        ranges = ((first_begin, first_end), second, third)
        if all(ranges[i][0] < writers[i].start() < writers[i].end() < ranges[i][1]
               for i in range(3)):
            matched.append(ranges)
    if len(matched) != 1:
        return _result('UNSUPPORTED', 'status_writers_not_in_expected_branches')
    if any(writers[i][2].strip() != "1'b0" for i in (0, 1)):
        return _result('UNSUPPORTED', 'reset_status_rhs_not_zero')
    old = writers[2][2].strip()
    if old == sample:
        return _result('NO_MATCH', 'status_already_latches_low_level_ack')
    if old != "1'b0":
        return _result('UNSUPPORTED', 'normal_status_rhs_not_supported')
    start = writers[2].start(2) + len(writers[2][2]) - len(writers[2][2].lstrip())
    return {'variant': 'wishbone_status_bit7',
            'roles': {'status_word': status_word, 'status_latch': latch,
                      'low_level_ack': sample, 'byte_controller_ack_port': 'ack_out'},
            'source_role': 'top', 'rhs_span': [start, start + len(old)],
            'old_rhs': old, 'replacement_rhs': sample}


def bind(asset: Mapping, buggy_sources: str | Mapping,
         public_context: Mapping) -> dict:
    if not isinstance(asset, Mapping) or dict(asset) != {'binding_template': TEMPLATE}:
        return _result('UNSUPPORTED', 'dev_binding_template_mismatch')
    if not isinstance(public_context, Mapping):
        return _result('UNSUPPORTED', 'public_context_not_supported')
    context = dict(public_context)
    if type(context.get('interface')) is str and context['interface'] in v1.INTERFACES:
        if not isinstance(buggy_sources, str):
            return _result('UNSUPPORTED', 'source_size_or_type')
        prior = v1.bind({'binding_template': v1.TEMPLATE}, buggy_sources, context)
        if prior['status'] != 'BOUND':
            return _result(prior['status'], prior['reason'])
        local = dict(prior['witness'])
        local['source_role'] = 'single'
        local['v1_binding'] = prior
        local['source_hashes'] = {'single': _sha(buggy_sources)}
    elif (set(context) == {'interface', 'parameters', 'defined_macros'} and
          context['interface'] == INTERFACE and
          isinstance(context['parameters'], Mapping) and
          dict(context['parameters']) == {'ARST_LVL': 0} and type(context['parameters']['ARST_LVL']) is int and
          type(context['defined_macros']) is list and not context['defined_macros']):
        if not isinstance(buggy_sources, Mapping) or set(buggy_sources) != set(ROLES):
            return _result('UNSUPPORTED', 'source_closure_size_or_type')
        try:
            if any(not isinstance(buggy_sources[role], str) or
                   not 0 < len(buggy_sources[role].encode('utf-8')) <= 65536
                   for role in ROLES):
                return _result('UNSUPPORTED', 'source_closure_size_or_type')
            local = _bit7(buggy_sources)
        except (ValueError, UnicodeError) as exc:
            reason = str(exc)
            return _result('AMBIGUOUS' if reason == 'module_boundaries_not_unique' else 'UNSUPPORTED', reason)
        if 'status' in local:
            return local
        if buggy_sources['top'][slice(*local['rhs_span'])] != local['old_rhs']:
            return _result('UNSUPPORTED', 'unmasked_rhs_span_mismatch')
        local['source_hashes'] = {role: _sha(buggy_sources[role]) for role in ROLES}
    else:
        return _result('UNSUPPORTED', 'public_context_not_supported')
    local.pop('source_sha256', None)
    local.pop('public_context_digest', None)
    local.pop('action_digest', None)
    local['public_context_digest'] = _digest(context)
    local['action_digest'] = _digest({'contract': CONTRACT, 'witness': local})
    return _result('BOUND', 'unique_syntactic_nack_status_latch', witness=local,
                   memory_authority_granted=False)


def apply(asset: Mapping, buggy_sources: str | Mapping,
          public_context: Mapping, binding: Mapping) -> tuple[str | dict, dict]:
    current = bind(asset, buggy_sources, public_context)
    if current['status'] != 'BOUND' or not isinstance(binding, Mapping) or dict(binding) != current:
        raise ValueError('missing_stale_or_tampered_source_binding')
    witness = current['witness']
    if witness['source_role'] == 'single':
        candidate, _ = v1.apply({'binding_template': v1.TEMPLATE}, buggy_sources,
                                public_context, witness['v1_binding'])
        hashes = {'single': _sha(candidate)}
    else:
        start, stop = witness['rhs_span']
        top = buggy_sources['top']
        candidate = dict(buggy_sources)
        candidate['top'] = top[:start] + witness['replacement_rhs'] + top[stop:]
        hashes = {role: _sha(candidate[role]) for role in ROLES}
    if bind(asset, candidate, public_context)['status'] != 'NO_MATCH':
        raise ValueError('candidate_postcondition_not_no_match')
    return candidate, {'contract': CONTRACT, 'operator': OPERATOR,
                       'action_digest': witness['action_digest'],
                       'before_source_hashes': witness['source_hashes'],
                       'after_source_hashes': hashes,
                       'rewritten_spans': 1, 'source_binding_rederived': True,
                       'functional_verdict': 'NOT_EVALUATED',
                       'memory_authority_granted': False}
