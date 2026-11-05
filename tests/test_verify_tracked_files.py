from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_MODULE_SPEC = importlib.util.spec_from_file_location(
    "verify_tracked_files", Path(__file__).parents[1] / "scripts" / "verify_tracked_files.py"
)
assert _MODULE_SPEC and _MODULE_SPEC.loader
_MODULE = importlib.util.module_from_spec(_MODULE_SPEC)
_MODULE_SPEC.loader.exec_module(_MODULE)
UnsafeTrackedPathError = _MODULE.UnsafeTrackedPathError
tracked_file_hashes = _MODULE.tracked_file_hashes


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def init_repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "--quiet")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    return tmp_path


def test_hashes_tracked_regular_file(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    (repo / "tracked.txt").write_bytes(b"safe")
    git(repo, "add", "tracked.txt")

    hashes = tracked_file_hashes(repo)

    assert "tracked.txt" in hashes


def test_rejects_tracked_symlink_without_reading_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    outside = tmp_path / "outside-secret.txt"
    outside.write_bytes(b"synthetic secret")
    link = repo / "linked.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    git(repo, "add", "linked.txt")

    with pytest.raises(UnsafeTrackedPathError, match="symlink"):
        tracked_file_hashes(repo)


def test_rejects_symlink_parent_without_reading_outside_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tracked.txt").write_bytes(b"synthetic secret")
    nested = repo / "nested"
    nested.mkdir()
    (nested / "tracked.txt").write_bytes(b"safe")
    git(repo, "add", "nested/tracked.txt")
    (nested / "tracked.txt").unlink()
    nested.rmdir()
    try:
        nested.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink creation unavailable: {exc}")

    with pytest.raises(UnsafeTrackedPathError, match="parent"):
        tracked_file_hashes(repo)


def test_rejects_junction_parent_without_reading_outside_target(tmp_path: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("junctions are a Windows-specific reparse-point fixture")

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tracked.txt").write_bytes(b"synthetic secret")
    nested = repo / "nested"
    nested.mkdir()
    (nested / "tracked.txt").write_bytes(b"safe")
    git(repo, "add", "nested/tracked.txt")
    (nested / "tracked.txt").unlink()
    nested.rmdir()
    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(nested), str(outside)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"junction creation unavailable: {exc}")

    try:
        with pytest.raises(UnsafeTrackedPathError, match="parent"):
            tracked_file_hashes(repo)
    finally:
        subprocess.run(
            ["cmd", "/c", "rmdir", str(nested)],
            check=False,
            capture_output=True,
            text=True,
        )


def test_rejects_parent_junction_replacement_during_open(tmp_path: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("junctions are a Windows-specific reparse-point fixture")

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tracked.txt").write_bytes(b"synthetic secret")
    nested = repo / "nested"
    nested.mkdir()
    (nested / "tracked.txt").write_bytes(b"safe")
    git(repo, "add", "nested/tracked.txt")

    original_open = _MODULE.os.open
    swapped = False

    def swap_parent_then_open(path: object, flags: int, *args: object) -> int:
        nonlocal swapped
        if not swapped and Path(path) == nested / "tracked.txt":
            (nested / "tracked.txt").unlink()
            nested.rmdir()
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(nested), str(outside)],
                check=True,
                capture_output=True,
                text=True,
            )
            swapped = True
        return original_open(path, flags, *args)

    try:
        with (
            patch.object(_MODULE.os, "open", side_effect=swap_parent_then_open),
            pytest.raises(UnsafeTrackedPathError, match="changed during read"),
        ):
            tracked_file_hashes(repo)
    finally:
        if nested.exists():
            subprocess.run(
                ["cmd", "/c", "rmdir", str(nested)],
                check=False,
                capture_output=True,
                text=True,
            )


def test_rejects_missing_tracked_path(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    (repo / "missing.txt").write_text("present", encoding="utf-8")
    git(repo, "add", "missing.txt")
    (repo / "missing.txt").unlink()

    with pytest.raises(UnsafeTrackedPathError, match="missing"):
        tracked_file_hashes(repo)


def test_rejects_tracked_directory_path(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    tracked = repo / "tracked.txt"
    tracked.write_text("present", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    tracked.unlink()
    tracked.mkdir()

    with pytest.raises(UnsafeTrackedPathError, match="non-regular"):
        tracked_file_hashes(repo)


def test_rejects_open_permission_failure(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    tracked = repo / "tracked.txt"
    tracked.write_text("present", encoding="utf-8")
    git(repo, "add", "tracked.txt")

    with (
        patch.object(_MODULE.os, "open", side_effect=PermissionError("denied")),
        pytest.raises(UnsafeTrackedPathError, match="safely open"),
    ):
        tracked_file_hashes(repo)


def test_rejects_file_swap_detected_after_read(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    tracked = repo / "tracked.txt"
    replacement = repo / "replacement.txt"
    tracked.write_text("present", encoding="utf-8")
    replacement.write_text("replacement", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    before = tracked.lstat()
    after = replacement.lstat()

    with (
        patch.object(_MODULE.Path, "lstat", side_effect=[before, after]),
        pytest.raises(UnsafeTrackedPathError, match="changed during read"),
    ):
        tracked_file_hashes(repo)
