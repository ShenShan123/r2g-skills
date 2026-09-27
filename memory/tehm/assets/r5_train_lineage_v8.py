"""Bounded recorded-origin groups for the three pinned v8 TRAIN files.

Not a proof of human authorship, statistical independence, or hidden-copy
absence. Repo-level vendoring is retained and distinguished from the actual
elaborated DUT closure. All observations are offline and scope-specific.
"""
from __future__ import annotations

import difflib
import hashlib
import itertools
import json
from pathlib import Path
import re

from tehm.evaluation import research_r5_lineage_history_audit as history
from tehm.ids import stable_dumps
from . import r5_train_raw_v8 as raw

INPUTS = raw.TRAIN.parent / 'source-locks/skid-v8-lineage-r1/inputs'
MUX_ORIGIN = '44d81b794c3c71056b4db27e8bda62c1aa5c83dd'
MUX_PINS = {
    'receipt.json': '462e8d152246304f9d6c55d9ac96b00a3a355d8f9734170eece1bbab5faa274f',
    'github-repository.json': 'd628f2fb960682059831b8fc0ad662aba8aa6a7c003e64c8d562ddfdf9d39367',
    'dut-history.txt': 'd865f247ce33203227019bf35359bd35d66b37a2e05870790fe642deb3833a84',
    'dut-introduction.patch': '57c2c137343ba1046b244f576ed03e80ff65c59def0693c523dd8811c4c741aa',
    'scope-index.txt': 'fa56259ec90700dbc821c96091b0bbb7a72724011c41cdc45d635a4ada8219b9',
    'tb-history.txt': '3871c54d08e33c6dfec6c4f0a07c7ebb9c70558f070317ff3047bedba4840cae',
    'tb-verdict-change.patch': 'e9df975608f20a9323b5d8fe019aff1747c79300d550af920af92c510b391c1b',
}


def _require(value, reason):
    if not value:
        raise ValueError('v8 TRAIN lineage: ' + reason)


def _digest(value):
    return 'sha256:' + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def _input_pins():
    pins = {'mux/' + name: digest for name, digest in MUX_PINS.items()}
    for case in raw.CASE_ORDER[:2]:
        lock = history.CASES[case]
        for suffix, key in [('_commits.json', 'history_sha256'),
                            ('_origin.v', 'origin_source_sha256'),
                            ('_origin_commit.json', 'origin_commit_sha256')]:
            pins['axis_zip/' + case + suffix] = lock[key]
        filename, digest = history.REPOSITORY_METADATA[lock['repo']]
        pins['axis_zip/' + filename] = digest
    return pins


def _inputs(root):
    _require(root.is_dir() and root.absolute() == root.resolve(), 'linked/missing input root')
    observed = {}
    for p in sorted(root.rglob('*')):
        _require(not p.is_symlink(), 'linked input')
        if not p.is_dir():
            observed[p.relative_to(root).as_posix()] = raw._sha(p)
    _require(observed == _input_pins(), 'history input inventory drift')


def _pair_origin(root, case):
    lock = history.CASES[case]
    metadata_file = history.REPOSITORY_METADATA[lock['repo']][0]
    metadata = raw._read(root / metadata_file)
    _require(metadata['full_name'] == lock['repo'] and metadata['fork'] is False,
             'repository identity/fork')
    commits = json.loads((root / (case + '_commits.json')).read_bytes())
    ids = [item['sha'] for item in commits]
    _require(len(ids) == len(set(ids)) == lock['count'] and ids[-1] == lock['origin'],
             'history count/origin')
    _require(all(item['html_url'] == 'https://github.com/' + lock['repo'] + '/commit/' + item['sha']
                 for item in commits), 'history repository')
    origin = raw._read(root / (case + '_origin_commit.json'))
    matches = [f for f in origin['files'] if f['filename'] == lock['path']]
    author, login = (('Alex Forencich', 'alexforencich') if case == 'axis_register' else ('ZipCPU', 'ZipCPU'))
    _require(origin['sha'] == lock['origin'] and len(matches) == 1 and
             matches[0]['status'] == 'added' and origin['commit']['author']['name'] == author
             and origin['author']['login'] == login, 'origin addition/recorded author')
    blob = history.git_blob((root / (case + '_origin.v')).read_bytes())
    _require(blob == matches[0]['sha'] == lock['origin_blob'], 'origin Git blob')
    _require(lock['head'] == raw.SOURCES[case]['commit'] and
             lock['source_sha256'] == raw.SOURCES[case]['candidate_sha256'], 'TRAIN/history source identity')
    return {'repository': lock['repo'], 'origin_commit': lock['origin'],
            'recorded_author': author, 'recorded_login': login, 'origin_blob': blob,
            'history_commits': len(ids), 'nonfork_metadata_snapshot': True}


