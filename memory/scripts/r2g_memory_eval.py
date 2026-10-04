"""R2G memory evaluation harness: sandbox flows, frozen stores, preregistered gates (memory/evaluation/r2g_memory_*).

Runs only inside a sandbox copy (_r2g_eval/sandbox*) with the memory frozen or confined to its arm's store; the
tracked knowledge store is hashed before and after. Commands of finished, sealed phases are retired (git history).

  qualify     control flows, no fix loop, no memory (the task list of a round)
  d2-setup    fresh or code-only-refreshed sandbox from `git archive HEAD`, per-lane TEHM stores
  d2-stream   one (arm, lane[, rep]) stream: engineer loop, then LLM only if the arm's own repair failed
  g-trials    hierarchical component trials on training designs (the composition pool; no LLM)
  g-store     build component-pool stores from the trials
  h-select / h-store / h-replace / h-analyze / h-ablate
              Phase H: frozen task set, SL stores, replacement attempts (H-A4), analysis, causal ablation
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVAL = Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval')
SB = EVAL / 'sandbox'
SKILL = SB / 'r2g-skills/signoff-loop'
FLOW = SKILL / 'scripts/flow'
CASES = SB / 'design_cases'
DESIGNS = ('des_area', 'sha256_core', 'verilog_ethernet_eth_demux', 'verilog_ethernet_udp_ip_rx_64', 'chacha_core')
TRACKED = {'knowledge.sqlite': 'e5b46fe49c289b609c39df077be4cfcf444366767f54edc75686cc50f1d449f0',
           'heuristics.json': 'cfd39ec8120cce14bf0b5149695b17afae880170bab1f5f47c6777eaac1166d0'}
ROUNDS = {  # round 1: card r2g_memory_backend_eval_contract; round 2: r2g_memory_backend_eval_round2_contract;
    # rounds 3/4: Phase D2 (r2g_memory_phaseD_contract_20261001) -- recurring situations, new utilisation variants
    1: {'platform': 'sky130hd', 'variants': {'default': None, 'u70': 70, 'u85': 85}, 'qual': 'qualification',
        'sandbox': 'sandbox'},
    2: {'platform': 'sky130hs', 'variants': {'u8': 8, 'u12': 12, 'u17': 17}, 'qual': 'qualification-r2',
        'sandbox': 'sandbox'},
    3: {'platform': 'sky130hd', 'variants': {'u75': 75, 'u80': 80, 'u90': 90}, 'qual': 'qualification-d2hd',
        'sandbox': 'sandbox-d2', 'lane': 'hd'},
    4: {'platform': 'sky130hs', 'variants': {'u10': 10, 'u14': 14, 'u20': 20}, 'qual': 'qualification-d2hs',
        'sandbox': 'sandbox-d2', 'lane': 'hs'},
    # rounds 5/6: Phase F (r2g_memory_phaseF_contract_20261002) -- held-out variants, frozen memory, no LLM
    5: {'platform': 'sky130hd', 'variants': {'u72': 72, 'u77': 77, 'u82': 82, 'u87': 87}, 'qual': 'qualification-fhd',
        'sandbox': 'sandbox-f', 'lane': 'hd', 'frozen': True, 'order_seed': '20261002'},
    6: {'platform': 'sky130hs', 'variants': {'u9': 9, 'u11': 11, 'u13': 13, 'u16': 16, 'u18': 18},
        'qual': 'qualification-fhs', 'sandbox': 'sandbox-f', 'lane': 'hs', 'frozen': True, 'order_seed': '20261002'},
    # rounds 7/8: Phase H (r2g_memory_phaseH_contract_20261002) -- new variants + new designs, stage 2 with LLM
    7: {'platform': 'sky130hd', 'variants': {'u74': 74, 'u79': 79, 'u84': 84, 'u89': 89}, 'qual': 'qualification-hhd',
        'sandbox': 'sandbox-f', 'lane': 'hd', 'frozen': True, 'order_seed': '20261003'},
    8: {'platform': 'sky130hs', 'variants': {'u8': 8, 'u15': 15, 'u19': 19}, 'qual': 'qualification-hhs',
        'sandbox': 'sandbox-f', 'lane': 'hs', 'frozen': True, 'order_seed': '20261003'}}
CFG = dict(ROUNDS[1])  # set from --round in main()


def use_sandbox(name):
    """Point every sandbox path at EVAL/<name> (rounds 1-2: 'sandbox'; Phase D2: 'sandbox-d2')."""
    global SB, SKILL, FLOW, CASES, LOOP
    SB = EVAL / name
    SKILL = SB / 'r2g-skills/signoff-loop'
    FLOW = SKILL / 'scripts/flow'
    CASES = SB / 'design_cases'
    LOOP = SKILL / 'scripts/loop/engineer_loop.py'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tracked_unchanged():
    k = REPO / 'r2g-skills/signoff-loop/knowledge'
    return {n: sha(k / n) == v for n, v in TRACKED.items()}


def base_env(backend):
    env = dict(os.environ)
    env.update(R2G_MEMORY_BACKEND=backend, R2G_MEMORY_READ_ONLY_EVAL='1', NUM_CORES='8', ORFS_TIMEOUT='5400',
               R2G_KNOWLEDGE_DB=str(SKILL / 'knowledge/knowledge.sqlite'),
               R2G_LEGACY_KNOWLEDGE_DIR=str(SKILL / 'knowledge'),
               R2G_JOURNAL_DB=str(EVAL / 'journal-eval.sqlite'))
    return env


def make_variant(design, variant):
    src = CASES / (design + '__sky130hd')
    dst = CASES / ('%s__%s__%s' % (design, CFG['platform'], variant))
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    cfg = dst / 'constraints/config.mk'
    text = cfg.read_text().replace(str(src) + '/', str(dst) + '/')
    if CFG['platform'] != 'sky130hd':
        text = re.sub(r'^export PLATFORM\s*=.*$', 'export PLATFORM    = %s' % CFG['platform'], text, flags=re.M)
    util = CFG['variants'][variant]
    if util is not None:
        text = re.sub(r'^export (DIE_AREA|CORE_AREA)\s*=.*\n', '', text, flags=re.M)
        text += '\nexport CORE_UTILIZATION = %d\n' % util
    cfg.write_text(text)
    return dst


def run(cmd, env, log, timeout):
    with open(log, 'a') as f:
        f.write('\n$ %s\n' % ' '.join(map(str, cmd)))
        f.flush()
        try:
            return subprocess.run([str(c) for c in cmd], env=env, stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            f.write('TIMEOUT\n')
            return -9


def jload(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return {}


def control_flow(proj, env):
    """ORFS backend then strict signoff; no fix loop. Returns the outcome record."""
    log = proj / 'eval_flow.log'
    t0 = time.time()
    rc_orfs = run(['bash', FLOW / 'run_orfs.sh', proj, CFG['platform']], env, log, 6000)
    run(['python3', SKILL / 'scripts/extract/extract_ppa.py', proj, proj / 'reports/ppa.json'], env, log, 600)
    final = list((proj / 'backend').rglob('6_final.odb')) + list((proj / 'backend').rglob('6_final.gds'))
    rec = {'orfs_rc': rc_orfs, 'final_layout': bool(final), 'ppa': {k: jload(proj / 'reports/ppa.json').get(k)
                                                                     for k in ('orfs_status', 'orfs_fail_stage')}}
    if final:
        run(['python3', SKILL / 'scripts/reports/check_timing.py', proj], env, log, 600)
        rec['timing_tier'] = jload(proj / 'reports/timing_check.json').get('tier')
        rec['signoff_rc'] = run(['bash', FLOW / 'run_strict_signoff.sh', proj, CFG['platform']], env, log, 9000)
        for leg in ('drc', 'lvs', 'rcx'):
            j = jload(proj / 'reports' / (leg + '.json'))
            rec[leg] = {k: j.get(k) for k in ('status', 'total_violations', 'mismatch_class') if k in j}
    rec['wall_s'] = round(time.time() - t0)
    drc_ok = (rec.get('drc') or {}).get('status') in ('clean', 'clean_beol')
    lvs_ok = (rec.get('lvs') or {}).get('status') == 'clean'
    rec['signoff_clean'] = bool(final and drc_ok and lvs_ok)
    return rec


def qualify(args):
    out = EVAL / CFG['qual']
    out.mkdir(parents=True, exist_ok=True)
    before = tracked_unchanged()
    jobs = []
    for d in (args.designs.split(',') if args.designs else DESIGNS):
        for v in args.variants.split(','):
            jobs.append((d, v))

    def one(job):
        d, v = job
        proj = make_variant(d, v)
        rec = {'design': d, 'variant': v, 'project': str(proj), 'backend': 'none', **control_flow(proj, base_env('none'))}
        (out / ('%s__%s.json' % (d, v))).write_text(json.dumps(rec, indent=2, sort_keys=True) + '\n')
        print(json.dumps({k: rec.get(k) for k in ('design', 'variant', 'final_layout', 'signoff_clean', 'timing_tier', 'wall_s')}),
              flush=True)
        return rec

    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        list(ex.map(one, jobs))
    # A5: the summary is rebuilt from every per-flow record on disk, so a partial re-run never drops records
    recs = [json.loads(f.read_text()) for f in sorted(out.glob('*__*.json'))]
    summary = {'schema': 'r2g-memory-eval-qualification-v1', 'tracked_before': before, 'tracked_after': tracked_unchanged(),
               'records': recs}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'tracked_unchanged': summary['tracked_after'], 'tasks': [(r['design'], r['variant']) for r in recs
                                                                           if not r['signoff_clean']]}))


# ---------------------------------------------------------------- engineer loop and the LLM step
LOOP = SKILL / 'scripts/loop/engineer_loop.py'
LLM_MODEL_LABEL, LLM_API_MODEL, LLM_ITERS = 'deepseek-v4.1-flash', 'deepseek-flash', 3
# Whitelisted config.mk knobs for the LLM arm (no clock-period change: no timing relaxation). ONE policy,
# shared with memory edits: knowledge/knob_policy.py (Phase D card; identical to the rounds 1-2 table).
sys.path.insert(0, str(REPO / 'r2g-skills/signoff-loop/knowledge'))
from knob_policy import KNOB_BOUNDS as KNOBS  # noqa: E402
STAGES = ('synth', 'floorplan', 'place', 'cts', 'route')


def tasks_from_qualification():
    """A2/A3: a task is a qualified failure that is NOT already physically clean (des_area/default only failed LVS)."""
    summ = json.loads((EVAL / CFG['qual'] / 'summary.json').read_text())
    out = []
    for r in summ['records']:
        phys = (r.get('final_layout') and (r.get('drc') or {}).get('status') in ('clean', 'clean_beol')
                and r.get('timing_tier') in ('clean', 'minor'))
        if not r['signoff_clean'] and not phys:
            out.append((r['design'], r['variant']))
    return out


def arm_project(design, variant, arm):
    src = CASES / ('%s__%s__%s' % (design, CFG['platform'], variant))
    dst = CASES / ('%s__%s__%s' % (design, variant, arm))
    if dst.exists():
        shutil.rmtree(dst)
    # every arm starts from the same fresh task config and runs the whole flow itself (A3: copying a backend
    # state and resuming is refused by the skill's resume-lineage guard)
    shutil.copytree(src / 'constraints', dst / 'constraints')
    for sub in ('input', 'rtl', 'tb', 'lint', 'sim', 'synth', 'backend', 'drc', 'lvs', 'rcx', 'reports'):
        (dst / sub).mkdir(parents=True, exist_ok=True)
    cfg = dst / 'constraints/config.mk'
    cfg.write_text(cfg.read_text().replace(str(src) + '/', str(dst) + '/'))
    return dst


def final_verdict(proj, env, log):
    """Uniform final check: judge ONLY the newest backend run (amendment A3 -- an older completed run must never
    stand in for a newer failed one). Timing tier + strict signoff run only when that newest run completed."""
    runs = sorted(d for d in (proj / 'backend').glob('RUN_*') if d.is_dir())
    newest = runs[-1] if runs else None
    final = bool(newest and ((newest / 'final/6_final.odb').exists() or (newest / 'final/6_final.gds').exists()))
    rec = {'final_layout': final, 'judged_run': newest.name if newest else None}
    if final:
        run(['python3', SKILL / 'scripts/reports/check_timing.py', proj], env, log, 600)
        rec['timing_tier'] = jload(proj / 'reports/timing_check.json').get('tier')
        rec['signoff_rc'] = run(['bash', FLOW / 'run_strict_signoff.sh', proj, CFG['platform']], env, log, 9000)
        for leg in ('drc', 'lvs', 'rcx'):
            j = jload(proj / 'reports' / (leg + '.json'))
            rec[leg] = {k: j.get(k) for k in ('status', 'total_violations', 'mismatch_class') if k in j}
    rec['signoff_clean'] = bool(final and (rec.get('drc') or {}).get('status') in ('clean', 'clean_beol')
                                and (rec.get('lvs') or {}).get('status') == 'clean')
    # amendment A2 primary outcome: GDS + DRC clean + timing clean/minor (LVS is environment-limited on this host)
    rec['physical_clean'] = bool(final and (rec.get('drc') or {}).get('status') in ('clean', 'clean_beol')
                                 and rec.get('timing_tier') in ('clean', 'minor'))
    sdc = (proj / 'constraints/constraint.sdc').read_text(errors='replace')
    m = re.search(r'set\s+clk_period\s+([0-9.]+)', sdc)
    rec['final_clk_period'] = float(m[1]) if m else None
    rec['backend_runs'] = len([d for d in (proj / 'backend').glob('RUN_*') if d.is_dir()])
    rec['final_config'] = (proj / 'constraints/config.mk').read_text()
    return rec


def run_engineer(proj, arm, env):
    ledger = EVAL / 'ledgers' / (proj.name + '.jsonl')
    ledger.parent.mkdir(exist_ok=True)
    log = proj / 'eval_arm.log'
    t0 = time.time()
    run(['python3', LOOP, 'add', '--ledger', ledger, '--project', proj, '--platform', CFG['platform']], env, log, 600)
    rc = run(['python3', LOOP, 'run', '--ledger', ledger, '--max', '1', '--workers', '1'], env, log, 4 * 3600)
    rec = {'loop_rc': rc, 'loop_wall_s': round(time.time() - t0)}
    last = {}
    for ln in ledger.read_text().splitlines():
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if e.get('event') != 'memory_session':
            last.update(e)
    rec['ledger_final'] = {k: last.get(k) for k in ('status', 'stage', 'reason', 'residual', 'fixes', 'note') if k in last}
    fixlog = proj / 'reports/fix_log.jsonl'
    rec['fix_rows'] = [json.loads(l) for l in fixlog.read_text().splitlines() if l.strip()] if fixlog.exists() else []
    rec.update(final_verdict(proj, env, log))
    rec['wall_s'] = round(time.time() - t0)
    return rec


def llm_key(path):
    blocks, cur = [], {}
    for line in Path(path).read_text().splitlines():
        m = re.match(r'\s*export\s+(DEEPSEEK_[A-Z_]+)=(.*)$', line)
        if not m:
            continue
        name, value = m[1], m[2].strip().strip('"').strip("'")
        if name == 'DEEPSEEK_API_KEY' and cur:
            blocks.append(cur); cur = {}
        cur[name] = value
    if cur:
        blocks.append(cur)
    chosen = [b for b in blocks if b.get('DEEPSEEK_MODEL') == LLM_MODEL_LABEL and b.get('DEEPSEEK_API_KEY')]
    assert len(chosen) == 1
    return chosen[0]['DEEPSEEK_API_KEY']


def observation(proj):
    """Failure evidence the LLM sees: stage, first ORFS errors, signoff summaries, timing tier, current knobs."""
    logs = sorted((proj / 'backend').rglob('flow.log'), key=lambda p: p.stat().st_mtime)
    errs = []
    if logs:
        lines = logs[-1].read_text(errors='replace').splitlines()
        errors = [l for l in lines if re.match(r'\[ERROR [A-Z]+-\d+\]', l)][:8]
        warns = list(dict.fromkeys(l for l in lines if re.match(r'\[WARNING [A-Z]+-\d+\]', l)))[:6]
        errs = errors + warns  # errors first, then distinct warnings
    ppa = jload(proj / 'reports/ppa.json')
    obs = {'orfs_fail_stage': ppa.get('orfs_fail_stage'), 'orfs_status': ppa.get('orfs_status'), 'tool_messages': errs,
           'timing_tier': jload(proj / 'reports/timing_check.json').get('tier')}
    for leg in ('drc', 'lvs'):
        j = jload(proj / 'reports' / (leg + '.json'))
        if j:
            obs[leg] = {k: j.get(k) for k in ('status', 'total_violations', 'mismatch_class', 'categories', 'by_rule') if k in j}
    knobs = {}
    for ln in (proj / 'constraints/config.mk').read_text().splitlines():
        m = re.match(r'\s*export\s+([A-Z_0-9]+)\s*=\s*(.*)$', ln)
        if m and m[1] not in ('VERILOG_FILES', 'SDC_FILE', 'POST_GLOBAL_PLACE_TCL'):
            knobs[m[1]] = m[2].strip()
    obs['config_knobs'] = knobs
    return obs


LLM_SYSTEM = ('You are a physical-design engineer closing an OpenROAD-flow-scripts (ORFS) run on the {platform} platform. '
              'You cannot run tools. Given the failure evidence and the current config.mk knobs, propose ONE set of '
              'config.mk knob changes that should let the flow reach a signoff-clean layout (DRC clean, LVS clean) '
              'without changing the clock period. Answer with exactly one json object: {"set": {"KNOB": "value", ...}, '
              '"unset": ["KNOB", ...], "rerun_from": "synth|floorplan|place|cts|route", "rationale": "<one sentence>"}. '
              'Allowed knobs: ' + ', '.join(sorted(KNOBS)) + '. PLACE_DENSITY_LB_ADDON must be at least 0.10. '
              'DIE_AREA/CORE_AREA are "x0 y0 x1 y1" in microns; use either DIE_AREA+CORE_AREA or CORE_UTILIZATION, not both.')


def llm_call(key, messages, out_prefix, backoff=()):
    body = json.dumps({'model': LLM_API_MODEL, 'temperature': 0.3, 'max_tokens': 800, 'stream': False,
                       'thinking': {'type': 'disabled'}, 'response_format': {'type': 'json_object'},
                       'messages': messages}).encode()
    for attempt in range(len(backoff) + 1 if backoff else 3):  # transport retries only (H-A4: with backoff)
        req = urllib.request.Request('https://api.deepseek.com/chat/completions', data=body, method='POST',
                                     headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                raw, status = r.read(), r.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read() or str(exc).encode(), exc.code
        except Exception as exc:
            raw, status = str(exc).encode(), -1
        Path(str(out_prefix) + '.try%d.request.json' % attempt).write_bytes(body)
        Path(str(out_prefix) + '.try%d.response.json' % attempt).write_bytes(raw)
        if status != -1 and status < 500:
            break
        if attempt < len(backoff):
            time.sleep(backoff[attempt])
    try:
        data = json.loads(raw)
        return data['choices'][0]['message']['content'], data.get('usage', {})
    except Exception:
        return None, {}


def apply_llm(proj, content):
    """Apply a whitelisted knob proposal to config.mk; returns (rerun_from, applied) or (None, reason)."""
    try:
        obj = json.loads(content)
    except Exception:
        return None, 'malformed_json'
    sets, unsets = obj.get('set') or {}, obj.get('unset') or []
    if not isinstance(sets, dict) or not isinstance(unsets, list):
        return None, 'malformed_proposal'
    bad = [k for k in list(sets) + list(unsets) if k not in KNOBS]
    if bad:
        return None, 'non_whitelisted:' + ','.join(bad)
    for k, v in sets.items():
        rng = KNOBS[k]
        if rng is not None:
            try:
                x = float(v)
            except (TypeError, ValueError):
                return None, 'non_numeric:' + k
            if not rng[0] <= x <= rng[1]:
                return None, 'out_of_range:%s=%s' % (k, v)
    cfg = proj / 'constraints/config.mk'
    text = cfg.read_text()
    for k in list(sets) + list(unsets):
        text = re.sub(r'^export %s\s*=.*\n' % k, '', text, flags=re.M)
    text += ''.join('export %s = %s\n' % (k, v) for k, v in sets.items())
    cfg.write_text(text)
    rf = obj.get('rerun_from') if obj.get('rerun_from') in STAGES else 'floorplan'
    return rf, {'set': sets, 'unset': unsets, 'rerun_from': rf, 'rationale': obj.get('rationale')}


# ---------------------------------------------------------------- task helpers


def _cfg_dict(path):
    out = {}
    for ln in Path(path).read_text(errors='ignore').splitlines():
        m = re.match(r'\s*export\s+(\w+)\s*[?:]?=\s*(.*?)\s*$', ln)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _task_failure(qproj):
    """(check, violation_class, before_count) of a qualified failure, from its frozen reports."""
    ppa, drc = jload(qproj / 'reports/ppa.json'), jload(qproj / 'reports/drc.json')
    stage = ppa.get('orfs_fail_stage') or ((ppa.get('summary') or {}).get('orfs_fail_stage'))
    runs = sorted((qproj / 'backend').glob('RUN_*'), key=lambda d: d.stat().st_mtime)
    if runs:
        rows = [json.loads(x) for x in (runs[-1] / 'stage_log.jsonl').read_text().splitlines() if x.strip()] \
            if (runs[-1] / 'stage_log.jsonl').exists() else []
        if rows and rows[-1].get('status') not in (0, '0', 'pass'):
            stage = rows[-1].get('stage')
    if stage:
        return 'orfs_stage', stage, 1
    cats = drc.get('categories') or {}
    if drc.get('status') == 'fail' and cats:
        return 'drc', max(cats, key=lambda k: cats[k].get('count') or 0), drc.get('total_violations')
    return 'physical', 'timing_or_drc', 1


# ---------------------------------------------------------------- Phase D2: batch with write-back vs frozen control
D2_ARMS = ('M', 'C')       # M: evolving memory (write-back); C: frozen copy of the same starting store
F_ARMS = ('N', 'N2', 'M', 'Mp', 'G')  # Phase F: memoryless, determinism repeat, memory, memory + unblockers;
# Phase G: G = Mp + component pool + composition
H_ARMS = ('NL', 'SL')  # Phase H stage 2: no memory + LLM; simplified memory (frozen store SL-<lane>) + LLM
LAYOUT_BINARIES = ('*.odb', '*.gds', '*.gds.gz', '*.def', '*.spef', '*.v', '*.lef')  # never read by any analysis
D2_ORDER_SEED = '20261001'  # preregistered task-order seed (card D-A2 / D2 section)
D2_PATHS = ('r2g-skills', 'memory', 'tools', 'CLAUDE.md')


def _copy_store(src_dir, dst_dir):
    """Single-file copy of a TEHM store through the SQLite backup API (no WAL/SHM sidecars), plus its artifacts."""
    import sqlite3
    dst_dir.mkdir(parents=True)
    s = sqlite3.connect('file:%s?mode=ro' % (src_dir / 'tehm.sqlite'), uri=True)
    d = sqlite3.connect(str(dst_dir / 'tehm.sqlite'))
    s.backup(d)
    d.execute('PRAGMA journal_mode=DELETE')
    d.close()
    s.close()
    if (src_dir / 'artifacts').exists():
        shutil.copytree(src_dir / 'artifacts', dst_dir / 'artifacts')


def d2_setup(args):
    """Fresh sandbox (D2: sandbox-d2; Phase F: sandbox-f) from `git archive HEAD` (exact code identity; refuses a dirty
    tree), base projects with paths rewritten, and per-lane TEHM stores: D2 copies the D1 seed store for M/C; Phase F
    copies the D2 M-lane stores for M and rebuilds a copy with the value-tolerant unblockers for Mp."""
    out = EVAL / CFG['sandbox']
    if args.refresh:
        # D-A5: new code + fresh starting stores; qualification projects (design_cases) stay. Everything replaced is
        # MOVED to _superseded/<tag>/ (kept on record), never deleted. Arm projects (<design>__<variant>__M|C) too.
        keep = out / '_superseded' / args.refresh
        keep.mkdir(parents=True, exist_ok=False)
        for name in ((*D2_PATHS,) if args.code_only else (*D2_PATHS, 'tehm', 'd2_setup.json')):
            if (out / name).exists():
                shutil.move(str(out / name), str(keep / name))
        for arm_dir in ([] if args.code_only else sorted((out / 'design_cases').glob('*__*__[MC]'))):
            (keep / 'design_cases').mkdir(exist_ok=True)
            shutil.move(str(arm_dir), str(keep / 'design_cases' / arm_dir.name))
    else:
        out.mkdir(parents=False, exist_ok=False)
    dirty = subprocess.run(['git', '-C', REPO, 'status', '--porcelain', '--', *D2_PATHS],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        raise SystemExit('refusing: uncommitted changes under %s:\n%s' % (D2_PATHS, dirty))
    head = subprocess.run(['git', '-C', REPO, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    arch = subprocess.run(['git', '-C', REPO, 'archive', 'HEAD', *D2_PATHS], capture_output=True, check=True).stdout
    subprocess.run(['tar', '-x', '-C', out], input=arch, check=True)
    for env_local in REPO.glob('r2g-skills/*/references/env.local.sh'):   # gitignored, machine-local
        shutil.copy(env_local, out / env_local.relative_to(REPO))
    old_cases = EVAL / 'sandbox' / 'design_cases'
    for d in (() if args.refresh else DESIGNS):
        for name in (d, d + '__sky130hd'):
            src, dst = old_cases / name, out / 'design_cases' / name
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns('backend', 'reports', '*.log', 'drc', 'lvs', 'rcx'))
            for mk in dst.rglob('*.mk'):
                mk.write_text(mk.read_text().replace(str(EVAL / 'sandbox') + '/', str(out) + '/'))
    stores = {}
    if args.code_only:                                      # Phase G: new code, evaluation records untouched
        rec = {'schema': 'r2g-memory-d2-setup-v1', 'repo_head': head, 'paths': D2_PATHS,
               'refresh': args.refresh, 'code_only': True, 'tracked': tracked_unchanged()}
        (out / ('code_refresh_%s.json' % args.refresh)).write_text(json.dumps(rec, indent=1) + '\n')
        print(json.dumps(rec, indent=1))
        return
    if CFG.get('frozen'):                                   # Phase F
        sys.path[:0] = [str(REPO / 'memory')]
        from tehm_backend import TehmMemoryBackend
        for lane in ('hd', 'hs'):
            src = EVAL / 'sandbox-d2' / 'tehm' / ('M-%s' % lane)
            _copy_store(src, out / 'tehm' / ('M-%s' % lane))
            _copy_store(src, out / 'tehm' / ('Mp-%s' % lane))
            b = TehmMemoryBackend(db_path=out / 'tehm' / ('Mp-%s' % lane) / 'tehm.sqlite',
                                  artifact_root=out / 'tehm' / ('Mp-%s' % lane) / 'artifacts')
            b.rebuild()                                      # value tolerance is the default (Phase H)
            b.close()
            for arm in ('M', 'Mp'):
                stores['%s-%s' % (arm, lane)] = sha(out / 'tehm' / ('%s-%s' % (arm, lane)) / 'tehm.sqlite')
    else:
        seed_store = EVAL / args.seed_store
        for arm in D2_ARMS:
            for lane in ('hd', 'hs'):
                dst = out / 'tehm' / ('%s-%s' % (arm, lane))
                shutil.copytree(seed_store, dst)
                stores['%s-%s' % (arm, lane)] = sha(dst / 'tehm.sqlite')
    rec = {'schema': 'r2g-memory-d2-setup-v1', 'repo_head': head, 'paths': D2_PATHS, 'refresh': args.refresh,
           'seed_store': ('sandbox-d2/tehm/M-{hd,hs} (+Mp rebuilt value-tolerant)' if CFG.get('frozen')
                          else str(EVAL / args.seed_store)), 'store_sha256': stores, 'tracked': tracked_unchanged()}
    (out / 'd2_setup.json').write_text(json.dumps(rec, indent=1, sort_keys=True) + '\n')
    print(json.dumps(rec, indent=1))


def d2_env(arm):
    if arm in ('N', 'N2', 'NL'):                            # memoryless arms (Phase F N/N2, Phase H NL)
        return base_env('none')
    store = SB / 'tehm' / ('%s-%s' % (arm, CFG['lane']))
    env = base_env('tehm')
    # Trial candidates, value tolerance and composition are memory's defaults since Phase H (the Phase F/G arms M,
    # Mp and G ran with them behind flags; their exact code is the commits recorded in those results).
    env.update(TEHM_DB=str(store / 'tehm.sqlite'), TEHM_ARTIFACTS_ROOT=str(store / 'artifacts'),
               R2G_MEMORY_READ_ONLY_EVAL='0' if arm == 'M' and not CFG.get('frozen') else '1')  # D2: only M writes
    return env


def d2_order(tasks):
    seed = CFG.get('order_seed', D2_ORDER_SEED)
    return sorted(tasks, key=lambda t: hashlib.sha1(('%s:%s/%s' % (seed, *t)).encode()).hexdigest())


def llm_assist(proj, arm, env, key, budget, situation, backoff=()):
    """LLM only after memory + catalogue left the task open. Each iteration continues from the project's current
    state; its measured outcome is logged as a fix_log row keyed per edit shape (D-A1) and, in arm M, ingested
    (write-back). Same whitelist/apply rules as the rounds-1/2 L arm."""
    log = proj / 'eval_arm.log'
    msgs = [{'role': 'system', 'content': LLM_SYSTEM.replace('{platform}', CFG['platform'])}]
    iters, last = [], None
    for i in range(1, LLM_ITERS + 1):
        if budget['calls'] >= budget['max_calls'] or budget['tokens'] >= budget['max_tokens']:
            iters.append({'iter': i, 'skipped': 'cap'}); break
        check, vclass, count = _task_failure(proj)
        sit = situation.from_project(proj, check, vclass, count)
        cfg_before = _cfg_dict(proj / 'constraints/config.mk')
        obs = observation(proj)
        msgs.append({'role': 'user', 'content': 'Failure evidence (iteration %d):\n%s\nReply with the json object only.'
                                                % (i, json.dumps(obs, indent=1))})
        content, usage = llm_call(key, msgs, proj / ('llm_assist%d' % i), backoff=backoff)
        budget['calls'] += 1
        budget['tokens'] += int(usage.get('total_tokens', 0))
        usage = {k: usage.get(k) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}   # Phase H
        if content is None:
            iters.append({'iter': i, 'result': 'call_failed', 'usage': usage}); break
        msgs.append({'role': 'assistant', 'content': content})
        rf, applied = apply_llm(proj, content)
        if rf is None:
            iters.append({'iter': i, 'result': applied, 'usage': usage}); break
        run(['bash', FLOW / 'run_orfs.sh', proj, CFG['platform']], env, log, 6000)
        run(['python3', SKILL / 'scripts/extract/extract_ppa.py', proj, proj / 'reports/ppa.json'], env, log, 600)
        v = last = final_verdict(proj, env, log)
        edits = dict(applied['set'])
        edits.update({k: None for k in applied['unset']})
        row = {'fix_session_id': 'llm_' + proj.name, 'iter': i, 'check': check, 'violation_class': vclass,
               'strategy': 'llm_edit:' + ','.join(sorted(edits)), 'from_stage': rf, 'before': 1,
               'after': 0 if v['physical_clean'] else 1,
               'verdict': 'cleared' if v['physical_clean'] else 'no_improvement',
               'config_delta': edits, 'config_before': {k: cfg_before.get(k) for k in edits},
               'situation': sit, 'llm': True}
        with (proj / 'reports/fix_log.jsonl').open('a') as fh:
            fh.write(json.dumps(row, sort_keys=True) + '\n')
        if arm == 'M':     # write-back: ingest routes to this arm's TEHM store and rebuilds the rules
            run(['python3', SKILL / 'knowledge/ingest_run.py', proj], env, log, 1800)
        iters.append({'iter': i, 'applied': applied, 'physical_clean': v['physical_clean'],
                      'signoff_clean': v['signoff_clean'], 'situation': sit, 'usage': usage})
        if v['physical_clean']:
            break
    return iters, last


_ITER_APPLY = re.compile(r'^\[(\w+)\] iter (\d+): applying (\S+)')
# '[drc] iter 1: 6 -> 4 (applied)' and the route form '[route] iter 1:  -> 0 (cleared)' (empty before). The
# streams ran with a route-only pattern (D2 result record); every analysis re-derives iterations with this one.
_ITER_RESULT = re.compile(r'^\[(\w+)\] iter (\d+):\s*(\S*)\s*-> (\S*) \(([^)]*)\)')
_ITER_RERUN_FAIL = re.compile(r'^\[(\w+)\] run_orfs failed \(rc=(\d+)\)')


def fix_iterations(log):
    """Every fix iteration of every fix_signoff session in an arm log. fix_signoff truncates reports/fix_log.jsonl
    per invocation, so the final log alone loses earlier sessions (e.g. the route fix before signoff); the arm log
    keeps them all. memory = TEHM strategy (id 'tehm_...')."""
    out = []
    try:
        lines = Path(log).read_text(errors='replace').splitlines()
    except OSError:
        return out
    for ln in lines:
        m = _ITER_APPLY.match(ln)
        if m:
            out.append({'check': m[1], 'iter': int(m[2]), 'strategy': m[3], 'memory': m[3].startswith('tehm_')})
            continue
        m = _ITER_RESULT.match(ln)
        if m and out and out[-1]['check'] == m[1] and out[-1]['iter'] == int(m[2]):
            out[-1].update(before=m[3] or None, after=m[4], verdict=m[5])
            continue
        m = _ITER_RERUN_FAIL.match(ln)
        if m and out and out[-1]['check'] == m[1] and 'verdict' not in out[-1]:
            out[-1].update(verdict='rerun_failed_rc' + m[2])
    return out


def d2_stream(args):
    """One (arm, lane) stream: the lane's frozen task list in the preregistered order, sequentially."""
    gate = json.loads(Path(args.gate).read_text())
    assert gate.get('schema') == 'r2g-memory-d2-gate-v1' and gate.get('authorized_by_user') is True
    assert gate['harness_sha256'] == sha(__file__)
    sys.path[:0] = [str(REPO / 'r2g-skills/signoff-loop/knowledge')]
    import situation
    rep2 = args.arm in H_ARMS and args.rep == 2
    assert args.rep == 1 or args.arm in H_ARMS, '--rep 2 is Phase H only'
    out = EVAL / args.out / ('%s-%s%s' % (args.arm, CFG['lane'], '-r2' if rep2 else ''))
    out.mkdir(parents=True, exist_ok=False)
    # Phase H: per-stream caps sized to each lane's task count (so no arm is cut short by a flat split)
    cap = gate.get('stream_caps', {}).get(out.name) or {'calls': gate['per_stream_max_calls'],
                                                         'tokens': gate['per_stream_max_tokens']}
    key = llm_key(args.credentials) if cap['calls'] > 0 else None   # no LLM: never read the key
    budget = {'calls': 0, 'tokens': 0, 'max_calls': cap['calls'], 'max_tokens': cap['tokens']}
    tasks = d2_order(tasks_from_qualification())
    if args.tasks:                                          # frozen task list (amendment)
        wanted = [tuple(t.split('/')) for t in args.tasks.split(',')]
        assert all(w in tasks for w in wanted), 'task not in qualification failures'
        tasks = [t for t in tasks if t in wanted]
    tasks = tasks[:args.limit or None]
    env = d2_env(args.arm)
    for n, (d, v) in enumerate(tasks):
        qp = next(Path(r['project']) for r in json.loads((EVAL / CFG['qual'] / 'summary.json').read_text())['records']
                  if r['design'] == d and r['variant'] == v)
        check, vclass, count = _task_failure(qp)
        rec = {'design': d, 'variant': v, 'arm': args.arm, 'lane': CFG['lane'], 'order': n,
               'task_situation': situation.from_project(qp, check, vclass, count)}
        t0 = time.time()
        if rep2:
            # Phase H repeat: flows are deterministic (Phase F N2 = N), so the pre-LLM part is rep 1's outcome; only
            # the LLM part is repeated, from rep 1's pre-LLM snapshot (card amendment H-A2).
            proj, pre = h_rep2_project(EVAL / args.out / ('%s-%s' % (args.arm, CFG['lane'])), d, v, args.arm, n)
            rec.update(pre)
        else:
            proj = arm_project(d, v, args.arm)
            rec.update(run_engineer(proj, args.arm, env=env))
            rec['closed_by'] = 'memory_or_catalogue' if rec.get('physical_clean') else None
            if args.arm in H_ARMS and not rec.get('physical_clean'):
                h_snapshot(proj)
        calls0, tokens0 = budget['calls'], budget['tokens']
        if not rec.get('physical_clean') and key and proj is not None:
            rec['llm_assist'], last = llm_assist(proj, args.arm, env, key, budget, situation)
            if last:                       # the newest run's verdict (already judged in the last iteration)
                rec.update(last)
            if rec.get('physical_clean'):
                rec['closed_by'] = 'llm'
        rec['llm_calls'] = budget['calls'] - calls0
        rec['llm_tokens'] = budget['tokens'] - tokens0
        if proj is None:                                   # rep 2 of a task memory/catalogue closed in rep 1
            (out / ('%02d__%s__%s.json' % (n, d, v))).write_text(json.dumps(rec, indent=1, sort_keys=True) + '\n')
            print(json.dumps({'order': n, 'design': d, 'variant': v, 'arm': args.arm, 'reused_from_rep1': True,
                              'closed_by': rec.get('closed_by')}), flush=True)
            continue
        fixlog = proj / 'reports/fix_log.jsonl'
        rec['fix_rows'] = [json.loads(l) for l in fixlog.read_text().splitlines() if l.strip()] if fixlog.exists() else []
        rec['fix_iterations'] = fix_iterations(proj / 'eval_arm.log')
        rec['memory_applied'] = ([i for i in rec['fix_iterations'] if i['memory']] +
                                 [r for r in rec['fix_rows'] if r.get('memory_rule')
                                  and str(r.get('fix_session_id', '')).startswith('memfix_')])  # B6 hook rows
        rec['total_wall_s'] = round(time.time() - t0)
        (out / ('%02d__%s__%s.json' % (n, d, v))).write_text(json.dumps(rec, indent=1, sort_keys=True) + '\n')
        if args.arm in H_ARMS:
            h_prune(proj)                                  # disk: layout binaries only, after the record is written
        print(json.dumps({k: rec.get(k) for k in ('order', 'design', 'variant', 'arm', 'physical_clean', 'closed_by',
                                                  'llm_calls', 'backend_runs', 'total_wall_s')}), flush=True)
    store = SB / 'tehm' / ('%s-%s' % (args.arm, CFG['lane'])) / 'tehm.sqlite'
    (out / 'stream.json').write_text(json.dumps({'tasks': tasks, 'llm_calls': budget['calls'],
                                                 'llm_tokens': budget['tokens'], 'tracked': tracked_unchanged(),
                                                 'store_sha256_after': sha(store) if store.exists() else None},  # memoryless arms: no store
                                                indent=1) + '\n')


