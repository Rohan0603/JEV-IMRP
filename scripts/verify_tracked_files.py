"""Run read-only quality checks and detect tracked-file mutations."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def tracked_file_hashes() -> dict[str, str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths = result.stdout.decode().split("\0")[:-1]
    hashes: dict[str, str] = {}
    for path in paths:
        file_path = ROOT / path
        if file_path.is_file():
            hashes[path] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return hashes


def worktree_status() -> bytes:
    return subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def run(command: list[str]) -> int:
    return subprocess.run(command, cwd=ROOT).returncode


def main() -> int:
    before_hashes = tracked_file_hashes()
    before_status = worktree_status()

    check_status = run([sys.executable, "-m", "ruff", "check", "--no-fix", "."])
    format_status = run(
        [sys.executable, "-m", "ruff", "format", "--check", "--diff", "."]
    )

    after_hashes = tracked_file_hashes()
    after_status = worktree_status()
    changed = sorted(
        path
        for path in set(before_hashes) | set(after_hashes)
        if before_hashes.get(path) != after_hashes.get(path)
    )
    if changed or before_status != after_status:
        print("ERROR: verification changed the worktree; no files were restored.")
        for path in changed:
            print(f"tracked content changed: {path}")
        if before_status != after_status:
            print("git status changed during verification")
        return 1

    return max(check_status, format_status)


if __name__ == "__main__":
    raise SystemExit(main())
