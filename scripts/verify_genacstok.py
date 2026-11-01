#!/usr/bin/env python3
"""Minimal, standalone reproducer for the Shoonya GenAcsTok INVALID_VERIFIER failure.

Usage:
    python scripts/verify_genacstok.py <auth_code> [client_id] [secret_key]

Prints every intermediate value (repr of each input, the raw string that gets
hashed, the checksum, and the exact request body sent) and then POSTs to the
live GenAcsTok endpoint, logging the full request and the full response.

This exists because INVALID_VERIFIER means "the checksum field was accepted but
its value did not match what Shoonya computed" — so the only way to find the
bug is to see, byte-for-byte, what string is actually being hashed.

Credentials default to the local .env (git-ignored); override on the CLI or
via SHOONYA_USER_ID / SHOONYA_API_SECRET env vars. Secrets are never written
to disk by this script.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
REST_BASE = "https://api.shoonya.com/NorenWClientAPI/"


def _load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    for key in ("SHOONYA_USER_ID", "SHOONYA_API_KEY", "SHOONYA_API_SECRET"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def _checksum(client_id: str, secret_key: str, auth_code: str) -> str:
    raw = f"{client_id}{secret_key}{auth_code}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), raw


def main() -> int:
    env = _load_env()
    if len(sys.argv) < 2:
        print("usage: python scripts/verify_genacstok.py <auth_code> [client_id] [secret_key]")
        return 2
    auth_code = sys.argv[1]
    client_id = sys.argv[2] if len(sys.argv) > 2 else env.get("SHOONYA_USER_ID", "")
    secret_key = sys.argv[3] if len(sys.argv) > 3 else env.get("SHOONYA_API_SECRET", "")

    if not (client_id and secret_key and auth_code):
        print("missing client_id / secret_key / auth_code", file=sys.stderr)
        return 1

    print("=== INPUTS (repr) ===")
    print(f"client_id = {client_id!r}  (len={len(client_id)})")
    print(f"secret_key= {secret_key!r}  (len={len(secret_key)})")
    print(f"auth_code = {auth_code!r}  (len={len(auth_code)})")

    for label, value in (("client_id", client_id), ("secret_key", secret_key), ("auth_code", auth_code)):
        bad = [c for c in value if ord(c) < 32 or ord(c) == 127]
        if bad:
            print(f"  !! {label} contains control chars: {[hex(ord(c)) for c in bad]}")

    checksum, raw = _checksum(client_id, secret_key, auth_code)
    print("\n=== HASH ===")
    print(f"raw       = {raw!r}")
    print(f"raw bytes = {raw.encode('utf-8').hex()}")
    print(f"checksum  = {checksum}")
    print(f"  len={len(checksum)} lower_hex={checksum == checksum.lower()}")

    body = "jData=" + json.dumps({"code": auth_code, "checksum": checksum})
    print("\n=== REQUEST ===")
    print(f"URL      = {REST_BASE}GenAcsTok")
    print(f"method   = POST")
    print(f"headers  = {{'Content-Type': 'text/plain'}}")
    print(f"body     = {body}")
    print(f"body len = {len(body)}")

    req = urllib.request.Request(
        f"{REST_BASE}GenAcsTok",
        data=body.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
        method="POST",
    )
    print("\n=== SENDING ===")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            resp_body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        resp_body = exc.read().decode("utf-8", errors="replace")
    print(f"HTTP {status}")
    print(f"response = {resp_body}")

    try:
        parsed = json.loads(resp_body)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        print(f"\nstat = {parsed.get('stat')!r}")
        print(f"emsg = {parsed.get('emsg')!r}")
        if parsed.get("stat") == "Ok":
            print("\n*** SUCCESS ***")
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())