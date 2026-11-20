#!/usr/bin/env python3
"""Recipe-reuse re-run (2026-11-08): R2G + DeepSeek from the ORIGINAL baseline failure on every in-scope design
R2G did not fix in Exp3 but an LLM fallback did (61). Identical to the Exp3 R2G arm (start_full203_b.py /
start_gf180full203.py: same runtime 42c774be, env files, model, cores per design, ingest off) EXCEPT:
  * repo   = N/repo: the 42c774be archive + the learned-recipe mechanism (3 files, see N/OVERLAY.md)
  * store  = the arm's own Exp3 store + the learned_recipes tables built from the Exp3 fallback attempts
             (N45/SKY130: the shipped 42c774be store; GF180: the frozen r2g_full_gf180_20261101/store)
Deployed on 203 as /data/yangao/recipe_reuse_20261108/start_reuse.py.   Usage: start_reuse.py servers | runners
"""
import os, subprocess, sys
from pathlib import Path
N = Path('/data/yangao/recipe_reuse_20261108'); repo = N / 'repo'
OLD = Path('/data/yangao/r2g-api-repair-cost-20261028'); PY = OLD / 'venv-agent/bin/python'
ARM = dict(provider='deepseek', model='deepseek-flash', base_url='http://127.0.0.1:18774/v1', protocol='openai')
PLATS = {'nangate45': dict(port=8840, workers=4, env='tools203_data.env', store={'R2G_KNOWLEDGE_DB': N / 'store_n45sky/knowledge.sqlite'}),
         'sky130hd': dict(port=8841, workers=2, env='tools203_data.env', store={'R2G_KNOWLEDGE_DB': N / 'store_n45sky/knowledge.sqlite'}),
         'gf180': dict(port=8842, workers=4, env='tools203_gf180.env', store={'R2G_KNOWLEDGE_DB': N / 'store_gf180/knowledge.sqlite',
                                                                               'R2G_HEURISTICS_PATH': N / 'store_gf180/heuristics.json'})}
CORES = 8


def servers():
    sys.path.insert(0, str(repo / 'agent'))
    for plat, P in PLATS.items():
        out = N / plat; rt = out / 'runtime'; rt.mkdir(parents=True, exist_ok=True)
        if (out / 'server.pid').exists():
            print('exists, skip', out); continue
        os.environ['R2G_HOME'] = str(rt)
        from r2g_agent import config
        cfg = config.Config(platform=plat, workers=P['workers'], cores_per_design=CORES, ingest=False,
                            min_free_gb=80, keep_intermediate=False, port=P['port'])
        cfg.model = config.ModelConfig(api_key_env='R2G_LOCAL_RELAY_AUTH', thinking=True, max_tokens=16384, **ARM)
        config.save(cfg)
        e = {k: v for k, v in os.environ.items() if k not in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONEXECUTABLE', '__PYVENV_LAUNCHER__',
                                                             'PYTHONSAFEPATH', 'OPENAI_API_KEY', 'DEEPSEEK_API_KEY')}
        e.update(R2G_REPO=str(repo), R2G_HOME=str(rt), R2G_FIXED_CORES='1', R2G_IGNORE_ENV_LOCAL='1',
                 R2G_ENV_FILE=str(OLD / 'r2g_vs_bare' / P['env']),
                 R2G_LOCAL_RELAY_AUTH='local-relay-only-not-a-provider-key', R2G_ORFS_AUDIT_FILE=str(out / 'orfs_audit.jsonl'),
                 R2G_GRAPH_PYTHON=str(OLD / 'bin/graph-python'), R2G_SLANG_YOSYS='/home/yangao/r2g_toolchain/oss-cad-suite/bin/yosys',
                 R2G_LEARNED_RECIPES='1', **{k: str(v) for k, v in P['store'].items()})
        code = "import sys;sys.path.insert(0,%r);from r2g_agent.cli import main;sys.exit(main(['ui','--port','%d']))" % (
            str(repo / 'agent'), P['port'])
        with (out / 'server.log').open('ab') as log:
            p = subprocess.Popen([str(PY), '-c', code], cwd=repo, env=e, stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        (out / 'server.pid').write_text('%d\n' % p.pid)
        print(plat, 'port', P['port'], 'pid', p.pid)


def runners(extra):
    for plat, P in PLATS.items():
        out = N / plat
        cmd = ['python3', str(repo / 'experiments/measure_agent_repair_cost.py'), '--manifest', str(N / ('reuse_%s.json' % plat)),
               '--api', 'http://127.0.0.1:%d' % P['port'], '--output', str(out / 'measure'), '--workers', str(P['workers']),
               '--orfs-audit', str(out / 'orfs_audit.jsonl'), '--output-mode', 'signoff', '--invocation-retries', '1'] + extra
        with (out / 'runner.log').open('ab') as log:
            p = subprocess.Popen(cmd, cwd=repo, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        (out / 'runner.pid').write_text('%d\n' % p.pid)
        print(plat, 'runner pid', p.pid, ' '.join(extra))


if __name__ == '__main__':
    servers() if sys.argv[1] == 'servers' else runners(sys.argv[2:])
