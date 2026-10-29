"""No-model environment repair after a retained conda solver failure.

conda-forge's matplotlib-base 3.6.2 build demands newer numpy than the official
sklearn spec's numpy 1.19.2. Keep numpy/scipy and the Python minor unchanged;
install Python wheels for the other original cached versions (matplotlib-base
maps explicitly to PyPI matplotlib). The local differential qualification gate
is unchanged; neither Agent is launched until baseline FAIL + reference PASS.
"""
import importlib.util
import json
from pathlib import Path
import shlex
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters'),str(ROOT/'operators')]
import provision
from admission import sha
from qualify_remaining_v2 import conda_only_recipe


def mixed_recipe(spec,upstream):
    commands=conda_only_recipe(spec,upstream)
    index=next(i for i,s in enumerate(commands) if s.startswith('/opt/anaconda3/bin/conda create '))
    packages=shlex.split(commands[index])[9:]
    # Extract from the generated pinned command, not user text or hidden tests.
    all_args=shlex.split(commands[index])
    packages=all_args[all_args.index('-y')+1:]
    pins=dict(s.split('=',1) for s in packages)
    conda_names=('python','numpy','scipy','pip')
    commands[index]='/opt/anaconda3/bin/conda create --override-channels -c conda-forge '+\
        '-p /root/envs/testbed -y '+' '.join(shlex.quote(n+'='+pins[n]) for n in conda_names)
    pip_pins=[('matplotlib' if n=='matplotlib-base' else n)+'=='+v for n,v in pins.items()
              if n not in {'python','pip'}]
    commands.append('python -m pip install '+' '.join(shlex.quote(s) for s in pip_pins))
    return commands


def main():
    original=provision.local_dependency_recipe
    def recipe(spec,upstream):
        try:
            return original(spec,upstream)
        except ValueError as exc:
            if str(exc)!='cached environment has no portable pip dependency list' or not all(
                    s in spec.get('packages','') for s in ('numpy==1.19.2','scipy==1.5.2','cython==3.0.10')):
                raise
            return mixed_recipe(spec,upstream)
    provision.local_dependency_recipe=recipe
    provision.record(ROOT/'reports/qualification-mixed-v4.json',{
        'created':time.time(),'operator_sha256':sha(Path(__file__)),
        'model_requests':0,'both_arms_same_environment':True,'roster_changed':False,
        'preserved_pins':['Python 3.9.20','numpy 1.19.2','scipy 1.5.2','all cached Python dependency versions'],
        'mapping':'conda matplotlib-base=3.6.2 -> PyPI matplotlib==3.6.2',
        'no_native_runtime_changes':True,'no_terms_acceptance':True,
        'previous_solver_failure_retained':True})
    sys.argv=[str(ROOT/'adapters/provision.py'),'--resume-preparation']
    provision.main()


if __name__=='__main__':
    main()
