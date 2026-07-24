#!/usr/bin/env python3
"""Refresh reviewed Python lock pins with hashes for every published wheel.

This script deliberately does not resolve or upgrade package versions. Version
selection remains a separate reviewed change. The candidate lock is only
published after pip proves that every pin has a hash-approved wheel for each
required release target.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from verify_python_wheels import REQUIRED_TARGETS, WheelPolicyError, verify_lock_for_target


PIN = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s;\\]+)(?:\s+\\)?$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LockRefreshError(RuntimeError):
    pass


def _read_pins(path: Path) -> tuple[list[str], list[tuple[str, str]]]:
    header: list[str] = []
    pins: list[tuple[str, str]] = []
    saw_pin = False
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            if not saw_pin:
                header.append(raw_line)
            continue
        if line.startswith("--hash=sha256:"):
            continue
        requirement = line.split(" --hash=sha256:", 1)[0].rstrip()
        match = PIN.fullmatch(requirement)
        if match is None:
            raise LockRefreshError(f"{path}:{line_number} is not a supported exact pin")
        saw_pin = True
        pins.append((match.group("name"), match.group("version")))
    if not pins:
        raise LockRefreshError(f"{path} contains no exact package pins")
    return header, pins


def _wheel_hashes(name: str, version: str) -> list[str]:
    url = f"https://pypi.org/pypi/{quote(name, safe='')}/{quote(version, safe='')}/json"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "electronic-mail-lock-refresh/1",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload: Any = json.load(response)
    except Exception as error:
        raise LockRefreshError(f"could not read PyPI metadata for {name}=={version}: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("urls"), list):
        raise LockRefreshError(f"PyPI returned malformed metadata for {name}=={version}")

    hashes: set[str] = set()
    for artifact in payload["urls"]:
        if not isinstance(artifact, dict):
            continue
        if artifact.get("packagetype") != "bdist_wheel" or artifact.get("yanked") is True:
            continue
        digests = artifact.get("digests")
        digest = digests.get("sha256") if isinstance(digests, dict) else None
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise LockRefreshError(f"PyPI returned an invalid wheel digest for {name}=={version}")
        hashes.add(digest)
    if not hashes:
        raise LockRefreshError(f"{name}=={version} has no non-yanked wheel artifacts")
    return sorted(hashes)


def refresh(path: Path) -> None:
    header, pins = _read_pins(path)
    output = [*header]
    if output and output[-1].strip():
        output.append("")
    for name, version in pins:
        hashes = _wheel_hashes(name, version)
        output.append(f"{name}=={version} \\")
        for index, digest in enumerate(hashes):
            suffix = " \\" if index + 1 < len(hashes) else ""
            output.append(f"    --hash=sha256:{digest}{suffix}")

    contents = "\n".join(output) + "\n"
    mode = path.stat().st_mode
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(contents)
            temporary_name = temporary.name
        os.chmod(temporary_name, mode)
        candidate = Path(temporary_name)
        for target in REQUIRED_TARGETS:
            try:
                verify_lock_for_target(candidate, target)
            except WheelPolicyError as error:
                raise LockRefreshError(
                    f"refusing to replace {path}: candidate lock failed wheel policy: {error}"
                ) from error
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("locks", nargs="+", type=Path)
    arguments = parser.parse_args()
    for path in arguments.locks:
        refresh(path)
        print(f"refreshed {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
