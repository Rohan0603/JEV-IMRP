#!/usr/bin/env python3

"""OAuth login for the Shoonya API.

Runs the documented 3-step OAuth flow (CLAUDE.md: OAuth is the only supported
login; NorenRestApiPy 0.0.x ships no OAuth support, so the token exchange is a
direct POST to GenAcsTok on the NorenWClientAPI/ base):

  1. serve localhost:8080 and open the authorize URL,
  2. capture the ?code= redirect,
  3. POST GenAcsTok with jData={code, checksum}, checksum =
     SHA256(client_id + secret_key + auth_code),
  4. print the susertoken and write it into .env as SHOONYA_ACCESS_TOKEN.

The token exchange tries both documented payload shapes - the 2-field
``{code, checksum}`` form from manual-authentication-oauth and the 4-field
``{uid, code, appkey, apikey}`` form from the Postman collection - because the
docs describe the endpoint both ways.

Research-only. Credentials come from the local ``.env`` (git-ignored); the
token is never printed in full.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
REST_BASE = "https://api.shoonya.com/NorenWClientAPI/"
OAUTH_AUTHORIZE = "https://api.shoonya.com/OAuthlogin/authorize/oauth"
REDIRECT_HOST = "localhost"
REDIRECT_PORT = 8080


def _load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    for key in ("SHOONYA_USER_ID", "SHOONYA_CLIENT_ID", "SHOONYA_API_KEY", "SHOONYA_API_SECRET", "SHOONYA_SECRET_CODE"):
        if os.environ.get(key):
            values[key] = os.environ[key]

    # Normalize/fallback aliases per docs (§12)
    if not values.get("SHOONYA_USER_ID") and values.get("SHOONYA_CLIENT_ID"):
        values["SHOONYA_USER_ID"] = values["SHOONYA_CLIENT_ID"]
    if not values.get("SHOONYA_CLIENT_ID") and values.get("SHOONYA_USER_ID"):
        values["SHOONYA_CLIENT_ID"] = values["SHOONYA_USER_ID"]
    if not values.get("SHOONYA_SECRET_CODE") and values.get("SHOONYA_API_SECRET"):
        values["SHOONYA_SECRET_CODE"] = values["SHOONYA_API_SECRET"]
    if not values.get("SHOONYA_API_SECRET") and values.get("SHOONYA_SECRET_CODE"):
        values["SHOONYA_API_SECRET"] = values["SHOONYA_SECRET_CODE"]

    return values


class _CaptureHandler(BaseHTTPRequestHandler):
    auth_code: str | None = None

    def do_GET(self):  # noqa: N802 - http.server convention
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = params.get("code", [None])[0]
        if code:
            _CaptureHandler.auth_code = code
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h1>JEV-IMRP</h1>"
            b"<p>OAuth code captured. You may close this window.</p></body></html>"
        )

    def log_message(self, *_args):  # silence request logging
        pass


def _capture_auth_code(client_id: str, timeout_s: float = 300.0) -> str:
    server = HTTPServer((REDIRECT_HOST, REDIRECT_PORT), _CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    auth_url = f"{OAUTH_AUTHORIZE}?client_id={urllib.parse.quote(client_id)}"
    print(f"1. Opening browser to Shoonya OAuth login page:\n   {auth_url}")
    webbrowser.open(auth_url)

    print("2. Waiting for the OAuth redirect to localhost:8080 ...")
    waited = 0.0
    while _CaptureHandler.auth_code is None and waited < timeout_s:
        thread.join(timeout=0.5)
        waited += 0.5
    server.shutdown()

    if _CaptureHandler.auth_code is None:
        raise SystemExit("timed out waiting for the OAuth redirect")
    code = _CaptureHandler.auth_code
    print(f"3. Auth code captured ({len(code)} chars).")
    return code


def _checksum_variants(client_id: str, uid: str) -> list[tuple[str, str, str]]:
    """Candidate (checksum_client_id, payload_uid, label) tuples.

    Tests the hypothesis that UID and CLIENT_ID may differ, and that the
    checksum signing string uses CLIENT_ID while the payload uses UID (or vice
    versa).
    """
    bare_client_id = client_id[:-2] if client_id.endswith(("_U", "_C")) else client_id
    bare_uid = uid[:-2] if uid.endswith(("_U", "_C")) else uid

    variants = [
        # 1. client_id as-is, uid as-is
        (client_id, uid, f"client_id={client_id!r}, uid={uid!r}"),
        # 2. bare client_id, uid as-is
        (bare_client_id, uid, f"client_id={bare_client_id!r}, uid={uid!r}"),
        # 3. client_id as-is, bare uid
        (client_id, bare_uid, f"client_id={client_id!r}, uid={bare_uid!r}"),
        # 4. bare client_id, bare uid
        (bare_client_id, bare_uid, f"client_id={bare_client_id!r}, uid={bare_uid!r}"),
    ]
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for item in variants:
        key = (item[0], item[1])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _genacstok_payloads(client_id: str, uid: str, secret_code: str, api_key: str, code: str) -> list[tuple[bytes, str]]:
    """Build candidate GenAcsTok request bodies with descriptive labels."""
    candidates: list[tuple[bytes, str]] = []
    for c_id, p_uid, label in _checksum_variants(client_id, uid):
        raw = f"{c_id}{secret_code}{code}"
        checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()

        # Shape 1: 3-field {uid, code, checksum}
        body_3f = "jData=" + json.dumps({"uid": p_uid, "code": code, "checksum": checksum})
        candidates.append((body_3f.encode("utf-8"), f"3-field ({label}, checksum)"))

        # Shape 2: 2-field {code, checksum}
        body_2f = "jData=" + json.dumps({"code": code, "checksum": checksum})
        candidates.append((body_2f.encode("utf-8"), f"2-field ({label}, checksum)"))

        # Shape 3: 4-field Postman shape {uid, code, appkey, apikey}
        body_4f = "jData=" + json.dumps({"uid": p_uid, "code": code, "appkey": checksum, "apikey": api_key})
        candidates.append((body_4f.encode("utf-8"), f"4-field Postman ({label}, appkey)"))

    return candidates


def _exchange_for_token(client_id: str, uid: str, api_key: str, secret_code: str, code: str) -> tuple[str, str]:
    """POST GenAcsTok; returns (susertoken, account id)."""
    print(f"   debug: client_id   ={client_id!r}")
    print(f"   debug: uid         ={uid!r}")
    print(f"   debug: secret_code ={secret_code[:4]}...{secret_code[-4:]} (len={len(secret_code)})")
    print(f"   debug: auth_code   ={code!r}")

    last_error = ""
    payload_candidates = _genacstok_payloads(client_id, uid, secret_code, api_key, code)
    for index, (body, label) in enumerate(payload_candidates, start=1):
        print(f"   debug: [{index}/{len(payload_candidates)}] {label}")
        print(f"          sending body={body.decode('utf-8')}")
        req = urllib.request.Request(
            f"{REST_BASE}GenAcsTok",
            data=body,
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:200]}"
            print(f"          -> {last_error}")
            continue
        if payload.get("stat") == "Ok":
            token = payload.get("susertoken")
            actid = payload.get("actid") or uid
            if not token:
                last_error = f"no susertoken: keys={sorted(payload)}"
                print(f"          -> {last_error}")
                continue
            print(f"          -> SUCCESS!")
            return token, actid
        last_error = payload.get("emsg", str(payload))
        print(f"          -> {last_error}")
    hint = ""
    if "INVALID_IP" in last_error:
        hint = (
            "\n\nThe IP this request is coming from is not whitelisted. Log in to your "
            "Shoonya trading account -> profile icon -> API Key Generation -> enter your "
            "outbound IP under 'Primary IP Address' -> Update."
        )
    raise SystemExit(
        f"GenAcsTok failed (tried {len(payload_candidates)} payload formats): {last_error}{hint}"
    )


def _write_token_to_env(token: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("SHOONYA_ACCESS_TOKEN="):
            out.append(f"SHOONYA_ACCESS_TOKEN={token}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"SHOONYA_ACCESS_TOKEN={token}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    env = _load_env()
    client_id = env.get("SHOONYA_CLIENT_ID") or env.get("SHOONYA_USER_ID")
    uid = env.get("SHOONYA_USER_ID") or env.get("SHOONYA_CLIENT_ID")
    api_key = env.get("SHOONYA_API_KEY")
    secret_code = env.get("SHOONYA_SECRET_CODE") or env.get("SHOONYA_API_SECRET")
    missing = [
        name
        for name, val in (
            ("SHOONYA_CLIENT_ID/SHOONYA_USER_ID", client_id),
            ("SHOONYA_API_KEY", api_key),
            ("SHOONYA_SECRET_CODE/SHOONYA_API_SECRET", secret_code),
        )
        if not val
    ]
    if missing:
        print(f"missing credential(s): {missing}", file=sys.stderr)
        print("Add them to .env (git-ignored) or export them in the shell.", file=sys.stderr)
        return 1

    assert client_id and uid and api_key and secret_code
    code = _capture_auth_code(client_id)
    print("4. Exchanging auth code for an access token via GenAcsTok ...")
    token, actid = _exchange_for_token(client_id, uid, api_key, secret_code, code)
    _write_token_to_env(token)
    print(f"   ok: account={actid}, token length={len(token)}")
    print("   SHOONYA_ACCESS_TOKEN written to .env (not printed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())