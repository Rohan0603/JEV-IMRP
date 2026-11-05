"""Run read-only quality checks and detect tracked-file mutations."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
_REPARSE_POINT: Final[int] = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class UnsafeTrackedPathError(RuntimeError):
    """A tracked path could not be inspected without unsafe filesystem access."""


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    return bool(getattr(file_stat, "st_file_attributes", 0) & _REPARSE_POINT)


def _parse_index_entries(output: bytes) -> list[tuple[int, str]]:
    entries: list[tuple[int, str]] = []
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", maxsplit=1)
        mode = int(metadata.split(b" ", maxsplit=1)[0], 8)
        entries.append((mode, raw_path.decode("utf-8", errors="surrogateescape")))
    return entries


def _read_regular_file(file_path: Path, path: str) -> str:
    try:
        before = file_path.lstat()
    except FileNotFoundError as exc:
        raise UnsafeTrackedPathError(f"missing tracked path: {path}") from exc
    if stat.S_ISLNK(before.st_mode) or _is_reparse_point(before):
        raise UnsafeTrackedPathError(f"symlink or reparse point: {path}")
    if not stat.S_ISREG(before.st_mode):
        raise UnsafeTrackedPathError(f"non-regular tracked path: {path}")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(file_path, flags)
    except OSError as exc:
        raise UnsafeTrackedPathError(f"could not safely open tracked path: {path}") from exc

    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or _is_reparse_point(opened):
            raise UnsafeTrackedPathError(f"non-regular tracked path: {path}")
        digest = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=True) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = file_path.lstat()
    except UnsafeTrackedPathError:
        raise
    except (FileNotFoundError, OSError) as exc:
        raise UnsafeTrackedPathError(f"tracked path became unsafe during read: {path}") from exc

    before_signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    opened_signature = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    after_signature = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if (
        before_signature != opened_signature
        or opened_signature != after_signature
        or stat.S_ISLNK(after.st_mode)
        or _is_reparse_point(after)
    ):
        raise UnsafeTrackedPathError(f"tracked path changed during read: {path}")
    return digest.hexdigest()


def tracked_file_hashes(root: Path = ROOT) -> dict[str, str]:
    result = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    hashes: dict[str, str] = {}
    for mode, path in _parse_index_entries(result.stdout):
        if mode == 0o120000:
            raise UnsafeTrackedPathError(f"symlink in tracked index: {path}")
        if mode != 0o100644 and mode != 0o100755:
            raise UnsafeTrackedPathError(f"unsupported tracked index mode {mode:o}: {path}")
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise UnsafeTrackedPathError(f"unsafe tracked path: {path}")
        hashes[path] = _read_regular_file(root / relative, path)
    return hashes


def worktree_status() -> bytes:
    return subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def run(command: list[str]) -> int:
    return subprocess.run(command, cwd=ROOT).returncode


def main() -> int:
    try:
        before_hashes = tracked_file_hashes()
        before_status = worktree_status()
    except UnsafeTrackedPathError as exc:
        print(f"NOT CHECKED: {exc}")
        return 2

    check_status = run([sys.executable, "-m", "ruff", "check", "--no-fix", "."])
    format_status = run([sys.executable, "-m", "ruff", "format", "--check", "--diff", "."])

    try:
        after_hashes = tracked_file_hashes()
    except UnsafeTrackedPathError as exc:
        print(f"NOT CHECKED: {exc}")
        return 2
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
