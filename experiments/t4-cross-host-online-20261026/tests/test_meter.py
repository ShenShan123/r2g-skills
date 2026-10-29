import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters"))
from meter import MeteredProvider, UnknownUsage


class Response:
    status = 200
    headers = {"x-request-id": "server-id", "Set-Cookie": "not-recorded"}
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return json.dumps(self.body).encode()


def test_provider_usage_is_recorded_without_secret_headers(tmp_path):
    p = MeteredProvider(tmp_path / "episode", "TEST-NOT-A-REAL-KEY")
    body = {"id": "completion-1", "model": "deepseek-flash", "choices": [],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}
    assert p.request({"messages": []}, transport=lambda *a, **kw: Response(body)) == body
    text = p.journal.path.read_text()
    assert '"total_tokens":120' in text
    assert "TEST-NOT-A-REAL-KEY" not in text and "Set-Cookie" not in text


@pytest.mark.parametrize("kind", ["network", "missing_usage", "bad_usage"])
def test_unknown_never_becomes_zero_or_an_implicit_retry(tmp_path, kind):
    p = MeteredProvider(tmp_path / "episode", "TEST-NOT-A-REAL-KEY")
    calls = []
    def transport(*a, **kw):
        calls.append(1)
        if kind == "network": raise OSError("test connection loss")
        data = {"id": "completion-1", "model": "deepseek-flash"}
        if kind == "bad_usage":
            data["usage"] = {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 99}
        return Response(data)
    with pytest.raises(UnknownUsage): p.request({"messages": []}, transport=transport)
    with pytest.raises(UnknownUsage): p.request({"messages": []}, transport=transport)
    assert len(calls) == 1
    assert '"usage":"UNKNOWN"' in p.journal.path.read_text()


def test_transport_diagnostics_preserve_unknown_and_redact_the_credential(tmp_path):
    from urllib.error import URLError
    provider = MeteredProvider(tmp_path / "episode", "SECRET-FIXTURE-KEY")
    def fail(*args, **kwargs):
        raise URLError("SSL connection lost SECRET-FIXTURE-KEY")
    with pytest.raises(UnknownUsage): provider.request({"messages": []}, transport=fail)
    record = provider.journal.path.read_text()
    assert "SSL connection lost" in record and "[REDACTED]" in record
    assert "SECRET-FIXTURE-KEY" not in record and '"usage":"UNKNOWN"' in record
