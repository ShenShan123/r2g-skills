"""No external network or real credentials: test relay forwarding boundaries."""
import asyncio
import importlib.util
import json
from pathlib import Path
import unittest

import httpx

MODULE = Path(__file__).resolve().parents[1] / "agent_repro/serve_model_relay.py"
spec = importlib.util.spec_from_file_location("serve_model_relay", MODULE)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class RelayTests(unittest.TestCase):
    def request(self, handler, body=None, path="/v1/chat/completions", method="POST"):
        async def run():
            app = relay.create_app("https://fixed.example/v1", "fixed-model", "upstream-secret",
                                   transport=httpx.MockTransport(handler))
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://relay") as client:
                    return await client.request(method, path, json=body,
                        headers={"Authorization": "Bearer client-dummy", "X-API-Key": "client-secret"})
        return asyncio.run(run())

    def test_stream_and_usage_pass_through_without_client_headers(self):
        payload = (b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                   b'data: {"choices":[],"usage":{"prompt_tokens":23,"completion_tokens":7,"total_tokens":30}}\n\n'
                   b'data: [DONE]\n\n')
        def upstream(request):
            self.assertEqual(str(request.url), "https://fixed.example/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer upstream-secret")
            self.assertNotIn("x-api-key", request.headers)
            self.assertTrue(json.loads(request.content)["stream_options"]["include_usage"])
            return httpx.Response(200, content=payload)
        result = self.request(upstream, {"model": "fixed-model", "messages": [],
                              "stream": True, "stream_options": {"include_usage": True}})
        self.assertEqual(result.content, payload)
        self.assertNotIn("upstream-secret", result.text)

    def test_model_endpoint_and_unknown_routes_cannot_override_upstream(self):
        def never(_request):
            self.fail("Rejected request contacted upstream")
        for body in ({"model": "other", "messages": []},
                     {"model": "fixed-model", "messages": [], "base_url": "https://evil"},
                     {"model": "fixed-model", "messages": [], "headers": {"Authorization": "x"}}):
            self.assertEqual(self.request(never, body).status_code, 400)
        self.assertEqual(self.request(never, {}, "/v1/elsewhere").status_code, 404)
        self.assertEqual(self.request(never, {"model": "fixed-model", "messages": []},
                         "/v1/chat/completions?url=https://evil").status_code, 400)

    def test_errors_and_redirects_never_echo_provider_credentials(self):
        for status in (302, 401, 500):
            result = self.request(lambda _: httpx.Response(status,
                headers={"Location": "https://evil/steal"}, content=b'upstream-secret'),
                {"model": "fixed-model", "messages": []})
            self.assertEqual(result.status_code, 502)
            self.assertNotIn("upstream-secret", result.text)

    def test_transport_exception_and_health_do_not_expose_credentials(self):
        def broken(_request):
            raise RuntimeError("upstream-secret")
        result = self.request(broken, {"model": "fixed-model", "messages": []})
        self.assertEqual(result.status_code, 502)
        self.assertNotIn("upstream-secret", result.text)
        health = self.request(broken, path="/health", method="GET")
        self.assertEqual(health.status_code, 200)
        self.assertNotIn("secret", health.text)

    def test_url_credentials_and_non_https_upstream_rejected(self):
        for url in ("http://fixed.example/v1", "https://user:secret@fixed.example/v1",
                    "https://fixed.example/v1?key=secret"):
            with self.assertRaises(ValueError):
                relay.create_app(url, "fixed-model", "upstream-secret")


if __name__ == "__main__":
    unittest.main()
