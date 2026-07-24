#!/usr/bin/env python3
"""Fail closed when production inputs can drift without a reviewed lock update."""

from __future__ import annotations

import base64
import binascii
import json
import platform
import re
import shlex
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXACT_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
SHA256_REFERENCE = re.compile(r"@sha256:[0-9a-f]{64}(?:\s|$)")
ACTION_SHA = re.compile(r"^[^\s@]+@[0-9a-f]{40}$")
NODE_SHA512_SRI = re.compile(r"^sha512-(?P<digest>[A-Za-z0-9+/]+={0,2})$")
PINNED_NPM_VERSION = "10.9.4"
CANONICAL_PYTHON_VERSION = "3.12.13"
MACOS_AUTOMATION_PYTHON_VERSION = "3.12.10"
SUPPORTED_MACOS_RUNNER = "macos-15-intel"
DOCKER_FROM_INSTRUCTION = re.compile(
    r"^\s*FROM\s+"
    r"(?:(?:--platform(?:\s*=\s*|\s+))\S+\s+)?"
    r"(?P<image>\S+)"
    r"(?:\s+AS\s+(?P<stage>[A-Za-z0-9][A-Za-z0-9_.-]*))?"
    r"\s*(?:#.*)?$",
    re.IGNORECASE,
)
WORKFLOW_USES = re.compile(r"^\s*(?:-\s*)?uses\s*:\s*([^\s#]+)")
WORKFLOW_IMAGE = re.compile(r"^\s*image\s*:\s*([^\s#]+)")
WORKFLOW_CONTAINER = re.compile(
    r"^\s*container\s*:\s*"
    r"(?:'(?P<single>[^']*)'|\"(?P<double>[^\"]*)\"|(?P<bare>[^\s#]+))"
    r"(?:\s+#.*)?\s*$"
)
WORKFLOW_NODE_VERSION = re.compile(
    r"^\s*node-version\s*:\s*"
    r"(?:'(?P<single>[^']*)'|\"(?P<double>[^\"]*)\"|(?P<bare>[^\s#]+))"
    r"(?:\s+#.*)?\s*$"
)
WORKFLOW_PYTHON_VERSION = re.compile(
    r"^\s*python-version\s*:\s*"
    r"(?:'(?P<single>[^']*)'|\"(?P<double>[^\"]*)\"|(?P<bare>[^\s#]+))"
    r"(?:\s+#.*)?\s*$"
)
WORKFLOW_RUNS_ON = re.compile(
    r"^\s*runs-on\s*:\s*"
    r"(?:'(?P<single>[^']*)'|\"(?P<double>[^\"]*)\"|(?P<bare>[^\s#]+))"
    r"(?:\s+#.*)?\s*$"
)
WORKFLOW_QUOTED_MAPPING_KEY = re.compile(
    r"^\s*(?:-\s*)?(?P<quote>['\"])(?P<key>[^'\"]+)(?P=quote)\s*:"
)
WORKFLOW_SEQUENCE_ITEM = re.compile(r"^(?P<indent>\s*)-(?:\s+|$)")
WORKFLOW_PLAIN_MAPPING = re.compile(r"^(?P<key>[A-Za-z0-9_.-]+)\s*:\s*(?P<value>.*)$")
WORKFLOW_BLOCK_SCALAR = re.compile(
    r":\s*[|>](?P<indicators>(?:[1-9][+-]?|[+-][1-9]?))?\s*$"
)
PYTHON_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[0-9][0-9A-Za-z.!+_-]*)$"
)
PYTHON_SHA256 = re.compile(r"^--hash=sha256:(?P<digest>[0-9a-f]{64})$")
PYTHON_DIRECT_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?"
    r"(?P<constraints>(?:(?:>=|<=|==|>|<)[0-9]+\.[0-9]+\.[0-9]+)"
    r"(?:,(?:>=|<=|==|>|<)[0-9]+\.[0-9]+\.[0-9]+)*)$"
)
PYTHON_EXTRA_LOCK_PACKAGES: dict[tuple[str, str], tuple[str, bool]] = {
    ("psycopg", "binary"): ("psycopg-binary", True),
}
REQUIRED_PYTHON_TRANSITIVE_PINS = {"greenlet"}
REQUIRED_AUDIT_TOOL_PINS = {"pip", "pip-audit"}


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


def _is_valid_sha512_sri(value: object) -> bool:
    if not isinstance(value, str):
        return False
    match = NODE_SHA512_SRI.fullmatch(value)
    if match is None:
        return False
    encoded_digest = match.group("digest")
    try:
        digest = base64.b64decode(encoded_digest, validate=True)
    except (binascii.Error, ValueError):
        return False
    return len(digest) == 64 and base64.b64encode(digest).decode("ascii") == encoded_digest