# ---------------------------------------------------------------- Phase H: stage 2 helpers
def h_snapshot(proj):
    """Rep 1 of a Phase H arm: keep the pre-LLM state (config, reports, logs; no layout binaries) for rep 2."""
    snap = CASES / (proj.name + '__prellm')
    if snap.exists():
        shutil.rmtree(snap)
    shutil.copytree(proj, snap, ignore=shutil.ignore_patterns(*LAYOUT_BINARIES))   # copy2 keeps mtimes


def h_rep2_project(rep1_dir, design, variant, arm, order):
    """Rep 2: (project restored from rep 1's pre-LLM snapshot, rep 1's pre-LLM record fields), or (None, fields)
    when memory/catalogue closed the task in rep 1 (deterministic: nothing to repeat)."""
    rep1 = json.loads((rep1_dir / ('%02d__%s__%s.json' % (order, design, variant))).read_text())
    keep = ('loop_rc', 'loop_wall_s', 'ledger_final', 'backend_runs', 'fix_iterations', 'memory_applied')
    pre = {k: rep1.get(k) for k in keep if k in rep1}
    pre['rep1_closed_by'] = rep1.get('closed_by')
    if rep1.get('closed_by') == 'memory_or_catalogue':
        pre.update(physical_clean=True, signoff_clean=rep1.get('signoff_clean'), closed_by='memory_or_catalogue',
                   reused_from_rep1=True)
        return None, pre
    src = CASES / ('%s__%s__%s' % (design, variant, arm))
    snap = CASES / (src.name + '__prellm')
    dst = CASES / ('%s__%s__%sr2' % (design, variant, arm))
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(snap, dst)
    for mk in (dst / 'constraints').rglob('*.mk'):
        mk.write_text(mk.read_text().replace(str(src) + '/', str(dst) + '/'))
    pre.update(physical_clean=False, closed_by=None, reused_from_rep1=True)
    return dst, pre


