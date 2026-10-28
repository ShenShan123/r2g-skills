"""Exercise the public selftest command through real argument parsing."""
from types import SimpleNamespace

import pytest

from r2g_agent import cli, tools


@pytest.mark.parametrize("platform", ["nangate45", "sky130hd", "gf180"])
@pytest.mark.parametrize("passed, expected", [(1, 0), (0, 1)])
def test_selftest_submits_default_graph_job(monkeypatch, platform, passed, expected):
    submissions = []
    monkeypatch.setattr(cli, "_runtime", lambda: {})
    monkeypatch.setattr(tools, "RT", SimpleNamespace(store=object()))

    def start(designs, **kwargs):
        submissions.append((designs, kwargs))
        return {"job_id": "selftest-job"}

    monkeypatch.setattr(tools, "start_job", start)
    monkeypatch.setattr(cli, "_follow", lambda *a, **kw: {"result": {"passed": passed}})
    assert cli.main(["selftest", "--platform", platform]) == expected
    assert submissions == [([{"example": "counter8"}], {
        "platform": platform, "output": "graph", "params": None,
    })]
