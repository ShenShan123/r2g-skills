"""Bounded source-only I2C NACK status-latch binding, DEV authority only.

This recognizes two syntactic cones observed in registered DEV designs. It
does not prove RTL functionality, select a Memory asset, or run an oracle.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re

from .research_r5_skid_binding import _mask_comments


CONTRACT = 'rtl_i2c_nack_status_latch_binding_dev_v1'
OPERATOR = 'rtl.I2C_NACK_STATUS_LATCH_DEV_V1'
PROFILE = 'rtl.i2c.nack_status_latch.v1.dev'
TEMPLATE = {'contract': CONTRACT, 'operator': OPERATOR, 'profile': PROFILE,
            'proof_scope': 'unique_syntactic_public_status_latch_only'}
INTERFACES = {'dedicated_missed_ack_output', 'wishbone_status_err_bit_30'}
IDENT = r'[A-Za-z_][A-Za-z_0-9]*'
ASSIGN = re.compile(rf'\b({IDENT})\s*<=\s*([^;]+);')
DIRECTIVE = re.compile(r'(?m)^[ \t]*`[^\r\n]*')
WORDS = re.compile(r'\bbegin\b|\bend\b')


def _result(status: str, reason: str, **fields: object) -> dict:
    return {'contract': CONTRACT, 'status': status, 'reason': reason, **fields}


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=True).encode('ascii')
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def _one(pattern: str, text: str, flags: int = 0) -> re.Match | None:
    matches = list(re.finditer(pattern, text, flags))
    if len(matches) != 1:
        return None
    return matches[0]


def _block_end(text: str, begin_at: int) -> int | None:
    """Offset just after the matching end token, or None on imbalance."""
    depth = 0
    for word in WORDS.finditer(text, begin_at):
        if word[0] == 'begin':
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return word.end()
    return None


def _active(source: str) -> str:
    text = _mask_comments(source)
    if '/*' in text or '*/' in text:
        raise ValueError('unterminated_or_nested_comment')
    directives = list(DIRECTIVE.finditer(text))
    if len(directives) > 1:
        raise ValueError('unsupported_directives')
    if directives:
        match = directives[0]
        allowed = (r'[ \t]*`timescale[ \t]+1ns[ \t]*/[ \t]*1ps[ \t]*',
                   r'[ \t]*`default_nettype[ \t]+none[ \t]*')
        if not any(re.fullmatch(pattern, match[0]) for pattern in allowed):
            raise ValueError('unsupported_directive')
        if re.search(r'\bmodule\b', text[:match.start()]):
            raise ValueError('directive_inside_module')
        text = text[:match.start()] + ''.join('\n' if c == '\n' else ' '
                                                  for c in match[0]) + text[match.end():]
    if re.search(r'[`"\\]|[^\x09\x0a\x0d\x20-\x7e]', text):
        raise ValueError('unsupported_active_lexical_construct')
    return text


def _writers(text: str, name: str) -> list[re.Match]:
    return [match for match in ASSIGN.finditer(text) if match[1] == name]


def _writer_count(text: str, name: str) -> int:
    # Include blocking writers and continuous assigns, not only the supported
    # nonblocking assignments returned by _writers.
    return len(re.findall(rf'\b{re.escape(name)}\s*(?:<=|(?<![=])=(?!=))', text))


def _dedicated(text: str) -> dict:
    if not re.search(r'\boutput\s+wire\s+missed_ack\b', text):
        return _result('NO_MATCH', 'public_missed_ack_output_absent')
    output = _one(rf'\bassign\s+missed_ack\s*=\s*(?P<reg>{IDENT})\s*;', text)
    if output is None:
        return _result('UNSUPPORTED', 'public_output_cone_not_unique')
    register = output['reg']
    declaration = _one(rf"\breg\s+{re.escape(register)}\s*=\s*1'b0\s*,\s*"
                       rf'(?P<next>{IDENT})\s*;', text)
    if declaration is None:
        return _result('UNSUPPORTED', 'status_next_state_pair_not_unique')
    next_state = declaration['next']
    if next_state == register:
        return _result('AMBIGUOUS', 'aliased_status_and_next_state')
    next_writers = list(re.finditer(rf'\b{re.escape(next_state)}\s*=\s*'
                                    rf"(?P<rhs>{IDENT}|1'b[01])\s*;", text))
    all_next_writers = _writer_count(text, next_state)
    if len(next_writers) != 3 or all_next_writers != 3:
        return _result('UNSUPPORTED', 'next_state_writer_set_not_supported')
    values = [match['rhs'] for match in next_writers]
    if values.count("1'b0") != 1 or len(set(v for v in values if v != "1'b0")) != 1:
        return _result('UNSUPPORTED', 'ack_sample_relation_not_unique')
    sample = next(v for v in values if v != "1'b0")
    if not re.search(rf'\breg\s+{re.escape(sample)}\s*=\s*1\'b0\s*,\s*{IDENT}\s*;', text):
        return _result('UNSUPPORTED', 'ack_sample_not_registered')
    writers = _writers(text, register)
    if len(writers) != 2 or _writer_count(text, register) != 3:
        return _result('AMBIGUOUS', 'status_register_writer_count')
    reset = _one(r'\bif\s*\(\s*rst\s*\)\s*begin\b', text)
    if reset is None:
        return _result('UNSUPPORTED', 'reset_branch_not_unique')
    reset_begin = text.rfind('begin', reset.start(), reset.end())
    reset_end = _block_end(text, reset_begin)
    if reset_end is None:
        return _result('UNSUPPORTED', 'reset_branch_unbalanced')
    inside = [writer for writer in writers if reset_begin < writer.start() < reset_end]
    outside = [writer for writer in writers if writer not in inside]
    if (len(inside) != 1 or len(outside) != 1
            or inside[0][2].strip() != "1'b0"
            or outside[0].start() >= reset_begin):
        return _result('UNSUPPORTED', 'reset_and_nonreset_writers_not_separated')
    # Both writers must be in the same sequential process. The only supported
    # reset control is the explicit branch above.
    process = _one(r'\balways\s*@\s*\(\s*posedge\s+clk\s*\)\s*begin\b', text)
    if process is None:
        return _result('UNSUPPORTED', 'sequential_process_not_unique')
    process_begin = text.rfind('begin', process.start(), process.end())
    process_end = _block_end(text, process_begin)
    if process_end is None or not all(process_begin < w.start() < process_end for w in writers):
        return _result('UNSUPPORTED', 'status_writers_not_in_one_process')
    old = outside[0][2].strip()
    if old == next_state:
        return _result('NO_MATCH', 'status_already_latches_ack_next')
    if old != "1'b0":
        return _result('UNSUPPORTED', 'nonreset_status_rhs_not_supported')
    start = outside[0].start(2) + len(outside[0][2]) - len(outside[0][2].lstrip())
    return {'variant': 'dedicated_next_state', 'roles':
            {'status': register, 'next_state': next_state, 'ack_sample': sample},
            'rhs_span': [start, start + len(old)], 'old_rhs': old,
            'replacement_rhs': next_state}


def _wishbone(text: str) -> dict:
    if not re.search(r'\boutput\s+reg\s*\[\s*31\s*:\s*0\s*\]\s*o_wb_data\b', text):
        return _result('NO_MATCH', 'public_wishbone_data_output_absent')
    status = _one(rf"\b(?P<word>{IDENT})\s*\[\s*31\s*:\s*24\s*\]\s*=\s*"
                  rf"\{{\s*(?P<busy>{IDENT})\s*,\s*(?P<err>{IDENT})\s*,\s*6'h0\s*\}}\s*;", text)
    if status is None:
        return _result('UNSUPPORTED', 'public_error_bit_cone_not_unique')
    word, busy, error = status['word'], status['busy'], status['err']
    if len({word, busy, error}) != 3:
        return _result('AMBIGUOUS', 'status_cone_alias')
    if not re.search(rf'\breg\s*\[\s*31\s*:\s*0\s*\]\s*{re.escape(word)}\s*;', text):
        return _result('UNSUPPORTED', 'status_word_declaration_absent')
    if not re.search(rf'\b2\'b00\s*:\s*o_wb_data\s*<=\s*{re.escape(word)}\s*;', text):
        return _result('UNSUPPORTED', 'status_word_not_routed_to_public_data')
    if not re.search(rf'\breg\s+{re.escape(error)}\s*;', text):
        return _result('UNSUPPORTED', 'error_latch_declaration_absent')
    if not re.search(rf'\breg\s+{re.escape(busy)}\s*;', text):
        return _result('UNSUPPORTED', 'busy_latch_declaration_absent')
    writers = _writers(text, error)
    if len(writers) != 2 or _writer_count(text, error) != 2:
        return _result('AMBIGUOUS', 'error_latch_writer_count')
    clear, event = sorted(writers, key=lambda w: w.start())
    if clear[2].strip() != "1'b0":
        return _result('UNSUPPORTED', 'error_clear_rhs_not_zero')
    bridge = text[clear.end():event.start()]
    condition = _one(rf'\belse\s+if\s*\(\s*\(\s*{re.escape(busy)}\s*\)\s*'
                     rf'&&\s*\(\s*(?P<low>{IDENT})\s*\)\s*\)\s*', bridge)
    if condition is None or bridge[condition.end():].strip():
        return _result('UNSUPPORTED', 'busy_and_low_level_error_guard_not_unique')
    low_error = condition['low']
    if low_error in {busy, error, word}:
        return _result('AMBIGUOUS', 'error_source_alias')
    if not re.search(rf'\bwire\s+[^;]*\b{re.escape(low_error)}\s*;', text):
        return _result('UNSUPPORTED', 'low_level_error_signal_not_declared')
    if len(re.findall(rf'\bif\s*\(\s*{re.escape(low_error)}\s*\)', text)) < 1:
        return _result('UNSUPPORTED', 'low_level_error_not_used_by_controller')
    before_clear = text[max(0, clear.start()-240):clear.start()]
    if (not re.search(rf'\bif\s*\(', before_clear)
            or not re.search(rf'!\s*{re.escape(busy)}\b', before_clear)
            or not re.search(r'\bi_wb_stb\b', before_clear)):
        return _result('UNSUPPORTED', 'busy_clear_guard_not_supported')
    # This source has several sequential processes, so identify the one
    # enclosing both writers rather than requiring a unique clocked process.
    processes = list(re.finditer(r'\balways\s*@\s*\(\s*posedge\s+i_clk\s*\)\s*begin\b', text))
    enclosing = []
    for item in processes:
        begin = text.rfind('begin', item.start(), item.end())
        end = _block_end(text, begin)
        if end is not None and begin < clear.start() < event.start() < end:
            enclosing.append((begin, end))
    if len(enclosing) != 1:
        return _result('UNSUPPORTED', 'error_writers_not_in_one_process')
    old = event[2].strip()
    if old == "1'b1":
        return _result('NO_MATCH', 'error_event_already_sets_status')
    if old != "1'b0":
        return _result('UNSUPPORTED', 'error_event_rhs_not_supported')
    start = event.start(2) + len(event[2]) - len(event[2].lstrip())
    return {'variant': 'wishbone_error_event', 'roles':
            {'status_word': word, 'busy': busy, 'error_latch': error,
             'low_level_error': low_error},
            'rhs_span': [start, start + len(old)], 'old_rhs': old,
            'replacement_rhs': "1'b1"}


def bind(asset: Mapping, buggy_source: str, public_context: Mapping) -> dict:
    if not isinstance(asset, Mapping) or dict(asset) != {'binding_template': TEMPLATE}:
        return _result('UNSUPPORTED', 'dev_binding_template_mismatch')
    if (not isinstance(public_context, Mapping) or set(public_context) !=
            {'interface', 'defined_macros'} or
            type(public_context['interface']) is not str or
            public_context['interface'] not in INTERFACES or
            type(public_context['defined_macros']) is not list or
            public_context['defined_macros']):
        return _result('UNSUPPORTED', 'public_context_not_supported')
    if not isinstance(buggy_source, str) or not 0 < len(buggy_source) <= 65536:
        return _result('UNSUPPORTED', 'source_size_or_type')
    try:
        buggy_source.encode('utf-8')
        text = _active(buggy_source)
    except (ValueError, UnicodeError) as exc:
        return _result('UNSUPPORTED', str(exc))
    modules = list(re.finditer(r'\bmodule\s+' + IDENT + r'\b', text))
    ends = list(re.finditer(r'\bendmodule\b', text))
    if not modules and not ends:
        return _result('NO_MATCH', 'no_module')
    if len(modules) > 1 or len(ends) > 1:
        return _result('AMBIGUOUS', 'multiple_modules')
    if len(modules) != 1 or len(ends) != 1 or modules[0].start() >= ends[0].start():
        return _result('UNSUPPORTED', 'module_boundaries_invalid')
    body = text[modules[0].start():ends[0].end()]
    if public_context['interface'] == 'dedicated_missed_ack_output':
        local = _dedicated(body)
    else:
        local = _wishbone(body)
    if 'status' in local:
        return local
    start, stop = local['rhs_span']
    start += modules[0].start()
    stop += modules[0].start()
    if buggy_source[start:stop] != local['old_rhs']:
        return _result('UNSUPPORTED', 'unmasked_rhs_span_mismatch')
    witness = {'variant': local['variant'], 'roles': local['roles'],
               'rhs_span': [start, stop], 'old_rhs': local['old_rhs'],
               'replacement_rhs': local['replacement_rhs'],
               'source_sha256': 'sha256:' + hashlib.sha256(buggy_source.encode()).hexdigest(),
               'public_context_digest': _digest(dict(public_context))}
    witness['action_digest'] = _digest({'contract': CONTRACT, 'witness': witness})
    return _result('BOUND', 'unique_syntactic_nack_status_latch', witness=witness,
                   memory_authority_granted=False)


def apply(asset: Mapping, buggy_source: str, public_context: Mapping,
          binding: Mapping) -> tuple[str, dict]:
    current = bind(asset, buggy_source, public_context)
    if current['status'] != 'BOUND' or not isinstance(binding, Mapping) or dict(binding) != current:
        raise ValueError('missing_stale_or_tampered_source_binding')
    witness = current['witness']
    start, stop = witness['rhs_span']
    candidate = (buggy_source[:start] + witness['replacement_rhs'] +
                 buggy_source[stop:])
    after = bind(asset, candidate, public_context)
    if after['status'] != 'NO_MATCH':
        raise ValueError('candidate_postcondition_not_no_match')
    return candidate, {'contract': CONTRACT, 'operator': OPERATOR,
                       'action_digest': witness['action_digest'],
                       'before_source_sha256': witness['source_sha256'],
                       'after_source_sha256': 'sha256:' + hashlib.sha256(candidate.encode()).hexdigest(),
                       'rewritten_spans': 1, 'source_binding_rederived': True,
                       'functional_verdict': 'NOT_EVALUATED',
                       'memory_authority_granted': False}
