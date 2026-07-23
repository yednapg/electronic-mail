#!/usr/bin/env python3
"""Fail closed when production inputs can drift without a reviewed lock update."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXACT_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
SHA256_REFERENCE = re.compile(r"@sha256:[0-9a-f]{64}(?:\s|$)")
ACTION_SHA = re.compile(r"^[^\s@]+@[0-9a-f]{40}$")
PYTHON_PIN = re.compile(r"^[A-Za-z0-9_.-]+==[^\s;]+(?:\s*;.*)?$")


class ReproducibilityError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReproducibilityError(message)


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    require(isinstance(payload, dict), f"{path.relative_to(ROOT)} must contain a JSON object")
    return payload


def verify_node_manifests() -> None:
    manifests = [ROOT / "package.json", ROOT / "web/package.json", ROOT / "packages/types/package.json"]
    lock = load_json(ROOT / "package-lock.json")
    require(lock.get("lockfileVersion") == 3, "package-lock.json must use lockfileVersion 3")
    lock_packages = lock.get("packages")
    require(isinstance(lock_packages, dict), "package-lock.json is missing packages")

    for manifest_path in manifests:
        manifest = load_json(manifest_path)
        relative = manifest_path.relative_to(ROOT)
        lock_key = "" if relative == Path("package.json") else str(relative.parent)
        locked_manifest = lock_packages.get(lock_key)
        require(isinstance(locked_manifest, dict), f"package-lock.json is missing workspace {lock_key or 'root'}")
        for section in ("dependencies", "devDependencies", "optionalDependencies"):
            declared = manifest.get(section, {})
            require(isinstance(declared, dict), f"{relative} {section} must be an object")
            locked_declared = locked_manifest.get(section, {})
            require(isinstance(locked_declared, dict), f"package-lock.json {lock_key or 'root'} {section} must be an object")
            require(declared == locked_declared, f"{relative} {section} does not exactly match package-lock.json")
            for dependency, version in declared.items():
                require(
                    isinstance(version, str) and EXACT_VERSION.fullmatch(version) is not None,
                    f"{relative} {section}.{dependency} must use an exact version, found {version!r}",
                )

        def verify_override_values(value: object, key_path: str) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    verify_override_values(child, f"{key_path}.{key}")
                return
            require(
                isinstance(value, str) and EXACT_VERSION.fullmatch(value) is not None,
                f"{relative} {key_path} must use an exact version, found {value!r}",
            )

        for override_name, override_value in manifest.get("overrides", {}).items():
            verify_override_values(override_value, f"overrides.{override_name}")

    for package_path, package in lock_packages.items():
        if not package_path.startswith("node_modules/") or package.get("link"):
            continue
        require(isinstance(package.get("version"), str), f"{package_path} is missing a locked version")
        require(isinstance(package.get("integrity"), str), f"{package_path} is missing an integrity digest")

    require((ROOT / ".nvmrc").read_text(encoding="utf-8").strip() == "22.22.0", ".nvmrc must pin Node 22.22.0")
    npm_configuration = {
        line.strip()
        for line in (ROOT / ".npmrc").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    require("engine-strict=true" in npm_configuration, ".npmrc must enforce engine-strict=true")


def verify_python_lock() -> None:
    version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    require(EXACT_VERSION.fullmatch(version) is not None, ".python-version must be an exact three-component version")
    pins: set[str] = set()
    for line_number, raw_line in enumerate((ROOT / "backend/requirements.lock").read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        require(PYTHON_PIN.fullmatch(line) is not None, f"backend/requirements.lock:{line_number} is not an exact pin")
        name = line.split("==", 1)[0].lower().replace("_", "-")
        require(name not in pins, f"backend/requirements.lock contains duplicate package {name}")
        pins.add(name)

    install_policy = {
        "backend/Dockerfile": 1,
        "scripts/bootstrap.sh": 1,
        ".github/workflows/quality.yml": 2,
    }
    no_deps_install = re.compile(r"pip[\"']?\s+install\s+--no-deps\s+(?:--requirement|-r)\s+")
    for relative, minimum_count in install_policy.items():
        normalized = " ".join((ROOT / relative).read_text(encoding="utf-8").replace("\\\n", " ").split())
        require(
            len(no_deps_install.findall(normalized)) >= minimum_count,
            f"{relative} must install requirements.lock with --no-deps",
        )


def verify_container_inputs() -> None:
    for relative in (Path("Dockerfile.web"), Path("backend/Dockerfile")):
        lines = (ROOT / relative).read_text(encoding="utf-8").splitlines()
        syntax = next((line for line in lines if line.startswith("# syntax=")), "")
        require(SHA256_REFERENCE.search(syntax) is not None, f"{relative} Dockerfile frontend must be digest-pinned")
        from_lines = [line for line in lines if line.startswith("FROM ")]
        require(from_lines, f"{relative} must contain a FROM instruction")
        for line in from_lines:
            # A named stage is immutable when it refers to a previous pinned stage.
            image = line.split()[1]
            if image in {candidate.split()[-1] for candidate in from_lines if " AS " in candidate}:
                continue
            require(SHA256_REFERENCE.search(image + " ") is not None, f"{relative} has a mutable base image: {image}")


def verify_actions() -> None:
    workflow_root = ROOT / ".github/workflows"
    workflows = sorted([*workflow_root.glob("*.yml"), *workflow_root.glob("*.yaml")])
    require(bool(workflows), "no GitHub Actions workflows found")
    for workflow in workflows:
        workflow_text = workflow.read_text(encoding="utf-8")
        for line_number, raw_line in enumerate(workflow_text.splitlines(), 1):
            match = re.search(r"\buses:\s*([^\s#]+)", raw_line)
            if match:
                reference = match.group(1)
                if not reference.startswith("./"):
                    require(ACTION_SHA.fullmatch(reference) is not None, f"{workflow.relative_to(ROOT)}:{line_number} action is not commit-pinned: {reference}")
            image_match = re.match(r"\s+image:\s*([^\s#]+)", raw_line)
            if image_match:
                image_reference = image_match.group(1)
                require(
                    SHA256_REFERENCE.search(image_reference + " ") is not None,
                    f"{workflow.relative_to(ROOT)}:{line_number} service/container image is mutable: {image_reference}",
                )
        for match in re.finditer(r"node-version:\s*['\"]?([^'\"\s]+)", workflow_text):
            require(match.group(1) == "22.22.0", f"{workflow.relative_to(ROOT)} must pin Node 22.22.0")


def main() -> int:
    try:
        verify_node_manifests()
        verify_python_lock()
        verify_container_inputs()
        verify_actions()
    except (OSError, json.JSONDecodeError, ReproducibilityError) as error:
        print(f"reproducibility verification failed: {error}", file=sys.stderr)
        return 1
    print("Reproducibility verification passed: manifests, locks, runtimes, containers, and Actions are immutable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