def h_prune(proj):
    """Remove layout binaries (LAYOUT_BINARIES) of a finished Phase H task; logs, reports and configs stay."""
    freed = 0
    for pat in LAYOUT_BINARIES:
        for f in (proj / 'backend').rglob(pat):
            if f.is_file():
                freed += f.stat().st_size
                f.unlink()
    return freed


H_TASK_CAP = 20   # worst case 20 tasks x 2 arms x 2 reps x 3 LLM iterations = 240 <= 250 calls (card H4)


def h_select(args):
    """Phase H frozen task set (amendment H-A2), from both lanes' qualification failures:
    - excluded: layout + DRC clean, failing only because timing is unconstrained (clock/SDC: out of scope);
    - one variant per (lane, design, error code), picked by sha1(order_seed + design + variant);
    - at most H_TASK_CAP strata, kept in sha1(order_seed + stratum) order.
    Writes EVAL/h-tasks.json (refuses to overwrite)."""
    sys.path[:0] = [str(REPO / 'r2g-skills/signoff-loop/knowledge')]
    import situation
    dst = EVAL / 'h-tasks.json'
    assert not dst.exists(), 'h-tasks.json is frozen'
    strata, excluded = {}, []
    for rnd in (7, 8):
        CFG.clear(); CFG.update(ROUNDS[rnd])
        recs = {(r['design'], r['variant']): r for r in json.loads((EVAL / CFG['qual'] / 'summary.json').read_text())['records']}
        for d, v in tasks_from_qualification():
            r = recs[(d, v)]
            if r.get('final_layout') and (r.get('drc') or {}).get('status') in ('clean', 'clean_beol') \
                    and r.get('timing_tier') == 'unconstrained':
                excluded.append({'lane': CFG['lane'], 'task': '%s/%s' % (d, v), 'reason': 'timing unconstrained (SDC)'})
                continue
            qp = Path(r['project'])
            check, vclass, count = _task_failure(qp)
            sit = situation.from_project(qp, check, vclass, count)
            code = sit.get('error_code') or '%s:%s' % (check, vclass)
            strata.setdefault((CFG['lane'], d, code), []).append(v)
    h = lambda x: hashlib.sha1(('20261003' + x).encode()).hexdigest()
    chosen = []
    for (lane, d, code), vs in sorted(strata.items(), key=lambda kv: h('|'.join(kv[0]))):
        chosen.append({'lane': lane, 'design': d, 'error_code': code, 'variant': min(vs, key=lambda v: h(d + v)),
                       'candidates': sorted(vs)})
    rec = {'schema': 'r2g-memory-h-tasks-v1', 'harness_sha256': sha(__file__), 'cap': H_TASK_CAP,
           'strata': len(chosen), 'tasks': chosen[:H_TASK_CAP], 'dropped_by_cap': chosen[H_TASK_CAP:],
           'excluded': excluded}
    dst.write_text(json.dumps(rec, indent=1, sort_keys=True) + '\n')
    print(json.dumps(rec, indent=1, sort_keys=True))


