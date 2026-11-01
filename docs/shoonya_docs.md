# shoonya_docs.md

# Shoonya API Documentation (Unified & Validated)

Version: 2026-10-29

Sources:
- Official Shoonya API Documentation
- Manual Authentication (OAuth)
- Authentication Overview
- Authentication Flow Overview
- Quick Start
- Existing project integration research

This document intentionally excludes unsupported assumptions, legacy login behavior, and unverified SDK folklore. When documentation and historical implementations disagree, the official Shoonya documentation takes precedence.

---

# 1. Overview

Shoonya provides:

- REST APIs
- WebSocket Market Data APIs
- OAuth 2.0 Authentication

Base REST URL:

```text
https://api.shoonya.com/NorenWClientAPI/
```

WebSocket URL:

```text
wss://api.shoonya.com/NorenWSAPI/
```

Authentication Method:

```text
OAuth 2.0 Authorization Code Flow
```

OAuth is currently the only documented authentication mechanism.

---

# 2. Authentication

## 2.1 OAuth Flow

Shoonya uses an OAuth authorization-code exchange.

Flow:

```text
Application
    │
    ▼
Redirect user to Shoonya Login
    │
    ▼
User logs in
    │
    ▼
Redirect back with auth code
    │
    ▼
GenAcsTok
    │
    ▼
Access Token Returned
```

Your application never sees:

- User password
- OTP
- TOTP

Those are entered directly on Shoonya's login page.

---

# 2.2 Prerequisites

Before OAuth works:

- API access enabled
- Client ID generated
- Secret Code generated
- Redirect URL configured
- IP Address whitelisted

The Secret Code is available from:

```text
Profile
 └─ API Key Generation
      └─ Secret Code
```

Treat the Secret Code exactly like a password.

---

# 2.3 Step 1 – Authorization Request

Redirect user to:

```text
https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=<CLIENT_ID>
```

Example:

```text
https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=FN248368_U
```

---

# 2.4 Step 2 – Capture Authorization Code

After successful login:

```text
http://your-redirect-url/?code=<AUTH_CODE>
```

Extract only:

```text
AUTH_CODE
```

The authorization code:

- is short-lived
- can only be exchanged once

---

# 2.5 Step 3 – Exchange Code For Access Token

Endpoint:

```text
POST https://api.shoonya.com/NorenWClientAPI/GenAcsTok
```

Content-Type:

```text
text/plain
```

Payload:

```text
jData={
  "code":"<AUTH_CODE>",
  "checksum":"<SHA256_HASH>"
}
```

---

# 2.6 Checksum Generation

Official Formula:

```text
SHA256(
    client_id +
    secret_code +
    auth_code
)
```

Python:

```python
import hashlib

checksum = hashlib.sha256(
    (client_id + secret_code + auth_code).encode("utf-8")
).hexdigest()
```

Checksum output:

```text
64-character lowercase hexadecimal string
```

Example:

```python
checksum = hashlib.sha256(
    f"{client_id}{secret_code}{auth_code}".encode("utf-8")
).hexdigest()
```

---

# 2.7 GenAcsTok Example

```bash
curl -X POST \
  https://api.shoonya.com/NorenWClientAPI/GenAcsTok \
  -H "Content-Type: text/plain" \
  -d 'jData={"code":"AUTH_CODE","checksum":"HASH"}'
```

Successful response:

```json
{
  "stat":"Ok",
  "susertoken":"ACCESS_TOKEN",
  "uid":"ABC1234",
  "actid":"ABC1234"
}
```

---

# 2.8 Access Token Usage

Every authenticated request:

```http
Authorization: Bearer <ACCESS_TOKEN>
```

Example:

```http
Authorization: Bearer eyJhbGci...
```

---

# 2.9 Token Lifetime

Access tokens are valid for:

```text
24 hours
```

Current documentation states:

```text
No token refresh endpoint exists.
```

After expiration:

```text
Run OAuth login again.
```

---

# 2.10 Important Compatibility Note (INVALID_VERIFIER)

Official OAuth docs show:

```json
{
  "code":"AUTH_CODE",
  "checksum":"HASH"
}
```

However, some working SDKs and production implementations send:

```json
{
  "uid":"USER_ID",
  "code":"AUTH_CODE",
  "checksum":"HASH"
}
```

If:

```text
checksum is correct
```

but Shoonya returns:

```text
INVALID_VERIFIER
```

investigate whether your account/API version expects:

```json
{
  "uid":"USER_ID",
  "code":"AUTH_CODE",
  "checksum":"HASH"
}
```

This is currently the highest-probability cause when:

- code is valid
- checksum is correct
- payload shape is accepted

but verification still fails.

---

# 3. Request Format

Authenticated API calls generally use:

```text
Authorization: Bearer <ACCESS_TOKEN>
```

and endpoint-specific payloads.

Always consult the endpoint documentation before assuming:

```text
jData/jKey
```

because newer OAuth documentation places primary emphasis on:

```text
Bearer token authentication
```

---

# 4. WebSocket Market Data

Endpoint:

```text
wss://api.shoonya.com/NorenWSAPI/
```

