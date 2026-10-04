"""R5 Phase 3b part 0 (no model calls): causal situation-keyed store, bounded compression, symptom observer and
retrieval policies (card memory/evaluation/research_r5_phase3b_causal_store_contract_20260929.md).

usage:
  r5_phase3b_causal_store.py run       build everything into ROOT (must not exist)
  r5_phase3b_causal_store.py audit     separate-process re-derivation of stream, store and retrieval from ROOT
"""
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
import r5_phase3_agent as P  # noqa: E402  (tasks, frozen TRAIN helpers, diff and hashing; imports frozen tehm)

ROOT = P.PILOT / 'agent/p3b-part0-r2'  # r1 rendered hint nodes in retrieval (card: statement only)
P3C = P.PILOT / 'agent/p3c-agent-r1'
P3C_RECEIPT_SHA = '29237aa669efe66b38a152c74d72d7a60298aec3504f9fdc5d370a995ec9885f'
PART0_SYMPTOMS_PATH = P.PILOT / 'agent/p3b-part0-r2/symptoms.json'  # situation of the 3c tasks (part 1b guard scope)
assert P.h(PART0_SYMPTOMS_PATH.read_bytes()) == '00415109def289377a911e402d399aa3fc5ebfdcaac6abcbab28ef163fd64c8a'
PART0_SYMPTOMS = {t: v['class'] for t, v in json.loads(PART0_SYMPTOMS_PATH.read_text()).items()}
OSS = Path('/opt/pdk_klayout_openroad/oss-cad-suite/bin')
K = 2
NACK_NAME = r'[A-Za-z_]*nack[A-Za-z_0-9]*'
FAMILY = 'I2C_NACK_STATUS_LATCH'
STATEMENT = {
    'EVENT_SET_TERM': 'the branch that samples a NACK must assign 1 to the status set term',
    'NACK_SAMPLE': 'the acknowledge bit must be sampled with the correct polarity (SDA high = NACK)',
    'HOLD_TERM': 'the next-state status term must default to the current status value (hold)',
    'CLEAR_TERM': 'the status must be cleared to 0 when the next command is accepted',
    'STATUS_REGISTER_LINK': 'the status register must be loaded from its next-state/set term, not a constant',
    'OUTPUT_DRIVE': 'the status output port must be driven by the status register, not a constant',
    'OTHER_NACK': 'other status-signal assignment'}
SYMPTOM_OF = {'EVENT_SET_TERM': 'NEVER_SET', 'STATUS_REGISTER_LINK': 'NEVER_SET', 'OUTPUT_DRIVE': 'NEVER_SET',
              'HOLD_TERM': 'SET_NOT_HELD', 'CLEAR_TERM': 'NOT_CLEARED'}
PRECONDITION = {'OUTPUT_DRIVE': 'status output driven by a continuous assign',
                'STATUS_REGISTER_LINK': 'a status register with non-blocking writes',
                'HOLD_TERM': 'a next-state status signal', 'CLEAR_TERM': 'a next-state status signal',
                'EVENT_SET_TERM': 'none'}
TRUTH = {'evt': 'EVENT_SET_TERM', 'holdloss': 'HOLD_TERM', 'clearloss': 'CLEAR_TERM'}
OBSERVER = r'''
module p3b_observer;
  reg max3 = 1'b0; reg end3 = 1'bx; reg acc4 = 1'bx; reg armed = 1'b0; reg printed = 1'b0; integer cnt = 0;
  reg soa = 1'b0; reg soa_printed = 1'b0;
  always @(posedge tb_spec_nack.clk) begin
    if ((tb_spec_nack.phase == 1 || tb_spec_nack.phase == 2) && tb_spec_nack.nack === 1'b1) soa <= 1'b1;
    if (tb_spec_nack.phase == 3 && !soa_printed) begin  // reported early: a later stage may hang
      $display("P3B_SOA soa=%b", soa);
      soa_printed <= 1'b1;
    end
    if (tb_spec_nack.phase == 3) begin
      if (tb_spec_nack.nack === 1'b1) max3 <= 1'b1;
      end3 <= tb_spec_nack.nack;
    end
    if (tb_spec_nack.phase == 4) begin
      if (tb_spec_nack.cmd_valid === 1'b1) armed <= 1'b1;
      else if (armed && cnt == 0 && acc4 === 1'bx) cnt <= 2;
      if (cnt > 0) begin
        cnt <= cnt - 1;
        if (cnt == 1) acc4 <= tb_spec_nack.nack;
      end
      if (!printed && acc4 !== 1'bx) begin
        $display("P3B_OBS max3=%b end3=%b acc4=%b", max3, end3, acc4);
        printed <= 1'b1;
      end
    end
  end
endmodule
'''


