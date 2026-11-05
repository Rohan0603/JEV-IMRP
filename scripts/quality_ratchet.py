"""Keep the existing quality baseline from growing while it is retired."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = {
    "ruff": 127,
    "format_files": 39,
    "mypy": 24,
}


def run(*args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return result.returncode, result.stdout + result.stderr


def count_ruff(output: str) -> int:
    match = re.search(r"Found (\d+) errors?\.", output)
    return int(match.group(1)) if match else 0


def count_format_files(output: str) -> int:
    match = re.search(r"(\d+) files would be reformatted", output)
    return int(match.group(1)) if match else 0


def count_mypy(output: str) -> int:
    match = re.search(r"Found (\d+) errors? in", output)
    return int(match.group(1)) if match else 0


def main() -> int:
    ruff_status, ruff_output = run("ruff", "check", ".", "--output-format", "concise")
    format_status, format_output = run("ruff", "format", "--check", ".")
    mypy_status, mypy_output = run("mypy")
    failures = []
    for name, status, output in (
        ("ruff", ruff_status, ruff_output),
        ("format", format_status, format_output),
        ("mypy", mypy_status, mypy_output),
    ):
        if status not in (0, 1):
            failures.append(f"{name} command failed with exit code {status}: {output.strip()}")
    observed = {
        "ruff": count_ruff(ruff_output),
        "format_files": count_format_files(format_output),
        "mypy": count_mypy(mypy_output),
    }
    print(json.dumps({"baseline": BASELINE, "observed": observed}, sort_keys=True))
    if failures:
        print("quality tool failure: " + "; ".join(failures), file=sys.stderr)
        return 1
    regressions = [
        f"{name}: {observed[name]} > {BASELINE[name]}"
        for name in BASELINE
        if observed[name] > BASELINE[name]
    ]
    if regressions:
        print("quality baseline regression: " + "; ".join(regressions), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
