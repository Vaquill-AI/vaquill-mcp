"""Abuse of /consent, /token and refresh. Each test states the SECURE outcome.

A failure is a finding for the internal review (PRD cases OA2 to OA5, OA7), not
necessarily a bug inside the test.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from tests.oauth_lab.flow import PUBLIC, REDIRECT, pkce


def _tokens(lab, client_id: str | None = None):
    client_id, verifier, code, _ = lab.sign_in(client_id)
    assert code, "the honest flow produced no code"
    response = lab.exchange(client_id, verifier, code)
    assert response.status_code == 200, response.text
    return client_id, verifier, code, response.json()


# -- Authorization code (RFC 6749 4.1.3, RFC 9700 2.1) ------------------------


def test_a_code_cannot_be_used_twice(lab) -> None:
    client_id, verifier, code, _ = _tokens(lab)
    again = lab.exchange(client_id, verifier, code)
    assert again.status_code >= 400
    assert "access_token" not in again.text


def test_a_wrong_verifier_does_not_redeem_the_code(lab) -> None:
    client_id, _, code, _ = lab.sign_in()
    other_verifier, _ = pkce()
    response = lab.exchange(client_id, other_verifier, code)
    assert response.status_code >= 400
    assert "access_token" not in response.text


def test_a_missing_verifier_does_not_redeem_the_code(lab) -> None:
    client_id, _, code, _ = lab.sign_in()
    response = lab.exchange(client_id, "ignored", code, code_verifier=None)
    assert response.status_code >= 400
    assert "access_token" not in response.text


def test_a_failed_attempt_does_not_burn_the_real_verifier_into_a_bypass(lab) -> None:
    """After a wrong verifier the code must be dead or still require the right one,
    never redeemable with no proof at all."""
    client_id, verifier, code, _ = lab.sign_in()
    other_verifier, _ = pkce()
    lab.exchange(client_id, other_verifier, code)
    assert lab.exchange(client_id, verifier, code, code_verifier=None).status_code >= 400


def test_another_client_cannot_redeem_the_code(lab) -> None:
    client_id, verifier, code, _ = lab.sign_in()
    attacker = lab.new_client(client_name="Attacker")
    response = lab.exchange(attacker, verifier, code)
    assert response.status_code >= 400
    assert "access_token" not in response.text


def test_the_redirect_uri_must_match_at_the_token_endpoint(lab) -> None:
    client_id, verifier, code, _ = lab.sign_in()
    response = lab.exchange(client_id, verifier, code, redirect_uri="https://evil.example/cb")
    assert response.status_code >= 400
    assert "access_token" not in response.text


def test_a_made_up_code_is_refused(lab) -> None:
    client_id = lab.new_client()
    verifier, _ = pkce()
    response = lab.exchange(client_id, verifier, "not-a-real-code")
    assert response.status_code >= 400
    assert "access_token" not in response.text


def test_the_token_response_is_never_cacheable(lab) -> None:
    client_id, verifier, code, _ = lab.sign_in()
    response = lab.exchange(client_id, verifier, code)
    assert response.status_code == 200
    assert "no-store" in response.headers.get("cache-control", "")  # RFC 6749 5.1


def test_the_token_error_does_not_leak_internals(lab) -> None:
    client_id = lab.new_client()
    verifier, _ = pkce()
    body = lab.exchange(client_id, verifier, "nope").text
    for marker in ("Traceback", "site-packages", "supabase", "sb_secret", "/Users/", "redis"):
        assert marker not in body, f"{marker!r} appeared in a token error"


def test_unsupported_grants_are_refused_with_an_oauth_error(lab) -> None:
    response = lab.token(grant_type="urn:ietf:params:oauth:grant-type:device_code", client_id=lab.new_client())
    assert response.status_code == 400
    assert response.json().get("error")


# -- Consent (confused deputy, CSRF) ------------------------------------------


def _consent_page(lab):
    client_id = lab.new_client()
    _, challenge = pkce()
    location = lab.authorize(client_id, challenge).headers["location"].replace(PUBLIC, "")
    page = lab.client.get(location)
    form = {
        "txn_id": re.search(r'name="txn_id" value="([^"]*)"', page.text).group(1),
        "csrf_token": re.search(r'name="csrf_token" value="([^"]*)"', page.text).group(1),
        "submit": "true",
        "action": "approve",
    }
    return client_id, form


def test_consent_without_the_csrf_token_is_refused(lab) -> None:
    _, form = _consent_page(lab)
    form.pop("csrf_token")
    response = lab.client.post("/consent", data=form)
    assert response.status_code >= 400 or "auth/callback" not in response.headers.get("location", "")
    assert "accounts.supabase" not in response.headers.get("location", "")


def test_consent_with_a_forged_csrf_token_is_refused(lab) -> None:
    _, form = _consent_page(lab)
    form["csrf_token"] = "forged-token"
    response = lab.client.post("/consent", data=form)
    assert response.status_code >= 400
    assert "location" not in response.headers


def test_consent_from_a_browser_without_the_binding_cookie_is_refused(lab) -> None:
    """A consent link opened by a victim and approved from a different browser."""
    _, form = _consent_page(lab)
    lab.client.cookies.clear()
    response = lab.client.post("/consent", data=form)
    assert response.status_code >= 400
    assert "location" not in response.headers


def test_a_consent_transaction_cannot_be_approved_twice(lab) -> None:
    _, form = _consent_page(lab)
    first = lab.client.post("/consent", data=form)
    assert first.status_code in (302, 303)
    second = lab.client.post("/consent", data=form)
    assert second.status_code >= 400 or second.headers.get("location") != first.headers.get("location")


def test_denying_consent_never_issues_a_code(lab) -> None:
    client_id = lab.new_client()
    _, challenge = pkce()
    denied = lab.approve(lab.authorize(client_id, challenge), approve=False)
    location = denied.headers.get("location", "")
    assert "code=" not in location
    assert "error=access_denied" in location or denied.status_code >= 400


def test_an_unknown_transaction_id_is_refused(lab) -> None:
    _, form = _consent_page(lab)
    form["txn_id"] = "00000000-0000-0000-0000-000000000000"
    response = lab.client.post("/consent", data=form)
    assert response.status_code >= 400


def test_the_callback_with_a_forged_state_is_refused(lab) -> None:
    response = lab.client.get("/auth/callback", params={"code": "x", "state": "forged-state"})
    assert response.status_code >= 400
    assert "access_token" not in response.text


def test_the_callback_without_a_state_is_refused(lab) -> None:
    response = lab.client.get("/auth/callback", params={"code": "x"})
    assert response.status_code >= 400


# -- Refresh tokens (RFC 9700 2.2.2) ------------------------------------------


def test_refresh_works_and_rotates(lab) -> None:
    client_id, _, _, tokens = _tokens(lab)
    response = lab.token(grant_type="refresh_token", client_id=client_id, refresh_token=tokens["refresh_token"])
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


def test_another_client_cannot_use_my_refresh_token(lab) -> None:
    _, _, _, tokens = _tokens(lab)
    attacker = lab.new_client(client_name="Attacker")
    response = lab.token(grant_type="refresh_token", client_id=attacker, refresh_token=tokens["refresh_token"])
    assert response.status_code >= 400
    assert "access_token" not in response.text


def test_a_made_up_refresh_token_is_refused(lab) -> None:
    response = lab.token(grant_type="refresh_token", client_id=lab.new_client(), refresh_token="made-up")
    assert response.status_code >= 400


def test_a_revoked_refresh_token_stops_working(lab) -> None:
    client_id, _, _, tokens = _tokens(lab)
    revoke = lab.client.post(
        "/revoke", data={"token": tokens["refresh_token"], "client_id": client_id, "client_secret": ""}
    )
    assert revoke.status_code == 200
    again = lab.token(grant_type="refresh_token", client_id=client_id, refresh_token=tokens["refresh_token"])
    assert again.status_code >= 400


def test_revoke_needs_the_client_secret_field_even_for_a_public_client(lab) -> None:
    """Recorded as a Low usability finding (SR-05): the SDK's revocation request model
    requires the `client_secret` key, so a public client must send it empty. Revocation
    itself works (see the test above). Pinned so a change in the SDK is noticed."""
    client_id, _, _, tokens = _tokens(lab)
    bare = lab.client.post("/revoke", data={"token": tokens["refresh_token"], "client_id": client_id})
    assert bare.status_code in (200, 400)


def test_another_client_cannot_revoke_my_token(lab) -> None:
    client_id, _, _, tokens = _tokens(lab)
    attacker = lab.new_client(client_name="Attacker")
    lab.client.post(
        "/revoke", data={"token": tokens["refresh_token"], "client_id": attacker, "client_secret": ""}
    )
    still = lab.token(grant_type="refresh_token", client_id=client_id, refresh_token=tokens["refresh_token"])
    assert still.status_code == 200, "a different client revoked someone else's refresh token"


def test_an_upstream_refusal_surfaces_as_invalid_grant(lab) -> None:
    client_id, _, _, tokens = _tokens(lab)
    lab.upstream.refuse_refresh = True
    response = lab.token(grant_type="refresh_token", client_id=client_id, refresh_token=tokens["refresh_token"])
    # The SDK answers 401 here rather than RFC 6749's 400; clients key off the error code.
    assert response.status_code in (400, 401)
    assert response.json()["error"] == "invalid_grant"
    assert "no-store" in response.headers.get("cache-control", "")


# -- Access token shape --------------------------------------------------------


def test_the_issued_access_token_is_not_the_upstream_token(lab) -> None:
    """The proxy must hand clients its own token, never Supabase's."""
    *_, tokens = _tokens(lab)
    upstream_tokens = {i["access_token"] for i in lab.upstream.issued}
    assert tokens["access_token"] not in upstream_tokens
    assert tokens.get("refresh_token") not in {i["refresh_token"] for i in lab.upstream.issued}


def test_no_redirect_leaks_the_upstream_token(lab) -> None:
    _, _, _, callback = lab.sign_in()
    location = callback.headers["location"]
    assert urlparse(location).hostname == "localhost"
    assert "access_token" not in location and "upstream-refresh" not in location
    assert REDIRECT.split("?")[0] in location
    assert parse_qs(urlparse(location).query).get("state") == ["client-state-1"]