def truth_role(tid):
    kind = tid.rsplit('_', 1)[1]
    if kind == 'copy':
        return 'STATUS_REGISTER_LINK' if '_007_' in tid else 'OUTPUT_DRIVE'
    return TRUTH.get(kind)


# ---------------------------------------------------------------- roles, features, static checks
def role_patterns(name):
    return (('OUTPUT_DRIVE', re.compile(rf'^\s*assign\s+{name}\s*=')),
            ('STATUS_REGISTER_LINK', re.compile(rf'(?<![\w.]){name}\s*<=\s*[A-Za-z_]\w*\s*;')),
            ('HOLD_TERM', re.compile(rf'(?<![\w.]){name}\s*=\s*{name}\s*;')),
            ('CLEAR_TERM', re.compile(rf"(?<![\w.]){name}\s*<?=\s*(\d+'[bdh]0+|0)\s*;")),
            ('EVENT_SET_TERM', re.compile(rf"(?<![\w.]){name}\s*<?=\s*(\d+'[bdh]\d+|\d+)\s*;")),
            ('OTHER_NACK', re.compile(rf'(?<![\w.]){name}\s*(<=|=(?!=))')))


def classify(stmt, name=NACK_NAME):
    for role, pat in role_patterns(name):
        if pat.search(stmt.split('//')[0]):
            return role
    return None


def roles_of(pre, post, name=NACK_NAME, sides='+'):
    roles = set()
    for line in difflib.unified_diff(pre.splitlines(), post.splitlines(), lineterm='', n=0):
        if line[:1] in sides and line[:3] not in ('+++', '---'):
            r = classify(line[1:], name)
            if r:
                roles.add(r)
    return sorted(roles)


def features(src):
    comb = re.search(r'always\s*@\s*\*|always\s*@\s*\(\s*\*\s*\)|always_comb', src)
    return {'process_style': '2proc' if comb else '1proc',
            'status_drive': 'assign' if re.search(r'^\s*assign\s+nack\s*=', src, re.M) else 'reg',
            'has_next_state': bool(re.search(r'\bnext_\w*nack\w*|\b\w*nack\w*_next\b', src))}


def static_checks(src):
    writes = {}
    for m in re.finditer(rf'(?<![\w.])({NACK_NAME})\s*<=\s*([^;]+);', src):
        writes.setdefault(m[1], []).append(m[2].strip())
    never_loaded = any(all(re.fullmatch(r"\d+'[bdh]0+|0", r) for r in rhs) for rhs in writes.values())
    return {'output_drive_constant': bool(re.search(r"^\s*assign\s+nack\s*=\s*\d+'[bdh]\d+\s*;", src, re.M)),
            'register_never_loaded': never_loaded}


def fix_form(pre, post, name):
    for line in difflib.unified_diff(pre.splitlines(), post.splitlines(), lineterm='', n=0):
        if line[:1] == '+' and line[:3] != '+++' and classify(line[1:], name):
            s = line[1:]
            kind = 'assign' if re.match(r'\s*assign\b', s) else ('nonblocking' if '<=' in s else 'blocking')
            val = 'literal' if re.search(r"=\s*(\d+'[bdh]\d+|\d+)\s*;", s) else 'signal'
            return kind + '/' + val
    return 'none'


# ---------------------------------------------------------------- episodes
def train_episodes():
    out = []
    for case in P.raw.CASE_ORDER:
        source = P.evidence.train_source(case)
        cand, _ = P.apply_rtl_action(source, P.payload_from_source_i2c_v3(source, P.raw.SOURCES[case]['public_context']))
        assert P.raw._equal(P.evidence._source_hashes(case, cand), P.raw.SOURCES[case]['candidate_sha256'])
        pairs = ([(P.decode_closure(source)[r], P.decode_closure(cand)[r]) for r in P.raw.ROLES]
                 if case == 'freecores' else [(source, cand)])
        pre, post = [(a, b) for a, b in pairs if a != b][0]
        changed = [l[1:] for l in difflib.unified_diff(pre.splitlines(), post.splitlines(), lineterm='', n=0)
                   if l[:1] == '+' and l[:3] != '+++']
        lhs = re.match(r'\s*([A-Za-z_]\w*)\s*<?=', changed[0])[1]  # the frozen action edits exactly the status writer
        out.append({'id': 'train:' + case, 'design': 'train:' + case, 'kind': 'positive', 'pre': pre, 'post': post,
                    'status_name': re.escape(lhs), 'evidence': 'frozen TRAIN source + v3 action (pinned candidate)'})
    return out


