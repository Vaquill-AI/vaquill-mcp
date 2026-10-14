"""A local replica of the OAuth sign-in, with a fake Supabase and no network.

The real proxy (`build_auth_provider`) runs in-process behind a Starlette test
client. The only thing faked is the upstream authorization server: its token
endpoint and its JWKS are answered by respx with an access token signed by a key
generated here, so the proxy validates it exactly as it validates Supabase's.

Every request in this lab stays on this machine. That is the point: OAuth abuse
tests (replayed codes, hostile redirect URIs, malformed registrations) can be run
as often as needed without writing one record to production.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import httpx  # respx builds mock replies from the classic httpx classes, even over httpx2
from authlib.jose import JsonWebKey, jwt
from starlette.applications import Starlette
from starlette.testclient import TestClient

from tests.test_oauth import _OAUTH_ENV
from vaquill_mcp.oauth import build_auth_provider
from vaquill_mcp.registration_guard import RegistrationRateLimit

PUBLIC = "https://mcp.vaquill.ai"
REDIRECT = "http://localhost:4567/callback"
RESOURCE = f"{PUBLIC}/mcp"
UPSTREAM_ISSUER = _OAUTH_ENV["VAQUILL_OAUTH_UPSTREAM_ISSUER"]
UPSTREAM_TOKEN_URL = _OAUTH_ENV["VAQUILL_OAUTH_TOKEN_URL"]
UPSTREAM_JWKS_URL = _OAUTH_ENV["VAQUILL_OAUTH_UPSTREAM_JWKS_URI"]


def pkce() -> tuple[str, str]:
    """A (verifier, S256 challenge) pair."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@dataclass
class FakeUpstream:
    """Stands in for Supabase: signs access tokens and answers the token endpoint."""

    refresh_counter: int = 0
    issued: list[dict] = field(default_factory=list)
    refuse_refresh: bool = False

    def __post_init__(self) -> None:
        self.key = JsonWebKey.generate_key("EC", "P-256", is_private=True)
        self.kid = "lab-key-1"

    @property
    def jwks(self) -> dict:
        public = self.key.as_dict(is_private=False)
        public.update({"kid": self.kid, "alg": "ES256", "use": "sig"})
        return {"keys": [public]}

    def access_token(self, sub: str = "lab-user-1", ttl: int = 3600) -> str:
        now = int(time.time())
        claims = {
            "iss": UPSTREAM_ISSUER, "aud": "authenticated", "sub": sub,
            "iat": now, "exp": now + ttl, "role": "authenticated",
        }
        return jwt.encode({"alg": "ES256", "kid": self.kid}, claims, self.key).decode()

    def install(self, respx_mock) -> None:
        # Not every test reaches the token endpoint or the JWKS; an unused route is fine.
        respx_mock._assert_all_called = False
        respx_mock.get(UPSTREAM_JWKS_URL).mock(return_value=httpx.Response(200, json=self.jwks))

        def token_endpoint(request: httpx.Request) -> httpx.Response:
            form = parse_qs(request.content.decode())
            grant = form.get("grant_type", [""])[0]
            if grant == "refresh_token" and self.refuse_refresh:
                return httpx.Response(400, json={"code": 400, "error_code": "refresh_token_already_used", "msg": "Invalid Refresh Token: Already Used"})
            self.refresh_counter += 1
            body = {
                "access_token": self.access_token(), "token_type": "bearer", "expires_in": 3600,
                "refresh_token": f"upstream-refresh-{self.refresh_counter}", "scope": "offline_access",
            }
            self.issued.append({"grant": grant, **body})
            return httpx.Response(200, json=body)

        respx_mock.post(UPSTREAM_TOKEN_URL).mock(side_effect=token_endpoint)


class Lab:
    """The proxy, a client for it, and helpers for each step of the flow."""

    def __init__(self, monkeypatch, respx_mock) -> None:
        for name, value in _OAUTH_ENV.items():
            monkeypatch.setenv(name, value)
        self.auth = build_auth_provider()
        assert self.auth is not None
        self.proxy = self.auth.server
        self.app = Starlette(routes=self.auth.get_routes(mcp_path="/mcp"))
        # The production stack puts the registration guard in front of the proxy's routes
        # (remote_main.py). A high count limit keeps the rate limit out of these tests; the
        # size caps are the defaults.
        guarded = RegistrationRateLimit(self.app, limit=100_000)
        self.client = TestClient(guarded, base_url=PUBLIC, follow_redirects=False)
        self.upstream = FakeUpstream()
        self.upstream.install(respx_mock)

    # -- steps ---------------------------------------------------------------
    def register(self, **overrides) -> httpx.Response:
        body = {
            "redirect_uris": [REDIRECT], "client_name": "Lab Client",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        }
        body.update(overrides)
        return self.client.post("/register", json={k: v for k, v in body.items() if v is not None})

    def new_client(self, **overrides) -> str:
        response = self.register(**overrides)
        assert response.status_code == 201, response.text
        return response.json()["client_id"]

    def authorize(self, client_id: str, challenge: str | None = None, **overrides) -> httpx.Response:
        params = {
            "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "state": "client-state-1", "scope": "offline_access", "resource": RESOURCE,
        }
        params.update(overrides)
        return self.client.get("/authorize", params={k: v for k, v in params.items() if v is not None})

    def approve(self, authorize_response: httpx.Response, *, approve: bool = True) -> httpx.Response:
        """Open the consent page and submit it, as a browser would."""
        txn_url = authorize_response.headers["location"].replace(PUBLIC, "")
        page = self.client.get(txn_url)
        assert page.status_code == 200, page.text[:200]
        form = {
            "txn_id": re.search(r'name="txn_id" value="([^"]*)"', page.text).group(1),
            "csrf_token": re.search(r'name="csrf_token" value="([^"]*)"', page.text).group(1),
            "submit": "true",
            "action": "approve" if approve else "deny",
        }
        return self.client.post("/consent", data=form)

    def upstream_callback(self, consent_response: httpx.Response, code: str = "upstream-code") -> httpx.Response:
        """The browser coming back from the (fake) upstream sign-in."""
        upstream_url = urlparse(consent_response.headers["location"])
        state = parse_qs(upstream_url.query)["state"][0]
        return self.client.get("/auth/callback", params={"code": code, "state": state})

    def token(self, **form) -> httpx.Response:
        return self.client.post("/token", data=form)

    # -- the whole flow ------------------------------------------------------
    def sign_in(self, client_id: str | None = None):
        """Register, authorize, consent, call back. Returns (client_id, verifier, code, redirect)."""
        client_id = client_id or self.new_client()
        verifier, challenge = pkce()
        consent = self.approve(self.authorize(client_id, challenge))
        callback = self.upstream_callback(consent)
        redirect = urlparse(callback.headers["location"])
        query = parse_qs(redirect.query)
        return client_id, verifier, query.get("code", [None])[0], callback

    def exchange(self, client_id: str, verifier: str, code: str, **overrides) -> httpx.Response:
        form = {
            "grant_type": "authorization_code", "client_id": client_id, "code": code,
            "code_verifier": verifier, "redirect_uri": REDIRECT, "resource": RESOURCE,
        }
        form.update(overrides)
        return self.token(**{k: v for k, v in form.items() if v is not None})