def _tokens(source):
    # Reproduce the already recorded bounded token screen, not RTL semantics.
    text = source.split('`ifdef FORMAL')[0]
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)
    return re.findall(r'[A-Za-z_$][\w$]*|\d+|\S', text)


def _lines(source):
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', source, flags=re.S)
    return [re.sub(r'\s+', '', line) for line in text.splitlines() if line.strip()]


def _windows(lines):
    return {tuple(lines[i:i + 8]) for i in range(max(0, len(lines) - 7))
            if sum(map(len, lines[i:i + 8])) >= 160}


def _compare(left, right):
    a, b = _tokens(left), _tokens(right)
    x, y = _lines(left), _lines(right)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    longest = matcher.find_longest_match()
    return {'byte_identical': left == right, 'normalized_identical': x == y,
            'shared_substantive_8_line_windows': len(_windows(x) & _windows(y)),
            'comment_stripped_token_identical': a == b,
            'left_tokens': len(a), 'right_tokens': len(b),
            'longest_exact_token_run': longest.size, 'sequence_ratio': matcher.ratio(),
            'longest_run_text': ' '.join(a[longest.a:longest.a + longest.size])}


def _mux_origin(root, source, package):
    saved = raw._read(root / 'receipt.json')
    metadata = raw._read(root / 'github-repository.json')
    _require(metadata['full_name'] == raw.SOURCES['mux_skid']['repository'] and
             metadata['fork'] is False and saved['independent_authorship_proven'] is False,
             'mux identity/fork/historical claim')
    lines = (root / 'dut-history.txt').read_text().splitlines()
    commits = [line.split(' ', 3) for line in lines]
    _require(len(commits) == 3 and len({row[0] for row in commits}) == 3 and
             commits[-1][0] == MUX_ORIGIN and all(row[2] == 'drewbabel' for row in commits),
             'mux file history')
    patch = (root / 'dut-introduction.patch').read_text()
    _require(patch.startswith('commit ' + MUX_ORIGIN + '\nAuthor: drewbabel <') and
             'new file mode 100644\n' in patch and '\n--- /dev/null\n+++ b/rtl/axis_skid.sv\n' in patch,
             'mux recorded addition')
    added = '\n'.join(line[1:] for line in patch.splitlines()
                      if line.startswith('+') and not line.startswith('+++')) + '\n'
    blob = history.git_blob(added.encode())
    _require(source.count('`ifdef FORMAL') == source.count('`endif') == 1,
             'mux inactive FORMAL boundary')
    prefix, formal = source.split('`ifdef FORMAL')
    active_source = prefix + formal.split('`endif')[1]
    _require(blob == '6777ffe6e1f2447d344b265a58d104400e204d76' and
             _tokens(added) == _tokens(active_source), 'mux origin blob/active implementation')
    active = source.split('`ifdef FORMAL')[0]
    _require('`include' not in active and len(re.findall(r'\bmodule\s+axis_skid\b', active)) == 1,
             'mux selected closure')
    for copy in ('original', 'recovery'):
        for arm in ('fault', 'candidate', 'rollback'):
            vvp = (package / copy / 'oracles' / arm / 'native/build/sim').read_text()
            modules = re.findall(r'\.scope module, "[^"]+" "([^"]+)"', vvp)
            _require(sorted(modules) == ['axis_skid', 'axis_skid_tb'], 'vendored/extra elaborated module')
    _require(saved['source_commit'] == raw.SOURCES['mux_skid']['commit'] and
             saved['dut_sha256'] == hashlib.sha256(source.encode()).hexdigest(), 'mux TRAIN/source audit identity')
    return {'repository': metadata['full_name'], 'origin_commit': MUX_ORIGIN,
            'recorded_author': 'drewbabel', 'recorded_login': 'drewbabel', 'origin_blob': blob,
            'history_commits': 3, 'nonfork_metadata_snapshot': True,
            'repository_vendors_train_projects': saved['vendored_repository_components'],
            'selected_native_elaboration_modules': ['axis_skid_tb', 'axis_skid'],
            'selected_closure_uses_vendored_components': False,
            'independent_authorship_proven': False, 'formal_executed': False}