def p3c_episodes(all_tasks):
    rec = json.loads((P3C / 'receipt.json').read_text())
    assert P.h((P3C / 'receipt.json').read_bytes()) == P3C_RECEIPT_SHA
    out = []
    for design in ('007', '025'):
        for key in sorted(k for k in rec['results'] if k.startswith('p3c_%s_' % design)):
            r = rec['results'][key]
            tid, arm, s = key.split('/')
            if not r['changed'] or r['candidate_sha256'] is None:
                continue
            path = P3C / 'calls' / tid / arm / (s + '.candidate.txt')
            post = path.read_text()
            assert P.h(post) == r['candidate_sha256']
            healthy = tid.endswith('_healthy')
            if not healthy and r['verdicts'] == P.PASS:
                kind = 'positive'
            elif healthy and r['verdicts'] != P.PASS:
                kind = 'negative'
            else:
                continue
            out.append({'id': 'p3c:' + key, 'design': 'ctrlpop:' + design, 'kind': kind, 'pre': all_tasks[tid], 'post': post,
                        'status_name': NACK_NAME, 'evidence': str(path.relative_to(P.PILOT)), 'task': tid,
                        'situation': PART0_SYMPTOMS[tid]})
    return out


# ---------------------------------------------------------------- store
class Store:
    def __init__(self):
        self.nodes, self.family_guards, self.log = {}, [], []

    def add(self, ep):
        if ep['kind'] == 'negative':
            touched = roles_of(ep['pre'], ep['post'], ep['status_name'], sides='+-')
            touched = [r for r in touched if r != 'OTHER_NACK'] or ['FAMILY']
            acts = []
            for r in touched:
                guard = {'role': r, 'rule': ('%s is intact in this situation: do not edit it' % r if r != 'FAMILY' else
                                             'do not restructure non-status logic for a status symptom'),
                         'situation': ep['situation'], 'support': [ep['id']]}
                bucket = self.family_guards if r == 'FAMILY' else self.nodes.setdefault(r, self._node(r))['guards']
                same = [g for g in bucket if g['rule'] == guard['rule'] and g['situation'] == guard['situation']]
                if same:
                    same[0]['support'].append(ep['id']); acts.append('GUARD_ABSORB')
                else:
                    bucket.append(guard); acts.append('GUARD_ADD')
            self.log.append({'episode': ep['id'], 'actions': acts})
            return
        roles = [r for r in roles_of(ep['pre'], ep['post'], ep['status_name']) if r != 'OTHER_NACK']
        if not roles:
            self.log.append({'episode': ep['id'], 'actions': ['UNCLASSIFIED']})
            return
        acts = []
        f = features(ep['pre'])
        style = '%s|%s|next=%s|%s' % (f['process_style'], f['status_drive'], f['has_next_state'],
                                     fix_form(ep['pre'], ep['post'], ep['status_name']))
        for r in roles:
            if r not in self.nodes:
                self.nodes[r] = self._node(r); acts.append('ADD_NODE')
            n = self.nodes[r]
            n['episodes'] += 1
            n['designs'] = sorted(set(n['designs']) | {ep['design']})
            n['evidence'].append(ep['id'])
            same = [x for x in n['realizations'] if x['style'] == style]
            if same:
                same[0]['support'] += 1; acts.append('ABSORB')
            else:
                new = {'style': style, 'support': 1, 'episode': ep['id'], 'diff': P.udiff(ep['pre'], ep['post'], 'realization')}
                if len(n['realizations']) < K:
                    n['realizations'].append(new); acts.append('ADD_REALIZATION')
                else:
                    n['overflow'][style] = n['overflow'].get(style, 0) + 1
                    best = max(n['overflow'].items(), key=lambda kv: kv[1])
                    low = min(n['realizations'], key=lambda x: x['support'])
                    if best[0] == style and n['overflow'][style] > low['support']:
                        n['realizations'].remove(low)
                        n['overflow'][low['style']] = low['support']
                        new['support'] = n['overflow'].pop(style)
                        n['realizations'].append(new); acts.append('SWAP')
                    else:
                        acts.append('ABSORB_OVERFLOW')
            n['status'] = 'knowledge' if len(n['designs']) >= 2 else 'hint'
        self.log.append({'episode': ep['id'], 'actions': acts})

    @staticmethod
    def _node(role):
        return {'role': role, 'statement': STATEMENT[role], 'predicted_symptom': SYMPTOM_OF.get(role),
                'precondition': PRECONDITION.get(role), 'guards': [], 'episodes': 0, 'designs': [],
                'evidence': [], 'realizations': [], 'overflow': {}, 'status': 'hint'}

    def render_node(self, r, situation=None):
        n = self.nodes[r]
        if n['status'] != 'knowledge':  # card section 2: hints are not rendered beyond their role statement
            return 'Role %s: %s. When broken, the port symptom is %s.' % (r, n['statement'], n['predicted_symptom'])
        parts = ['Role %s (%s; support %d verified repairs over %d designs): %s. When broken, the port symptom is %s. '
                 'Applies when: %s.' % (r, n['status'], n['episodes'], len(n['designs']), n['statement'],
                                        n['predicted_symptom'], n['precondition'])]
        parts += ['Guard (learned when the observation was %s): %s' % (g['situation'], g['rule'])
                  for g in n['guards'] if situation is None or g['situation'] == situation]
        parts += ['Realization (%s):\n%s' % (x['style'], x['diff']) for x in n['realizations']]
        return '\n'.join(parts)

    def guards_for(self, situation):
        return ['Family guard (learned when the observation was %s): %s' % (g['situation'], g['rule'])
                for g in self.family_guards if g['situation'] == situation]

    def render_active(self):
        roles = sorted(r for r, n in self.nodes.items() if n['status'] == 'knowledge' and n['episodes'])
        parts = ['Family %s, causal path: NACK sample -> EVENT_SET_TERM -> HOLD_TERM/CLEAR_TERM -> '
                 'STATUS_REGISTER_LINK -> OUTPUT_DRIVE -> nack.' % FAMILY]
        parts += ['Family guard: ' + g['rule'] for g in self.family_guards]
        parts += [self.render_node(r) for r in roles]
        return '\n'.join(parts)


