#!/usr/bin/env python3
"""Prove that every locked Python package has a supported macOS Intel wheel."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable, Sequence


PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[0-9][0-9A-Za-z.!+_-]*)(?:\s+\\)?$"
)


class WheelPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class WheelTarget:
    name: str
    platform: str
    python_version: str
    implementation: str


MACOS_INTEL_CPYTHON_312 = WheelTarget(
    name="CPython 3.12 on macOS 15 Intel",
    platform="macosx_15_0_x86_64",
    python_version="3.12",
    implementation="cp",
)
REQUIRED_TARGETS = (MACOS_INTEL_CPYTHON_312,)


def _locked_package_count(path: Path) -> int:
    names: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("--hash=sha256:"):
            continue
        match = PIN.fullmatch(line)
        if match is None:
            raise WheelPolicyError(f"{path}:{line_number} is not a supported exact package pin")
        normalized_name = re.sub(r"[-_.]+", "-", match.group("name").lower())
        if normalized_name in names:
            raise WheelPolicyError(f"{path} contains duplicate package {normalized_name}")
        names.add(normalized_name)
    if not names:
        raise WheelPolicyError(f"{path} contains no exact package pins")
    return len(names)


def _download_command(
    path: Path,
    destination: Path,
    target: WheelTarget,
    *,
    python_executable: str = sys.executable,
) -> list[str]:
    return [
        python_executable,
        "-m",
        "pip",
        "download",
        "--disable-pip-version-check",
        "--progress-bar",
        "off",
        "--no-deps",
        "--require-hashes",
        "--only-binary=:all:",
        "--platform",
        target.platform,
        "--python-version",
        target.python_version,
        "--implementation",
        target.implementation,
        "--dest",
        str(destination),
        "--requirement",
        str(path),
    ]


def verify_lock_for_target(
    path: Path,
    target: WheelTarget,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    expected_wheels = _locked_package_count(path)
    with tempfile.TemporaryDirectory(prefix="electronic-mail-wheel-proof-") as temporary:
        destination = Path(temporary)
        command = _download_command(path, destination, target)
        try:
            result = runner(command, check=False, capture_output=True, text=True)
        except OSError as error:
            raise WheelPolicyError(f"could not run pip for {target.name}: {error}") from error
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()
            raise WheelPolicyError(
                f"{path} is missing a hash-approved wheel for {target.name}"
                + (f":\n{details}" if details else "")
            )
        downloaded_wheels = list(destination.glob("*.whl"))
        if len(downloaded_wheels) != expected_wheels:
            raise WheelPolicyError(
                f"{path} downloaded {len(downloaded_wheels)} wheels for {target.name}; "
                f"expected exactly {expected_wheels}"
            )
    return expected_wheels


def verify_locks(
    paths: Sequence[Path],
    *,
    targets: Sequence[WheelTarget] = REQUIRED_TARGETS,
) -> list[tuple[Path, WheelTarget, int]]:
    results: list[tuple[Path, WheelTarget, int]] = []
    for path in paths:
        if not path.is_file():
            raise WheelPolicyError(f"Python lock does not exist: {path}")
        for target in targets:
            count = verify_lock_for_target(path, target)
            results.append((path, target, count))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("locks", nargs="+", type=Path)
    arguments = parser.parse_args()
    try:
        results = verify_locks(arguments.locks)
    except (OSError, WheelPolicyError) as error:
        print(f"Python wheel verification failed: {error}", file=sys.stderr)
        return 1
    for path, target, count in results:
        print(f"Python wheel verification passed: {path} has {count} wheels for {target.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
