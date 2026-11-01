#!/usr/bin/env python3

"""
Automated final integration test script.

This script handles:
1. Running the OAuth flow (browser interaction required)
2. Updating .env with the token
3. Running the full integration test
"""

import os
import sys
import subprocess
import re
from pathlib import Path

# Ensure the environment is set up
REPO_ROOT = Path(__file__).resolve().parents[1]

# Step 1: Run OAuth script and capture token
print("=== Step 1: Running OAuth flow ===")
oauth_result = subprocess.run(
    [sys.executable, "scripts/oauth_login_script.py"],
    capture_output=True,
    text=True
)

if oauth_result.returncode != 0:
    print(f"OAuth failed: {oauth_result.stderr}")
    sys.exit(1)

# Extract token from output. oauth_login_script.py writes the token to .env
# and prints a line of the form "access token: <token>" for the orchestrator.
token_line = None
for line in oauth_result.stdout.splitlines():
    if "access token:" in line.lower():
        token_line = line
        break

if not token_line:
    print("Could not extract token from OAuth output")
    print(oauth_result.stdout)
    sys.exit(1)

token = token_line.split("access token:", 1)[1].strip()
print(f"Token obtained: {token[:20]}...")

# Step 2: Update .env with the token
print("\n=== Step 2: Updating .env ===")
env_path = REPO_ROOT / ".env"

# Read current .env content
with open(env_path, 'r') as f:
    env_content = f.read()

# Replace the SHOONYA_ACCESS_TOKEN line
new_env_content = env_content.replace(
    "SHOONYA_ACCESS_TOKEN=",
    f"SHOONYA_ACCESS_TOKEN={token}"
)

# Write back to .env
with open(env_path, 'w') as f:
    f.write(new_env_content)

print("Updated .env with the new token")

# Step 3: Run the integration test
print("\n=== Step 3: Running integration test ===")
try:
    test_result = subprocess.run(
        [sys.executable, "scripts/live_integration_test.py"],
        capture_output=True,
        text=True
    )

    # Print the output
    print(test_result.stdout)

    if test_result.returncode != 0:
        print(f"Test failed with exit code {test_result.returncode}:")
        print(test_result.stderr)
        sys.exit(1)

except Exception as e:
    print(f"Error running test: {str(e)}")
    sys.exit(1)

print("\n=== Test completed successfully! ===")
print("All components are working as expected.")

# Step 4: Update .gitignore to exclude attempt store
print("\n=== Step 4: Updating .gitignore ===")
with open(REPO_ROOT / ".gitignore", 'a') as f:
    f.write("\nexperiments/attempts.jsonl\n")
print("Added experiments/attempts.jsonl to .gitignore")