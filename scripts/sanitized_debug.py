#!/usr/bin/env python3
"""Sanitized debug script for Shoonya GenAcsTok.

Prints exact redacted inputs, the signing string, the checksum, and the raw
HTTP body so we can inspect it character-for-character.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"

def redact(s: str) -> str:
    if not s:
        return "<empty>"
    if len(s) <= 8:
        return "****"
    return f"{s[:4]}...{s[-4:]} (len={len(s)})"

def main() -> int:
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()

    uid = env.get("SHOONYA_USER_ID", "")
    secret = env.get("SHOONYA_API_SECRET", "")
    # Placeholder auth code for offline check
    dummy_code = "dummy-auth-code-12345"

    print("=== REDACTED CONFIG ===")
    print(f"SHOONYA_USER_ID   = {redact(uid)}")
    print(f"SHOONYA_API_SECRET = {redact(secret)}")

    for client_id in (uid, uid[:-2] if uid.endswith(("_U", "_C")) else uid):
        raw = f"{client_id}{secret}{dummy_code}"
        checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        print(f"\n--- Variant (client_id={client_id!r}) ---")
        print(f"  raw string len = {len(raw)}")
        print(f"  checksum       = {checksum}")
        body = "jData=" + json.dumps({"uid": uid, "code": dummy_code, "checksum": checksum})
        print(f"  raw body repr  = {body!r}")

    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
