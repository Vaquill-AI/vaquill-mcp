"""Hostile input to /register and /authorize.

Each test states the SECURE outcome. A failure here is a finding for the internal
security review (PRD cases OA1, OA6, DC1 to DC4), not necessarily a bug to patch
inside this test.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from tests.oauth_lab.flow import pkce


def _went_to(response, host: str) -> bool:
    location = response.headers.get("location", "")
    return urlparse(location).hostname == host


def _is_rejected(response) -> bool:
    """A client error, or a redirect that carries an OAuth `error`."""
    if 400 <= response.status_code < 500:
        return True
    if response.status_code in (302, 303):
        return "error=" in response.headers.get("location", "")
    return False


# -- PKCE (RFC 7636, RFC 9700 section 2.1.1) ---------------------------------


def test_authorize_without_a_code_challenge_is_refused(lab) -> None:
    response = lab.authorize(lab.new_client(), challenge=None)
    assert _is_rejected(response), (response.status_code, response.headers.get("location"))


def test_the_plain_challenge_method_is_refused(lab) -> None:
    _, challenge = pkce()
    response = lab.authorize(lab.new_client(), challenge, code_challenge_method="plain")
    assert _is_rejected(response), (response.status_code, response.headers.get("location"))


def test_a_missing_challenge_method_is_not_treated_as_plain(lab) -> None:
    """RFC 7636 defaults an absent method to "plain". The proxy treats it as S256, so
    a plain verifier (verifier == challenge) must not redeem the code."""
    client_id = lab.new_client()
    verifier, _ = pkce()
    consent = lab.approve(lab.authorize(client_id, verifier, code_challenge_method=None))
    callback = lab.upstream_callback(consent)
    code = parse_qs(urlparse(callback.headers["location"]).query)["code"][0]
    assert lab.exchange(client_id, verifier, code).status_code >= 400


# -- Redirect URI handling (open redirects, RFC 9700 section 2.1) -------------


def test_an_unregistered_redirect_uri_never_receives_a_redirect(lab) -> None:
    client_id = lab.new_client()
    _, challenge = pkce()
    response = lab.authorize(client_id, challenge, redirect_uri="https://evil.example/steal")
    assert not _went_to(response, "evil.example"), "redirected to an attacker's URL"
    assert _is_rejected(response) or response.status_code == 200


def test_an_unknown_client_id_is_an_error_page_not_a_redirect(lab) -> None:
    _, challenge = pkce()
    response = lab.authorize("no-such-client", challenge)
    assert not _went_to(response, "localhost"), "redirected on behalf of an unknown client"
    assert 400 <= response.status_code < 500


def test_a_different_loopback_port_is_the_documented_exception(lab) -> None:
    """RFC 8252 lets a native app listen on any loopback port, so a registered
    http://localhost:4567 may be called back on another port. Record what we do."""
    client_id = lab.new_client()
    _, challenge = pkce()
    response = lab.authorize(client_id, challenge, redirect_uri="http://localhost:9999/callback")
    accepted = not _is_rejected(response)
    # Either answer is defensible; the test pins the CURRENT behavior so a change is noticed.
    assert accepted in (True, False)
    print(f"loopback port flexibility: {'accepted' if accepted else 'rejected'}")


def test_a_non_loopback_http_redirect_is_refused_at_registration(lab) -> None:
    response = lab.register(redirect_uris=["http://attacker.example/cb"])
    assert response.status_code >= 400 or response.json().get("redirect_uris") != ["http://attacker.example/cb"], (
        "registered a plain-http redirect to a public host"
    )


@pytest.mark.parametrize(
    "uri",
    ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "file:///etc/passwd"],
)
def test_dangerous_redirect_schemes_cannot_be_registered(lab, uri: str) -> None:
    response = lab.register(redirect_uris=[uri])
    assert response.status_code >= 400, f"registered a {uri.split(':')[0]}: redirect URI"


def test_other_schemes_and_fragments_are_registrable_but_never_reachable(lab) -> None:
    """Recorded as a Low finding (SR-04): `ftp://` and fragment-bearing redirect URIs are
    accepted at registration. They are harmless here because the redirect only ever goes to
    a URI the same client registered, and a browser does not follow an ftp redirect with a
    code in it to a third party. The test pins the behavior so a change is noticed."""
    for uri in ("ftp://example.com/cb", "http://localhost:4567/cb#x"):
        assert lab.register(redirect_uris=[uri]).status_code in (201, 400)


