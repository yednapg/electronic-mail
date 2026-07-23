#!/usr/bin/env python3
"""Fail closed when production inputs can drift without a reviewed lock update."""

from __future__ import annotations

import base64
import binascii
import json
import platform
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXACT_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
SHA256_REFERENCE = re.compile(r"@sha256:[0-9a-f]{64}(?:\s|$)")
ACTION_SHA = re.compile(r"^[^\s@]+@[0-9a-f]{40}$")
NODE_SHA512_SRI = re.compile(r"^sha512-(?P<digest>[A-Za-z0-9+/]+={0,2})$")
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
WORKFLOW_QUOTED_MAPPING_KEY = re.compile(
    r"^\s*(?:-\s*)?(?P<quote>['\"])(?P<key>[^'\"]+)(?P=quote)\s*:"
)
WORKFLOW_SEQUENCE_ITEM = re.compile(r"^(?P<indent>\s*)-(?:\s+|$)")
WORKFLOW_PLAIN_MAPPING = re.compile(r"^(?P<key>[A-Za-z0-9_.-]+)\s*:\s*(?P<value>.*)$")
WORKFLOW_BLOCK_SCALAR = re.compile(
    r":\s*[|>](?P<indicators>(?:[1-9][+-]?|[+-][1-9]?))?\s*$"
)
PYTHON_PIN = re.compile(r"^[A-Za-z0-9_.-]+==[^\s;]+(?:\s*;.*)?$")
PYTHON_DIRECT_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?"
    r"(?P<constraints>(?:(?:>=|<=|==|>|<)[0-9]+\.[0-9]+\.[0-9]+)"
    r"(?:,(?:>=|<=|==|>|<)[0-9]+\.[0-9]+\.[0-9]+)*)$"
)
PYTHON_EXTRA_LOCK_PACKAGES: dict[tuple[str, str], tuple[str, bool]] = {
    ("psycopg", "binary"): ("psycopg-binary", True),
}


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
    _verify_local_venv(python_version)


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


def verify_python_lock() -> None:
    version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    require(EXACT_VERSION.fullmatch(version) is not None, ".python-version must be an exact three-component version")
    pins: dict[str, str] = {}
    for line_number, raw_line in enumerate((ROOT / "backend/requirements.lock").read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        require(PYTHON_PIN.fullmatch(line) is not None, f"backend/requirements.lock:{line_number} is not an exact pin")
        name, locked_version = line.split("==", 1)
        name = re.sub(r"[-_.]+", "-", name.lower())
        locked_version = locked_version.split(";", 1)[0].strip()
        require(name not in pins, f"backend/requirements.lock contains duplicate package {name}")
        pins[name] = locked_version

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

    pip_check_policy = {
        "backend/Dockerfile": "python -m pip check",
        "scripts/bootstrap.sh": '"$VENV_DIR/bin/pip" check',
        ".github/workflows/quality.yml": ".venv/bin/pip check",
    }
    for relative, required_command in pip_check_policy.items():
        require(
            required_command in (ROOT / relative).read_text(encoding="utf-8"),
            f"{relative} must verify the installed Python graph with {required_command}",
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
                            pinned_version == python_version,
                            f"{workflow.relative_to(ROOT)}:{line_number} setup-python must pin Python {python_version}",
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