def raw_render(eps):
    return '\n'.join('Verified repair (%s):\n%s' % (e['id'], P.udiff(e['pre'], e['post'], 'experience')) for e in eps)


# ---------------------------------------------------------------- observer
def observe(text, out):
    out.mkdir(parents=True, exist_ok=False)
    dut = out / ('dut.sv' if re.search(r'\b(always_ff|always_comb|logic)\b', text) else 'dut.v')
    dut.write_text(text)
    (out / 'tb_spec_nack.sv').write_bytes((P.CTRL / 'oracle/tb_spec_nack.sv').read_bytes())
    (out / 'p3b_observer.v').write_text(OBSERVER)
    comp = subprocess.run([str(OSS / 'iverilog'), '-g2012', '-o', 'obs.vvp', '-s', 'tb_spec_nack', '-s', 'p3b_observer',
                           'tb_spec_nack.sv', 'p3b_observer.v', dut.name], cwd=out, capture_output=True, text=True, timeout=60)
    (out / 'compile.log').write_text(comp.stdout + comp.stderr)
    if comp.returncode:
        return {'class': 'UNOBSERVED', 'line': None}
    sim = subprocess.run([str(OSS / 'vvp'), '-n', 'obs.vvp'], cwd=out, capture_output=True, text=True, timeout=300)
    (out / 'sim.log').write_text(sim.stdout + sim.stderr)
    return classify_obs(sim.stdout)


