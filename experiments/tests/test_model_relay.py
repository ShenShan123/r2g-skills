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
    def request(self, handler, body=None, path="/v1/chat/completions", method="POST", protocol="openai"):
        async def run():
            app = relay.create_app("https://fixed.example/v1", "fixed-model", "upstream-secret",
                                   transport=httpx.MockTransport(handler), protocol=protocol)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://relay") as client:
                    return await client.request(method, path, json=body,
                        headers={"Authorization": "Bearer client-dummy", "X-API-Key": "client-secret"})
        return asyncio.run(run())

    def test_upstream_model_alias_rewrites_only_the_upstream_request(self):
        seen = {}
        def upstream(request):
            seen.update(json.loads(request.content)); return httpx.Response(200, json={"ok": True})
        async def run(body):
            app = relay.create_app("https://fixed.example/v1", "fixed-model", "upstream-secret",
                                   transport=httpx.MockTransport(upstream), protocol="openai-responses",
                                   upstream_model="vendor/fixed-model")
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay") as client:
                    return await client.post("/v1/responses", json=body)
        ok = asyncio.run(run({"model": "fixed-model", "input": []}))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(seen["model"], "vendor/fixed-model")
        self.assertIs(seen["store"], False)
        # workers must still name the fixed model; the alias is not accepted from them
        bad = asyncio.run(run({"model": "vendor/fixed-model", "input": []}))
        self.assertEqual(bad.status_code, 400)

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

    def test_responses_route_forces_store_off_and_keeps_its_own_whitelist(self):
        def upstream(request):
            self.assertEqual(str(request.url), "https://fixed.example/v1/responses")
            self.assertEqual(request.headers["authorization"], "Bearer upstream-secret")
            self.assertIs(json.loads(request.content)["store"], False)
            return httpx.Response(200, content=b'data: {"type":"response.completed"}\n\n')
        ok = self.request(upstream, {"model": "fixed-model", "input": [], "store": True, "stream": True},
                          "/v1/responses", protocol="openai-responses")
        self.assertEqual(ok.status_code, 200)
        def never(_request):
            self.fail("Rejected request contacted upstream")
        for body in ({"model": "fixed-model", "input": [], "previous_response_id": "resp_x"},
                     {"model": "fixed-model", "messages": []}, {"model": "other", "input": []}):
            self.assertEqual(self.request(never, body, "/v1/responses", protocol="openai-responses").status_code, 400)
        # one protocol per relay: the chat route does not exist on a Responses relay
        self.assertEqual(self.request(never, {"model": "fixed-model", "messages": []},
                                      protocol="openai-responses").status_code, 404)

    def test_messages_route_uses_x_api_key_and_v1_messages_and_names_rejected_fields(self):
        seen = {}
        def upstream(request):
            seen["url"] = str(request.url); seen["h"] = dict(request.headers)
            return httpx.Response(200, content=b'event: message_stop\ndata: {}\n\n')
        async def run(body, headers):
            app = relay.create_app("https://fixed.example/anthropic", "fixed-model", "upstream-secret",
                                   transport=httpx.MockTransport(upstream), protocol="anthropic")
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay") as c:
                    return await c.post("/v1/messages", json=body, headers=headers)
        ok = asyncio.run(run({"model": "fixed-model", "messages": [], "system": "s", "max_tokens": 9,
                              "thinking": {"type": "enabled"}, "output_config": {"effort": "high"}, "stream": True},
                             {"x-api-key": "client-dummy", "Authorization": "Bearer client", "anthropic-beta": "x-2026-07-01",
                              "anthropic-version": "2023-06-01", "X-Forwarded-Host": "evil"}))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(seen["url"], "https://fixed.example/anthropic/v1/messages")
        self.assertEqual(seen["h"]["x-api-key"], "upstream-secret")
        self.assertNotIn("authorization", seen["h"])
        self.assertEqual(seen["h"]["anthropic-beta"], "x-2026-07-01")
        self.assertNotIn("x-forwarded-host", seen["h"])
        # native DeepSeek Harness attribution passes through unchanged
        asyncio.run(run({"model": "fixed-model", "messages": [], "dsh_session_log": "s", "dsh_plugin_packages": []},
                        {"x-deepseek-harness-session-id": "session-1a2b", "user-agent": "deepseek-harness/0.2.2-rc.1 (+https://x)"}))
        self.assertEqual(seen["h"]["x-deepseek-harness-session-id"], "session-1a2b")
        self.assertTrue(seen["h"]["user-agent"].startswith("deepseek-harness/"))
        bad = asyncio.run(run({"model": "fixed-model", "messages": [], "base_url": "https://evil"}, {}))
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()["fields"], ["base_url"])
        # a header value outside the protocol vocabulary is dropped, not forwarded
        seen.clear()
        asyncio.run(run({"model": "fixed-model", "messages": []}, {"anthropic-beta": "a\r\nx-api-key: steal"}))
        self.assertNotIn("anthropic-beta", seen.get("h", {}))

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
