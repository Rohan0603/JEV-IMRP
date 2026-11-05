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


def test_signal_terminated_quality_check_fails_closed() -> None:
    with (
        patch.object(_MODULE, "tracked_file_hashes", side_effect=[{}, {}]),
        patch.object(_MODULE, "worktree_status", return_value=b""),
        patch.object(_MODULE, "run", side_effect=[-9, 0]),
    ):
        assert _MODULE.main() == 1


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
            pytest.raises(UnsafeTrackedPathError, match="resolved outside target root"),
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


def test_rejects_open_handle_that_resolves_outside_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    tracked = repo / "tracked.txt"
    tracked.write_text("present", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    outside = tmp_path / "outside.txt"

    with (
        patch.object(_MODULE, "_final_handle_path", return_value=outside),
        pytest.raises(UnsafeTrackedPathError, match="outside target root"),
    ):
        tracked_file_hashes(repo)


def test_unc_final_path_keeps_server_share_root() -> None:
    assert _MODULE._normalize_final_path(r"\\?\UNC\server\share\tracked.txt") == Path(
        r"\\server\share\tracked.txt"
    )


def test_mixed_case_unc_final_path_keeps_server_share_root() -> None:
    assert _MODULE._normalize_final_path(r"\\?\uNc\server\share\tracked.txt") == Path(
        r"\\server\share\tracked.txt"
    )


def test_volume_final_path_is_rejected() -> None:
    with pytest.raises(UnsafeTrackedPathError, match="unsupported Windows handle namespace"):
        _MODULE._normalize_final_path(r"\\?\Volume{abc}\tracked.txt")


def test_unknown_windows_handle_namespace_is_rejected() -> None:
    with pytest.raises(UnsafeTrackedPathError, match="unsupported Windows handle namespace"):
        _MODULE._normalize_final_path(r"\\?\GLOBALROOT\Device\HarddiskVolume1\tracked.txt")


def test_handle_path_failure_closes_descriptor(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    tracked = repo / "tracked.txt"
    tracked.write_text("present", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    opened: list[int] = []
    original_open = _MODULE.os.open

    def capture_open(path: object, flags: int, *args: object) -> int:
        fd = original_open(path, flags, *args)
        opened.append(fd)
        return fd

    with (
        patch.object(_MODULE.os, "open", side_effect=capture_open),
        patch.object(
            _MODULE,
            "_final_handle_path",
            side_effect=UnsafeTrackedPathError("synthetic handle failure"),
        ),
        pytest.raises(UnsafeTrackedPathError, match="synthetic handle failure"),
    ):
        tracked_file_hashes(repo)
    assert opened
    with pytest.raises(OSError):
        _MODULE.os.fstat(opened[0])


@pytest.mark.parametrize("path", [r"\rooted.txt", r"C:relative-drive.txt"])
def test_rejects_rooted_or_drive_qualified_index_paths(path: str, tmp_path: Path) -> None:
    result = type("GitResult", (), {"stdout": f"100644 deadbeef\t{path}\0".encode()})()
    with (
        patch.object(_MODULE.subprocess, "run", return_value=result),
        pytest.raises(UnsafeTrackedPathError, match="unsafe tracked path"),
    ):
        tracked_file_hashes(tmp_path)


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