def classify_obs(stdout):
    if 'P3B_SOA soa=1' in stdout.splitlines():  # part 1b: nack set during acknowledged transfers
        return {'class': 'SET_ON_ACK', 'line': 'P3B_SOA soa=1'}
    lines = [l for l in stdout.splitlines() if l.startswith('P3B_OBS ')]
    if len(lines) != 1:
        return {'class': 'UNOBSERVED', 'line': None}
    v = dict(x.split('=') for x in lines[0].split()[1:])
    if v['max3'] == '0':
        c = 'NEVER_SET'
    elif v['end3'] == '0':
        c = 'SET_NOT_HELD'
    elif v['end3'] == '1' and v['acc4'] == '1':
        c = 'NOT_CLEARED'
    elif v['end3'] == '1' and v['acc4'] == '0':
        c = 'NO_ANOMALY'
    else:
        c = 'UNOBSERVED'
    return {'class': c, 'line': lines[0]}


# ---------------------------------------------------------------- retrieval
def causal_candidates(src, symptom):
    f, s = features(src), static_checks(src)
    if symptom == 'NEVER_SET':
        cands = []
        if f['status_drive'] == 'assign' and s['output_drive_constant']:
            cands.append('OUTPUT_DRIVE')
        if s['register_never_loaded']:
            cands.append('STATUS_REGISTER_LINK')
        return cands + ['EVENT_SET_TERM']
    if symptom in ('SET_NOT_HELD', 'NOT_CLEARED'):
        role = 'HOLD_TERM' if symptom == 'SET_NOT_HELD' else 'CLEAR_TERM'
        return [role] if f['has_next_state'] else ['OTHER_NACK']
    if symptom == 'SET_ON_ACK':
        return ['NACK_SAMPLE']
    return []


def retrieve(policy, store, eps, src, symptom):
    if policy == 'none':
        return {'role': None, 'text': ''}
    if policy == 'all':
        return {'role': 'ALL', 'candidates': sorted(r for r, n in store.nodes.items() if n['status'] == 'knowledge'),
                'text': store.render_active()}
    if policy == 'similarity':
        pos = [e for e in eps if e['kind'] == 'positive']
        best = max(pos, key=lambda e: (difflib.SequenceMatcher(None, src.splitlines(), e['pre'].splitlines(),
                                                               autojunk=False).ratio(), e['id']))
        roles = [r for r in roles_of(best['pre'], best['post'], best['status_name']) if r != 'OTHER_NACK']
        role = roles[0] if roles else None
        return {'role': role, 'episode': best['id'], 'text': store.render_node(role) if role in store.nodes else ''}
    cands = causal_candidates(src, symptom)
    if not cands:
        return {'role': None, 'candidates': [], 'text': ''}
    role = cands[0]
    text = store.render_node(role) if role in store.nodes else 'Role %s: %s.' % (role, STATEMENT.get(role, ''))
    return {'role': role, 'candidates': cands, 'text': text}


# ---------------------------------------------------------------- run / audit
def build(all_tasks, symptoms):
    train, p3c = train_episodes(), p3c_episodes(all_tasks)
    stream = train + p3c
    full, raw_pos, series = Store(), [], []
    for e in stream:
        full.add(e)
        if e['kind'] == 'positive':
            raw_pos.append(e)
        series.append({'episode': e['id'], 'kind': e['kind'], 'actions': full.log[-1]['actions'],
                       'raw_append_bytes': len(raw_render(raw_pos).encode()),
                       'compressed_active_bytes': len(full.render_active().encode())})
    retrieval = {}
    for held in ('007', '025'):
        other = '025' if held == '007' else '007'
        eps = train + [e for e in p3c if e['design'] == 'ctrlpop:' + other]
        store = Store()
        for e in eps:
            store.add(e)
        for tid in sorted(t for t in all_tasks if '_%s_' % held in t):
            row = {'truth': truth_role(tid), 'symptom': symptoms[tid]['class']}
            for policy in ('none', 'all', 'similarity', 'causal'):
                r = retrieve(policy, store, eps, all_tasks[tid], symptoms[tid]['class'])
                row[policy] = {'role': r['role'], 'bytes': len(r['text'].encode()), 'advice': bool(r['text']),
                               **({'episode': r['episode']} if 'episode' in r else {}),
                               **({'candidates': r['candidates']} if 'candidates' in r else {})}
            retrieval[tid] = row
    summary = {}
    for policy in ('none', 'all', 'similarity', 'causal'):
        faults = [v for t, v in retrieval.items() if not t.endswith('healthy')]
        healthy = [v for t, v in retrieval.items() if t.endswith('healthy')]
        summary[policy] = {'fault_top1_correct': sum(v[policy]['role'] == v['truth'] for v in faults),
                           'fault_truth_in_candidates': sum(v['truth'] in v[policy].get('candidates', [v[policy]['role']]) for v in faults),
                           'faults': len(faults), 'healthy_given_advice': sum(v[policy]['advice'] for v in healthy),
                           'healthy': len(healthy),
                           'mean_bytes_faults': round(sum(v[policy]['bytes'] for v in faults) / len(faults))}
    store_view = {r: {k: v for k, v in n.items() if k not in ('evidence',)} | {'evidence_count': len(n['evidence'])}
                  for r, n in full.nodes.items()}
    return {'stream': series, 'store': store_view, 'family_guards': full.family_guards, 'retrieval': retrieval,
            'retrieval_summary': summary, 'episode_counts': {'train': len(train),
            'p3c_positive': sum(e['kind'] == 'positive' for e in p3c), 'p3c_negative': sum(e['kind'] == 'negative' for e in p3c)},
            'role_agreement_with_p3c_classifier': all(roles_of(e['pre'], e['post']) == P.causal_roles(e['pre'], e['post'])
                                                      for e in p3c if e['kind'] == 'positive')}, stream


