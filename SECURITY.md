# Security

## Reporting a vulnerability

Email contact@vaquill.ai.
Please do not open a public issue for a security problem.
The full policy, scope and safe-harbor terms are at https://www.vaquill.ai/security/vulnerability-disclosure.
We acknowledge a report within 72 hours.
Critical issues are patched within 7 days, high severity within 30 days, and medium and low severity within 90 days.

## What this package is

`vaquill-mcp` is an MCP server (and a hosted one at `https://mcp.vaquill.ai/mcp`) that gives AI assistants read-only access to US primary law through the Vaquill Data API.
It authenticates with OAuth 2.1 (authorization code with PKCE) or a Vaquill API key.
Calls are metered in credits against the signed-in account.

## Design notes that matter to a security review

- **Read-only.** Every published tool is annotated `readOnlyHint: true` and `destructiveHint: false`. No tool creates, changes or deletes anything. Annotations describe behavior and are not enforcement, so do not rely on them as a control.
- **No token passthrough.** The OAuth access token a client presents is validated here and is never forwarded to the Data API. The server resolves the signed-in user to a Vaquill API key on the server side and uses that.
- **Returned text is untrusted.** The tools return text written by third parties: legislatures, agencies, and the parties whose filings agency decisions quote. We sanitise HTML and restrict official-source links to government hosts. We do not filter wording for instructions, so a sentence inside a section that tells a model to do something is part of the document, not a request from the user. The server sends an `instructions` note to that effect. `openWorldHint: false` describes what a tool can reach, not whether its output is trustworthy.
- **Tool definitions are pinned.** The text a model reads (tool and parameter descriptions, prompts, resources) is snapshotted in `tests/fixtures/model_facing_text.json`, and CI fails when it changes, so a change in the backend's API document cannot silently change what agents are told.
- **Prompt arguments are bounded.** Short arguments are validated, and a passage pasted into the `cite_check` prompt is fenced and labelled as data.
- **Secrets stay out of logs.** API keys that appear in a request path are redacted from the server's access logs.

## Known limitations

- The `https://mcp.vaquill.ai/s/{api_key}` form puts an API key in the URL for clients that cannot send a header. It is redacted in our logs, but any proxy or client configuration that records URLs will see it. Prefer OAuth or the `Authorization` header.
- OAuth client registration is open (dynamic client registration and client ID metadata documents) and rate limited per address. A registered client is kept for 90 days and renewed each time it refreshes a token.
- Access tokens last about an hour. A refresh token lasts 30 days and is replaced on every use. `POST /revoke` (RFC 7009) ends a refresh token immediately. An access token that was already issued is not recalled and lapses on its own within the hour.
- This is a point-in-time statement about this repository. An internal security assessment is in progress; its summary will be linked here when it is published, and it will be labelled as internal, not an independent audit.
