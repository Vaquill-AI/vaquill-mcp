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

    def test_every_family_is_in_the_backend_allow_list(self):
        assert set(cc.FAMILIES) == {
            "claude",
            "chatgpt",
            "claude_code",
            "codex",
            "cursor",
            "vscode",
            "other",
        }


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
        assert calls == [("user-9", "chatgpt", "https://chatgpt.com/cimd")]

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