def run():
    ROOT.mkdir(parents=True, exist_ok=False)
    all_tasks = P.tasks()
    symptoms = {tid: observe(text, ROOT / 'observer' / tid) for tid, text in sorted(all_tasks.items())}
    P.base.write(ROOT / 'symptoms.json', symptoms)
    report, stream = build(all_tasks, symptoms)
    with open(ROOT / 'evidence.jsonl', 'w') as f:
        for e in stream:
            f.write(json.dumps({'id': e['id'], 'design': e['design'], 'kind': e['kind'], 'pre_sha256': P.h(e['pre']),
                                'post_sha256': P.h(e['post']), 'evidence': e['evidence']}, sort_keys=True) + '\n')
    report.update(schema='r5-phase3b-part0-v1', model_calls=0, driver_sha256=P.h(Path(__file__).read_bytes()),
                  p3c_receipt_sha256=P3C_RECEIPT_SHA, k=K,
                  symptom_by_task={t: s['class'] for t, s in symptoms.items()})
    P.base.write(ROOT / 'report.json', report)
    print(json.dumps({'symptoms': report['symptom_by_task'], 'episode_counts': report['episode_counts'],
                      'role_agreement': report['role_agreement_with_p3c_classifier'],
                      'final_bytes': report['stream'][-1], 'retrieval_summary': report['retrieval_summary'],
                      'store': {r: (n['status'], n['episodes'], len(n['designs']), len(n['realizations']), len(n['guards']))
                                for r, n in report['store'].items()},
                      'family_guards': len(report['family_guards'])}, indent=1))
    return 0


def audit():
    """Re-derive symptoms from the observer logs and the whole report from the pinned inputs; compare."""
    rec = json.loads((ROOT / 'report.json').read_text())
    all_tasks = P.tasks()
    symptoms = {t: classify_obs((ROOT / 'observer' / t / 'sim.log').read_text()) for t in sorted(all_tasks)}
    again, _ = build(all_tasks, symptoms)
    checks = {'symptoms_rederived': {t: s['class'] for t, s in symptoms.items()} == rec['symptom_by_task'],
              'report_rederived': all(again[k] == rec[k] for k in again),
              'no_model_calls': rec['model_calls'] == 0,
              'observer_inputs_are_tasks': all((ROOT / 'observer' / t / ('dut.sv' if (ROOT / 'observer' / t / 'dut.sv').exists() else 'dut.v')).read_text()
                                               == all_tasks[t] for t in all_tasks),
              'tb_pinned': all(P.h((ROOT / 'observer' / t / 'tb_spec_nack.sv').read_bytes()) == P.ORACLE_TB_SHA for t in all_tasks)}
    out = {'schema': 'r5-phase3b-part0-audit-v1', 'valid': all(checks.values()), 'checks': checks}
    print(json.dumps(out, indent=1))
    return out


def main():
    if sys.argv[1:] == ['run']:
        return run()
    if sys.argv[1:] == ['audit']:
        out = audit()
        P.base.write(ROOT / 'audit.json', out)
        return 0 if out['valid'] else 1
    raise SystemExit(__doc__)


if __name__ == '__main__':
    raise SystemExit(main())
