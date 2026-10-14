"""The lab itself: a correct sign-in must succeed end to end.

Every abuse test in this folder is only meaningful if the honest path works here,
because a lab that rejects everything would "pass" every attack.
"""

from urllib.parse import parse_qs, urlparse


def test_an_honest_sign_in_ends_with_working_tokens(lab) -> None:
    client_id, verifier, code, callback = lab.sign_in()
    assert callback.status_code in (302, 303), callback.text[:300]
    redirect = urlparse(callback.headers["location"])
    assert f"{redirect.scheme}://{redirect.netloc}{redirect.path}" == "http://localhost:4567/callback"
    query = parse_qs(redirect.query)
    assert query["state"] == ["client-state-1"], "the client's own state must come back unchanged"
    assert query["iss"] == ["https://mcp.vaquill.ai/"], "RFC 9207 iss on the authorization response"
    assert code

    token = lab.exchange(client_id, verifier, code)
    assert token.status_code == 200, token.text
    body = token.json()
    assert body["token_type"].lower() == "bearer"
    assert body["access_token"] and body["refresh_token"]