def _runtime_pins() -> tuple[str, str]:
    node_version = (ROOT / ".nvmrc").read_text(encoding="utf-8").strip()
    python_version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    require(EXACT_VERSION.fullmatch(node_version) is not None, ".nvmrc must be an exact three-component version")
    require(
        EXACT_VERSION.fullmatch(python_version) is not None,
        ".python-version must be an exact three-component version",
    )
    require(
        python_version == CANONICAL_PYTHON_VERSION,
        f".python-version must pin canonical Python {CANONICAL_PYTHON_VERSION}",
    )
    return node_version, python_version


def _verify_local_venv(python_version: str) -> None:
    venv_directory = ROOT / ".venv"
    configuration_path = venv_directory / "pyvenv.cfg"
    if not venv_directory.exists() and not configuration_path.exists():
        return
    require(configuration_path.is_file(), ".venv exists without pyvenv.cfg provenance")

    configuration: dict[str, str] = {}
    for line_number, raw_line in enumerate(configuration_path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        require("=" in raw_line, f".venv/pyvenv.cfg:{line_number} is malformed")
        key, value = raw_line.split("=", 1)
        normalized_key = key.strip().lower()
        require(bool(normalized_key), f".venv/pyvenv.cfg:{line_number} has an empty key")
        configuration[normalized_key] = value.strip()

    require(
        configuration.get("version") == python_version,
        f".venv/pyvenv.cfg version must match .python-version ({python_version})",
    )
    home = configuration.get("home", "")
    executable = configuration.get("executable", "")
    command = configuration.get("command", "")
    require(bool(home), ".venv/pyvenv.cfg must record the base interpreter home")
    require(bool(executable), ".venv/pyvenv.cfg must record the base interpreter executable")
    require(Path(executable).is_absolute(), ".venv/pyvenv.cfg executable must be an absolute path")
    major_minor = ".".join(python_version.split(".")[:2])
    require(
        major_minor in home or major_minor in executable,
        f".venv/pyvenv.cfg base interpreter must come from Python {major_minor}",
    )
    require(bool(command), ".venv/pyvenv.cfg must record the creation command")
    require(
        str(venv_directory) in command,
        ".venv/pyvenv.cfg creation command must identify this repository's .venv",
    )
    venv_python = venv_directory / "bin/python"
    require(venv_python.is_file(), ".venv/pyvenv.cfg exists but .venv/bin/python is missing")
    try:
        version_result = subprocess.run(
            [str(venv_python), "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise ReproducibilityError(f"could not execute .venv/bin/python: {error}") from error
    require(version_result.returncode == 0, ".venv/bin/python --version failed")
    version_output = (version_result.stdout or version_result.stderr).strip()
    require(
        version_output == f"Python {python_version}",
        f".venv/bin/python must match .python-version ({python_version}); found {version_output or 'unknown'}",
    )


def verify_runtime_environment() -> None:
    node_version, python_version = _runtime_pins()
    require(
        platform.python_version() == python_version,
        f"executing Python must match .python-version ({python_version}); found {platform.python_version()}",
    )
    try:
        node_result = subprocess.run(
            ["node", "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise ReproducibilityError(f"could not execute pinned Node runtime: {error}") from error
    require(node_result.returncode == 0, "node --version failed")
    executing_node_version = node_result.stdout.strip().removeprefix("v")
    require(
        executing_node_version == node_version,
        f"executing Node must match .nvmrc ({node_version}); found {executing_node_version or 'unknown'}",
    )
    try:
        npm_result = subprocess.run(
            ["npm", "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise ReproducibilityError(f"could not execute pinned npm package manager: {error}") from error
    require(npm_result.returncode == 0, "npm --version failed")
    executing_npm_version = npm_result.stdout.strip()
    require(
        executing_npm_version == PINNED_NPM_VERSION,
        f"executing npm must match the repository pin ({PINNED_NPM_VERSION}); "
        f"found {executing_npm_version or 'unknown'}",
    )
    _verify_local_venv(python_version)


def verify_node_manifests() -> None:
    manifests = [ROOT / "package.json", ROOT / "web/package.json", ROOT / "packages/types/package.json"]
    root_manifest = load_json(ROOT / "package.json")
    lock = load_json(ROOT / "package-lock.json")
    require(lock.get("lockfileVersion") == 3, "package-lock.json must use lockfileVersion 3")
    lock_packages = lock.get("packages")
    require(isinstance(lock_packages, dict), "package-lock.json is missing packages")
    node_version, _ = _runtime_pins()
    expected_engines = {"node": node_version, "npm": PINNED_NPM_VERSION}
    require(
        root_manifest.get("packageManager") == f"npm@{PINNED_NPM_VERSION}",
        f"package.json packageManager must pin npm@{PINNED_NPM_VERSION}",
    )
    require(
        root_manifest.get("engines") == expected_engines,
        f"package.json engines must exactly pin Node {node_version} and npm {PINNED_NPM_VERSION}",
    )
    locked_root = lock_packages.get("")
    require(isinstance(locked_root, dict), "package-lock.json is missing the root package")
    require(
        locked_root.get("engines") == expected_engines,
        "package-lock.json root engines do not exactly match package.json",
    )

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
        require(
            _is_valid_sha512_sri(package.get("integrity")),
            f"{package_path} is missing a valid sha512 integrity digest",
        )

    require((ROOT / ".nvmrc").read_text(encoding="utf-8").strip() == "22.22.0", ".nvmrc must pin Node 22.22.0")
    npm_configuration = {
        line.strip()
        for line in (ROOT / ".npmrc").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    require("engine-strict=true" in npm_configuration, ".npmrc must enforce engine-strict=true")


def _hashed_python_pins(path: Path) -> dict[str, str]:
    logical_requirements: list[tuple[int, str]] = []
    continuation: list[str] = []
    continuation_line = 0
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            require(not continuation, f"{path.relative_to(ROOT)}:{line_number} interrupts a continued requirement")
            continue
        if not continuation:
            continuation_line = line_number
        continued = line.endswith("\\")
        continuation.append(line[:-1].rstrip() if continued else line)
        if not continued:
            logical_requirements.append((continuation_line, " ".join(continuation)))
            continuation = []
    require(not continuation, f"{path.relative_to(ROOT)} ends with an unfinished requirement")

    pins: dict[str, str] = {}
    for line_number, requirement in logical_requirements:
        tokens = requirement.split()
        pin_match = PYTHON_PIN.fullmatch(tokens[0]) if tokens else None
        require(pin_match is not None, f"{path.relative_to(ROOT)}:{line_number} is not an exact package pin")
        hashes: set[str] = set()
        for token in tokens[1:]:
            hash_match = PYTHON_SHA256.fullmatch(token)
            require(
                hash_match is not None,
                f"{path.relative_to(ROOT)}:{line_number} contains an unsupported lock option or hash",
            )
            digest = hash_match.group("digest")
            require(
                digest not in hashes,
                f"{path.relative_to(ROOT)}:{line_number} contains a duplicate SHA-256 hash",
            )
            hashes.add(digest)
        require(hashes, f"{path.relative_to(ROOT)}:{line_number} is missing a SHA-256 artifact hash")

        name = re.sub(r"[-_.]+", "-", pin_match.group("name").lower())
        require(name not in pins, f"{path.relative_to(ROOT)} contains duplicate package {name}")
        pins[name] = pin_match.group("version")
    require(pins, f"{path.relative_to(ROOT)} contains no package pins")
    return pins


def _pip_install_commands(path: Path) -> list[list[str]]:
    policy_text = "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ).replace("\\\n", " ")
    commands: list[list[str]] = []
    for line in policy_text.splitlines():
        for segment in re.split(r"\s*(?:&&|;)\s*", line):
            if "pip" not in segment or "install" not in segment:
                continue
            try:
                tokens = shlex.split(segment)
            except ValueError as error:
                raise ReproducibilityError(f"could not parse a pip command in {path.relative_to(ROOT)}: {error}") from error
            for index, token in enumerate(tokens):
                executable = Path(token).name
                if executable == "pip" and index + 1 < len(tokens) and tokens[index + 1] == "install":
                    commands.append(tokens[index + 2 :])
                    break
                if (
                    executable.startswith("python")
                    and tokens[index + 1 : index + 4] == ["-m", "pip", "install"]
                ):
                    commands.append(tokens[index + 4 :])
                    break
    return commands


def _requirement_targets(arguments: list[str]) -> list[str]:
    targets: list[str] = []
    for index, argument in enumerate(arguments):
        if argument in {"-r", "--requirement"} and index + 1 < len(arguments):
            targets.append(Path(arguments[index + 1]).name)
        elif argument.startswith("--requirement="):
            targets.append(Path(argument.partition("=")[2]).name)
    return targets


def verify_python_lock() -> None:
    version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    require(EXACT_VERSION.fullmatch(version) is not None, ".python-version must be an exact three-component version")
    pins = _hashed_python_pins(ROOT / "backend/requirements.lock")
    audit_pins = _hashed_python_pins(ROOT / "backend/audit-requirements.lock")
    for required_pin in REQUIRED_AUDIT_TOOL_PINS:
        require(
            required_pin in audit_pins,
            f"backend/audit-requirements.lock is missing required tool {required_pin}",
        )

    direct_names: set[str] = set()
    for line_number, raw_line in enumerate((ROOT / "backend/requirements.txt").read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = PYTHON_DIRECT_REQUIREMENT.fullmatch(line)
        require(match is not None, f"backend/requirements.txt:{line_number} must use reviewed numeric bounds")
        direct_name = re.sub(r"[-_.]+", "-", match.group("name").lower())
        require(direct_name not in direct_names, f"backend/requirements.txt contains duplicate package {direct_name}")
        direct_names.add(direct_name)
        require(direct_name in pins, f"backend/requirements.lock is missing direct package {direct_name}")
        locked_tuple = tuple(int(part) for part in pins[direct_name].split("."))
        require(len(locked_tuple) == 3, f"direct package {direct_name} must resolve to a three-component version")
        for constraint in match.group("constraints").split(","):
            operator = next(candidate for candidate in (">=", "<=", "==", ">", "<") if constraint.startswith(candidate))
            expected_tuple = tuple(int(part) for part in constraint[len(operator):].split("."))
            comparisons = {
                ">=": locked_tuple >= expected_tuple,
                "<=": locked_tuple <= expected_tuple,
                "==": locked_tuple == expected_tuple,
                ">": locked_tuple > expected_tuple,
                "<": locked_tuple < expected_tuple,
            }
            require(
                comparisons[operator],
                f"backend/requirements.lock {direct_name}=={pins[direct_name]} violates {constraint}",
            )
        extras_match = re.search(r"\[([^]]+)\]", line)
        for raw_extra in extras_match.group(1).split(",") if extras_match else ():
            extra = re.sub(r"[-_.]+", "-", raw_extra.strip().lower())
            provider_policy = PYTHON_EXTRA_LOCK_PACKAGES.get((direct_name, extra))
            require(
                provider_policy is not None,
                f"backend/requirements.txt:{line_number} extra {direct_name}[{extra}] needs an explicit lock policy",
            )
            provider, must_match_parent_version = provider_policy
            require(provider in pins, f"backend/requirements.lock is missing extra provider {provider}")
            if must_match_parent_version:
                require(
                    pins[provider] == pins[direct_name],
                    f"backend/requirements.lock {provider} must match {direct_name}=={pins[direct_name]}",
                )

    for required_pin in REQUIRED_PYTHON_TRANSITIVE_PINS:
        require(
            required_pin in pins,
            f"backend/requirements.lock is missing required transitive package {required_pin}",
        )

    install_policy = {
        "backend/Dockerfile": {"requirements.lock": 1},
        "scripts/bootstrap.sh": {"requirements.lock": 1},
        ".github/workflows/quality.yml": {
            "requirements.lock": 2,
            "audit-requirements.lock": 1,
        },
    }
    pip_check = re.compile(r"(?:[^\s]*pip[\"']?|python\s+-m\s+pip)\s+check(?:\s|$)")
    required_install_flags = {"--no-deps", "--require-hashes", "--only-binary=:all:"}
    for relative, minimum_counts in install_policy.items():
        path = ROOT / relative
        policy_text = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        normalized = " ".join(policy_text.replace("\\\n", " ").split())
        install_commands = _pip_install_commands(path)
        observed_counts = {name: 0 for name in minimum_counts}
        for arguments in install_commands:
            targets = _requirement_targets(arguments)
            require(
                len(targets) == 1 and targets[0] in observed_counts,
                f"{relative} contains a pip install outside an approved hash lock",
            )
            missing_flags = sorted(required_install_flags.difference(arguments))
            require(
                not missing_flags,
                f"{relative} {targets[0]} install is missing {' '.join(missing_flags)}",
            )
            observed_counts[targets[0]] += 1
        for lock_name, minimum_count in minimum_counts.items():
            require(
                observed_counts[lock_name] >= minimum_count,
                f"{relative} must install {lock_name} with hash and binary enforcement",
            )
        require(
            len(pip_check.findall(normalized)) >= len(install_commands),
            f"{relative} must run pip check for every Python lock install",
        )

    workflow_text = (ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")
    wheel_proof_command = (
        ".audit-venv/bin/python scripts/verify_python_wheels.py "
        "backend/requirements.lock backend/audit-requirements.lock"
    )
    require(
        sum(line.strip() == wheel_proof_command for line in workflow_text.splitlines()) == 1,
        "Python CI must prove each lock has a hash-approved CPython 3.12 macOS Intel wheel",
    )
    wheel_policy_path = ROOT / "scripts/verify_python_wheels.py"
    require(wheel_policy_path.is_file(), "scripts/verify_python_wheels.py is required")
    wheel_policy_text = wheel_policy_path.read_text(encoding="utf-8")
    for required_policy in (
        'platform="macosx_15_0_x86_64"',
        'python_version="3.12"',
        'implementation="cp"',
        '"--require-hashes"',
        '"--only-binary=:all:"',
    ):
        require(
            required_policy in wheel_policy_text,
            f"macOS Intel wheel verifier is missing required policy {required_policy}",
        )
    audit_commands = [
        line.strip()
        for line in workflow_text.splitlines()
        if line.strip().startswith(".audit-venv/bin/python -m pip_audit ")
    ]
    audited_locks: dict[str, int] = {
        "backend/requirements.lock": 0,
        "backend/audit-requirements.lock": 0,
    }
    for audit_command in audit_commands:
        audit_arguments = shlex.split(audit_command)
        for required_argument in ("--strict", "--no-deps", "--disable-pip"):
            require(
                required_argument in audit_arguments,
                f"Python lock audit must include {required_argument}",
            )
        lock_argument_indexes = [
            index
            for index, argument in enumerate(audit_arguments)
            if argument in ("-r", "--requirement")
        ]
        require(
            len(lock_argument_indexes) == 1 and lock_argument_indexes[0] + 1 < len(audit_arguments),
            "every Python lock audit must inspect exactly one requirements file",
        )
        audited_lock = audit_arguments[lock_argument_indexes[0] + 1]
        require(audited_lock in audited_locks, f"Python audit uses unapproved lock {audited_lock}")
        audited_locks[audited_lock] += 1
    require(
        all(count == 1 for count in audited_locks.values()),
        "Python audits must inspect each runtime and audit-tool lock exactly once",
    )
    require(
        "cache-dependency-path: |" in workflow_text
        and "backend/audit-requirements.lock" in workflow_text,
        "Python CI cache key must include the audit-tool lock",
    )


def verify_container_inputs() -> None:
    for relative in (Path("Dockerfile.web"), Path("backend/Dockerfile")):
        lines = (ROOT / relative).read_text(encoding="utf-8").splitlines()
        syntax = next((line for line in lines if line.startswith("# syntax=")), "")
        require(SHA256_REFERENCE.search(syntax) is not None, f"{relative} Dockerfile frontend must be digest-pinned")
        from_instructions: list[tuple[str, str | None]] = []
        for line_number, line in enumerate(lines, 1):
            if re.match(r"^\s*FROM(?:\s|$)", line, re.IGNORECASE) is None:
                continue
            match = DOCKER_FROM_INSTRUCTION.fullmatch(line)
            require(match is not None, f"{relative}:{line_number} has a malformed FROM instruction")
            from_instructions.append((match.group("image"), match.group("stage")))

        require(from_instructions, f"{relative} must contain a FROM instruction")
        named_stages: set[str] = set()
        for image, stage in from_instructions:
            # A named stage is immutable when it refers to a previous pinned stage.
            if image.casefold() not in named_stages:
                require(
                    SHA256_REFERENCE.search(image + " ") is not None,
                    f"{relative} has a mutable base image: {image}",
                )
            if stage is not None:
                named_stages.add(stage.casefold())


def _workflow_step_range(lines: list[str], uses_index: int) -> range:
    uses_line = lines[uses_index]
    uses_indent = len(uses_line) - len(uses_line.lstrip())
    inline_step = re.match(r"^\s*-\s*uses\s*:", uses_line) is not None
    step_start = uses_index
    step_indent = uses_indent

    if not inline_step:
        for candidate_index in range(uses_index - 1, -1, -1):
            candidate = lines[candidate_index]
            sequence_item = WORKFLOW_SEQUENCE_ITEM.match(candidate)
            if sequence_item is None:
                continue
            candidate_indent = len(sequence_item.group("indent"))
            if candidate_indent < uses_indent:
                step_start = candidate_index
                step_indent = candidate_indent
                break

    step_end = len(lines)
    for candidate_index in range(uses_index + 1, len(lines)):
        candidate = lines[candidate_index]
        stripped = candidate.strip()
        if not stripped or stripped.startswith("#"):
            continue
        candidate_indent = len(candidate) - len(candidate.lstrip())
        if WORKFLOW_SEQUENCE_ITEM.match(candidate) is not None and candidate_indent <= step_indent:
            step_end = candidate_index
            break
        if candidate_indent < step_indent:
            step_end = candidate_index
            break
    return range(step_start, step_end)


def _workflow_scalar(match: re.Match[str]) -> str:
    return next(value for value in match.groupdict().values() if value is not None)


def _workflow_step_versions(
    lines: list[str],
    uses_index: int,
    pattern: re.Pattern[str],
    structural_lines: set[int],
    runtime_key: str,
) -> list[str]:
    step_range = _workflow_step_range(lines, uses_index)
    uses_mapping_indent = _workflow_mapping_indent(lines[uses_index])
    with_indexes: list[int] = []
    for line_index in step_range:
        if line_index not in structural_lines:
            continue
        logical_line = _strip_yaml_inline_comment(lines[line_index])
        if (
            re.fullmatch(r"\s*with\s*:\s*", logical_line) is not None
            and _workflow_mapping_indent(logical_line) == uses_mapping_indent
        ):
            with_indexes.append(line_index)
    if len(with_indexes) != 1:
        return []

    with_index = with_indexes[0]
    child_indent: int | None = None
    runtime_key_count = 0
    versions: list[str] = []
    for line_index in range(with_index + 1, step_range.stop):
        if line_index not in structural_lines:
            continue
        step_line = lines[line_index]
        indentation = len(step_line) - len(step_line.lstrip())
        if indentation <= uses_mapping_indent:
            break
        if child_indent is None:
            child_indent = indentation
        if indentation != child_indent:
            continue
        logical_line = _strip_yaml_inline_comment(step_line)
        if re.match(rf"^\s*{re.escape(runtime_key)}\s*:", logical_line) is None:
            continue
        runtime_key_count += 1
        match = pattern.fullmatch(step_line)
        if match is not None:
            versions.append(_workflow_scalar(match))
    if runtime_key_count != 1 or len(versions) != 1:
        return []
    return versions


def _strip_yaml_inline_comment(line: str) -> str:
    in_single_quote = False
    in_double_quote = False
    index = 0
    while index < len(line):
        character = line[index]
        if in_single_quote:
            if character == "'":
                if index + 1 < len(line) and line[index + 1] == "'":
                    index += 2
                    continue
                in_single_quote = False
        elif in_double_quote:
            if character == "\\":
                index += 2
                continue
            if character == '"':
                in_double_quote = False
        elif character == "'":
            in_single_quote = True
        elif character == '"':
            in_double_quote = True
        elif character == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
        index += 1
    return line.rstrip()


def _contains_unquoted_flow_mapping(value: str) -> bool:
    in_single_quote = False
    in_double_quote = False
    index = 0
    while index < len(value):
        character = value[index]
        if in_single_quote:
            if character == "'":
                if index + 1 < len(value) and value[index + 1] == "'":
                    index += 2
                    continue
                in_single_quote = False
        elif in_double_quote:
            if character == "\\":
                index += 2
                continue
            if character == '"':
                in_double_quote = False
        elif character == "'":
            in_single_quote = True
        elif character == '"':
            in_double_quote = True
        elif value.startswith("${{", index):
            expression_end = value.find("}}", index + 3)
            if expression_end == -1:
                return False
            index = expression_end + 2
            continue
        elif character in "{:":
            return True
        index += 1
    return False


def _contains_unquoted_node_property(value: str) -> bool:
    in_single_quote = False
    in_double_quote = False
    index = 0
    while index < len(value):
        character = value[index]
        if in_single_quote:
            if character == "'":
                if index + 1 < len(value) and value[index + 1] == "'":
                    index += 2
                    continue
                in_single_quote = False
        elif in_double_quote:
            if character == "\\":
                index += 2
                continue
            if character == '"':
                in_double_quote = False
        elif character == "'":
            in_single_quote = True
        elif character == '"':
            in_double_quote = True
        elif value.startswith("${{", index):
            expression_end = value.find("}}", index + 3)
            if expression_end == -1:
                return False
            index = expression_end + 2
            continue
        elif character in "!&*" and (
            index == 0 or value[index - 1].isspace() or value[index - 1] in "[{,:?"
        ):
            return True
        index += 1
    return False


def _workflow_mapping_indent(line: str) -> int:
    indentation = len(line) - len(line.lstrip())
    sequence_item = re.match(r"^-\s+", line.lstrip())
    if sequence_item is not None:
        return indentation + sequence_item.end()
    return indentation


def _workflow_direct_child_lines(
    lines: list[str],
    parent_index: int,
    structural_lines: set[int],
) -> list[int]:
    parent_indent = _workflow_mapping_indent(lines[parent_index])
    child_indent: int | None = None
    children: list[int] = []
    for line_index in range(parent_index + 1, len(lines)):
        if line_index not in structural_lines:
            continue
        line = lines[line_index]
        indentation = len(line) - len(line.lstrip())
        if indentation <= parent_indent:
            break
        if child_indent is None:
            child_indent = indentation
        if indentation == child_indent:
            children.append(line_index)
    return children


def _workflow_enclosing_job_runner(
    workflow: Path,
    lines: list[str],
    uses_index: int,
    structural_lines: set[int],
) -> str:
    contexts: list[int] = []
    for jobs_index in sorted(structural_lines):
        jobs_line = _strip_yaml_inline_comment(lines[jobs_index])
        if re.fullmatch(r"jobs\s*:\s*", jobs_line) is None:
            continue
        if len(jobs_line) - len(jobs_line.lstrip()) != 0:
            continue

        job_indexes = _workflow_direct_child_lines(lines, jobs_index, structural_lines)
        for position, job_index in enumerate(job_indexes):
            job_line = _strip_yaml_inline_comment(lines[job_index]).lstrip()
            job_mapping = WORKFLOW_PLAIN_MAPPING.fullmatch(job_line)
            if job_mapping is None or job_mapping.group("value").strip():
                continue
            job_stop = job_indexes[position + 1] if position + 1 < len(job_indexes) else len(lines)
            for candidate_index in range(job_index + 1, len(lines)):
                if candidate_index not in structural_lines:
                    continue
                candidate = lines[candidate_index]
                if len(candidate) - len(candidate.lstrip()) <= len(lines[job_index]) - len(lines[job_index].lstrip()):
                    job_stop = min(job_stop, candidate_index)
                    break
            if job_index < uses_index < job_stop:
                contexts.append(job_index)

    line_number = uses_index + 1
    require(
        len(contexts) == 1,
        f"{workflow.relative_to(ROOT)}:{line_number} setup-python must be nested under "
        "exactly one literal jobs.<job> runner context",
    )
    job_index = contexts[0]
    runner_matches: list[re.Match[str]] = []
    for child_index in _workflow_direct_child_lines(lines, job_index, structural_lines):
        logical_line = _strip_yaml_inline_comment(lines[child_index])
        if re.match(r"^\s*runs-on\s*:", logical_line) is None:
            continue
        match = WORKFLOW_RUNS_ON.fullmatch(logical_line)
        require(
            match is not None,
            f"{workflow.relative_to(ROOT)}:{line_number} setup-python job must use one literal runs-on label",
        )
        runner_matches.append(match)
    require(
        len(runner_matches) == 1,
        f"{workflow.relative_to(ROOT)}:{line_number} setup-python job must define exactly one literal runs-on label",
    )
    runner_label = _workflow_scalar(runner_matches[0])
    require(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", runner_label) is not None,
        f"{workflow.relative_to(ROOT)}:{line_number} setup-python job runs-on must be a static literal label",
    )
    return runner_label


def _workflow_structural_lines(workflow: Path, lines: list[str]) -> set[int]:
    structural_lines: set[int] = set()
    block_scalar_parent_indent: int | None = None
    block_scalar_content_indent: int | None = None
    for line_index, raw_line in enumerate(lines):
        if not raw_line.strip():
            continue
        indentation = len(raw_line) - len(raw_line.lstrip())
        if block_scalar_parent_indent is not None:
            if block_scalar_content_indent is None and indentation > block_scalar_parent_indent:
                block_scalar_content_indent = indentation
                continue
            if block_scalar_content_indent is not None and indentation >= block_scalar_content_indent:
                continue
            block_scalar_parent_indent = None
            block_scalar_content_indent = None
        if raw_line.lstrip().startswith("#"):
            continue

        logical_line = _strip_yaml_inline_comment(raw_line)
        if not logical_line.strip():
            continue
        line_number = line_index + 1
        quoted_key = WORKFLOW_QUOTED_MAPPING_KEY.match(logical_line)
        require(
            quoted_key is None,
            f"{workflow.relative_to(ROOT)}:{line_number} uses unsupported quoted YAML mapping keys",
        )

        mapping_candidate = logical_line.lstrip()
        if mapping_candidate.startswith("-") and len(mapping_candidate) > 1 and mapping_candidate[1].isspace():
            mapping_candidate = mapping_candidate[1:].lstrip()
        explicit_mapping = re.match(r"^(?:\?(?:\s|$)|:(?:\s|$))", mapping_candidate)
        require(
            explicit_mapping is None,
            f"{workflow.relative_to(ROOT)}:{line_number} uses unsupported YAML explicit mapping-key syntax",
        )
        document_control = mapping_candidate.startswith("%") or re.match(
            r"^(?:---|\.\.\.)(?:\s|$)",
            mapping_candidate,
        )
        require(
            not document_control,
            f"{workflow.relative_to(ROOT)}:{line_number} uses unsupported YAML directives or document markers",
        )
        node_property = (
            mapping_candidate.startswith(("!", "&", "*"))
            or re.match(r"^<<\s*:", mapping_candidate) is not None
        )
        flow_mapping = mapping_candidate.startswith("{") or (
            mapping_candidate.startswith("[") and _contains_unquoted_flow_mapping(mapping_candidate)
        )
        if mapping_candidate.startswith("["):
            node_property = node_property or _contains_unquoted_node_property(mapping_candidate)
        plain_mapping = WORKFLOW_PLAIN_MAPPING.match(mapping_candidate)
        if plain_mapping is not None:
            mapping_value = plain_mapping.group("value").lstrip()
            node_property = (
                node_property
                or plain_mapping.group("key") == "<<"
                or mapping_value.startswith(("!", "&", "*"))
            )
            if mapping_value.startswith("{"):
                flow_mapping = True
            elif mapping_value.startswith("["):
                flow_mapping = _contains_unquoted_flow_mapping(mapping_value)
                node_property = node_property or _contains_unquoted_node_property(mapping_value)
        require(
            not node_property,
            f"{workflow.relative_to(ROOT)}:{line_number} uses unsupported YAML anchors, aliases, tags, or merge keys",
        )
        require(
            not flow_mapping,
            f"{workflow.relative_to(ROOT)}:{line_number} uses unsupported YAML flow mapping syntax",
        )

        structural_lines.add(line_index)
        block_scalar = WORKFLOW_BLOCK_SCALAR.search(logical_line)
        if block_scalar is not None:
            block_scalar_parent_indent = _workflow_mapping_indent(logical_line)
            indicators = block_scalar.group("indicators") or ""
            indentation_indicator = next(
                (int(character) for character in indicators if character.isdigit()),
                None,
            )
            block_scalar_content_indent = (
                block_scalar_parent_indent + indentation_indicator
                if indentation_indicator is not None
                else None
            )
    return structural_lines


def verify_actions() -> None:
    workflow_root = ROOT / ".github/workflows"
    workflows = sorted([*workflow_root.glob("*.yml"), *workflow_root.glob("*.yaml")])
    require(bool(workflows), "no GitHub Actions workflows found")
    python_version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    require(
        EXACT_VERSION.fullmatch(python_version) is not None,
        ".python-version must be an exact three-component version",
    )
    require(
        python_version == CANONICAL_PYTHON_VERSION,
        f".python-version must pin canonical Python {CANONICAL_PYTHON_VERSION}",
    )
    for workflow in workflows:
        workflow_text = workflow.read_text(encoding="utf-8")
        workflow_lines = workflow_text.splitlines()
        structural_lines = _workflow_structural_lines(workflow, workflow_lines)
        for line_index, raw_line in enumerate(workflow_lines):
            if line_index not in structural_lines:
                continue
            line_number = line_index + 1
            logical_line = _strip_yaml_inline_comment(raw_line)
            empty_sensitive_scalar = re.match(
                r"^\s*(?:-\s*)?(?P<key>uses|image)\s*:\s*$",
                logical_line,
            )
            if empty_sensitive_scalar is not None:
                require(
                    False,
                    f"{workflow.relative_to(ROOT)}:{line_number} "
                    f"{empty_sensitive_scalar.group('key')} must use a nonempty single-line scalar value",
                )
            match = WORKFLOW_USES.match(raw_line)
            if match:
                reference = match.group(1)
                if not reference.startswith("./"):
                    require(ACTION_SHA.fullmatch(reference) is not None, f"{workflow.relative_to(ROOT)}:{line_number} action is not commit-pinned: {reference}")
                action_name = reference.split("@", 1)[0].casefold()
                if action_name == "actions/setup-python":
                    runner_label = _workflow_enclosing_job_runner(
                        workflow,
                        workflow_lines,
                        line_index,
                        structural_lines,
                    )
                    expected_python_version = (
                        MACOS_AUTOMATION_PYTHON_VERSION
                        if runner_label == SUPPORTED_MACOS_RUNNER
                        else python_version
                    )
                    pinned_versions = _workflow_step_versions(
                        workflow_lines,
                        line_index,
                        WORKFLOW_PYTHON_VERSION,
                        structural_lines,
                        "python-version",
                    )
                    require(
                        bool(pinned_versions),
                        f"{workflow.relative_to(ROOT)}:{line_number} setup-python must pin python-version",
                    )
                    for pinned_version in pinned_versions:
                        require(
                            pinned_version == expected_python_version,
                            f"{workflow.relative_to(ROOT)}:{line_number} setup-python on {runner_label} "
                            f"must pin Python {expected_python_version}",
                        )
                if action_name == "actions/setup-node":
                    pinned_versions = _workflow_step_versions(
                        workflow_lines,
                        line_index,
                        WORKFLOW_NODE_VERSION,
                        structural_lines,
                        "node-version",
                    )
                    require(
                        bool(pinned_versions),
                        f"{workflow.relative_to(ROOT)}:{line_number} setup-node must pin node-version",
                    )
                    for pinned_version in pinned_versions:
                        require(
                            pinned_version == "22.22.0",
                            f"{workflow.relative_to(ROOT)}:{line_number} setup-node must pin Node 22.22.0",
                        )
            image_match = WORKFLOW_IMAGE.match(raw_line)
            if image_match:
                image_reference = image_match.group(1)
                require(
                    SHA256_REFERENCE.search(image_reference + " ") is not None,
                    f"{workflow.relative_to(ROOT)}:{line_number} service/container image is mutable: {image_reference}",
                )
            container_key = re.match(r"^\s*container\s*:\s*(?P<value>.*)$", logical_line)
            if container_key is not None:
                if container_key.group("value").strip():
                    container_match = WORKFLOW_CONTAINER.fullmatch(logical_line)
                    require(
                        container_match is not None,
                        f"{workflow.relative_to(ROOT)}:{line_number} job container must use a literal digest-pinned image",
                    )
                    container_reference = _workflow_scalar(container_match)
                    require(
                        SHA256_REFERENCE.search(container_reference + " ") is not None,
                        f"{workflow.relative_to(ROOT)}:{line_number} job container image is mutable: {container_reference}",
                    )
                else:
                    direct_images: list[str] = []
                    for child_index in _workflow_direct_child_lines(
                        workflow_lines,
                        line_index,
                        structural_lines,
                    ):
                        child_line = _strip_yaml_inline_comment(workflow_lines[child_index])
                        child_image = re.match(r"^\s*image\s*:\s*(?P<value>.*)$", child_line)
                        if child_image is not None and child_image.group("value").strip():
                            direct_images.append(child_image.group("value").strip())
                    require(
                        len(direct_images) == 1,
                        f"{workflow.relative_to(ROOT)}:{line_number} job container long form must define one direct nonempty image",
                    )
            node_version_match = WORKFLOW_NODE_VERSION.search(raw_line)
            if node_version_match:
                require(
                    _workflow_scalar(node_version_match) == "22.22.0",
                    f"{workflow.relative_to(ROOT)} must pin Node 22.22.0",
                )
            runs_on_match = WORKFLOW_RUNS_ON.fullmatch(raw_line)
            if runs_on_match:
                runner_label = _workflow_scalar(runs_on_match)
                if runner_label.startswith("macos-"):
                    require(
                        runner_label == SUPPORTED_MACOS_RUNNER,
                        f"{workflow.relative_to(ROOT)}:{line_number} macOS jobs must use "
                        f"{SUPPORTED_MACOS_RUNNER} so Python {MACOS_AUTOMATION_PYTHON_VERSION} "
                        "and the approved Xcode pin are available",
                    )


def main() -> int:
    try:
        verify_runtime_environment()
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