---

## 4.1 Connection Frame

```json
{
  "t":"a",
  "uid":"USER_ID",
  "actid":"ACCOUNT_ID",
  "source":"API",
  "accesstoken":"ACCESS_TOKEN"
}
```

---

## 4.2 Subscribe

```json
{
  "t":"t",
  "k":"NSE|22#BSE|508123"
}
```

Format:

```text
EXCHANGE|TOKEN
```

Multiple instruments:

```text
#
```

separated.

Example:

```text
NSE|22#NSE|2885#BSE|508123
```

---

## 4.3 Unsubscribe

```json
{
  "t":"u",
  "k":"NSE|22"
}
```

---

# 5. Feed Message Types

## tk

Full Snapshot

First message received after subscribe.

Example:

```json
{
  "t":"tk"
}
```

---

## tf

Incremental Update

Contains only changed fields.

Example:

```json
{
  "t":"tf"
}
```

---

# 6. Common Feed Fields

| Field | Meaning |
|---------|---------|
| lp | Last traded price |
| o | Open |
| h | High |
| l | Low |
| c | Close |
| bp1 | Best bid |
| bq1 | Best bid quantity |
| sp1 | Best ask |
| sq1 | Best ask quantity |
| v | Volume |
| oi | Open interest |
| poi | Previous open interest |
| toi | Total open interest |
| ts | Trading symbol |
| ft | Feed timestamp |
| ls | Lot size |
| pp | Price precision |
| pc | Previous close |
| ap | Average price |

---

# 7. WebSocket Reconnect Rules

After reconnect:

1. Re-authenticate.
2. Open websocket again.
3. Send connect frame.
4. Re-subscribe.
5. Wait for fresh tk snapshots.

Do not trust previous local state after reconnect until a new snapshot arrives.

---

# 8. Symbol Master Files

No authentication required.

Available downloads:

```text
https://api.shoonya.com/NSE_symbols.txt.zip
https://api.shoonya.com/NFO_symbols.txt.zip
https://api.shoonya.com/BSE_symbols.txt.zip
https://api.shoonya.com/BFO_symbols.txt.zip
https://api.shoonya.com/CDS_symbols.txt.zip
https://api.shoonya.com/MCX_symbols.txt.zip
```

These are the preferred source for:

- token lookup
- contract metadata
- instrument universe generation

---

# 9. Key Market Data Endpoints

## GetQuotes

Real-time touchline quote.

---

## SearchScrip

Symbol lookup.

Important:

```text
Do not blindly use first match.
```

Verify exact trading symbol match.

Example:

```text
SBIN
```

may return multiple entries.

Prefer:

```text
SBIN-EQ
```

exact match.

---

## GetSecurityInfo

Returns:

- lot size
- tick size
- precision
- contract information

---

## GetOptionChain

Option chain retrieval.

---

## TPSeries

Intraday historical candles.

Supported intervals:

```text
1 to 240 minutes
```

---

## EODChartData

Daily candles.

---

## GetIndexList

Index information.

---

## GetLinkedScrips

Related instruments.

---

## ExchStatus

Exchange status.

---

# 10. Error Handling

Success:

```json
{
  "stat":"Ok"
}
```

Failure:

```json
{
  "stat":"Not_Ok",
  "emsg":"Error Message"
}
```

Do not rely solely on:

```text
HTTP status code
```

Evaluate:

```text
stat
```

in the response body.

---

# 11. OAuth Debug Checklist

If GenAcsTok returns:

```text
INVALID_VERIFIER
```

Verify:

✅ Client ID is correct

✅ Secret Code is correct

✅ Auth code is newly captured

✅ Auth code is not reused

✅ Auth code contains no spaces

✅ Auth code contains no newline

✅ SHA256 is lowercase hex

✅ UTF-8 encoding used

✅ Content-Type = text/plain

✅ Body starts with:

```text
jData=
```

✅ IP whitelist is correct

✅ Test adding:

```json
{
  "uid":"USER_ID",
  "code":"AUTH_CODE",
  "checksum":"HASH"
}
```

if checksum validation fails despite a correct hash.

---

# 12. Recommended Environment Variables

```env
SHOONYA_CLIENT_ID=
SHOONYA_USER_ID=
SHOONYA_SECRET_CODE=
SHOONYA_ACCESS_TOKEN=
```

Avoid ambiguous names such as:

```env
SHOONYA_API_SECRET
```

because Shoonya documentation refers specifically to:

```text
Secret Code
```

used in checksum generation.

---

# 13. Verified Implementation Requirements

For modern Shoonya OAuth integrations:

✅ OAuth only

✅ Authorization Code flow

✅ SHA256(client_id + secret_code + auth_code)

✅ Content-Type: text/plain

✅ Access token in Bearer header

✅ WebSocket authentication via accesstoken

✅ Symbol master ZIP downloads supported

✅ Token lifetime: 24 hours

✅ Re-login required after expiration

✅ No token refresh endpoint documented

✅ Investigate uid in GenAcsTok when INVALID_VERIFIER occurs

---
End of Document