def h_store(args):
    """Phase H: the SL-lane stores = the G-lane stores rebuilt with the current (simplified, tagged) code. Reports
    rule-lifecycle differences vs G (H-A1 verified 0 for the simplification alone)."""
    import sqlite3
    sys.path[:0] = [str(REPO / 'memory')]
    from tehm_backend import TehmMemoryBackend
    def status(db):
        c = sqlite3.connect('file:%s?mode=ro&immutable=1' % db, uri=True)
        r = {k: (v, st) for k, v, st in c.execute('SELECT r.rule_id, r.validity_status, s.status FROM tehm_rules r '
                                                    'LEFT JOIN tehm_rule_status s ON s.rule_id=r.rule_id')}
        c.close()
        return r
    rec = {'schema': 'r2g-memory-h-store-v1', 'harness_sha256': sha(__file__), 'lanes': {}}
    for lane in ('hd', 'hs'):
        src, dst = SB / 'tehm' / ('G-%s' % lane), SB / 'tehm' / ('SL-%s' % lane)
        _copy_store(src, dst)
        b = TehmMemoryBackend(db_path=dst / 'tehm.sqlite', artifact_root=dst / 'artifacts')
        b.rebuild()
        b.close()
        a, n = status(src / 'tehm.sqlite'), status(dst / 'tehm.sqlite')
        rec['lanes'][lane] = {'rules': len(n), 'sha256': sha(dst / 'tehm.sqlite'),
                              'lifecycle_diff_vs_G': {k: (a.get(k), n.get(k)) for k in set(a) | set(n)
                                                      if a.get(k) != n.get(k)}}
    rec['tracked'] = tracked_unchanged()
    (SB / 'h_store.json').write_text(json.dumps(rec, indent=1, sort_keys=True) + '\n')
    print(json.dumps(rec, indent=1, sort_keys=True))


_MECH = re.compile(r'TEHM mechanisms: tehm_(\S+) (\S+)')
_GATES = (('knob_policy', 'dropped: knob policy'), ('exclude', 'dropped: already tried'),
          ('unfilled_knob', 'rejected: unfilled'), ('veto', 'vetoed in this situation'),
          ('regression_rollback', 'GLOBAL REGRESSION'))


def _h_attempt(rec, proj):
    """One (arm, rep, task) attempt: outcome, LLM cost, and the memory decisions with their mechanism tags."""
    log = proj / 'eval_arm.log' if proj is not None else None
    lines = log.read_text(errors='replace').splitlines() if log and log.exists() else []
    tags = {}
    for ln in lines:
        m = _MECH.search(ln)
        if m:
            tags[m.group(1).replace('tehm_rule:', '')] = m.group(2).split(',')
    ev = _arm_events(log) if log else {'applied': [], 'b6': []}
    decisions = [{'id': rid, 'path': path, 'mechanisms': tags.get(rid, [])}
                 for path, key in (('diagnose', 'applied'), ('b6', 'b6')) for rid in ev.get(key, [])]
    # the decisive memory action: memory closed the task and a memory action was the one that cleared
    decisive = None
    if rec.get('closed_by') == 'memory_or_catalogue' and not rec.get('reused_from_rep1'):
        clears = [i for i in rec.get('fix_iterations') or [] if i.get('verdict') == 'cleared']
        b6 = [r for r in rec.get('memory_applied') or [] if r.get('after') == 0 and r.get('memory_rule')]
        last = clears[-1] if clears else None
        source = (last.get('strategy') if last and last.get('memory') else b6[-1]['memory_rule'] if b6 else '')
        ids = _RULE_ID.findall(str(source))
        decisive = ids[0] if ids else None
    usage = [i.get('usage') or {} for i in rec.get('llm_assist') or []]
    return {'closed': bool(rec.get('physical_clean')), 'closed_by': rec.get('closed_by'),
            'llm_calls': rec.get('llm_calls') or 0, 'llm_tokens': rec.get('llm_tokens') or 0,
            'prompt_tokens': sum(u.get('prompt_tokens') or 0 for u in usage),
            'completion_tokens': sum(u.get('completion_tokens') or 0 for u in usage),
            'llm_iterations': [(i.get('iter'), i.get('result') or ('cleared' if i.get('physical_clean') else 'open'))
                               for i in rec.get('llm_assist') or []],
            'flow_runs': (rec.get('backend_runs') or 0) + sum(1 for i in rec.get('llm_assist') or [] if 'applied' in i),
            'decisions': decisions, 'decisive': decisive,
            'gates': {g: sum(pat in ln for ln in lines) for g, pat in _GATES},
            'reused_from_rep1': bool(rec.get('reused_from_rep1'))}


