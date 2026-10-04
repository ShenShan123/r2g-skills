"""Bounded recorded-origin groups for the four I2C NACK v3 TRAIN source closures.

Contract: memory/evaluation/research_r5_i2c_nack_v3_train_generation_contract_20260929.md (2b),
using the v2 lineage method (research_r5_i2c_nack_v2_lineage_contract_20260928.md + A1/A2).
Same method as the retired ``r5_train_lineage_v8`` (frozen-gen6 worktree). Not a proof of human authorship, statistical
independence, or hidden-copy absence. Offline once the pinned inputs exist.
"""
from __future__ import annotations

import difflib
import hashlib
import itertools
import json
from pathlib import Path
import re
import subprocess

from tehm.ids import stable_dumps

PILOT = Path('/data1/zhangdy/RTL/RTL_testbench/_r5_pilot')
TRAIN = PILOT / 'training/i2c-nack-v3-r1'
INPUTS = PILOT / 'source-locks/i2c-v3-lineage-r1/inputs'
CLONES = Path('/data1/zhangdy/RTL/RTL_testbench')
TRAIN_RECEIPT_SHA = 'cf3af0bf7f993e43cf35c246de2b8766b27e77255fe429bd2e176c1a884cd671'
FETCH_LOG_SHA = None  # pinned at first audit run; see PINS below

SOURCES = {
    'alex': {'repo': 'alexforencich/verilog-i2c', 'commit': 'a65be4045e898a52e791c6ee71f8f79a7cd2e129',
             'files': {'rtl/i2c_master.v': 'worker/alex.v'},
             'login': 'alexforencich'},
    'zip': {'repo': 'ZipCPU/wbi2c', 'commit': 'afa64c2c151731fd4bcd5b3af9dd3b9a84c857e2',
            'files': {'rtl/wbi2cmaster.v': 'worker/zip.v',
                      'rtl/lli2cm.v': 'oracles/zip/candidate/stage/rtl/lli2cm.v'},
            'login': 'ZipCPU'},
    'freecores': {'repo': 'freecores/i2c', 'commit': '3b067f00ccced753b0502024766a51f58f3e04bc',
                  'files': {'rtl/verilog/i2c_master_top.v': 'worker/freecores/top.v',
                            'rtl/verilog/i2c_master_byte_ctrl.v': 'worker/freecores/byte_ctrl.v',
                            'rtl/verilog/i2c_master_bit_ctrl.v': 'worker/freecores/bit_ctrl.v',
                            'rtl/verilog/i2c_master_defines.v': 'worker/freecores/defines.v'},
                  'login': 'git:rherveille <rherveille@de7ef3d1-e315-45f5-93a0-b3ed24765857>'},
    'chance189': {'repo': 'chance189/I2C_Master', 'commit': '84cdaab5cfd6e00d594e4273f7b978ca1a0a08a4',
                  'files': {'i2c_master.v': 'worker/chance189.v'},
                  'login': 'git:Chance Reimer <chance189@knights.ucf.edu>'},
}
ELABORATED = {
    'alex': {'i2c_master', 'test_i2c_master'},
    'freecores': {'i2c_master_top', 'i2c_master_byte_ctrl', 'i2c_master_bit_ctrl',
                  'tst_bench_top', 'wb_master_model', 'i2c_slave_model', 'delay'},
    'zip': {'wbi2cmaster.v', 'lli2cm.v'},
    'chance189': {'i2c_master', 'tb_nack_aug'},
}
# `delay` is defined inside bench/verilog/tst_bench_top.v (line 458), not in the DUT closure.
BENCH_ONLY = {'test_i2c_master', 'tst_bench_top', 'wb_master_model', 'i2c_slave_model', 'delay', 'tb_nack_aug'}


def _require(value, reason):
    if not value:
        raise ValueError('I2C v3 TRAIN lineage: ' + reason)


