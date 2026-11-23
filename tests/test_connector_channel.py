"""Which AI client a connector user came through: classify, extract, report."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from tests.test_oauth import _BASE, _OAUTH_ENV
from vaquill_mcp import connector_channel as cc
from vaquill_mcp.oauth import build_auth_provider

_URL = f"{_BASE}/api/v1/internal/connector-keys/client-seen"


def _jwt(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"h.{body}.s"


class TestClassify:
    @pytest.mark.parametrize(
        ("client_id", "name", "redirects", "family"),
        [
            (
                "https://claude.ai/oauth/x",
                "Claude",
                ["https://claude.ai/api/mcp/auth_callback"],
                "claude",
            ),
            ("dcr-1", "Claude", ["https://claude.com/api/mcp/auth_callback"], "claude"),
            (
                "dcr-2",
                "ChatGPT",
                ["https://chatgpt.com/connector_platform_oauth_redirect"],
                "chatgpt",
            ),
            (
                "dcr-3",
                "OpenAI",
                ["https://platform.openai.com/apps-manage/oauth"],
                "chatgpt",
            ),
            (
                "dcr-4",
                "Claude Code (vaquill)",
                ["http://localhost:51234/callback"],
                "claude_code",
            ),
            ("dcr-5", "Codex", ["http://127.0.0.1:1455/auth/callback"], "codex"),
            (
                "dcr-6",
                "Cursor",
                ["cursor://anysphere.cursor-retrieval/oauth/callback"],
                "cursor",
            ),
            ("dcr-7", "Visual Studio Code", ["http://127.0.0.1:33418"], "vscode"),
            ("dcr-8", "my script", ["http://localhost:9999/cb"], "other"),
            # Gemini Enterprise's real registration, 2026-11-23.
            (
                "dcr-9",
                "Gemini Enterprise",
                [
                    "https://vertexaisearch.cloud.google.com/oauth-redirect",
                    "https://enterprise.gemini.google.com/oauth-redirect",
                ],
                "gemini",
            ),
            (
                "dcr-10",
                "anything",
                ["https://vertexaisearch.cloud.google.com/oauth-redirect"],
                "gemini",
            ),
            # Another Google host is not the connector.
            ("dcr-11", "tool", ["https://script.google.com/cb"], "other"),
        ],
    )
    def test_families(self, client_id, name, redirects, family):
        assert cc.classify_client(client_id, name, redirects) == family

    def test_a_lookalike_host_is_not_claude_or_chatgpt(self):
        assert (
            cc.classify_client("x", "tool", ["https://claude.ai.evil.example/cb"])
            == "other"
        )
        assert cc.classify_client("x", "tool", ["https://notchatgpt.com/cb"]) == "other"

    def test_garbage_input_never_raises(self):
        assert cc.classify_client(None, None, None) == "other"
        assert cc.classify_client(object(), 5, [None]) == "other"

    @pytest.mark.parametrize(
        ("name", "redirects"),
        [
            ("Harvey", ["https://app.harvey.ai/oauth/callback"]),
            ("Harvey MCP", ["https://example.invalid/cb"]),
        ],
    )
    def test_harvey(self, name, redirects):
        assert cc.classify_client("dcr-h", name, redirects) == "harvey"

    def test_every_family_is_in_the_backend_allow_list(self):
        assert set(cc.FAMILIES) == {
            "claude",
            "chatgpt",
            "gemini",
            "harvey",
            "claude_code",
            "codex",
            "cursor",
            "vscode",
            "other",
        }


class TestEvidence:
    def test_cloudflare_address_and_headers(self):
        scope = {
            "client": ("10.0.0.5", 4),
            "headers": [
                (b"x-forwarded-for", b"1.1.1.1, 172.16.0.1"),
                (b"CF-Connecting-IP", b"198.51.100.4"),
                (b"cf-ipcountry", b"US"),
                (b"user-agent", b"x" * 900),
            ],
        }
        ev = cc.request_evidence(scope)
        assert ev["ip"] == "198.51.100.4"
        assert ev["country"] == "US"
        assert len(ev["userAgent"]) == 300
        assert "city" not in ev

    def test_garbage_scope_is_empty(self):
        assert cc.request_evidence(None) == {}
        assert cc.request_evidence({"headers": [], "client": None}) == {}

    def test_redirect_hosts_are_distinct_and_ordered(self):
        assert cc.redirect_hosts(
            ["https://a.example/x", "https://b.example/y", "https://a.example/z", 5]
        ) == ["a.example", "b.example"]
        assert cc.redirect_hosts(None) == []


class TestTokenRequestContext:
    async def test_token_handler_sees_the_platform_and_it_is_cleared_after(self):
        from vaquill_mcp.oauth import AuthDiagnosticsMiddleware

        seen: list = []

        async def app(scope, receive, send):
            seen.append(cc.TOKEN_REQUEST.get())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        async def receive():
            return {"type": "http.request", "body": b"grant_type=authorization_code"}

        async def send(message):
            return None

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/token",
            "headers": [
                (b"cf-connecting-ip", b"2600:1900::1"),
                (b"user-agent", b"GoogleGeminiEnterprise-ConnectorOAuth/1.0"),
            ],
        }
        await AuthDiagnosticsMiddleware(app)(scope, receive, send)
        assert seen == [
            {
                "ip": "2600:1900::1",
                "userAgent": "GoogleGeminiEnterprise-ConnectorOAuth/1.0",
            }
        ]
        assert cc.TOKEN_REQUEST.get() is None

        seen.clear()
        await AuthDiagnosticsMiddleware(app)({**scope, "path": "/mcp"}, receive, send)
        assert seen == [None]


class TestSubject:
    def test_reads_sub(self):
        assert cc.subject_from_access_token(_jwt({"sub": "user-1"})) == "user-1"

    @pytest.mark.parametrize(
        "bad", [None, "", "abc", "a.b.c", _jwt({"no": "sub"}), _jwt({"sub": 5})]
    )
    def test_unreadable_is_none(self, bad):
        assert cc.subject_from_access_token(bad) is None


class TestReport:
    async def test_posts_family_with_the_secret(self, respx_mock) -> None:
        seen = {}

        def _record(request):
            seen["secret"] = request.headers.get("x-vaquill-internal")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"clientFamily": "claude"})

        respx_mock.post(_URL).mock(side_effect=_record)
        assert await cc.report_client_seen(_BASE, "s3", "u1", "claude", "cid") is True
        assert seen["secret"] == "s3"
        assert seen["body"] == {
            "userId": "u1",
            "clientFamily": "claude",
            "clientId": "cid",
        }

    async def test_details_are_sent_and_empty_ones_dropped(self, respx_mock) -> None:
        seen = {}

        def _record(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"clientFamily": "gemini"})

        respx_mock.post(_URL).mock(side_effect=_record)
        details = {
            "clientName": "Gemini Enterprise",
            "redirectHosts": ["vertexaisearch.cloud.google.com"],
            "browser": {"ip": "203.0.113.7"},
            "platform": None,
        }
        assert await cc.report_client_seen(_BASE, "s", "u1", "gemini", "c", details)
        assert seen["body"]["browser"] == {"ip": "203.0.113.7"}
        assert seen["body"]["clientName"] == "Gemini Enterprise"
        assert "platform" not in seen["body"]

    async def test_a_failure_is_swallowed(self, respx_mock) -> None:
        respx_mock.post(_URL).mock(return_value=httpx.Response(500))
        assert await cc.report_client_seen(_BASE, "s", "u1", "claude", None) is False
        respx_mock.post(_URL).mock(side_effect=httpx.ConnectError("down"))
        assert await cc.report_client_seen(_BASE, "s", "u1", "claude", None) is False

    def test_no_secret_means_no_task(self, monkeypatch) -> None:
        monkeypatch.delenv("VAQUILL_INTERNAL_SECRET", raising=False)
        cc.schedule_report("u1", "claude", None)  # no running loop, must not raise


class TestExchangeHook:
    def _proxy(self, monkeypatch):
        for name, value in _OAUTH_ENV.items():
            monkeypatch.setenv(name, value)
        auth = build_auth_provider()
        assert auth is not None
        return auth.server

    async def test_report_is_scheduled_after_a_successful_exchange(
        self, monkeypatch
    ) -> None:
        from fastmcp.server.auth import OAuthProxy

        proxy = self._proxy(monkeypatch)
        calls: list[tuple] = []
        monkeypatch.setattr(
            "vaquill_mcp.oauth.schedule_report", lambda *a: calls.append(a)
        )

        order: list[str] = []

        async def fake_super(self, client, code):
            # The one-time code must still be readable when the subject is taken.
            order.append("super")
            return "token"

        monkeypatch.setattr(OAuthProxy, "exchange_authorization_code", fake_super)

        class _Store:
            async def get(self, key):
                order.append("read-code")
                return type(
                    "M", (), {"idp_tokens": {"access_token": _jwt({"sub": "user-9"})}}
                )()

        proxy._code_store = _Store()

        class _Client:
            client_id = "https://chatgpt.com/cimd"
            client_name = "ChatGPT"
            redirect_uris = ["https://chatgpt.com/connector_platform_oauth_redirect"]

        class _Code:
            code = "c1"

        result = await proxy.exchange_authorization_code(_Client(), _Code())
        assert result == "token"
        assert order == ["read-code", "super"]
        assert len(calls) == 1
        assert calls[0][:3] == ("user-9", "chatgpt", "https://chatgpt.com/cimd")
        assert calls[0][3]["clientName"] == "ChatGPT"
        assert calls[0][3]["redirectHosts"] == ["chatgpt.com"]

    async def test_browser_and_platform_evidence_reach_the_report(
        self, monkeypatch
    ) -> None:
        """The callback keeps the person's browser; the exchange adds the platform."""
        from fastmcp.server.auth import OAuthProxy
        from starlette.requests import Request
        from starlette.responses import RedirectResponse

        from vaquill_mcp import oauth

        proxy = self._proxy(monkeypatch)
        calls: list[tuple] = []
        monkeypatch.setattr(
            "vaquill_mcp.oauth.schedule_report", lambda *a: calls.append(a)
        )

        class _KV:
            def __init__(self):
                self.data: dict = {}

            async def put(self, key, value, *, collection=None, ttl=None):
                self.data[(collection, key)] = (dict(value), ttl)

            async def get(self, key, *, collection=None):
                hit = self.data.get((collection, key))
                return hit[0] if hit else None

            async def delete(self, key, *, collection=None):
                return self.data.pop((collection, key), None) is not None

        kv = _KV()
        proxy._client_storage = kv

        async def fake_callback(self, request):
            return RedirectResponse(
                "https://chatgpt.com/cb?code=the-code&state=s", status_code=302
            )

        monkeypatch.setattr(OAuthProxy, "_handle_idp_callback", fake_callback)

        browser_scope = {
            "type": "http",
            "method": "GET",
            "path": "/auth/callback",
            "query_string": b"",
            "client": ("10.0.0.2", 1),
            "headers": [
                (b"cf-connecting-ip", b"203.0.113.7"),
                (b"cf-ipcountry", b"IN"),
                (b"cf-ipcity", b"Pune"),
                (b"user-agent", b"Mozilla/5.0 Chrome/131"),
                (b"accept-language", b"en-IN"),
            ],
        }
        await proxy._handle_idp_callback(Request(browser_scope))
        key = (oauth._EVIDENCE_COLLECTION, oauth._evidence_key("the-code"))
        assert kv.data[key][0]["ip"] == "203.0.113.7"
        assert kv.data[key][1] == oauth._EVIDENCE_TTL_SECONDS
        # The raw code is never a key name.
        assert all("the-code" not in k for _, k in kv.data)

        async def fake_super(self, client, code):
            return "token"

        monkeypatch.setattr(OAuthProxy, "exchange_authorization_code", fake_super)

        class _Store:
            async def get(self, key):
                return type(
                    "M", (), {"idp_tokens": {"access_token": _jwt({"sub": "u-7"})}}
                )()

        proxy._code_store = _Store()
        platform = {"ip": "2600:1900::1", "userAgent": "GoogleGeminiEnterprise/1.0"}
        reset = cc.TOKEN_REQUEST.set(platform)
        try:
            await proxy.exchange_authorization_code(
                type(
                    "C",
                    (),
                    {
                        "client_id": "dcr-x",
                        "client_name": "Gemini Enterprise",
                        "redirect_uris": [
                            "https://vertexaisearch.cloud.google.com/oauth-redirect"
                        ],
                    },
                )(),
                type("K", (), {"code": "the-code"})(),
            )
        finally:
            cc.TOKEN_REQUEST.reset(reset)

        details = calls[0][3]
        assert calls[0][1] == "gemini"
        assert details["browser"] == {
            "ip": "203.0.113.7",
            "country": "IN",
            "city": "Pune",
            "userAgent": "Mozilla/5.0 Chrome/131",
            "acceptLanguage": "en-IN",
        }
        assert details["platform"] == platform
        # Taken once: the evidence is deleted after the exchange reads it.
        assert key not in kv.data

    async def test_a_callback_without_a_code_stores_nothing(self, monkeypatch) -> None:
        from fastmcp.server.auth import OAuthProxy
        from starlette.requests import Request
        from starlette.responses import HTMLResponse

        proxy = self._proxy(monkeypatch)
        stored: list = []

        class _KV:
            async def put(self, *a, **k):
                stored.append(a)

        proxy._client_storage = _KV()

        async def failed(self, request):
            return HTMLResponse("error", status_code=400)

        monkeypatch.setattr(OAuthProxy, "_handle_idp_callback", failed)
        scope = {"type": "http", "headers": [(b"cf-connecting-ip", b"203.0.113.9")]}
        response = await proxy._handle_idp_callback(Request(scope))
        assert response.status_code == 400
        assert stored == []

    async def test_a_failed_exchange_reports_nothing(self, monkeypatch) -> None:
        from fastmcp.server.auth import OAuthProxy

        proxy = self._proxy(monkeypatch)
        calls: list[tuple] = []
        monkeypatch.setattr(
            "vaquill_mcp.oauth.schedule_report", lambda *a: calls.append(a)
        )

        async def boom(self, client, code):
            raise RuntimeError("invalid_grant")

        monkeypatch.setattr(OAuthProxy, "exchange_authorization_code", boom)

        class _Store:
            async def get(self, key):
                return type(
                    "M", (), {"idp_tokens": {"access_token": _jwt({"sub": "u"})}}
                )()

        proxy._code_store = _Store()
        with pytest.raises(RuntimeError):
            await proxy.exchange_authorization_code(
                type(
                    "C", (), {"client_id": "x", "client_name": "n", "redirect_uris": []}
                )(),
                type("K", (), {"code": "c"})(),
            )
        assert calls == []