H_EXCLUDED_TASKS = ('i2c_master_top/u89', 'i2c_master_top/u15')   # H-A3 rule 2: setup clock defect
H_REPLACE_BACKOFF = (60, 120, 240, 480)                              # H-A4: 5 transport attempts
H_LANE_ROUND = {'hd': 7, 'hs': 8}


def _h_streams(root):
    """(arm, lane, rep, stream dir) of the 8 Phase H streams."""
    return [(a, l, r, root / ('%s-%s%s' % (a, l, '-r2' if r == 2 else '')))
            for a in H_ARMS for l in ('hd', 'hs') for r in (1, 2)]


def _infra_affected(rec):
    return any(i.get('result') == 'call_failed' for i in rec.get('llm_assist') or [])


def h_replace(args):
    """H-A4: one replacement attempt per infra-affected attempt -- only the LLM part, from that rep's pre-LLM
    snapshot, with a longer transport backoff, within the gate's remaining budget. Writes <stream>-rp/ records."""
    gate = json.loads(Path(args.gate).read_text())
    assert gate.get('schema') == 'r2g-memory-d2-gate-v1' and gate.get('authorized_by_user') is True
    assert gate['harness_sha256'] == sha(__file__)
    sys.path[:0] = [str(REPO / 'r2g-skills/signoff-loop/knowledge')]
    import situation
    root = EVAL / args.out
    key = llm_key(args.credentials)
    budget = {'calls': 0, 'tokens': 0, 'max_calls': gate['replace_caps']['calls'],
              'max_tokens': gate['replace_caps']['tokens']}
    done = []
    for arm, lane, rep_, d_ in _h_streams(root):
        CFG.clear(); CFG.update(ROUNDS[H_LANE_ROUND[lane]])
        for f in sorted(d_.glob('[0-9][0-9]__*.json')):
            rec = json.loads(f.read_text())
            task = '%s/%s' % (rec['design'], rec['variant'])
            if not _infra_affected(rec) or task in H_EXCLUDED_TASKS:
                continue
            out = root / (d_.name + '-rp')
            out.mkdir(exist_ok=True)
            if (out / f.name).exists():
                continue                                    # resumable
            src = CASES / ('%s__%s__%s' % (rec['design'], rec['variant'], arm))
            dst = CASES / ('%s__%s__%srp%d' % (rec['design'], rec['variant'], arm, rep_))
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(CASES / (src.name + '__prellm'), dst)
            for mk in (dst / 'constraints').rglob('*.mk'):
                mk.write_text(mk.read_text().replace(str(src) + '/', str(dst) + '/'))
            new = {k: v for k, v in rec.items() if k not in ('llm_assist', 'llm_calls', 'llm_tokens', 'fix_rows',
                                                             'physical_clean', 'signoff_clean', 'closed_by')}
            new.update(physical_clean=False, closed_by=None, replaces=str(f.relative_to(EVAL)), replacement=True)
            calls0, tokens0, t0 = budget['calls'], budget['tokens'], time.time()
            new['llm_assist'], last = llm_assist(dst, arm, d2_env(arm), key, budget, situation,
                                                 backoff=H_REPLACE_BACKOFF)
            if last:
                new.update(last)
            new['closed_by'] = 'llm' if new.get('physical_clean') else None
            new['llm_calls'], new['llm_tokens'] = budget['calls'] - calls0, budget['tokens'] - tokens0
            new['replacement_wall_s'] = round(time.time() - t0)
            (out / f.name).write_text(json.dumps(new, indent=1, sort_keys=True) + '\n')
            h_prune(dst)
            done.append({'stream': d_.name, 'task': task, 'closed': new.get('physical_clean'),
                         'calls': new['llm_calls'], 'infra_again': _infra_affected(new)})
            print(json.dumps(done[-1]), flush=True)
    (root / 'replace.json').write_text(json.dumps({'replacements': done, 'llm_calls': budget['calls'],
                                                   'llm_tokens': budget['tokens'],
                                                   'tracked': tracked_unchanged()}, indent=1) + '\n')


def h_analyze(args):
    """Read-only Phase H analysis (card H2/H4; amendments H-A3 infra pairing + i2c exclusion, H-A4 replacements).
    Views: primary = with replacements, infra pairs dropped; as_run = no replacements, infra pairs dropped;
    all_attempts = nothing dropped (sensitivity). Excluded (i2c) tasks are reported separately."""
    root = EVAL / args.out
    raw = {}
    for arm, lane, rep_, d_ in _h_streams(root):
        for kind, dd in (('run', d_), ('rp', root / (d_.name + '-rp'))):
            for f in sorted(dd.glob('[0-9][0-9]__*.json')):
                r = json.loads(f.read_text())
                suffix = ('rp%d' % rep_) if kind == 'rp' else ('r2' if rep_ == 2 else '')
                proj = CASES / ('%s__%s__%s%s' % (r['design'], r['variant'], arm, suffix))
                if kind == 'run' and rep_ == 2 and r.get('closed_by') == 'memory_or_catalogue':
                    proj = CASES / ('%s__%s__%s' % (r['design'], r['variant'], arm))
                a = dict(_h_attempt(r, proj if proj.exists() else None), lane=lane,
                         situation=r.get('task_situation'), infra=_infra_affected(r))
                raw[(kind, arm, rep_, '%s/%s' % (r['design'], r['variant']))] = a

    def view(replacements, drop_infra, tasks_filter):
        att = {}
        for (kind, arm, rep_, t), a in raw.items():
            if kind != 'run' or not tasks_filter(t):
                continue
            rp = raw.get(('rp', arm, rep_, t))
            att[(arm, rep_, t)] = rp if (replacements and a['infra'] and rp and not rp['infra']) else a
        if drop_infra:
            bad = {(rep_, t) for (arm, rep_, t), a in att.items() if a['infra']}
            att = {k: v for k, v in att.items() if (k[1], k[2]) not in bad}
        return att

    def arm_summary(att, arm):
        xs = [v for (a, _, _), v in att.items() if a == arm]
        closed = [x for x in xs if x['closed']]
        by_llm = [x for x in closed if x['closed_by'] == 'llm']
        calls, toks = sum(x['llm_calls'] for x in xs), sum(x['llm_tokens'] for x in xs)
        return {'attempts': len(xs), 'closed': len(closed), 'closed_without_llm': len(closed) - len(by_llm),
                'closed_by_llm': len(by_llm), 'llm_calls': calls, 'llm_tokens': toks,
                'calls_per_closure': round(calls / len(closed), 3) if closed else None,
                'tokens_per_closure': round(toks / len(closed), 1) if closed else None,
                'tokens_per_llm_fix': round(sum(x['llm_tokens'] for x in by_llm) / len(by_llm), 1) if by_llm else None,
                'calls_per_llm_fix': round(sum(x['llm_calls'] for x in by_llm) / len(by_llm), 3) if by_llm else None,
                'flow_runs': sum(x['flow_runs'] for x in xs)}

    def per_closure(x, k):            # cost per closure; spending with no closure is infinitely expensive
        return x[k] / x['closed'] if x['closed'] else (float('inf') if x[k] else 0.0)

    def evaluate(att):
        summary = {a: arm_summary(att, a) for a in H_ARMS}
        tasks = sorted({t for (_, _, t) in att})
        kept = lambda arm, t: [att[k] for k in ((arm, 1, t), (arm, 2, t)) if k in att]
        harm = [t for t in tasks if kept('NL', t) and kept('SL', t) and all(x['closed'] for x in kept('NL', t))
                and not any(x['closed'] for x in kept('SL', t))]
        gain = [t for t in tasks if kept('NL', t) and kept('SL', t) and all(x['closed'] for x in kept('SL', t))
                and not any(x['closed'] for x in kept('NL', t))]
        both_llm = []
        for t in tasks:
            nl = [x for x in kept('NL', t) if x['closed_by'] == 'llm']
            sl = [x for x in kept('SL', t) if x['closed_by'] == 'llm']
            if nl and sl:
                both_llm.append({'task': t, 'NL_tokens': sum(x['llm_tokens'] for x in nl) / len(nl),
                                 'SL_tokens': sum(x['llm_tokens'] for x in sl) / len(sl)})
        s_, n_ = summary['SL'], summary['NL']
        success = {'SL_closes_ge_NL': s_['closed'] >= n_['closed'],
                   'fewer_calls_per_closure': per_closure(s_, 'llm_calls') < per_closure(n_, 'llm_calls'),
                   'fewer_tokens_per_closure': per_closure(s_, 'llm_tokens') < per_closure(n_, 'llm_tokens'),
                   'zero_harm': not harm}
        per_task = [{'task': t, **{a: [{k: x[k] for k in ('closed', 'closed_by', 'llm_calls', 'llm_tokens',
                                                            'llm_iterations', 'infra')} if x else None
                                       for x in (att.get((a, 1, t)), att.get((a, 2, t)))] for a in H_ARMS}}
                    for t in tasks]
        return {'summary': summary, 'success': success, 'all_met': all(success.values()), 'harm': harm,
                'gain': gain, 'tokens_both_llm': both_llm, 'per_task': per_task}

    valid = lambda t: t not in H_EXCLUDED_TASKS
    primary = view(True, True, valid)
    out = {'schema': 'r2g-memory-h-analysis-v2', 'harness_sha256': sha(__file__),
           'primary': evaluate(primary), 'as_run': evaluate(view(False, True, valid)),
           'all_attempts': evaluate(view(False, False, valid)),
           'excluded_tasks': evaluate(view(False, False, lambda t: t in H_EXCLUDED_TASKS)),
           'infra_affected': sorted('%s|%s|%d|%s' % k for k, a in raw.items() if a['infra']),
           'replacements': sorted('%s|%d|%s' % k[1:] for k in raw if k[0] == 'rp')}
    # per-mechanism attribution on the primary view's SL attempts (rep-2 reuses of rep-1 closures not recounted)
    mech, gates = {}, {}
    for (a, rep_, t), x in primary.items():
        if a != 'SL' or x['reused_from_rep1']:
            continue
        nl_closed = any(primary.get(('NL', k, t), {}).get('closed') for k in (1, 2))
        for dcs in x['decisions']:
            for tag in dcs['mechanisms'] + ['path_' + dcs['path']]:
                m = mech.setdefault(tag, {'decisions': 0, 'decisive_closures': [], 'in_harmed_tasks': 0})
                m['decisions'] += 1
                if x['decisive'] == dcs['id']:
                    m['decisive_closures'].append(t)
                if nl_closed and not x['closed']:
                    m['in_harmed_tasks'] += 1
        for g, n in x['gates'].items():
            gates[g] = gates.get(g, 0) + n
    out.update(mechanisms=mech, safety_gates_fired=gates,
               attempts={'%s|%s|%d|%s' % k: v for k, v in sorted(raw.items())})
    (root / 'analysis.json').write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + '\n')
    print(json.dumps({k: out[k] for k in ('primary', 'mechanisms', 'safety_gates_fired', 'infra_affected')},
                     indent=1, default=str)[:20000])


EVIDENCE_DROP = {   # Phase H/I predicates for learner-ineligible knowledge sources (amendment I-A1)
    'llm': lambda payload: str(payload.get('strategy') or '').startswith('llm_edit'),
    'component_trial': lambda payload: payload.get('evidence_tier') == 'component_trial',
    'memory_trial': lambda payload: payload.get('memory_trial') is True}


def _derived_store(src, dst, drop=(), rebuild=False, value_tolerant=True):
    """Copy a TEHM store; mark the given knowledge sources learner-ineligible; optionally rebuild (unmeasured rows are
    made ineligible too, else they block stale-rule retirement -- Phase F) with or without V4 value tolerance."""
    import functools
    import sqlite3
    sys.path[:0] = [str(REPO / 'memory')]
    import tehm.crystallization.build_rules as br
    from tehm.crystallization.build_rules import _load_transitions
    from tehm.crystallization.validity import ValidityConfig
    from tehm_backend import TehmMemoryBackend
    _copy_store(src, dst)
    if drop:
        c = sqlite3.connect(str(dst / 'tehm.sqlite'))
        ids = [tid for tid, aj in c.execute('SELECT transition_id, action_json FROM tehm_transitions')
               if any(EVIDENCE_DROP[k](json.loads(aj).get('payload') or {}) for k in drop)]
        c.executemany('UPDATE tehm_dataset_membership SET learner_eligible=0 WHERE transition_id=?', [(t,) for t in ids])
        c.commit()
        c.close()
    if rebuild:
        b = TehmMemoryBackend(db_path=dst / 'tehm.sqlite', artifact_root=dst / 'artifacts')
        conn_b, _ = b._open()
        conn_b.executemany('UPDATE tehm_dataset_membership SET learner_eligible=0 WHERE transition_id=?',
                           [(t,) for t in _load_transitions(conn_b)[3]])
        conn_b.commit()
        saved = br.ValidityConfig
        if not value_tolerant:
            br.ValidityConfig = functools.partial(ValidityConfig, value_tolerant=False)
        try:
            b.rebuild()
        finally:
            br.ValidityConfig = saved
            b.close()
    return dst