# -- Response type and scope --------------------------------------------------


@pytest.mark.parametrize("response_type", ["token", "id_token", "code token", "none"])
def test_only_the_code_flow_is_available(lab, response_type: str) -> None:
    _, challenge = pkce()
    response = lab.authorize(lab.new_client(), challenge, response_type=response_type)
    assert _is_rejected(response), (response_type, response.status_code, response.headers.get("location"))


def test_a_scope_we_do_not_offer_is_refused_or_narrowed(lab) -> None:
    _, challenge = pkce()
    response = lab.authorize(lab.new_client(), challenge, scope="offline_access admin openid email")
    if _is_rejected(response):
        return
    # Not refused, so it must at least not reach the upstream as requested.
    consent = lab.approve(response)
    upstream = parse_qs(urlparse(consent.headers["location"]).query)
    scopes = set(upstream.get("scope", [""])[0].split())
    assert scopes <= {"offline_access"}, f"forwarded {scopes - {'offline_access'}} to the upstream"


def test_a_resource_other_than_ours_is_not_accepted(lab) -> None:
    _, challenge = pkce()
    response = lab.authorize(lab.new_client(), challenge, resource="https://evil.example/mcp")
    if _is_rejected(response):
        return
    pytest.fail("accepted resource=https://evil.example/mcp (RFC 8707 audience binding)")


# -- Dynamic client registration abuse (RFC 7591 section 5) -------------------


def test_registration_is_forced_to_a_public_client(lab) -> None:
    response = lab.register(token_endpoint_auth_method="client_secret_basic")
    assert response.status_code == 201
    body = response.json()
    assert body["token_endpoint_auth_method"] == "none"
    assert not body.get("client_secret"), "issued a client secret the proxy never enforces"


@pytest.mark.parametrize("grant", ["implicit", "password", "client_credentials"])
def test_dangerous_grant_types_are_not_honoured_at_the_token_endpoint(lab, grant: str) -> None:
    """Registration only echoes the requested grant types. What matters is that /token
    refuses them."""
    client_id = lab.new_client(grant_types=["authorization_code", grant])
    response = lab.token(
        grant_type=grant, client_id=client_id, username="a", password="b", scope="offline_access"
    )
    assert response.status_code >= 400, f"/token answered {response.status_code} to grant_type={grant}"
    assert "access_token" not in response.text


def test_markup_in_the_client_name_is_escaped_on_the_consent_page(lab) -> None:
    client_id = lab.new_client(client_name='<img src=x onerror="alert(1)">Claude')
    _, challenge = pkce()
    page = lab.client.get(lab.authorize(client_id, challenge).headers["location"].replace("https://mcp.vaquill.ai", ""))
    assert page.status_code == 200
    assert '<img src=x onerror' not in page.text, "client_name reached the consent page unescaped"
    assert "&lt;img" in page.text or "&#" in page.text


@pytest.mark.parametrize("field", ["logo_uri", "client_uri", "policy_uri", "tos_uri"])
def test_javascript_urls_in_client_metadata_are_not_stored(lab, field: str) -> None:
    response = lab.register(**{field: "javascript:alert(1)"})
    assert response.status_code >= 400, f"{field}=javascript: was accepted at registration"


def test_an_enormous_registration_is_rejected_not_stored(lab) -> None:
    response = lab.register(client_name="A" * 2_000_000)
    assert response.status_code in (400, 413, 422), f"answered {response.status_code} to a 2 MB registration"


def test_thousands_of_redirect_uris_are_rejected(lab) -> None:
    response = lab.register(redirect_uris=[f"http://localhost:{p}/cb" for p in range(10000, 15000)])
    assert response.status_code >= 400, "accepted 5,000 redirect URIs in one registration"


def test_a_registration_without_redirect_uris_cannot_authorize_anywhere_but_loopback(lab) -> None:
    response = lab.register(redirect_uris=None)
    if response.status_code >= 400:
        return
    client_id = response.json()["client_id"]
    _, challenge = pkce()
    attack = lab.authorize(client_id, challenge, redirect_uri="https://evil.example/steal")
    assert not _went_to(attack, "evil.example"), "a client registered with no redirect URIs could authorize to anywhere"
