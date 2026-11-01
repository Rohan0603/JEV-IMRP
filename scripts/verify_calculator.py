#!/usr/bin/env python3
"""Interactive checksum comparison helper.

Prints the exact inputs and resulting SHA256 hex digest so you can paste
them into Shoonya's official browser checksum calculator.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"

def main() -> int:
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()

    client_id = env.get("SHOONYA_CLIENT_ID") or env.get("SHOONYA_USER_ID", "FN248368_U")
    secret_code = env.get("SHOONYA_SECRET_CODE") or env.get("SHOONYA_API_SECRET", "")
    # Allow passing an auth code as an argument for live comparison
    auth_code = sys.argv[1] if len(sys.argv) > 1 else "<PASTE_AUTH_CODE_HERE>"

    raw = f"{client_id}{secret_code}{auth_code}"
    checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    print("=" * 60)
    print("SHOONYA BROWSER CALCULATOR INPUTS CHECK")
    print("=" * 60)
    print(f"Client ID   : {client_id}")
    print(f"Secret Code : {secret_code}")
    print(f"Auth Code   : {auth_code}")
    print("-" * 60)
    print(f"Raw string  : {raw}")
    print(f"SHA256 Hex  : {checksum}")
    print("=" * 60)
    print("\nInstructions:")
    print("1. Open https://shoonya.com/api-documentation/manual-authentication-oauth")
    print("2. Scroll down to 'Checksum calculator'")
    print("3. Enter the Client ID, Secret Code, and Auth Code above.")
    print("4. Verify if the browser calculator output matches 'SHA256 Hex' exactly.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