H_ABLATIONS = ('full', 'no_trial_candidates', 'no_value_tolerance', 'no_categorical_fill', 'no_composition',
               'no_knob_subset_rules', 'retire_only', 'no_llm_evidence', 'no_component_trial_evidence')


def h_ablate(args):
    """Phase H causal check per mechanism and knowledge source (card H2), as Phase F/G: flows are deterministic, so
    re-query memory at each task's frozen pre-fix state with one mechanism or source removed. For every SL attempt
    whose closure memory decided: 'removed' = no proposal (the run follows the memoryless trajectory), 'kept' = the
    proposals up to the decisive action are unchanged, 'reordered' = the decisive action is still proposed but later,
    'decisive_lost' = the decisive action is gone and other proposals take its place (outcome needs a flow).
    Read-only on the run's stores."""
    sys.path[:0] = [str(REPO / 'memory'), str(REPO / 'r2g-skills/signoff-loop/knowledge')]
    import runtime_router
    import situation
    import severity
    import tehm.activation.instantiate as inst
    from tehm_backend import TehmMemoryBackend
    ana = json.loads((EVAL / args.out / 'analysis.json').read_text())
    decided = {}
    for key, a in ana['attempts'].items():
        kind, arm, rep_, task = key.split('|')
        if kind == 'run' and arm == 'SL' and a.get('decisive') and not a.get('reused_from_rep1'):
            decided.setdefault((a['lane'], task), a['decisive'])
    work = EVAL / (args.out + '-ablation')
    work.mkdir(exist_ok=False)
    results = {}
    for cond in H_ABLATIONS:
        for lane in ('hd', 'hs'):
            tasks = [(t, d) for (l, t), d in sorted(decided.items()) if l == lane]
            if not tasks:
                continue
            dst = _derived_store(SB / 'tehm' / ('SL-%s' % lane), work / ('%s-%s' % (cond, lane)),
                                 drop={'no_llm_evidence': ('llm',), 'no_component_trial_evidence': ('component_trial',)}.get(cond, ()),
                                 rebuild=cond in ('no_value_tolerance', 'retire_only', 'no_llm_evidence',
                                                  'no_component_trial_evidence'),
                                 value_tolerant=cond != 'no_value_tolerance')
            b = TehmMemoryBackend(db_path=dst / 'tehm.sqlite', artifact_root=dst / 'artifacts',
                                  trial_candidates=cond != 'no_trial_candidates')
            saved_cat, saved_comp = inst._resolve_categorical, runtime_router._composition_strategies
            if cond == 'no_categorical_fill':
                inst._resolve_categorical = lambda unresolved, edits, after, witnesses: (edits, unresolved, [], [])
            if cond == 'no_composition':
                runtime_router._composition_strategies = lambda *a, **k: []
            try:
                CFG.clear(); CFG.update(ROUNDS[H_LANE_ROUND[lane]])
                summ = {'%s/%s' % (r['design'], r['variant']): Path(r['project'])
                        for r in json.loads((EVAL / CFG['qual'] / 'summary.json').read_text())['records']}
                for task, decisive in tasks:
                    qp = summ[task]
                    check, vclass, count = _task_failure(qp)
                    sit = situation.from_project(qp, check, vclass, count)
                    cfg = _cfg_dict(qp / 'constraints/config.mk')
                    st = runtime_router.signoff_strategies(
                        project_dir=qp, check=check, design_id=task.split('/')[0], platform=cfg.get('PLATFORM'),
                        cfg=cfg, reports={}, situation=sit, backend=b,
                        severity=severity.from_project(qp, check, vclass))
                    if cond == 'no_knob_subset_rules':
                        st = [x for x in st if 'rule_knob_subset' not in (x.get('mechanisms') or [])]
                    entry = results.setdefault(task, {'decisive': decisive})
                    if cond == 'full' and decisive.startswith('rule_'):     # knowledge sources of the decisive rule
                        entry['decisive_sources'] = sorted({s_['kind'] for s_ in _rule_sources(
                            SB / 'tehm' / ('SL-%s' % lane) / 'tehm.sqlite', decisive, set())})
                    entry[cond] = [
                        {'id': _RULE_ID.findall(str(x.get('rule_id')))[:1], 'edits': x['config_edits'],
                         'mechanisms': x.get('mechanisms')} for x in st]
            finally:
                inst._resolve_categorical, runtime_router._composition_strategies = saved_cat, saved_comp
                b.close()
    for task, by in results.items():
        full = [x['id'] for x in by.get('full') or []]
        upto = full[:full.index([by['decisive']]) + 1] if [by['decisive']] in full else None
        for cond in H_ABLATIONS[1:]:
            props = [x['id'] for x in by.get(cond) or []]
            by[cond + '_verdict'] = ('removed' if not props else                    # memoryless trajectory
                                     'kept' if upto and props[:len(upto)] == upto else
                                     'reordered' if [by['decisive']] in props else
                                     'decisive_lost')                               # alternatives: needs a flow
        by['decisive_in_first_query'] = upto is not None
    (work / 'ablation.json').write_text(json.dumps(results, indent=1, sort_keys=True) + '\n')
    for task, by in sorted(results.items()):
        print('%-34s decisive=%s in_first_query=%s | %s' % (
            task, by['decisive'], by['decisive_in_first_query'],
            ' '.join('%s:%s' % (c, by.get(c + '_verdict')) for c in H_ABLATIONS[1:])))





H_FLOW_CONDITIONS = {'no_trial_candidates': 'ABtc', 'no_value_tolerance': 'ABvt', 'no_llm_evidence': 'ABllm',
                     'no_component_trial_evidence': 'ABct'}   # amendment H-A5: store-level conditions only


_B6 = re.compile(r'\[loop\] memory (?:rule|composition) (?:tehm_rule:)?(\S+) before escalation')


def _repair_sequence(proj, rec):
    """Ordered memory / catalogue actions of a run with their source tags and result, from the arm log."""
    b6_after = {str(r.get('memory_rule')).replace('tehm_rule:', ''): r.get('after')
                for r in rec.get('fix_rows') or [] if str(r.get('fix_session_id', '')).startswith('memfix_')}
    seq, tags, pending = [], {}, {}
    log = proj / 'eval_arm.log'
    for ln in (log.read_text(errors='replace').splitlines() if log.exists() else []):
        m = _MECH.search(ln)
        if m:
            tags[m.group(1).replace('tehm_rule:', '')] = m.group(2)
        m = _B6.search(ln)
        if m:
            seq.append({'check': 'b6', 'action': m.group(1), 'source': tags.get(m.group(1), 'memory'),
                        'result': 'cleared' if b6_after.get(m.group(1)) == 0 else 'not_cleared'})
            continue
        m = _ITER_APPLY.match(ln)
        if m:
            sid = m.group(3).replace('tehm_tehm_rule:', '').replace('tehm_', '')
            pending[m.group(1)] = (sid, tags.get(sid, 'catalogue'))
            continue
        m = _ITER_RERUN_FAIL.match(ln) or _ITER_RESULT.match(ln)
        if m and m.group(1) in pending:
            sid, src = pending.pop(m.group(1))
            result = ('rerun_failed_rc%s' % m.group(2)) if m.re is _ITER_RERUN_FAIL else m.group(5)
            seq.append({'check': m.group(1), 'action': sid, 'source': src, 'result': result})
    return seq


def _store_flow(job):
    """The SL engineer loop (memory + catalogue, no LLM) on one task with a given read-only store (H-A5, Phase I)."""
    task, label, arm, lane, store, outfile = job
    CFG.clear(); CFG.update(ROUNDS[H_LANE_ROUND[lane]])
    use_sandbox(CFG['sandbox'])
    d, v = task.split('/')
    proj = arm_project(d, v, arm)
    env = base_env('tehm')
    env.update(TEHM_DB=str(store / 'tehm.sqlite'), TEHM_ARTIFACTS_ROOT=str(store / 'artifacts'),
               R2G_MEMORY_READ_ONLY_EVAL='1')
    rec = {'task': task, 'condition': label, 'lane': lane, 'store': str(store), **run_engineer(proj, arm, env)}
    rec['fix_iterations'] = fix_iterations(proj / 'eval_arm.log')
    rec['repair_sequence'] = _repair_sequence(proj, rec)
    rec['verdict'] = 'closed' if rec.get('physical_clean') else 'not_closed'
    Path(outfile).write_text(json.dumps(rec, indent=1, sort_keys=True) + '\n')
    h_prune(proj)
    return rec


def h_ablate_flows(args):
    """Amendment H-A5: resolve the decisive_lost / reordered cells of the store-level conditions with no-LLM flows.
    Resumable; merges the flow verdicts into ablation-flows.json."""
    import sqlite3
    work = EVAL / (args.out + '-ablation')
    abl = json.loads((work / 'ablation.json').read_text())
    lane_of = {key.split('|')[3]: a['lane'] for key, a in json.loads(
        (EVAL / args.out / 'analysis.json').read_text())['attempts'].items()}
    out = work / 'flows'
    out.mkdir(exist_ok=True)
    jobs = []
    for task, by in sorted(abl.items()):
        for cond in H_FLOW_CONDITIONS:
            if by.get(cond + '_verdict') not in ('decisive_lost', 'reordered'):
                continue
            if (out / ('%s__%s.json' % (cond, task.replace('/', '__')))).exists():
                continue
            lane = lane_of[task]
            store = work / ('%s-%s' % (cond, lane))
            if cond == 'no_trial_candidates':      # store-level emulation of trial_candidates=False
                flow_store = work / ('%s-%s-flow' % (cond, lane))
                if not flow_store.exists():
                    _copy_store(store, flow_store)
                    c = sqlite3.connect(str(flow_store / 'tehm.sqlite'))
                    c.execute("UPDATE tehm_rule_status SET status='shadow' WHERE status='candidate'")
                    c.commit()
                    c.close()
                store = flow_store
            jobs.append((task, cond, H_FLOW_CONDITIONS[cond], lane, store,
                         out / ('%s__%s.json' % (cond, task.replace('/', '__')))))
    with cf.ProcessPoolExecutor(max_workers=args.jobs) as ex:
        for rec in ex.map(_store_flow, jobs):
            print(json.dumps({k: rec.get(k) for k in ('task', 'condition', 'verdict', 'backend_runs', 'wall_s')}),
                  flush=True)
    for f in sorted(out.glob('*.json')):
        rec = json.loads(f.read_text())
        abl[rec['task']][rec['condition'] + '_flow'] = rec['verdict']
    (work / 'ablation-flows.json').write_text(json.dumps(abl, indent=1, sort_keys=True) + '\n')


# ---------------------------------------------------------------- Phase I: knowledge-source isolation (no LLM)
I_CONDITIONS = {   # card r2g_memory_phaseI_contract_20261003 (+ I-A1): label -> (arm code, store builder)
    'P': ('IP', 'evaluation-h-ablation/no_llm_evidence-{lane}'),               # pool only (Phase H store, reused)
    'L': ('IL', 'evaluation-h-ablation/no_component_trial_evidence-{lane}'),   # LLM-derived rules only (reused)
    'J': ('IJ', ('llm', 'component_trial')),                                  # neither
    'J*': ('IJs', ('llm', 'component_trial', 'memory_trial'))}                # J minus remaining memory trials


def _i_tasks():
    t = json.loads((EVAL / 'h-tasks.json').read_text())['tasks']
    return [('%s/%s' % (x['design'], x['variant']), x['lane']) for x in t
            if '%s/%s' % (x['design'], x['variant']) not in H_EXCLUDED_TASKS]


