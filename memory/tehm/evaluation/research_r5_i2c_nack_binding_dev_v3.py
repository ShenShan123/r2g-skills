"""Source-only I2C NACK status-latch proposal, DEV generation v3.

Contract: memory/evaluation/research_r5_i2c_nack_binding_v3_dev_contract_20260928.md.
The three v2 interfaces delegate unchanged to the frozen v2 binder. One new public
interface (a directly registered status output named in the public context) adds a
bounded active-text layer and one structural cone. The binder never reads paths,
tests, mutation metadata, clean counterparts or Memory.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re

from . import research_r5_i2c_nack_binding_dev as v1
from . import research_r5_i2c_nack_binding_dev_v2 as v2

FROZEN_SHA256 = {
    'research_r5_i2c_nack_binding_dev.py': 'bb8f459b38c4797de17b62655a9e13b26e16fe32417d36ab5140a6d6624963ff',
    'research_r5_i2c_nack_binding_dev_v2.py': 'd0b594a767bace01886b247636d875721a95643130f7678ea6a93c798d4b88e7',
}
for _module in (v1, v2):
    _path = Path(_module.__file__)
    if hashlib.sha256(_path.read_bytes()).hexdigest() != FROZEN_SHA256[_path.name]:
        raise ImportError('frozen I2C binder drift: ' + _path.name)

CONTRACT = 'rtl_i2c_nack_status_latch_binding_dev_v3'
OPERATOR = 'rtl.I2C_NACK_STATUS_LATCH_DEV_V3'
PROFILE = 'rtl.i2c.nack_status_latch.v3.dev'
TEMPLATE = {'contract': CONTRACT, 'operator': OPERATOR, 'profile': PROFILE,
            'proof_scope': 'unique_syntactic_public_status_latch_only'}
NEW_INTERFACE = 'dedicated_nack_status_register_output'
DELEGATED_INTERFACES = frozenset(v1.INTERFACES) | {v2.INTERFACE}
IDENT = r'[A-Za-z_][A-Za-z_0-9]*'
SYSTEM_TASKS = r'\$(?:display|write|strobe|monitor)'
TIMESCALE = re.compile(r'`timescale\s+\d+\s*(?:s|ms|us|ns|ps|fs)\s*/\s*\d+\s*(?:s|ms|us|ns|ps|fs)')
ZERO = re.compile(r"(?:\d*'[bB]0+|\d*'[dD]0+|\d*'[hH]0+|0+|'0)")
WORDS = re.compile(r'\bbegin\b|\bend\b')


def _result(status: str, reason: str, **fields: object) -> dict:
    return {'contract': CONTRACT, 'status': status, 'reason': reason, **fields}


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def _sha(source: str) -> str:
    return 'sha256:' + hashlib.sha256(source.encode('utf-8')).hexdigest()


def _blank(text: str) -> str:
    return ''.join('\n' if c == '\n' else ' ' for c in text)


# ---------------------------------------------------------------- active text

def _lex(source: str) -> tuple[str, list[tuple[int, int]]]:
    """Mask comments; return masked text and string-literal spans (offsets preserved)."""
    out, strings, i, n = [], [], 0, len(source)
    while i < n:
        if source.startswith('//', i):
            j = source.find('\n', i)
            j = n if j < 0 else j
            out.append(_blank(source[i:j])); i = j
        elif source.startswith('/*', i):
            j = source.find('*/', i + 2)
            if j < 0:
                raise ValueError('unterminated_comment')
            out.append(_blank(source[i:j + 2])); i = j + 2
        elif source[i] == '"':
            j = i + 1
            while j < n and source[j] != '"':
                if source[j] == '\n':
                    raise ValueError('unterminated_string')
                j += 2 if source[j] == '\\' else 1
            if j >= n:
                raise ValueError('unterminated_string')
            strings.append((i, j + 1))
            out.append(source[i:j + 1]); i = j + 1
        else:
            out.append(source[i]); i += 1
    return ''.join(out), strings


def _mask_strings(text: str, strings: list[tuple[int, int]]) -> str:
    masked = text
    for start, stop in strings:
        masked = masked[:start] + _blank(masked[start:stop]) + masked[stop:]
    for start, _ in strings:
        depth, k = 0, start - 1
        while k >= 0:
            if masked[k] == ')':
                depth += 1
            elif masked[k] == '(':
                if depth == 0:
                    break
                depth -= 1
            k -= 1
        if k < 0 or not re.search(SYSTEM_TASKS + r'\s*$', masked[:k]):
            raise ValueError('unsupported_string_literal')
    return masked


def _active(source: str, defined: set[str]) -> str:
    text, strings = _lex(source)
    text = _mask_strings(text, strings)
    lines = text.splitlines(keepends=True)
    offset, out, block, seen_timescale = 0, [], None, False
    first_module = re.search(r'\bmodule\b', text)
    for line in lines:
        stripped = line.strip()
        directive = stripped.startswith('`')
        if directive:
            if TIMESCALE.fullmatch(stripped):
                if seen_timescale or (first_module and offset > first_module.start()):
                    raise ValueError('unsupported_timescale_position')
                seen_timescale = True
            elif re.fullmatch(r'`(ifdef|ifndef)\s+' + IDENT, stripped):
                if block is not None:
                    raise ValueError('nested_conditional')
                kind, name = stripped[1:].split()
                block = {'active': (name in defined) == (kind == 'ifdef'), 'else': False}
            elif stripped == '`else':
                if block is None or block['else']:
                    raise ValueError('unbalanced_conditional')
                block = {'active': not block['active'], 'else': True}
            elif stripped == '`endif':
                if block is None:
                    raise ValueError('unbalanced_conditional')
                block = None
            else:
                raise ValueError('unsupported_directive')
            out.append(_blank(line))
        elif block is not None and not block['active']:
            out.append(_blank(line))
        else:
            out.append(line)
        offset += len(line)
    if block is not None:
        raise ValueError('unbalanced_conditional')
    active = ''.join(out)
    if re.search(r'[`"\\]|[^\x09\x0a\x0d\x20-\x7e]', active):
        raise ValueError('unsupported_active_lexical_construct')
    return active


# ---------------------------------------------------------------- structure

def _blocks(text: str) -> dict[int, int]:
    """begin offset -> offset just after its matching end (balanced text only)."""
    stack, pairs = [], {}
    for word in WORDS.finditer(text):
        if word[0] == 'begin':
            stack.append(word.start())
        else:
            if not stack:
                raise ValueError('unbalanced_begin_end')
            pairs[stack.pop()] = word.end()
    if stack:
        raise ValueError('unbalanced_begin_end')
    return pairs


def _innermost(pairs: dict[int, int], at: int) -> int | None:
    inside = [b for b, e in pairs.items() if b < at < e]
    return max(inside) if inside else None


def _reg_names(text: str) -> set[str]:
    names = set()
    for match in re.finditer(r'\breg\b', text):
        tail = re.split(r'[;)]', text[match.end():], maxsplit=1)[0]
        tail = re.sub(r'^\s*\[[^\]]*\]', '', tail)
        for piece in tail.split(','):
            if re.search(r'\b(?:input|output|inout|wire|reg)\b', piece):
                break
            ident = re.match(r'\s*(' + IDENT + r')', piece)
            if ident:
                names.add(ident[1])
    return names


def _registered(body: str, status: str) -> dict:
    s = re.escape(status)
    if len(re.findall(rf'\boutput\s+reg\s+{s}\b', body)) != 1:
        if re.search(rf'\boutput\b[^;,()]*\b{s}\b', body):
            return _result('UNSUPPORTED', 'status_output_not_scalar_registered')
        return _result('NO_MATCH', 'public_status_output_absent')
    if (re.search(rf'\b(?:input|inout|wire)\b[^;,()]*\b{s}\b', body) or
            len(re.findall(rf'\breg\b(?:\s*\[[^\]]*\])?\s+{s}\b', body)) != 1):
        return _result('UNSUPPORTED', 'status_declaration_not_unique')
    scalar = list(re.finditer(rf'(?<![\w.$]){s}\s*<=\s*([^;]+);', body))
    blocking = re.findall(rf'(?<![\w.$]){s}\s*=(?!=)', body)
    continuous = re.findall(rf'\bassign\s+{s}\b', body)
    concats = [m for m in re.finditer(r'\{([^{}]*)\}\s*(<=|=(?!=))\s*([^;]+);', body)
               if status in [p.strip() for p in m[1].split(',')]]
    if blocking or continuous:
        return _result('UNSUPPORTED', 'status_blocking_or_continuous_writer')
    pairs = _blocks(body)
    processes = []
    for proc in re.finditer(rf'\balways\s*@\s*\(\s*posedge\s+(?P<clk>{IDENT})\s+or\s+negedge\s+'
                            rf'(?P<rst>{IDENT})\s*\)\s*begin\b', body):
        begin = body.rfind('begin', proc.start(), proc.end())
        processes.append((begin, pairs[begin], proc['clk'], proc['rst']))
    writers = [m.start() for m in scalar] + [m.start() for m in concats]
    owners = {p for p in processes for w in writers if p[0] < w < p[1]}
    if len(owners) != 1 or not all(any(p[0] < w < p[1] for p in owners) for w in writers):
        return _result('UNSUPPORTED', 'status_writers_not_in_one_process')
    begin, end, clock, reset = owners.pop()
    guard = re.match(rf'\s*if\s*\(\s*!\s*{re.escape(reset)}\s*\)\s*begin\b', body[begin + 5:end])
    if guard is None:
        return _result('UNSUPPORTED', 'reset_branch_not_first')
    reset_begin = body.rfind('begin', begin + 5, begin + 5 + guard.end())
    reset_end = pairs[reset_begin]
    in_reset = [m for m in scalar if reset_begin < m.start() < reset_end]
    concat_reset = [m for m in concats if reset_begin < m.start() < reset_end]
    if any(not (reset_begin < m.start() < reset_end) for m in concats):
        return _result('UNSUPPORTED', 'status_concatenation_writer_outside_reset')
    if len(in_reset) + len(concat_reset) != 1:
        return _result('UNSUPPORTED', 'reset_status_writer_not_unique')
    reset_rhs = (in_reset or concat_reset)[0]
    rhs = reset_rhs[1] if in_reset else reset_rhs[3]
    if in_reset and not ZERO.fullmatch(rhs.strip()) or concat_reset and (
            reset_rhs[2] != '<=' or not ZERO.fullmatch(rhs.strip())):
        return _result('UNSUPPORTED', 'reset_status_value_not_zero')
    live = [m for m in scalar if not (reset_begin < m.start() < reset_end)]
    if len(live) != 2:
        return _result('AMBIGUOUS' if len(live) > 2 else 'UNSUPPORTED', 'nonreset_status_writer_count')
    regs = _reg_names(body)
    events, samples = [], {}
    for writer in live:
        inner = _innermost(pairs, writer.start())
        if inner is None or not re.search(r'\belse\s*$', body[:inner]):
            continue
        else_at = re.search(r'\belse\s*$', body[:inner]).start()
        prior_end = re.search(r'\bend\s*$', body[:else_at])
        if prior_end is None:
            continue
        then_begin = next((b for b, e in pairs.items() if e == prior_end.start() + 3), None)
        if then_begin is None:
            continue
        test = re.search(rf'\bif\s*\(\s*!\s*(?P<x>{IDENT})\s*\)\s*$', body[:then_begin])
        if test is None or test['x'] not in regs:
            continue
        if any(then_begin < m.start() < pairs[then_begin] for m in live):
            continue
        events.append(writer)
        samples[writer.start()] = test['x']
    if len(events) != 1:
        return _result('AMBIGUOUS' if events else 'UNSUPPORTED',
                       'event_writer_not_unique' if events else 'event_writer_absent')
    event = events[0]
    clear = next(m for m in live if m is not event)
    if clear[1].strip() != "1'b0":
        return _result('UNSUPPORTED', 'clear_writer_rhs_not_zero')
    old = event[1].strip()
    if old == "1'b1":
        return _result('NO_MATCH', 'status_event_already_sets_status')
    if old != "1'b0":
        return _result('UNSUPPORTED', 'event_writer_rhs_not_supported')
    start = event.start(1) + len(event[1]) - len(event[1].lstrip())
    return {'variant': 'dedicated_registered_status',
            'roles': {'status': status, 'clock': clock, 'reset': reset,
                      'ack_sample': samples[event.start()]},
            'rhs_span': [start, start + len(old)], 'old_rhs': old, 'replacement_rhs': "1'b1"}


# ---------------------------------------------------------------- public API

def _new_context_ok(context: dict) -> bool:
    macros = context.get('defined_macros')
    return (set(context) == {'interface', 'status_output', 'defined_macros'} and
            context['interface'] == NEW_INTERFACE and
            type(context['status_output']) is str and re.fullmatch(IDENT, context['status_output']) is not None and
            type(macros) is list and all(type(m) is str and re.fullmatch(IDENT, m) for m in macros) and
            len(set(macros)) == len(macros))


def bind(asset: Mapping, buggy_source, public_context: Mapping) -> dict:
    if not isinstance(asset, Mapping) or dict(asset) != {'binding_template': TEMPLATE}:
        return _result('UNSUPPORTED', 'dev_binding_template_mismatch')
    if not isinstance(public_context, Mapping):
        return _result('UNSUPPORTED', 'public_context_not_supported')
    context = dict(public_context)
    if type(context.get('interface')) is str and context['interface'] in DELEGATED_INTERFACES:
        prior = v2.bind({'binding_template': v2.TEMPLATE}, buggy_source, context)
        if prior['status'] != 'BOUND':
            return _result(prior['status'], prior['reason'], delegated_contract=v2.CONTRACT)
        witness = {'variant': 'delegated_v2', 'v2_binding': prior,
                   'v2_action_digest': prior['witness']['action_digest'],
                   'public_context_digest': _digest(context)}
        witness['action_digest'] = _digest({'contract': CONTRACT, 'witness': witness})
        return _result('BOUND', 'delegated_v2_binding', witness=witness, memory_authority_granted=False)
    if not _new_context_ok(context):
        return _result('UNSUPPORTED', 'public_context_not_supported')
    if not isinstance(buggy_source, str) or not 0 < len(buggy_source.encode('utf-8', 'surrogatepass')) <= 65536:
        return _result('UNSUPPORTED', 'source_size_or_type')
    try:
        buggy_source.encode('utf-8')
        text = _active(buggy_source, set(context['defined_macros']))
        modules = list(re.finditer(r'\bmodule\s+' + IDENT + r'\b', text))
        ends = list(re.finditer(r'\bendmodule\b', text))
        if not modules and not ends:
            return _result('NO_MATCH', 'no_module')
        if len(modules) > 1 or len(ends) > 1:
            return _result('AMBIGUOUS', 'multiple_modules')
        if len(modules) != 1 or len(ends) != 1 or modules[0].start() >= ends[0].start():
            return _result('UNSUPPORTED', 'module_boundaries_invalid')
        base = modules[0].start()
        local = _registered(text[base:ends[0].end()], context['status_output'])
    except (ValueError, UnicodeError, KeyError) as exc:
        return _result('UNSUPPORTED', str(exc).strip("'") or type(exc).__name__)
    if 'status' in local:
        return local
    start, stop = local['rhs_span'][0] + base, local['rhs_span'][1] + base
    if buggy_source[start:stop] != local['old_rhs']:
        return _result('UNSUPPORTED', 'unmasked_rhs_span_mismatch')
    witness = {'variant': local['variant'], 'roles': local['roles'], 'rhs_span': [start, stop],
               'old_rhs': local['old_rhs'], 'replacement_rhs': local['replacement_rhs'],
               'source_sha256': _sha(buggy_source), 'public_context_digest': _digest(context)}
    witness['action_digest'] = _digest({'contract': CONTRACT, 'witness': witness})
    return _result('BOUND', 'unique_syntactic_nack_status_latch', witness=witness,
                   memory_authority_granted=False)


def apply(asset: Mapping, buggy_source, public_context: Mapping, binding: Mapping):
    current = bind(asset, buggy_source, public_context)
    if current['status'] != 'BOUND' or not isinstance(binding, Mapping) or dict(binding) != current:
        raise ValueError('missing_stale_or_tampered_source_binding')
    witness = current['witness']
    if witness['variant'] == 'delegated_v2':
        candidate, receipt = v2.apply({'binding_template': v2.TEMPLATE}, buggy_source,
                                      public_context, witness['v2_binding'])
        return candidate, {**receipt, 'contract': CONTRACT, 'operator': OPERATOR,
                           'action_digest': witness['action_digest'],
                           'delegated_contract': v2.CONTRACT}
    start, stop = witness['rhs_span']
    candidate = buggy_source[:start] + witness['replacement_rhs'] + buggy_source[stop:]
    if bind(asset, candidate, public_context)['status'] != 'NO_MATCH':
        raise ValueError('candidate_postcondition_not_no_match')
    return candidate, {'contract': CONTRACT, 'operator': OPERATOR,
                       'action_digest': witness['action_digest'],
                       'before_source_sha256': witness['source_sha256'],
                       'after_source_sha256': _sha(candidate), 'rewritten_spans': 1,
                       'source_binding_rederived': True, 'functional_verdict': 'NOT_EVALUATED',
                       'memory_authority_granted': False}