def _sha(path: Path) -> str:
    _require(path.is_file() and not path.is_symlink(), 'missing/linked file ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path):
    return json.loads(path.read_bytes())


def _digest(value) -> str:
    return 'sha256:' + hashlib.sha256(stable_dumps(value).encode()).hexdigest()


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def _slug(path: str) -> str:
    return path.replace('/', '__')


def _inputs(root: Path) -> dict:
    _require(root.is_dir() and root.absolute() == root.resolve(), 'linked/missing input root')
    observed = {}
    for p in sorted(root.rglob('*')):
        _require(not p.is_symlink(), 'linked input')
        if p.is_file() and not p.name.endswith('.headers'):
            observed[p.relative_to(root).as_posix()] = _sha(p)
    for log in ('fetch-log.json', 'fetch-newest-log.json'):
        for entry in _read(root / log):
            _require(entry['curl_rc'] == 0 and entry['http_status'] == '200', 'failed fetch ' + entry['url'])
            _require(observed.get(entry['file']) == entry['sha256'], 'fetched byte drift ' + entry['file'])
    return observed


def _train(train: Path) -> dict:
    _require(_sha(train / 'receipt.json') == TRAIN_RECEIPT_SHA, 'TRAIN receipt identity')
    receipt = _read(train / 'receipt.json')
    for name, digest in receipt['original_files'].items():
        _require(_sha(train / 'original' / name) == digest, 'sealed TRAIN file drift ' + name)
    _require(_read(train / 'original/audit.json') == receipt['audit'], 'TRAIN audit identity')
    _require(all(receipt['audit']['component_training_obligations_pass'].values()), 'TRAIN obligations')
    return receipt


def _elaborated(train: Path) -> dict:
    out = {}
    oracles = train / 'original/oracles'
    for case, vvp in (('alex', 'alex/{arm}/stage/tb/test_i2c_master.vvp'),
                      ('freecores', 'freecores/{arm}/stage/build/tst_bench_top.vvp'),
                      ('chance189', 'chance189/{arm}/stage/tb.vvp')):
        seen = set()
        for arm in ('fault', 'candidate', 'rollback'):
            text = (oracles / vvp.format(arm=arm)).read_text(errors='replace')
            seen |= set(re.findall(r'\.scope module, "[^"]+" "([^"]+)"', text))
        _require(seen == ELABORATED[case], case + ' elaborated modules ' + str(sorted(seen)))
        out[case] = sorted(seen - BENCH_ONLY)
    files = set()
    for arm in ('fault', 'candidate', 'rollback'):
        dep = (oracles / f'zip/{arm}/stage/rtl/obj_dir/Vwbi2cmaster__ver.d').read_text()
        files |= {Path(t).name for t in dep.split() if t.endswith('.v')}
    _require(files == ELABORATED['zip'], 'zip Verilator inputs ' + str(sorted(files)))
    out['zip'] = sorted(files)
    return out


def _pinned_blob(repo_dir: Path, commit: str, path: str) -> str:
    head = subprocess.run(['git', '-C', str(repo_dir), 'rev-parse', 'HEAD'], check=True,
                          capture_output=True, text=True).stdout.strip()
    _require(head == commit, 'local clone HEAD drift')
    row = subprocess.run(['git', '-C', str(repo_dir), 'ls-tree', commit, '--', path], check=True,
                         capture_output=True, text=True).stdout.split()
    _require(len(row) == 4 and row[1] == 'blob', 'pinned tree entry ' + path)
    return row[2]


def _origin(case: str, inputs: Path, train: Path) -> dict:
    spec = SOURCES[case]
    meta = _read(inputs / case / 'repository.json')
    _require(meta['full_name'] == spec['repo'] and meta['fork'] is False and 'parent' not in meta,
             case + ' repository identity/fork')
    files = {}
    for path, rel in spec['files'].items():
        pages = sorted((inputs / case).glob(_slug(path) + '.commits.p*.json'))
        commits = [c for p in pages for c in _read(p)]
        ids = [c['sha'] for c in commits]
        _require(commits and len(ids) == len(set(ids)) and len(commits) < 100 * len(pages) + 1, case + ' history')
        _require(all(c['html_url'] == f'https://github.com/{spec["repo"]}/commit/{c["sha"]}' for c in commits),
                 case + ' history repository')
        oldest, newest = ids[-1], ids[0]
        origin = _read(inputs / case / (_slug(path) + '.origin_commit.json'))
        added = [f for f in origin['files'] if f['filename'] == path]
        _require(origin['sha'] == oldest and len(added) == 1 and added[0]['status'] == 'added',
                 case + ' origin addition ' + path)
        # Amendment A2: GitHub login if linked, else the recorded Git author identity.
        linked = (origin.get('author') or {}).get('login')
        author = origin['commit']['author']
        login = linked or 'git:%s <%s>' % (author['name'], author['email'])
        _require(login == spec['login'], case + ' recorded identity ' + str(login))
        content = (inputs / case / (_slug(path) + '.origin_content')).read_bytes()
        _require(git_blob(content) == added[0]['sha'], case + ' origin blob ' + path)
        latest = _read(inputs / case / f'newest_commit.{newest}.json')
        touched = [f for f in latest['files'] if f['filename'] == path]
        pinned = _pinned_blob(CLONES / spec['repo'], spec['commit'], path)
        _require(len(touched) == 1 and touched[0]['sha'] == pinned, case + ' newest blob != pinned ' + path)
        train_text = (train / 'original' / rel).read_bytes()
        _require(git_blob(train_text) == pinned, case + ' TRAIN candidate != pinned upstream ' + path)
        files[path] = {'origin_commit': oldest, 'origin_date': origin['commit']['author']['date'],
                       'recorded_author': origin['commit']['author']['name'], 'recorded_login': login,
                       'github_login_linked': linked is not None,
                       'origin_blob': added[0]['sha'], 'newest_commit': newest, 'pinned_blob': pinned,
                       'history_commits': len(ids)}
    origins = {row['origin_commit'] for row in files.values()}
    return {'repository': spec['repo'], 'pinned_commit': spec['commit'], 'fork': False,
            'created_at': meta['created_at'], 'origin_commits': sorted(origins),
            'recorded_logins': sorted({row['recorded_login'] for row in files.values()}), 'files': files}


def _header(text: str) -> list[str]:
    head = '\n'.join(text.splitlines()[:60])
    return sorted({line.strip(' /*\t') for line in head.splitlines()
                   if re.search(r'(?i)copyright|author|\(c\)|written by', line)})[:8]


def _tokens(source: str) -> list[str]:
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', source, flags=re.S)
    return re.findall(r'[A-Za-z_$][\w$]*|\d+|\S', text)


def _lines(source: str) -> list[str]:
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', source, flags=re.S)
    return [re.sub(r'\s+', '', line) for line in text.splitlines() if line.strip()]


def _windows(lines: list[str]) -> set:
    return {tuple(lines[i:i + 8]) for i in range(max(0, len(lines) - 7))
            if sum(map(len, lines[i:i + 8])) >= 160}


def compare(left: str, right: str) -> dict:
    a, b = _tokens(left), _tokens(right)
    x, y = _lines(left), _lines(right)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    longest = matcher.find_longest_match(0, len(a), 0, len(b))
    return {'byte_identical': left == right, 'normalized_identical': x == y,
            'comment_stripped_token_identical': a == b,
            'shared_substantive_8_line_windows': len(_windows(x) & _windows(y)),
            'left_tokens': len(a), 'right_tokens': len(b),
            'longest_exact_token_run': longest.size, 'sequence_ratio': round(matcher.ratio(), 6),
            'longest_run_text': ' '.join(a[longest.a:longest.a + longest.size])[:400]}


def classify(comparisons: list[dict]) -> list[dict]:
    """Cross-source pairs that require lineage reassessment."""
    return [c for c in comparisons if c['byte_identical'] or c['normalized_identical']
            or c['comment_stripped_token_identical'] or c['shared_substantive_8_line_windows']]


def audit(train: Path = TRAIN, inputs: Path = INPUTS) -> dict:
    train, inputs = Path(train), Path(inputs)
    pinned_inputs = _inputs(inputs)
    receipt = _train(train)
    elaborated = _elaborated(train)
    origins = {case: _origin(case, inputs, train) for case in SOURCES}
    texts = {(case, path): (train / 'original' / rel).read_text()
             for case, spec in SOURCES.items() for path, rel in spec['files'].items()}
    repos = [o['repository'] for o in origins.values()]
    origin_sets = [set(o['origin_commits']) for o in origins.values()]
    logins = [set(o['recorded_logins']) for o in origins.values()]
    _require(len(set(repos)) == 4, 'distinct repositories')
    _require(all(not (x & y) for x, y in itertools.combinations(origin_sets, 2)), 'shared origin commit')
    _require(all(not (x & y) for x, y in itertools.combinations(logins, 2)), 'shared recorded login')
    comparisons = []
    for (ka, ta), (kb, tb) in itertools.combinations(texts.items(), 2):
        if ka[0] == kb[0]:
            continue
        row = compare(ta, tb)
        comparisons.append({'left': ka[0] + ':' + ka[1], 'right': kb[0] + ':' + kb[1], **row})
    _require(len(comparisons) == 21, 'cross-source pair count')
    shared = classify(comparisons)
    _require(_inputs(inputs) == pinned_inputs, 'inputs changed during audit')
    verdict = 'BOUNDED_RECORDED_ORIGIN_GROUPS_VERIFIED' if not shared else 'LINEAGE_REASSESSMENT_REQUIRED'
    result = {'schema': 'r5-i2c-v3-bounded-train-origin-groups-v1', 'verdict': verdict,
              'valid': not shared, 'scope': 'only_four_pinned_reused_dev_i2c_v3_train_closures',
              'lineage_ids': sorted(repos), 'origins': origins, 'elaborated_dut_closure': elaborated,
              'header_attributions': {f'{c}:{p}': _header(t) for (c, p), t in texts.items()},
              'comparisons': comparisons, 'shared_pairs': [(c['left'], c['right']) for c in shared],
              'train_receipt_sha256': TRAIN_RECEIPT_SHA, 'train_audit_digest': _digest(receipt['audit']),
              'history_inputs_digest': _digest(pinned_inputs),
              'bounded_recorded_origin_groups_verified': not shared,
              'independent_authorship_proven': False, 'statistical_independence_established': False,
              'hidden_rewrites_or_external_generators_ruled_out': False,
              'knowledge_or_asset_authority': False, 'heldout_transfer': False}
    result['digest'] = _digest(result)
    return result


if __name__ == '__main__':
    print(json.dumps(audit(), indent=2, sort_keys=True))