def i_isolate(args):
    """Phase I flows: every valid Phase H task under P, L, J (and J* where J closes but N does not), no LLM.
    P records already produced as H-A5 flows on the same store are reused. Resumable."""
    out = EVAL / 'evaluation-i'
    (out / 'stores').mkdir(parents=True, exist_ok=True)
    conds = args.conditions.split(',')
    reuse = EVAL / 'evaluation-h-ablation' / 'flows'
    jobs = []
    for label in conds:
        arm, spec = I_CONDITIONS[label]
        (out / label).mkdir(exist_ok=True)
        targets = _i_tasks()
        if label == 'J*':                              # only where J closes and N (catalogue only) does not
            n_pre = {k.split('|')[3]: a['closed_by'] == 'memory_or_catalogue' for k, a in json.loads(
                (EVAL / 'evaluation-h' / 'analysis.json').read_text())['attempts'].items() if k.startswith('run|NL|1|')}
            j = {json.loads(f.read_text())['task']: json.loads(f.read_text())['verdict'] for f in (out / 'J').glob('*.json')}
            targets = [(t, l) for t, l in targets if j.get(t) == 'closed' and not n_pre.get(t)]
        for task, lane in targets:
            outfile = out / label / ('%s.json' % task.replace('/', '__'))
            if outfile.exists():
                continue
            if label == 'P' and (reuse / ('no_llm_evidence__%s.json' % task.replace('/', '__'))).exists():
                rec = json.loads((reuse / ('no_llm_evidence__%s.json' % task.replace('/', '__'))).read_text())
                rec.update(condition='P', reused_from='evaluation-h-ablation/flows (H-A5, same store and loop)')
                if 'repair_sequence' not in rec:
                    rec['repair_sequence'] = _repair_sequence(CASES / ('%s__%s__ABllm' % tuple(task.split('/'))), rec)
                outfile.write_text(json.dumps(rec, indent=1, sort_keys=True) + '\n')
                continue
            if isinstance(spec, str):
                store = EVAL / spec.format(lane=lane)
            else:
                store = out / 'stores' / ('%s-%s' % (arm, lane))
                if not store.exists():
                    _derived_store(SB / 'tehm' / ('SL-%s' % lane), store, drop=spec, rebuild=True)
            jobs.append((task, label, arm, lane, store, outfile))
    with cf.ProcessPoolExecutor(max_workers=args.jobs) as ex:
        for rec in ex.map(_store_flow, jobs):
            print(json.dumps({k: rec.get(k) for k in ('condition', 'task', 'verdict', 'backend_runs', 'wall_s')}), flush=True)


def i_analyze(args):
    """Phase I readings (card Q1/Q2): closures per condition, per situation, memory-decided, harm vs N, cost."""
    ana = json.loads((EVAL / 'evaluation-h' / 'analysis.json').read_text())
    att = ana['attempts']
    sit = {k.split('|')[3]: a.get('situation') or {} for k, a in att.items()}
    def code(s):
        return s.get('error_code') or ('DRC ' + str(s.get('violation_class')) if s.get('check') == 'drc' else 'route stage')
    rows = {}
    for task, lane in _i_tasks():
        nl, sl = att.get('run|NL|1|' + task, {}), att.get('run|SL|1|' + task, {})
        r = {'task': task, 'lane': lane, 'situation': code(sit.get(task, {})),
             'N': {'closed': nl.get('closed_by') == 'memory_or_catalogue', 'flow_runs': nl.get('flow_runs')},
             'S': {'closed': sl.get('closed_by') == 'memory_or_catalogue', 'flow_runs': sl.get('flow_runs'),
                   'memory_decided': bool(sl.get('decisive'))}}
        for label in I_CONDITIONS:
            f = EVAL / 'evaluation-i' / label / ('%s.json' % task.replace('/', '__'))
            if f.exists():
                x = json.loads(f.read_text())
                seq = x.get('repair_sequence') or []
                r[label] = {'closed': x['verdict'] == 'closed', 'flow_runs': x.get('backend_runs'), 'wall_s': x.get('wall_s'),
                            'memory_decided': any(a['source'] != 'catalogue' and a['result'] == 'cleared'
                                                  for a in seq),
                            'sequence': seq, 'reused': bool(x.get('reused_from'))}
        rows[task] = r
    labels = ['N', 'S', 'P', 'L', 'J', 'J*']
    summ = {}
    for lb in labels:
        cells = [r[lb] for r in rows.values() if lb in r]
        closed = [c for c in cells if c['closed']]
        summ[lb] = {'tasks': len(cells), 'closed': len(closed),
                    'memory_decided_closures': sum(1 for c in closed if c.get('memory_decided')),
                    'flow_runs': sum(c.get('flow_runs') or 0 for c in cells),
                    'flow_runs_per_closure': round(sum(c.get('flow_runs') or 0 for c in closed) / len(closed), 2) if closed else None,
                    'harm_vs_N': sorted(r['task'] for r in rows.values() if lb in r and r['N']['closed'] and not r[lb]['closed'])}
    by_sit = {}
    for r in rows.values():
        for lb in labels:
            if lb in r:
                c = by_sit.setdefault(r['situation'], {}).setdefault(lb, [0, 0])
                c[0] += 1; c[1] += r[lb]['closed']
    p_, l_ = summ['P']['closed'], summ['L']['closed']
    gap_classes = [k for k, v in by_sit.items() if v.get('L', [0, 0])[1] >= 2 and v.get('P', [0, 0])[1] == 0]
    q1 = ('pool stronger' if p_ > l_ else 'pool matches' if (p_ >= l_ - 1 and not gap_classes) else 'LLM-derived rules superior')
    out = {'schema': 'r2g-memory-i-analysis-v1', 'harness_sha256': sha(__file__), 'summary': summ, 'by_situation': by_sit,
           'Q1': {'P_closed': p_, 'L_closed': l_, 'gap_classes': gap_classes, 'reading': q1},
           'Q2': {'J_closed': summ['J']['closed'], 'N_closed': summ['N']['closed'],
                  'residual_memory_closures': sorted(t for t, r in rows.items() if 'J' in r and r['J']['closed'] and not r['N']['closed'])},
           'rows': rows}
    (EVAL / 'evaluation-i' / 'analysis.json').write_text(json.dumps(out, indent=1, sort_keys=True) + '\n')
    print(json.dumps({k: out[k] for k in ('summary', 'by_situation', 'Q1', 'Q2')}, indent=1))


# ---------------------------------------------------------------- arm-log parsing and rule knowledge sources
_RULE_ID = re.compile(r'(?:rule|compose)_[0-9a-f]{8,}')


def _arm_events(log):
    """Memory decisions in one arm log: diagnose rule applications, B6 hook applications, vetoes, rejections."""
    ev = {'applied': [], 'b6': [], 'vetoed': [], 'rejected': []}
    try:
        lines = Path(log).read_text(errors='replace').splitlines()
    except OSError:
        return ev
    for ln in lines:
        if ln.startswith('[') and ' applying tehm_' in ln:
            ev['applied'] += _RULE_ID.findall(ln)[:1]
        elif ('memory rule' in ln or 'memory composition' in ln) and 'before escalation' in ln:
            ev['b6'] += _RULE_ID.findall(ln)[:1]
        elif 'vetoed in this situation' in ln:
            ev['vetoed'].append(ln.strip()[:160])
        elif 'TEHM strategy' in ln and ('rejected:' in ln or 'dropped:' in ln):
            ev['rejected'].append(ln.strip()[:160])
    return ev


def _rule_sources(store_db, rule_id, seed_ids):
    """Knowledge sources of a rule: its crystallisation witnesses -> transitions -> {llm, r2g_live, memory_trial,
    component_trial, backfill} x {seed (rounds 1-2), d2_writeback}."""
    import sqlite3
    c = sqlite3.connect('file:%s?mode=ro&immutable=1' % store_db, uri=True)
    out = []
    for (raw,) in c.execute("SELECT source_substitution_json FROM tehm_rule_sources WHERE rule_id=?", (rule_id,)):
        for tid in (json.loads(raw) or {}):
            row = c.execute("SELECT action_json FROM tehm_transitions WHERE transition_id=?", (tid,)).fetchone()
            payload = (json.loads(row[0]).get('payload') or {}) if row else {}
            strat = str(payload.get('strategy') or '')
            kind = ('memory_trial' if payload.get('memory_trial') else
                    'component_trial' if payload.get('evidence_tier') == 'component_trial' else
                    'backfill' if payload.get('evidence_tier') == 'backfill' else
                    'llm' if strat.startswith('llm_edit') else 'r2g_live')
            out.append({'transition': tid, 'strategy': strat, 'kind': kind,
                        'origin': 'seed' if tid in seed_ids else 'd2_writeback'})
    c.close()
    return out


# ---------------------------------------------------------------- Phase G1: hierarchical component trials (no LLM)
# Training-side variants only (none of the 17 Phase F evaluation tasks). Each dimension's sweep runs on top of the
# best values kept so far; a value is kept only if it lowers the graded severity (knowledge/severity.py).
G_PLAN = {
    'flw_hd': {'round': 3, 'stages': 'synth floorplan place', 'check': 'orfs_stage', 'stage': 'place',
               'designs': [('sha256_core', 'u90'), ('chacha_core', 'u90'), ('des_area', 'u90')],
               'steps': [('CORE_UTILIZATION', 'delta', [-20, -30, -40]), ('PLACE_DENSITY_LB_ADDON', 'abs', [0.1])]},
    'grt_hd': {'round': 3, 'stages': 'synth floorplan place cts route', 'check': 'orfs_stage', 'stage': 'route',
               'designs': [('sha256_core', 'u75'), ('chacha_core', 'u75')],
               'steps': [('CORE_UTILIZATION', 'delta', [-25, -35]), ('GPL_ROUTABILITY_DRIVEN', 'abs', [1]),
                         ('ROUTING_LAYER_ADJUSTMENT', 'abs', [0.1, 0.2])]},
    'grt_hs': {'round': 4, 'stages': 'synth floorplan place cts route', 'check': 'orfs_stage', 'stage': 'route',
               'designs': [('chacha_core', 'u10'), ('chacha_core', 'u14'), ('chacha_core', 'u20')],
               'steps': [('CORE_UTILIZATION', 'delta', [15, 25]), ('GPL_ROUTABILITY_DRIVEN', 'abs', [1]),
                         ('ROUTING_LAYER_ADJUSTMENT', 'abs', [0.1, 0.2])]},
    'li3_hs': {'round': 4, 'stages': None, 'check': 'drc', 'stage': None,
               'designs': [('sha256_core', 'u10'), ('sha256_core', 'u14'), ('sha256_core', 'u20')],
               'steps': [('CORE_UTILIZATION', 'delta', [8, 16]), ('PLACE_DENSITY_LB_ADDON', 'abs', [0.1]),
                         ('CELL_PAD_IN_SITES_DETAIL_PLACEMENT', 'abs', [2])]},
}


def _g_trial(sit_key, design, variant, k, edits, plan):
    """One stage-scoped trial: a fresh project for (design, variant) with ``edits`` applied, run to the plan's
    stage (li.3: full flow + DRC). Returns (severity_after, stage_ok, project)."""
    sys.path[:0] = [str(SKILL / 'knowledge')]
    import severity
    proj = make_variant(design, variant)
    trial = CASES / ('g_%s__%s__%s__%02d' % (sit_key, design, variant, k))
    if trial.exists():
        shutil.rmtree(trial)
    shutil.move(str(proj), str(trial))
    cfg = trial / 'constraints/config.mk'
    text = cfg.read_text().replace(str(proj) + '/', str(trial) + '/')
    for knob in edits:
        text = re.sub(r'^export %s\s*[?:]?=.*\n' % knob, '', text, flags=re.M)
    text += ''.join('export %s = %s\n' % (kk, vv) for kk, vv in edits.items())
    cfg.write_text(text)
    env = base_env('none')
    if plan['stages']:
        env['ORFS_STAGES'] = plan['stages']
    log = trial / 'eval_trial.log'
    rc = run(['bash', FLOW / 'run_orfs.sh', trial, CFG['platform']], env, log, 6000)
    if plan['check'] == 'drc':
        if rc != 0:                       # full flow: here the exit code IS the completion signal
            return None, False, trial
        run(['bash', FLOW / 'run_drc.sh', trial, CFG['platform'], trial.name], env, log, 9000)
        runs = sorted((trial / 'backend').glob('RUN_*'), key=lambda d: d.stat().st_mtime)
        run(['python3', SKILL / 'scripts/extract/extract_drc.py', trial, trial / 'reports/drc.json', '--run-dir',
             runs[-1]], env, log, 1200)
        sev = severity.from_project(trial, 'drc', 'li.3')
        return sev, sev == 0, trial
    sev = severity.from_project(trial, plan['check'], plan['stage'])
    return sev, _stages_ok(trial, plan['stages']), trial