def audit(training_root=raw.TRAIN, inputs=INPUTS):
    training_root, inputs = Path(training_root), Path(inputs)
    _inputs(inputs)
    checked = raw.verify(training_root)
    pair = training_root / raw.PACKAGES['axis_zip']['directory']
    mux = training_root / raw.PACKAGES['mux']['directory']
    sources = {case: (pair / 'original/worker' / (case + '.v')).read_text()
               for case in raw.CASE_ORDER[:2]}
    sources['mux_skid'] = (mux / 'original/worker/candidate.sv').read_text()
    for case, source in sources.items():
        _require(hashlib.sha256(source.encode()).hexdigest() == raw.SOURCES[case]['candidate_sha256'],
                 'TRAIN candidate source drift')
    origins = {case: _pair_origin(inputs / 'axis_zip', case) for case in raw.CASE_ORDER[:2]}
    origins['mux_skid'] = _mux_origin(inputs / 'mux', sources['mux_skid'], mux)
    _require(all(len({row[key] for row in origins.values()}) == 3
                 for key in ('repository', 'origin_commit', 'recorded_login', 'origin_blob')),
             'distinct recorded origins required')
    comparisons = []
    for left, right in itertools.combinations(raw.CASE_ORDER, 2):
        row = _compare(sources[left], sources[right])
        _require(not row['byte_identical'] and not row['normalized_identical'] and
                 not row['comment_stripped_token_identical'] and not row['shared_substantive_8_line_windows'],
                 'shared source requires lineage reassessment')
        comparisons.append({'left': left, 'right': right, **row})
    previous = raw._read(inputs / 'mux/receipt.json')
    for case, label in [('axis_register', 'TRAIN_axis'), ('zipcpu_skidbuffer', 'TRAIN_zipcpu')]:
        row = _compare(sources['mux_skid'], sources[case])
        old = next(item for item in previous['comparisons'] if item['peer'] == label)
        for key in ('byte_identical', 'comment_stripped_token_identical', 'longest_exact_token_run',
                    'sequence_ratio', 'longest_run_text'):
            _require(raw._equal(row[key], old[key]), 'historical descriptive screen drift')
    _inputs(inputs)
    result = {'schema': 'r5-v8-bounded-train-origin-groups-v1', 'valid': True,
        'scope': 'only_three_pinned_reused_dev_train_files',
        'lineage_ids': sorted(row['repository'] for row in origins.values()),
        'origins': origins, 'comparisons': comparisons, 'raw_train_digest': checked['digest'],
        'history_inputs_digest': _digest(_input_pins()),
        'bounded_recorded_origin_groups_verified': True,
        'independent_authorship_proven': False, 'statistical_independence_established': False,
        'hidden_rewrites_or_external_generators_ruled_out': False,
        'knowledge_or_asset_authority': False, 'heldout_transfer': False}
    result['digest'] = _digest(result)
    return result
