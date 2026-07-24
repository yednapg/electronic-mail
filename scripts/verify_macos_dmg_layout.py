#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Iterable


class DMGLayoutError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DMGLayoutError(message)


def _root_name(value: str, label: str) -> str:
    _require(bool(value), f"{label} cannot be empty")
    _require(value not in {".", ".."}, f"{label} must be a root item name")
    _require(Path(value).name == value and "/" not in value and "\\" not in value, f"{label} must be a root item name")
    return value


def validate_macos_dmg_layout(
    mount_path: Path,
    *,
    app_name: str,
    required_files: Iterable[str] = (),
) -> None:
    """Require the mounted DMG to contain only the reviewed install surface."""
    app_name = _root_name(app_name, "app name")
    _require(app_name.endswith(".app"), "app name must end with .app")
    file_names = [_root_name(value, "required file") for value in required_files]
    _require(len(file_names) == len(set(file_names)), "required files contain a duplicate name")
    _require(app_name not in file_names and "Applications" not in file_names, "required files collide with install items")

    _require(not mount_path.is_symlink(), "DMG mount path must not be a symbolic link")
    _require(mount_path.is_dir(), "DMG mount path is not a directory")
    expected = {"Applications", app_name, *file_names}
    actual = {entry.name for entry in os.scandir(mount_path)}
    _require(
        actual == expected,
        f"mounted DMG root contents differ from policy: expected {sorted(expected)}, found {sorted(actual)}",
    )

    applications = mount_path / "Applications"
    _require(applications.is_symlink(), "DMG Applications item must be a symbolic link")
    _require(os.readlink(applications) == "/Applications", "DMG Applications shortcut must target /Applications")

    app_path = mount_path / app_name
    _require(not app_path.is_symlink(), f"DMG {app_name} must not be a symbolic link")
    _require(app_path.is_dir(), f"DMG {app_name} is not an app bundle directory")

    for name in file_names:
        path = mount_path / name
        _require(not path.is_symlink(), f"DMG required file {name} must not be a symbolic link")
        _require(path.is_file(), f"DMG required file {name} is not a regular file")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the exact root layout of a mounted Electronic Mail DMG")
    parser.add_argument("--mount", required=True, type=Path)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--required-file", action="append", default=[])
    arguments = parser.parse_args(argv)
    try:
        validate_macos_dmg_layout(
            arguments.mount,
            app_name=arguments.app_name,
            required_files=arguments.required_file,
        )
    except (DMGLayoutError, OSError) as error:
        print(f"macOS DMG layout verification failed: {error}", file=sys.stderr)
        return 1
    print(f"macOS DMG layout verified: {arguments.app_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
