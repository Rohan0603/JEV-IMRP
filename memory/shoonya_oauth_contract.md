---
name: shoonya-oauth-contract
description: Shoonya GenAcsTok OAuth contract per the 2026-10-26 docs, and where the Secret Code comes from
metadata:
  type: reference
---

## GenAcsTok contract (verified 2026-10-26 against shoonya.com/api-documentation/manual-authentication-oauth)

- `POST https://api.shoonya.com/NorenWClientAPI/GenAcsTok`
- `Content-Type: text/plain`
- Body: `jData=<JSON payload>` — NOT bare JSON, NOT URL-encoded. Raw JSON after `jData=`.
- Payload carries exactly **two** fields:
  - `code` — the authorization code from the OAuth redirect
  - `checksum` — `SHA256(client_id + secret_key + auth_code)`
- `client_id` = the Shoonya user ID (e.g. `FN248368_U`), NOT the API key.
- Response: `{"stat":"Ok","susertoken":"<AccessToken>","uid":"...","actid":"..."}`

## Where the Secret Code comes from

From [IP Whitelisting Guide](https://shoonya.com/api-documentation/ip-whitelisting):

1. Log in to your Shoonya trading account (web).
2. Profile icon → **API Key Generation**.
3. The **Secret Code** is a separate, revealable value (eye icon) — it is **not** the same as the API key.
4. Treat it like a password; never commit it.

**The `.env` currently has `SHOONYA_API_KEY == SHOONYA_API_SECRET`** (both `XxhOrpPxqepg…`), which is wrong — the Secret Code is a distinct value from the API Key Generation screen.

## Scripts fixed

- `scripts/oauth_login_script.py` — payload now `jData={"code","checksum"}` per docs; checksum uses `uid + api_secret + code`.
- `scripts/final_automated_test.py` — `REPO_ROOT` was `parents[2]` (points at `C:\Users\ponna\Project`), fixed to `parents[1]`.

## Related

- [[live_integration_test_observations]]