def _stages_ok(proj, stages):
    """Every requested stage passed in the newest run's stage_log. A stage-scoped run_orfs.sh exits 1 at the end
    (no final GDS) even when all requested stages passed, so the exit code cannot judge a trial."""
    runs = sorted((proj / 'backend').glob('RUN_*'), key=lambda d: d.stat().st_mtime)
    if not runs or not (runs[-1] / 'stage_log.jsonl').exists():
        return False
    status = {}
    for ln in (runs[-1] / 'stage_log.jsonl').read_text().splitlines():
        if ln.strip():
            r = json.loads(ln)
            status[r.get('stage')] = r.get('status')
    return all(status.get(st) in (0, '0', 'pass') for st in stages.split())


def _g_job(sit_key, design, variant):
    """Hierarchical sweep for one (situation, design); returns the trial rows (fix_log form)."""
    sys.path[:0] = [str(SKILL / 'knowledge')]
    import situation
    import severity
    plan = G_PLAN[sit_key]
    CFG.update(ROUNDS[plan['round']])
    qual = {(r['design'], r['variant']): Path(r['project'])
            for r in json.loads((EVAL / CFG['qual'] / 'summary.json').read_text())['records']}[(design, variant)]
    qcfg = _cfg_dict(qual / 'constraints/config.mk')
    check, vclass, count = _task_failure(qual)
    sit = situation.from_project(qual, check, vclass, count)
    base_sev = severity.from_project(qual, plan['check'], plan['stage'] or vclass)
    kept, cur_sev, rows, k = {}, base_sev, [], 0
    for knob, mode, values in plan['steps']:
        best = None
        for val in values:
            k += 1
            v = (float(qcfg.get(knob, 0) or 0) + val) if mode == 'delta' else val
            v = '%g' % max(5.0, min(90.0, v)) if knob == 'CORE_UTILIZATION' else '%g' % v
            edits = {**kept, knob: v}
            t0 = time.time()
            sev, ok, trial = _g_trial(sit_key, design, variant, k, edits, plan)
            before_cfg = {kk: qcfg.get(kk) for kk in edits}
            measured = sev is not None and base_sev is not None
            verdict = ('cleared' if ok or (measured and sev <= 0) else
                       'applied' if measured and sev < base_sev else
                       'regression' if measured and sev > base_sev else 'no_improvement')
            rows.append({'fix_session_id': 'gtrial_%s_%s_%s' % (sit_key, design, variant), 'iter': k,
                         'check': check, 'violation_class': vclass,
                         'strategy': 'component:' + '+'.join(sorted(edits)), 'from_stage': 'floorplan',
                         'before': base_sev if plan['check'] == 'drc' else 1,
                         'after': (sev if plan['check'] == 'drc' else (0 if ok else 1)),
                         'verdict': verdict if measured or ok else 'rerun_failed_rc1',
                         'config_delta': edits, 'config_before': before_cfg, 'situation': sit,
                         'severity_before': base_sev, 'severity_after': sev, 'evidence_tier': 'component_trial',
                         'provenance': 'component_trial:%s' % sit_key, 'trial_step': knob, 'trial_value': v,
                         'stage_ok': ok, 'wall_s': round(time.time() - t0), 'trial_project': trial.name})
            print(json.dumps({'sit': sit_key, 'design': design, 'variant': variant, 'k': k, 'edits': edits,
                              'severity': sev, 'base': base_sev, 'ok': ok}), flush=True)
            if sev is not None and (best is None or sev < best[1]):
                best = (v, sev)
        ref = cur_sev if cur_sev is not None else float('inf')
        if best is not None and best[1] < ref:             # keep a value only if it lowers severity
            kept[knob], cur_sev = best[0], best[1]
    return rows


def _g_job_write(arg):
    out, job = arg
    rows = _g_job(*job)
    (out / ('%s__%s__%s.json' % job)).write_text(json.dumps(rows, indent=1, sort_keys=True) + '\n')
    return rows


def g_store(args):
    """G store per lane = copy of the Mp store + the captured G1 trial rows, rebuilt value-tolerant (with the
    retirement-guard fix). Reports every rule whose lifecycle differs from Mp, so the arm-G effect stays
    attributable."""
    import sqlite3
    sys.path[:0] = [str(REPO / 'memory')]
    from tehm.adapters.r2g_evidence import capture_rows
    from tehm_backend import TehmMemoryBackend
    lane_of = {'flw_hd': 'hd', 'grt_hd': 'hd', 'grt_hs': 'hs', 'li3_hs': 'hs'}
    rows = {'hd': [], 'hs': []}
    for f in sorted((EVAL / args.trials).glob('*__*__*.json')):
        sit_key, design, variant = f.stem.split('__')
        for r in json.loads(f.read_text()):
            rows[lane_of[sit_key]].append({**r, 'design': 'g_%s_%s_%s' % (sit_key, design, variant),
                                           'platform': 'sky130hd' if lane_of[sit_key] == 'hd' else 'sky130hs'})
    report = {}
    for lane in ('hd', 'hs'):
        dst = SB / 'tehm' / ('G-%s' % lane)
        if dst.exists():
            raise SystemExit('refusing to overwrite %s' % dst)
        _copy_store(SB / 'tehm' / ('Mp-%s' % lane), dst)
        b = TehmMemoryBackend(db_path=dst / 'tehm.sqlite', artifact_root=dst / 'artifacts')
        conn, store = b._open()
        n = capture_rows(conn, store, rows[lane])
        b.rebuild()
        b.close()
        def statuses(db):
            c = sqlite3.connect('file:%s?mode=ro&immutable=1' % db, uri=True)
            out = dict(c.execute('SELECT rule_id, status FROM tehm_rule_status').fetchall())
            c.close()
            return out
        mp, g = statuses(SB / 'tehm' / ('Mp-%s' % lane) / 'tehm.sqlite'), statuses(dst / 'tehm.sqlite')
        report[lane] = {'trial_rows': len(rows[lane]), 'captured': n,
                        'changed_vs_Mp': {r: (mp.get(r), g.get(r)) for r in sorted(set(mp) | set(g))
                                          if mp.get(r) != g.get(r)},
                        'store_sha256': sha(dst / 'tehm.sqlite')}
    (SB / ('g_store_%s.json' % args.trials)).write_text(json.dumps(report, indent=1, sort_keys=True) + '\n')
    print(json.dumps(report, indent=1, sort_keys=True))


def g_trials(args):
    out = EVAL / args.out
    out.mkdir(parents=False, exist_ok=False)
    before = tracked_unchanged()
    jobs = [(sk, d, v) for sk, plan in G_PLAN.items() for d, v in plan['designs']
            if not args.only or sk in args.only.split(',')]
    # Processes, not threads: each job sets its own round in the module-level CFG.
    with cf.ProcessPoolExecutor(max_workers=args.jobs) as ex:
        allrows = [r for rows in ex.map(_g_job_write, [(out, j) for j in jobs]) for r in rows]
    (out / 'trials.json').write_text(json.dumps({'schema': 'r2g-memory-g1-v1', 'harness_sha256': sha(__file__),
                                                  'trials': len(allrows), 'tracked_before': before,
                                                  'tracked_after': tracked_unchanged()}, indent=1) + '\n')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    q = sub.add_parser('qualify')
    q.add_argument('--variants', default='')
    q.add_argument('--round', type=int, default=1)
    q.add_argument('--designs', default='', help='comma-separated subset (A5 re-runs)')
    q.add_argument('--jobs', type=int, default=6)
    ds = sub.add_parser('d2-setup', help='Phase D2: fresh sandbox-d2 from git archive + per-lane M/C stores')
    ds.add_argument('--seed-store', default='seed-d1d/store_all')
    ds.add_argument('--round', type=int, default=3)
    ds.add_argument('--refresh', default='', help='tag: refresh code + stores in place, superseded parts kept (D-A5)')
    ds.add_argument('--code-only', action='store_true', help='with --refresh: replace only the code trees (Phase G)')
    st = sub.add_parser('d2-stream', help='Phase D2: one (arm, lane) stream, sequential, preregistered order')
    st.add_argument('--arm', choices=D2_ARMS + F_ARMS + H_ARMS, required=True)
    st.add_argument('--rep', type=int, choices=(1, 2), default=1, help='Phase H: repeat index (2 = LLM part only)')
    st.add_argument('--round', type=int, choices=(3, 4, 5, 6, 7, 8), required=True)
    st.add_argument('--tasks', default='', help='frozen design/variant list (comma-separated)')
    st.add_argument('--gate', required=True)
    st.add_argument('--credentials', required=True)
    st.add_argument('--out', default='evaluation-d2')
    st.add_argument('--limit', type=int, default=0, help='first N tasks only (smoke run)')
    hsel = sub.add_parser('h-select', help='Phase H: freeze the stratified task set (writes h-tasks.json)')
    hsel.add_argument('--round', type=int, default=7)
    hs_ = sub.add_parser('h-store', help='Phase H: SL-lane stores = G stores rebuilt with the current code')
    hs_.add_argument('--round', type=int, default=7)
    hr = sub.add_parser('h-replace', help='Phase H A4: replacement LLM attempts for infra-affected attempts')
    hr.add_argument('--gate', required=True)
    hr.add_argument('--credentials', required=True)
    hr.add_argument('--out', default='evaluation-h')
    hr.add_argument('--round', type=int, default=7)
    hab = sub.add_parser('h-ablate', help='Phase H: causal check per mechanism / knowledge source (read-only)')
    hab.add_argument('--out', default='evaluation-h')
    hab.add_argument('--round', type=int, default=7)
    haf = sub.add_parser('h-ablate-flows', help='Phase H A5: resolve decisive_lost cells with no-LLM flows')
    haf.add_argument('--out', default='evaluation-h')
    haf.add_argument('--round', type=int, default=7)
    haf.add_argument('--jobs', type=int, default=4)
    ii = sub.add_parser('i-isolate', help='Phase I: knowledge-source isolation flows (no LLM)')
    ii.add_argument('--conditions', default='P,L,J')
    ii.add_argument('--jobs', type=int, default=6)
    ii.add_argument('--round', type=int, default=7)
    ia = sub.add_parser('i-analyze', help='Phase I: readings Q1/Q2 (read-only)')
    ia.add_argument('--round', type=int, default=7)
    ha = sub.add_parser('h-analyze', help='Phase H: stage-2 LLM-cost and mechanism attribution (read-only)')
    ha.add_argument('--out', default='evaluation-h')
    ha.add_argument('--round', type=int, default=7)
    gs = sub.add_parser('g-store', help='Phase G: build the G-lane stores (Mp + trial pool), report changes vs Mp')
    gs.add_argument('--trials', default='g-trials')
    gs.add_argument('--round', type=int, default=5)
    gt = sub.add_parser('g-trials', help='Phase G1: hierarchical component trials on training designs (no LLM)')
    gt.add_argument('--out', default='g-trials')
    gt.add_argument('--round', type=int, default=5)
    gt.add_argument('--jobs', type=int, default=6)
    gt.add_argument('--only', default='', help='comma-separated situation keys (smoke)')
    args = ap.parse_args()
    CFG.update(ROUNDS[args.round])
    use_sandbox(CFG['sandbox'])
    if args.cmd == 'qualify':
        args.variants = args.variants or ','.join(CFG['variants'])
    return {'qualify': qualify, 'd2-setup': d2_setup, 'd2-stream': d2_stream, 'g-trials': g_trials,
            'g-store': g_store, 'h-select': h_select, 'h-store': h_store, 'h-replace': h_replace,
            'h-ablate': h_ablate, 'h-ablate-flows': h_ablate_flows, 'h-analyze': h_analyze,
            'i-isolate': i_isolate, 'i-analyze': i_analyze}[args.cmd](args)

if __name__ == '__main__':
    main()
