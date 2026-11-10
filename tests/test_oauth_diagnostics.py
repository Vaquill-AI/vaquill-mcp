"""`/token` and `/mcp` 401s must say who and what, without ever printing a credential.

2026-11-08 14:32:32 and 19:59:55 each logged a `/token` 401 that no client, grant
or cause could be attached to, and a client that went 401 -> discovery ->
authorize with no `/token` call at all could not be classed as "held nothing" or
"held something expired". These tests assert the line that settles each, and
that the secret next to it is absent from the log.
"""

from __future__ import annotations

import logging

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tests.test_brand_assets import _live_api  # noqa: F401 - fixture
from tests.test_oauth import _OAUTH_ENV
from vaquill_mcp.oauth import AuthDiagnosticsMiddleware, _credential_kind

_SECRET = "SECRET-refresh-token-value"


def _app(status_for: dict[str, int]) -> Starlette:
    async def answer(request):
        await request.body()
        return JSONResponse({}, status_code=status_for[request.url.path])

    return Starlette(
        routes=[
            Route("/token", answer, methods=["POST"]),
            Route("/mcp", answer, methods=["GET", "POST"]),
            Route("/other", answer, methods=["GET", "POST"]),
        ],
        middleware=[Middleware(AuthDiagnosticsMiddleware)],
    )


def test_a_token_401_logs_the_grant_and_client_but_not_the_form_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(_app({"/token": 401}))
    with caplog.at_level(logging.INFO, logger="vaquill_mcp.oauth"):
        response = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": "claude-abc",
                "refresh_token": _SECRET,
                "client_secret": _SECRET,
            },
        )
    assert response.status_code == 401
    assert "oauth_token_request grant_type=refresh_token client_id='claude-abc' status=401" in caplog.text
    assert _SECRET not in caplog.text
    assert [r.levelno for r in caplog.records if "oauth_token_request" in r.message] == [logging.WARNING]


def test_a_successful_token_call_is_info_and_an_odd_grant_is_not_echoed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(_app({"/token": 200}))
    with caplog.at_level(logging.INFO, logger="vaquill_mcp.oauth"):
        client.post("/token", data={"grant_type": "authorization_code", "code": _SECRET})
        client.post("/token", data={"grant_type": "x\nforged_line=1"})
        client.post("/token", data={})
    lines = [r for r in caplog.records if "oauth_token_request" in r.message]
    assert [r.levelno for r in lines] == [logging.INFO] * 3
    messages = [r.getMessage() for r in lines]
    assert "grant_type=authorization_code" in messages[0]
    assert "grant_type=other" in messages[1] and "forged_line" not in caplog.text
    assert "grant_type=absent" in messages[2]
    assert _SECRET not in caplog.text


def test_an_mcp_401_logs_whether_a_credential_was_held_and_its_shape(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(_app({"/mcp": 401}))
    jwt_like = "aaa.bbb.ccc"
    with caplog.at_level(logging.INFO, logger="vaquill_mcp.oauth"):
        client.post("/mcp")
        client.post("/mcp", headers={"Authorization": f"Bearer {jwt_like}"})
        client.post("/mcp", headers={"Authorization": "Bearer vq_key_" + "k" * 20})
        client.post("/mcp", headers={"Authorization": "Basic Zm9vOmJhcg=="})
    messages = [r.getMessage() for r in caplog.records if "mcp_unauthorized" in r.message]
    assert messages == [
        "mcp_unauthorized authorization_present=False credential_kind=none",
        "mcp_unauthorized authorization_present=True credential_kind=jwt",
        "mcp_unauthorized authorization_present=True credential_kind=vq_key",
        "mcp_unauthorized authorization_present=True credential_kind=other",
    ]
    assert jwt_like not in caplog.text and "Zm9vOmJhcg" not in caplog.text and "kkkkkkkk" not in caplog.text


def test_a_successful_mcp_call_and_other_paths_log_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(_app({"/mcp": 200, "/other": 401, "/token": 200}))
    with caplog.at_level(logging.INFO, logger="vaquill_mcp.oauth"):
        client.post("/mcp", headers={"Authorization": "Bearer a.b.c"})
        client.post("/other")
        client.get("/token")  # not a POST
    assert caplog.text == ""


def test_the_body_still_reaches_the_application(caplog: pytest.LogCaptureFixture) -> None:
    """The tee must not consume the form: the app downstream still reads every field."""
    seen: dict = {}

    async def echo(request):
        seen.update(await request.form())
        return JSONResponse({})

    app = Starlette(
        routes=[Route("/token", echo, methods=["POST"])],
        middleware=[Middleware(AuthDiagnosticsMiddleware)],
    )
    TestClient(app).post("/token", data={"grant_type": "refresh_token", "refresh_token": _SECRET})
    assert seen == {"grant_type": "refresh_token", "refresh_token": _SECRET}


@pytest.mark.parametrize(
    ("header", "kind"),
    [(None, "none"), ("", "none"), ("Bearer", "other"), ("bearer a.b.c", "jwt"), ("Bearer opaque", "other")],
)
def test_credential_kind(header: str | None, kind: str) -> None:
    assert _credential_kind(header) == kind


def test_the_real_app_logs_a_token_refusal_and_a_bare_mcp_401(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, _live_api: None  # noqa: F811
) -> None:
    """Through `remote_main.build_app`, so the middleware is proven to be mounted."""
    from vaquill_mcp.remote_main import build_app

    for name, value in _OAUTH_ENV.items():
        monkeypatch.setenv(name, value)
    with TestClient(build_app(), base_url="https://mcp.vaquill.ai") as client:
        with caplog.at_level(logging.INFO, logger="vaquill_mcp.oauth"):
            token = client.post(
                "/token",
                data={"grant_type": "refresh_token", "client_id": "nobody", "refresh_token": _SECRET},
            )
            mcp = client.post("/mcp", json={})
    assert token.status_code == 401 and mcp.status_code == 401
    assert "oauth_token_request grant_type=refresh_token client_id='nobody' status=401" in caplog.text
    assert "mcp_unauthorized authorization_present=False credential_kind=none" in caplog.text
    assert _SECRET not in caplog.text
