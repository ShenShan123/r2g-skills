import importlib.util
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('qualify_remaining_v2',ROOT/'operators/qualify_remaining_v2.py')
operator=importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)


def test_conda_only_uses_upstream_versions_and_no_commercial_channel():
    names={'python':'3.9.20','numpy':'1.19.2','scipy':'1.5.2','cython':'3.0.10',
           'pytest':'7.4.4','pandas':'1.4.4','matplotlib-base':'3.6.2','joblib':'1.4.2',
           'threadpoolctl':'3.5.0','pip':'24.2','setuptools':'75.1.0','wheel':'0.44.0'}
    upstream=["cat <<'EOF_59812759871' > /root/environment.yml\ndependencies:\n"+
              ''.join(f'  - {n}={v}=fixture-build\n' for n,v in names.items())+'EOF_59812759871']
    text='\n'.join(operator.conda_only_recipe({'python':'3.9'},upstream))
    assert '--override-channels -c conda-forge' in text
    assert 'tos accept' not in text and 'defaults' not in text and 'fixture-build' not in text
    assert all(n+'='+v in text for n,v in names.items())
    with pytest.raises(ValueError,match='Python minor'):
        operator.conda_only_recipe({'python':'3.8'},upstream)


def test_unknown_conda_recipe_not_silently_approximated():
    with pytest.raises(ValueError):
        operator.conda_only_recipe({'python':'3.9'},[])
