"""Environment-only continuation, never launches an Agent or changes its code.

The cached scikit-learn environments contain conda packages, not a pip block.
Use the public upstream spec and the cached Python package version pins through
conda-forge only. Container OS/build identities are not claimed as reproduced.
All qualification failures, setup commands and differential grades are kept.
"""
import json
from pathlib import Path
import shlex
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters'))
import provision
from admission import sha


def conda_only_recipe(spec,upstream):
    import yaml
    envs=[s.split('\n',1)[1].rsplit('\nEOF_59812759871',1)[0] for s in upstream
          if 'EOF_59812759871' in s and 'environment.yml' in s]
    if len(envs)!=1:
        raise ValueError('one cached upstream environment is required')
    data=yaml.safe_load(envs[0])
    pins={s.split('=')[0]:s.split('=')[1] for s in data['dependencies']
          if isinstance(s,str) and '=' in s}
    # These are all explicitly named Python dependencies of the pinned sklearn
    # spec, plus its environment management tools. No package pins are guessed.
    names=['python','numpy','scipy','cython','pytest','pandas','matplotlib-base',
           'joblib','threadpoolctl','pip','setuptools','wheel']
    if any(n not in pins for n in names) or not pins['python'].startswith(spec['python']+'.'):
        raise ValueError('upstream dependency pins or Python minor unavailable')
    packages=[n+'='+pins[n] for n in names]
    return ['export CONDA_ENVS_PATH=/root/envs','export CONDA_PKGS_DIRS=/root/conda-pkgs',
        'export CONDARC=/root/.condarc','mkdir -p /root/envs',
        '/opt/anaconda3/bin/conda create --override-channels -c conda-forge '
        '-p /root/envs/testbed -y '+' '.join(shlex.quote(s) for s in packages),
        'export PATH=/root/envs/testbed/bin:/usr/bin:/bin']


def main():
    original=provision.local_dependency_recipe
    def recipe(spec,upstream):
        try:
            return original(spec,upstream)
        except ValueError as exc:
            if str(exc)!='cached environment has no portable pip dependency list':
                raise
            if not all(s in spec.get('packages','') for s in ('numpy==1.19.2','scipy==1.5.2','cython==3.0.10')):
                raise ValueError('no registered conda-only adaptation for this spec') from exc
            return conda_only_recipe(spec,upstream)
    provision.local_dependency_recipe=recipe
    provision.record(ROOT/'reports/qualification-continuation-v2.json',{
        'operator_sha256':sha(Path(__file__)),'created':time.time(),
        'model_requests':0,'both_arms_same_environment':True,'reselect_tasks':False,
        'adaptation':'public sklearn spec, cached Python dependency version pins, conda-forge only; no OS/build identity claim',
        'system_or_upstream_source_mutation':False})
    sys.argv=[str(ROOT/'adapters/provision.py'),'--resume-preparation']
    provision.main()


if __name__=='__main__':
    main()
