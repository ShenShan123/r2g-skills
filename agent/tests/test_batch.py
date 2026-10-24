import tarfile
from pathlib import Path

from r2g_agent import batch
from r2g_agent.jobs import JobStore
from r2g_agent.package import package_design, package_job


def _w(p: Path, t: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(t)


def test_scan_one_design_per_subfolder(tmp_path):
    _w(tmp_path / "a" / "rtl" / "a.v", "module a(input x, output y); assign y = x; endmodule\n")
    _w(tmp_path / "b" / "b.v", "module p(); endmodule\nmodule q(); endmodule\n")      # two tops: skipped
    _w(tmp_path / "c" / "design.json", '{"top": "q"}')
    _w(tmp_path / "c" / "c.v", "module p(); endmodule\nmodule q(); endmodule\n")      # design.json decides
    _w(tmp_path / "docs" / "readme.txt", "no hdl")
    _w(tmp_path / ".git" / "x.v", "module g(); endmodule\n")
    r = batch.scan(tmp_path)
    assert (r["total"], r["ok"], r["skipped"]) == (3, 2, 1)
    by = {d["name"]: d for d in r["designs"]}
    assert by["a"]["top"] == "a" and by["c"]["top"] == "q" and "顶层" in by["b"]["problem"]
    assert [s["name"] for s in batch.sources(r)] == ["a", "c"]


def test_scan_a_plain_design_folder_is_one_design(tmp_path):
    _w(tmp_path / "top.v", "module top(); endmodule\n")
    r = batch.scan(tmp_path)
    assert r["total"] == 1 and r["designs"][0]["top"] == "top"


def test_partial_package_and_per_design(tmp_path):
    st = JobStore()
    jid = st.create_job("t", {"platform": "nangate45", "output": "graph"}, [{"name": "a"}, {"name": "b"}])
    for n, status in (("a", "passed"), ("b", "running")):
        p = tmp_path / n
        _w(p / "dataset" / "b_graph.pt", "g")
        _w(p / "reports" / "drc.json", "{}")
        st.set_design(jid, n, status=status, project=str(p))
    part = package_job(st, jid, partial=True)
    assert part.name.endswith("_partial.tar.gz")
    names = tarfile.open(part).getnames()
    assert any("/a/dataset" in x for x in names) and not any("/b/" in x for x in names)
    one = package_design(st, jid, "a")
    assert any(x.endswith("dataset/b_graph.pt") for x in tarfile.open(one).getnames())
    try:
        package_design(st, jid, "b")
        raise AssertionError("a running design was packaged")
    except ValueError:
        pass
